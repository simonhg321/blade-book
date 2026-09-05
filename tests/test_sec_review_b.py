# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Regression tests for security review 2026-09-04, batch B
(docs/SECURITY-REVIEW-2026-09-04.md: M2, M7, M8, M9, L1, L3, L10). Each test
is the reviewer's repro, inverted: it now asserts the safe behaviour."""
import io
import os

from PIL import Image

from bb import publish
from tests.conftest import magic_link_from, signed_in

A = '/blade-book/api/auth'
K = '/blade-book/api/knives'


# --- L10: authed API JSON is never cached -------------------------------------

def test_api_json_is_no_store(client, mailer):
    signed_in(client, mailer)
    r = client.get(A + '/me')
    assert r.status_code == 200
    assert r.headers.get('Cache-Control') == 'no-store'
    # anonymous JSON too, and the healthz probe
    assert client.get('/blade-book/api/healthz').headers.get('Cache-Control') == 'no-store'
    assert client.get(A + '/providers').headers.get('Cache-Control') == 'no-store'


# --- M9: the JPEG comment does not survive the "all metadata dropped" re-encode --

class _MemStore:
    def __init__(self):
        self.d = {}

    def put(self, key, data):
        self.d[key] = data

    def get(self, key):
        return self.d[key]


def _jpeg_with_comment(comment=b'shot at 47.6N 117.4W by Simon', size=(300, 200)):
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 120, 40)).save(buf, 'JPEG', comment=comment)
    return buf.getvalue()


def _knife(store, data, tag='K01'):
    store.put('1/1/1.jpg', data)
    return {'tag': tag, 'hero_photo': 1, 'photos': [{'seq': 1, 'store_key': '1/1/1.jpg'}]}


def test_jpeg_comment_is_stripped_at_publish(tmp_path):
    store = _MemStore()
    data = _jpeg_with_comment()
    assert Image.open(io.BytesIO(data)).info.get('comment')            # the source really carries it
    hero, thumb = publish.export_hero(store, _knife(store, data), 'sam', str(tmp_path))
    for name in (hero, thumb):
        out = Image.open(os.path.join(tmp_path, name))
        assert 'comment' not in out.info, name
        assert b'Simon' not in open(os.path.join(tmp_path, name), 'rb').read()


import pytest

from bb import photos
from tests.test_photos_api import _up


def _ppm(w, h):
    """A binary PPM: Pillow decodes it fully, no draft mode, no size check
    until the pixels are in memory — the reviewer's bypass file."""
    return b'P6\n%d %d\n255\n' % (w, h) + b'\x00' * (w * h * 3)


# --- M7: an undecodable file is refused at ingest, and publish keeps the cap ----

def test_ingest_refuses_what_it_cannot_decode():
    with pytest.raises(photos.Undecodable):
        photos.ingest(b'not really an image', 'shot.heic')
    with pytest.raises(photos.Undecodable):
        photos.ingest(_ppm(4, 4), 'renamed.jpg')                        # PPM is not a vetted decoder


def test_upload_of_undecodable_file_is_415(client, mailer):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = _up(client, kid, 1, _ppm(4, 4), name='IMG_1.jpg')
    assert r.status_code == 415
    assert 'could not read' in r.get_json()['error']
    assert client.get(f'{K}/{kid}/photos/1/original').status_code == 404   # nothing stored


def test_export_hero_refuses_oversize_via_guarded_open(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, 'MAX_PIXELS_NON_JPEG', 100)              # 10x10 PNG will be "too big"
    store = _MemStore()
    buf = io.BytesIO()
    Image.new('RGB', (20, 20)).save(buf, 'PNG')
    store.put('1/1/1.png', buf.getvalue())
    k = {'tag': 'K02', 'hero_photo': 1, 'photos': [{'seq': 1, 'store_key': '1/1/1.png'}]}
    assert publish.export_hero(store, k, 'sam', str(tmp_path)) == (None, None)
    assert not os.path.exists(os.path.join(tmp_path, 'K02.jpg'))


def test_export_hero_never_opens_unvetted_formats(tmp_path):
    store = _MemStore()
    store.put('1/1/1.jpg', _ppm(4, 4))                                    # a PPM wearing a .jpg key
    k = {'tag': 'K03', 'hero_photo': 1, 'photos': [{'seq': 1, 'store_key': '1/1/1.jpg'}]}
    assert publish.export_hero(store, k, 'sam', str(tmp_path)) == (None, None)


from datetime import datetime, timedelta, timezone

from bb import account, auth, db


# --- M8: a released handle cannot be claimed by a stranger for 90 days ----------

def _user(con, email='sam@example.com', handle='sam'):
    uid = db.create_user(con, email, handle)
    db.rotate_session_secret(con, uid)
    return db.get_user(con, uid)


def test_renamed_handle_is_tombstoned(con):
    u = _user(con)
    account.change_handle(con, u, 'samuel-k')
    assert db.handle_exists(con, 'sam')                                    # still "taken"
    assert auth.unique_handle(con, 'sam') == 'sam-2'                       # sign-ups skip it
    other = _user(con, 'o@example.com', 'other')
    with pytest.raises(ValueError, match='taken'):
        account.validate_new_handle(con, other, 'sam')


def test_deleted_handle_is_tombstoned(env, con):
    from bb import store as store_mod
    u = _user(con)
    account.delete_account(con, store_mod.from_paths(), u)
    assert db.handle_exists(con, 'sam')
    assert auth.handle_for_email(con, 'sam@other.example') == 'sam-2'


def test_tombstone_expires_after_90_days(con):
    db.release_handle(con, 'ghost')
    assert db.handle_exists(con, 'ghost')
    old = (datetime.now(timezone.utc) - timedelta(days=db.HANDLE_TOMBSTONE_DAYS + 1)).isoformat()
    con.execute('UPDATE released_handles SET released_at = ? WHERE handle = ?', (old, 'ghost'))
    con.commit()
    assert not db.handle_exists(con, 'ghost')
    db.purge_auth_tables(con)
    assert con.execute('SELECT count(*) FROM released_handles').fetchone()[0] == 0


def test_schema_is_v11(con):
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == 11
    assert db.SCHEMA_VERSION == 11


# --- L1: sign-out revokes THIS cookie; sign-out-everywhere still revokes all ----

def _cookie(client):
    c = client.get_cookie('bb_session', path='/blade-book')
    assert c is not None
    return c.value


def test_signout_invalidates_a_captured_cookie(client, mailer, app):
    signed_in(client, mailer)
    stolen = _cookie(client)
    assert client.post(A + '/signout').get_json() == {'ok': True}
    thief = app.test_client()
    thief.set_cookie('bb_session', stolen, path='/blade-book')
    assert thief.get(A + '/me').status_code == 401                       # the row is gone


def test_signout_leaves_other_devices_signed_in(client, mailer, app):
    signed_in(client, mailer)
    phone = app.test_client()
    signed_in(phone, mailer)                                              # second session, same user
    assert client.post(A + '/signout').status_code == 200
    assert phone.get(A + '/me').status_code == 200


def test_signout_all_still_revokes_every_device(client, mailer, app):
    signed_in(client, mailer)
    phone = app.test_client()
    signed_in(phone, mailer)
    assert client.post(A + '/signout-all').status_code == 200
    assert phone.get(A + '/me').status_code == 401
    assert client.get(A + '/me').status_code == 401


def test_cookie_without_session_row_is_not_signed_in(client, mailer, con):
    signed_in(client, mailer)
    con.execute('DELETE FROM sessions')
    con.commit()
    assert client.get(A + '/me').status_code == 401


from tests.test_auth_oidc import _fake_exchange, _start, both  # noqa: F401 — fixture re-export


# --- M2a: an OIDC callback minted elsewhere does not sign this browser in ------

def test_oidc_callback_requires_the_flow_cookie(client, both, monkeypatch, app):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-9', 'email': 'attacker@example.com',
                                            'email_verified': True}})
    q = _start(client, 'google')                                          # attacker's browser starts
    victim = app.test_client()                                            # victim never visited /auth/google
    r = victim.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.status_code == 302 and r.headers['Location'].endswith('?auth=failed')
    assert victim.get(A + '/me').status_code == 401
    # the state was burned: replaying it in the attacker's own browser fails too
    r = client.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.headers['Location'].endswith('?auth=failed')


def test_oidc_flow_cookie_is_scoped_and_short_lived(client, both):
    r = client.get('/blade-book/api/auth/google')
    assert r.status_code == 302
    c = client.get_cookie('bb_oidc', path='/blade-book/api/auth')
    assert c is not None and c.http_only and c.path == '/blade-book/api/auth'
    assert c.max_age == db.STATE_TTL_MIN * 60


def test_oidc_same_browser_still_signs_in(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-1', 'email': 'sam@example.com',
                                            'email_verified': True}})
    q = _start(client, 'google')
    r = client.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.headers['Location'].endswith('/blade-book/')
    assert client.get(A + '/me').status_code == 200
    assert client.get_cookie('bb_oidc', path='/blade-book/api/auth') is None   # cleared on callback


import re


def _token(link):
    return re.search(r'[?&]t=([^&\s]+)', link).group(1)


# --- M2b: a magic link clicked outside the requesting browser must be confirmed --

def test_same_browser_magic_link_signs_in_silently(client, mailer):
    client.post(A + '/magic', json={'email': 'sam@example.com'})
    c = client.get_cookie('bb_magic', path='/blade-book/api/auth')
    assert c is not None and c.http_only and c.max_age == db.MAGIC_TTL_MIN * 60
    r = client.get(magic_link_from(mailer))
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert client.get(A + '/me').status_code == 200
    assert client.get_cookie('bb_magic', path='/blade-book/api/auth') is None


def test_foreign_browser_gets_confirm_page_not_a_session(client, mailer, app):
    client.post(A + '/magic', json={'email': 'attacker@example.com'})
    link = magic_link_from(mailer)
    victim = app.test_client()
    r = victim.get(link)
    assert r.status_code == 200 and r.mimetype == 'text/html'
    body = r.get_data(as_text=True)
    assert 'a***@example.com' in body and 'attacker@example.com' not in body
    assert 'action="/blade-book/api/auth/magic/confirm"' in body and 'method="post"' in body
    assert victim.get(A + '/me').status_code == 401                       # nothing happened yet
    # the token is still live: the confirm consumes it
    r = victim.post(A + '/magic/confirm', data={'t': _token(link)},
                    headers={'Origin': 'http://localhost'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert victim.get(A + '/me').get_json()['email'] == 'attacker@example.com'
    # and only once
    r = victim.post(A + '/magic/confirm', data={'t': _token(link)}, headers={'Origin': 'http://localhost'})
    assert r.headers['Location'].endswith('?auth=expired')


def test_confirm_page_names_the_signed_in_account(client, mailer, app):
    signed_in(client, mailer, 'victim@example.com')                       # @victim is signed in here
    other = app.test_client()
    other.post(A + '/magic', json={'email': 'attacker@example.com'})
    r = client.get(magic_link_from(mailer))
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '@victim' in body and 'switch' in body.lower()
    assert client.get(A + '/me').get_json()['email'] == 'victim@example.com'   # still the victim


def test_confirm_from_foreign_origin_is_refused(client, mailer, app):
    client.post(A + '/magic', json={'email': 'attacker@example.com'})
    link = magic_link_from(mailer)
    victim = app.test_client()
    r = victim.post(A + '/magic/confirm', data={'t': _token(link)}, headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403
    assert victim.get(A + '/me').status_code == 401


def test_dead_token_still_says_expired(client, mailer):
    assert client.get(A + '/magic?t=nope').headers['Location'].endswith('?auth=expired')
    assert client.post(A + '/magic/confirm', data={'t': 'nope'}, headers={'Origin': 'http://localhost'}) \
        .headers['Location'].endswith('?auth=expired')


# --- M2c: under https the flow cookies carry the __Host- prefix ----------------
# The app also answers on the alias vhost billboard.instockornot.club, so any
# sibling on *.instockornot.club could set bb_magic/bb_oidc with
# Domain=.instockornot.club and force the silent "bound" path with a link the
# attacker minted. A browser refuses a __Host- cookie that carries a Domain,
# which closes that door.

HTTPS_BASE = 'https://blade-book.com'


def _https_client(mailer, monkeypatch):
    monkeypatch.setenv('SESSION_KEY', 'k')
    monkeypatch.setenv('BASE_URL', HTTPS_BASE)
    from app import create_app
    return create_app(mailer=mailer).test_client()


def _set_cookie(resp, prefix):
    return next(sc for sc in resp.headers.get_all('Set-Cookie') if sc.startswith(prefix))


def _attrs(set_cookie):
    """The attribute list of a Set-Cookie header, minus the name=value pair."""
    return [p.strip() for p in set_cookie.split(';')[1:]]


def _assert_host_prefixed(set_cookie):
    attrs = _attrs(set_cookie)
    assert 'Secure' in attrs, set_cookie
    assert 'HttpOnly' in attrs, set_cookie
    assert 'Path=/' in attrs, set_cookie
    assert 'SameSite=None' in attrs, set_cookie
    assert not any(a.lower().startswith('domain=') for a in attrs), set_cookie


def test_magic_flow_cookie_is_host_prefixed_under_https(mailer, monkeypatch):
    c = _https_client(mailer, monkeypatch)
    r = c.post(A + '/magic', json={'email': 'sam@example.com'}, base_url=HTTPS_BASE)
    assert r.status_code == 202
    _assert_host_prefixed(_set_cookie(r, '__Host-bb_magic='))
    assert not any(sc.startswith('bb_magic=') for sc in r.headers.get_all('Set-Cookie'))
    # the bound (silent) path still works on the browser that asked
    link = magic_link_from(mailer)
    r = c.get(link.replace(HTTPS_BASE, ''), base_url=HTTPS_BASE)
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert c.get(A + '/me', base_url=HTTPS_BASE).status_code == 200


def test_oidc_flow_cookie_is_host_prefixed_under_https(mailer, monkeypatch, both):
    c = _https_client(mailer, monkeypatch)
    r = c.get(A + '/google', base_url=HTTPS_BASE)
    assert r.status_code == 302
    _assert_host_prefixed(_set_cookie(r, '__Host-bb_oidc='))
    assert not any(sc.startswith('bb_oidc=') for sc in r.headers.get_all('Set-Cookie'))


def test_duplicate_flow_cookies_are_never_bound(client, mailer, app):
    """Two bb_magic cookies = something is shadowing the real one (a sibling
    host planting a Domain cookie). Treat the flow as unbound: confirm page."""
    client.post(A + '/magic', json={'email': 'sam@example.com'})
    flow = client.get_cookie('bb_magic', path='/blade-book/api/auth').value
    link = magic_link_from(mailer)
    # use_cookies=False: an empty test-client jar would strip our Cookie header
    other = app.test_client(use_cookies=False)
    r = other.get(link, headers={'Cookie': f'bb_magic={flow}; bb_magic=planted'})
    assert r.status_code == 200 and r.mimetype == 'text/html'
    assert 'confirm sign-in' in r.get_data(as_text=True)
    assert not any(sc.startswith('bb_session=') for sc in r.headers.get_all('Set-Cookie'))
    assert r.headers.get('X-Frame-Options') == 'DENY'


def test_non_ascii_state_is_refused_not_a_500(client, both):
    """The constant-time compare must not blow up on a state the attacker
    picked: compare_digest raises TypeError on non-ASCII str."""
    _start(client, 'google')                                              # this browser has bb_oidc
    r = client.get('/blade-book/api/auth/google/callback?code=c0de&state=%C3%A9vil')
    assert r.status_code == 302 and r.headers['Location'].endswith('?auth=failed')
    assert client.get(A + '/me').status_code == 401


from bb import cdn


# --- L3: removed public files are purged from the edge -----------------------------

def test_remove_public_surface_purges_every_file(env, monkeypatch):
    dest = publish.bundle_dir('sam')
    os.makedirs(os.path.join(dest, 'img'))
    open(os.path.join(dest, 'index.html'), 'w').close()
    open(os.path.join(dest, 'img', 'K01.jpg'), 'w').close()
    purged = []
    monkeypatch.setattr(cdn, 'purge_later', lambda urls: purged.extend(urls) or len(urls))
    assert account.remove_public_surface('sam') is True
    assert sorted(purged) == [cdn.public_url('sam', 'img/K01.jpg'), cdn.public_url('sam', 'index.html')]


def test_rebuild_purges_files_that_vanished(env, con, monkeypatch):
    from bb import store as store_mod
    from tests.test_account import _knife_with_photo
    u = _user(con)
    store = store_mod.from_paths()
    k = _knife_with_photo(con, store, u, tag_photo=_jpeg_with_comment(b'', (40, 30)))
    publish.build_user(con, u, store)
    hero = cdn.public_url('sam', f"img/{k['tag']}.jpg")
    purged = []
    monkeypatch.setattr(cdn, 'purge_later', lambda urls: purged.extend(urls) or len(urls))
    db.set_public(con, u['id'], [k['id']], False)                          # bb/db.py:639
    publish.build_user(con, db.get_user(con, u['id']), store)
    assert hero in purged and cdn.public_url('sam', f"{k['tag']}/index.html") in purged
    assert cdn.public_url('sam', 'index.html') not in purged                # still there, rewritten
