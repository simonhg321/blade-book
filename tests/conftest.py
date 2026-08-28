# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import importlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    """Point every blade-book path at tmp_path so tests never touch the box."""
    for name in ('CONFIG', 'DATA', 'LOG', 'WWW'):
        monkeypatch.setenv(f'BLADEBOOK_{name}_DIR', str(tmp_path / name.lower()))
    from bb import paths
    importlib.reload(paths)
    paths.ensure_dirs()
    yield paths
    importlib.reload(paths)


@pytest.fixture
def mailer():
    from bb import mail
    return mail.FakeMailer()


def ok_result(**over):
    """A canned schema-shaped decode result (CRK Sebenza 31) for FakeDecoder.
    Lives here (not in tests/test_decode_api.py) so both conftest's `decoder`
    fixture and the test module can import it without a cycle."""
    from bb.makers import core, crk
    r = {f: '' for f in core.CORE_FIELDS}
    r.update({'blade_length_in': None, 'condition': 1, 'has_box': True, 'has_card': True, 'has_papers': False,
              'has_pouch': True, 'has_lanyard': False, 'has_spare_hardware': False, 'model': 'Sebenza',
              'blade_steel': 'CPM MagnaCut', 'blade_shape': 'Drop Point', 'born_on': '2025-09-29',
              'born_on_precision': 'day', 'born_on_source': 'card'})
    r['ext'] = {k: '' for k in crk.EXT_PROPS}
    r['ext'].update({'generation': '31', 'size': 'Large', 'crk_sku': 'L31-1400-0004', 'hand': 'right'})
    r.update({'card_text': 'LARGE SEBENZA 31', 'no_card': False, 'reasoning': 'card read',
              'confidence': {f: 'high' for f in list(core.CORE_FIELDS) + list(crk.EXT_PROPS)}})
    r.update(over)
    return r


@pytest.fixture
def decoder():
    from bb import decode
    return decode.FakeDecoder(ok_result())


@pytest.fixture
def app(env, mailer, decoder, monkeypatch):
    monkeypatch.setenv('SESSION_KEY', 'test-session-key-not-secret')
    monkeypatch.setenv('BASE_URL', 'http://localhost')
    from app import create_app
    return create_app(mailer=mailer, decoder=decoder)


@pytest.fixture
def client(app):
    return app.test_client()


def magic_link_from(mailer):
    """The last magic link the fake mailer saw."""
    import re
    m = re.search(r'https?://\S+/api/auth/magic\?t=\S+', mailer.sent[-1]['text'])
    assert m, mailer.sent[-1]['text']
    return m.group(0)


def signed_in(client, mailer, email='sam@example.com'):
    """Magic-link a user in on this client; returns the /me payload."""
    client.post('/blade-book/api/auth/magic', json={'email': email})
    client.get(magic_link_from(mailer))
    me = client.get('/blade-book/api/auth/me')
    assert me.status_code == 200, me.data
    return me.get_json()
