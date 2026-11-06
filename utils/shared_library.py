"""Shared course library: merge identical uploads, rank versions and let data age out on its own.

Ranking (per course):
  1. Versions confirmed by at least one logged-in student come before guest-only versions.
  2. Score = sum of event weights, each fading with a 14-day half-life and ignored after 60 days.
     Verifications weigh 3, logged-in uploads 1, guest uploads 0.3.
  3. Ties: more faculty, then more slots, then the most recent activity.
"""

import hashlib
import hmac
import json
from datetime import datetime, timedelta

from flask import current_app

from models import db, SharedCourse, SharedCourseEvent

HALF_LIFE_DAYS = 14
CUTOFF_DAYS = 60
WEIGHT_VERIFY = 3.0
WEIGHT_UPLOAD = 1.0
WEIGHT_GUEST_UPLOAD = 0.3
MAX_VERSIONS_PER_COURSE = 10
MAX_COURSES_PER_SEARCH = 20


def contributor_hash(user_id, guest_id):
    """One-way code for a person: detects repeats without storing who they are."""
    who = f'u:{user_id}' if user_id else f'g:{guest_id}'
    key = (current_app.config.get('SHARED_LIBRARY_KEY') or current_app.config['SECRET_KEY']).encode()
    return hmac.new(key, who.encode(), hashlib.sha256).hexdigest()


def fingerprint(course, slots):
    """Same course details + same (slot, venue, faculty) set => same fingerprint. Seats are ignored."""
    header = [course['code'], course['name'].upper(), course['l'], course['t'], course['p'], course['j'],
              course['c'], (course['course_type'] or '').upper(), (course['category'] or '').upper()]
    rows = sorted({(s['slot_code'], s['venue'], s['faculty'].upper()) for s in slots})
    payload = json.dumps([header, rows], separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _now():
    return datetime.utcnow()


def contribute(course, slots, user_id, guest_id, at=None):
    """Add an upload to the library (or merge it into its identical version). Commits.

    `course` and `slots` must already be validated. Returns the SharedCourse, or None if skipped.
    """
    if not slots or not (user_id or guest_id):
        return None
    at = at or _now()
    fp = fingerprint(course, slots)
    who = contributor_hash(user_id, guest_id)

    entry = SharedCourse.query.filter_by(fingerprint=fp).first()
    public_slots = [{'slot_code': s['slot_code'], 'venue': s['venue'], 'faculty': s['faculty'],
                     'available_seats': s['available_seats']} for s in slots]
    if not entry:
        entry = SharedCourse(
            fingerprint=fp,
            code=course['code'], name=course['name'],
            l=course['l'], t=course['t'], p=course['p'], j=course['j'], c=course['c'],
            course_type=course['course_type'], category=course['category'],
            faculty_count=len({s['faculty'].upper() for s in slots}),
            slot_count=len({(s['slot_code'], s['venue'], s['faculty'].upper()) for s in slots}),
            created_at=at,
        )
        db.session.add(entry)
    # Latest upload carries the freshest seat counts
    entry.slots_json = json.dumps(public_slots)
    entry.last_activity_at = max(entry.last_activity_at or at, at)
    db.session.flush()

    event = SharedCourseEvent.query.filter_by(shared_course_id=entry.id, kind='upload', contributor_hash=who).first()
    if event:
        # The same person uploading again doesn't count twice, but it does show the data is still current
        event.created_at = max(event.created_at, at)
    else:
        db.session.add(SharedCourseEvent(shared_course_id=entry.id, kind='upload', contributor_hash=who,
                                         is_guest=not user_id, created_at=at))
    db.session.commit()
    return entry


def event_weight(event, now):
    age_days = (now - event.created_at).total_seconds() / 86400
    if age_days > CUTOFF_DAYS:
        return 0.0
    if event.kind == 'verify':
        base = WEIGHT_VERIFY
    else:
        base = WEIGHT_GUEST_UPLOAD if event.is_guest else WEIGHT_UPLOAD
    return base * 0.5 ** (max(age_days, 0) / HALF_LIFE_DAYS)


def summarize(entry, events, now, viewer_hash=None):
    """Score and counts for one version, from its events."""
    live = [e for e in events if (now - e.created_at).total_seconds() <= CUTOFF_DAYS * 86400]
    uploads = [e for e in live if e.kind == 'upload']
    verifies = [e for e in live if e.kind == 'verify']
    return {
        'score': sum(event_weight(e, now) for e in live),
        'uploads': len(uploads),
        'verifications': len(verifies),
        'confirmed': any(e.kind == 'verify' or not e.is_guest for e in live),
        'viewer_uploaded': bool(viewer_hash) and any(e.contributor_hash == viewer_hash for e in uploads),
        'viewer_verified': bool(viewer_hash) and any(e.contributor_hash == viewer_hash for e in verifies),
    }


def rank_key(entry, summary):
    return (
        1 if summary['confirmed'] else 0,
        summary['score'],
        entry.faculty_count or 0,
        entry.slot_count or 0,
        entry.last_activity_at or datetime.min,
    )


def serialize(entry, summary, include_slots=True):
    data = {
        'id': str(entry.id),
        'code': entry.code,
        'name': entry.name,
        'ltpjc': f'{entry.l} {entry.t} {entry.p} {entry.j} {entry.c}',
        'credits': entry.c,
        'course_type': entry.course_type or '',
        'category': entry.category or '',
        'faculty_count': entry.faculty_count,
        'slot_count': entry.slot_count,
        'uploads': summary['uploads'],
        'verifications': summary['verifications'],
        'confirmed': summary['confirmed'],
        'score': round(summary['score'], 3),
        'updated_at': (entry.last_activity_at or entry.created_at).isoformat() + 'Z',
        'viewer_uploaded': summary['viewer_uploaded'],
        'viewer_verified': summary['viewer_verified'],
    }
    if include_slots:
        data['slots'] = entry.slots[:300]
    return data


def _events_by_entry(entry_ids):
    grouped = {i: [] for i in entry_ids}
    if entry_ids:
        for e in SharedCourseEvent.query.filter(SharedCourseEvent.shared_course_id.in_(entry_ids)).all():
            grouped[e.shared_course_id].append(e)
    return grouped


def search(query_text, viewer_hash=None, include_hidden=False):
    """Find courses by code or name. Returns courses with their versions, best version first."""
    now = _now()
    cutoff = now - timedelta(days=CUTOFF_DAYS)
    like = f'%{query_text}%'
    q = SharedCourse.query.filter(
        db.or_(SharedCourse.code.ilike(like), SharedCourse.name.ilike(like)),
        SharedCourse.last_activity_at >= cutoff,
    )
    if not include_hidden:
        q = q.filter(SharedCourse.hidden.is_(False))
    entries = q.order_by(SharedCourse.last_activity_at.desc()).limit(300).all()

    events = _events_by_entry([e.id for e in entries])
    by_code = {}
    for entry in entries:
        summary = summarize(entry, events[entry.id], now, viewer_hash)
        if summary['uploads'] == 0 and summary['verifications'] == 0:
            continue
        by_code.setdefault(entry.code, []).append((entry, summary))

    courses = []
    for code, versions in by_code.items():
        versions.sort(key=lambda v: rank_key(*v), reverse=True)
        best_entry, best_summary = versions[0]
        courses.append({
            'code': code,
            'name': best_entry.name,
            'best_key': rank_key(best_entry, best_summary),
            'versions': [serialize(e, s) for e, s in versions[:MAX_VERSIONS_PER_COURSE]],
            'version_count': len(versions),
        })
    # Exact code matches first, then courses with the strongest best version
    upper = query_text.upper()
    courses.sort(key=lambda c: (c['code'] == upper, c['best_key']), reverse=True)
    for c in courses:
        c.pop('best_key')
    return courses[:MAX_COURSES_PER_SEARCH]


def summary_for(entry, viewer_hash=None):
    return summarize(entry, list(entry.events), _now(), viewer_hash)


def set_verification(entry, user_id, verified):
    """Add or remove a logged-in student's verification. Commits. Returns an error message or None."""
    who = contributor_hash(user_id, None)
    uploaded = SharedCourseEvent.query.filter_by(shared_course_id=entry.id, kind='upload', contributor_hash=who).first()
    existing = SharedCourseEvent.query.filter_by(shared_course_id=entry.id, kind='verify', contributor_hash=who).first()
    if verified:
        if uploaded:
            return 'You uploaded this version, so it already counts. Only other students can verify it.'
        if not existing:
            db.session.add(SharedCourseEvent(shared_course_id=entry.id, kind='verify', contributor_hash=who,
                                             is_guest=False, created_at=_now()))
            entry.last_activity_at = _now()
    elif existing:
        db.session.delete(existing)
    db.session.commit()
    return None


def cleanup(now=None):
    """Drop events older than the cutoff and versions with no activity inside it. Commits."""
    now = now or _now()
    cutoff = now - timedelta(days=CUTOFF_DAYS)
    removed_events = SharedCourseEvent.query.filter(SharedCourseEvent.created_at < cutoff).delete(synchronize_session=False)
    stale_ids = [row[0] for row in db.session.query(SharedCourse.id).filter(SharedCourse.last_activity_at < cutoff).all()]
    if stale_ids:
        SharedCourseEvent.query.filter(SharedCourseEvent.shared_course_id.in_(stale_ids)).delete(synchronize_session=False)
        SharedCourse.query.filter(SharedCourse.id.in_(stale_ids)).delete(synchronize_session=False)
    db.session.commit()
    return {'events_removed': removed_events or 0, 'versions_removed': len(stale_ids)}


def reset_all():
    """Admin: empty the whole library. Commits."""
    events = SharedCourseEvent.query.delete(synchronize_session=False)
    versions = SharedCourse.query.delete(synchronize_session=False)
    db.session.commit()
    return {'events_removed': events or 0, 'versions_removed': versions or 0}
