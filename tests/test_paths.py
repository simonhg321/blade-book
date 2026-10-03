# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import importlib
import os


def test_paths_follow_env_and_ensure_dirs_creates_them(env, tmp_path):
    assert env.DATA_DIR == str(tmp_path / 'data')
    assert env.LOG_DIR == str(tmp_path / 'log')
    assert env.db_path() == str(tmp_path / 'data' / 'blade-book.db')
    assert env.env_file() == str(tmp_path / 'config' / '.env')
    for d in (env.CONFIG_DIR, env.DATA_DIR, env.LOG_DIR, env.WWW_DIR,
              os.path.join(env.DATA_DIR, 'photos')):
        assert os.path.isdir(d)


def test_defaults_are_the_production_paths(monkeypatch):
    for name in ('CONFIG', 'DATA', 'LOG', 'WWW'):
        monkeypatch.delenv(f'BLADEBOOK_{name}_DIR', raising=False)
    from bb import paths
    importlib.reload(paths)
    assert paths.CONFIG_DIR == '/etc/blade-book'
    assert paths.DATA_DIR == '/var/lib/blade-book'
    assert paths.LOG_DIR == '/var/log/blade-book'
    assert paths.WWW_DIR == '/var/www/html/blade-book'
