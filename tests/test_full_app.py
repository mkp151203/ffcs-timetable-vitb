"""End-to-end tests for the whole app, using the sample VTOP pages in courses/.

SAFETY: these tests always run on a throwaway SQLite file and refuse to start on any other
database, so they can never touch the live database.

Run:  venv/bin/python -m unittest tests.test_full_app -v
"""

import glob
import io
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix='ffcs-test-'), 'test.db')
os.environ['DATABASE_URL'] = 'sqlite:///' + _TMP_DB   # set before the app (and dotenv) load
os.environ['SECRET_KEY'] = 'test-secret-key'
os.environ['FFCS_DISABLE_CLEANUP_THREAD'] = '1'
os.environ.pop('VERCEL', None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app import app, db  # noqa: E402

if not app.config['SQLALCHEMY_DATABASE_URI'].startswith('sqlite:///') or _TMP_DB not in app.config['SQLALCHEMY_DATABASE_URI']:
    raise SystemExit('Refusing to run tests: the app is not using the throwaway SQLite database.')

from models import (User, Course, Slot, Registration, SavedTimetable, Rating, Feedback,  # noqa: E402
                    SharedCourse, SharedCourseEvent)
from utils import shared_library  # noqa: E402
from utils.security import _hits  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
SAMPLE_FILES = sorted(glob.glob(os.path.join(ROOT, 'courses', '*.html')))
SAMPLE_CODES = {'MAT2003', 'CSA3006', 'CSA3007', 'CSA4028', 'CSA4029', 'CSE3010', 'HUM1002', 'PLA1006'}
XSS = '<img src=x onerror=alert(1)>'
ADMIN_EMAIL = app.config['ADMIN_EMAILS'][0]


def read_sample(name_part):
    path = next(p for p in SAMPLE_FILES if name_part in os.path.basename(p))
    with open(path, 'rb') as f:
        return f.read()


class Browser:
    """A test client that behaves like a browser tab: has a session and sends the CSRF token."""

    def __init__(self, user=None):
        self.client = app.test_client()
        if user is not None:
            with self.client.session_transaction() as s:
                s['user_id'] = user.id
        page = self.client.get('/')
        assert page.status_code == 200, page.status_code
        match = re.search(r'<meta name="csrf-token" content="([^"]+)"', page.get_data(as_text=True))
        assert match, 'CSRF meta tag missing'
        self.token = match.group(1)

    def _h(self, csrf=True):
        return {'X-CSRF-Token': self.token} if csrf else {}

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, json_body=None, csrf=True, **kw):
        return self.client.post(url, json=json_body, headers=self._h(csrf), **kw)

    def put(self, url, json_body=None, csrf=True):
        return self.client.put(url, json=json_body, headers=self._h(csrf))

    def delete(self, url, json_body=None, csrf=True):
        return self.client.delete(url, json=json_body, headers=self._h(csrf))

    def upload(self, files, url='/api/upload/import'):
        data = {'files[]': [(io.BytesIO(content), name) for name, content in files]}
        return self.client.post(url, data=data, headers=self._h(), content_type='multipart/form-data')

    def session(self):
        with self.client.session_transaction() as s:
            return dict(s)


def make_user(email, name='Student'):
    user = User(google_id='gid-' + email, email=email, name=name)
    db.session.add(user)
    db.session.commit()
    return user


class AppTestCase(unittest.TestCase):

    def setUp(self):
        app.config['RATE_LIMITS_DISABLED'] = True
        _hits.clear()
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    def import_all_samples(self, browser):
        files = []
        for p in SAMPLE_FILES:
            with open(p, 'rb') as f:
                files.append((os.path.basename(p), f.read()))
        resp = browser.upload(files)
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        return resp.get_json()


# ==================== Core app with the sample courses ====================

class TestCoreFeatures(AppTestCase):

    def test_import_all_sample_courses(self):
        b = Browser()
        data = self.import_all_samples(b)
        self.assertEqual(data['success_count'], len(SAMPLE_FILES), data)
        courses = b.get('/api/courses/all').get_json()['courses']
        self.assertEqual({c['code'] for c in courses}, SAMPLE_CODES)
        total_slots = sum(len(b.get(f"/api/courses/{c['id']}/slots").get_json()['slots']) for c in courses)
        self.assertEqual(total_slots, 146)
        # Importing the same files again adds no duplicate slots
        again = self.import_all_samples(b)
        self.assertTrue(all(r['slots_added'] == 0 for r in again['results']))

    def test_parse_preview(self):
        b = Browser()
        resp = b.upload([('CSA3006.html', read_sample('CSA3006'))], url='/api/upload/parse-batch')
        result = resp.get_json()['results'][0]
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['course']['code'], 'CSA3006')
        self.assertEqual(result['slot_count'], 11)

    def test_csv_import(self):
        b = Browser()
        template = b.get('/api/upload/csv-template').get_data()
        resp = b.upload([('course.csv', template)]).get_json()
        self.assertEqual(resp['success_count'], 1, resp)
        self.assertEqual(resp['results'][0]['course_code'], 'CSA3006')

    def test_registration_flow_and_clashes(self):
        b = Browser()
        self.import_all_samples(b)
        courses = {c['code']: c for c in b.get('/api/courses/all').get_json()['courses']}
        dm_slots = b.get(f"/api/courses/{courses['CSA3006']['id']}/slots").get_json()['slots']
        dl_slots = b.get(f"/api/courses/{courses['CSA3007']['id']}/slots").get_json()['slots']

        first = dm_slots[0]  # A11+A12+A13
        r = b.post('/api/registration/', {'slot_id': first['id']})
        self.assertIn(r.status_code, (200, 201), r.get_data(as_text=True))
        reg_id = b.get('/api/registration/').get_json()['registrations'][0]['id']

        # Same course twice is refused
        self.assertEqual(b.post('/api/registration/', {'slot_id': dm_slots[1]['id']}).status_code, 400)

        # A clashing slot from another course is detected
        clashing = next(s for s in dl_slots if 'A21' not in s['slot_code'] and set(s['slot_code'].split('+')) & {'A11', 'A12', 'A13'}) \
            if any(set(s['slot_code'].split('+')) & {'A11', 'A12', 'A13'} for s in dl_slots) else None
        batch = b.post('/api/registration/check-clash-batch', {'slot_ids': [s['id'] for s in dl_slots]}).get_json()
        self.assertEqual(len(batch['results']), len(dl_slots))
        if clashing:
            self.assertTrue(batch['results'][str(clashing['id'])]['has_clash'])

        # Switch to another slot of the same course
        other = next(s for s in dm_slots if s['id'] != first['id'])
        self.assertEqual(b.put(f'/api/registration/{reg_id}', {'slot_id': other['id']}).status_code, 200)
        credits = b.get('/api/registration/credits').get_json()
        self.assertEqual(credits.get('total_credits', credits.get('credits')), 4)

        # Delete and bulk delete
        self.assertEqual(b.delete(f'/api/registration/{reg_id}').status_code, 200)
        b.post('/api/registration/', {'slot_id': first['id']})
        ids = [r['id'] for r in b.get('/api/registration/').get_json()['registrations']]
        self.assertEqual(b.post('/api/registration/bulk-delete', {'registration_ids': ids, 'ids': ids}).status_code, 200)

    def test_manual_course_and_edit(self):
        b = Browser()
        r = b.post('/api/courses/manual', {'course_code': 'abc1234', 'course_name': 'Test Course', 'slot_code': 'b11+b12',
                                            'venue': 'ab-101', 'faculty': 'JOHN DOE', 'credits': 3})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        course = b.get('/api/courses/all').get_json()['courses'][0]
        self.assertEqual(course['code'], 'ABC1234')

        ok = b.post(f"/api/courses/{course['id']}/sync", {'slots': [
            {'slot_code': 'A11+A12', 'venue': 'AB-1', 'faculty': 'X Y', 'available_seats': 10},
            {'slot_code': 'C11', 'venue': '', 'faculty': '', 'available_seats': 5}]})
        self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True))
        slots = b.get(f"/api/courses/{course['id']}/slots").get_json()['slots']
        self.assertEqual({s['slot_code'] for s in slots}, {'A11+A12', 'C11'})

        bad = b.post(f"/api/courses/{course['id']}/sync", {'slots': [{'slot_code': 'A11', 'faculty': XSS}]})
        self.assertEqual(bad.status_code, 400)
        self.assertIn('Row 1', bad.get_json()['error'])

        self.assertEqual(b.delete(f"/api/courses/{course['id']}").status_code, 200)
        self.assertEqual(b.get('/api/courses/all').get_json()['courses'], [])

    def test_course_options_and_bulk_delete(self):
        b = Browser()
        self.import_all_samples(b)
        opts = b.get('/api/courses/options').get_json()
        self.assertIn('CSA3006', opts['course_codes'])
        self.assertTrue(opts['faculty_names'])
        ids = [c['id'] for c in b.get('/api/courses/all').get_json()['courses']]
        self.assertEqual(b.delete('/api/courses/bulk', {'course_ids': ids}).status_code, 200)
        self.assertEqual(b.get('/api/courses/all').get_json()['courses'], [])

    def test_generate_save_and_apply(self):
        b = Browser()
        self.import_all_samples(b)
        self.assertEqual(b.get('/api/generate/page').status_code, 200)
        available = b.get('/api/generate/available').get_json()
        self.assertNotIn('debug', available)
        ids = [c['id'] for c in available['courses'] if c['code'] in ('CSA3006', 'CSA4028', 'HUM1002')]

        count = b.post('/api/generate/count', {'course_ids': ids, 'preferences': {}}).get_json()
        self.assertGreater(count['count'], 0)
        self.assertNotIn('debug', count)

        sugg = b.post('/api/generate/suggest', {'course_ids': ids, 'preferences': {}, 'limit': 5}).get_json()
        self.assertTrue(sugg['success'])
        self.assertGreater(len(sugg['suggestions']), 0)
        data = b.post('/api/generate/data', {'course_ids': ids}).get_json()
        self.assertEqual(len(data['courses']), 3)

        first = sugg['suggestions'][0]
        slot_ids = [s['slot_id'] for s in first['slots']]
        applied = b.post('/api/generate/apply', {'slot_ids': slot_ids})
        self.assertEqual(applied.status_code, 200, applied.get_data(as_text=True))
        self.assertEqual(len(b.get('/api/registration/').get_json()['registrations']), 3)

        save = b.post('/api/generate/save', {'name': 'Option #1 (11 cr)', 'slot_ids': slot_ids,
                                              'total_credits': 11, 'course_count': 3})
        self.assertEqual(save.status_code, 200, save.get_data(as_text=True))
        saved = b.get('/api/generate/saved').get_json()['saved']
        self.assertEqual(len(saved), 1)
        preview = b.post('/api/generate/preview-details', {'slot_ids': json.loads(saved[0]['slot_ids'])}).get_json()
        self.assertEqual(len(preview['suggestion']['slots']), 3)
        self.assertEqual(b.delete(f"/api/generate/saved/{saved[0]['id']}").status_code, 200)

        bad_name = b.post('/api/generate/save', {'name': XSS, 'slot_ids': slot_ids})
        self.assertEqual(bad_name.status_code, 400)

    def test_rating_and_feedback(self):
        guest = Browser()
        self.assertEqual(guest.post('/api/rating/submit', {'stars': 2}).status_code, 200)
        self.assertEqual(guest.get('/api/rating/my').get_json()['stars'], 2)
        self.assertEqual(guest.post('/api/rating/remove').status_code, 200)
        self.assertEqual(guest.get('/api/rating/my').get_json()['stars'], 0)
        self.assertEqual(guest.post('/api/feedback/submit', {'message': 'hi'}).status_code, 401)

        user = make_user('stud.23bai10001@vitbhopal.ac.in')
        b = Browser(user)
        self.assertEqual(b.post('/api/feedback/submit', {'message': 'Love the app'}).status_code, 200)
        self.assertEqual(b.post('/api/feedback/submit', {'message': 'again'}).status_code, 429)
        Feedback.query.delete(); db.session.commit()
        self.assertEqual(b.post('/api/feedback/submit', {'message': '<script>alert(1)</script>'}).status_code, 400)

    def test_guest_data_moves_to_account_on_first_login(self):
        from routes.auth import transfer_guest_data
        b = Browser()
        self.import_all_samples(b)
        guest_id = b.session()['guest_id']
        user = make_user('new.24bai20002@vitbhopal.ac.in')
        self.assertTrue(transfer_guest_data(guest_id, user.id))
        self.assertEqual(Course.query.filter_by(user_id=user.id).count(), 8)

    def test_logout_is_post_only(self):
        user = make_user('stud.23bai10003@vitbhopal.ac.in')
        b = Browser(user)
        self.assertEqual(b.get('/auth/logout').status_code, 405)
        self.assertEqual(b.client.post('/auth/logout').status_code, 403)          # no CSRF token
        resp = b.client.post('/auth/logout', data={'csrf_token': b.token})
        self.assertEqual(resp.status_code, 302)
        self.assertNotIn('user_id', b.session())

    def test_public_pages(self):
        c = app.test_client()
        for url in ['/', '/sitemap.xml', '/robots.txt', '/llms.txt', '/favicon.ico', '/site.webmanifest']:
            self.assertEqual(c.get(url).status_code, 200, url)


# ==================== Security ====================

class TestSecurity(AppTestCase):

    def test_security_headers(self):
        resp = app.test_client().get('/')
        h = resp.headers
        self.assertIn("frame-ancestors 'none'", h['Content-Security-Policy'])
        self.assertIn("object-src 'none'", h['Content-Security-Policy'])
        self.assertEqual(h['X-Frame-Options'], 'DENY')
        self.assertEqual(h['X-Content-Type-Options'], 'nosniff')
        cookie = resp.headers.get('Set-Cookie', '')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Lax', cookie)

    def test_csrf_required_for_every_write(self):
        b = Browser()
        for method, url in [('post', '/api/registration/'), ('post', '/api/upload/import'), ('post', '/api/courses/manual'),
                            ('post', '/api/rating/submit'), ('delete', '/api/courses/bulk'), ('post', '/api/shared/1/verify'),
                            ('post', '/api/generate/save'), ('post', '/admin/shared/reset')]:
            resp = getattr(b.client, method)(url, json={})
            self.assertEqual(resp.status_code, 403, (method, url, resp.status_code))
        wrong = b.client.post('/api/rating/submit', json={'stars': 5}, headers={'X-CSRF-Token': 'wrong'})
        self.assertEqual(wrong.status_code, 403)

    def test_xss_payloads_rejected_everywhere(self):
        b = Browser()
        html = read_sample('ANM').decode('utf-8').replace('Applied Numerical Method', XSS)
        resp = b.upload([('evil.html', html.encode())]).get_json()
        self.assertEqual(resp['results'][0]['status'], 'error')
        self.assertEqual(Course.query.count(), 0)

        html = read_sample('ANM').decode('utf-8').replace('MAYANK SHARMA', '"><script>alert(1)</script>')
        resp = b.upload([('anm.html', html.encode())]).get_json()
        self.assertEqual(resp['results'][0]['status'], 'success')
        self.assertFalse(any('<' in s.venue + (s.faculty.name if s.faculty else '') for s in Slot.query.all()))
        from utils.html_parser import parse_vtop_html
        expected = sum(1 for s in parse_vtop_html(read_sample('ANM').decode('utf-8'))['slots'] if s['faculty'] == 'MAYANK SHARMA')
        self.assertEqual(resp['results'][0].get('rejected_rows'), expected)

        for payload in [{'course_code': 'ABC1234', 'course_name': XSS, 'slot_code': 'A11'},
                        {'course_code': '<b>', 'course_name': 'X', 'slot_code': 'A11'},
                        {'course_code': 'ABC1234', 'course_name': 'X', 'slot_code': 'A11', 'faculty': "x');alert(1);//"},
                        {'course_code': 'ABC1234', 'course_name': 'X', 'slot_code': 'A11', 'venue': 'javascript:alert(1)'},
                        {'course_code': 'ABC1234', 'course_name': 'X', 'slot_code': 'A11"onmouseover="x'}]:
            self.assertEqual(b.post('/api/courses/manual', payload).status_code, 400, payload)

    def test_filenames_are_sanitised(self):
        b = Browser()
        resp = b.upload([(XSS + '.html', b'<html></html>')]).get_json()
        self.assertNotIn('<', resp['results'][0]['filename'])

    def test_upload_limits(self):
        b = Browser()
        big = b'x' * (9 * 1024 * 1024)
        self.assertEqual(b.upload([('big.html', big)]).status_code, 413)
        many = [(f'f{i}.html', b'<html></html>') for i in range(31)]
        self.assertEqual(b.upload(many).status_code, 400)

    def test_cannot_touch_other_peoples_slots(self):
        a, b = Browser(), Browser()
        self.import_all_samples(a)
        slot = Slot.query.first()
        self.assertEqual(b.post('/api/registration/', {'slot_id': slot.id}).status_code, 404)
        self.assertEqual(b.post('/api/registration/check-clash', {'slot_id': slot.id}).status_code, 404)
        preview = b.post('/api/generate/preview-details', {'slot_ids': [slot.id]}).get_json()
        self.assertEqual(preview['suggestion']['slots'], [])
        self.assertEqual(b.get(f'/api/courses/{slot.course_id}/slots').status_code, 404)

    def test_errors_do_not_leak_internals(self):
        b = Browser()
        resp = b.post('/api/courses/manual', {'course_code': 'ABC1234', 'course_name': 'X', 'slot_code': 'A11',
                                               'credits': 'lots'})
        self.assertEqual(resp.status_code, 400)
        self.assertNotIn('Traceback', resp.get_data(as_text=True))

    def test_admin_pages_need_admin(self):
        student = Browser(make_user('stud.23bai10004@vitbhopal.ac.in'))
        for url in ['/admin/users', '/admin/shared', '/admin/feedback']:
            self.assertEqual(student.get(url).status_code, 403, url)
        admin = Browser(make_user(ADMIN_EMAIL, 'Admin'))
        for url in ['/admin/users', '/admin/shared', '/admin/feedback']:
            self.assertEqual(admin.get(url).status_code, 200, url)

    def test_admin_page_escapes_user_names(self):
        make_user('evil.23bai10005@vitbhopal.ac.in', name="');alert(1);//" + XSS)
        admin = Browser(make_user(ADMIN_EMAIL, 'Admin'))
        page = admin.get('/admin/users').get_data(as_text=True)
        self.assertNotIn(XSS, page)
        self.assertNotIn("confirm('Are you sure you want to log in as", page)

    def test_cron_secret(self):
        app.config['CRON_SECRET'] = 'cron-test-secret'
        try:
            c = app.test_client()
            self.assertEqual(c.get('/api/cron/cleanup').status_code, 401)
            ok = c.get('/api/cron/cleanup', headers={'Authorization': 'Bearer cron-test-secret'})
            self.assertEqual(ok.status_code, 200)
        finally:
            app.config['CRON_SECRET'] = None

    def test_rate_limit(self):
        app.config['RATE_LIMITS_DISABLED'] = False
        b = Browser()
        codes = [b.get('/api/shared/search?q=CSA').status_code for _ in range(125)]
        self.assertIn(429, codes)


# ==================== Shared library ====================

class TestSharedLibrary(AppTestCase):

    def search(self, browser, q):
        resp = browser.get(f'/api/shared/search?q={q}')
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()['courses']

    def test_every_import_is_shared_without_identity(self):
        a = Browser()
        self.import_all_samples(a)
        self.assertEqual(SharedCourse.query.count(), 8)
        found = self.search(Browser(), 'CSA3006')
        self.assertEqual(found[0]['code'], 'CSA3006')
        version = found[0]['versions'][0]
        self.assertEqual(version['slot_count'], 11)
        text = json.dumps(found)
        self.assertNotIn(a.session()['guest_id'], text)
        # Search by name works too
        self.assertEqual(self.search(Browser(), 'deep learn')[0]['code'], 'CSA3007')

    def test_identical_uploads_merge_and_count_people_once(self):
        a, b = Browser(), Browser()
        content = read_sample('CSA3006')
        a.upload([('a.html', content)])
        a.upload([('a.html', content)])     # same person again
        b.upload([('b.html', content)])
        self.assertEqual(SharedCourse.query.count(), 1)
        v = self.search(Browser(), 'CSA3006')[0]['versions'][0]
        self.assertEqual(v['uploads'], 2)

    def test_more_complete_version_ranks_first(self):
        html = read_sample('CSA3006').decode('utf-8')
        partial = html.replace('NILAMADHAB MISHRA', 'X').split('RIZWAN UR RAHMAN')[0]  # fewer rows
        u1 = Browser(make_user('a.23bai10010@vitbhopal.ac.in'))
        u2 = Browser(make_user('b.23bai10011@vitbhopal.ac.in'))
        u1.upload([('partial.html', partial.encode())])
        u2.upload([('full.html', html.encode())])
        versions = self.search(Browser(), 'CSA3006')[0]['versions']
        self.assertEqual(len(versions), 2)
        self.assertGreater(versions[0]['faculty_count'], versions[1]['faculty_count'])

    def test_verified_version_ranks_top_and_guest_ranks_below_equal_data(self):
        full = read_sample('CSA3006').decode('utf-8')
        alt = full.replace('NILAMADHAB MISHRA', 'SOMEONE ELSE')
        less = full.split('RIZWAN UR RAHMAN')[0]
        g = Browser()
        g.upload([('guest.html', full.encode())])                      # guest only, most complete
        u1 = Browser(make_user('a.23bai10020@vitbhopal.ac.in'))
        u1.upload([('alt.html', alt.encode())])
        u2 = Browser(make_user('b.23bai10021@vitbhopal.ac.in'))
        u2.upload([('less.html', less.encode())])
        versions = self.search(Browser(), 'CSA3006')[0]['versions']
        # Equally complete: the logged-in version beats the guest one. The fewer rows lose to both.
        self.assertTrue(versions[0]['confirmed'])
        self.assertEqual(versions[0]['slot_count'], versions[1]['slot_count'])
        self.assertFalse(versions[1]['confirmed'])
        self.assertLess(versions[2]['slot_count'], versions[1]['slot_count'])

        # Two students verify the less complete version: it moves to the top
        less_id = next(v['id'] for v in versions if v['slot_count'] == min(x['slot_count'] for x in versions))
        for i in range(2):
            voter = Browser(make_user(f'v{i}.23bai1003{i}@vitbhopal.ac.in'))
            r = voter.post(f'/api/shared/{less_id}/verify')
            self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        top = self.search(Browser(), 'CSA3006')[0]['versions'][0]
        self.assertEqual(top['id'], less_id)
        self.assertEqual(top['verifications'], 2)

    def test_verification_rules(self):
        uploader = Browser(make_user('up.23bai10040@vitbhopal.ac.in'))
        uploader.upload([('a.html', read_sample('EI'))])
        entry_id = str(SharedCourse.query.first().id)
        self.assertEqual(uploader.post(f'/api/shared/{entry_id}/verify').status_code, 400)   # own upload
        self.assertEqual(Browser().post(f'/api/shared/{entry_id}/verify').status_code, 401)  # guest
        voter = Browser(make_user('v.23bai10041@vitbhopal.ac.in'))
        voter.post(f'/api/shared/{entry_id}/verify')
        voter.post(f'/api/shared/{entry_id}/verify')                                         # once only
        self.assertEqual(SharedCourseEvent.query.filter_by(kind='verify').count(), 1)
        undo = voter.delete(f'/api/shared/{entry_id}/verify').get_json()
        self.assertEqual(undo['verifications'], 0)

    def test_import_from_library(self):
        Browser().upload([('a.html', read_sample('CSA4029'))])
        entry_id = str(SharedCourse.query.first().id)
        other = Browser()
        r = other.post(f'/api/shared/{entry_id}/import').get_json()
        self.assertEqual(r['course_code'], 'CSA4029')
        self.assertEqual(r['slots_added'], 11)
        courses = other.get('/api/courses/all').get_json()['courses']
        self.assertEqual([c['code'] for c in courses], ['CSA4029'])
        self.assertEqual(SharedCourseEvent.query.filter_by(kind='upload').count(), 1)  # importing is not uploading

    def test_decay_newer_beats_older(self):
        old = Browser(make_user('o.23bai10050@vitbhopal.ac.in'))
        old.upload([('a.html', read_sample('CSA3006'))])
        new = Browser(make_user('n.23bai10051@vitbhopal.ac.in'))
        new.upload([('b.html', read_sample('CSA3006').decode('utf-8').replace('NILAMADHAB MISHRA', 'NEW NAME').encode())])
        older = SharedCourseEvent.query.order_by(SharedCourseEvent.id).first()
        older.created_at = datetime.utcnow() - timedelta(days=28)
        db.session.commit()
        versions = self.search(Browser(), 'CSA3006')[0]['versions']
        self.assertGreater(versions[0]['score'], versions[1]['score'])
        self.assertAlmostEqual(versions[1]['score'], 0.25, places=2)  # two half-lives

    def test_cleanup_removes_data_older_than_30_days(self):
        Browser().upload([('a.html', read_sample('CSA3006'))])
        entry = SharedCourse.query.first()
        entry.last_activity_at = datetime.utcnow() - timedelta(days=31)
        for e in entry.events:
            e.created_at = entry.last_activity_at
        db.session.commit()
        self.assertEqual(self.search(Browser(), 'CSA3006'), [])
        result = shared_library.cleanup()
        self.assertEqual(result['versions_removed'], 1)
        self.assertEqual(SharedCourse.query.count(), 0)

    def test_admin_hide_delete_reset(self):
        Browser().upload([('a.html', read_sample('CSA3006')), ('b.html', read_sample('CSA3007'))])
        admin = Browser(make_user(ADMIN_EMAIL, 'Admin'))
        entry = SharedCourse.query.filter_by(code='CSA3006').first()
        r = admin.client.post(f'/admin/shared/{entry.id}/hide', data={'csrf_token': admin.token})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.search(Browser(), 'CSA3006'), [])
        self.assertIn('CSA3006', admin.get('/admin/shared?q=CSA').get_data(as_text=True))
        admin.client.post(f'/admin/shared/{entry.id}/delete', data={'csrf_token': admin.token})
        self.assertEqual(SharedCourse.query.count(), 1)
        admin.client.post('/admin/shared/reset', data={'csrf_token': admin.token})
        self.assertEqual(SharedCourse.query.count(), 0)
        self.assertEqual(Course.query.count(), 2)   # students' own data untouched

    def test_impersonation_does_not_contribute(self):
        admin_user = make_user(ADMIN_EMAIL, 'Admin')
        student = make_user('s.23bai10060@vitbhopal.ac.in')
        admin = Browser(admin_user)
        admin.client.post(f'/admin/impersonate/{student.id}', data={'csrf_token': admin.token})
        admin.upload([('a.html', read_sample('CSA3006'))])
        self.assertEqual(Course.query.filter_by(user_id=student.id).count(), 1)
        self.assertEqual(SharedCourse.query.count(), 0)

    def course_and_slots(self, name_part):
        from utils.html_parser import parse_vtop_html
        from utils.validation import validate_course, validate_slots
        parsed = parse_vtop_html(read_sample(name_part).decode('utf-8'))
        return validate_course(parsed['course']), validate_slots(parsed['slots'])[0]

    def test_same_faculty_data_merges_even_if_course_details_differ(self):
        course, slots = self.course_and_slots('CSA3006')
        typed_differently = dict(course, name='data mining & data warehousing', l=2, t=1, p=1,
                                 course_type='Lecture and Practical Hours On', category='')
        reshuffled = [dict(s, faculty=s['faculty'].lower() + '  ', available_seats=0) for s in reversed(slots)]
        a = shared_library.contribute(dict(course, category=''), slots, None, 'guest-a')
        b = shared_library.contribute(typed_differently, reshuffled, None, 'guest-b')
        c = shared_library.contribute(course, slots, None, 'guest-c')
        self.assertEqual(a.id, b.id)
        self.assertEqual(a.id, c.id)
        self.assertEqual(SharedCourse.query.count(), 1)
        self.assertEqual(a.name, course['name'])          # first upload's details are kept...
        self.assertEqual(a.category, course['category'])  # ...with blanks filled in by later uploads
        self.assertEqual(self.search(Browser(), 'CSA3006')[0]['versions'][0]['uploads'], 3)

    def test_spelled_out_course_type_is_accepted(self):
        from utils.validation import ValidationError, validate_course_type
        long_type = 'Lecture and Tutorial ,practical hours only'
        self.assertEqual(validate_course_type(long_type), long_type)
        for bad in [XSS, 'LTP"><b>', "LTP'; --"]:
            with self.assertRaises(ValidationError):
                validate_course_type(bad)

    def test_ranking_balances_completeness_and_logged_in(self):
        course, slots = self.course_and_slots('CSA3006')
        user = make_user('a.23bai10090@vitbhopal.ac.in')
        # A logged-in student shares only one row; two guests share the full list
        shared_library.contribute(course, [slots[0]], user.id, None)
        shared_library.contribute(course, slots, None, 'guest-1')
        shared_library.contribute(course, slots, None, 'guest-2')
        versions = self.search(Browser(), 'CSA3006')[0]['versions']
        self.assertEqual([v['slot_count'] for v in versions], [11, 1])

        # A logged-in version missing just one row (same faculty) beats the full guest-only one
        names = [s['faculty'] for s in slots]
        drop = next(i for i, n in enumerate(names) if names.count(n) > 1)
        shared_library.contribute(course, slots[:drop] + slots[drop + 1:], user.id, None)
        versions = self.search(Browser(), 'CSA3006')[0]['versions']
        self.assertEqual([v['slot_count'] for v in versions], [10, 11, 1])

    def test_fuller_version_beats_newer_smaller_one(self):
        course, slots = self.course_and_slots('CSA3006')
        smaller = [s for s in slots if s['faculty'] != 'NILAMADHAB MISHRA']
        u1 = make_user('a.23bai10080@vitbhopal.ac.in')
        u2 = make_user('b.23bai10081@vitbhopal.ac.in')
        u3 = make_user('c.23bai10082@vitbhopal.ac.in')
        shared_library.contribute(course, slots, u1.id, None, at=datetime.utcnow() - timedelta(days=10))
        shared_library.contribute(course, smaller, u2.id, None, at=datetime.utcnow())
        shared_library.contribute(dict(course, name='DMDW'), [slots[0]], u3.id, None)
        versions = self.search(Browser(), 'CSA3006')[0]['versions']
        self.assertEqual([v['faculty_count'] for v in versions], [7, 6, 1])
        # Searching by one version's name still shows every version of that course
        self.assertEqual(len(self.search(Browser(), 'DMDW')[0]['versions']), 3)

    def test_remerge_joins_versions_saved_under_old_fingerprints(self):
        course, slots = self.course_and_slots('CSA3006')
        person = shared_library.contributor_hash(None, 'guest-x')
        old_rows = []
        for i, (name, category) in enumerate([('DATA MINING', 'PC'), ('Data Mining', ''), ('DM', 'UCSDC')]):
            entry = SharedCourse(fingerprint=f'old-{i}', code='CSA3006', name=name, l=3, t=0, p=0, j=0, c=3,
                                 course_type='LTP', category=category, slots_json=json.dumps(slots),
                                 faculty_count=7, slot_count=11, created_at=datetime.utcnow() - timedelta(days=3 - i),
                                 last_activity_at=datetime.utcnow() - timedelta(days=3 - i))
            db.session.add(entry)
            db.session.flush()
            who = person if i < 2 else shared_library.contributor_hash(None, f'guest-{i}')
            db.session.add(SharedCourseEvent(shared_course_id=entry.id, kind='upload', contributor_hash=who,
                                             is_guest=True, created_at=entry.created_at))
            old_rows.append(entry)
        db.session.add(SharedCourse(fingerprint='old-other', code='CSA3007', name='DL', slots_json='[]'))
        db.session.commit()

        self.assertEqual(shared_library.remerge(apply=False)['merged'], 2)
        self.assertEqual(SharedCourse.query.count(), 4)          # dry run changes nothing
        result = shared_library.remerge()
        self.assertEqual((result['versions_before'], result['versions_after']), (4, 2))
        keeper = SharedCourse.query.filter_by(code='CSA3006').one()
        self.assertEqual(keeper.id, old_rows[0].id)
        self.assertEqual(keeper.name, 'DATA MINING')
        self.assertEqual(keeper.fingerprint, shared_library.fingerprint(course, slots))
        self.assertEqual(SharedCourseEvent.query.count(), 2)     # the same person counted once
        # New uploads of the same data now land on the merged version
        shared_library.contribute(course, slots, None, 'guest-new')
        self.assertEqual(SharedCourse.query.filter_by(code='CSA3006').count(), 1)

    def test_browse_lists_every_course_a_to_z_in_pages(self):
        self.assertEqual(Browser().get('/api/shared/browse').get_json()['total'], 0)
        self.import_all_samples(Browser())
        full = Browser().get('/api/shared/browse').get_json()
        codes = [c['code'] for c in full['courses']]
        self.assertEqual(full['total'], 8)
        self.assertEqual(codes, sorted(codes))
        self.assertNotIn('slots', full['courses'][0])          # list stays light; details come from search

        seen = []
        for page in (1, 2, 3):
            data = shared_library.browse(page, per_page=3)
            self.assertEqual(data['pages'], 3)
            seen += [c['code'] for c in data['courses']]
        self.assertEqual(seen, codes)
        self.assertEqual(shared_library.browse(99, per_page=3)['page'], 3)    # past the end: last page

        # Several versions of one course show once; hidden and expired versions are left out
        Browser().upload([('x.html', read_sample('CSA3006').decode('utf-8').replace('NILAMADHAB MISHRA', 'X').encode())])
        row = next(c for c in Browser().get('/api/shared/browse').get_json()['courses'] if c['code'] == 'CSA3006')
        self.assertEqual(row['version_count'], 2)
        SharedCourse.query.filter_by(code='CSA3007').update({'hidden': True})
        old = SharedCourse.query.filter_by(code='CSA4029').first()
        old.last_activity_at = datetime.utcnow() - timedelta(days=31)
        db.session.commit()
        codes = [c['code'] for c in Browser().get('/api/shared/browse').get_json()['courses']]
        self.assertNotIn('CSA3007', codes)
        self.assertNotIn('CSA4029', codes)

        for page in ['0', '-1', 'abc', '9999999999', '<script>']:
            resp = Browser().get('/api/shared/browse', query_string={'page': page})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.get_json()['page'], 1)

    def test_search_input_is_sanitised(self):
        Browser().upload([('a.html', read_sample('CSA3006'))])
        for q in ['%', '_', "'; DROP TABLE shared_courses;--", XSS]:
            resp = Browser().get('/api/shared/search', query_string={'q': q})
            self.assertEqual(resp.status_code, 200)
        self.assertEqual(SharedCourse.query.count(), 1)

    def test_backfill_script_dry_run_and_apply(self):
        user = make_user('bf.23bai10070@vitbhopal.ac.in')
        b = Browser(user)
        b.upload([('a.html', read_sample('CSA3006'))])
        shared_library.reset_all()
        import subprocess
        env = dict(os.environ, DATABASE_URL='sqlite:///' + _TMP_DB)
        script = os.path.join(ROOT, 'scripts', 'backfill_shared_library.py')
        dry = subprocess.run([sys.executable, script], env=env, capture_output=True, text=True, cwd=ROOT)
        self.assertIn('Would add 1', dry.stdout, dry.stdout + dry.stderr)
        self.assertEqual(SharedCourse.query.count(), 0)
        run = subprocess.run([sys.executable, script, '--apply'], env=env, capture_output=True, text=True, cwd=ROOT)
        self.assertIn('Added 1', run.stdout, run.stdout + run.stderr)
        db.session.expire_all()
        self.assertEqual(SharedCourse.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
