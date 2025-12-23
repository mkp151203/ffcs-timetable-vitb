from flask import Blueprint, render_template, session
from models import db, Registration, User, Slot, Course, Faculty
from models.slot import SLOT_TIMINGS
import uuid

main_bp = Blueprint('main', __name__)

# Color palette for different courses (no gradients per user preference)
COURSE_COLORS = [
    '#90EE90',  # Light green
    '#87CEEB',  # Sky blue
    '#FFB6C1',  # Light pink
    '#DDA0DD',  # Plum
    '#F0E68C',  # Khaki
    '#FF7F50',  # Coral (Replaced Pale Green)
    '#00CED1',  # Dark Turquoise (Replaced Light Blue)
    '#FFE4B5',  # Moccasin
    '#E6E6FA',  # Lavender
    '#FFDAB9',  # Peach puff
    '#DA70D6',  # Orchid (Replaced Powder Blue)
    '#FFA07A',  # Light salmon
]


@main_bp.route('/')
def index():
    """Main timetable page."""
    current_user = None
    registrations = []
    
    # Eager load options
    eager_options = (
        db.joinedload(Registration.slot).joinedload(Slot.course),
        db.joinedload(Registration.slot).joinedload(Slot.faculty)
    )

    # Check for logged-in user
    if 'user_id' in session:
        current_user = User.query.get(session['user_id'])
        registrations = Registration.query.filter_by(user_id=session['user_id']).options(*eager_options).all()
    else:
        # Check/Create guest session
        if 'guest_id' not in session:
            session['guest_id'] = str(uuid.uuid4())
        registrations = Registration.query.filter_by(guest_id=session['guest_id']).options(*eager_options).all()
    
    # Assign colors to each unique course
    course_colors = {}
    color_index = 0
    for reg in registrations:
        if reg.slot and reg.slot.course:
            course_code = reg.slot.course.code
            if course_code not in course_colors:
                course_colors[course_code] = COURSE_COLORS[color_index % len(COURSE_COLORS)]
                color_index += 1
    
    # Build a map of occupied slots
    occupied_slots = {}
    for reg in registrations:
        if reg.slot:
            course_code = reg.slot.course.code if reg.slot.course else ''
            for slot_code in reg.slot.get_individual_slots():
                occupied_slots[slot_code] = {
                    'registration_id': reg.id,
                    'course_code': course_code,
                    'course_name': reg.slot.course.name if reg.slot.course else '',
                    'venue': reg.slot.venue,
                    'faculty': reg.slot.faculty.name if reg.slot.faculty else '',
                    'slot_code': reg.slot.slot_code,
                    'color': course_colors.get(course_code, '#90EE90')
                }
    
    # Calculate credits
    total_credits = sum(
        reg.slot.course.c for reg in registrations 
        if reg.slot and reg.slot.course
    )
    course_count = len(registrations)
    
    # Define timetable structure
    days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT']
    periods = [
        {'num': 1, 'start': '08:30', 'end': '10:00'},
        {'num': 2, 'start': '10:05', 'end': '11:35'},
        {'num': 3, 'start': '11:40', 'end': '13:10'},
        {'num': 'lunch', 'start': 'Lunch', 'end': ''},
        {'num': 4, 'start': '13:15', 'end': '14:45'},
        {'num': 5, 'start': '14:50', 'end': '16:20'},
        {'num': 6, 'start': '16:25', 'end': '17:55'},
        {'num': 7, 'start': '18:00', 'end': '19:30'},
    ]
    
    # Day letter mapping
    day_letters = {'MON': 'A', 'TUE': 'B', 'WED': 'C', 'THU': 'D', 'FRI': 'E', 'SAT': 'F'}
    
    return render_template(
        'index.html',
        days=days,
        periods=periods,
        day_letters=day_letters,
        occupied_slots=occupied_slots,
        total_credits=total_credits,
        course_count=course_count,
        max_credits=27,
        min_credits=16,
        current_user=current_user
    )
