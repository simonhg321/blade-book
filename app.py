# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
app.py — blade-book Flask app. gunicorn entry: `app:app` on 127.0.0.1:5004.
Apache proxies /blade-book/api/ here and serves /blade-book/ static itself.
"""
import logging
import os
import shutil
import subprocess
from logging.handlers import RotatingFileHandler

from flask import Blueprint, Flask, jsonify

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
            'version': _version()}
    return jsonify(body), (200 if ok_db else 503)


@api.app_errorhandler(404)
def _not_found(_e):
    return jsonify({'error': 'not found'}), 404


def create_app():
    config.load()
    _setup_logging()
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # one iPhone photo, with room
    app.register_blueprint(api)
    log.info('blade-book app created, version %s, data %s', _version(), paths.DATA_DIR)
    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=paths.PORT, debug=False)
