# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from urllib.parse import parse_qs, urlparse

import pytest

from bb import db, oidc
from tests.conftest import magic_link_from


@pytest.fixture
def both(monkeypatch):
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'gid')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'gs')
    monkeypatch.setenv('APPLE_CLIENT_ID', 'com.blade-book.web')
    monkeypatch.setenv('APPLE_TEAM_ID', 'T')
    monkeypatch.setenv('APPLE_KEY_ID', 'K')
    monkeypatch.setenv('APPLE_PRIVATE_KEY', 'not-a-real-key')


def _fake_exchange(monkeypatch, claims_by_provider, seen=None):
    def fake(provider, code, nonce):
        if seen is not None:
            seen.append((provider.name, code, nonce))
        c = claims_by_provider[provider.name]
        if isinstance(c, Exception):
            raise c
        return c
    monkeypatch.setattr(oidc, 'exchange_code', fake)


def _start(client, provider):
    r = client.get(f'/blade-book/api/auth/{provider}')
    assert r.status_code == 302
    return parse_qs(urlparse(r.headers['Location']).query)


def test_providers_endpoint_lists_configured(client, both):
    assert sorted(client.get('/blade-book/api/auth/providers').get_json()['providers']) == ['apple', 'google']


def test_providers_empty_when_unconfigured(client, monkeypatch):
    for v in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'APPLE_CLIENT_ID'):
        monkeypatch.delenv(v, raising=False)
    assert client.get('/blade-book/api/auth/providers').get_json() == {'providers': []}
    assert client.get('/blade-book/api/auth/google').status_code == 404
    assert client.get('/blade-book/api/auth/nope').status_code == 404


def test_start_redirects_and_stores_state(client, both):
    q = _start(client, 'google')
    con = db.connect()
    row = con.execute('SELECT provider, nonce FROM oauth_states WHERE state = ?',
                      (q['state'][0],)).fetchone()
    assert dict(row) == {'provider': 'google', 'nonce': q['nonce'][0]}


def test_google_callback_creates_user_and_signs_in(client, both, monkeypatch):
    seen = []
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-1', 'email': 'sam@example.com',
                                            'email_verified': True}}, seen)
    q = _start(client, 'google')
    r = client.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert seen == [('google', 'c0de', q['nonce'][0])]
    me = client.get('/blade-book/api/auth/me').get_json()
    assert me['email'] == 'sam@example.com' and me['handle'] == 'sam' and me['verified_at']
    con = db.connect()
    assert db.get_user(con, me['id'])['auth_subjects'] == {'google': 'g-1'}


def test_apple_callback_is_form_post(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'apple': {'sub': 'a-1', 'email': 'sam@example.com',
                                           'email_verified': True}})
    q = _start(client, 'apple')
    r = client.post('/blade-book/api/auth/apple/callback',
                    data={'code': 'c', 'state': q['state'][0]})
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert client.get('/blade-book/api/auth/me').status_code == 200


def test_same_email_across_methods_is_one_user(client, mailer, both, monkeypatch):
    _fake_exchange(monkeypatch, {
        'google': {'sub': 'g-1', 'email': 'sam@example.com', 'email_verified': True},
        'apple': {'sub': 'a-1', 'email': 'sam@example.com', 'email_verified': True}})
    client.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    client.get(magic_link_from(mailer))
    uid = client.get('/blade-book/api/auth/me').get_json()['id']
    for p in ('google', 'apple'):
        q = _start(client, p)
        client.get(f"/blade-book/api/auth/{p}/callback?code=x&state={q['state'][0]}")
        assert client.get('/blade-book/api/auth/me').get_json()['id'] == uid
    con = db.connect()
    assert con.execute('SELECT count(*) FROM users').fetchone()[0] == 1
    assert db.get_user(con, uid)['auth_subjects'] == {'google': 'g-1', 'apple': 'a-1'}


def test_known_subject_with_new_email_signs_into_existing_user(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-1', 'email': 'sam@example.com',
                                            'email_verified': True}})
    q = _start(client, 'google')
    client.get(f"/blade-book/api/auth/google/callback?code=x&state={q['state'][0]}")
    uid = client.get('/blade-book/api/auth/me').get_json()['id']
    client.post('/blade-book/api/auth/signout')
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-1', 'email': 'new@example.com',
                                            'email_verified': True}})
    q = _start(client, 'google')
    client.get(f"/blade-book/api/auth/google/callback?code=x&state={q['state'][0]}")
    me = client.get('/blade-book/api/auth/me').get_json()
    assert me['id'] == uid and me['email'] == 'sam@example.com'  # email is not silently rewritten


def test_unverified_provider_email_does_not_set_verified(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-2', 'email': 'sam@example.com',
                                            'email_verified': False}})
    q = _start(client, 'google')
    client.get(f"/blade-book/api/auth/google/callback?code=x&state={q['state'][0]}")
    me = client.get('/blade-book/api/auth/me').get_json()
    assert me['verified_at'] is None


@pytest.mark.parametrize('qs', ['code=x&state=bogus', 'code=x', 'state=', 'error=access_denied&state=x'])
def test_callback_failures_redirect_not_500(client, both, monkeypatch, qs):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g', 'email': 'sam@example.com', 'email_verified': True}})
    r = client.get(f'/blade-book/api/auth/google/callback?{qs}')
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/?auth=failed')
    assert client.get('/blade-book/api/auth/me').status_code == 401


def test_state_is_single_use(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g', 'email': 'sam@example.com', 'email_verified': True}})
    q = _start(client, 'google')
    url = f"/blade-book/api/auth/google/callback?code=x&state={q['state'][0]}"
    assert client.get(url).headers['Location'].endswith('/blade-book/')
    client.post('/blade-book/api/auth/signout')
    assert client.get(url).headers['Location'].endswith('?auth=failed')


def test_exchange_error_redirects_failed(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'google': oidc.OIDCError('nonce mismatch')})
    q = _start(client, 'google')
    r = client.get(f"/blade-book/api/auth/google/callback?code=x&state={q['state'][0]}")
    assert r.headers['Location'].endswith('?auth=failed')


def test_start_is_rate_limited_per_ip(client, both):
    from bb import auth
    for _ in range(auth.IP_ATTEMPTS_PER_HOUR):
        assert client.get('/blade-book/api/auth/google', headers={'X-Forwarded-For': '9.9.9.9'}).status_code == 302
    assert client.get('/blade-book/api/auth/google', headers={'X-Forwarded-For': '9.9.9.9'}).status_code == 429
