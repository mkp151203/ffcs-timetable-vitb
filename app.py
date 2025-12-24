from flask import Flask
from models import db
from routes import main_bp, courses_bp, registration_bp, upload_bp, auth_bp, sitemap_bp, generate_bp
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
app.register_blueprint(sitemap_bp)

# Create tables
with app.app_context():
    db.create_all()

from flask import request

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

# Background Cleanup Task
import threading
import time
from datetime import datetime, timedelta, timezone
from models import Course, User

import os

def _perform_cleanup_logic():
    """Core cleanup logic to delete old guest data."""
    try:
        with app.app_context():
            # Define cutoff time (7 days ago for weekly reset)
            # Use timezone-aware UTC
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            
            deleted_count = 0
            
            # 1. Delete old User accounts (cascades to Registrations)
            old_users = User.query.filter(User.created_at < cutoff).all()
            if old_users:
                print(f"[{datetime.now()}] Cleanup: Deleting {len(old_users)} old users...")
                for user in old_users:
                    db.session.delete(user)
                    deleted_count += 1
            
            # 2. Delete old Guest courses (Users courses are deleted via cascade above if user is deleted, 
            #    but we might want to clean up courses for users who haven't expired yet? 
            #    User asked to reset data after one week. If user is > 1 week, they go. 
            #    If user is < 1 week, their data stays. This fits 'reset after one week'.
            #    So we just need to handle Guest courses explicitly.)
            old_guest_courses = Course.query.filter(
                Course.guest_id.isnot(None), 
                Course.created_at < cutoff
            ).all()
            
            if old_guest_courses:
                print(f"[{datetime.now()}] Cleanup: Deleting {len(old_guest_courses)} old guest courses...")
                for course in old_guest_courses:
                    db.session.delete(course)
                    deleted_count += 1
            
            if deleted_count > 0:
                db.session.commit()
                print(f"[{datetime.now()}] Cleanup complete. Total items deleted: {deleted_count}")
                return deleted_count
            
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
    """Endpoint for Serverless Cron Jobs."""
    count = _perform_cleanup_logic()
    return {'status': 'success', 'deleted_count': count}

# Start cleanup thread ONLY if not on Vercel
# Vercel serverless functions cannot handle background threads (they timeout/crash)
if not os.environ.get('VERCEL'):
    cleanup_thread = threading.Thread(target=cleanup_orphaned_data, daemon=True)
    cleanup_thread.start()

if __name__ == '__main__':
    app.run(debug=True, port=5000)
