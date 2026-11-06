"""Request-level security: CSRF tokens, security headers, rate limits and safe error replies."""

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, current_app, jsonify, request, session

CSRF_SESSION_KEY = '_csrf_token'
CSRF_HEADER = 'X-CSRF-Token'
CSRF_FORM_FIELD = 'csrf_token'
UNSAFE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}

CONTENT_SECURITY_POLICY = '; '.join([
    "default-src 'self'",
    # 'unsafe-inline' is still needed for the onclick= handlers in the templates; injected markup is
    # prevented by escaping and input validation instead
    "script-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://www.googletagmanager.com",
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",
    "font-src 'self' data: https://cdnjs.cloudflare.com",
    "img-src 'self' data: https:",
    "connect-src 'self' https://www.google-analytics.com https://*.google-analytics.com "
    "https://*.analytics.google.com https://www.googletagmanager.com",
    "frame-src https://www.youtube.com https://www.youtube-nocookie.com",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self' https://accounts.google.com",
    "frame-ancestors 'none'",
])


# ==================== CSRF ====================

def get_csrf_token():
    """Return this session's CSRF token, creating it on first use."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_protect():
    """Reject state-changing requests that don't carry this session's CSRF token."""
    if request.method not in UNSAFE_METHODS:
        return None
    if current_app.config.get('CSRF_DISABLED'):
        return None

    expected = session.get(CSRF_SESSION_KEY)
    sent = request.headers.get(CSRF_HEADER) or request.form.get(CSRF_FORM_FIELD)
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Your session expired. Please reload the page and try again.'}), 403
        abort(403)
    return None


# ==================== Security headers ====================

def add_security_headers(response):
    response.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()')
    if request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response


# ==================== Rate limiting ====================

_hits = defaultdict(deque)
_hits_lock = threading.Lock()


def client_key():
    """Identify the caller: logged-in user, else guest session, else IP address."""
    if session.get('user_id'):
        return f"u:{session['user_id']}"
    if session.get('guest_id'):
        return f"g:{session['guest_id']}"
    return f"ip:{request.remote_addr}"


def is_rate_limited(bucket, limit, window_seconds, key=None):
    """Sliding-window limit kept in memory. Best effort: each server instance counts separately."""
    if current_app.config.get('RATE_LIMITS_DISABLED'):
        return False
    now = time.time()
    k = (bucket, key or client_key())
    with _hits_lock:
        q = _hits[k]
        while q and q[0] <= now - window_seconds:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        # Keep memory bounded
        if len(_hits) > 50000:
            _hits.clear()
    return False


def rate_limit(bucket, limit, window_seconds):
    """Decorator: reply 429 once a caller (and separately their IP) goes over the limit."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            limited = is_rate_limited(bucket, limit, window_seconds)
            limited = is_rate_limited(bucket + ':ip', limit * 3, window_seconds, key=f"ip:{request.remote_addr}") or limited
            if limited:
                return jsonify({'error': 'Too many requests. Please wait a little and try again.'}), 429
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ==================== Errors ====================

def server_error(exc, message='Something went wrong. Please try again.'):
    """Log the real exception and send the user a generic message."""
    current_app.logger.exception('Request failed: %s', exc)
    return jsonify({'error': message}), 500
