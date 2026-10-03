# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import hashlib
import json
import os
import threading
import time

import pytest

from bb import db, paths, publish, store as store_mod

from tests.test_publish import _jpeg_with_exif  # reuse the EXIF fixture


STRIP = ('href="/blade-book/search/">search<', 'href="/blade-book/board/">board<', 'href="/blade-book/how/">how<',
         'href="/blade-book/about/">about<', 'href="/blade-book/terms/">terms<', 'class="bb-auth" href="/blade-book/me/">sign in<')


def _assert_strip(html):
    assert html.count('class="bb-foot"') == 1
    foot = html[html.index('class="bb-foot"'):]
    pos = [foot.index(n) for n in STRIP]
    assert pos == sorted(pos)
    assert '<script src="/blade-book/nav.js?v=20260927" defer></script>' in foot
    assert html.count('<footer') == 1


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
    db.set_public(con, uid, [k['id']], True)   # born private; these fixtures are public
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
    assert 'bbmark' in idx  # the brandmark is inlined on the index
    assert '/blade-book/how/' in idx  # visitor hook: keep a register like this
    assert 'class="bb-auth" href="/blade-book/me/">sign in</a>' in idx  # owner's door back to /me
    assert 'class="signin"' in idx  # visible at the top, not just the footer
    assert 'knifes' not in idx  # 62 knives, not 62 knifes
    assert '/blade-book/search/' in idx
    assert '/blade-book/search/' in idx  # visitor hook: search all registers
    data = json.load(open(os.path.join(d, 'knives.json')))
    assert data['count'] == 1 and data['knives'][0]['tag'] == k['tag']
    page = open(os.path.join(d, k['tag'], 'index.html')).read()
    assert 'og:title' in page and 'og:image' in page
    assert 'bbmark' in page  # brandmark in the permalink footer
    assert '/blade-book/search/' in page  # visitor hook: search all registers
    assert f"/blade-book/@bundle-guy/img/{k['tag']}.jpg" in page
    assert not os.path.exists(os.path.join(d, 'keys.json'))
    assert 'noindex' not in page


def test_generated_pages_share_the_footer_strip(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    idx = open(os.path.join(d, 'index.html')).read()
    page = open(os.path.join(d, k['tag'], 'index.html')).read()
    _assert_strip(idx)
    _assert_strip(page)
    assert ('<p class="signin"><a href="/blade-book/search/">search</a><a href="/blade-book/board/">the board</a>'
            '<a class="bb-auth" href="/blade-book/me/">sign in</a></p>') in idx     # hero sign-in stays, with company
    assert 'Keep a register like this' in idx                     # the register's CTA stays
    assert 'Your knives deserve a page like this.' in page        # the knife page's own (2026-09-27)


def test_rebuild_drops_stale_pages(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    db.set_public(con, user['id'], [k['id']], False)
    assert publish.build_user(con, user, st) == 0
    d = publish.bundle_dir('bundle-guy')
    assert not os.path.exists(os.path.join(d, k['tag']))
    assert json.load(open(os.path.join(d, 'knives.json')))['count'] == 0


def test_build_user_hero_follows_featured_knife_id(con, tmp_path):
    """hero-pin: settings.featured_knife_id picks the hero + og:image over
    the first-with-a-photo default, resolved by tag inside build_user."""
    user, st, k1 = _setup(con, tmp_path)
    uid = user['id']
    k2 = db.create_draft_knife(con, uid)
    con.execute("UPDATE knives SET confidence = '{}', model = 'Mnandi' WHERE id = ?", (k2['id'],))
    con.commit()
    key2 = f"{uid}/{k2['id']}/1.jpg"
    st.put(key2, _jpeg_with_exif(800, 600))
    db.add_photo(con, uid, k2['id'], 1, key2, hashlib.sha256(b'y').hexdigest(), 800, 600)
    db.publish_knife(con, uid, k2['id'])
    db.set_public(con, uid, [k2['id']], True)   # born private; these fixtures are public
    db.set_user_settings(con, uid, {'featured_knife_id': k2['id']})
    user = db.get_user(con, uid)

    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    idx = open(os.path.join(d, 'index.html')).read()
    assert f'img/{k2["tag"]}.jpg' in idx.split('class="hero-bg"')[1][:60]
    feat = idx.split('class="feat"')[1][:200]
    assert f'href="{k2["tag"]}/"' in feat
    assert f'/@bundle-guy/img/{k2["tag"]}.jpg">' in idx.split('property="og:image"')[1][:120]


def test_unpublishing_the_pinned_hero_clears_the_pin_and_falls_back(con, tmp_path):
    """hero-pin review fix: taking the pinned knife private must clear
    featured_knife_id in the SAME write (db.set_public), not just leave the
    register to silently fall back while /me still thinks it's pinned."""
    user, st, k1 = _setup(con, tmp_path)
    uid = user['id']
    k2 = db.create_draft_knife(con, uid)
    con.execute("UPDATE knives SET confidence = '{}', model = 'Mnandi' WHERE id = ?", (k2['id'],))
    con.commit()
    key2 = f"{uid}/{k2['id']}/1.jpg"
    st.put(key2, _jpeg_with_exif(800, 600))
    db.add_photo(con, uid, k2['id'], 1, key2, hashlib.sha256(b'y').hexdigest(), 800, 600)
    db.publish_knife(con, uid, k2['id'])
    db.set_public(con, uid, [k2['id']], True)   # born private; these fixtures are public
    db.set_user_settings(con, uid, {'featured_knife_id': k2['id']})
    assert db.get_user(con, uid)['featured_knife_id'] == k2['id']

    db.set_public(con, uid, [k2['id']], False)                  # owner unticks "public on my page"
    assert db.get_user(con, uid)['featured_knife_id'] is None

    user = db.get_user(con, uid)
    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    idx = open(os.path.join(d, 'index.html')).read()
    assert f'img/{k1["tag"]}.jpg' in idx.split('class="hero-bg"')[1][:60]   # fell back to k1


def test_set_public_true_does_not_touch_the_pin(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    db.set_user_settings(con, user['id'], {'featured_knife_id': k['id']})
    db.set_public(con, user['id'], [k['id']], True)             # already public — no-op for the pin
    assert db.get_user(con, user['id'])['featured_knife_id'] == k['id']


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


def test_concurrent_builds_are_serialized(con, tmp_path, monkeypatch):
    """Two overlapping build_user() calls for the same handle (two gunicorn
    workers' debounce timers, or a timer racing the cron sweep) must never
    interleave — the flock in build_user (bb/publish.py) serializes them.
    Without it this is the reviewer's repro: a bundle can land with missing
    img/ files while both builders think they finished cleanly."""
    user, st, k = _setup(con, tmp_path)
    real_export = publish.export_hero

    def slow_export(store, kk, handle, img_dir, **kw):
        time.sleep(0.15)
        return real_export(store, kk, handle, img_dir, **kw)

    monkeypatch.setattr(publish, 'export_hero', slow_export)

    results = []

    def run():
        c = db.connect()
        try:
            u = db.get_user(c, user['id'])
            results.append(publish.build_user(c, u, st))
        finally:
            c.close()

    t1 = threading.Thread(target=run)
    t2 = threading.Thread(target=run)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results == [1, 1]
    d = publish.bundle_dir('bundle-guy')
    assert os.path.exists(os.path.join(d, 'index.html'))
    data = json.load(open(os.path.join(d, 'knives.json')))
    assert data['count'] == 1
    for row in data['knives']:
        for img_field in ('img', 'img_t'):
            if row.get(img_field):
                assert os.path.exists(os.path.join(d, 'img', row[img_field])), \
                    f'{img_field} referenced by knives.json missing on disk: {row}'
    assert os.path.exists(os.path.join(d, k['tag'], 'index.html'))


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


def test_build_user_reindexes_search(con, tmp_path):
    from bb import search
    user, st, k = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 1
    r = search.run_query(con, 'sebenza')
    assert r['count'] == 1 and r['knives'][0]['handle'] == 'bundle-guy'
    assert r['knives'][0]['img_t'] == f"{k['tag']}_t.jpg"
    db.set_public(con, user['id'], [k['id']], False)
    publish.build_user(con, db.get_user(con, user['id']), st)
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 0


def test_gated_user_never_in_search(con, tmp_path):
    user, st, _ = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    con.execute("UPDATE users SET public_key = 'k' WHERE id = ?", (user['id'],))
    con.commit()
    publish.build_user(con, db.get_user(con, user['id']), st)
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 0


def test_private_user_deindexed(con, tmp_path):
    user, st, _ = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    con.execute('UPDATE users SET profile_private = 1 WHERE id = ?', (user['id'],))
    con.commit()
    assert publish.build_user(con, db.get_user(con, user['id']), st) == -1
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 0


def test_build_user_bakes_the_plate_into_the_public_photo(con, tmp_path):
    from PIL import Image
    user, st, k = _setup(con, tmp_path)
    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    tag = db.get_knife(con, user['id'], k['id'])['tag']
    display = Image.open(os.path.join(d, 'img', f'{tag}.jpg')).convert('RGB')
    w, h = display.size
    assert (w, h > 600) == (800, True)
    assert all(abs(a - b) <= 8 for a, b in zip(display.getpixel((3, h - 3)), (250, 246, 238)))


def test_build_user_wires_each_knife_page_to_its_neighbours(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    k2 = db.create_draft_knife(con, user['id'])
    con.execute("UPDATE knives SET confidence = '{}', model = 'Mnandi' WHERE id = ?", (k2['id'],))
    con.commit()
    key2 = f"{user['id']}/{k2['id']}/1.jpg"
    st.put(key2, _jpeg_with_exif(800, 600))
    db.add_photo(con, user['id'], k2['id'], 1, key2, hashlib.sha256(b'y').hexdigest(), 800, 600)
    db.publish_knife(con, user['id'], k2['id'])
    db.set_public(con, user['id'], [k2['id']], True)
    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    t1 = db.get_knife(con, user['id'], k['id'])['tag']
    t2 = db.get_knife(con, user['id'], k2['id'])['tag']
    p1 = open(os.path.join(d, t1, 'index.html')).read()
    p2 = open(os.path.join(d, t2, 'index.html')).read()
    assert f'rel="next" href="../{t2}/"' in p1 and 'rel="prev"' not in p1
    assert f'rel="prev" href="../{t1}/"' in p2 and 'rel="next"' not in p2
    assert '· 2 knives' in p1
