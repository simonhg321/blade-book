# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/routes/settings.py — the owner's settings: public-page options (plan 06),
the register hero pin, and the account operations behind /me/settings (plan
11: handle once, export, delete). The page key is write-only: the API reports
has_key, never the key itself."""
import json
import logging
import os
import sqlite3
import time

from flask import Blueprint, current_app, g, jsonify, request, send_file

from bb import account, auth, db, mail, passkeys, paths, publish

log = logging.getLogger('blade-book.settings')

bp = Blueprint('settings', __name__, url_prefix=paths.API_PREFIX + '/settings')

MAX_KEY = 64
MIN_PASSWORD, MAX_PASSWORD = 12, 256   # the sign-in route caps at 256 too
EXPORT_EVERY_S = 600           # one ZIP per user per 10 minutes; in-process, resets on restart
_last_export = {}              # user_id -> time.monotonic() of the last export

EXPORT_WAIT_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1">'
    f'<meta http-equiv="refresh" content="8;url={paths.URL_PREFIX}/me/settings/">'
    '<title>One export every 10 minutes — blade-book</title>'
    '<style>body{font-family:system-ui,sans-serif;background:#f6f1e7;color:#1a1a1a;margin:0;'
    'display:flex;min-height:100vh;align-items:center;justify-content:center}'
    'main{text-align:center;padding:2rem}h1{font-size:1.6rem;margin:0 0 .5rem}'
    'a{color:#1a1a1a;font-weight:600}p{margin:.4rem 0}</style></head><body><main>'
    '<h1>One export every 10 minutes</h1>'
    '<p>one export every 10 minutes — try again shortly</p>'
    f'<p><a href="{paths.URL_PREFIX}/me/settings/">Back to settings</a></p>'
    '<p style="color:#666;font-size:.9rem">Taking you there in 8 seconds.</p>'
    '</main></body></html>'
)


def _view(u):
    return {'handle': u['handle'],
            'public_url': auth.base_url() + paths.URL_PREFIX + '/@' + u['handle'],
            'hide_born_day': u['hide_born_day'],
            'profile_private': u['profile_private'],
            'share_email_on_intro': u['share_email_on_intro'],
            'has_key': bool(u.get('public_key')),
            'featured_knife_id': u.get('featured_knife_id'),
            'email': u['email'] if mail.deliverable(u['email']) else None,
            'sign_in': 'password' if u.get('password_hash') else 'email',
            'has_password': bool(u.get('password_hash')),
            'created': u['created'],
            'handle_changed_at': u.get('handle_changed_at'),
            'can_change_handle': not u.get('handle_changed_at'),
            'is_admin': u['is_admin']}


@bp.get('/', strict_slashes=False)
@auth.login_required
def get_settings():
    return jsonify(_view(g.user))


@bp.patch('/', strict_slashes=False)
@auth.login_required
def patch_settings():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    fields = {}
    for name in ('hide_born_day', 'profile_private', 'share_email_on_intro'):
        if name in body:
            if not isinstance(body[name], bool):
                return jsonify({'error': f'{name} must be true or false'}), 400
            fields[name] = 1 if body[name] else 0
    if 'public_key' in body:
        key = body['public_key']
        if key is not None and not isinstance(key, str):
            return jsonify({'error': 'public_key must be a string'}), 400
        key = (key or '').strip()
        if len(key) > MAX_KEY:
            return jsonify({'error': f'public_key over {MAX_KEY} characters'}), 400
        fields['public_key'] = key or None
    new_handle = None
    if 'handle' in body:
        if not isinstance(body['handle'], str):
            return jsonify({'error': 'handle must be a string'}), 400
        new_handle = body['handle']
    bad = set(body) - db.SETTINGS_COLUMNS - {'handle'}
    if bad:
        return jsonify({'error': f'unknown setting: {sorted(bad)}'}), 400
    con = db.connect()
    try:
        if 'featured_knife_id' in body:
            val = body['featured_knife_id']
            knife = None
            if val is not None:
                if isinstance(val, int) and not isinstance(val, bool):
                    knife = db.get_knife(con, g.user['id'], val)
                if not knife or knife['status'] != 'live' or not knife['photos']:
                    return jsonify({'error': 'featured_knife_id must be one of your '
                                             'live knives with a photo'}), 400
            fields['featured_knife_id'] = val
        u = g.user
        if new_handle is not None:
            try:
                u = account.change_handle(con, g.user, new_handle)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        u = db.set_user_settings(con, g.user['id'], fields)
    finally:
        con.close()
    if fields or new_handle is not None:
        publish.schedule(g.user['id'])
        log.info('settings changed for @%s: %s', u['handle'],
                 sorted(fields) + (['handle'] if new_handle is not None else []))
    return jsonify(_view(u))


# --- a password on any account (2026-10-19) --------------------------------------

@bp.post('/password')
@auth.login_required
def set_password():
    """Set, replace or remove (password: null) the handle+password door on the
    signed-in account, so the owner can sign in where their email is out of
    reach. The magic link keeps working and is the only reset path, so an
    invited account (no deliverable email) may not remove its only door."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or 'password' not in body:
        return jsonify({'error': 'send {"password": "..."} or {"password": null}'}), 400
    password = body['password']
    if password is not None:
        if not isinstance(password, str) or not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
            return jsonify({'error': f'password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters'}), 400
    elif not mail.deliverable(g.user['email']):
        return jsonify({'error': 'this account has no email on file, so the password is '
                                 'its only way in — it cannot be removed'}), 400
    con = db.connect()
    try:
        db.set_password(con, g.user['id'], password)
        u = db.get_user(con, g.user['id'])
    finally:
        con.close()
    log.info('password %s for @%s', 'set' if password is not None else 'removed', u['handle'])
    return jsonify(_view(u))


# --- passkeys (2026-10-19) -------------------------------------------------------------
# Registration is a two-step dance bound to the browser by the bb_passkey flow
# cookie (challenge, 5 minutes, single use in this browser). The sign-in half lives in
# routes/auth.py. Unavailable library → 503 and the page hides the button.

PASSKEY_NAME_MAX = 40


def _passkeys_view(con):
    return {'available': passkeys.available(),
            'passkeys': db.passkeys_for_user(con, g.user['id'])}


@bp.get('/passkeys', strict_slashes=False)      # the page calls '/settings/passkeys/'; no 308 hop
@auth.login_required
def list_passkeys():
    con = db.connect()
    try:
        return jsonify(_passkeys_view(con))
    finally:
        con.close()


@bp.post('/passkeys/begin')
@auth.login_required
def passkey_register_begin():
    if not passkeys.available():
        return jsonify({'error': 'passkeys are not set up here'}), 503
    con = db.connect()
    try:
        if db.count_passkeys(con, g.user['id']) >= passkeys.MAX_PASSKEYS:
            return jsonify({'error': 'ten passkeys is the limit — remove one first'}), 400
        exclude = [r[0] for r in con.execute('SELECT credential_id FROM passkeys WHERE user_id = ?',
                                             (g.user['id'],)).fetchall()]
    finally:
        con.close()
    challenge = passkeys.new_challenge()
    options = passkeys.registration_options(g.user['id'], g.user['handle'], g.user.get('display_name'),
                                            exclude, challenge)
    resp = jsonify({'options': json.loads(options)})
    return auth.set_flow_cookie(resp, passkeys.COOKIE, passkeys.encode_challenge(challenge),
                                passkeys.FLOW_TTL_S, path=auth.FLOW_PATH_API)


@bp.post('/passkeys/finish')
@auth.login_required
def passkey_register_finish():
    if not passkeys.available():
        return jsonify({'error': 'passkeys are not set up here'}), 503
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get('credential'), dict):
        return _clear_passkey_cookie(jsonify({'error': 'send the credential the browser produced'}), 400)
    name = body.get('name')
    if name is not None:
        if not isinstance(name, str) or len(name.strip()) > PASSKEY_NAME_MAX:
            return _clear_passkey_cookie(jsonify({'error': f'name must be {PASSKEY_NAME_MAX} characters or fewer'}), 400)
        name = name.strip() or None
    challenge = passkeys.decode_challenge(auth.flow_value(passkeys.COOKIE))
    if challenge is None:
        return _clear_passkey_cookie(jsonify({'error': 'that took too long — start again'}), 400)
    try:
        reg = passkeys.verify_registration(body['credential'], challenge)
    except passkeys.PasskeyError as e:
        log.warning('passkey registration failed for @%s: %s', g.user['handle'], e)
        return _clear_passkey_cookie(jsonify({'error': f'could not verify that passkey: {e}'}), 400)
    con = db.connect()
    try:
        try:
            db.add_passkey(con, g.user['id'], reg.credential_id, reg.public_key, reg.sign_count,
                           reg.transports, name or passkeys.device_name(request.headers.get('User-Agent')))
        except sqlite3.IntegrityError:
            return _clear_passkey_cookie(jsonify({'error': 'that passkey is already registered'}), 400)
        view = _passkeys_view(con)
    finally:
        con.close()
    log.info('passkey added for @%s (%s)', g.user['handle'], view['passkeys'][-1]['name'])
    return _clear_passkey_cookie(jsonify(view), 200)


@bp.delete('/passkeys/<int:passkey_id>')
@auth.login_required
def passkey_remove(passkey_id):
    con = db.connect()
    try:
        if not db.remove_passkey(con, g.user['id'], passkey_id):
            return jsonify({'error': 'no such passkey'}), 404
        view = _passkeys_view(con)
    finally:
        con.close()
    log.info('passkey removed for @%s (id %s)', g.user['handle'], passkey_id)
    return jsonify(view)


def _clear_passkey_cookie(resp, status):
    resp.status_code = status
    return auth.clear_flow_cookie(resp, passkeys.COOKIE, path=auth.FLOW_PATH_API)


# --- account operations (plan 11) --------------------------------------------------

@bp.get('/export.csv')
@auth.login_required
def export_csv():
    """The register as one CSV — the "take my data" button. Never gated, no
    rate limit (no photos, no disk), same columns as the ZIP's knives.csv."""
    con = db.connect()
    try:
        text = account.export_csv(con, g.user)
    finally:
        con.close()
    return (text, 200, {
        'Content-Type': 'text/csv; charset=utf-8',
        'Content-Disposition': f'attachment; filename="blade-book-{g.user["handle"]}.csv"',
        'Cache-Control': 'no-store'})


@bp.get('/export')
@auth.login_required
def export():
    """Everything the owner has, as a ZIP. Never gated (spec §10). One per 10
    minutes per user — the ZIP is built on disk in DATA_DIR/exports and
    unlinked once the response is closed."""
    uid = g.user['id']
    now = time.monotonic()
    last = _last_export.get(uid)
    if last is not None and now - last < EXPORT_EVERY_S:
        msg = 'one export every 10 minutes — try again shortly'
        if auth._wants_html():
            return (EXPORT_WAIT_HTML, 429, {'Content-Type': 'text/html; charset=utf-8'})
        return jsonify({'error': msg}), 429
    _last_export[uid] = now
    con = db.connect()
    try:
        path = account.export_zip(con, current_app.config['STORE'], g.user,
                                  os.path.join(paths.DATA_DIR, 'exports'))
    finally:
        con.close()
    resp = send_file(path, mimetype='application/zip', as_attachment=True,
                     download_name=f"blade-book-{g.user['handle']}.zip", max_age=0)
    # send_file sets direct_passthrough=True, which makes Response.get_app_iter()
    # hand the WSGI server the raw file wrapper instead of
    # ClosingIterator(iterable, self.close) — so the server's close() on the
    # app_iter never reaches this response's own close(), and call_on_close
    # below would silently never fire (verified against Werkzeug 3.1.8). Turn
    # passthrough off so our unlink runs when the response is closed.
    resp.direct_passthrough = False
    resp.call_on_close(lambda: _unlink_quiet(path))
    return resp


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@bp.post('/delete')
@auth.login_required
def delete():
    """Complete account deletion. Type-the-handle confirm; admin rows refuse
    (Simon's account is not deletable from the UI); the session is cleared."""
    if g.user['is_admin']:
        return jsonify({'error': 'the admin account cannot be deleted here'}), 403
    body = request.get_json(silent=True)
    confirm = body.get('confirm') if isinstance(body, dict) else None
    if confirm != g.user['handle']:
        return jsonify({'error': 'type your handle exactly to confirm'}), 400
    handle = g.user['handle']
    con = db.connect()
    try:
        counts = account.delete_account(con, current_app.config['STORE'], g.user)
    finally:
        con.close()
    auth.logout()
    if counts.get('store_failed') or not counts.get('surface_removed'):
        log.error('delete for @%s left residue: %s', handle, counts)
    return jsonify({'ok': True, 'deleted': counts})
