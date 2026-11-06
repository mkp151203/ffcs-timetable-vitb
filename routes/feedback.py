from flask import Blueprint, request, jsonify, session, render_template, redirect, url_for, flash, abort
from utils.security import server_error
from utils.validation import ValidationError, validate_free_text
from datetime import datetime
from models import db, Feedback, User, Rating

feedback_bp = Blueprint('feedback', __name__)

ADMIN_EMAIL = "mehul.23bai10105@vitbhopal.ac.in"

@feedback_bp.route('/api/feedback/submit', methods=['POST'])
def submit_feedback():
    """Submit feedback from a logged-in user."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    message = data.get('message')

    try:
        message = validate_free_text(message, 'Message', 2000)
    except ValidationError as e:
        return jsonify({'error': str(e)}), 400

    # Rate limiting: Check last feedback from this user
    last_feedback = Feedback.query.filter_by(user_id=session['user_id']).order_by(Feedback.created_at.desc()).first()
    if last_feedback:
        time_diff = datetime.utcnow() - last_feedback.created_at
        if time_diff.total_seconds() < 180:  # 3 minute cooldown
            remaining = 180 - int(time_diff.total_seconds())
            minutes = (remaining + 59) // 60  # round up

            return jsonify({
                'error': f'Please wait {minutes} minute(s) before sending another message.'
            }), 429

    try:
        feedback = Feedback(
            user_id=session['user_id'],
            message=message.strip()
        )
        db.session.add(feedback)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Feedback submitted successfully!'})
    except Exception as e:
        db.session.rollback()
        return server_error(e)

@feedback_bp.route('/admin/feedback')
def view_feedback():
    """View all feedback (Admin only)."""
    if 'user_id' not in session:
        flash("Please login to access this page.", "error")
        return redirect(url_for('auth.login'))

    user = User.query.get(session['user_id'])
    if not user or user.email != ADMIN_EMAIL:
        abort(403) # Forbidden

    all_feedback = Feedback.query.order_by(Feedback.created_at.desc()).all()

    # Aggregate rating data
    all_ratings = Rating.query.order_by(Rating.created_at.desc()).all()
    total_ratings = len(all_ratings)
    if total_ratings > 0:
        avg_rating = round(sum(r.stars for r in all_ratings) / total_ratings, 1)
        distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        for r in all_ratings:
            distribution[r.stars] += 1
    else:
        avg_rating = 0
        distribution = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

    return render_template('admin_feedback.html',
                           feedbacks=all_feedback,
                           avg_rating=avg_rating,
                           total_ratings=total_ratings,
                           distribution=distribution,
                           all_ratings=all_ratings)

@feedback_bp.route('/admin/feedback/delete/<int:feedback_id>', methods=['POST'])
def delete_feedback(feedback_id):
    """Delete a feedback message (Admin only)."""
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401

    user = User.query.get(session['user_id'])
    if not user or user.email != ADMIN_EMAIL:
        return jsonify({'error': 'Forbidden'}), 403

    feedback = Feedback.query.get_or_404(feedback_id)
    try:
        db.session.delete(feedback)
        db.session.commit()
        return jsonify({'success': True, 'message': 'Feedback deleted successfully'})
    except Exception as e:
        db.session.rollback()
        return server_error(e)
