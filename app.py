from flask import Flask
from models import db
from routes import main_bp, courses_bp, registration_bp, upload_bp, auth_bp, sitemap_bp, generate_bp, admin_bp

from routes.feedback import feedback_bp
from routes.rating import rating_bp
from routes.auth import init_oauth
from flask_compress import Compress
from werkzeug.middleware.proxy_fix import ProxyFix
import os

app = Flask(__name__)
# Vercel sits behind a proxy, so we need to trust the headers (X-Forwarded-Proto, etc.)
# x_proto=1 (HTTPS), x_host=1, x_port=1, x_prefix=1
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

Compress(app)
app.config.from_object('config')

# Initialize database
db.init_app(app)

# Initialize OAuth
init_oauth(app)

# Register blueprints
app.register_blueprint(main_bp)
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(courses_bp, url_prefix='/api/courses')
app.register_blueprint(registration_bp, url_prefix='/api/registration')
app.register_blueprint(upload_bp, url_prefix='/api/upload')
app.register_blueprint(generate_bp, url_prefix='/api/generate')
app.register_blueprint(feedback_bp)
app.register_blueprint(rating_bp)
app.register_blueprint(sitemap_bp)
app.register_blueprint(admin_bp, url_prefix='/admin')


# Create tables
with app.app_context():
    db.create_all()

from flask import request, session as flask_session

@app.before_request
def make_session_permanent():
    """Ensure session cookie persists for 7 days (not deleted on browser close)."""
    flask_session.permanent = True

@app.after_request
def add_header(response):
    """Add headers to prevent caching for API/HTML, but allow for Static/Sitemaps."""
    # Allow caching for static files, sitemap, and robots.txt
    if 'static' in request.url or 'sitemap' in request.url or 'robots.txt' in request.url:
        return response
        
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# Background Cleanup Task - ONLY for Guest data (preserves logged-in user data)
import threading
import time
from datetime import datetime, timedelta, timezone
from models import Course

import os

def _perform_cleanup_logic(max_duration=50):
    """Core cleanup logic to delete old GUEST data only. Stop after max_duration seconds."""
    try:
        with app.app_context():
            # Define cutoff time (7 days ago)
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            start_time = time.time()
            
            total_deleted = 0
            from models import Slot, Registration, SavedTimetable

            while True:
                # Check for timeout
                if time.time() - start_time > max_duration:
                    print(f"[{datetime.now()}] Cleanup: Time limit reached ({max_duration}s). Stopping.")
                    break

                # 1. IDENTIFY Candidates (Bulk Fetch IDs)
                candidates = db.session.query(Course.id).filter(
                    Course.guest_id.isnot(None),
                    Course.user_id.is_(None),
                    Course.created_at < cutoff
                ).limit(50).all() # Increased batch size because bulk delete is fast
                
                if not candidates:
                    break
                
                course_ids = [c[0] for c in candidates]
                print(f"[{datetime.now()}] Cleanup: Found {len(course_ids)} candidates. Performing bulk delete...")

                try:
                    # 2. BULK DELETE DEPENDENCIES (Manual Cascade)
                    # This avoids loading objects into memory and avoids complexity of ORM cascades
                    
                    # A. Find relevant Slots
                    # We need slot IDs to delete registrations efficiently
                    slot_ids_query = db.session.query(Slot.id).filter(Slot.course_id.in_(course_ids))
                    # Check if there are any slots to process to avoid empty IN clause errors if optimizing further, 
                    # but .in_([]) usually works fine or we can skip.
                    
                    # B. Delete Registrations for those Slots
                    delete_regs = Registration.__table__.delete().where(
                        Registration.slot_id.in_(slot_ids_query)
                    )
                    res_regs = db.session.execute(delete_regs)
                    
                    # C. Delete Slots for those Courses
                    delete_slots = Slot.__table__.delete().where(
                        Slot.course_id.in_(course_ids)
                    )
                    res_slots = db.session.execute(delete_slots)
                    
                    # D. Delete Courses
                    delete_courses = Course.__table__.delete().where(
                        Course.id.in_(course_ids)
                    )
                    res_courses = db.session.execute(delete_courses)
                    
                    # 3. COMMIT TRANSACTION
                    db.session.commit()
                    
                    count = res_courses.rowcount if res_courses.rowcount is not None else len(course_ids)
                    total_deleted += count
                    print(f"[{datetime.now()}] Cleanup: Bulk deleted batch of {count}. Total: {total_deleted}")
                    
                    # Small breather
                    time.sleep(0.5)
                    
                except Exception as e:
                    db.session.rollback()
                    print(f"Cleanup batch error: {e}. Retrying with smaller batch next time...")
                    time.sleep(2)
                    # Break to restart loop or retry logic could be more complex, 
                    # but for now let's just stop this cycle to avoid infinite error loops
                    break
                
                # Check timeout again
                if time.time() - start_time > max_duration:
                    break

            # 2. CLEANUP SAVED TIMETABLES
            while True:
                if time.time() - start_time > max_duration:
                    break

                st_candidates = db.session.query(SavedTimetable.id).filter(
                    SavedTimetable.guest_id.isnot(None),
                    SavedTimetable.user_id.is_(None),
                    SavedTimetable.created_at < cutoff
                ).limit(50).all()
                
                if not st_candidates:
                    break
                
                st_ids = [st[0] for st in st_candidates]
                print(f"[{datetime.now()}] Cleanup: Found {len(st_ids)} SavedTimetable candidates.")

                try:
                    delete_sts = SavedTimetable.__table__.delete().where(
                        SavedTimetable.id.in_(st_ids)
                    )
                    res_sts = db.session.execute(delete_sts)
                    db.session.commit()
                    
                    count = res_sts.rowcount if res_sts.rowcount is not None else len(st_ids)
                    total_deleted += count
                    print(f"[{datetime.now()}] Cleanup: Bulk deleted {count} saved timetables.")
                    time.sleep(0.5)
                except Exception as e:
                    db.session.rollback()
                    print(f"Cleanup SavedTimetable batch error: {e}")
                    break

            if total_deleted > 0:
                print(f"[{datetime.now()}] Cleanup complete. Total guest items deleted: {total_deleted}")
                return total_deleted
            
            return 0
    except Exception as e:
        print(f"Cleanup error: {e}")
        return -1

def cleanup_orphaned_data():
    """Background thread loop for local development."""
    while True:
        _perform_cleanup_logic()
        # Run every hour (3600 seconds)
        time.sleep(3600)

@app.route('/api/cron/cleanup')
def trigger_cleanup():
    """Endpoint for Serverless Cron Jobs - cleans GUEST data only."""
    count = _perform_cleanup_logic()
    return {'status': 'success', 'deleted_count': count}

# Start cleanup thread ONLY if not on Vercel
if not os.environ.get('VERCEL'):
    cleanup_thread = threading.Thread(target=cleanup_orphaned_data, daemon=True)
    cleanup_thread.start()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
