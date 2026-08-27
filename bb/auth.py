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

from bb import db

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
