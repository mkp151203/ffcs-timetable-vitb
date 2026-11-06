"""Shared course library API: search, import into your timetable, and verify."""

import re

from flask import Blueprint, jsonify, request, session

from models import db, SharedCourse
from utils import shared_library
from utils.course_store import save_course_for_owner
from utils.security import rate_limit, server_error
from utils.validation import clean_text, validate_course, validate_slots

shared_bp = Blueprint('shared', __name__)

SEARCH_ALLOWED = re.compile(r"[^A-Za-z0-9 &.():'/-]")


def _viewer_hash():
    user_id, guest_id = session.get('user_id'), session.get('guest_id')
    if not user_id and not guest_id:
        return None
    return shared_library.contributor_hash(user_id, guest_id)


def _get_entry(entry_id):
    if not str(entry_id).isdigit():
        return None
    entry = SharedCourse.query.get(int(entry_id))
    if not entry or entry.hidden:
        return None
    return entry


@shared_bp.route('/search')
@rate_limit('shared-search', 120, 60)
def search_shared():
    """Search the library by course code or name."""
    text = SEARCH_ALLOWED.sub('', clean_text(request.args.get('q', ''), 60)).strip()
    if len(text) < 2:
        return jsonify({'query': text, 'courses': []})
    try:
        courses = shared_library.search(text, viewer_hash=_viewer_hash())
    except Exception as e:
        return server_error(e)
    return jsonify({'query': text, 'courses': courses, 'logged_in': bool(session.get('user_id'))})


@shared_bp.route('/browse')
@rate_limit('shared-search', 120, 60)
def browse_shared():
    """Page through every course in the library, A to Z."""
    page = request.args.get('page', '1')
    page = int(page) if page.isdigit() and len(page) <= 5 else 1
    try:
        return jsonify(shared_library.browse(page))
    except Exception as e:
        return server_error(e)


@shared_bp.route('/<entry_id>/import', methods=['POST'])
@rate_limit('shared-import', 60, 600)
def import_shared(entry_id):
    """Copy a library version into the caller's own timetable data."""
    user_id, guest_id = session.get('user_id'), session.get('guest_id')
    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401
    entry = _get_entry(entry_id)
    if not entry:
        return jsonify({'error': 'This course data is no longer available.'}), 404

    try:
        # Library data was validated on the way in; validate again in case the rules got stricter
        course = validate_course(entry.course_dict())
        slots, _ = validate_slots(entry.slots)
        if not slots:
            return jsonify({'error': 'This course data has no usable slots.'}), 400
        saved, added = save_course_for_owner(course, slots, user_id, guest_id)
        db.session.commit()
        return jsonify({'success': True, 'course_code': saved.code, 'slots_added': added})
    except Exception as e:
        db.session.rollback()
        return server_error(e)


@shared_bp.route('/<entry_id>/verify', methods=['POST', 'DELETE'])
@rate_limit('shared-verify', 60, 600)
def verify_shared(entry_id):
    """Logged-in students confirm (POST) or withdraw (DELETE) that a version is correct."""
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({'error': 'Log in with your VIT email to verify course data.'}), 401
    if session.get('is_impersonating'):
        return jsonify({'error': 'Verification is turned off while impersonating.'}), 403
    entry = _get_entry(entry_id)
    if not entry:
        return jsonify({'error': 'This course data is no longer available.'}), 404

    try:
        error = shared_library.set_verification(entry, user_id, verified=request.method == 'POST')
        if error:
            return jsonify({'error': error}), 400
        summary = shared_library.summary_for(entry, _viewer_hash())
        return jsonify({'success': True, 'verifications': summary['verifications'],
                        'viewer_verified': summary['viewer_verified']})
    except Exception as e:
        db.session.rollback()
        return server_error(e)
