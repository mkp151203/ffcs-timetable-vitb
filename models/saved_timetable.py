from datetime import datetime
from .database import db

class SavedTimetable(db.Model):
    """Model to store saved timetable configurations."""
    
    __tablename__ = 'saved_timetables'
    
    id = db.Column(db.Integer, primary_key=True)
    
    # Ownership (same pattern as Course/Registration)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    guest_id = db.Column(db.String(100), nullable=True, index=True)
    
    name = db.Column(db.String(200), nullable=False)
    slot_ids_json = db.Column(db.Text, nullable=False)  # JSON string of slot IDs
    
    # Metadata for quick display without parsing JSON
    total_credits = db.Column(db.Integer, default=0)
    course_count = db.Column(db.Integer, default=0)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def to_dict(self):
        return {
            'id': str(self.id),  # String to prevent JS BigInt precision loss
            'name': self.name,
            'slot_ids': self.slot_ids_json, # Frontend will parse this
            'total_credits': self.total_credits,
            'course_count': self.course_count,
            'created_at': self.created_at.isoformat()
        }
