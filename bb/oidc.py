# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/oidc.py — Sign in with Google / Apple, provider-agnostic.

Authorization-code flow, `openid email` only (spec §6). The ID token is
verified locally against the provider's JWKS (PyJWT); `verify_id_token`
takes an explicit key so tests never touch the network. Apple's client
secret is an ES256 JWT we mint from the .p8 key; Apple returns the code
by cross-site form_post, which is why OIDC state lives in the DB.

State is single-use and provider-bound but NOT bound to the caller's browser
(Apple's form_post is cross-site, so a SameSite=Lax cookie cannot carry it). A
login-CSRF where an attacker completes their own flow and hands the callback
URL to a victim is therefore possible; later plans must not treat state as
proof of browser identity.
"""
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import jwt
import requests

from bb import config, paths

log = logging.getLogger('blade-book.oidc')


class OIDCError(Exception):
    pass


@dataclass
class Provider:
    name: str
    authorize_url: str
    token_url: str
    jwks_url: str
    issuers: tuple
    client_id: str
    client_secret: object  # str or zero-arg callable
    scope: str = 'openid email'
    response_mode: str = 'query'

    def secret(self):
        return self.client_secret() if callable(self.client_secret) else self.client_secret


def google():
    cid, sec = config.get('GOOGLE_CLIENT_ID'), config.get('GOOGLE_CLIENT_SECRET')
    if not (cid and sec):
        return None
    return Provider(
        name='google',
        authorize_url='https://accounts.google.com/o/oauth2/v2/auth',
        token_url='https://oauth2.googleapis.com/token',
        jwks_url='https://www.googleapis.com/oauth2/v3/certs',
        issuers=('https://accounts.google.com', 'accounts.google.com'),
        client_id=cid, client_secret=sec)


def apple():
    cid, team, kid = (config.get('APPLE_CLIENT_ID'), config.get('APPLE_TEAM_ID'),
                      config.get('APPLE_KEY_ID'))
    pem = config.get('APPLE_PRIVATE_KEY')
    if not (cid and team and kid and pem):
        return None
    pem = pem.replace('\\n', '\n')  # .env keeps it on one line
    return Provider(
        name='apple',
        authorize_url='https://appleid.apple.com/auth/authorize',
        token_url='https://appleid.apple.com/auth/token',
        jwks_url='https://appleid.apple.com/auth/keys',
        issuers=('https://appleid.apple.com',),
        client_id=cid,
        client_secret=lambda: apple_client_secret(team, cid, kid, pem),
        scope='email', response_mode='form_post')


PROVIDERS = {'google': google, 'apple': apple}


def get(name):
    factory = PROVIDERS.get(name)
    return factory() if factory else None


def configured():
    return [n for n in PROVIDERS if get(n) is not None]


def redirect_uri(provider):
    from bb import auth
    return f'{auth.base_url()}{paths.API_PREFIX}/auth/{provider.name}/callback'


def authorize_url(provider, state, nonce):
    q = {'client_id': provider.client_id, 'response_type': 'code',
         'scope': provider.scope, 'redirect_uri': redirect_uri(provider),
         'state': state, 'nonce': nonce}
    if provider.response_mode != 'query':
        q['response_mode'] = provider.response_mode
    return provider.authorize_url + '?' + urlencode(q)


def apple_client_secret(team_id, client_id, key_id, private_key_pem):
    now = int(time.time())
    return jwt.encode(
        {'iss': team_id, 'iat': now, 'exp': now + 180 * 86400,
         'aud': 'https://appleid.apple.com', 'sub': client_id},
        private_key_pem, algorithm='ES256', headers={'kid': key_id})


_JWK_CLIENTS = {}


def _signing_key(provider, id_token):
    """Public key for this token's kid, fetched from the provider's JWKS.
    One PyJWKClient per jwks_url, cached, so cache_keys=True actually helps
    and repeat logins don't refetch the JWKS. Separate so tests can
    monkeypatch it."""
    client = _JWK_CLIENTS.get(provider.jwks_url)
    if client is None:
        client = jwt.PyJWKClient(provider.jwks_url, cache_keys=True)
        _JWK_CLIENTS[provider.jwks_url] = client
    return client.get_signing_key_from_jwt(id_token).key


def verify_id_token(provider, id_token, nonce, key=None):
    try:
        key = key or _signing_key(provider, id_token)
        claims = jwt.decode(id_token, key, algorithms=['RS256', 'ES256'],
                            audience=provider.client_id, leeway=60,
                            options={'require': ['exp', 'iat', 'sub', 'aud', 'iss']})
    except jwt.PyJWTError as e:
        raise OIDCError(f'{provider.name} id_token rejected: {e}') from e
    except Exception as e:  # JWKS fetch failed, etc. — surface it, never hide
        raise OIDCError(f'{provider.name} key lookup failed: {e!r}') from e
    # PyJWT 2.7 (stark) only takes a single issuer string; Google has two forms.
    if claims.get('iss') not in provider.issuers:
        raise OIDCError(f'{provider.name} issuer rejected: {claims.get("iss")!r}')
    if claims.get('nonce') != nonce:
        raise OIDCError(f'{provider.name} nonce mismatch')
    if not claims.get('sub') or not claims.get('email'):
        raise OIDCError(f'{provider.name} token missing sub/email')
    ev = claims.get('email_verified', False)
    if isinstance(ev, str):
        ev = ev.lower() == 'true'  # Apple sends the string "true"
    return {'sub': claims['sub'], 'email': claims['email'].strip().lower(),
            'email_verified': bool(ev)}


def exchange_code(provider, code, nonce):
    """Authorization code → verified claims. Network happens here and only here."""
    data = {'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': redirect_uri(provider),
            'client_id': provider.client_id, 'client_secret': provider.secret()}
    try:
        r = requests.post(provider.token_url, data=data, timeout=10)
    except requests.RequestException as e:
        raise OIDCError(f'{provider.name} token endpoint unreachable: {e!r}') from e
    if r.status_code != 200:
        raise OIDCError(f'{provider.name} token endpoint {r.status_code}: {r.text[:200]!r}')
    try:
        id_token = r.json().get('id_token')
    except ValueError as e:
        raise OIDCError(f'{provider.name} token endpoint returned non-JSON: {r.text[:200]!r}') from e
    if not id_token:
        raise OIDCError(f'{provider.name} token response had no id_token')
    return verify_id_token(provider, id_token, nonce)
