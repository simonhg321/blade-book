# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
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
import pytest

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
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 14
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
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 14
    assert 'handle_changed_at' in {r[1] for r in con.execute('PRAGMA table_info(users)')}
    assert con.execute('SELECT handle_changed_at FROM users WHERE id = 1').fetchone()[0] is None
    con.close()
    con = db.connect()                                   # idempotent second open
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    con.close()
    con.close()


# --- plan 14: v11 → v12 adds maker_name (knives + search_cards) and backfills CRK ---

def _build_v11_db():
    """The live v11 shape: no maker_name anywhere. Built from the current SCHEMA
    with the two columns stripped, so nothing else drifts."""
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    v11 = db.SCHEMA.replace("  maker_name TEXT,\n", '').replace(' maker_name TEXT,', '')
    assert 'maker_name' not in v11, 'strip pattern drifted — fix the test, not the schema'
    raw = sqlite3.connect(path)
    raw.executescript(v11)
    raw.execute("INSERT INTO users (id, email, handle, created) VALUES (1, 'v11@example.com', 'v11-guy', "
                "'2026-09-05T00:00:00+00:00')")
    raw.execute("INSERT INTO knives (id, owner_id, tag, maker, status, model, created, updated) "
                "VALUES (1, 1, 'K01', 'crk', 'live', 'Sebenza', 't', 't')")
    raw.execute("INSERT INTO knives (id, owner_id, tag, maker, status, model, created, updated) "
                "VALUES (2, 1, 'K02', 'other', 'draft', 'XM-18', 't', 't')")
    raw.execute('INSERT INTO schema_version VALUES (11)')
    raw.commit()
    raw.close()


def test_v11_to_v12_adds_maker_name_and_backfills_crk(env):
    _build_v11_db()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 14
    assert 'maker_name' in {r[1] for r in con.execute('PRAGMA table_info(knives)')}
    assert 'maker_name' in {r[1] for r in con.execute('PRAGMA table_info(search_cards)')}
    rows = {r[0]: r[1] for r in con.execute('SELECT id, maker_name FROM knives')}
    assert rows == {1: 'Chris Reeve Knives', 2: None}       # only crk rows are backfilled
    con.execute("UPDATE knives SET maker_name = 'CRK (hand-edited)' WHERE id = 1")
    con.commit()
    con.close()
    con = db.connect()                                   # idempotent second open: no re-backfill
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert con.execute('SELECT maker_name FROM knives WHERE id = 1').fetchone()[0] == 'CRK (hand-edited)'
    con.close()


# --- 2026-09-22: v12 → v13 lifts the photos CHECK from 3 slots to 6 (table rebuild) ---

def _build_v12_db():
    """The live v12 shape: photos.seq CHECK (seq BETWEEN 1 AND 3). Built from the
    current SCHEMA with the CHECK narrowed back, so nothing else drifts."""
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    v12 = db.SCHEMA.replace(f'CHECK (seq BETWEEN 1 AND {db.MAX_PHOTO_SLOTS})', 'CHECK (seq BETWEEN 1 AND 3)')
    assert v12 != db.SCHEMA, 'strip pattern drifted — fix the test, not the schema'
    raw = sqlite3.connect(path)
    raw.executescript(v12)
    raw.execute("INSERT INTO users (id, email, handle, created) VALUES (1, 'v12@example.com', 'v12-guy', "
                "'2026-09-22T00:00:00+00:00')")
    raw.execute("INSERT INTO knives (id, owner_id, tag, maker, status, model, created, updated) "
                "VALUES (1, 1, 'K01', 'crk', 'live', 'Sebenza', 't', 't')")
    for seq in (1, 2, 3):
        raw.execute("INSERT INTO photos (knife_id, owner_id, seq, store_key, sha256, width, height, created) "
                    f"VALUES (1, 1, {seq}, '1/1/{seq}.jpg', 'sha{seq}', 800, 600, 't')")
    raw.execute('INSERT INTO schema_version VALUES (12)')
    raw.commit()
    raw.close()


def test_v12_to_v13_rebuilds_photos_with_six_slots(env):
    _build_v12_db()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 14
    ddl = con.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'photos'").fetchone()[0]
    assert 'BETWEEN 1 AND 6' in ddl and 'photos_new' not in ddl
    # every row survived, ids and order intact
    assert [tuple(r) for r in con.execute('SELECT id, seq, store_key FROM photos ORDER BY seq')] == \
        [(1, 1, '1/1/1.jpg'), (2, 2, '1/1/2.jpg'), (3, 3, '1/1/3.jpg')]
    # slots 4–6 now insert; 7 still refused by the DB itself
    db.add_photo(con, 1, 1, 6, '1/1/6.jpg', 'sha6', 800, 600)
    with pytest.raises(db.SlotTaken):
        db.add_photo(con, 1, 1, 7, '1/1/7.jpg', 'sha7', 800, 600)
    # UNIQUE (knife_id, seq) and the FK cascade came along with the rebuild
    with pytest.raises(db.SlotTaken):
        db.add_photo(con, 1, 1, 1, '1/1/1b.jpg', 'sha1b', 800, 600)
    idx = con.execute("SELECT sql FROM sqlite_master WHERE tbl_name = 'photos' AND type = 'index' AND sql IS NOT NULL").fetchall()
    assert idx == []   # the UNIQUE is inline in the DDL, no stray indexes from the rebuild
    con.close()

    # an old worker re-stamps 12 until the restart: the rebuild must be a no-op then
    raw = sqlite3.connect(paths.db_path()); raw.execute('UPDATE schema_version SET version = 12'); raw.commit(); raw.close()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    assert con.execute('SELECT count(*) FROM photos').fetchone()[0] == 4
    con.execute('DELETE FROM knives WHERE id = 1'); con.commit()
    assert con.execute('SELECT count(*) FROM photos').fetchone()[0] == 0   # ON DELETE CASCADE survived
    con.close()


# --- 2026-09-23: v13 → v14 adds users.password_hash (invited handle+password accounts) ---

def _build_v13_db():
    """The live v13 shape: no users.password_hash. Current SCHEMA with the one
    column stripped, so nothing else drifts."""
    path = paths.db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    v13 = db.SCHEMA.replace(",\n  password_hash TEXT\n);", "\n);")
    assert 'password_hash' not in v13, 'strip pattern drifted — fix the test, not the schema'
    raw = sqlite3.connect(path)
    raw.executescript(v13)
    raw.execute("INSERT INTO users (id, email, handle, created) VALUES (1, 'v13@example.com', 'v13-guy', "
                "'2026-09-23T00:00:00+00:00')")
    raw.execute('INSERT INTO schema_version VALUES (13)')
    raw.commit()
    raw.close()


def test_v13_to_v14_adds_password_hash(env):
    _build_v13_db()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION == 14
    assert 'password_hash' in {r[1] for r in con.execute('PRAGMA table_info(users)')}
    assert db.get_user(con, 1)['password_hash'] is None
    con.close()
    # an old worker re-stamps 13 until the restart: the ALTER must be tolerated
    raw = sqlite3.connect(paths.db_path()); raw.execute('UPDATE schema_version SET version = 13'); raw.commit(); raw.close()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == 14
    con.close()
