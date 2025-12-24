from flask import Blueprint, jsonify, request, session
from models import db, Registration, Slot, User
from models.slot import get_slot_timing

registration_bp = Blueprint('registration', __name__)

def get_current_registrations_query():
    """Helper to get registrations query based on current session."""
    query = None
    if 'user_id' in session:
        query = Registration.query.filter_by(user_id=session['user_id'])
    elif 'guest_id' in session:
        query = Registration.query.filter_by(guest_id=session['guest_id'])
        
    if query:
        # Eager load Slot, Course, and Faculty to prevent N+1 queries
        return query.options(
            db.joinedload(Registration.slot).joinedload(Slot.course),
            db.joinedload(Registration.slot).joinedload(Slot.faculty)
        )
    return None

@registration_bp.route('/', methods=['GET'])
def get_registrations():
    """Get all registered courses."""
    query = get_current_registrations_query()
    registrations = query.all() if query else []
    
    return jsonify({
        'registrations': [reg.to_dict() for reg in registrations],
        'count': len(registrations),
        'total_credits': sum(
            reg.slot.course.c for reg in registrations 
            if reg.slot and reg.slot.course
        )
    })

@registration_bp.route('/', methods=['POST'])
def register_course():
    """Register a new course slot."""
    data = request.get_json()
    slot_id = data.get('slot_id')
    
    if not slot_id:
        return jsonify({'error': 'slot_id is required'}), 400
        
    query = get_current_registrations_query()
    if query is None:
        return jsonify({'error': 'No active session'}), 401
    
    # Check if slot exists
    slot = Slot.query.get(slot_id)
    if not slot:
        return jsonify({'error': 'Slot not found'}), 404
    
    # Check if already registered for this course (scoped to user)
    existing = query.join(Slot).filter(Slot.course_id == slot.course_id).first()
    if existing:
        return jsonify({'error': 'Already registered for this course'}), 400
    
    # Check for clashes logic needs to be scoped too
    clash_result = check_slot_clashes(slot)
    if clash_result['has_clash']:
        return jsonify({
            'error': 'Slot clash detected',
            'clashing_slots': clash_result['clashing_slots']
        }), 400
    
    # Create registration
    registration = Registration(slot_id=slot_id)
    if 'user_id' in session:
        registration.user_id = session['user_id']
    else:
        registration.guest_id = session['guest_id']
        
    db.session.add(registration)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'registration': registration.to_dict()
    }), 201

@registration_bp.route('/<int:reg_id>', methods=['DELETE'])
def delete_registration(reg_id):
    """Delete a registration."""
    # Ensure user owns this registration
    query = get_current_registrations_query()
    if query is None:
        return jsonify({'error': 'No active session'}), 401
        
    registration = query.filter_by(id=reg_id).first()
    if not registration:
        return jsonify({'error': 'Registration not found'}), 404
        
    db.session.delete(registration)
    db.session.commit()
    
    return jsonify({'success': True})


@registration_bp.route('/bulk-delete', methods=['POST'])
def bulk_delete_registrations():
    """Delete multiple registrations at once."""
    query = get_current_registrations_query()
    if query is None:
        return jsonify({'error': 'No active session'}), 401
    
    data = request.get_json() or {}
    reg_ids = data.get('registration_ids', [])
    
    if not reg_ids:
        return jsonify({'error': 'No registrations specified'}), 400
    
    # Convert string IDs to integers (handles JS precision issue)
    try:
        reg_ids = [int(rid) for rid in reg_ids]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid registration ID format'}), 400
    
    # Find registrations owned by current user
    registrations = query.filter(Registration.id.in_(reg_ids)).all()
    
    if not registrations:
        return jsonify({'error': 'No valid registrations found'}), 404
    
    deleted_count = len(registrations)
    for reg in registrations:
        db.session.delete(reg)
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'deleted_count': deleted_count
    })

@registration_bp.route('/<int:reg_id>', methods=['PUT'])
def update_registration(reg_id):
    """Update registration to a different slot."""
    data = request.get_json()
    new_slot_id = data.get('slot_id')
    
    if not new_slot_id:
        return jsonify({'error': 'slot_id is required'}), 400

    query = get_current_registrations_query()
    if query is None:
        return jsonify({'error': 'No active session'}), 401
        
    registration = query.filter_by(id=reg_id).first()
    if not registration:
        return jsonify({'error': 'Registration not found'}), 404
        
    # Get new slot
    new_slot = Slot.query.get(new_slot_id)
    if not new_slot:
        return jsonify({'error': 'Slot not found'}), 404
        
    # Ensure course matches (can only switch slots within same course)
    if registration.slot.course_id != new_slot.course_id:
        return jsonify({'error': 'Cannot change course, only slot'}), 400
        
    # Check clashes (excluding current registration)
    clash_result = check_slot_clashes(new_slot, exclude_reg_id=reg_id)
    if clash_result['has_clash']:
        return jsonify({
            'error': 'Slot clash detected',
            'clashing_slots': clash_result['clashing_slots']
        }), 400
        
    # Update slot
    registration.slot_id = new_slot_id
    db.session.commit()
    
    return jsonify({
        'success': True,
        'registration': registration.to_dict()
    })

@registration_bp.route('/check-clash', methods=['POST'])
def check_clash():
    """Check if a slot would clash with existing registrations."""
    data = request.get_json()
    slot_id = data.get('slot_id')
    exclude_reg_id = data.get('exclude_reg_id') # Optional exclusion
    
    if not slot_id:
        return jsonify({'error': 'slot_id is required'}), 400
    
    slot = Slot.query.get(slot_id)
    if not slot:
        return jsonify({'error': 'Slot not found'}), 404
    
    result = check_slot_clashes(slot, exclude_reg_id=exclude_reg_id)
    return jsonify(result)

@registration_bp.route('/check-clash-batch', methods=['POST'])
def check_clash_batch():
    """Check clashes for multiple slots in one go."""
    data = request.get_json()
    slot_ids = data.get('slot_ids', [])
    exclude_reg_id = data.get('exclude_reg_id')
    
    if not slot_ids:
        return jsonify({'results': {}})
        
    # Get all slots in one query to avoid N+1 inside the loop if possible
    # But check_slot_clashes takes a slot object. 
    # Let's fetch all slots first.
    slots = Slot.query.filter(Slot.id.in_(slot_ids)).all()
    
    results = {}
    
    # We could optimize check_slot_clashes to take a list of slots too, 
    # but for now iterating here is still way better than N HTTP requests.
    # The expensive part of check_slot_clashes is `get_current_registrations_query()`, 
    # which we can cache/fetch once here.
    
    # Optimization: Fetch registrations ONCE
    query = get_current_registrations_query()
    registrations = query.all() if query else []

    for slot in slots:
        results[slot.id] = check_slot_clashes(slot, exclude_reg_id=exclude_reg_id, existing_registrations=registrations)

    return jsonify({'results': results})


@registration_bp.route('/credits', methods=['GET'])
def get_credits():
    """Get current credit summary."""
    query = get_current_registrations_query()
    registrations = query.all() if query else []
    
    total_credits = sum(
        reg.slot.course.c for reg in registrations 
        if reg.slot and reg.slot.course
    )
    
    return jsonify({
        'total_credits': total_credits,
        'max_credits': 27,
        'min_credits': 16,
        'course_count': len(registrations)
    })


from models.slot import get_slot_timing

# ... (omitted)

def check_slot_clashes(new_slot, exclude_reg_id=None, existing_registrations=None):
    """Check if a new slot clashes with existing registrations."""
    # Get all individual slots from the new slot
    new_individual_slots = new_slot.get_individual_slots()
    
    # Get all registered slots for current user/guest
    if existing_registrations is None:
        query = get_current_registrations_query()
        registrations = query.all() if query else []
    else:
        registrations = existing_registrations
    
    clashing_slots = []
    
    
    # Mutual Exclusion Groups
    GROUP_C1 = {'C11', 'C12', 'C13'}
    GROUP_A2 = {'A21', 'A22', 'A23'}
    
    new_slots_set = set(new_individual_slots)
    
    for reg in registrations:
        # Exclude specified registration (for updates)
        if exclude_reg_id and reg.id == int(exclude_reg_id):
            continue

        if reg.slot:
            registered_individual_slots = reg.slot.get_individual_slots()
            reg_slots_set = set(registered_individual_slots)
            
            # --- Mutual Exclusion Check ---
            # Check if one set has any C1 slots and the other has any A2 slots
            has_new_C1 = not new_slots_set.isdisjoint(GROUP_C1)
            has_new_A2 = not new_slots_set.isdisjoint(GROUP_A2)
            
            has_reg_C1 = not reg_slots_set.isdisjoint(GROUP_C1)
            has_reg_A2 = not reg_slots_set.isdisjoint(GROUP_A2)
            
            if (has_new_C1 and has_reg_A2) or (has_new_A2 and has_reg_C1):
                 clashing_slots.append({
                    'slot_code': reg.slot.slot_code,
                    'course_code': reg.slot.course.code if reg.slot.course else '',
                    'course_name': reg.slot.course.name if reg.slot.course else '',
                    'reason': 'Mutual exclusion: C1 slots (C11, C12, C13) cannot be taken with A2 slots (A21, A22, A23)'
                })
            
            # Check for overlap
            for new_s in new_individual_slots:
                
                for reg_s in registered_individual_slots:
                    
                    # 1. Standard Clash: Same Timing
                    new_timing = get_slot_timing(new_s)
                    reg_timing = get_slot_timing(reg_s)
                    
                    if new_timing and reg_timing:
                        if (new_timing['day'] == reg_timing['day'] and 
                            new_timing['period'] == reg_timing['period']):
                            clashing_slots.append({
                                'slot_code': reg.slot.slot_code,
                                'course_code': reg.slot.course.code if reg.slot.course else '',
                                'course_name': reg.slot.course.name if reg.slot.course else '',
                                'reason': 'Time overlap'
                            })
                            break
                    

                
                if len(clashing_slots) > 0 and clashing_slots[-1] in clashing_slots[:-1]:
                    # Avoid duplicates
                     pass

    
    return {
        'has_clash': len(clashing_slots) > 0,
        'clashing_slots': clashing_slots
    }
