# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/db.py — the only module that touches SQLite. Every row except users is
owned (owner_id). Private columns are listed once, here, so the leak test in
later plans has a single source of truth.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

from bb import paths

SCHEMA_VERSION = 1

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
    if con.execute('SELECT count(*) FROM schema_version').fetchone()[0] == 0:
        con.execute('INSERT INTO schema_version VALUES (?)', (SCHEMA_VERSION,))
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
