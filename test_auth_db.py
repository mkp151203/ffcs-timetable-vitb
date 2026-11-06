import sys
# Safety: this script writes a test user to the configured database (production in .env).
if '--live' not in sys.argv:
    raise SystemExit('This writes to the configured database. Re-run with --live only if you mean it.')

from app import app, db
from models.user import User
import uuid

def test_user_persistence():
    print("--- Testing User Persistence on CockroachDB ---")
    
    # Use a unique fake ID to avoid conflicts
    fake_google_id = f"test_gid_{uuid.uuid4().hex[:8]}"
    fake_email = f"test_user_{uuid.uuid4().hex[:8]}@vitbhopal.ac.in"
    
    with app.app_context():
        # 1. Create User
        print(f"Creating User: {fake_email}")
        new_user = User(
            google_id=fake_google_id,
            email=fake_email,
            name="Test User",
            profile_pic="http://example.com/pic.jpg"
        )
        db.session.add(new_user)
        db.session.commit()
        
        user_id = new_user.id
        print(f"User saved with ID: {user_id}")
        
        # 2. Retrieve User (Simulate Login)
        db.session.expire_all() # Ensure fresh fetch
        user = User.query.filter_by(google_id=fake_google_id).first()
        
        if user:
            print(f"SUCCESS: Retrieved user '{user.name}' from DB.")
            print(f"  Google ID: {user.google_id}")
            print(f"  Email: {user.email}")
        else:
            print("FAILURE: User not found in DB after commit.")
            return

        # 3. Clean up
        print("Cleaning up test user...")
        db.session.delete(user)
        db.session.commit()
        
        # Verify deletion
        db.session.expire_all()
        check = User.query.filter_by(google_id=fake_google_id).first()
        if not check:
            print("SUCCESS: Test user deleted.")
        else:
            print("WARNING: Test user still exists.")

if __name__ == "__main__":
    test_user_persistence()
