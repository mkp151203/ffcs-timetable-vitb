from flask import Blueprint, render_template, session, redirect, url_for, flash, abort, current_app, request
from models import db, User, Course, Registration, SavedTimetable, Rating
from functools import wraps
import re
from datetime import datetime, timezone, timedelta

admin_bp = Blueprint('admin', __name__)

IST = timezone(timedelta(hours=5, minutes=30))

@admin_bp.app_template_filter('ist')
def format_ist(value, fmt='%Y-%m-%d %H:%M'):
    """Format a stored UTC datetime in Indian Standard Time."""
    if not value:
        return ''
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(IST).strftime(fmt) + ' IST'

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please login to access admin area.", "error")
            return redirect(url_for('auth.login'))
        
        # Check if actual user (before impersonation) is admin
        # If impersonating, we curb admin rights? Or do we allow admin-as-user to still see admin panel?
        # Let's check the 'original_user_id' if it exists, otherwise 'user_id'
        
        checker_id = session.get('original_user_id', session.get('user_id'))
        user = User.query.get(checker_id)
        
        if not user or user.email not in current_app.config.get('ADMIN_EMAILS', []):
            abort(403)
            
        return f(*args, **kwargs)
    return decorated_function

@admin_bp.route('/users')
@admin_required
def list_users():
    """List all registered users and active guest sessions with stats."""
    # 1. Registered Users
    users = User.query.all()
    
    # Bulk Count Courses
    course_counts = db.session.query(
        Course.user_id, db.func.count(Course.id)
    ).filter(Course.user_id.isnot(None)).group_by(Course.user_id).all()
    course_map = {uid: count for uid, count in course_counts}

    # Bulk Count Registrations
    reg_counts = db.session.query(
        Registration.user_id, db.func.count(Registration.id)
    ).filter(Registration.user_id.isnot(None)).group_by(Registration.user_id).all()
    reg_map = {uid: count for uid, count in reg_counts}

    # Bulk Count Saved Timetables
    saved_counts = db.session.query(
        SavedTimetable.user_id, db.func.count(SavedTimetable.id)
    ).filter(SavedTimetable.user_id.isnot(None)).group_by(SavedTimetable.user_id).all()
    saved_map = {uid: count for uid, count in saved_counts}

    user_stats = []
    for u in users:
        user_stats.append({
            'user': u,
            'course_count': course_map.get(u.id, 0),
            'registration_count': reg_map.get(u.id, 0),
            'saved_count': saved_map.get(u.id, 0)
        })
        
    # 2. Guest Sessions
    # Find distinct guest_ids with timestamps (min created_at)
    guest_courses = db.session.query(
        Course.guest_id, 
        db.func.count(Course.id),
        db.func.min(Course.created_at)
    ).filter(Course.guest_id.isnot(None), Course.user_id.is_(None)).group_by(Course.guest_id).all()
    
    # Organize into a dict
    guest_map = {}
    for gid, count, first_seen in guest_courses:
        guest_map[gid] = {
            'id': gid, 
            'course_count': count, 
            'registration_count': 0,
            'saved_count': 0,
            'created_at': first_seen
        }
        
    # Add registration counts/timestamps for guests
    guest_regs = db.session.query(
        Registration.guest_id, 
        db.func.count(Registration.id),
        db.func.min(Registration.registered_at)
    ).filter(Registration.guest_id.isnot(None), Registration.user_id.is_(None)).group_by(Registration.guest_id).all()
    
    for gid, count, first_seen in guest_regs:
        if gid not in guest_map:
            guest_map[gid] = {
                'id': gid, 
                'course_count': 0, 
                'registration_count': 0,
                'saved_count': 0,
                'created_at': first_seen
            }
        guest_map[gid]['registration_count'] = count
        if not guest_map[gid]['created_at'] or (first_seen and first_seen < guest_map[gid]['created_at']):
            guest_map[gid]['created_at'] = first_seen

    # Add saved timetable counts for guests
    guest_saved = db.session.query(
        SavedTimetable.guest_id,
        db.func.count(SavedTimetable.id),
        db.func.min(SavedTimetable.created_at)
    ).filter(SavedTimetable.guest_id.isnot(None), SavedTimetable.user_id.is_(None)).group_by(SavedTimetable.guest_id).all()

    for gid, count, first_seen in guest_saved:
        if gid not in guest_map:
            guest_map[gid] = {
                'id': gid, 
                'course_count': 0, 
                'registration_count': 0,
                'saved_count': count,
                'created_at': first_seen
            }
        else:
            guest_map[gid]['saved_count'] = count
            if not guest_map[gid]['created_at'] or (first_seen and first_seen < guest_map[gid]['created_at']):
                guest_map[gid]['created_at'] = first_seen

    # Add rating guests
    guest_ratings = db.session.query(
        Rating.guest_id,
        db.func.min(Rating.created_at)
    ).filter(Rating.guest_id.isnot(None), Rating.user_id.is_(None)).group_by(Rating.guest_id).all()

    for gid, first_seen in guest_ratings:
        if gid not in guest_map:
            guest_map[gid] = {
                'id': gid, 
                'course_count': 0, 
                'registration_count': 0,
                'saved_count': 0,
                'created_at': first_seen
            }
        elif not guest_map[gid]['created_at'] or (first_seen and first_seen < guest_map[gid]['created_at']):
            guest_map[gid]['created_at'] = first_seen
        
    # Newest guests first; guests with no timestamp go last
    guest_stats = sorted(
        guest_map.values(),
        key=lambda g: (g['created_at'] or datetime.min).replace(tzinfo=None),
        reverse=True
    )


    
    return render_template('admin_users.html', users=user_stats, guests=guest_stats)

@admin_bp.route('/impersonate/<int:user_id>', methods=['POST'])
@admin_required
def impersonate_user(user_id):
    """Start impersonating a registered user."""
    target_user = User.query.get_or_404(user_id)
    
    real_admin_id = session.get('original_user_id', session.get('user_id'))
    
    if real_admin_id == user_id:
        flash("You cannot impersonate yourself.", "warning")
        return redirect(url_for('admin.list_users'))
        
    if 'original_user_id' not in session:
        session['original_user_id'] = session['user_id']
        
    session['user_id'] = user_id
    session['is_impersonating'] = True
    session.pop('guest_id', None) # Clear guest context
    
    flash(f"Now impersonating {target_user.name}", "success")
    return redirect(url_for('main.index'))

@admin_bp.route('/impersonate-guest/<guest_id>', methods=['POST'])
@admin_required
def impersonate_guest(guest_id):
    """Start impersonating a guest session."""
    if not re.fullmatch(r'[0-9a-fA-F-]{8,64}', guest_id or ''):
        abort(404)
    if 'original_user_id' not in session:
        session['original_user_id'] = session['user_id']
        
    session.pop('user_id', None) # Remove user context
    session['guest_id'] = guest_id
    session['is_impersonating'] = True
    
    flash(f"Now impersonating Guest {guest_id[:8]}...", "success")
    return redirect(url_for('main.index'))

@admin_bp.route('/stop-impersonate', methods=['POST'])
def stop_impersonation():
    """Stop impersonation and return to admin account."""
    if 'original_user_id' in session:
        session['user_id'] = session['original_user_id']
        session.pop('original_user_id', None)
        session.pop('is_impersonating', None)
        session.pop('guest_id', None) # Clear any guest ID set during impersonation
        flash("Restored admin session.", "success")
    
    return redirect(url_for('admin.list_users'))



# ==================== Shared course library ====================

@admin_bp.route('/shared')
@admin_required
def shared_library_page():
    """Browse, hide or remove shared course data."""
    from utils import shared_library
    from utils.validation import clean_text
    query = re.sub(r"[^A-Za-z0-9 &.():'/-]", '', clean_text(request.args.get('q', ''), 60)).strip()
    courses = shared_library.search(query or '', include_hidden=True) if len(query) >= 2 else []
    if not query:
        # Without a search, show the most recently active versions
        from models import SharedCourse
        recent = SharedCourse.query.order_by(SharedCourse.last_activity_at.desc()).limit(50).all()
        courses = []
        for entry in recent:
            summary = shared_library.summary_for(entry)
            courses.append({'code': entry.code, 'name': entry.name, 'version_count': 1,
                            'versions': [dict(shared_library.serialize(entry, summary, include_slots=False),
                                              hidden=entry.hidden)]})
    else:
        from models import SharedCourse
        hidden_ids = {str(e.id) for e in SharedCourse.query.filter(SharedCourse.hidden.is_(True)).all()}
        for course in courses:
            for v in course['versions']:
                v['hidden'] = v['id'] in hidden_ids
    from models import SharedCourse, SharedCourseEvent
    stats = {'versions': SharedCourse.query.count(), 'events': SharedCourseEvent.query.count()}
    return render_template('admin_shared.html', courses=courses, query=query, stats=stats)


@admin_bp.route('/shared/<int:entry_id>/<action>', methods=['POST'])
@admin_required
def shared_library_action(entry_id, action):
    from models import SharedCourse
    entry = SharedCourse.query.get_or_404(entry_id)
    if action == 'hide':
        entry.hidden = True
        flash(f'Hidden {entry.code} version from search.', 'success')
    elif action == 'unhide':
        entry.hidden = False
        flash(f'{entry.code} version is visible again.', 'success')
    elif action == 'delete':
        db.session.delete(entry)
        flash(f'Deleted {entry.code} version.', 'success')
    else:
        abort(404)
    db.session.commit()
    return redirect(url_for('admin.shared_library_page', q=request.form.get('q', '')))


@admin_bp.route('/shared/reset', methods=['POST'])
@admin_required
def shared_library_reset():
    from utils import shared_library
    result = shared_library.reset_all()
    flash(f"Shared library emptied ({result['versions_removed']} versions removed).", 'success')
    return redirect(url_for('admin.shared_library_page'))
