# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""A software WebAuthn authenticator for tests: a real P-256 key, real
authenticatorData, a `none` attestation object and real ECDSA assertions, in
the exact JSON shape a browser's PublicKeyCredential.toJSON() produces. No
mocking of py_webauthn: every test drives the library's true verification.
Knobs on assert_() exist to forge the wrong origin / challenge / sign count /
user handle so the refusal paths are exercised with otherwise-valid data."""
import base64
import hashlib
import json
import secrets
import struct

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


def b64u_dec(s):
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


class SoftAuthenticator:
    """One credential. uv=False makes an authenticator that never verifies the user."""

    def __init__(self, rp_id, origin, uv=True):
        self.rp_id, self.origin, self.uv = rp_id, origin, uv
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = secrets.token_bytes(16)
        self.sign_count = 0
        self.user_handle = None

    def _cose_key(self):
        nums = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1,
                            -2: nums.x.to_bytes(32, 'big'), -3: nums.y.to_bytes(32, 'big')})

    def _auth_data(self, with_cred, sign_count):
        flags = 0x01 | (0x04 if self.uv else 0) | (0x40 if with_cred else 0)   # UP, UV, AT
        data = hashlib.sha256(self.rp_id.encode()).digest() + bytes([flags]) + struct.pack('>I', sign_count)
        if with_cred:
            data += (b'\x00' * 16 + struct.pack('>H', len(self.credential_id))
                     + self.credential_id + self._cose_key())
        return data

    def _client_data(self, typ, challenge_b64u, origin=None):
        return json.dumps({'type': typ, 'challenge': challenge_b64u,
                           'origin': origin or self.origin, 'crossOrigin': False}).encode()

    def register(self, options_json, user_handle=None):
        opts = json.loads(options_json)
        self.user_handle = b64u_dec(opts['user']['id']) if user_handle is None else user_handle
        cd = self._client_data('webauthn.create', opts['challenge'])
        att = cbor2.dumps({'fmt': 'none', 'attStmt': {}, 'authData': self._auth_data(True, 0)})
        return {'id': b64u(self.credential_id), 'rawId': b64u(self.credential_id),
                'type': 'public-key', 'authenticatorAttachment': 'platform',
                'response': {'clientDataJSON': b64u(cd), 'attestationObject': b64u(att),
                             'transports': ['internal']}}

    def assert_(self, options_json, sign_count=None, origin=None, challenge=None, user_handle=None):
        opts = json.loads(options_json)
        if sign_count is None:
            self.sign_count += 1
            sign_count = self.sign_count
        cd = self._client_data('webauthn.get', challenge or opts['challenge'], origin)
        ad = self._auth_data(False, sign_count)
        sig = self.key.sign(ad + hashlib.sha256(cd).digest(), ec.ECDSA(hashes.SHA256()))
        uh = self.user_handle if user_handle is None else user_handle
        return {'id': b64u(self.credential_id), 'rawId': b64u(self.credential_id),
                'type': 'public-key', 'authenticatorAttachment': 'platform',
                'response': {'clientDataJSON': b64u(cd), 'authenticatorData': b64u(ad),
                             'signature': b64u(sig), 'userHandle': b64u(uh) if uh else None}}
