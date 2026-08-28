# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import os
import stat


def test_healthz_ok(env):
    from app import create_app
    client = create_app().test_client()
    r = client.get('/blade-book/api/healthz')
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] is True and body['db'] is True
    assert 0 <= body['disk_free_pct'] <= 100
    assert isinstance(body['version'], str) and body['version']
    assert os.path.exists(os.path.join(env.LOG_DIR, 'app.log'))


def test_healthz_reports_db_failure(env, monkeypatch):
    from app import create_app
    from bb import db

    def boom():
        raise RuntimeError('disk on fire')
    monkeypatch.setattr(db, 'connect', boom)
    r = create_app().test_client().get('/blade-book/api/healthz')
    assert r.status_code == 503
    assert r.get_json()['ok'] is False and r.get_json()['db'] is False


def test_unknown_api_route_is_json_404(env):
    from app import create_app
    r = create_app().test_client().get('/blade-book/api/nope')
    assert r.status_code == 404 and r.get_json() == {'error': 'not found'}


def test_config_load_tolerates_missing_env_file(env):
    from bb import config
    config.load()  # must not raise when CONFIG_DIR/.env is absent


def test_app_log_is_not_world_readable(env):
    from app import create_app
    create_app()
    app_log = os.path.join(env.LOG_DIR, 'app.log')
    assert stat.S_IMODE(os.stat(app_log).st_mode) == 0o640


def test_healthz_version_is_captured_at_app_creation(env, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, '_version', lambda: 'abc1234')
    a = app_module.create_app()
    monkeypatch.setattr(app_module, '_version', lambda: 'zzz9999')  # git moved on; process did not
    assert a.test_client().get('/blade-book/api/healthz').get_json()['version'] == 'abc1234'
