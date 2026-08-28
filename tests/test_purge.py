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
