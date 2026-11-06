"""Recompute shared library fingerprints and merge versions with the same faculty and slot data.

Run once after the fingerprint rules change. Only touches the shared library tables; students'
own courses are never changed.

Usage:
    python scripts/remerge_shared_library.py            # dry run: shows how many would merge
    python scripts/remerge_shared_library.py --apply    # write, only allowed on a SQLite database
    python scripts/remerge_shared_library.py --apply --live   # write to a non-SQLite (production) database
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
os.environ.setdefault('FFCS_DISABLE_CLEANUP_THREAD', '1')

from app import app  # noqa: E402
from utils import shared_library  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='actually merge')
    parser.add_argument('--live', action='store_true', help='allow writing to a non-SQLite database')
    args = parser.parse_args()

    with app.app_context():
        uri = app.config['SQLALCHEMY_DATABASE_URI']
        print(f'Database type: {uri.split(":", 1)[0]}')
        if args.apply and not uri.startswith('sqlite') and not args.live:
            print('Refusing to write to a non-SQLite database without --live.')
            return 1
        if args.apply and args.live:
            answer = input('This merges versions in the PRODUCTION shared library. Type "remerge" to continue: ')
            if answer.strip() != 'remerge':
                print('Cancelled.')
                return 1

        result = shared_library.remerge(apply=args.apply)
        verb = 'Merged' if args.apply else 'Would merge'
        print(f'{verb} {result["merged"]} duplicate versions: '
              f'{result["versions_before"]} versions become {result["versions_after"]}.')
        return 0


if __name__ == '__main__':
    sys.exit(main())
