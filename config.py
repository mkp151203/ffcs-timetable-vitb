import os
import dotenv
dotenv.load_dotenv()
basedir = os.path.abspath(os.path.dirname(__file__))

# Flask configuration
SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-key-please-change-in-production'
DEBUG = True

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

print(f"DEBUG: Using Database URI: {SQLALCHEMY_DATABASE_URI}")
SQLALCHEMY_TRACK_MODIFICATIONS = False
SQLALCHEMY_ENGINE_OPTIONS = {
    "pool_pre_ping": True,
    "pool_recycle": 300,
}

# Google OAuth Configuration
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', 'placeholder-client-id')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', 'placeholder-client-secret')
GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
