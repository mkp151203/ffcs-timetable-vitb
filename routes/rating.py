from flask import Blueprint, request, jsonify, session
from datetime import datetime
from models import db, Rating

rating_bp = Blueprint('rating', __name__)


@rating_bp.route('/api/rating/submit', methods=['POST'])
def submit_rating():
    """Submit a star rating (1-5). One rating per user/guest, updated on re-submit."""
    data = request.get_json()
    stars = data.get('stars')

    if not stars or not isinstance(stars, int) or stars < 1 or stars > 5:
        return jsonify({'error': 'Rating must be between 1 and 5'}), 400

    user_id = session.get('user_id')
    guest_id = session.get('guest_id')

    if not user_id and not guest_id:
        return jsonify({'error': 'Session not found'}), 401

    try:
        # Check for existing rating — update if exists (one rating per user/guest)
        existing = None
        if user_id:
            existing = Rating.query.filter_by(user_id=user_id).first()
        elif guest_id:
            existing = Rating.query.filter_by(guest_id=guest_id).first()

        if existing:
            existing.stars = stars
            existing.created_at = datetime.utcnow()
            db.session.commit()
            return jsonify({'success': True, 'message': 'Rating updated!', 'updated': True})
        else:
            rating = Rating(
                user_id=user_id,
                guest_id=guest_id if not user_id else None,
                stars=stars
            )
            db.session.add(rating)
            db.session.commit()
            return jsonify({'success': True, 'message': 'Thank you for rating!', 'updated': False})

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@rating_bp.route('/api/rating/my', methods=['GET'])
def my_rating():
    """Get the current user/guest's existing rating, if any."""
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')

    if not user_id and not guest_id:
        return jsonify({'stars': 0})

    existing = None
    if user_id:
        existing = Rating.query.filter_by(user_id=user_id).first()
    elif guest_id:
        existing = Rating.query.filter_by(guest_id=guest_id).first()

    if existing:
        return jsonify({'stars': existing.stars})
    return jsonify({'stars': 0})


@rating_bp.route('/api/rating/remove', methods=['POST'])
def remove_rating():
    """Remove the current user/guest's rating, if any."""
    user_id = session.get('user_id')
    guest_id = session.get('guest_id')

    if not user_id and not guest_id:
        return jsonify({'error': 'Session not found'}), 401

    try:
        if user_id:
            Rating.query.filter_by(user_id=user_id).delete()
        else:
            Rating.query.filter_by(guest_id=guest_id).delete()
        db.session.commit()
        return jsonify({'success': True, 'message': 'Rating removed'})
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500
