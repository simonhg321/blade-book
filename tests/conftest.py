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
