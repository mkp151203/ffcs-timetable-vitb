"""Shared course library: merge identical uploads, rank versions and let data age out on its own.

Two uploads are the same version when they have the same course code and the same set of
(slot, venue, faculty) rows. Course name, LTPJC, type and category are typed differently by
different students, so they don't split versions; the first upload's details are kept.

Ranking (per course), by one score that balances completeness against who stands behind a version:
  - Completeness: faculty and slot counts compared with the fullest version of that course, worth up to 5.
  - Verifications 3 each, logged-in uploads 1, guest uploads 0.3, all fading with a 14-day half-life
    and ignored after 30 days.
  So a logged-in upload beats a guest one with the same or nearly the same data, but a version with only
  one or two slots loses to a much fuller one, whoever uploaded it. Ties go to the most recent activity.
"""

import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta

from flask import current_app

from models import db, SharedCourse, SharedCourseEvent

HALF_LIFE_DAYS = 14
CUTOFF_DAYS = 30
WEIGHT_VERIFY = 3.0
WEIGHT_UPLOAD = 1.0
WEIGHT_GUEST_UPLOAD = 0.3
WEIGHT_COMPLETENESS = 5.0
MAX_VERSIONS_PER_COURSE = 10
MAX_COURSES_PER_SEARCH = 20
BROWSE_PAGE_SIZE = 10


def contributor_hash(user_id, guest_id):
    """One-way code for a person: detects repeats without storing who they are."""
    who = f'u:{user_id}' if user_id else f'g:{guest_id}'
    key = (current_app.config.get('SHARED_LIBRARY_KEY') or current_app.config['SECRET_KEY']).encode()
    return hmac.new(key, who.encode(), hashlib.sha256).hexdigest()


def _norm(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().upper()


def slot_rows(slots):
    """The set of (slot, venue, faculty) rows that identifies a version's data. Seats are ignored."""
    return {(_norm(s['slot_code']), _norm(s['venue']), _norm(s['faculty'])) for s in slots}


def fingerprint(course, slots):
    """Same course code + same (slot, venue, faculty) rows => same fingerprint."""
    payload = json.dumps([_norm(course['code']), sorted(slot_rows(slots))], separators=(',', ':'))
    return hashlib.sha256(payload.encode()).hexdigest()


def _fill_blank_details(entry, course):
    """A merged upload can supply course type or category the first uploader left blank."""
    if not entry.course_type and course.get('course_type'):
        entry.course_type = course['course_type']
    if not entry.category and course.get('category'):
        entry.category = course['category']


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
            faculty_count=len({row[2] for row in slot_rows(slots)}),
            slot_count=len(slot_rows(slots)),
            created_at=at,
        )
        db.session.add(entry)
    else:
        _fill_blank_details(entry, course)
    # The newest upload carries the freshest seat counts; replaying an older one doesn't overwrite them
    if not entry.slots_json or at >= (entry.last_activity_at or at):
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
        'verify_score': sum(event_weight(e, now) for e in verifies),
        'upload_score': sum(event_weight(e, now) for e in uploads),
        'uploads': len(uploads),
        'verifications': len(verifies),
        'confirmed': any(e.kind == 'verify' or not e.is_guest for e in live),
        'viewer_uploaded': bool(viewer_hash) and any(e.contributor_hash == viewer_hash for e in uploads),
        'viewer_verified': bool(viewer_hash) and any(e.contributor_hash == viewer_hash for e in verifies),
    }


def rank_versions(versions):
    """Score each (entry, summary) against the fullest version of the same course, best first."""
    most_faculty = max((e.faculty_count or 0 for e, _ in versions), default=0) or 1
    most_slots = max((e.slot_count or 0 for e, _ in versions), default=0) or 1
    for entry, summary in versions:
        completeness = ((entry.faculty_count or 0) / most_faculty + (entry.slot_count or 0) / most_slots) / 2
        summary['rank'] = WEIGHT_COMPLETENESS * completeness + summary['score']
    versions.sort(key=lambda v: rank_key(*v), reverse=True)
    return versions


def rank_key(entry, summary):
    return (summary.get('rank', summary['score']), entry.last_activity_at or datetime.min)


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


def _group_versions(entries, now, viewer_hash=None):
    """{code: [(entry, summary), ...]} with the best version first. Versions with no live events are dropped."""
    events = _events_by_entry([e.id for e in entries])
    by_code = {}
    for entry in entries:
        summary = summarize(entry, events[entry.id], now, viewer_hash)
        if summary['uploads'] == 0 and summary['verifications'] == 0:
            continue
        by_code.setdefault(entry.code, []).append((entry, summary))
    for versions in by_code.values():
        rank_versions(versions)
    return by_code


def search(query_text, viewer_hash=None, include_hidden=False):
    """Find courses by code or name. Returns courses with all their versions, best version first."""
    now = _now()
    cutoff = now - timedelta(days=CUTOFF_DAYS)
    like = f'%{query_text}%'
    live = SharedCourse.query.filter(SharedCourse.last_activity_at >= cutoff)
    if not include_hidden:
        live = live.filter(SharedCourse.hidden.is_(False))
    # Versions of one course can carry different names, so match codes first, then load every version
    codes = [row[0] for row in live.filter(db.or_(SharedCourse.code.ilike(like), SharedCourse.name.ilike(like)))
             .with_entities(SharedCourse.code).distinct().limit(MAX_COURSES_PER_SEARCH * 3)]
    entries = (live.filter(SharedCourse.code.in_(codes)).order_by(SharedCourse.last_activity_at.desc())
               .limit(300).all() if codes else [])

    courses = []
    for code, versions in _group_versions(entries, now, viewer_hash).items():
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


def browse(page=1, per_page=BROWSE_PAGE_SIZE):
    """One page of every course in the library, A to Z by code, summarised by its best version."""
    now = _now()
    cutoff = now - timedelta(days=CUTOFF_DAYS)
    live = SharedCourse.query.filter(
        SharedCourse.hidden.is_(False),
        SharedCourse.last_activity_at >= cutoff,
        SharedCourse.events.any(SharedCourseEvent.created_at >= cutoff),
    )
    codes_q = live.with_entities(SharedCourse.code).distinct()
    total = codes_q.count()
    pages = max(1, -(-total // per_page))
    page = min(max(page, 1), pages)
    codes = [row[0] for row in codes_q.order_by(SharedCourse.code).offset((page - 1) * per_page).limit(per_page)]

    grouped = _group_versions(live.filter(SharedCourse.code.in_(codes)).all() if codes else [], now)
    courses = []
    for code in codes:
        versions = grouped.get(code)
        if not versions:
            continue
        best, summary = versions[0]
        latest = max(e.last_activity_at or e.created_at for e, _ in versions)
        courses.append({
            'code': code,
            'name': best.name,
            'credits': best.c,
            'faculty_count': best.faculty_count,
            'slot_count': best.slot_count,
            'verifications': summary['verifications'],
            'confirmed': summary['confirmed'],
            'version_count': len(versions),
            'updated_at': latest.isoformat() + 'Z',
        })
    return {'page': page, 'pages': pages, 'per_page': per_page, 'total': total, 'courses': courses}


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


def remerge(apply=True):
    """Recompute every fingerprint and merge versions that now count as the same. Commits when applying.

    Run after the fingerprint rules change. The oldest version of each group is kept; the others'
    uploads and verifications move onto it (one per person and kind, the latest kept).
    """
    groups = {}
    for entry in SharedCourse.query.order_by(SharedCourse.created_at, SharedCourse.id).all():
        groups.setdefault(fingerprint(entry.course_dict(), entry.slots), []).append(entry)

    merged = 0
    for fp, entries in groups.items():
        keeper, dupes = entries[0], entries[1:]
        if not apply:
            merged += len(dupes)
            continue
        events = {(e.kind, e.contributor_hash): e for e in keeper.events}
        for dupe in dupes:
            for event in list(dupe.events):
                key = (event.kind, event.contributor_hash)
                if key in events:
                    events[key].created_at = max(events[key].created_at, event.created_at)
                    events[key].is_guest = events[key].is_guest and event.is_guest
                    db.session.delete(event)
                else:
                    event.shared_course_id = keeper.id
                    events[key] = event
            _fill_blank_details(keeper, dupe.course_dict())
            keeper.hidden = keeper.hidden or dupe.hidden
            if (dupe.last_activity_at or datetime.min) > (keeper.last_activity_at or datetime.min):
                keeper.last_activity_at = dupe.last_activity_at
                keeper.slots_json = dupe.slots_json   # freshest seat counts
            db.session.flush()
            db.session.delete(dupe)
            merged += 1
        db.session.flush()
        rows = slot_rows(keeper.slots)
        keeper.fingerprint = fp
        keeper.faculty_count = len({row[2] for row in rows})
        keeper.slot_count = len(rows)
    if apply:
        db.session.commit()
    return {'versions_before': sum(len(e) for e in groups.values()), 'versions_after': len(groups),
            'merged': merged}


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
