"""Routes for HTML/CSV file upload and parsing."""

from flask import Blueprint, request, jsonify, session, Response, current_app
from models import db
from utils.html_parser import parse_vtop_html
from utils.csv_parser import parse_course_csv
from utils.validation import ValidationError, validate_course, validate_slots, safe_filename
from utils.course_store import save_course_for_owner
from utils.security import rate_limit
from utils import shared_library

upload_bp = Blueprint('upload', __name__)


@upload_bp.route('/csv-template', methods=['GET'])
def download_csv_template():
    """
    Download a CSV template file for course data import.
    Format: Course details header + data, then slot details header + data rows.
    """
    csv_content = """course_code,course_name,l,t,p,j,c,course_type,category
CSA3006,DATA MINING,2,1,1,0,4,LTP,PC
slot_code,faculty,venue,available_seats
A11+A12+A13,NILAMADHAB MISHRA,AB02-330,0
B14+B23+D21,JASMINE SELVAKUMARI JEYA,AR-002,14
C11+C12+TC1,ANOTHER FACULTY,AB-105,25
"""
    
    return Response(
        csv_content,
        mimetype='text/csv',
        headers={
            'Content-Disposition': 'attachment; filename=course_template.csv'
        }
    )
HTML_EXTENSIONS = ('.html', '.htm', '.mhtml')


def _read_and_parse(file):
    """Read an uploaded file and return validated (course, slots, rejected_rows).

    Raises ValidationError with a user-safe message.
    """
    raw = file.read()
    try:
        content = raw.decode('utf-8')
    except UnicodeDecodeError:
        content = raw.decode('latin-1')
    try:
        if file.filename.lower().endswith('.csv'):
            parsed = parse_course_csv(content)
        else:
            parsed = parse_vtop_html(content)
    except ValueError as e:
        # The CSV parser explains format problems with ValueError
        raise ValidationError(str(e)[:200])
    if not parsed or not parsed.get('course'):
        raise ValidationError('Could not find course details in this file.')
    course = validate_course(parsed['course'])
    slots, rejected = validate_slots(parsed.get('slots'))
    return course, slots, rejected


def _too_many_files(files):
    limit = current_app.config.get('MAX_FILES_PER_UPLOAD', 30)
    if len(files) > limit:
        return jsonify({'error': f'You can upload at most {limit} files at once.'}), 400
    return None


@upload_bp.route('/parse', methods=['POST'])
@rate_limit('upload-parse', 60, 600)
def parse_html_file():
    """
    Parse uploaded HTML file and extract course/slot information.
    Returns parsed data without saving to database.
    """
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400

    if not file.filename.lower().endswith(HTML_EXTENSIONS):
        return jsonify({'error': 'File must be HTML or MHTML'}), 400

    try:
        course, slots, rejected = _read_and_parse(file)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        current_app.logger.exception('HTML parse failed: %s', e)
        return jsonify({'error': 'Could not read this file.'}), 400

    return jsonify({
        'success': True,
        'course': course,
        'slots': slots,
        'slot_count': len(slots),
        'rejected_rows': rejected
    })

@upload_bp.route('/parse-batch', methods=['POST'])
@rate_limit('upload-parse', 60, 600)
def parse_html_batch():
    """
    Parse uploaded HTML files in batch and extract course/slot information.
    """
    files = request.files.getlist('files[]')

    if not files:
        return jsonify({'error': 'No files provided'}), 400
    too_many = _too_many_files(files)
    if too_many:
        return too_many

    results = []
    for file in files:
        if file.filename == '':
            continue
        filename = safe_filename(file.filename)

        if not file.filename.lower().endswith(HTML_EXTENSIONS):
            results.append({'filename': filename, 'status': 'error', 'error': 'File must be HTML or MHTML'})
            continue

        try:
            course, slots, rejected = _read_and_parse(file)
            results.append({
                'filename': filename,
                'status': 'success',
                'course': course,
                'slot_count': len(slots),
                'rejected_rows': rejected
            })
        except ValidationError as e:
            results.append({'filename': filename, 'status': 'error', 'error': str(e)})
        except Exception as e:
            current_app.logger.exception('HTML parse failed: %s', e)
            results.append({'filename': filename, 'status': 'error', 'error': 'Could not read this file.'})

    return jsonify({'results': results})

@upload_bp.route('/import', methods=['POST'])
@rate_limit('upload-import', 60, 600)
def import_html_file():
    """
    Parse uploaded HTML/CSV files and save data to database.
    Accepts multiple files key 'files[]'.
    """
    files = request.files.getlist('files[]')

    if not files:
        if 'file' in request.files:
            files = [request.files['file']]
        else:
            return jsonify({'error': 'No files provided'}), 400
    too_many = _too_many_files(files)
    if too_many:
        return too_many

    # Determine owner
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')

    if not user_id and not guest_id:
        return jsonify({'error': 'No active session'}), 401

    results = []
    success_count = 0

    for file in files:
        if file.filename == '':
            continue
        filename = safe_filename(file.filename)

        if not file.filename.lower().endswith(HTML_EXTENSIONS + ('.csv',)):
            results.append({
                'filename': filename,
                'status': 'error',
                'message': 'Invalid file type. Supported: HTML, MHTML, CSV'
            })
            continue

        try:
            result = _process_single_file_import(file, filename, user_id, guest_id)
        except ValidationError as e:
            db.session.rollback()
            result = {'filename': filename, 'status': 'error', 'message': str(e)}
        except Exception as e:
            db.session.rollback()
            current_app.logger.exception('Import failed: %s', e)
            result = {'filename': filename, 'status': 'error', 'message': 'Could not import this file.'}
        results.append(result)
        if result['status'] == 'success':
            success_count += 1

    return jsonify({
        'success': True,
        'summary': f'Processed {len(files)} files. {success_count} succeeded.',
        'results': results,
        'success_count': success_count
    })

def _process_single_file_import(file, filename, user_id, guest_id):
    """Validate one file, save it to the owner's timetable data, then share it."""
    course, slots, rejected = _read_and_parse(file)
    if not slots:
        raise ValidationError('No valid faculty/slot rows were found in this file.')

    saved_course, slots_added = save_course_for_owner(course, slots, user_id, guest_id)
    db.session.commit()

    _share(course, slots, user_id, guest_id)

    result = {
        'filename': filename,
        'status': 'success',
        'course_code': saved_course.code,
        'slots_added': slots_added
    }
    if rejected:
        result['rejected_rows'] = rejected
    return result


def _share(course, slots, user_id, guest_id):
    """Copy an import into the shared library. Never makes the import itself fail."""
    # Guests are anonymous and easy to repeat, so their contributions are rate limited
    from utils.security import is_rate_limited
    if session.get('is_impersonating'):
        return  # An admin viewing as someone else must not contribute in their name
    if not user_id and is_rate_limited('shared-guest-upload', 20, 3600):
        return
    try:
        shared_library.contribute(course, slots, user_id, guest_id)
    except Exception as e:
        db.session.rollback()
        current_app.logger.warning('Shared library contribution skipped: %s', e)
