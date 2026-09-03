# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import hashlib
import os

import pytest

from bb import account, db, publish, search


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


def _public_surface(env, con, u):
    """A fake bundle dir + tmp + lock + one search card, the way build_user leaves them."""
    dest = publish.bundle_dir(u['handle'])
    os.makedirs(os.path.join(dest, 'K01'), exist_ok=True)
    open(os.path.join(dest, 'index.html'), 'w').write('x')
    os.makedirs(dest + '.tmp', exist_ok=True)
    open(publish._lock_path(u['handle']), 'w').close()
    con.execute('INSERT INTO search_cards (knife_id, owner_id, handle, model, generation, size, born_year,'
                ' damascus_smith, damascus_pattern, special_edition, for_sale, card)'
                " VALUES (?, ?, ?, 'Sebenza', '31', 'Large', 2025, '', '', '', 0, '{}')",
                (u['id'] * 1_000_000 + 1, u['id'], u['handle']))
    con.execute('INSERT INTO search_fts (rowid, text) VALUES (?, ?)', (u['id'] * 1_000_000 + 1, 'sebenza'))
    con.commit()
    return dest


def _surface_gone(env, con, u, handle=None):
    handle = handle or u['handle']
    dest = os.path.join(env.WWW_DIR, '@' + handle)
    assert not os.path.exists(dest) and not os.path.exists(dest + '.tmp')
    assert not os.path.exists(os.path.join(env.DATA_DIR, 'publish-locks', handle + '.lock'))
    assert con.execute('SELECT count(*) FROM search_cards WHERE owner_id = ?', (u['id'],)).fetchone()[0] == 0


def test_change_handle_once(env, con):
    u = _user(con)
    _public_surface(env, con, u)
    u2 = account.change_handle(con, u, 'samuel-k')
    assert u2['handle'] == 'samuel-k' and u2['handle_changed_at']
    _surface_gone(env, con, u, 'sam')
    with pytest.raises(ValueError, match='already changed'):
        account.change_handle(con, u2, 'third')


@pytest.mark.parametrize('bad, msg', [
    ('admin', 'reserved'), ('Sam', '3–24'), ('s', '3–24'), ('x' * 25, '3–24'),
    ('sam k', '3–24'), ('sam', 'already your'), ('', '3–24'),
])
def test_change_handle_rejects(con, bad, msg):
    u = _user(con)
    with pytest.raises(ValueError, match=msg):
        account.change_handle(con, u, bad)
    assert db.get_user(con, u['id'])['handle'] == 'sam'


def test_change_handle_taken(con):
    u = _user(con)
    _user(con, 'o@example.com', 'other')
    with pytest.raises(ValueError, match='taken'):
        account.change_handle(con, u, 'other')


def test_remove_public_surface_tolerates_absence(env):
    account.remove_public_surface('nobody')     # nothing there — no raise
