# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import sqlite3

import pytest

from bb import db


def test_connect_creates_schema_with_wal():
    con = db.connect()
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {'users', 'knives', 'photos', 'events', 'wants', 'intros',
            'reports', 'deleted_users', 'schema_version'} <= tables
    assert con.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert con.execute('PRAGMA foreign_keys').fetchone()[0] == 1
    con.close()


def test_every_table_but_users_has_owner_id():
    con = db.connect()
    for t in ('knives', 'photos', 'events', 'wants', 'reports'):
        cols = {r[1] for r in con.execute(f'PRAGMA table_info({t})')}
        assert 'owner_id' in cols, t
    con.close()


def test_private_columns_exist_on_knives():
    con = db.connect()
    cols = {r[1] for r in con.execute('PRAGMA table_info(knives)')}
    assert db.PRIVATE_COLUMNS <= cols
    con.close()


def test_create_and_fetch_user():
    con = db.connect()
    uid = db.create_user(con, 'a@example.com', 'alice', display_name='Alice',
                         auth_subjects={'google': 'g-123'})
    u = db.get_user(con, uid)
    assert u['email'] == 'a@example.com' and u['handle'] == 'alice'
    assert u['sub_status'] == 'free' and u['free_old_used'] == 0
    assert u['auth_subjects'] == {'google': 'g-123'}
    assert db.get_user_by_email(con, 'a@example.com'.upper())['id'] == uid
    assert db.get_user_by_handle(con, 'alice')['id'] == uid
    assert db.get_user_by_email(con, 'nobody@example.com') is None
    con.close()


def test_email_and_handle_are_unique():
    con = db.connect()
    db.create_user(con, 'a@example.com', 'alice')
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user(con, 'a@example.com', 'alice2')
    with pytest.raises(sqlite3.IntegrityError):
        db.create_user(con, 'b@example.com', 'alice')
    con.close()


def test_next_tag_is_per_owner_and_never_reuses():
    con = db.connect()
    a = db.create_user(con, 'a@example.com', 'alice')
    b = db.create_user(con, 'b@example.com', 'bob')
    assert db.next_tag(con, a) == 'K01'
    assert db.next_tag(con, a) == 'K02'
    assert db.next_tag(con, b) == 'K01'
    # abandoning a draft never hands K02 out again
    assert db.next_tag(con, a) == 'K03'
    con.close()


def test_knife_sale_status_is_constrained():
    con = db.connect()
    a = db.create_user(con, 'a@example.com', 'alice')
    con.execute("INSERT INTO knives (owner_id, tag, maker, status, created, updated) "
                "VALUES (?, 'K01', 'crk', 'draft', ?, ?)", (a, db.now(), db.now()))
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO knives (owner_id, tag, maker, status, sale_status, created, updated) "
                    "VALUES (?, 'K02', 'crk', 'draft', 'bogus', ?, ?)", (a, db.now(), db.now()))
    con.close()


def test_deleting_user_cascades():
    con = db.connect()
    a = db.create_user(con, 'a@example.com', 'alice')
    con.execute("INSERT INTO knives (owner_id, tag, maker, status, created, updated) "
                "VALUES (?, 'K01', 'crk', 'draft', ?, ?)", (a, db.now(), db.now()))
    con.execute('DELETE FROM users WHERE id = ?', (a,))
    assert con.execute('SELECT count(*) FROM knives').fetchone()[0] == 0
    con.close()
