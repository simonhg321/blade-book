# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Passkey routes: registration under /api/settings/passkeys (Task 4) and
sign-in under /api/auth/passkey (Task 5). Driven by tests.softauth."""
import json

from bb import db, passkeys
from tests.conftest import signed_in
from tests.softauth import SoftAuthenticator, b64u_dec

S = '/blade-book/api/settings/passkeys'
A = '/blade-book/api/auth'
RP, ORIGIN = 'localhost', 'http://localhost'


def _opts(r):
    assert r.status_code == 200, r.data
    return json.dumps(r.get_json()['options'])


def register_passkey(client, auth, name=None):
    """Signed-in client registers `auth`; returns the finish JSON."""
    opts = _opts(client.post(S + '/begin'))
    body = {'credential': auth.register(opts)}
    if name:
        body['name'] = name
    r = client.post(S + '/finish', json=body, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15'})
    assert r.status_code == 200, r.data
    return r.get_json()


# --- registration -------------------------------------------------------------------

def test_register_list_remove(client, mailer):
    me = signed_in(client, mailer)
    assert client.get(S).get_json() == {'available': True, 'passkeys': []}
    auth = SoftAuthenticator(RP, ORIGIN)
    j = register_passkey(client, auth)
    assert j['available'] is True and len(j['passkeys']) == 1
    pk = j['passkeys'][0]
    assert pk['name'] == 'Safari on Mac' and pk['last_used'] is None and pk['created']
    assert set(pk) == {'id', 'name', 'created', 'last_used', 'transports'}
    assert auth.user_handle == str(me['id']).encode()            # the user handle is our user id
    r = client.delete(f"{S}/{pk['id']}")
    assert r.status_code == 200 and r.get_json()['passkeys'] == []


def test_begin_sets_a_short_browser_bound_cookie_and_excludes_existing(client, mailer):
    signed_in(client, mailer)
    auth = SoftAuthenticator(RP, ORIGIN)
    register_passkey(client, auth)
    r = client.post(S + '/begin')
    c = client.get_cookie('bb_passkey', path='/blade-book/api')
    assert c is not None and c.http_only and c.path == '/blade-book/api' and c.max_age == 300
    o = r.get_json()['options']
    assert [b64u_dec(x['id']) for x in o['excludeCredentials']] == [auth.credential_id]
    assert o['authenticatorSelection']['userVerification'] == 'required'


def test_finish_needs_the_cookie_and_the_cookie_is_single_use(client, mailer):
    """Review focus 1."""
    signed_in(client, mailer)
    auth = SoftAuthenticator(RP, ORIGIN)
    opts = _opts(client.post(S + '/begin'))
    cred = auth.register(opts)
    client.delete_cookie('bb_passkey', path='/blade-book/api')
    r = client.post(S + '/finish', json={'credential': cred})
    assert r.status_code == 400 and 'start again' in r.get_json()['error']
    # a fresh begin, finished once, cannot be finished twice
    opts = _opts(client.post(S + '/begin'))
    cred = auth.register(opts)
    assert client.post(S + '/finish', json={'credential': cred}).status_code == 200
    assert client.post(S + '/finish', json={'credential': cred}).status_code == 400
    assert len(client.get(S).get_json()['passkeys']) == 1


def test_finish_refuses_bad_credentials_with_the_reason(client, mailer):
    signed_in(client, mailer)
    opts = _opts(client.post(S + '/begin'))
    r = client.post(S + '/finish', json={'credential': SoftAuthenticator(RP, 'https://evil.example').register(opts)})
    assert r.status_code == 400 and 'origin' in r.get_json()['error'].lower()
    client.post(S + '/begin')
    assert client.post(S + '/finish', json={'credential': 'nope'}).status_code == 400
    assert client.post(S + '/finish', json={}).status_code == 400
    assert client.post(S + '/finish', json=[1]).status_code == 400


def test_duplicate_credential_and_name_override(client, mailer):
    signed_in(client, mailer)
    auth = SoftAuthenticator(RP, ORIGIN)
    j = register_passkey(client, auth, name='  my yubikey  ')
    assert j['passkeys'][0]['name'] == 'my yubikey'
    opts = _opts(client.post(S + '/begin'))
    r = client.post(S + '/finish', json={'credential': auth.register(opts)})
    assert r.status_code == 400 and r.get_json()['error'] == 'that passkey is already registered'
    opts = _opts(client.post(S + '/begin'))
    r = client.post(S + '/finish', json={'credential': SoftAuthenticator(RP, ORIGIN).register(opts), 'name': 'x' * 41})
    assert r.status_code == 400


def test_cap_of_ten(client, mailer, con):
    me = signed_in(client, mailer)
    for i in range(10):
        db.add_passkey(con, me['id'], b'c%d' % i, b'p', 0, None, 'n')
    r = client.post(S + '/begin')
    assert r.status_code == 400 and r.get_json()['error'] == 'ten passkeys is the limit — remove one first'
    assert client.get_cookie('bb_passkey', path='/blade-book/api') is None


def test_remove_only_your_own(client, mailer, con):
    signed_in(client, mailer)
    other = db.create_user(con, 'bo@example.com', 'bo')
    pid = db.add_passkey(con, other, b'c', b'p', 0, None, 'n')
    assert client.delete(f'{S}/{pid}').status_code == 404
    assert db.count_passkeys(con, other) == 1


def test_registration_requires_auth(client):
    assert client.post(S + '/begin').status_code == 401
    assert client.post(S + '/finish', json={}).status_code == 401
    assert client.get(S).status_code == 401
    assert client.delete(S + '/1').status_code == 401


def test_registration_503_without_the_library(client, mailer, monkeypatch):
    """Review focus 3."""
    signed_in(client, mailer)
    monkeypatch.setattr(passkeys, 'available', lambda: False)
    r = client.post(S + '/begin')
    assert r.status_code == 503 and r.get_json() == {'error': 'passkeys are not set up here'}
    assert client.post(S + '/finish', json={'credential': {}}).status_code == 503
    assert client.get(S).get_json() == {'available': False, 'passkeys': []}
