"""One-time: copy logged-in students' recent courses (last 60 days) into the shared library.

Only ADDS rows to the shared library tables. It never changes or deletes anyone's courses.
Guest data is skipped (it expires after 7 days anyway).

Usage:
    python scripts/backfill_shared_library.py            # dry run: shows what would be added
    python scripts/backfill_shared_library.py --apply    # write, only allowed on a SQLite database
    python scripts/backfill_shared_library.py --apply --live   # write to a non-SQLite (production) database
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('FFCS_DISABLE_CLEANUP_THREAD', '1')

from app import app  # noqa: E402
from models import Course, Slot  # noqa: E402
from utils import shared_library  # noqa: E402
from utils.validation import ValidationError, validate_course, validate_slots  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='actually write to the shared library')
    parser.add_argument('--live', action='store_true', help='allow writing to a non-SQLite database')
    args = parser.parse_args()

    with app.app_context():
        uri = app.config['SQLALCHEMY_DATABASE_URI']
        db_type = uri.split(':', 1)[0]
        print(f'Database type: {db_type}')
        if args.apply and not uri.startswith('sqlite') and not args.live:
            print('Refusing to write to a non-SQLite database without --live.')
            return 1
        if args.apply and args.live:
            answer = input('This writes to the PRODUCTION shared library tables. Type "backfill" to continue: ')
            if answer.strip() != 'backfill':
                print('Cancelled.')
                return 1

        cutoff = datetime.utcnow() - timedelta(days=shared_library.CUTOFF_DAYS)
        courses = Course.query.filter(Course.user_id.isnot(None), Course.created_at >= cutoff).all()
        added = skipped = 0
        for course in courses:
            slots = [{'slot_code': s.slot_code, 'venue': s.venue,
                      'faculty': s.faculty.name if s.faculty else 'N/A',
                      'available_seats': s.available_seats}
                     for s in Slot.query.filter_by(course_id=course.id).all()]
            try:
                clean_course = validate_course({
                    'code': course.code, 'name': course.name, 'l': course.l, 't': course.t, 'p': course.p,
                    'j': course.j, 'c': course.c, 'course_type': course.course_type, 'category': course.category,
                })
            except ValidationError:
                skipped += 1
                continue
            clean_slots, _ = validate_slots(slots)
            if not clean_slots:
                skipped += 1
                continue
            if args.apply:
                shared_library.contribute(clean_course, clean_slots, course.user_id, None, at=course.created_at)
            added += 1

        verb = 'Added' if args.apply else 'Would add'
        print(f'{verb} {added} courses to the shared library; skipped {skipped} with invalid or no slot data.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
