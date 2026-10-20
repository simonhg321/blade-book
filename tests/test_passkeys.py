# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/passkeys.py — the py_webauthn wrapper, driven by a software authenticator."""
import json

import pytest

from bb import passkeys
from tests.softauth import SoftAuthenticator, b64u

RP, ORIGIN = 'localhost', 'http://localhost'


@pytest.fixture
def base(monkeypatch):
    monkeypatch.setenv('BASE_URL', ORIGIN)


def _register(auth, uid=7, handle='sam'):
    chal = passkeys.new_challenge()
    opts = passkeys.registration_options(uid, handle, None, [], chal)
    return passkeys.verify_registration(auth.register(opts), chal)


def test_available_and_rp_from_base_url(base):
    assert passkeys.available() is True
    assert passkeys.rp_id() == 'localhost' and passkeys.origin() == 'http://localhost'


def test_rp_id_follows_base_url_host(monkeypatch):
    monkeypatch.setenv('BASE_URL', 'https://blade-book.com')
    assert passkeys.rp_id() == 'blade-book.com' and passkeys.origin() == 'https://blade-book.com'


def test_challenge_is_32_fresh_bytes_from_secrets(monkeypatch):
    seen = []
    orig = passkeys.secrets.token_bytes          # capture BEFORE patching, or the lambda recurses into itself
    monkeypatch.setattr(passkeys.secrets, 'token_bytes', lambda n: seen.append(n) or orig(n))
    a, b = passkeys.new_challenge(), passkeys.new_challenge()
    assert seen == [32, 32] and len(a) == 32 and a != b


def test_challenge_cookie_round_trip():
    chal = passkeys.new_challenge()
    raw = passkeys.encode_challenge(chal)
    assert '=' not in raw and len(raw) == 43 and passkeys.COOKIE == 'bb_passkey'
    assert passkeys.decode_challenge(raw) == chal
    for bad in (None, '', 'x' * 200, '!!!!', passkeys.encode_challenge(b'short')):
        assert passkeys.decode_challenge(bad) is None


def test_registration_options_carry_our_policy(base):
    chal = passkeys.new_challenge()
    o = json.loads(passkeys.registration_options(7, 'sam', 'Sam', [b'old-cred'], chal))
    assert o['challenge'] == b64u(chal)
    assert o['rp'] == {'id': 'localhost', 'name': 'blade-book'}
    assert o['user']['id'] == b64u(b'7') and o['user']['name'] == 'sam' and o['user']['displayName'] == 'Sam'
    assert o['authenticatorSelection']['residentKey'] == 'required'
    assert o['authenticatorSelection']['userVerification'] == 'required'
    assert o['attestation'] == 'none'
    assert [c['id'] for c in o['excludeCredentials']] == [b64u(b'old-cred')]


def test_display_name_falls_back_to_handle(base):
    o = json.loads(passkeys.registration_options(7, 'sam', None, [], passkeys.new_challenge()))
    assert o['user']['displayName'] == 'sam'


def test_register_then_assert_round_trip(base):
    auth = SoftAuthenticator(RP, ORIGIN)
    reg = _register(auth)
    assert reg.credential_id == auth.credential_id
    assert reg.sign_count == 0 and reg.transports == ['internal'] and len(reg.public_key) > 40
    chal = passkeys.new_challenge()
    opts = passkeys.authentication_options(chal)
    o = json.loads(opts)
    assert o['challenge'] == b64u(chal) and o['rpId'] == 'localhost'
    assert o['userVerification'] == 'required' and o.get('allowCredentials') in (None, [])
    assert passkeys.verify_authentication(auth.assert_(opts), chal, reg.public_key, 0) == 1


def test_registration_refusals(base):
    chal = passkeys.new_challenge()
    opts = passkeys.registration_options(7, 'sam', None, [], chal)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration(SoftAuthenticator('other.example', ORIGIN).register(opts), chal)      # wrong RP
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration(SoftAuthenticator(RP, 'https://evil.example').register(opts), chal)  # wrong origin
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration(SoftAuthenticator(RP, ORIGIN).register(opts), passkeys.new_challenge())  # wrong challenge
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration(SoftAuthenticator(RP, ORIGIN, uv=False).register(opts), chal)        # no UV
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_registration({'id': 'nope'}, chal)                                                # garbage


def test_authentication_refusals(base):
    auth = SoftAuthenticator(RP, ORIGIN)
    reg = _register(auth)
    chal = passkeys.new_challenge()
    opts = passkeys.authentication_options(chal)
    for bad in (auth.assert_(opts, origin='https://evil.example'),
                auth.assert_(opts, challenge=b64u(passkeys.new_challenge())),
                auth.assert_(opts, sign_count=1)):           # stored count below says 5
        with pytest.raises(passkeys.PasskeyError):
            passkeys.verify_authentication(bad, chal, reg.public_key, 5)
    auth.uv = False
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_authentication(auth.assert_(opts), chal, reg.public_key, 0)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.verify_authentication({'id': 'nope'}, chal, reg.public_key, 0)


def test_zero_sign_count_both_sides_is_fine(base):
    """iCloud Keychain and most synced passkeys always report 0."""
    auth = SoftAuthenticator(RP, ORIGIN)
    reg = _register(auth)
    chal = passkeys.new_challenge()
    opts = passkeys.authentication_options(chal)
    assert passkeys.verify_authentication(auth.assert_(opts, sign_count=0), chal, reg.public_key, 0) == 0


def test_unavailable_when_the_library_is_missing(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_webauthn(name, *a, **k):
        if name == 'webauthn' or name.startswith('webauthn.'):
            raise ImportError('no webauthn')
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, '__import__', no_webauthn)
    assert passkeys.available() is False


@pytest.mark.parametrize('ua,name', [
    ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 Version/17.5 Safari/605.1.15', 'Safari on Mac'),
    ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1', 'iPhone'),
    ('Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Safari/604.1', 'iPad'),
    ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36', 'Chrome on Windows'),
    ('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/537.36 Chrome/128.0 Safari/537.36', 'Chrome on Mac'),
    ('Mozilla/5.0 (X11; Linux x86_64; rv:129.0) Gecko/20100101 Firefox/129.0', 'Firefox on Linux'),
    ('Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/128.0 Mobile Safari/537.36', 'Android'),
    ('', 'passkey'), (None, 'passkey'), ('curl/8.5.0', 'passkey'),
])
def test_device_name_from_user_agent(ua, name):
    assert passkeys.device_name(ua) == name
