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


@pytest.fixture
def app(env, mailer, monkeypatch):
    monkeypatch.setenv('SESSION_KEY', 'test-session-key-not-secret')
    monkeypatch.setenv('BASE_URL', 'http://localhost')
    from app import create_app
    return create_app(mailer=mailer)


@pytest.fixture
def client(app):
    return app.test_client()


def magic_link_from(mailer):
    """The last magic link the fake mailer saw."""
    import re
    m = re.search(r'https?://\S+/api/auth/magic\?t=\S+', mailer.sent[-1]['text'])
    assert m, mailer.sent[-1]['text']
    return m.group(0)
