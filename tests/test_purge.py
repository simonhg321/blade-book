# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import importlib.util
import os
from datetime import datetime, timedelta, timezone

from bb import db, store


def _load_script():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     'scripts', 'purge_drafts.py')
    spec = importlib.util.spec_from_file_location('purge_drafts', p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_purge_script_deletes_rows_and_files(env):
    con = db.connect()
    uid = db.create_user(con, 'sam@example.com', 'sam')
    old = db.create_draft_knife(con, uid)
    s = store.from_paths()
    key = f"{uid}/{old['id']}/1.jpg"
    s.put(key, b'orig'); s.put(db.thumb_key(key), b'thumb')
    db.add_photo(con, uid, old['id'], 1, key, 'ab' * 32, 1, 1)
    fresh = db.create_draft_knife(con, uid)
    stale = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    con.execute('UPDATE knives SET updated=? WHERE id=?', (stale, old['id'])); con.commit()
    con.close()
    assert _load_script().run(days=7) == 1
    assert not s.exists(key) and not s.exists(db.thumb_key(key))
    con = db.connect()
    assert db.get_knife(con, uid, old['id']) is None
    assert db.get_knife(con, uid, fresh['id']) is not None
    assert _load_script().run(days=7) == 0


def test_purge_is_tolerant_of_a_failing_delete(env, monkeypatch):
    con = db.connect()
    uid = db.create_user(con, 'sam@example.com', 'sam')
    a = db.create_draft_knife(con, uid)
    b = db.create_draft_knife(con, uid)
    s = store.from_paths()
    key_a = f"{uid}/{a['id']}/1.jpg"
    key_b = f"{uid}/{b['id']}/1.jpg"
    s.put(key_a, b'orig-a'); s.put(db.thumb_key(key_a), b'thumb-a')
    s.put(key_b, b'orig-b'); s.put(db.thumb_key(key_b), b'thumb-b')
    db.add_photo(con, uid, a['id'], 1, key_a, 'aa' * 32, 1, 1)
    db.add_photo(con, uid, b['id'], 1, key_b, 'bb' * 32, 1, 1)
    stale = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    con.execute('UPDATE knives SET updated=? WHERE id IN (?, ?)', (stale, a['id'], b['id']))
    con.commit()
    con.close()

    m = _load_script()
    orig_delete = store.LocalFSStore.delete
    calls = []

    def flaky(self, key):
        calls.append(key)
        if key == key_a:
            raise PermissionError('nope')
        return orig_delete(self, key)

    monkeypatch.setattr(store.LocalFSStore, 'delete', flaky)
    assert m.run(days=7) == 2  # both draft rows still get purged
    assert m.last_failed == 1  # only key_a's delete failed
    con = db.connect()
    assert db.get_knife(con, uid, a['id']) is None
    assert db.get_knife(con, uid, b['id']) is None
    con.close()
    assert not s.exists(key_b) and not s.exists(db.thumb_key(key_b))


def test_purge_removes_now_empty_knife_dir(env):
    con = db.connect()
    uid = db.create_user(con, 'sam@example.com', 'sam')
    k = db.create_draft_knife(con, uid)
    s = store.from_paths()
    key = f"{uid}/{k['id']}/1.jpg"
    s.put(key, b'orig')
    db.add_photo(con, uid, k['id'], 1, key, 'cc' * 32, 1, 1)
    stale = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
    con.execute('UPDATE knives SET updated=? WHERE id=?', (stale, k['id'])); con.commit()
    con.close()
    knife_dir = os.path.join(s.root, str(uid), str(k['id']))
    assert os.path.isdir(knife_dir)
    assert _load_script().run(days=7) == 1
    assert not os.path.isdir(knife_dir)


def test_purge_sweeps_only_stale_part_files(env):
    s = store.from_paths()
    old_part = os.path.join(s.root, '1', '2', '3.jpg.999.deadbeef.part')
    fresh_part = os.path.join(s.root, '1', '2', '4.jpg.999.deadbeef.part')
    os.makedirs(os.path.dirname(old_part), exist_ok=True)
    for p in (old_part, fresh_part):
        with open(p, 'wb') as f:
            f.write(b'partial')
    old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
    os.utime(old_part, (old_time, old_time))
    con = db.connect()
    db.create_user(con, 'sam@example.com', 'sam')  # so run() has a DB to touch
    con.close()
    assert _load_script().run(days=7) == 0
    assert not os.path.exists(old_part)
    assert os.path.exists(fresh_part)
