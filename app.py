# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
app.py — blade-book Flask app. gunicorn entry: `app:app` on 127.0.0.1:5004.
Apache proxies /blade-book/api/ here and serves /blade-book/ static itself.
"""
import logging
import os
import re
import secrets
import shutil
import subprocess
from datetime import timedelta
from logging.handlers import WatchedFileHandler

from flask import Blueprint, Flask, current_app, jsonify, request

from bb import config, db, paths

log = logging.getLogger('blade-book')


def _version():
    try:
        return subprocess.run(['git', '-C', paths.CODE_DIR, 'rev-parse',
                               '--short', 'HEAD'], capture_output=True,
                              text=True, timeout=2).stdout.strip() or 'dev'
    except Exception:
        return 'dev'


class _OneRecordPerLine(logging.Formatter):
    """A record starts a line with its stamp, and nothing else does. User text
    lands in this log (a want, a file name, a report's reason); with a line
    break in it, the rest would read as a record of its own. So every line
    after the first is indented, tracebacks included, and the other characters
    that break a line become spaces. bb/activity.py and scripts/monitor.py
    read this file line by line and rely on it."""
    _BREAKS = re.compile('[\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029]')

    def format(self, record):
        return self._BREAKS.sub(' ', super().format(record)).replace('\n', '\n\t')


class _PrivateWatchedFile(WatchedFileHandler):
    """scripts/rotate_log.py moves app.log away each night (the terms promise
    90 days). This handler sees the move before its next line and opens a
    fresh app.log; two gunicorn workers rotating one file themselves would
    fight over it. A file it creates is born 0o640: sign-in links land here
    via LogMailer."""

    def _open(self):
        return open(self.baseFilename, self.mode, encoding=self.encoding, errors=self.errors,
                    opener=lambda path, flags: os.open(path, flags, 0o640))


def _setup_logging():
    """One file handler pointed at the *current* LOG_DIR (tests reload paths
    per test, so a stale handler is swapped, not duplicated)."""
    paths.ensure_dirs()
    target = os.path.join(paths.LOG_DIR, 'app.log')
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, _PrivateWatchedFile):
            if h.baseFilename == target:
                return
            root.removeHandler(h)
            h.close()
    handler = _PrivateWatchedFile(target)
    handler.setFormatter(_OneRecordPerLine(
        '%(asctime)s %(levelname)s %(name)s: %(message)s'))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        os.chmod(target, 0o640)  # a file that was already there keeps its old mode otherwise
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


_MUTATING = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})


def _origin_of(url):
    from urllib.parse import urlsplit
    u = urlsplit(url)
    return f'{u.scheme}://{u.netloc}'.lower()


@api.before_app_request
def _same_origin_only():
    """CSRF backstop (review M1). SameSite=Lax stops cross-SITE posts but not
    same-site ones (another host under the same registrable domain) and not a
    same-origin script. A browser sends `Origin` on every POST/PUT/PATCH/DELETE
    it makes, so a mutating API call whose Origin is not ours is refused;
    `Sec-Fetch-Site` is checked the same way when present. Requests carrying
    neither header are non-browser clients (curl, tests) and CSRF does not
    apply to them. The OIDC callbacks are cross-site by design (Apple form_post)
    and carry their own state check."""
    if request.method not in _MUTATING or not request.path.startswith(paths.API_PREFIX):
        return None
    if request.path.startswith(paths.API_PREFIX + '/auth/') and request.path.endswith('/callback'):
        return None
    from bb import auth as auth_mod
    # behind Apache the WSGI scheme is plain http, so build the request-host
    # origin from BASE_URL's scheme (alias + apex both work across the switch)
    base = _origin_of(auth_mod.base_url())
    ours = {base, f"{base.split('://', 1)[0]}://{request.host}".lower()}   # same scheme as BASE_URL, any of our hosts
    origin = request.headers.get('Origin')
    if origin is not None and origin.strip().lower() not in ours:
        log.warning('cross-origin %s %s refused: origin=%r', request.method, request.path, origin[:100])
        return jsonify({'error': 'cross-origin request refused'}), 403
    site = request.headers.get('Sec-Fetch-Site')
    if site is not None and site.strip().lower() not in ('same-origin', 'none'):
        log.warning('cross-site %s %s refused: sec-fetch-site=%r', request.method, request.path, site[:40])
        return jsonify({'error': 'cross-origin request refused'}), 403
    return None


@api.after_app_request
def _api_json_no_store(resp):
    """Review L10: authed JSON (register rows, settings, /me) must never sit in
    a shared or back/forward cache. Only API JSON — photo bytes keep their own
    caching, HTML is Apache's business."""
    if request.path.startswith(paths.API_PREFIX) and resp.mimetype == 'application/json':
        resp.headers['Cache-Control'] = 'no-store'
    return resp


@api.app_errorhandler(404)
def _not_found(_e):
    return jsonify({'error': 'not found'}), 404


@api.app_errorhandler(413)
def _too_large(_e):
    return jsonify({'error': 'photo over 20 MB'}), 413


def create_app(mailer=None, store=None, decoder=None, billing_impl=None, lookup=None):
    config.load()
    _setup_logging()
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # one iPhone photo, with room

    secret = config.get('SESSION_KEY')
    if not secret:
        secret = secrets.token_hex(32)
        log.warning('SESSION_KEY missing from .env — each gunicorn worker will mint its own '
                    'key and sessions will break across workers/restarts')
    from bb import auth, billing, decode, lookup as lookup_mod, mail
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
        LOOKUP=lookup or lookup_mod.from_env(),
        BILLING=billing_impl or billing.from_env(),
        VERSION=_version(),
    )

    from bb.routes import auth as auth_routes, knives as knife_routes, settings as settings_routes, search as search_routes, wants as wants_routes, board as board_routes, admin as admin_routes, billing as billing_routes
    app.register_blueprint(api)
    app.register_blueprint(auth_routes.bp)
    app.register_blueprint(knife_routes.bp)
    app.register_blueprint(settings_routes.bp)
    app.register_blueprint(search_routes.bp)
    app.register_blueprint(wants_routes.bp)
    app.register_blueprint(board_routes.bp)
    app.register_blueprint(admin_routes.bp)
    app.register_blueprint(billing_routes.bp)
    log.info('blade-book app created, version %s, data %s, mailer %s, decoder %s',
             app.config['VERSION'], paths.DATA_DIR, type(app.config['MAILER']).__name__,
             type(app.config['DECODER']).__name__)
    return app


app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=paths.PORT, debug=False)
