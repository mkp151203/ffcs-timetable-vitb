"""Whitelist validation for everything users can send: course data, slots, names and messages.

Every import path (VTOP HTML, CSV, AI-assisted, manual entry, editing) runs its data through here
before it is stored, so markup or scripts can never be saved in the first place.
"""

import re
import unicodedata

MAX_SLOT_TOKENS = 8

COURSE_CODE_RE = re.compile(r'^[A-Z]{2,5}[0-9]{3,5}[A-Z]?$')
SLOT_TOKEN_RE = re.compile(r'^[A-Z]{1,3}[0-9]{1,3}$')
COURSE_TYPE_RE = re.compile(r'^[A-Za-z0-9 /()+&-]{0,30}$')
CATEGORY_RE = re.compile(r'^[A-Za-z0-9 /()+&-]{0,50}$')
VENUE_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ./()_&-]{0,49}$')
# Anything that looks like HTML, a script URL or an inline event handler
MARKUP_RE = re.compile(r'<\s*[/!?a-zA-Z]|javascript\s*:|data\s*:\s*text/html|\bon[a-z]+\s*=', re.IGNORECASE)

COURSE_NAME_EXTRA = set(" &-,.():/'+")
FACULTY_EXTRA = set(" .'-(),/")
FREE_TEXT_FORBIDDEN = set('<>`')


class ValidationError(ValueError):
    """Raised with a message that is safe to show to the user."""


def clean_text(value, max_len):
    """Normalise Unicode, drop invisible control characters and collapse whitespace."""
    if value is None:
        return ''
    text = unicodedata.normalize('NFKC', str(value))
    text = ''.join(ch for ch in text if ch in '\t\n ' or unicodedata.category(ch)[0] != 'C')
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:max_len] if len(text) > max_len else text


def _only_allowed(text, extra):
    return all(ch.isalnum() or ch in extra for ch in text)


def validate_course_code(value):
    code = clean_text(value, 20).upper().replace(' ', '')
    if not COURSE_CODE_RE.match(code):
        raise ValidationError('Course code must look like CSA3006 (letters followed by digits).')
    return code


def validate_course_name(value):
    name = clean_text(value, 200)
    if not name:
        raise ValidationError('Course name is required.')
    if len(name) > 200 or not _only_allowed(name, COURSE_NAME_EXTRA) or MARKUP_RE.search(name):
        raise ValidationError("Course name may only contain letters, numbers, spaces and & - , . ( ) : / '")
    return name


def validate_int(value, field, low, high, default=0):
    if value is None or value == '':
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValidationError(f'{field} must be a whole number.')
    if number < low or number > high:
        raise ValidationError(f'{field} must be between {low} and {high}.')
    return number


def validate_course_type(value):
    text = clean_text(value, 30)
    if not COURSE_TYPE_RE.match(text):
        raise ValidationError('Course type has invalid characters.')
    return text


def validate_category(value):
    text = clean_text(value, 50)
    if not CATEGORY_RE.match(text):
        raise ValidationError('Category has invalid characters.')
    return text


def validate_slot_code(value):
    code = clean_text(value, 50).upper().replace(' ', '').replace('/', '+')
    tokens = [t for t in code.split('+') if t]
    if not tokens or len(tokens) > MAX_SLOT_TOKENS or not all(SLOT_TOKEN_RE.match(t) for t in tokens):
        raise ValidationError('Slot must look like A11 or A11+A12+A13.')
    return '+'.join(tokens)


def validate_venue(value, allow_empty=True):
    venue = clean_text(value, 50).upper()
    if not venue:
        if allow_empty:
            return 'N/A'
        raise ValidationError('Venue is required.')
    if venue == 'N/A':
        return venue
    if not VENUE_RE.match(venue):
        raise ValidationError('Venue may only contain letters, numbers and - . / ( ) _')
    return venue


def validate_faculty(value, allow_empty=True):
    name = clean_text(value, 100)
    if not name:
        if allow_empty:
            return 'N/A'
        raise ValidationError('Faculty name is required.')
    if not _only_allowed(name, FACULTY_EXTRA) or not any(ch.isalpha() for ch in name) or MARKUP_RE.search(name):
        raise ValidationError("Faculty name may only contain letters, spaces and . ' - ( ) ,")
    return name


def validate_course(data):
    """Validate a parsed course dict (from HTML, CSV or JSON) and return a cleaned copy."""
    return {
        'code': validate_course_code(data.get('code')),
        'name': validate_course_name(data.get('name')),
        'l': validate_int(data.get('l'), 'L', 0, 30),
        't': validate_int(data.get('t'), 'T', 0, 30),
        'p': validate_int(data.get('p'), 'P', 0, 30),
        'j': validate_int(data.get('j'), 'J', 0, 30),
        'c': validate_int(data.get('c'), 'Credits', 0, 40),
        'course_type': validate_course_type(data.get('course_type')),
        'category': validate_category(data.get('category')),
    }


def validate_slot(data):
    """Validate one slot row and return a cleaned copy."""
    return {
        'slot_code': validate_slot_code(data.get('slot_code')),
        'venue': validate_venue(data.get('venue')),
        'faculty': validate_faculty(data.get('faculty')),
        'available_seats': validate_int(data.get('available_seats'), 'Available seats', 0, 1000),
        'class_nbr': None,
    }


def validate_slots(rows):
    """Split rows into valid cleaned slots and a count of rejected rows."""
    valid, rejected = [], 0
    for row in rows or []:
        try:
            valid.append(validate_slot(row))
        except ValidationError:
            rejected += 1
    return valid, rejected


def validate_free_text(value, field, max_len, required=True):
    """Messages, timetable names and similar: any readable text, but never markup."""
    text = clean_text(value, max_len + 1)
    if required and not text:
        raise ValidationError(f'{field} is required.')
    if len(text) > max_len:
        raise ValidationError(f'{field} must be at most {max_len} characters.')
    if any(ch in FREE_TEXT_FORBIDDEN for ch in text) or MARKUP_RE.search(text):
        raise ValidationError(f'{field} cannot contain HTML or code.')
    return text


def safe_filename(value):
    """A filename that is safe to echo back to the browser."""
    name = clean_text(value, 200).replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[^A-Za-z0-9 ._()-]', '_', name)
    return name[:100] or 'file'
