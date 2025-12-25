"""CSV parser for course data import."""

import csv
import io


def parse_course_csv(content: str) -> dict:
    """
    Parse CSV content containing course and slot data.
    
    Format:
    - First row: Headers for course info (course_code, course_name, l, t, p, j, c, course_type, category)
    - Second row: Course details
    - Third row: Headers for slots (slot_code, faculty, venue, available_seats)
    - Remaining rows: Slot/faculty data
    
    Returns dict with same structure as HTML parser:
    {
        'course': {...},
        'slots': [...]
    }
    """
    content = content.strip()
    if not content:
        raise ValueError("Empty CSV content")
    
    lines = content.split('\n')
    if len(lines) < 4:
        raise ValueError("CSV must have at least 4 rows: course headers, course data, slot headers, slot data")
    
    # Parse course header and data (first two rows)
    course_reader = csv.DictReader(io.StringIO('\n'.join(lines[0:2])))
    course_headers = set(h.strip().lower() for h in (course_reader.fieldnames or []))
    
    required_course_cols = {'course_code', 'course_name'}
    missing = required_course_cols - course_headers
    if missing:
        raise ValueError(f"Missing required course columns: {', '.join(missing)}")
    
    course_row = None
    for row in course_reader:
        normalized = {k.strip().lower(): (v.strip() if v else '') for k, v in row.items()}
        course_code = normalized.get('course_code', '').strip()
        course_name = normalized.get('course_name', '').strip()
        
        if course_code and course_name:
            course_row = normalized
            break
    
    if not course_row:
        raise ValueError("No valid course data found in CSV")
    
    course_data = {
        'code': course_row.get('course_code', '').strip(),
        'name': course_row.get('course_name', '').strip(),
        'l': _safe_int(course_row.get('l', '0')),
        't': _safe_int(course_row.get('t', '0')),
        'p': _safe_int(course_row.get('p', '0')),
        'j': _safe_int(course_row.get('j', '0')),
        'c': _safe_int(course_row.get('c', '0')),
        'course_type': course_row.get('course_type', 'Theory').strip() or 'Theory',
        'category': course_row.get('category', 'Elective').strip() or 'Elective',
    }
    
    # Parse slot header and data (remaining rows starting from row 3)
    slot_content = '\n'.join(lines[2:])
    slot_reader = csv.DictReader(io.StringIO(slot_content))
    slot_headers = set(h.strip().lower() for h in (slot_reader.fieldnames or []))
    
    required_slot_cols = {'slot_code', 'faculty'}
    missing = required_slot_cols - slot_headers
    if missing:
        raise ValueError(f"Missing required slot columns: {', '.join(missing)}")
    
    slots = []
    for row in slot_reader:
        if not any(row.values()):
            continue
        
        normalized = {k.strip().lower(): (v.strip() if v else '') for k, v in row.items()}
        slot_code = normalized.get('slot_code', '').strip()
        faculty = normalized.get('faculty', '').strip()
        
        if not slot_code or not faculty:
            continue
        
        slots.append({
            'slot_code': slot_code,
            'faculty': faculty,
            'venue': normalized.get('venue', 'TBA').strip() or 'TBA',
            'available_seats': _safe_int(normalized.get('available_seats', '70'), default=70),
            'class_nbr': None,
        })
    
    if not slots:
        raise ValueError("No valid slot data found in CSV")
    
    return {
        'course': course_data,
        'slots': slots
    }


def _safe_int(value: str, default: int = 0) -> int:
    """Safely convert string to int with default."""
    try:
        return int(value) if value else default
    except ValueError:
        return default
