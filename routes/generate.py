"""Routes for auto-generating timetable suggestions."""

from flask import Blueprint, request, jsonify, session, render_template
from models import db, Course, Slot, Faculty, Registration, User, SavedTimetable
from utils.timetable_generator import TimetableGenerator, GenerationPreferences
import uuid

generate_bp = Blueprint('generate', __name__)


@generate_bp.route('/page')
def generate_page():
    """Dedicated timetable generation page."""
    current_user = None
    
    if 'user_id' in session:
        current_user = User.query.get(session['user_id'])
    else:
        if 'guest_id' not in session:
            session['guest_id'] = str(uuid.uuid4())
    
    return render_template('generate.html', current_user=current_user)


def get_user_scope():
    """Get current user/guest scope for queries."""
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')
    return user_id, guest_id


@generate_bp.route('/available', methods=['GET'])
def get_available_courses():
    """
    Get list of courses and faculties available for generation.
    Returns courses that have been imported but not yet registered.
    """
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    # Get all courses for this user
    if user_id:
        courses = Course.query.filter_by(user_id=user_id).all()
        registrations = Registration.query.filter_by(user_id=user_id).all()
    else:
        courses = Course.query.filter_by(guest_id=guest_id).all()
        registrations = Registration.query.filter_by(guest_id=guest_id).all()
    
    # Batch pre-fetch all slots for these courses to avoid N+1
    course_ids = [c.id for c in courses]
    all_slots = Slot.query.filter(Slot.course_id.in_(course_ids)).options(
        db.joinedload(Slot.faculty)
    ).all() if course_ids else []
    
    # Build a map: course_id -> list of slots
    slots_by_course = {}
    for slot in all_slots:
        slots_by_course.setdefault(slot.course_id, []).append(slot)
    
    # Get registered course IDs
    registered_course_ids = set()
    for reg in registrations:
        if reg.slot and reg.slot.course_id:
            registered_course_ids.add(reg.slot.course_id)
    
    # Filter to unregistered courses and collect faculty info
    available_courses = []
    all_faculty_names = set()
    
    for course in courses:
        # Get faculties teaching this course from pre-fetched slots
        faculties = set()
        course_slots = slots_by_course.get(course.id, [])
        for slot in course_slots:
            if slot.faculty:
                faculties.add(slot.faculty.name)
                all_faculty_names.add(slot.faculty.name)
        
        available_courses.append({
            'id': str(course.id),  # String to prevent JS precision loss
            'code': course.code,
            'name': course.name,
            'credits': course.c,
            'is_registered': course.id in registered_course_ids,
            'slot_count': len(course_slots),
            'faculties': list(faculties)
        })
    
    return jsonify({
        'courses': available_courses,
        'all_faculties': sorted(list(all_faculty_names)),
        'registered_count': len(registered_course_ids),
        'debug': {
            'user_id': user_id,
            'guest_id': guest_id
        }
    })


@generate_bp.route('/count', methods=['POST'])
def count_timetables():
    """
    Count total valid timetable combinations.
    
    Request body:
    {
        "course_ids": ["1", "2", "3"],
        "preferences": {...}
    }
    """
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    data = request.get_json() or {}
    course_ids = data.get('course_ids', [])
    pref_data = data.get('preferences', {})
    
    if not course_ids:
        return jsonify({'count': 0, 'capped': False})
    
    # Convert string IDs to integers (handles JS precision issue)
    try:
        course_ids = [int(cid) for cid in course_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid course ID format'}), 400
    
    # Get courses (scoped to user)
    if user_id:
        courses = Course.query.filter(
            Course.id.in_(course_ids),
            Course.user_id == user_id
        ).all()
    else:
        courses = Course.query.filter(
            Course.id.in_(course_ids),
            Course.guest_id == guest_id
        ).all()
    
    if not courses:
        return jsonify({'count': 0, 'capped': False})
    
    # Build preferences
    # Build preferences
    preferences = GenerationPreferences(
        avoid_early_morning=pref_data.get('avoid_early_morning', False),
        avoid_late_evening=pref_data.get('avoid_late_evening', False),
        prefer_morning=pref_data.get('prefer_morning', False),
        prefer_afternoon=pref_data.get('prefer_afternoon', False),
        preferred_faculties=pref_data.get('preferred_faculties', []),
        avoided_faculties=pref_data.get('avoided_faculties', []),
        exclude_slots=pref_data.get('exclude_slots', []),
        time_mode=pref_data.get('time_mode', 'none'),
        course_faculty_preferences=pref_data.get('course_faculty_preferences', {})
    )
    
    # Count solutions
    generator = TimetableGenerator(courses, preferences)
    max_count = 100000
    
    # Check for distinct mode
    mode = data.get('mode', 'std')
    if mode == 'distinct':
        count = generator.count_distinct_solutions(max_count=max_count)
    else:
        count = generator.count_solutions(max_count=max_count)
    
    # Debug: show slots per course after filtering
    slots_per_course = {}
    for course in courses:
        course_slots = generator.slot_map.get(course.id, [])
        slots_per_course[course.code] = len(course_slots)
    
    return jsonify({
        'count': count,
        'capped': count >= max_count,
        'debug': {
            'courses_count': len(courses),
            'slots_per_course': slots_per_course,
            'preferences': {
                'avoided_faculties': preferences.avoided_faculties
            }
        }
    })


@generate_bp.route('/suggest', methods=['POST'])
def suggest_timetable():
    """
    Generate initial batch of timetable suggestions.
    
    Request body:
    {
        "course_ids": [1, 2, 3],
        "preferences": {
            "time_mode": "morning",
            "avoid_early_morning": false,
            "avoid_late_evening": false,
            "preferred_faculties": ["FACULTY NAME"],
            "avoided_faculties": ["ANOTHER FACULTY"],
            "exclude_slots": ["A11", "B12"],
            "course_faculty_preferences": {"course_id": ["Fac1", "Fac2"]}
        }
    }
    """
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    data = request.get_json() or {}
    course_ids = data.get('course_ids', [])
    pref_data = data.get('preferences', {})
    
    if not course_ids:
        return jsonify({'error': 'No courses selected'}), 400
    
    # Ensure course_ids are integers
    try:
        course_ids = [int(cid) for cid in course_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid course ID format'}), 400
    
    # Get courses (scoped to user)
    if user_id:
        courses = Course.query.filter(
            Course.id.in_(course_ids),
            Course.user_id == user_id
        ).all()
    else:
        courses = Course.query.filter(
            Course.id.in_(course_ids),
            Course.guest_id == guest_id
        ).all()
    
    if not courses:
        # Debug: provide more info about what exists
        all_user_courses = []
        if user_id:
            all_user_courses = Course.query.filter_by(user_id=user_id).all()
        else:
            all_user_courses = Course.query.filter_by(guest_id=guest_id).all()
        
        # Also check unscoped
        unscoped_courses = Course.query.filter(Course.id.in_(course_ids)).all()
        
        return jsonify({
            'error': 'No valid courses found',
            'debug': {
                'requested_ids': course_ids,
                'user_id': user_id,
                'guest_id': guest_id,
                'user_course_count': len(all_user_courses),
                'user_course_ids': [c.id for c in all_user_courses[:10]],  # First 10
                'unscoped_match_count': len(unscoped_courses),
                'unscoped_owner_info': [(c.id, c.user_id, c.guest_id) for c in unscoped_courses[:5]]
            }
        }), 404
    
    # Build preferences
    # Build preferences
    preferences = GenerationPreferences(
        avoid_early_morning=pref_data.get('avoid_early_morning', False),
        avoid_late_evening=pref_data.get('avoid_late_evening', False),
        prefer_morning=pref_data.get('prefer_morning', False),
        prefer_afternoon=pref_data.get('prefer_afternoon', False),
        preferred_faculties=pref_data.get('preferred_faculties', []),
        avoided_faculties=pref_data.get('avoided_faculties', []),
        exclude_slots=pref_data.get('exclude_slots', []),
        time_mode=pref_data.get('time_mode', 'none'),
        course_faculty_preferences=pref_data.get('course_faculty_preferences', {})
    )
    
    
    # Generate DIVERSE solutions (very different from each other)
    limit = data.get('limit', 5)
    try:
        limit = int(limit)
        limit = max(1, min(limit, 100))  # Clamp between 1 and 100
    except (ValueError, TypeError):
        limit = 5
        
    generator = TimetableGenerator(courses, preferences)
    
    # Unified Generation Strategy:
    # Handles all 4 scenarios internally:
    # 1. NO FILTERS: Random 100
    # 2. TIME ONLY: 20k random → rank by time → top 100
    # 3. TEACHER ONLY: 20k random → tier by teacher count → rank by priority → top 100
    # 4. TIME + TEACHER: 20k random → tier by teacher count → rank by time → top 100
    
    solutions = generator.generate_unified(target_size=limit)
    
    # Extract method from first solution's details
    generation_method = solutions[0].details.get('method', 'unknown') if solutions else 'none'
    pool_size = solutions[0].details.get('pool_size', 0) if solutions else 0
    
    return jsonify({
        'success': True,
        'suggestions': [s.to_dict() for s in solutions],
        'count': len(solutions),
        'has_more': False,
        'generation_method': generation_method,
        'total_combinations': pool_size,
        'total_combinations': pool_size,
        'relaxed_constraints': False,  # Deprecated but kept for frontend compat
        'warnings': generator.warnings
    })



@generate_bp.route('/more', methods=['POST'])
def generate_more():
    """
    Generate next batch of suggestions (progressive loading).
    
    Request body:
    {
        "course_ids": [1, 2, 3],
        "preferences": {...},
        "offset": 5
    }
    """
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    data = request.get_json() or {}
    course_ids = data.get('course_ids', [])
    pref_data = data.get('preferences', {})
    offset = data.get('offset', 0)
    
    if not course_ids:
        return jsonify({'error': 'No courses selected'}), 400
    
    # Ensure course_ids are integers
    try:
        course_ids = [int(cid) for cid in course_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid course ID format'}), 400
    
    # Get courses (scoped to user) with eager loading
    eager_opts = (db.joinedload(Course.slots).joinedload(Slot.faculty),)
    if user_id:
        courses = Course.query.filter(
            Course.id.in_(course_ids),
            Course.user_id == user_id
        ).options(*eager_opts).all()
    else:
        courses = Course.query.filter(
            Course.id.in_(course_ids),
            Course.guest_id == guest_id
        ).options(*eager_opts).all()
    
    # Fallback if no courses found with scope filter
    if not courses:
        return jsonify({'error': 'No valid courses found'}), 404
    
    # Build preferences
    preferences = GenerationPreferences(
        avoid_early_morning=pref_data.get('avoid_early_morning', False),
        avoid_late_evening=pref_data.get('avoid_late_evening', False),
        prefer_morning=pref_data.get('prefer_morning', False),
        prefer_afternoon=pref_data.get('prefer_afternoon', False),
        preferred_faculties=pref_data.get('preferred_faculties', []),
        avoided_faculties=pref_data.get('avoided_faculties', []),
        exclude_slots=pref_data.get('exclude_slots', []),
        time_mode=pref_data.get('time_mode', 'none'),
        course_faculty_preferences=pref_data.get('course_faculty_preferences', {})
    )
    
    # Generate more solutions
    generator = TimetableGenerator(courses, preferences)
    solutions = generator.generate_batch(limit=5, offset=offset)
    
    return jsonify({
        'success': True,
        'suggestions': [s.to_dict() for s in solutions],
        'count': len(solutions),
        'offset': offset,
        'has_more': len(solutions) == 5
    })


@generate_bp.route('/apply', methods=['POST'])
def apply_suggestion():
    """
    Apply a timetable suggestion by registering all its slots.
    
    Request body:
    {
        "slot_ids": [1, 2, 3, 4]
    }
    """
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    data = request.get_json() or {}
    slot_ids = data.get('slot_ids', [])
    
    if not slot_ids:
        return jsonify({'error': 'No slots provided'}), 400
    
    # Convert string IDs to integers (handles JS precision issue)
    try:
        slot_ids = [int(sid) for sid in slot_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid slot ID format'}), 400
    
    # Get slots with their courses for ownership verification
    slots = Slot.query.filter(Slot.id.in_(slot_ids)).all()
    
    if len(slots) != len(slot_ids):
        return jsonify({'error': 'Some slots not found'}), 404
    
    # Security: Verify all slots belong to courses owned by this user/guest
    for slot in slots:
        if slot.course:
            course_owner_match = False
            if user_id and slot.course.user_id == user_id:
                course_owner_match = True
            elif guest_id and slot.course.guest_id == guest_id:
                course_owner_match = True
            
            if not course_owner_match:
                return jsonify({'error': 'Unauthorized: slot does not belong to your courses'}), 403
    
    try:
        # Clear existing registrations for this user (optional - could make this configurable)
        if user_id:
            Registration.query.filter_by(user_id=user_id).delete()
        else:
            Registration.query.filter_by(guest_id=guest_id).delete()
        
        # Create new registrations
        registrations = []
        for slot in slots:
            reg = Registration(slot_id=slot.id)
            if user_id:
                reg.user_id = user_id
            else:
                reg.guest_id = guest_id
            registrations.append(reg)
        
        db.session.add_all(registrations)
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Successfully registered {len(registrations)} courses',
            'registration_count': len(registrations)
        })
        
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@generate_bp.route('/preview-details', methods=['POST'])
def get_preview_details():
    """Get details for a list of slot IDs for previewing."""
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
        
    data = request.get_json() or {}
    slot_ids = data.get('slot_ids', [])
    
    if not slot_ids:
        return jsonify({'slots': []})
        
    # Convert to ints
    try:
        slot_ids = [int(sid) for sid in slot_ids]
    except:
        return jsonify({'error': 'Invalid IDs'}), 400
        
    # Fetch slots with Course and Faculty
    slots = Slot.query.filter(Slot.id.in_(slot_ids)).options(
        db.joinedload(Slot.course),
        db.joinedload(Slot.faculty)
    ).all()
    
    # Format for renderMiniTimetable (needs code, venue, faculty, slot_code)
    # The frontend expects a 'suggestion' object with 'slots' array.
    # Each slot item needs: slot_id, slot_code, course_code, faculty_name, venue.
    
    slot_list = []
    total_credits = 0
    
    for s in slots:
        if s.course:
            total_credits += s.course.c
            
        slot_list.append({
            'slot_id': s.id,
            'slot_code': s.slot_code,
            'course_code': s.course.code if s.course else 'N/A',
            'course_name': s.course.name if s.course else 'N/A',
            'faculty_name': s.faculty.name if s.faculty else 'TBA',
            'venue': s.venue,
            'credits': s.course.c if s.course else 0
        })
        
    return jsonify({
        'suggestion': {
            'slots': slot_list,
            'total_credits': total_credits,
            'details': {'teacher_match_count': 0} # Dummy
        }
    })


@generate_bp.route('/save', methods=['POST'])
def save_timetable():
    """Save a timetable configuration."""
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    
    data = request.get_json() or {}
    name = data.get('name', 'Saved Timetable')
    slot_ids = data.get('slot_ids', [])
    total_credits = data.get('total_credits', 0)
    course_count = data.get('course_count', 0)
    
    if not slot_ids:
        return jsonify({'error': 'No slots provided'}), 400
        
    try:
        import json
        # Sort IDs to ensure canonical representation for duplicate check
        slot_ids.sort()
        slot_ids_json = json.dumps(slot_ids)
        
        # Check for duplicates
        query = SavedTimetable.query
        if user_id:
            query = query.filter_by(user_id=user_id)
        else:
            query = query.filter_by(guest_id=guest_id)
            
        existing = query.filter_by(slot_ids_json=slot_ids_json).first()
        if existing:
            return jsonify({'success': False, 'message': 'This timetable configuration is already saved!'}), 409

        saved = SavedTimetable(
            name=name,
            slot_ids_json=slot_ids_json,
            total_credits=total_credits,
            course_count=course_count
        )
        
        if user_id:
            saved.user_id = user_id
        else:
            saved.guest_id = guest_id
            
        db.session.add(saved)
        db.session.commit()
        
        return jsonify({'success': True, 'saved_id': saved.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@generate_bp.route('/saved', methods=['GET'])
def get_saved_timetables():
    """Get all saved timetables for current user."""
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
        
    query = SavedTimetable.query
    if user_id:
        query = query.filter_by(user_id=user_id)
    else:
        query = query.filter_by(guest_id=guest_id)
        
    saved_list = query.order_by(SavedTimetable.created_at.desc()).all()
    
    return jsonify({
        'saved': [s.to_dict() for s in saved_list]
    })


@generate_bp.route('/saved/<int:saved_id>', methods=['DELETE'])
def delete_saved_timetable(saved_id):
    """Delete a saved timetable."""
    user_id, guest_id = get_user_scope()
    
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
        
    saved = SavedTimetable.query.get(saved_id)
    if not saved:
        return jsonify({'error': 'Saved timetable not found'}), 404
    
    # Verify ownership
    if (user_id and saved.user_id != user_id) or (guest_id and saved.guest_id != guest_id):
         return jsonify({'error': 'Unauthorized'}), 403
         
    try:
        db.session.delete(saved)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500

