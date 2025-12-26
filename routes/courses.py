from flask import Blueprint, jsonify, request, session
from models import db, Course, Slot, Faculty, Registration

courses_bp = Blueprint('courses', __name__)

def get_scoped_courses():
    """Get base query for courses visible to current user."""
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')
    
    if user_id:
        return Course.query.filter_by(user_id=user_id)
    elif guest_id:
        return Course.query.filter_by(guest_id=guest_id)
    else:
        # No session, return empty query (or should we return None?)
        # For safety return a query that matches nothing usually, 
        # but to be safe return Filter by False
        return Course.query.filter(db.false())

@courses_bp.route('/search')
def search_courses():
    """Search courses by code or name."""
    query_text = request.args.get('q', '').strip()
    
    if not query_text:
        return jsonify({'courses': []})
    
    # Scope query
    base_query = get_scoped_courses()
    
    courses = base_query.filter(
        db.or_(
            Course.code.ilike(f'%{query_text}%'),
            Course.name.ilike(f'%{query_text}%')
        )
    ).limit(20).all()
    
    return jsonify({
        'courses': [course.to_dict() for course in courses]
    })


@courses_bp.route('/<course_id>')
def get_course(course_id):
    """Get course details by ID."""
    base_query = get_scoped_courses()
    course = base_query.filter_by(id=course_id).first_or_404()
    return jsonify(course.to_dict())


@courses_bp.route('/<course_id>/slots')
def get_course_slots(course_id):
    """Get all available slots for a course."""
    base_query = get_scoped_courses()
    course = base_query.filter_by(id=course_id).first_or_404()
    
    # Slots don't have user_id explicit, but if we found the course,
    # the slots linked to it are authorized.
    # Eager load Faculty and Course to prevent N+1 queries during serialization
    slots = Slot.query.filter_by(course_id=course_id).options(
        db.joinedload(Slot.faculty),
        db.joinedload(Slot.course)
    ).all()
    
    return jsonify({
        'course': course.to_dict(),
        'slots': [slot.to_dict() for slot in slots]
    })


@courses_bp.route('/all')
def get_all_courses():
    """Get all courses."""
    base_query = get_scoped_courses()
    courses = base_query.order_by(Course.code).all()
    return jsonify({
        'courses': [course.to_dict() for course in courses]
    })


@courses_bp.route('/manual', methods=['POST'])
def add_course_manually():
    """Add a course manually with slot and auto-register."""
    data = request.get_json()
    
    # Validate required fields
    required = ['course_code', 'course_name', 'slot_code']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400
            
    # Determine owner
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    try:
        # Find or create course (Scoped)
        base_query = get_scoped_courses()
        course = base_query.filter_by(code=data['course_code'].upper()).first()
        
        if not course:
            course = Course(
                code=data['course_code'].upper(),
                name=data['course_name'],
                l=0,
                t=0,
                p=0,
                j=0,
                c=int(data.get('credits', 0)),
                course_type='N/A',
                category='N/A',
                user_id=user_id,
                guest_id=guest_id
            )
            db.session.add(course)
            db.session.flush()
        
        # Find or create faculty (Faculty is shared? Or should be scoped?
        # Faculty names are generic. Let's keep faculty shared for now to avoid DUPLICATE faculty table boom, 
        # or just create if missing. Faculty has no sensitive data.)
        faculty_name = data.get('faculty', 'N/A').strip() or 'N/A'
        faculty = Faculty.query.filter_by(name=faculty_name).first()
        if not faculty:
            faculty = Faculty(name=faculty_name)
            db.session.add(faculty)
            db.session.flush()
        
        # Create slot
        venue = data.get('venue', 'N/A').strip().upper() or 'N/A'
        slot = Slot(
            slot_code=data['slot_code'].upper(),
            course_id=course.id,
            faculty_id=faculty.id,
            venue=venue,
            available_seats=70,
            total_seats=70
        )
        db.session.add(slot)
        db.session.flush()
        
        # Auto-register
        registration = Registration(slot_id=slot.id)
        if user_id:
            registration.user_id = user_id
        else:
            registration.guest_id = guest_id
            
        db.session.add(registration)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f"Course {course.code} added and registered successfully!",
            'course': course.to_dict(),
            'slot': slot.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@courses_bp.route('/<course_id>', methods=['DELETE'])
def delete_course(course_id):
    """Delete a course and all associated slots/registrations."""
    base_query = get_scoped_courses()
    course = base_query.filter_by(id=course_id).first_or_404()
    
    try:
        db.session.delete(course)
        db.session.commit()
        return jsonify({'success': True, 'message': f'Course {course.code} deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@courses_bp.route('/bulk', methods=['DELETE'])
def bulk_delete_courses():
    """Delete multiple courses."""
    data = request.get_json()
    course_ids = data.get('course_ids', [])
    
    if not course_ids:
        return jsonify({'error': 'No course IDs provided'}), 400
        
    # Security: Ensure these courses belong to the current user
    base_query = get_scoped_courses()
    
    try:
        # 1. Verify ownership and get valid IDs
        # We only want to delete courses that are actually owned by this user
        valid_courses = base_query.filter(Course.id.in_(course_ids)).with_entities(Course.id).all()
        valid_ids = [c.id for c in valid_courses]
        
        count = len(valid_ids)
        if count == 0:
            return jsonify({'message': 'No matching courses found to delete'}), 200

        # 2. Bulk Delete Process (Manual Cascade for Performance)
        # SQLAlchemy ORM cascading is slow for bulk operations (iterates objects).
        # We manually delete children -> parents using bulk DELETE statements.
        
        # A. Find all Slots for these courses
        slots = Slot.query.filter(Slot.course_id.in_(valid_ids)).with_entities(Slot.id).all()
        slot_ids = [s.id for s in slots]
        
        if slot_ids:
            # B. Delete Registrations linked to these Slots
            Registration.query.filter(Registration.slot_id.in_(slot_ids)).delete(synchronize_session=False)
            
            # C. Delete Slots
            Slot.query.filter(Slot.id.in_(slot_ids)).delete(synchronize_session=False)
            
        # D. Delete Courses
        Course.query.filter(Course.id.in_(valid_ids)).delete(synchronize_session=False)
            
        db.session.commit()
        return jsonify({'success': True, 'message': f'Successfully deleted {count} courses'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@courses_bp.route('/<course_id>/sync', methods=['POST'])
def sync_course_slots(course_id):
    """Sync slots for a course (replace all existing)."""
    data = request.get_json()
    slots_data = data.get('slots', [])
    
    # 1. Get Course (Scoped)
    base_query = get_scoped_courses()
    course = base_query.filter_by(id=course_id).first_or_404()
    
    try:
        # 2. Delete Existing Slots
        existing_slots = Slot.query.filter_by(course_id=course.id).all()
        slot_ids = [s.id for s in existing_slots]
        
        if slot_ids:
             Registration.query.filter(Registration.slot_id.in_(slot_ids)).delete(synchronize_session=False)
             Slot.query.filter(Slot.id.in_(slot_ids)).delete(synchronize_session=False)
        
        # 3. Add New Slots (Batch Faculty Lookup to avoid N+1)
        # Collect all unique faculty names first
        faculty_names = set(s_data.get('faculty', 'N/A').strip() or 'N/A' for s_data in slots_data)
        
        # Fetch all existing faculties in one query
        existing_faculties = Faculty.query.filter(Faculty.name.in_(faculty_names)).all()
        faculty_map = {f.name: f for f in existing_faculties}
        
        # Create missing faculties
        missing_names = faculty_names - set(faculty_map.keys())
        for name in missing_names:
            new_faculty = Faculty(name=name)
            db.session.add(new_faculty)
            db.session.flush()
            faculty_map[name] = new_faculty
        
        # Now create slots using the map
        for s_data in slots_data:
            fac_name = s_data.get('faculty', 'N/A').strip() or 'N/A'
            faculty = faculty_map.get(fac_name)
            
            new_slot = Slot(
                slot_code=s_data.get('slot_code', 'N/A').upper(),
                course_id=course.id,
                faculty_id=faculty.id if faculty else None,
                venue=s_data.get('venue', 'N/A').upper(),
                available_seats=int(s_data.get('available_seats', 0)),
                total_seats=int(s_data.get('available_seats', 0)) # Default total to avail
            )
            db.session.add(new_slot)
            
        db.session.commit()
        return jsonify({'success': True, 'message': f'Updated {len(slots_data)} slots for {course.code}'})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
