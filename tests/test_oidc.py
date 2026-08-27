# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import time
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from bb import oidc


@pytest.fixture
def google_env(monkeypatch):
    monkeypatch.setenv('BASE_URL', 'https://blade-book.com')
    monkeypatch.setenv('GOOGLE_CLIENT_ID', 'gid.apps.googleusercontent.com')
    monkeypatch.setenv('GOOGLE_CLIENT_SECRET', 'gsecret')


@pytest.fixture
def apple_env(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    from cryptography.hazmat.primitives import serialization
    pem = key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    monkeypatch.setenv('BASE_URL', 'https://blade-book.com')
    monkeypatch.setenv('APPLE_CLIENT_ID', 'com.blade-book.web')
    monkeypatch.setenv('APPLE_TEAM_ID', 'TEAM123456')
    monkeypatch.setenv('APPLE_KEY_ID', 'KEY1234567')
    monkeypatch.setenv('APPLE_PRIVATE_KEY', pem.replace('\n', '\\n'))
    return key


def test_unconfigured_providers_are_none(monkeypatch):
    for v in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET', 'APPLE_CLIENT_ID',
              'APPLE_TEAM_ID', 'APPLE_KEY_ID', 'APPLE_PRIVATE_KEY'):
        monkeypatch.delenv(v, raising=False)
    assert oidc.google() is None and oidc.apple() is None
    assert oidc.configured() == [] and oidc.get('google') is None
    assert oidc.get('nope') is None


def test_google_authorize_url(google_env):
    p = oidc.google()
    assert p.name == 'google' and p.response_mode == 'query'
    assert oidc.configured() == ['google']
    assert oidc.redirect_uri(p) == 'https://blade-book.com/blade-book/api/auth/google/callback'
    u = urlparse(oidc.authorize_url(p, 'st8', 'n0nce'))
    assert u.netloc == 'accounts.google.com'
    q = parse_qs(u.query)
    assert q['client_id'] == ['gid.apps.googleusercontent.com']
    assert q['response_type'] == ['code'] and q['scope'] == ['openid email']
    assert q['state'] == ['st8'] and q['nonce'] == ['n0nce']
    assert q['redirect_uri'] == ['https://blade-book.com/blade-book/api/auth/google/callback']
    assert 'response_mode' not in q


def test_apple_authorize_url_uses_form_post(apple_env):
    p = oidc.apple()
    assert p.response_mode == 'form_post'
    q = parse_qs(urlparse(oidc.authorize_url(p, 's', 'n')).query)
    assert q['response_mode'] == ['form_post'] and q['scope'] == ['email']
    assert sorted(oidc.configured()) == ['apple']


def test_apple_client_secret_is_es256_jwt_with_right_claims(apple_env):
    p = oidc.apple()
    secret = p.client_secret() if callable(p.client_secret) else p.client_secret
    pub = apple_env.public_key()
    claims = jwt.decode(secret, pub, algorithms=['ES256'], audience='https://appleid.apple.com')
    assert claims['iss'] == 'TEAM123456' and claims['sub'] == 'com.blade-book.web'
    assert 0 < claims['exp'] - time.time() <= 180 * 86400
    assert jwt.get_unverified_header(secret)['kid'] == 'KEY1234567'


def _rsa_pair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


def _google_token(priv, **over):
    claims = {'iss': 'https://accounts.google.com', 'aud': 'gid.apps.googleusercontent.com',
              'sub': 'g-sub-1', 'email': 'sam@example.com', 'email_verified': True,
              'nonce': 'n0nce', 'iat': int(time.time()), 'exp': int(time.time()) + 300}
    claims.update(over)
    return jwt.encode(claims, priv, algorithm='RS256', headers={'kid': 'k1'})


def test_verify_id_token_accepts_good_token(google_env):
    priv, pub = _rsa_pair()
    claims = oidc.verify_id_token(oidc.google(), _google_token(priv), 'n0nce', key=pub)
    assert claims['sub'] == 'g-sub-1' and claims['email'] == 'sam@example.com'
    assert claims['email_verified'] is True


@pytest.mark.parametrize('bad', [
    {'nonce': 'wrong'},
    {'aud': 'someone-else'},
    {'iss': 'https://evil.example.com'},
    {'exp': int(time.time()) - 10},
    {'email': None},
])
def test_verify_id_token_rejects(google_env, bad):
    priv, pub = _rsa_pair()
    with pytest.raises(oidc.OIDCError):
        oidc.verify_id_token(oidc.google(), _google_token(priv, **bad), 'n0nce', key=pub)


def test_verify_id_token_rejects_wrong_key(google_env):
    priv, _ = _rsa_pair()
    _, other_pub = _rsa_pair()
    with pytest.raises(oidc.OIDCError):
        oidc.verify_id_token(oidc.google(), _google_token(priv), 'n0nce', key=other_pub)


def test_exchange_code_posts_and_verifies(google_env, monkeypatch):
    priv, pub = _rsa_pair()
    posted = {}

    class R:
        status_code = 200

        def json(self):
            return {'id_token': _google_token(priv)}

    def fake_post(url, data=None, timeout=None, **kw):
        posted.update({'url': url, 'data': data, 'timeout': timeout})
        return R()
    import requests
    monkeypatch.setattr(requests, 'post', fake_post)
    monkeypatch.setattr(oidc, '_signing_key', lambda provider, token: pub)
    claims = oidc.exchange_code(oidc.google(), 'the-code', 'n0nce')
    assert claims['email'] == 'sam@example.com'
    assert posted['url'] == 'https://oauth2.googleapis.com/token'
    assert posted['data']['code'] == 'the-code'
    assert posted['data']['grant_type'] == 'authorization_code'
    assert posted['data']['client_secret'] == 'gsecret'
    assert posted['data']['redirect_uri'].endswith('/api/auth/google/callback')
    assert posted['timeout'] == 10


def test_exchange_code_raises_on_token_endpoint_error(google_env, monkeypatch):
    class R:
        status_code = 400

        def json(self):
            return {'error': 'invalid_grant'}
    import requests
    monkeypatch.setattr(requests, 'post', lambda *a, **k: R())
    with pytest.raises(oidc.OIDCError):
        oidc.exchange_code(oidc.google(), 'bad', 'n')
