import sys
import os
import unittest
import uuid
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Safety: never run against the database in .env (production). Use a throwaway SQLite file.
import tempfile
os.environ['DATABASE_URL'] = 'sqlite:///' + os.path.join(tempfile.mkdtemp(prefix='ffcs-test-'), 'test.db')
os.environ.setdefault('SECRET_KEY', 'test-secret-key')
os.environ['FFCS_DISABLE_CLEANUP_THREAD'] = '1'

from app import app, db
from models import User, Course, Slot, Faculty, Registration

class TestMutualExclusion(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config['TESTING'] = True
        self.app.config['CSRF_DISABLED'] = True  # these tests check clash rules, not CSRF
        # Note: app likely uses real DB, so we must rely on cleanup or uniqueness
        self.client = self.app.test_client()
        
        with self.app.app_context():
            # No db.create_all() as we are likely running against a persistent dev DB
            
            # Create unique user
            unique_id = str(uuid.uuid4())
            self.user = User(
                google_id=f"test_user_{unique_id}", 
                email=f"test_{unique_id}@example.com", 
                name="Test User"
            )
            db.session.add(self.user)
            db.session.commit()
            
            # Store ID for use after session commit/expire
            self.user_id = self.user.id
            self.faculty_id = None # Placeholder
            
            # Create faculty
            self.faculty = Faculty(name=f"Test Faculty {unique_id}")
            db.session.add(self.faculty)
            db.session.commit()
            
            # Create unique courses
            c1_code = f"CSE1001_{unique_id[:8]}"
            c2_code = f"CSE1002_{unique_id[:8]}"
            print(f"DEBUG: User ID: {self.user.id}")
            
            try:
                # C1
                c1_code = f"T1_{unique_id[:8]}"
                print(f"DEBUG: Inserting {c1_code}")
                self.course1 = Course(code=c1_code, name="Course 1", c=4, user_id=self.user_id, course_type="Theory", category="Core")
                db.session.add(self.course1)
                db.session.commit()
                print(f"DEBUG: {c1_code} Success")
                
                # C2
                c2_code = f"T2_{unique_id[:8]}"
                print(f"DEBUG: Inserting {c2_code}")
                self.course2 = Course(code=c2_code, name="Course 2", c=4, user_id=self.user_id, course_type="Theory", category="Core")
                db.session.add(self.course2)
                db.session.commit()
                print(f"DEBUG: {c2_code} Success")
                
                # C3
                c3_code = f"T3_{unique_id[:8]}"
                print(f"DEBUG: Inserting {c3_code}")
                self.course3 = Course(code=c3_code, name="Course 3", c=4, user_id=self.user_id, course_type="Theory", category="Core")
                db.session.add(self.course3)
                db.session.commit()
                print(f"DEBUG: {c3_code} Success")

            except Exception as e:
                print(f"DEBUG: Course Insertion Failed: {e}")
                db.session.rollback()
                raise e
            
            # Create slots
            # C1 group slot
            self.slot_c1 = Slot(
                slot_code="C11+C12+C13",
                course_id=self.course1.id,
                faculty_id=self.faculty.id,
                venue="AB1",
                total_seats=60
            )
            
            # A2 group slot
            self.slot_a2 = Slot(
                slot_code="A21+A22+A23",
                course_id=self.course2.id,
                faculty_id=self.faculty.id,
                venue="AB2",
                total_seats=60
            )
            
            # Partial C1 slot
            self.slot_c1_partial = Slot(
                slot_code="C11",
                course_id=self.course3.id,
                faculty_id=self.faculty.id,
                venue="AB3",
                total_seats=60
            )
            
            db.session.add_all([self.slot_c1, self.slot_a2, self.slot_c1_partial])
            db.session.commit()
            
            self.slot_c1_id = self.slot_c1.id
            self.slot_a2_id = self.slot_a2.id
            self.slot_c1_partial_id = self.slot_c1_partial.id # Use correct var
            
            print(f"DEBUG: Created Slots: {self.slot_c1_id}, {self.slot_a2_id}, {self.slot_c1_partial_id}")
            
            # Verify existence
            print(f"DEBUG: Verification query: {Slot.query.get(self.slot_c1_id)}")

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            # Explicit cleanup instead of drop_all
            try:
                # Delete slots
                Slot.query.filter(Slot.course_id.in_([self.course1.id, self.course2.id, self.course3.id])).delete(synchronize_session=False)
                # Delete courses
                Course.query.filter(Course.id.in_([self.course1.id, self.course2.id, self.course3.id])).delete(synchronize_session=False)
                # Delete faculty
                Faculty.query.filter_by(id=self.faculty.id).delete()
                # Delete user
                User.query.filter_by(id=self.user_id).delete()
                db.session.commit()
            except Exception as e:
                print(f"Cleanup failed: {e}")
                db.session.rollback()

    def test_c1_then_a2_clash(self):
        """Test registering C1 then trying A2 fails."""
        with self.app.app_context():
            with self.client.session_transaction() as sess:
                sess['user_id'] = self.user_id
                
            # Register C1
            rv = self.client.post('/api/registration/', json={'slot_id': self.slot_c1_id})
            self.assertEqual(rv.status_code, 201)
            
            # Try Register A2
            rv = self.client.post('/api/registration/', json={'slot_id': self.slot_a2_id})
            self.assertEqual(rv.status_code, 400)
            self.assertIn("Mutual exclusion", rv.get_json()['clashing_slots'][0]['reason'])

    def test_a2_then_c1_clash(self):
        """Test registering A2 then trying C1 fails."""
        with self.app.app_context():
            with self.client.session_transaction() as sess:
                sess['user_id'] = self.user_id
                
            # Register A2
            rv = self.client.post('/api/registration/', json={'slot_id': self.slot_a2_id})
            self.assertEqual(rv.status_code, 201)
            
            # Try Register C1
            rv = self.client.post('/api/registration/', json={'slot_id': self.slot_c1_id})
            self.assertEqual(rv.status_code, 400)
            self.assertIn("Mutual exclusion", rv.get_json()['clashing_slots'][0]['reason'])

    def test_partial_c1_clash_with_a2(self):
        """Test registering partial C1 (C11) then trying A2 fails."""
        with self.app.app_context():
            with self.client.session_transaction() as sess:
                sess['user_id'] = self.user_id
                
            # Register C11
            rv = self.client.post('/api/registration/', json={'slot_id': self.slot_c1_partial_id})
            self.assertEqual(rv.status_code, 201)
            
            # Try Register A2
            rv = self.client.post('/api/registration/', json={'slot_id': self.slot_a2_id})
            self.assertEqual(rv.status_code, 400)
            self.assertIn("Mutual exclusion", rv.get_json()['clashing_slots'][0]['reason'])

if __name__ == '__main__':
    unittest.main()
