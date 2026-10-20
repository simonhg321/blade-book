# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""
bb/routes/auth.py — /blade-book/api/auth/*: magic link, whoami, sign-out.
OIDC (google/apple) routes are added in the same blueprint by plan 02 task 7.
"""
import base64
import json
import logging
import re
import secrets

from flask import Blueprint, current_app, g, jsonify, redirect, request

from bb import auth, db, mail, oidc, passkeys, paths

log = logging.getLogger('blade-book.auth')

bp = Blueprint('auth', __name__, url_prefix=paths.API_PREFIX + '/auth')

EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
LANDING = paths.URL_PREFIX + '/'


def _landing(**qs):
    if qs:
        return redirect(LANDING + '?' + '&'.join(f'{k}={v}' for k, v in qs.items()))
    return redirect(LANDING)


# flow cookies live in bb/auth.py since 2026-10-19 (shared with the settings
# blueprint for passkey registration); these names keep this file unchanged.
FLOW_COOKIE_PATH = auth.FLOW_PATH_AUTH
_flow_cookie_name = auth.flow_cookie_name
_flow_cookie = auth.set_flow_cookie
_clear_flow_cookie = auth.clear_flow_cookie
_flow_value = auth.flow_value


def _ct_equal(a, b):
    """Constant-time compare of two request-supplied strings. Either side can
    be anything a client chose to send and secrets.compare_digest raises on
    str that isn't ASCII — so compare the encoded bytes and treat anything
    that isn't a plain encodable string as simply not equal."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    try:
        return secrets.compare_digest(a.encode('utf-8'), b.encode('utf-8'))
    except UnicodeEncodeError:
        return False


@bp.post('/magic')
def request_magic_link():
    body = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254 or not mail.deliverable(email):
        return jsonify({'error': 'enter a valid email'}), 400
    flow = secrets.token_urlsafe(24)
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con, email)
        if reason:
            log.warning('rate limited %s from %s: %s', email, auth.client_ip(), reason)
            return jsonify({'error': reason}), 429
        token = db.create_magic_token(con, email, auth.client_ip(), flow=db._sha(flow))
    finally:
        con.close()
    link = f'{auth.base_url()}{paths.API_PREFIX}/auth/magic?t={token}'
    subject, text, html = mail.magic_link_message(link)
    try:
        current_app.config['MAILER'].send(email, subject, text, html)
    except Exception:
        log.exception('magic link send failed for %s', email)
        return jsonify({'error': 'could not send the email — try again in a minute'}), 502
    log.info('magic link requested for %s from %s', email, auth.client_ip())
    resp = jsonify({'ok': True})
    resp.status_code = 202
    return _flow_cookie(resp, 'bb_magic', flow, db.MAGIC_TTL_MIN * 60)


def _finish_magic_sign_in(con, token):
    """Burn the token and sign in; None when the token is dead."""
    email = db.consume_magic_token(con, token) if token else None
    if email is None:
        return None
    return auth.sign_in_by_email(con, email)


@bp.get('/magic')
def click_magic_link():
    token = request.args.get('t', '')
    con = db.connect()
    try:
        peek = db.peek_magic_token(con, token) if token else None
        if peek is None:
            return _clear_flow_cookie(_landing(auth='expired'), 'bb_magic')
        cookie = _flow_value('bb_magic')
        bound = cookie is not None and _ct_equal(db._sha(cookie), peek['flow'])
        if not bound:
            # opened somewhere else than where it was requested (another device —
            # or an attacker's link in a victim's browser, review M2): ask first
            current = auth.current_user(con)
            log.info('magic link for %s opened unbound from %s (signed in: %s)',
                     peek['email'], auth.client_ip(), current['handle'] if current else '-')
            html = auth.confirm_signin_html(peek['email'], token, current['handle'] if current else None)
            return html, 200, {'Content-Type': 'text/html; charset=utf-8',
                               'Cache-Control': 'no-store', 'X-Frame-Options': 'DENY'}
        user = _finish_magic_sign_in(con, token)
        if user is None:
            return _clear_flow_cookie(_landing(auth='expired'), 'bb_magic')
    finally:
        con.close()
    log.info('magic link sign-in: %s (@%s)', user['email'], user['handle'])
    return _clear_flow_cookie(_landing(), 'bb_magic')


@bp.post('/magic/confirm')
def confirm_magic_link():
    """The confirm page's form. Form-encoded on purpose (no JS on that page);
    the same-origin gate in app.py is what makes this safe to accept."""
    token = request.form.get('t', '')
    con = db.connect()
    try:
        user = _finish_magic_sign_in(con, token)
    finally:
        con.close()
    if user is None:
        return _clear_flow_cookie(_landing(auth='expired'), 'bb_magic')
    log.info('magic link sign-in (confirmed): %s (@%s)', user['email'], user['handle'])
    return _clear_flow_cookie(_landing(), 'bb_magic')


@bp.post('/password')
def password_sign_in():
    """Handle + password, for INVITED accounts only (2026-09-23). Nothing here
    creates an account — scripts/invite.py on the box is the only way to get a
    password_hash, so this route cannot be used to register without an email.
    Unknown handle and wrong password answer identically; the scrypt check runs
    either way so timing does not tell them apart."""
    body = request.get_json(silent=True) or {}
    handle = (body.get('handle') or '').strip().lower().lstrip('@')
    password = body.get('password') or ''
    if not handle or not password or len(password) > 256:
        return jsonify({'error': 'enter your handle and password'}), 400
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con)
        if reason is None and auth.password_locked(con, handle):
            reason = 'too many tries for that handle — try again in an hour'
        if reason:
            log.warning('password sign-in rate limited for @%s from %s: %s', handle, auth.client_ip(), reason)
            return jsonify({'error': reason}), 429
        user = db.get_user_by_handle(con, handle)
        stored = user['password_hash'] if user else None
        if not auth.verify_password(password, stored or auth.DUMMY_HASH):
            auth.record_password_failure(con, handle)
            log.info('password sign-in failed for @%s from %s', handle, auth.client_ip())
            return jsonify({'error': 'wrong handle or password'}), 401
        auth.login(con, user)
    finally:
        con.close()
    log.info('password sign-in: @%s from %s', user['handle'], auth.client_ip())
    return jsonify(auth.self_view(user))


# --- passkeys (2026-10-19) -------------------------------------------------------------
# One button, no handle: the browser finds the passkey itself (discoverable
# credential). The challenge rides in the bb_passkey flow cookie (300 s, single
# use in this browser, path /blade-book/api so the settings blueprint shares it). Every failure
# is one generic 401; the reason goes to the log.

PASSKEY_FAIL = {'error': 'that passkey did not work'}


def _passkey_refuse(reason, handle='?'):
    if 'sign count' in reason.lower():
        reason += ' — possible cloned authenticator; the owner can remove it under settings'
    log.warning('passkey sign-in refused for @%s from %s: %s', handle, auth.client_ip(), reason)
    resp = jsonify(PASSKEY_FAIL)
    resp.status_code = 401
    return auth.clear_flow_cookie(resp, passkeys.COOKIE, path=auth.FLOW_PATH_API)


@bp.post('/passkey/begin')
def passkey_sign_in_begin():
    if not passkeys.available():
        return jsonify({'error': 'passkeys are not set up here'}), 503
    if request.host.split(':', 1)[0] != passkeys.rp_id():
        return jsonify({'error': 'passkeys work on ' + passkeys.rp_id() + ' only'}), 404
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con)
    finally:
        con.close()
    if reason:
        log.warning('passkey begin rate limited from %s: %s', auth.client_ip(), reason)
        return jsonify({'error': reason}), 429
    challenge = passkeys.new_challenge()
    resp = jsonify({'options': json.loads(passkeys.authentication_options(challenge))})
    return auth.set_flow_cookie(resp, passkeys.COOKIE, passkeys.encode_challenge(challenge),
                                passkeys.FLOW_TTL_S, path=auth.FLOW_PATH_API)


@bp.post('/passkey/finish')
def passkey_sign_in_finish():
    if not passkeys.available():
        return jsonify({'error': 'passkeys are not set up here'}), 503
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con)
        if reason:
            log.warning('passkey finish rate limited from %s: %s', auth.client_ip(), reason)
            return jsonify({'error': reason}), 429
        body = request.get_json(silent=True)
        cred = body.get('credential') if isinstance(body, dict) else None
        if not isinstance(cred, dict) or not isinstance(cred.get('id'), str) or len(cred['id']) > 1024:
            return _passkey_refuse('malformed body')
        challenge = passkeys.decode_challenge(auth.flow_value(passkeys.COOKIE))
        if challenge is None:
            return _passkey_refuse('no challenge cookie (expired, replayed or missing)')
        try:
            cred_id = base64.urlsafe_b64decode(cred['id'] + '=' * (-len(cred['id']) % 4))
        except (ValueError, TypeError):
            return _passkey_refuse('credential id is not base64url')
        row = db.passkey_by_credential_id(con, cred_id)
        if row is None:
            return _passkey_refuse('unknown credential')
        user = db.get_user(con, row['user_id'])
        if user is None:
            return _passkey_refuse('credential without a user')
        try:
            new_count = passkeys.verify_authentication(cred, challenge, row['public_key'], row['sign_count'])
        except passkeys.PasskeyError as e:
            return _passkey_refuse(str(e), user['handle'])
        uh = (cred.get('response') or {}).get('userHandle') if isinstance(cred.get('response'), dict) else None
        if uh:
            try:
                uh_bytes = base64.urlsafe_b64decode(uh + '=' * (-len(uh) % 4))
            except (ValueError, TypeError):
                uh_bytes = b''
            if uh_bytes != str(user['id']).encode('ascii'):
                return _passkey_refuse('user handle does not match the credential owner', user['handle'])
        db.touch_passkey(con, row['id'], new_count)
        auth.login(con, user)
    finally:
        con.close()
    log.info('passkey sign-in: @%s from %s (passkey %s)', user['handle'], auth.client_ip(), row['id'])
    resp = jsonify(auth.self_view(user))
    return auth.clear_flow_cookie(resp, passkeys.COOKIE, path=auth.FLOW_PATH_API)


@bp.get('/me')
@auth.login_required
def me():
    return jsonify(auth.self_view(g.user))


@bp.post('/signout')
def signout():
    auth.logout()
    return jsonify({'ok': True})


@bp.post('/signout-all')
@auth.login_required
def signout_all():
    con = db.connect()
    try:
        auth.logout_everywhere(con, g.user['id'])
    finally:
        con.close()
    return jsonify({'ok': True})


# --- OIDC: Google (query callback) + Apple (form_post callback) ---------------

@bp.get('/providers')
def providers():
    return jsonify({'providers': oidc.configured()})


@bp.get('/<name>')
def oidc_start(name):
    provider = oidc.get(name)
    if provider is None:
        return jsonify({'error': 'not found'}), 404
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con)
        if reason:
            return jsonify({'error': reason}), 429
        nonce = secrets.token_urlsafe(16)
        state = db.create_oauth_state(con, provider.name, nonce)
    finally:
        con.close()
    resp = redirect(oidc.authorize_url(provider, state, nonce))
    return _flow_cookie(resp, 'bb_oidc', state, db.STATE_TTL_MIN * 60)


@bp.route('/<name>/callback', methods=['GET', 'POST'])
def oidc_callback(name):
    provider = oidc.get(name)
    if provider is None:
        return jsonify({'error': 'not found'}), 404
    params = request.form if request.method == 'POST' else request.args
    code, state = params.get('code'), params.get('state')
    con = db.connect()
    try:
        saved = db.pop_oauth_state(con, state) if state else None
        cookie = _flow_value('bb_oidc')
        bound = cookie is not None and _ct_equal(cookie, state)
        if params.get('error') or not code or saved is None or saved['provider'] != provider.name or not bound:
            log.warning('%s callback rejected: error=%r code=%s state_ok=%s browser_bound=%s',
                        provider.name, str(params.get('error'))[:64], bool(code),
                        saved is not None, bound)
            return _clear_flow_cookie(_landing(auth='failed'), 'bb_oidc')
        try:
            claims = oidc.exchange_code(provider, code, saved['nonce'])
        except oidc.OIDCError as e:
            log.warning('%s exchange failed: %s', provider.name, e)
            return _clear_flow_cookie(_landing(auth='failed'), 'bb_oidc')
        if not claims['email_verified']:
            log.warning('%s sign-in rejected: email not verified by provider', provider.name)
            return _clear_flow_cookie(_landing(auth='unverified'), 'bb_oidc')
        user = auth.sign_in_by_email(con, claims['email'], provider=provider.name,
                                     sub=claims['sub'])
    finally:
        con.close()
    log.info('%s sign-in: %s (@%s)', provider.name, user['email'], user['handle'])
    return _clear_flow_cookie(_landing(), 'bb_oidc')
