"""Routes for auto-generating timetable suggestions."""

from flask import Blueprint, request, jsonify, session, render_template
from utils.security import server_error
from utils.validation import (ValidationError, validate_free_text, validate_course_code, validate_slot_code,
                              validate_faculty, validate_venue, validate_course_name, validate_int)


def clean_saved_slots(slot_ids):
    """Saved timetables hold slot ids or slot descriptions; keep only known, validated fields."""
    if not isinstance(slot_ids, list) or not slot_ids or len(slot_ids) > 100:
        raise ValidationError('Invalid timetable data.')
    if isinstance(slot_ids[0], dict):
        cleaned = []
        for s in slot_ids:
            if not isinstance(s, dict):
                raise ValidationError('Invalid timetable data.')
            item = {
                'course_code': validate_course_code(s.get('course_code')),
                'slot_code': validate_slot_code(s.get('slot_code')),
                'faculty_name': validate_faculty(s.get('faculty_name') or 'TBA'),
                'venue': validate_venue(s.get('venue')),
                'credits': validate_int(s.get('credits'), 'Credits', 0, 40),
            }
            if s.get('course_name'):
                item['course_name'] = validate_course_name(s.get('course_name'))
            if s.get('slot_id') is not None and str(s.get('slot_id')).isdigit():
                item['slot_id'] = str(s.get('slot_id'))
            cleaned.append(item)
        return cleaned
    if not all(str(sid).isdigit() for sid in slot_ids):
        raise ValidationError('Invalid timetable data.')
    return [str(sid) for sid in slot_ids]
from models import db, Course, Slot, Faculty, Registration, User, SavedTimetable
from utils.timetable_generator import TimetableGenerator, GenerationPreferences, MUTUAL_EXCLUSION_GROUPS
from models.slot import SLOT_TIMINGS
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
        'registered_count': len(registered_course_ids)
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
        'capped': count >= max_count
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
        return jsonify({'error': 'No valid courses found'}), 404
    
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
        limit = max(1, min(limit, 500))  # Clamp between 1 and 500
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



@generate_bp.route('/data', methods=['POST'])
def get_generation_data():
    """
    Return everything the browser needs to run generation locally
    (static/js/timetable-generator.js mirrors TimetableGenerator.generate_unified).

    Request body:
    {
        "course_ids": ["1", "2", "3"]
    }
    """
    user_id, guest_id = get_user_scope()

    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401

    data = request.get_json() or {}
    course_ids = data.get('course_ids', [])

    if not course_ids:
        return jsonify({'error': 'No courses selected'}), 400

    try:
        course_ids = [int(cid) for cid in course_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid course ID format'}), 400

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
        return jsonify({'error': 'No valid courses found'}), 404

    # Single query for all slots (avoids one query per course)
    all_slots = Slot.query.filter(Slot.course_id.in_([c.id for c in courses])).options(
        db.joinedload(Slot.faculty)
    ).order_by(Slot.id).all()

    slots_by_course = {}
    for slot in all_slots:
        slots_by_course.setdefault(slot.course_id, []).append(slot)

    return jsonify({
        'courses': [
            {
                'id': str(course.id),  # String to prevent JS precision loss
                'code': course.code,
                'name': course.name,
                'credits': course.c,
                'slots': [
                    {
                        'id': str(slot.id),
                        'slot_code': slot.slot_code,
                        'faculty_name': slot.faculty.name if slot.faculty else None,
                        'venue': slot.venue
                    } for slot in slots_by_course.get(course.id, [])
                ]
            } for course in courses
        ],
        'slot_timings': {
            code: {'day': t['day'], 'period': t['period']}
            for code, t in SLOT_TIMINGS.items()
        },
        'mutual_exclusion_groups': [
            [sorted(group_a), sorted(group_b)]
            for group_a, group_b in MUTUAL_EXCLUSION_GROUPS
        ]
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
    
    # Get courses (scoped to user); TimetableGenerator pre-fetches their slots in one query
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
    
    resolved_slot_ids = []
    
    # Check if slot_ids is a list of dictionaries (resilient format)
    if isinstance(slot_ids[0], dict):
        for s_data in slot_ids:
            course_code = s_data.get('course_code')
            slot_code = s_data.get('slot_code')
            
            # Find the course for this user
            query = Course.query.filter_by(code=course_code)
            if user_id:
                query = query.filter_by(user_id=user_id)
            else:
                query = query.filter_by(guest_id=guest_id)
            
            course = query.first()
            if course:
                # Find the slot, prefer exact faculty match if possible
                faculty_name = s_data.get('faculty_name')
                slot_query = Slot.query.filter_by(course_id=course.id, slot_code=slot_code)
                
                if faculty_name and faculty_name != 'TBA':
                    from models import Faculty
                    slot_query_fac = slot_query.join(Faculty).filter(Faculty.name == faculty_name)
                    slot = slot_query_fac.first()
                    if not slot:
                        slot = slot_query.first() # fallback
                else:
                    slot = slot_query.first()
                    
                if slot:
                    resolved_slot_ids.append(slot.id)
                else:
                    return jsonify({'error': f"Slot {slot_code} for course {course_code} is no longer available in your imported data."}), 404
            else:
                return jsonify({'error': f"Course {course_code} is no longer available in your imported data. Please re-import it."}), 404
    else:
        # Convert string IDs to integers (handles JS precision issue)
        try:
            resolved_slot_ids = [int(sid) for sid in slot_ids]
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid slot ID format'}), 400
    
    # Get slots with their courses for ownership verification
    slots = Slot.query.filter(Slot.id.in_(resolved_slot_ids)).all()
    
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
        return server_error(e)


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
        
    # Check if we have hydrated slots (resilient format)
    if isinstance(slot_ids[0], dict):
        total_credits = sum([s.get('credits', 0) for s in slot_ids])
        return jsonify({
            'suggestion': {
                'slots': slot_ids,
                'total_credits': total_credits,
                'details': {'teacher_match_count': 0}
            }
        })
        
    # Convert to ints for old format
    try:
        slot_ids = [int(sid) for sid in slot_ids]
    except:
        return jsonify({'error': 'Invalid IDs'}), 400
        
    # Fetch slots with Course and Faculty (only slots of the caller's own courses)
    owner_filter = Course.user_id == user_id if user_id else Course.guest_id == guest_id
    slots = Slot.query.join(Course, Slot.course_id == Course.id).filter(Slot.id.in_(slot_ids), owner_filter).options(
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
    
    data = request.get_json(silent=True) or {}
    slot_ids = data.get('slot_ids', [])
    
    if not slot_ids:
        return jsonify({'error': 'No slots provided'}), 400

    try:
        name = validate_free_text(data.get('name') or 'Saved Timetable', 'Timetable name', 100)
        slot_ids = clean_saved_slots(slot_ids)
        total_credits = validate_int(data.get('total_credits'), 'Total credits', 0, 500)
        course_count = validate_int(data.get('course_count'), 'Course count', 0, 100)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
        
    try:
        import json
        # Sort IDs to ensure canonical representation for duplicate check
        if slot_ids and isinstance(slot_ids[0], dict):
            # Sort dictionaries by slot_code to ensure consistent ordering
            slot_ids.sort(key=lambda x: str(x.get('slot_code', '')))
        else:
            # Sort strings or integers
            slot_ids.sort(key=str)
        
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
        return server_error(e)


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
        return server_error(e)

