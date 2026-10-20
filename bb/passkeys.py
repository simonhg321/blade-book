# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""
bb/passkeys.py — WebAuthn (passkeys) via py_webauthn, 2026-10-19.

A thin wrapper: our relying-party constants, our challenge (32 bytes from
`secrets`, i.e. os.urandom → the kernel CSPRNG; never a library default), and
one exception type. No Flask, no DB. The library is imported lazily so a box
without it still runs every other route; `available()` tells the routes to
answer 503 and the pages to hide their buttons.

Policy (spec §1): user verification REQUIRED, resident key REQUIRED,
attestation none, user handle = the integer user id as ASCII bytes.
"""
import base64
import logging
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse

from bb import auth

log = logging.getLogger('blade-book.passkeys')

RP_NAME = 'blade-book'
CHALLENGE_BYTES = 32
FLOW_TTL_S = 300
MAX_PASSKEYS = 10


class PasskeyError(Exception):
    """Any verification failure; args[0] is the reason (log it, show it only to the owner)."""


@dataclass
class Registered:
    credential_id: bytes
    public_key: bytes
    sign_count: int
    transports: list


def available():
    try:
        import webauthn  # noqa: F401
        return True
    except ImportError:
        return False


def rp_id():
    return urlparse(auth.base_url()).hostname


def origin():
    return auth.base_url()


def new_challenge():
    return secrets.token_bytes(CHALLENGE_BYTES)


COOKIE = 'bb_passkey'          # flow cookie carrying the challenge; set/cleared by both route modules


def encode_challenge(challenge):
    return base64.urlsafe_b64encode(challenge).rstrip(b'=').decode('ascii')


def decode_challenge(raw):
    """The cookie value back to bytes, or None for anything that is not exactly
    one of ours (absent, oversize, bad base64, wrong length)."""
    if not isinstance(raw, str) or not raw or len(raw) > 128:
        return None
    try:
        out = base64.urlsafe_b64decode(raw + '=' * (-len(raw) % 4))
    except (ValueError, TypeError):
        return None
    return out if len(out) == CHALLENGE_BYTES else None


def _user_handle(user_id):
    return str(int(user_id)).encode('ascii')


def registration_options(user_id, handle, display_name, exclude_ids, challenge):
    import webauthn
    from webauthn.helpers.structs import (AuthenticatorSelectionCriteria, PublicKeyCredentialDescriptor,
                                          ResidentKeyRequirement, UserVerificationRequirement)
    opts = webauthn.generate_registration_options(
        rp_id=rp_id(), rp_name=RP_NAME, user_id=_user_handle(user_id), user_name=handle,
        user_display_name=display_name or handle, challenge=challenge,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=cid) for cid in exclude_ids])
    return webauthn.options_to_json(opts)


def verify_registration(credential, challenge):
    import webauthn
    from webauthn.helpers.exceptions import WebAuthnException
    try:
        v = webauthn.verify_registration_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=origin(), require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError) as e:
        raise PasskeyError(str(e) or e.__class__.__name__) from e
    transports = None
    resp = credential.get('response') if isinstance(credential, dict) else None
    if isinstance(resp, dict) and isinstance(resp.get('transports'), list):
        transports = [t for t in resp['transports'] if isinstance(t, str)][:8]
    return Registered(credential_id=v.credential_id, public_key=v.credential_public_key,
                      sign_count=v.sign_count, transports=transports)


def authentication_options(challenge):
    import webauthn
    from webauthn.helpers.structs import UserVerificationRequirement
    opts = webauthn.generate_authentication_options(
        rp_id=rp_id(), challenge=challenge, user_verification=UserVerificationRequirement.REQUIRED)
    return webauthn.options_to_json(opts)


def verify_authentication(credential, challenge, public_key, sign_count):
    """Returns the new sign count. The library refuses a count that did not
    grow when the stored one is above zero (a cloned authenticator); 0 → 0 is
    accepted because synced passkeys never count."""
    import webauthn
    from webauthn.helpers.exceptions import WebAuthnException
    try:
        v = webauthn.verify_authentication_response(
            credential=credential, expected_challenge=challenge, expected_rp_id=rp_id(),
            expected_origin=origin(), credential_public_key=public_key,
            credential_current_sign_count=int(sign_count), require_user_verification=True)
    except (WebAuthnException, ValueError, TypeError, KeyError) as e:
        raise PasskeyError(str(e) or e.__class__.__name__) from e
    return v.new_sign_count


_UA_RULES = (
    (re.compile(r'iPhone'), 'iPhone'),
    (re.compile(r'iPad'), 'iPad'),
    (re.compile(r'Android'), 'Android'),
)
_BROWSERS = (('Firefox/', 'Firefox'), ('Edg/', 'Edge'), ('Chrome/', 'Chrome'), ('Safari/', 'Safari'))
_PLATFORMS = (('Macintosh', 'Mac'), ('Windows', 'Windows'), ('Linux', 'Linux'))


def device_name(user_agent):
    """A short label for the settings list ('Safari on Mac', 'iPhone'); 'passkey' when unknown."""
    ua = user_agent or ''
    for rx, name in _UA_RULES:
        if rx.search(ua):
            return name
    browser = next((n for needle, n in _BROWSERS if needle in ua), None)
    platform = next((n for needle, n in _PLATFORMS if needle in ua), None)
    if browser and platform:
        return f'{browser} on {platform}'
    return 'passkey'
