import os
import dotenv
dotenv.load_dotenv()
basedir = os.path.abspath(os.path.dirname(__file__))

IS_PRODUCTION = bool(os.environ.get('VERCEL'))

# Flask configuration
# Never fall back to a known key in production: anyone could forge login cookies with it
SECRET_KEY = os.environ.get('SECRET_KEY')
if not SECRET_KEY:
    if IS_PRODUCTION:
        raise RuntimeError('SECRET_KEY environment variable must be set in production')
    import secrets
    SECRET_KEY = secrets.token_hex(32)  # Local development only: sessions reset on restart
DEBUG = os.environ.get('FLASK_DEBUG') == '1'

# Session cookie hardening
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = IS_PRODUCTION

# Largest request body accepted (uploads of VTOP HTML / CSV files)
MAX_CONTENT_LENGTH = 8 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 30

# Vercel sends "Authorization: Bearer <CRON_SECRET>" to cron endpoints when this is set
CRON_SECRET = os.environ.get('CRON_SECRET')

# Public production URL, used for canonical links, sitemap, robots.txt and llms.txt
SITE_URL = os.environ.get('SITE_URL', 'https://ffcs-timetable.vercel.app').rstrip('/')

# Session configuration — guest session cookie lifetime (activated by before_request in app.py)
from datetime import timedelta
PERMANENT_SESSION_LIFETIME = timedelta(days=7)

# Database configuration
# Database configuration
# 1. Prefer External DB (Postgres) if available (e.g. Render/Vercel/Neon)
if os.environ.get('DATABASE_URL'):
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    # Fix for some postgres providers (Render/Heroku) using 'postgres://' instead of 'postgresql://'
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
        
    # Fix for CockroachDB (needs cockroachdb:// dialect to parse version string correctly)
    if "cockroach" in SQLALCHEMY_DATABASE_URI and SQLALCHEMY_DATABASE_URI.startswith("postgresql://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgresql://", "cockroachdb://", 1)
        
# 2. Vercel Filesystem Fallback (Ephemeral /tmp) - Only for testing, not recommended for persistent data
elif os.environ.get('VERCEL'):
    SQLALCHEMY_DATABASE_URI = 'sqlite:////tmp/timetable.db'
# 3. Local Development
else:
    SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(basedir, 'timetable.db')

# Log only the database type, never the address or credentials
print(f"Using database: {SQLALCHEMY_DATABASE_URI.split(':', 1)[0]}")
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', 'placeholder-client-id')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', 'placeholder-client-secret')
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
ADMIN_EMAILS = [
    'mehul.23bai10105@vitbhopal.ac.in',
     # Added for testing if needed, or remove
]

