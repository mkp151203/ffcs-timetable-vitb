from .database import db
from .course import Course
from .faculty import Faculty
from .slot import Slot
from .registration import Registration
from .saved_timetable import SavedTimetable

from .user import User
from .feedback import Feedback
from .rating import Rating
from .shared_course import SharedCourse, SharedCourseEvent

__all__ = ['db', 'Course', 'Faculty', 'Slot', 'Registration', 'User', 'Feedback', 'Rating', 'SavedTimetable',
           'SharedCourse', 'SharedCourseEvent']
