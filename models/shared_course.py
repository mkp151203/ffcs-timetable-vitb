from datetime import datetime
import json

from .database import db


class SharedCourse(db.Model):
    """One version of a course's faculty/slot list in the shared library.

    Identical uploads from different people share one row (same fingerprint). No uploader
    identity is stored here; contributions live in SharedCourseEvent as one-way hashes.
    """

    __tablename__ = 'shared_courses'

    id = db.Column(db.Integer, primary_key=True)
    fingerprint = db.Column(db.String(64), unique=True, nullable=False, index=True)

    code = db.Column(db.String(20), nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    l = db.Column(db.Integer, default=0)
    t = db.Column(db.Integer, default=0)
    p = db.Column(db.Integer, default=0)
    j = db.Column(db.Integer, default=0)
    c = db.Column(db.Integer, default=0)
    course_type = db.Column(db.String(100), default='')
    category = db.Column(db.String(50), default='')

    slots_json = db.Column(db.Text, nullable=False)  # [{slot_code, venue, faculty, available_seats}]
    faculty_count = db.Column(db.Integer, default=0)
    slot_count = db.Column(db.Integer, default=0)

    hidden = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_activity_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    events = db.relationship('SharedCourseEvent', backref='shared_course', lazy='dynamic',
                             cascade='all, delete-orphan')

    @property
    def slots(self):
        try:
            return json.loads(self.slots_json)
        except (TypeError, ValueError):
            return []

    def course_dict(self):
        return {
            'code': self.code, 'name': self.name,
            'l': self.l, 't': self.t, 'p': self.p, 'j': self.j, 'c': self.c,
            'course_type': self.course_type or '', 'category': self.category or '',
        }

    def __repr__(self):
        return f'<SharedCourse {self.code} {self.fingerprint[:8]}>'


class SharedCourseEvent(db.Model):
    """An upload or verification of a shared course version, used for ranking and decay."""

    __tablename__ = 'shared_course_events'
    __table_args__ = (
        db.UniqueConstraint('shared_course_id', 'kind', 'contributor_hash', name='uq_shared_event_once'),
    )

    id = db.Column(db.Integer, primary_key=True)
    shared_course_id = db.Column(db.Integer, db.ForeignKey('shared_courses.id', ondelete='CASCADE'),
                                 nullable=False, index=True)
    kind = db.Column(db.String(10), nullable=False)  # 'upload' or 'verify'
    contributor_hash = db.Column(db.String(64), nullable=False, index=True)
    is_guest = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
