from flask import Blueprint, url_for, session, redirect, flash, current_app
from authlib.integrations.flask_client import OAuth
from models import db, User, Course, Registration, SavedTimetable, Rating
import uuid

auth_bp = Blueprint('auth', __name__)
oauth = OAuth()

def init_oauth(app):
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=app.config['GOOGLE_CLIENT_ID'],
        client_secret=app.config['GOOGLE_CLIENT_SECRET'],
        server_metadata_url=app.config['GOOGLE_DISCOVERY_URL'],
        client_kwargs={
            'scope': 'openid email profile'
        }
    )

def transfer_guest_data(guest_id, user_id):
    """Move a guest's data to a user account, only if the account has no data of its own."""
    if not guest_id:
        return False

    has_own_data = (
        Course.query.filter_by(user_id=user_id).first() is not None
        or Registration.query.filter_by(user_id=user_id).first() is not None
        or SavedTimetable.query.filter_by(user_id=user_id).first() is not None
    )
    if has_own_data:
        return False

    moved = 0
    for model in (Course, Registration, SavedTimetable):
        moved += model.query.filter_by(guest_id=guest_id, user_id=None).update(
            {'user_id': user_id, 'guest_id': None}, synchronize_session=False
        )

    # One rating per user: keep the guest's rating only if the account has none
    if Rating.query.filter_by(user_id=user_id).first() is None:
        moved += Rating.query.filter_by(guest_id=guest_id, user_id=None).update(
            {'user_id': user_id, 'guest_id': None}, synchronize_session=False
        )

    db.session.commit()
    return moved > 0

@auth_bp.route('/login')
def login():
    redirect_uri = url_for('auth.callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri, hd='vitbhopal.ac.in')

@auth_bp.route('/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    session.pop('guest_id', None) # Clear guest session too on explicit logout
    return redirect(url_for('main.index'))

@auth_bp.route('/callback')
def callback():
    try:
        token = oauth.google.authorize_access_token()
        user_info = token.get('userinfo')
        
        if not user_info:
            flash("Failed to fetch user info from Google.", "error")
            return redirect(url_for('main.index'))

        email = user_info.get('email', '')
        
        # Domain Restriction Check
        if not email.endswith('@vitbhopal.ac.in'):
            flash("Only vitbhopal.ac.in emails are allowed.", "error")
            return redirect(url_for('main.index'))

        # Check if user exists
        user = User.query.filter_by(google_id=user_info['sub']).first()
        
        if not user:
            # Create new user
            user = User(
                google_id=user_info['sub'],
                email=email,
                name=user_info.get('name', ''),
                profile_pic=user_info.get('picture', '')
            )
            db.session.add(user)
            db.session.commit()
        else:
            # Update profile info
            user.name = user_info.get('name', user.name)
            user.profile_pic = user_info.get('picture', user.profile_pic)
            db.session.commit()

        # Carry over guest data on first login (account has no data yet)
        try:
            if transfer_guest_data(session.get('guest_id'), user.id):
                flash("Your guest timetable has been saved to your account.", "success")
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f"Guest data transfer failed: {e}")

        # Set session
        session['user_id'] = user.id
        # Clear guest_id to stop showing guest data
        session.pop('guest_id', None) 
        
        flash(f"Logged in as {user.name}", "success")
        return redirect(url_for('main.index'))
    except Exception as e:
        current_app.logger.error(f"OAuth Error: {e}")
        flash("Authentication failed. Please try again.", "error")
        return redirect(url_for('main.index'))
