# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Final-review BLOCKER 1: wants/intros have existed since schema v1 with a
DIFFERENT shape (TEXT born_from/born_to, no intros.created, sent_at NOT
NULL) — CREATE TABLE IF NOT EXISTS is a no-op against an existing table, so
a v5 box (the live shape, confirmed at schema_version=5) needs a REAL
migration, not just the reshaped CREATE statements in SCHEMA. This test
builds that v5 shape directly (bypassing db.connect(), which would only
ever produce the current v6 shape) and proves db.connect() repairs it and
a full match.run() pass works cleanly afterward."""
import os
import sqlite3

from bb import db, match, paths
from bb.mail import FakeMailer
from tests.test_search import _mk_knife
from tests.test_wants_db import _u

# Verbatim from `git show c8ff56e:bb/db.py` — the shape every wants/intros
# row has had on stark since v1.
OLD_WANTS_DDL = """
CREATE TABLE IF NOT EXISTS wants (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  maker TEXT NOT NULL DEFAULT 'crk',
  model TEXT, generation TEXT, size TEXT, blade_shape TEXT, blade_steel TEXT,
  keyword TEXT, born_from TEXT, born_to TEXT,
  mode TEXT NOT NULL DEFAULT 'either' CHECK (mode IN ('trade', 'sale', 'either')),
  max_price REAL,
  active INTEGER NOT NULL DEFAULT 1,
  created TEXT NOT NULL
);
"""

OLD_INTROS_DDL = """
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
"""


def _build_v5_db():
    """A fresh DB stamped v5, with wants/intros in the OLD (v1-v5) shape and
    everything else in the current shape (unaffected by this migration)."""
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA)                     # current shape baseline
    raw.executescript('DROP TABLE wants; DROP TABLE intros;')
    raw.executescript(OLD_WANTS_DDL + OLD_INTROS_DDL)
    raw.execute('INSERT INTO schema_version VALUES (5)')
    raw.commit()
    raw.close()


def test_v5_migration_repairs_old_wants_intros_shape(env):
    _build_v5_db()

    con = db.connect()
    version = con.execute('SELECT version FROM schema_version').fetchone()[0]
    assert version == db.SCHEMA_VERSION

    # (a) the new shape landed: intros has `created`, wants.born_from is
    # INTEGER-affinity (a v5 box's TEXT column would silently store '2005'
    # and poison every born-range compare downstream).
    intro_cols = {r[1] for r in con.execute('PRAGMA table_info(intros)')}
    assert 'created' in intro_cols
    wanter = _u(con)
    w = db.create_want(con, wanter['id'], {'born_from': 2005, 'born_to': 2010})
    assert w['born_from'] == 2005 and isinstance(w['born_from'], int)

    # (b) a full matching pass runs clean end to end (this is exactly what
    # crashed with `table intros has no column named created` pre-fix).
    owner = _u(con, email='o@example.com', handle='o-guy')
    _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=450)
    m = FakeMailer()
    assert match.run(con, m) == 2
    con.close()


def _build_v6_db():
    """A fresh DB stamped v6: current shape minus the plan-09 columns/index
    (sqlite ≥ 3.35 supports DROP COLUMN; stark has 3.45)."""
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA)
    raw.executescript("""
        DROP INDEX IF EXISTS idx_reports_open;
        ALTER TABLE knives DROP COLUMN listed_at;
        ALTER TABLE knives DROP COLUMN hidden_at;
        ALTER TABLE knives DROP COLUMN hidden_by;
        ALTER TABLE knives DROP COLUMN hidden_note;
        ALTER TABLE intros DROP COLUMN message;
    """)
    ts = '2026-09-01T00:00:00+00:00'
    raw.execute("INSERT INTO users (id, email, handle, created) VALUES (1, 'v6@example.com', 'v6-guy', ?)", (ts,))
    raw.execute("INSERT INTO knives (id, owner_id, tag, status, sale_status, asking_price, created, updated) "
                "VALUES (1, 1, 'K01', 'live', 'for_sale', 500, ?, ?)", (ts, '2026-09-01T12:00:00+00:00'))
    raw.execute("INSERT INTO knives (id, owner_id, tag, status, sale_status, created, updated) "
                "VALUES (2, 1, 'K02', 'live', 'keeping', ?, ?)", (ts, ts))
    raw.execute('INSERT INTO schema_version VALUES (6)')
    raw.commit()
    raw.close()


def test_v6_to_v7_adds_columns_index_and_backfills_listed_at(env):
    _build_v6_db()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    kcols = {r[1] for r in con.execute('PRAGMA table_info(knives)')}
    assert {'listed_at', 'hidden_at', 'hidden_by', 'hidden_note'} <= kcols
    assert 'message' in {r[1] for r in con.execute('PRAGMA table_info(intros)')}
    assert 'idx_reports_open' in [r[1] for r in con.execute('PRAGMA index_list(reports)')]
    rows = {r[0]: r[1] for r in con.execute('SELECT tag, listed_at FROM knives')}
    assert rows['K01'] == '2026-09-01T12:00:00+00:00'   # for_sale before the upgrade → listed_at = updated
    assert rows['K02'] is None
    con.close()
    con = db.connect()                                   # idempotent second open
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    con.close()


# Verbatim shape of `users` at schema v7 — before hero-pin's featured_knife_id
# column. Hand-written (not derived from the live db.SCHEMA, unlike a bare
# `ALTER TABLE ... DROP COLUMN` against it) so this fixture keeps meaning
# "v7" even after a later migration touches `users` again.
OLD_USERS_V7_DDL = """
CREATE TABLE users (
  id INTEGER PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  handle TEXT NOT NULL UNIQUE,
  display_name TEXT,
  auth_subjects TEXT NOT NULL DEFAULT '{}',
  created TEXT NOT NULL,
  verified_at TEXT,
  is_admin INTEGER NOT NULL DEFAULT 0,
  sub_status TEXT NOT NULL DEFAULT 'free'
    CHECK (sub_status IN ('free', 'active', 'lapsed')),
  sub_source TEXT NOT NULL DEFAULT 'manual',
  stripe_customer_id TEXT,
  free_old_used INTEGER NOT NULL DEFAULT 0,
  share_email_on_intro INTEGER NOT NULL DEFAULT 1,
  hide_born_day INTEGER NOT NULL DEFAULT 0,
  profile_private INTEGER NOT NULL DEFAULT 0,
  public_key TEXT,
  publish_dirty_at TEXT,
  session_secret TEXT NOT NULL DEFAULT '',
  last_tag_no INTEGER NOT NULL DEFAULT 0
);
"""


def _build_v7_db():
    """A fresh DB stamped v7: current shape for every other table, `users`
    hand-written to the v7 shape (no featured_knife_id)."""
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA)
    raw.executescript('DROP TABLE users;' + OLD_USERS_V7_DDL)
    ts = '2026-09-02T00:00:00+00:00'
    raw.execute("INSERT INTO users (id, email, handle, created) VALUES (1, 'v7@example.com', 'v7-guy', ?)", (ts,))
    raw.execute('INSERT INTO schema_version VALUES (7)')
    raw.commit()
    raw.close()


def test_v7_to_v8_adds_featured_knife_id(env):
    _build_v7_db()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 11
    ucols = {r[1] for r in con.execute('PRAGMA table_info(users)')}
    assert 'featured_knife_id' in ucols
    assert con.execute('SELECT featured_knife_id FROM users WHERE id = 1').fetchone()[0] is None
    con.close()
    con = db.connect()                                   # idempotent second open
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION


# Verbatim `users` at schema v8 (v7 + featured_knife_id) — before plan 11's
# handle_changed_at. Hand-written for the same reason as OLD_USERS_V7_DDL.
OLD_USERS_V8_DDL = OLD_USERS_V7_DDL.replace(
    '  last_tag_no INTEGER NOT NULL DEFAULT 0\n);',
    '  last_tag_no INTEGER NOT NULL DEFAULT 0,\n  featured_knife_id INTEGER\n);')


def _build_v8_db():
    assert 'featured_knife_id' in OLD_USERS_V8_DDL
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    raw = sqlite3.connect(path)
    raw.executescript(db.SCHEMA)
    raw.executescript('DROP TABLE users;' + OLD_USERS_V8_DDL)
    raw.execute("INSERT INTO users (id, email, handle, created) VALUES (1, 'v8@example.com', 'v8-guy', "
                "'2026-09-03T00:00:00+00:00')")
    raw.execute('INSERT INTO schema_version VALUES (8)')
    raw.commit()
    raw.close()


def test_v8_to_v9_adds_handle_changed_at(env):
    _build_v8_db()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 11
    assert 'handle_changed_at' in {r[1] for r in con.execute('PRAGMA table_info(users)')}
    assert con.execute('SELECT handle_changed_at FROM users WHERE id = 1').fetchone()[0] is None
    con.close()
    con = db.connect()                                   # idempotent second open
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    con.close()
    con.close()
