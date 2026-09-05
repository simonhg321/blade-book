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
