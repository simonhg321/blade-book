# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
app.py — blade-book Flask app. gunicorn entry: `app:app` on 127.0.0.1:5004.
Apache proxies /blade-book/api/ here and serves /blade-book/ static itself.
"""
import logging
import os
import secrets
import shutil
import subprocess
from datetime import timedelta
from logging.handlers import RotatingFileHandler

from flask import Blueprint, Flask, current_app, jsonify

from bb import config, db, paths

log = logging.getLogger('blade-book')


def _version():
    try:
        return subprocess.run(['git', '-C', paths.CODE_DIR, 'rev-parse',
                               '--short', 'HEAD'], capture_output=True,
                              text=True, timeout=2).stdout.strip() or 'dev'
    except Exception:
        return 'dev'


def _setup_logging():
    """One rotating file handler pointed at the *current* LOG_DIR (tests
    reload paths per test, so a stale handler is swapped, not duplicated)."""
    paths.ensure_dirs()
    target = os.path.join(paths.LOG_DIR, 'app.log')
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, RotatingFileHandler):
            if h.baseFilename == target:
                return
            root.removeHandler(h)
            h.close()
    handler = RotatingFileHandler(target, maxBytes=5_000_000, backupCount=5)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        os.chmod(target, 0o640)  # magic links land here via LogMailer — never world-readable
        # rotated files (app.log.1, .2, ...) inherit this only via the process
        # umask at rotation time, not this chmod — acceptable, they're on the
        # same host under the same log dir permissions.
    except OSError as e:
        log.warning('could not chmod %s to 0o640: %r', target, e)


api = Blueprint('api', __name__, url_prefix=paths.API_PREFIX)


@api.route('/healthz')
def healthz():
    ok_db = True
    try:
        con = db.connect()
        con.execute('SELECT 1')
        con.close()
    except Exception as e:  # report it; never hide it
        log.error('healthz db check failed: %r', e)
        ok_db = False
    usage = shutil.disk_usage(paths.DATA_DIR)
    body = {'ok': ok_db, 'db': ok_db,
            'disk_free_pct': int(usage.free * 100 / usage.total),
            'version': current_app.config['VERSION']}
    return jsonify(body), (200 if ok_db else 503)


@api.app_errorhandler(404)
def _not_found(_e):
    return jsonify({'error': 'not found'}), 404


@api.app_errorhandler(413)
def _too_large(_e):
    return jsonify({'error': 'photo over 20 MB'}), 413


def create_app(mailer=None, store=None, decoder=None):
    config.load()
    _setup_logging()
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # one iPhone photo, with room

    secret = config.get('SESSION_KEY')
    if not secret:
        secret = secrets.token_hex(32)
        log.warning('SESSION_KEY missing from .env — each gunicorn worker will mint its own '
                    'key and sessions will break across workers/restarts')
    from bb import auth, decode, mail
    from bb import store as store_mod
    app.config.update(
        SECRET_KEY=secret,
        SESSION_COOKIE_NAME='bb_session',
        SESSION_COOKIE_PATH=paths.URL_PREFIX,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Lax',
        SESSION_COOKIE_SECURE=auth.base_url().startswith('https'),
        PERMANENT_SESSION_LIFETIME=timedelta(days=90),
        MAILER=mailer or mail.from_env(),
        STORE=store or store_mod.from_paths(),
        DECODER=decoder or decode.from_env(),
        VERSION=_version(),
    )

    from bb.routes import auth as auth_routes, knives as knife_routes, settings as settings_routes, search as search_routes, wants as wants_routes, board as board_routes
    app.register_blueprint(api)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(knife_routes.bp)
    app.register_blueprint(settings_routes.bp)
    app.register_blueprint(search_routes.bp)
    app.register_blueprint(wants_routes.bp)
    app.register_blueprint(board_routes.bp)
    log.info('blade-book app created, version %s, data %s, mailer %s, decoder %s',
             app.config['VERSION'], paths.DATA_DIR, type(app.config['MAILER']).__name__,
             type(app.config['DECODER']).__name__)
    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=paths.PORT, debug=False)
