# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import hashlib
import os

import pytest

from bb import db


def _user(con, email='sam@example.com', handle='sam', **cols):
    uid = db.create_user(con, email, handle)
    for c, v in cols.items():
        con.execute(f'UPDATE users SET {c} = ? WHERE id = ?', (v, uid))
    con.commit()
    return db.get_user(con, uid)


def test_create_user_free_old_used(con):
    a = db.create_user(con, 'a@example.com', 'a-guy')
    b = db.create_user(con, 'b@example.com', 'b-guy', free_old_used=3)
    assert db.get_user(con, a)['free_old_used'] == 0
    assert db.get_user(con, b)['free_old_used'] == 3


def test_tombstone_roundtrip(con):
    assert db.is_tombstoned(con, 'gone@example.com') is False
    db.tombstone_email(con, '  gone@example.com ')       # _norm_email strips + lower-cases
    assert db.is_tombstoned(con, 'gone@example.com') is True
    db.tombstone_email(con, 'gone@example.com')        # idempotent (INSERT OR REPLACE)
    assert con.execute('SELECT count(*) FROM deleted_users').fetchone()[0] == 1
    h = hashlib.sha256(b'gone@example.com').hexdigest()
    assert con.execute('SELECT email_hash FROM deleted_users').fetchone()[0] == h


def test_owner_photo_keys_every_status(con):
    u = _user(con)
    d = db.create_draft_knife(con, u['id'])
    db.add_photo(con, u['id'], d['id'], 1, f"{u['id']}/{d['id']}/1.jpg", 'x' * 64, 10, 10)
    db.add_photo(con, u['id'], d['id'], 2, f"{u['id']}/{d['id']}/2.jpg", 'y' * 64, 10, 10)
    other = _user(con, 'o@example.com', 'other')
    od = db.create_draft_knife(con, other['id'])
    db.add_photo(con, other['id'], od['id'], 1, f"{other['id']}/{od['id']}/1.jpg", 'z' * 64, 10, 10)
    keys = db.owner_photo_keys(con, u['id'])
    assert sorted(keys) == sorted([f"{u['id']}/{d['id']}/1.jpg", f"{u['id']}/{d['id']}/1.thumb.jpg",
                                   f"{u['id']}/{d['id']}/2.jpg", f"{u['id']}/{d['id']}/2.thumb.jpg"])


def test_set_handle_stamps_once(con):
    u = _user(con)
    assert u['handle_changed_at'] is None
    u2 = db.set_handle(con, u['id'], 'samuel')
    assert u2['handle'] == 'samuel' and u2['handle_changed_at']
    assert db.get_user_by_handle(con, 'sam') is None


def test_delete_user_cascades(con):
    u = _user(con)
    k = db.create_draft_knife(con, u['id'])
    db.add_photo(con, u['id'], k['id'], 1, f"{u['id']}/{k['id']}/1.jpg", 'x' * 64, 10, 10)
    db.add_event(con, u['id'], k['id'], 'photographed')
    other = _user(con, 'o@example.com', 'other')
    ok = db.create_draft_knife(con, other['id'])
    counts = db.delete_user(con, u['id'])
    assert counts == {'knives': 1, 'photos': 1}
    for table in ('users', 'knives', 'photos', 'events', 'wants'):
        col = 'id' if table == 'users' else 'owner_id'
        assert con.execute(f'SELECT count(*) FROM {table} WHERE {col} = ?', (u['id'],)).fetchone()[0] == 0, table
    assert db.get_user(con, other['id']) is not None
    assert db.get_knife(con, other['id'], ok['id']) is not None
