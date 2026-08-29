# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import hashlib
import json
import os

import pytest

from bb import db, paths, publish, store as store_mod

from tests.test_publish import _jpeg_with_exif  # reuse the EXIF fixture


def _setup(con, tmp_path, **user_over):
    uid = db.create_user(con, 'bundle@example.com', 'bundle-guy')
    if user_over:
        cols = ', '.join(f'{c} = ?' for c in user_over)
        con.execute(f'UPDATE users SET {cols} WHERE id = ?', (*user_over.values(), uid))
        con.commit()
    st = store_mod.LocalFSStore(str(tmp_path / 'photos'))
    k = db.create_draft_knife(con, uid)
    con.execute("UPDATE knives SET confidence = '{}', model = 'Sebenza', "
                "blade_steel = 'S35VN', born_on = '2019-06-05', born_on_precision = 'day', "
                "ext = ? WHERE id = ?",
                (json.dumps({'generation': '31', 'size': 'Large'}), k['id']))
    con.commit()
    key = f"{uid}/{k['id']}/1.jpg"
    st.put(key, _jpeg_with_exif(800, 600))
    db.add_photo(con, uid, k['id'], 1, key, hashlib.sha256(b'x').hexdigest(), 800, 600)
    db.publish_knife(con, uid, k['id'])
    return db.get_user(con, uid), st, k


def test_build_user_writes_bundle(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    n = publish.build_user(con, user, st)
    assert n == 1
    d = publish.bundle_dir('bundle-guy')
    assert d.startswith(paths.WWW_DIR)
    idx = open(os.path.join(d, 'index.html')).read()
    assert 'Large Sebenza 31' in idx and '@bundle-guy' in idx
    assert 'img/' in idx   # hero via img/
    data = json.load(open(os.path.join(d, 'knives.json')))
    assert data['count'] == 1 and data['knives'][0]['tag'] == k['tag']
    page = open(os.path.join(d, k['tag'], 'index.html')).read()
    assert 'og:title' in page and 'og:image' in page
    assert f"/blade-book/@bundle-guy/img/{k['tag']}.jpg" in page
    assert not os.path.exists(os.path.join(d, 'keys.json'))
    assert 'noindex' not in page


def test_rebuild_drops_stale_pages(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    db.set_public(con, user['id'], [k['id']], False)
    assert publish.build_user(con, user, st) == 0
    d = publish.bundle_dir('bundle-guy')
    assert not os.path.exists(os.path.join(d, k['tag']))
    assert json.load(open(os.path.join(d, 'knives.json')))['count'] == 0


def test_key_gate_publishes_hashes_only(con, tmp_path):
    user, st, k = _setup(con, tmp_path, public_key='Ozzy Rules')
    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    keys = json.load(open(os.path.join(d, 'keys.json')))
    assert keys['hashes'] == [hashlib.sha256(b'ozzy rules').hexdigest()]
    idx = open(os.path.join(d, 'index.html')).read()
    assert 'keys.json' in idx and 'Ozzy' not in idx
    page = open(os.path.join(d, k['tag'], 'index.html')).read()
    assert 'noindex' in page
    # gated: chat-preview unfurls must not leak the model/edition via <head>
    head = page.split('</head>', 1)[0]
    assert 'Sebenza' not in head


def test_build_failure_leaves_no_stranded_tmp(con, tmp_path, monkeypatch):
    user, st, _ = _setup(con, tmp_path)

    def boom(*a, **kw):
        raise RuntimeError('simulated build failure')

    monkeypatch.setattr(publish, 'export_hero', boom)
    with pytest.raises(RuntimeError):
        publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    assert not os.path.exists(d)
    assert not os.path.exists(d + '.tmp')


def test_profile_private_removes_bundle(con, tmp_path):
    user, st, _ = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    assert os.path.isdir(publish.bundle_dir('bundle-guy'))
    d = publish.bundle_dir('bundle-guy')
    tmp = d + '.tmp'                        # a stranded tmp from a prior crashed build
    os.makedirs(tmp, exist_ok=True)
    with open(os.path.join(tmp, 'index.html'), 'w') as f:
        f.write('stale partial build')
    con.execute('UPDATE users SET profile_private = 1 WHERE id = ?', (user['id'],))
    con.commit()
    assert publish.build_user(con, db.get_user(con, user['id']), st) == -1
    assert not os.path.exists(publish.bundle_dir('bundle-guy'))
    assert not os.path.exists(tmp)


def test_bad_handle_refused(con, tmp_path):
    user, st, _ = _setup(con, tmp_path)
    user = dict(user, handle='../../evil')
    try:
        publish.build_user(con, user, st)
        raise AssertionError('must refuse a path-unsafe handle')
    except ValueError:
        pass
