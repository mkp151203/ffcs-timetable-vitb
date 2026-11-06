"""Save validated course data into a user's or guest's own timetable data."""

from models import db, Course, Faculty, Slot


def save_course_for_owner(course_data, slots, user_id, guest_id):
    """Create the owner's course (or reuse one with the same code) and add any new slots.

    `course_data` and `slots` must already be cleaned by utils.validation. Does not commit.
    Returns (course, number_of_slots_added).
    """
    query = Course.query.filter_by(code=course_data['code'])
    query = query.filter_by(user_id=user_id) if user_id else query.filter_by(guest_id=guest_id)
    course = query.first()

    if not course:
        course = Course(
            code=course_data['code'],
            name=course_data['name'],
            l=course_data['l'],
            t=course_data['t'],
            p=course_data['p'],
            j=course_data['j'],
            c=course_data['c'],
            course_type=course_data['course_type'],
            category=course_data['category'],
            user_id=user_id,
            guest_id=None if user_id else guest_id
        )
        db.session.add(course)
        db.session.flush()

    # Faculty rows are shared by name
    faculty_names = {s['faculty'] for s in slots}
    faculty_map = {f.name: f for f in Faculty.query.filter(Faculty.name.in_(faculty_names)).all()} if faculty_names else {}
    missing = [Faculty(name=name) for name in faculty_names - set(faculty_map)]
    if missing:
        db.session.add_all(missing)
        db.session.flush()
        faculty_map.update({f.name: f for f in missing})

    existing = {(s.slot_code, s.venue) for s in Slot.query.filter_by(course_id=course.id).all()}
    new_slots = []
    for slot_data in slots:
        signature = (slot_data['slot_code'], slot_data['venue'])
        if signature in existing:
            continue
        new_slots.append(Slot(
            slot_code=slot_data['slot_code'],
            course_id=course.id,
            faculty_id=faculty_map[slot_data['faculty']].id,
            venue=slot_data['venue'],
            available_seats=slot_data['available_seats'],
            total_seats=70,
            class_nbr=None
        ))
        existing.add(signature)

    if new_slots:
        db.session.add_all(new_slots)
    return course, len(new_slots)
