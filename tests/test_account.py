# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import csv
import fcntl
import hashlib
import io
import json
import os
import threading
import time
import zipfile

import pytest

from bb import account, db, publish, search
from bb.store import LocalFSStore


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
    assert account.remove_public_surface('nobody') is True     # nothing there — no raise


def test_remove_public_surface_waits_for_build_lock(env):
    dest = publish.bundle_dir('sam')
    os.makedirs(dest, exist_ok=True)
    lockf = open(publish._lock_path('sam'), 'w')
    fcntl.flock(lockf, fcntl.LOCK_EX)
    result = {}
    t = threading.Thread(target=lambda: result.__setitem__('ok', account.remove_public_surface('sam')))
    t.start()
    try:
        time.sleep(0.2)
        assert os.path.exists(dest)
        assert t.is_alive()
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    t.join(2)
    assert not t.is_alive()
    assert not os.path.exists(dest)
    assert result['ok'] is True


def test_remove_public_surface_reports_leftover(env, monkeypatch):
    dest = publish.bundle_dir('sam')
    os.makedirs(dest, exist_ok=True)
    monkeypatch.setattr('bb.account.shutil.rmtree', lambda *a, **kw: None)
    assert account.remove_public_surface('sam') is False
    assert os.path.exists(dest)


def _knife_with_photo(con, store, u, tag_photo=b'JPEGBYTES'):
    k = db.create_draft_knife(con, u['id'])
    con.execute("UPDATE knives SET model = 'Sebenza', born_on = '2025-09-29', price_paid = 475, "
                "notes_private = 'gift from Dad', confidence = '{}' WHERE id = ?", (k['id'],))
    con.commit()
    key = f"{u['id']}/{k['id']}/1.jpg"
    store.put(key, tag_photo)
    db.add_photo(con, u['id'], k['id'], 1, key, hashlib.sha256(tag_photo).hexdigest(), 800, 600)
    db.publish_knife(con, u['id'], k['id'])
    return db.get_knife(con, u['id'], k['id'])


def test_export_zip_contents(env, con, tmp_path):
    store = LocalFSStore(str(tmp_path / 'store'))
    u = _user(con)
    k = _knife_with_photo(con, store, u)
    out = account.export_zip(con, store, u, str(tmp_path / 'exports'))
    assert os.path.basename(out).startswith('sam-') and out.endswith('.zip')
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert names == {'knives.json', 'knives.csv', f"photos/{k['tag']}-1.jpg"}
        j = json.loads(z.read('knives.json'))
        assert j['handle'] == 'sam' and j['count'] == 1 and j['missing_photos'] == []
        row = j['knives'][0]
        assert row['tag'] == k['tag'] and row['price_paid'] == 475 and row['notes_private'] == 'gift from Dad'
        assert row['photos'][0]['file'] == f"photos/{k['tag']}-1.jpg"
        assert 'events' in row
        rows = list(csv.DictReader(io.StringIO(z.read('knives.csv').decode())))
        assert rows[0]['tag'] == k['tag'] and rows[0]['price_paid'] == '475.0' and rows[0]['photo_count'] == '1'
        assert list(rows[0].keys()) == list(account.EXPORT_CSV_COLUMNS)
        assert z.read(f"photos/{k['tag']}-1.jpg") == b'JPEGBYTES'
        assert z.getinfo(f"photos/{k['tag']}-1.jpg").compress_type == zipfile.ZIP_STORED


def test_export_zip_lists_missing_photos(env, con, tmp_path):
    store = LocalFSStore(str(tmp_path / 'store'))
    u = _user(con)
    k = _knife_with_photo(con, store, u)
    store.delete(k['photos'][0]['store_key'])
    out = account.export_zip(con, store, u, str(tmp_path / 'exports'))
    with zipfile.ZipFile(out) as z:
        assert 'photos/' not in ' '.join(z.namelist())
        assert json.loads(z.read('knives.json'))['missing_photos'] == [f"{k['tag']}-1"]


def test_export_csv_columns_cover_private_and_skip_ids():
    cols = account.EXPORT_CSV_COLUMNS
    assert 'id' not in cols and 'owner_id' not in cols
    assert set(db.PRIVATE_COLUMNS) - {'events'} <= set(cols)
    assert cols[-1] == 'photo_count' and cols[0] == 'tag'


def test_export_csv_columns_track_the_knives_ddl(con):
    ddl = [r[1] for r in con.execute('PRAGMA table_info(knives)')]
    expected = tuple(c for c in ddl if c not in ('id', 'owner_id')) + ('photo_count',)
    assert account.EXPORT_CSV_COLUMNS == expected


def test_delete_account_is_complete(env, con, tmp_path):
    store = LocalFSStore(str(tmp_path / 'store'))
    u = _user(con)
    k = _knife_with_photo(con, store, u)
    thumb = db.thumb_key(k['photos'][0]['store_key'])
    store.put(thumb, b'THUMB')
    _public_surface(env, con, u)
    # the other side of every relation: a second user who wants, is intro'd to, and reports
    other = _user(con, 'o@example.com', 'other')
    ok = _knife_with_photo(con, store, other)
    w = db.create_want(con, other['id'], {'model': 'Sebenza', 'mode': 'either'})
    db.claim_intro(con, w['id'], k['id'], other['id'], u['id'])                 # other → sam
    db.create_want(con, u['id'], {'model': 'Inkosi', 'mode': 'either'})
    db.claim_intro(con, None, ok['id'], u['id'], other['id'], kind='board', message='hi')  # sam → other
    db.create_report(con, k['id'], u['id'], other['id'], 'spam')               # other reports sam's knife
    db.create_report(con, ok['id'], other['id'], u['id'], 'spam')              # sam reports other's knife

    counts = account.delete_account(con, store, u)
    assert counts == {'knives': 1, 'photos': 1, 'store_keys': 2, 'store_failed': 0, 'surface_removed': True}

    uid = u['id']
    assert con.execute('SELECT count(*) FROM users WHERE id = ?', (uid,)).fetchone()[0] == 0
    for table in ('knives', 'photos', 'events', 'wants', 'search_cards'):
        assert con.execute(f'SELECT count(*) FROM {table} WHERE owner_id = ?', (uid,)).fetchone()[0] == 0, table
    assert con.execute('SELECT count(*) FROM intros WHERE from_user = ? OR to_user = ?', (uid, uid)).fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM reports WHERE owner_id = ? OR reporter_id = ?', (uid, uid)).fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM search_fts WHERE rowid = ?', (uid * 1_000_000 + 1,)).fetchone()[0] == 0
    assert not store.exists(k['photos'][0]['store_key']) and not store.exists(thumb)
    _surface_gone(env, con, u)
    assert db.is_tombstoned(con, 'sam@example.com')
    # the other user keeps everything that was theirs
    assert db.get_user(con, other['id']) and db.get_knife(con, other['id'], ok['id'])
    assert store.exists(ok['photos'][0]['store_key'])
    assert len(db.list_wants(con, other['id'])) == 1


def test_delete_account_without_photos_or_bundle(env, con, tmp_path):
    store = LocalFSStore(str(tmp_path / 'store'))
    u = _user(con, 'bare@example.com', 'bare')
    assert account.delete_account(con, store, u) == {
        'knives': 0, 'photos': 0, 'store_keys': 0, 'store_failed': 0, 'surface_removed': True}
    assert db.get_user_by_email(con, 'bare@example.com') is None and db.is_tombstoned(con, 'bare@example.com')
