# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/db.py — the only module that touches SQLite. Every row except users is
owned (owner_id). Private columns are listed once, here, so the leak test in
later plans has a single source of truth.
"""
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from bb import paths

SCHEMA_VERSION = 2

SALE_STATUSES = ('keeping', 'for_trade', 'for_sale', 'consigned', 'sold')
KNIFE_STATUSES = ('draft', 'live')
SUB_STATUSES = ('free', 'active', 'lapsed')
EVENT_TYPES = ('photographed', 'decoded', 'edited', 'for_trade', 'for_sale',
               'traded', 'sold', 'consigned', 'withdrawn')
WANT_MODES = ('trade', 'sale', 'either')

# never leaves the private register — see spec §5 invariant
PRIVATE_COLUMNS = frozenset({'price_paid', 'acquired_from', 'acquired_date',
                             'location', 'notes_private', 'condition_note',
                             'confidence'})

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  handle TEXT NOT NULL UNIQUE,
  display_name TEXT,
  auth_subjects TEXT NOT NULL DEFAULT '{{}}',
  created TEXT NOT NULL,
  verified_at TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0,
  sub_status TEXT NOT NULL DEFAULT 'free'
    CHECK (sub_status IN {SUB_STATUSES}),
  sub_source TEXT NOT NULL DEFAULT 'manual',
  stripe_customer_id TEXT,
  free_old_used INTEGER NOT NULL DEFAULT 0,
  share_email_on_intro INTEGER NOT NULL DEFAULT 1,
  hide_born_day INTEGER NOT NULL DEFAULT 0,
  profile_private INTEGER NOT NULL DEFAULT 0,
  session_secret TEXT NOT NULL DEFAULT '',
  last_tag_no INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS knives (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tag TEXT NOT NULL,
  maker TEXT NOT NULL DEFAULT 'crk',
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN {KNIFE_STATUSES}),
  -- core
  model TEXT, variant TEXT, blade_steel TEXT, blade_shape TEXT,
  blade_length_in REAL, handle_material TEXT, lock_type TEXT,
  born_on TEXT, born_on_precision TEXT, born_on_source TEXT,
  condition INTEGER,
  has_box INTEGER, has_card INTEGER, has_papers INTEGER, has_pouch INTEGER,
  has_lanyard INTEGER, has_spare_hardware INTEGER,
  -- maker extension (JSON; CRK: generation, size, crk_sku, hand, ...)
  ext TEXT NOT NULL DEFAULT '{{}}',
  -- private
  price_paid REAL, acquired_from TEXT, acquired_date TEXT, location TEXT,
  notes_private TEXT, condition_note TEXT, confidence TEXT,
  -- public
  notes_public TEXT,
  is_public INTEGER NOT NULL DEFAULT 1,
  sale_status TEXT NOT NULL DEFAULT 'keeping'
    CHECK (sale_status IN {SALE_STATUSES}),
  asking_price REAL, seller_note TEXT,
  hero_photo INTEGER,
  created TEXT NOT NULL, updated TEXT NOT NULL,
  UNIQUE (owner_id, tag)
);
CREATE INDEX IF NOT EXISTS knives_owner ON knives(owner_id);
CREATE INDEX IF NOT EXISTS knives_public ON knives(is_public, status, sale_status);

CREATE TABLE IF NOT EXISTS photos (
  id INTEGER PRIMARY KEY,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  seq INTEGER NOT NULL CHECK (seq BETWEEN 1 AND 3),
  store_key TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  width INTEGER, height INTEGER,
  public_key TEXT,
  created TEXT NOT NULL,
  UNIQUE (knife_id, seq)
);

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  date TEXT NOT NULL,
  type TEXT NOT NULL CHECK (type IN {EVENT_TYPES}),
  detail TEXT, amount REAL, counterparty TEXT,
  public_visible INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_knife ON events(knife_id);

CREATE TABLE IF NOT EXISTS wants (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  maker TEXT NOT NULL DEFAULT 'crk',
  model TEXT, generation TEXT, size TEXT, blade_shape TEXT, blade_steel TEXT,
  keyword TEXT, born_from TEXT, born_to TEXT,
  mode TEXT NOT NULL DEFAULT 'either' CHECK (mode IN {WANT_MODES}),
  max_price REAL,
  active INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS intros (
  id INTEGER PRIMARY KEY,
  want_id INTEGER REFERENCES wants(id) ON DELETE SET NULL,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  from_user INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  to_user INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('match', 'board')),
  sent_at TEXT NOT NULL,
  resend_msg_id TEXT,
  UNIQUE (want_id, knife_id)
);

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  knife_id INTEGER NOT NULL REFERENCES knives(id) ON DELETE CASCADE,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  reason TEXT NOT NULL,
  created TEXT NOT NULL,
  resolved_at TEXT, resolution TEXT
);

CREATE TABLE IF NOT EXISTS deleted_users (
  email_hash TEXT PRIMARY KEY,
  deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS magic_tokens (
  id INTEGER PRIMARY KEY,
  token_hash TEXT NOT NULL UNIQUE,
  email TEXT NOT NULL,
  ip TEXT,
  created TEXT NOT NULL,
  expires TEXT NOT NULL,
  used_at TEXT
);
CREATE INDEX IF NOT EXISTS magic_tokens_email ON magic_tokens(email, created);

CREATE TABLE IF NOT EXISTS auth_attempts (
  id INTEGER PRIMARY KEY,
  ip TEXT NOT NULL,
  ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS auth_attempts_ip ON auth_attempts(ip, ts);

CREATE TABLE IF NOT EXISTS oauth_states (
  state TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  nonce TEXT NOT NULL,
  created TEXT NOT NULL,
  expires TEXT NOT NULL
);
"""


def now():
    return datetime.now(timezone.utc).isoformat()


def connect():
    """Open (and on first use create) the database. WAL + 5 s busy timeout
    so gunicorn workers and the match cron coexist; FK enforcement on so
    account deletion cascades."""
    os.makedirs(os.path.dirname(paths.db_path()), exist_ok=True)
    con = sqlite3.connect(paths.db_path(), timeout=5.0)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=5000')
    con.execute('PRAGMA foreign_keys=ON')
    con.executescript(SCHEMA)
    row = con.execute('SELECT version FROM schema_version').fetchone()
    if row is None:
        con.execute('INSERT INTO schema_version VALUES (?)', (SCHEMA_VERSION,))
    elif row[0] != SCHEMA_VERSION:
        con.execute('UPDATE schema_version SET version = ?', (SCHEMA_VERSION,))
    con.commit()
    return con


def _user_row(row):
    if row is None:
        return None
    d = dict(row)
    d['auth_subjects'] = json.loads(d['auth_subjects'] or '{}')
    return d


def create_user(con, email, handle, display_name=None, auth_subjects=None):
    cur = con.execute(
        'INSERT INTO users (email, handle, display_name, auth_subjects, created) '
        'VALUES (?, ?, ?, ?, ?)',
        (email.strip().lower(), handle, display_name,
         json.dumps(auth_subjects or {}), now()))
    con.commit()
    return cur.lastrowid


def get_user(con, user_id):
    return _user_row(con.execute('SELECT * FROM users WHERE id = ?',
                                 (user_id,)).fetchone())


def get_user_by_email(con, email):
    return _user_row(con.execute('SELECT * FROM users WHERE email = ?',
                                 (email.strip().lower(),)).fetchone())


def get_user_by_handle(con, handle):
    return _user_row(con.execute('SELECT * FROM users WHERE handle = ?',
                                 (handle,)).fetchone())


def next_tag(con, owner_id):
    """K01, K02, ... per owner. Monotonic forever: a purged draft's tag is
    never handed out again, because the counter lives on the user row."""
    con.execute('UPDATE users SET last_tag_no = last_tag_no + 1 WHERE id = ?',
                (owner_id,))
    n = con.execute('SELECT last_tag_no FROM users WHERE id = ?',
                    (owner_id,)).fetchone()[0]
    con.commit()
    return f'K{n:02d}'


MAGIC_TTL_MIN = 15
STATE_TTL_MIN = 10


def _norm_email(email):
    return email.strip().lower()


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def _plus(minutes):
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()


# --- magic links -----------------------------------------------------------

def create_magic_token(con, email, ip):
    token = secrets.token_urlsafe(32)
    con.execute(
        'INSERT INTO magic_tokens (token_hash, email, ip, created, expires) '
        'VALUES (?, ?, ?, ?, ?)',
        (_sha(token), _norm_email(email), ip, now(), _plus(MAGIC_TTL_MIN)))
    con.commit()
    return token


def consume_magic_token(con, token):
    """Email for a live, unused token — and burn it, atomically (RETURNING
    means the UPDATE's WHERE clause is the single point of truth for
    single-use, no read-then-write race). None otherwise."""
    row = con.execute(
        'UPDATE magic_tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL '
        'AND expires > ? RETURNING email', (now(), _sha(token), now())).fetchone()
    con.commit()
    return row['email'] if row else None


def count_magic_tokens_since(con, email, since_iso):
    return con.execute(
        'SELECT count(*) FROM magic_tokens WHERE email = ? AND created >= ?',
        (_norm_email(email), since_iso)).fetchone()[0]


# --- per-IP attempts -------------------------------------------------------

def record_attempt(con, ip):
    con.execute('INSERT INTO auth_attempts (ip, ts) VALUES (?, ?)', (ip, now()))
    con.commit()


def count_attempts_since(con, ip, since_iso):
    return con.execute(
        'SELECT count(*) FROM auth_attempts WHERE ip = ? AND ts >= ?',
        (ip, since_iso)).fetchone()[0]


# --- OIDC state (DB, not cookie: Apple's form_post callback is cross-site) --

def create_oauth_state(con, provider, nonce):
    state = secrets.token_urlsafe(24)
    con.execute(
        'INSERT INTO oauth_states (state, provider, nonce, created, expires) '
        'VALUES (?, ?, ?, ?, ?)', (state, provider, nonce, now(), _plus(STATE_TTL_MIN)))
    con.commit()
    return state


def pop_oauth_state(con, state):
    row = con.execute(
        'DELETE FROM oauth_states WHERE state = ? AND expires > ? RETURNING provider, nonce',
        (state, now())).fetchone()
    con.execute('DELETE FROM oauth_states WHERE state = ?', (state,))  # burn an expired one too
    con.commit()
    return dict(row) if row else None


# --- user auth fields ------------------------------------------------------

def rotate_session_secret(con, user_id):
    secret = secrets.token_hex(16)
    con.execute('UPDATE users SET session_secret = ? WHERE id = ?', (secret, user_id))
    con.commit()
    return secret


def set_verified(con, user_id):
    con.execute('UPDATE users SET verified_at = ? WHERE id = ? AND verified_at IS NULL',
                (now(), user_id))
    con.commit()


def set_auth_subject(con, user_id, provider, sub):
    subjects = get_user(con, user_id)['auth_subjects']
    subjects[provider] = sub
    con.execute('UPDATE users SET auth_subjects = ? WHERE id = ?',
                (json.dumps(subjects), user_id))
    con.commit()


def get_user_by_subject(con, provider, sub):
    return _user_row(con.execute(
        "SELECT * FROM users WHERE json_extract(auth_subjects, '$.' || ?) = ?",
        (provider, sub)).fetchone())


def handle_exists(con, handle):
    return con.execute('SELECT 1 FROM users WHERE handle = ?',
                       (handle,)).fetchone() is not None


def purge_auth_tables(con):
    cutoff = _plus(-24 * 60)
    con.execute('DELETE FROM magic_tokens WHERE created < ?', (cutoff,))
    con.execute('DELETE FROM auth_attempts WHERE ts < ?', (cutoff,))
    con.execute('DELETE FROM oauth_states WHERE created < ?', (cutoff,))
    con.commit()
