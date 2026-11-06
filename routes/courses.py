from flask import Blueprint, jsonify, request, session
from utils.security import server_error, rate_limit
from utils.validation import (ValidationError, validate_course_code, validate_course_name, validate_slot_code,
                              validate_venue, validate_faculty, validate_int, validate_slot)
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


@courses_bp.route('/options')
def get_course_options():
    """Get unique course codes, names, and faculty names for autocomplete."""
    base_query = get_scoped_courses()
    
    # Distinct course codes and names
    courses = base_query.with_entities(Course.code, Course.name).distinct().all()
    course_codes = list({c.code for c in courses if c.code})
    course_names = list({c.name for c in courses if c.name})
    
    # Distinct faculty names associated with the user's courses
    from models import db, Faculty, Slot
    faculties = db.session.query(Faculty.name).join(Slot).join(Course).filter(
        Course.id.in_(base_query.with_entities(Course.id))
    ).distinct().all()
    faculty_names = [f.name for f in faculties if f.name]
    course_map = {c.code: c.name for c in courses if c.code and c.name}
    name_to_code_map = {c.name: c.code for c in courses if c.code and c.name}
    
    # Distinct faculty names per course
    course_faculties = db.session.query(Course.code, Faculty.name)\
        .select_from(Course)\
        .join(Slot, Course.id == Slot.course_id)\
        .join(Faculty, Slot.faculty_id == Faculty.id)\
        .filter(Course.id.in_(base_query.with_entities(Course.id)))\
        .distinct().all()
    
    course_faculty_map = {}
    for code, fname in course_faculties:
        if code and fname:
            if code not in course_faculty_map:
                course_faculty_map[code] = []
            course_faculty_map[code].append(fname)
            
    # Distinct faculty names per course and slot
    course_slot_faculties = db.session.query(Course.code, Slot.slot_code, Faculty.name)\
        .select_from(Course)\
        .join(Slot, Course.id == Slot.course_id)\
        .join(Faculty, Slot.faculty_id == Faculty.id)\
        .filter(Course.id.in_(base_query.with_entities(Course.id)))\
        .distinct().all()
        
    course_slot_faculty_map = {}
    slot_faculty_map = {}
    for code, scode, fname in course_slot_faculties:
        if code and scode and fname:
            if code not in course_slot_faculty_map:
                course_slot_faculty_map[code] = {}
            if scode not in course_slot_faculty_map[code]:
                course_slot_faculty_map[code][scode] = []
            course_slot_faculty_map[code][scode].append(fname)
            
            if scode not in slot_faculty_map:
                slot_faculty_map[scode] = []
            if fname not in slot_faculty_map[scode]:
                slot_faculty_map[scode].append(fname)
            
    return jsonify({
        'course_codes': sorted(course_codes),
        'course_names': sorted(course_names),
        'faculty_names': sorted(faculty_names),
        'course_map': course_map,
        'name_to_code_map': name_to_code_map,
        'course_faculty_map': course_faculty_map,
        'course_slot_faculty_map': course_slot_faculty_map,
        'slot_faculty_map': slot_faculty_map
    })


@courses_bp.route('/manual', methods=['POST'])
@rate_limit('manual-course', 60, 600)
def add_course_manually():
    """Add a course manually with slot and auto-register."""
    data = request.get_json(silent=True) or {}
    
    # Validate required fields
    required = ['course_code', 'course_name', 'slot_code']
    for field in required:
        if not data.get(field):
            return jsonify({'error': f'{field} is required'}), 400

    try:
        data = {
            'course_code': validate_course_code(data.get('course_code')),
            'course_name': validate_course_name(data.get('course_name')),
            'slot_code': validate_slot_code(data.get('slot_code')),
            'venue': validate_venue(data.get('venue')),
            'faculty': validate_faculty(data.get('faculty')),
            'credits': validate_int(data.get('credits'), 'Credits', 0, 40),
        }
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
            
    # Determine owner
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    try:
        # Check if course with same code already exists (Scoped)
        base_query = get_scoped_courses()
        course = base_query.filter_by(code=data['course_code'].upper()).first()
        
        if course and course.name.strip().lower() != data['course_name'].strip().lower():
            return jsonify({
                'error': f"Course code '{course.code}' already exists with name '{course.name}'. Please use the exact existing name or a different code."
            }), 400
            
        # Check if course with same name already exists but under a different code
        course_by_name = base_query.filter(db.func.lower(Course.name) == data['course_name'].strip().lower()).first()
        if course_by_name and course_by_name.code != data['course_code'].upper():
            return jsonify({
                'error': f"Course name '{course_by_name.name}' is already assigned to course code '{course_by_name.code}'. Please use the exact existing code or a different name."
            }), 400
        
        # Create new course if it doesn't exist
        if not course:
            course = Course(
                code=data['course_code'].upper(),
                name=data['course_name'],
                l=0,
                t=0,
                p=0,
                j=0,
                c=data['credits'],
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
        faculty_name = data['faculty']
        faculty = Faculty.query.filter_by(name=faculty_name).first()
        if not faculty:
            faculty = Faculty(name=faculty_name)
            db.session.add(faculty)
            db.session.flush()
        
        # Check if this exact slot (Course + Faculty + Slot Code) already exists
        slot_code = data['slot_code']
        venue = data['venue']
        
        slot = Slot.query.filter_by(
            slot_code=slot_code,
            course_id=course.id,
            faculty_id=faculty.id
        ).first()
        
        if not slot:
            slot = Slot(
                slot_code=slot_code,
                course_id=course.id,
                faculty_id=faculty.id,
                venue=venue,
                available_seats=70,
                total_seats=70
            )
            db.session.add(slot)
            db.session.flush()
        
        # Check for time clashes against existing registrations
        from routes.registration import check_slot_clashes
        clash_result = check_slot_clashes(slot)
        if clash_result['has_clash']:
            db.session.rollback()
            clashing_info = ', '.join(c['course_code'] + ' (' + c.get('reason', 'Time overlap') + ')' for c in clash_result['clashing_slots'])
            return jsonify({
                'error': f'Slot clash detected with: {clashing_info}',
                'clashing_slots': clash_result['clashing_slots']
            }), 400
        
        # Check if already registered for this course (any slot)
        existing_reg_query = Registration.query.join(Slot).filter(Slot.course_id == course.id)
        if user_id:
            existing_reg_query = existing_reg_query.filter(Registration.user_id == user_id)
        else:
            existing_reg_query = existing_reg_query.filter(Registration.guest_id == guest_id)
        
        if existing_reg_query.first():
            db.session.rollback()
            return jsonify({'error': f'You are already registered for course {course.code}.'}), 400
        
        # Auto-register if not already registered for this exact slot
        reg_query = Registration.query.filter_by(slot_id=slot.id)
        if user_id:
            reg_query = reg_query.filter_by(user_id=user_id)
        else:
            reg_query = reg_query.filter_by(guest_id=guest_id)
            
        if not reg_query.first():
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
        else:
            db.session.rollback()
            return jsonify({'error': 'You are already registered for this exact slot and faculty.'}), 400
        
    except Exception as e:
        db.session.rollback()
        return server_error(e)


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
        return server_error(e)


@courses_bp.route('/bulk', methods=['DELETE'])
def bulk_delete_courses():
    """Delete multiple courses."""
    data = request.get_json(silent=True) or {}
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
        return server_error(e)


@courses_bp.route('/<course_id>/sync', methods=['POST'])
def sync_course_slots(course_id):
    """Sync slots for a course (replace all existing)."""
    data = request.get_json(silent=True) or {}
    raw_slots = data.get('slots', [])
    if not isinstance(raw_slots, list) or len(raw_slots) > 300:
        return jsonify({'error': 'Invalid slot list.'}), 400

    # Validate every row before touching anything; report the first bad row
    slots_data = []
    for index, row in enumerate(raw_slots, start=1):
        if not isinstance(row, dict):
            return jsonify({'error': f'Row {index}: invalid data.'}), 400
        try:
            slots_data.append(validate_slot(row))
        except ValidationError as e:
            return jsonify({'error': f'Row {index}: {e}'}), 400
    
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
        faculty_names = set(s_data['faculty'] for s_data in slots_data)
        
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
            faculty = faculty_map.get(s_data['faculty'])
            
            new_slot = Slot(
                slot_code=s_data['slot_code'],
                course_id=course.id,
                faculty_id=faculty.id,
                venue=s_data['venue'],
                available_seats=s_data['available_seats'],
                total_seats=s_data['available_seats'] # Default total to avail
            )
            db.session.add(new_slot)
            
        db.session.commit()
        return jsonify({'success': True, 'message': f'Updated {len(slots_data)} slots for {course.code}'})
        
    except Exception as e:
        db.session.rollback()
        return server_error(e)
