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
    w = db.create_want(con, wanter['id'], {'born_from': 2005})
    assert w['born_from'] == 2005 and isinstance(w['born_from'], int)

    # (b) a full matching pass runs clean end to end (this is exactly what
    # crashed with `table intros has no column named created` pre-fix).
    owner = _u(con, email='o@example.com', handle='o-guy')
    _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=450)
    m = FakeMailer()
    assert match.run(con, m) == 2
    con.close()
