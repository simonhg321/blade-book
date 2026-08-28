# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/auth.py — handles, sessions, and the login_required gate.

Session = Flask's signed cookie (SECRET_KEY = SESSION_KEY from .env), scoped
to /blade-book, carrying {'uid', 'ssh'} where ssh is a hash of the user's
session_secret. Rotating that secret invalidates every device at once.
"""
import hashlib
import re
import unicodedata
from functools import wraps

from flask import g, jsonify, request, session

from bb import config, db, paths

HANDLE_MAX = 24
HANDLE_MIN = 3

RESERVED_HANDLES = frozenset({
    'admin', 'administrator', 'root', 'staff', 'support', 'help', 'mod',
    'crk', 'chrisreeve', 'chris-reeve', 'reeve', 'blade-book', 'bladebook',
    'api', 'me', 'search', 'about', 'terms', 'privacy', 'board', 'settings',
    'wants', 'add', 'login', 'signin', 'signout', 'auth', 'static', 'assets',
    'healthz', 'new', 'null', 'undefined', 'www', 'mail', 'noreply', 'simon',
})


def slugify_handle(text):
    """Lower-case ascii slug, 3–24 chars, never reserved. Deterministic."""
    s = unicodedata.normalize('NFKD', text or '').encode('ascii', 'ignore').decode()
    s = re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    s = re.sub(r'-{2,}', '-', s)[:HANDLE_MAX].strip('-')
    if not s:
        return 'collector'
    if len(s) < HANDLE_MIN or s in RESERVED_HANDLES:
        s = f'{s}-collector'[:HANDLE_MAX].strip('-')
    return s


def unique_handle(con, base):
    """base, base-2, base-3 ... — first one not taken, trimmed to HANDLE_MAX."""
    if not db.handle_exists(con, base):
        return base
    n = 2
    while True:
        suffix = f'-{n}'
        candidate = base[:HANDLE_MAX - len(suffix)].rstrip('-') + suffix
        if not db.handle_exists(con, candidate):
            return candidate
        n += 1


def handle_for_email(con, email):
    local = email.strip().lower().split('@', 1)[0]
    return unique_handle(con, slugify_handle(local))


# --- sessions ----------------------------------------------------------------

DEFAULT_BASE_URL = 'https://billboard.instockornot.club'
SELF_VIEW_FIELDS = ('id', 'email', 'handle', 'display_name', 'verified_at',
                    'is_admin', 'sub_status', 'created')


def base_url():
    return config.get('BASE_URL', DEFAULT_BASE_URL).rstrip('/')


def client_ip():
    """Behind exactly one trusted proxy (Apache on this box) the real client is
    the LAST X-Forwarded-For hop — Apache appends it; earlier hops are whatever
    the client chose to send. Never use the first hop."""
    fwd = request.headers.get('X-Forwarded-For', '')
    return (fwd.rsplit(',', 1)[-1].strip() if fwd else request.remote_addr) or '?'


def _secret_hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()[:16]


def login(con, user):
    if not user['session_secret']:
        user['session_secret'] = db.rotate_session_secret(con, user['id'])
    session.clear()
    session.permanent = True
    session['uid'] = user['id']
    session['ssh'] = _secret_hash(user['session_secret'])
    g.user = user


def logout():
    session.clear()
    g.user = None


def logout_everywhere(con, user_id):
    db.rotate_session_secret(con, user_id)
    logout()


def current_user(con):
    """User dict for the session cookie, or None. Cached on g per request."""
    if 'user' in g:
        return g.user
    g.user = None
    uid, ssh = session.get('uid'), session.get('ssh')
    if uid and ssh:
        user = db.get_user(con, uid)
        if user and user['session_secret'] and _secret_hash(user['session_secret']) == ssh:
            g.user = user
    return g.user


def self_view(user):
    """The signed-in user's own view — includes email; never use for another user's page."""
    return {k: user[k] for k in SELF_VIEW_FIELDS}


def sign_in_by_email(con, email, provider=None, sub=None, verified=True):
    """Find-or-create the account for a proven email, merge the OIDC subject
    if any, mark verified, and log in. The single entry point for every
    sign-in method (spec §6: same email across providers → one user)."""
    # TODO plan 10/11: consult deleted_users (email hash) so a re-created account gets no fresh free_old_used allowance
    email = email.strip().lower()
    user = db.get_user_by_email(con, email)
    if user is None and provider and sub:
        user = db.get_user_by_subject(con, provider, sub)  # email changed at provider
    if user is None:
        uid = db.create_user(con, email, handle_for_email(con, email))
        db.rotate_session_secret(con, uid)
        user = db.get_user(con, uid)
    if provider and sub and user['auth_subjects'].get(provider) != sub:
        db.set_auth_subject(con, user['id'], provider, sub)
    if verified:
        db.set_verified(con, user['id'])
    user = db.get_user(con, user['id'])
    login(con, user)
    return user


UNAUTHENTICATED_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f'<meta http-equiv="refresh" content="30;url={paths.URL_PREFIX}/?auth=required">'
    '<title>Unauthenticated — blade-book</title>'
    '<style>body{font-family:system-ui,sans-serif;background:#f6f1e7;color:#1a1a1a;margin:0;'
    'display:flex;min-height:100vh;align-items:center;justify-content:center}'
    'main{text-align:center;padding:2rem}h1{font-size:1.6rem;margin:0 0 .5rem}'
    'a{color:#1a1a1a;font-weight:600}p{margin:.4rem 0}</style></head><body><main>'
    '<h1>Unauthenticated</h1>'
    f'<p><a href="{paths.URL_PREFIX}/?auth=required">Sign in to blade-book</a></p>'
    '<p style="color:#666;font-size:.9rem">Taking you there in 30 seconds.</p>'
    '</main></body></html>'
)


def _wants_html():
    """True for a browser navigation (address bar, link), false for fetch/XHR.
    fetch() sends Accept */* by default; navigations rank text/html first."""
    best = request.accept_mimetypes.best_match(['text/html', 'application/json'])
    return best == 'text/html' and request.accept_mimetypes['text/html'] > request.accept_mimetypes['application/json']


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        con = db.connect()
        try:
            user = current_user(con)
        finally:
            con.close()
        if user is None:
            if _wants_html():
                # a person typed an API URL into the address bar → a page, not JSON:
                # says so, links to sign-in, forwards there by itself after 30 s
                return UNAUTHENTICATED_HTML, 401, {'Content-Type': 'text/html; charset=utf-8'}
            return jsonify({'error': 'sign in required'}), 401
        return fn(*a, **kw)
    return wrapper


# --- rate limits -------------------------------------------------------------

EMAIL_LINKS_PER_HOUR = 5
IP_ATTEMPTS_PER_HOUR = 30
PURGE_EVERY = 50


def check_rate_limits(con, email=None):
    """Record this attempt for the caller's IP and answer with a reason string
    if either limit is hit, else None. Purges stale auth rows every
    PURGE_EVERY attempts (keyed on the attempts row count, so no randomness
    and no cron)."""
    from datetime import datetime, timedelta, timezone
    hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    ip = client_ip()
    db.record_attempt(con, ip)
    total = con.execute('SELECT count(*) FROM auth_attempts').fetchone()[0]
    if total % PURGE_EVERY == 0:
        db.purge_auth_tables(con)
    if db.count_attempts_since(con, ip, hour_ago) > IP_ATTEMPTS_PER_HOUR:
        return 'too many sign-in attempts — try again in an hour'
    if email and db.count_magic_tokens_since(con, email, hour_ago) >= EMAIL_LINKS_PER_HOUR:
        return 'too many links sent to that address — try again in an hour'
    return None
