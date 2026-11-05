from flask import Blueprint, render_template, session, redirect, url_for, flash, abort, current_app, request
from models import db, User, Course, Registration, SavedTimetable, Rating
from functools import wraps

admin_bp = Blueprint('admin', __name__)

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
        
    guest_stats = list(guest_map.values())


    
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

