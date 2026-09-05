# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/routes/auth.py — /blade-book/api/auth/*: magic link, whoami, sign-out.
OIDC (google/apple) routes are added in the same blueprint by plan 02 task 7.
"""
import logging
import re
import secrets

from flask import Blueprint, current_app, g, jsonify, redirect, request

from bb import auth, db, mail, oidc, paths

log = logging.getLogger('blade-book.auth')

bp = Blueprint('auth', __name__, url_prefix=paths.API_PREFIX + '/auth')

EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
LANDING = paths.URL_PREFIX + '/'


def _landing(**qs):
    if qs:
        return redirect(LANDING + '?' + '&'.join(f'{k}={v}' for k, v in qs.items()))
    return redirect(LANDING)


FLOW_COOKIE_PATH = paths.API_PREFIX + '/auth'


def _flow_cookie(resp, name, value, max_age):
    """A browser-binding cookie for a sign-in flow (review M2). Scoped to the
    auth routes, HttpOnly, and — because Apple's form_post callback is a
    cross-site POST — SameSite=None (which browsers only honour with Secure,
    hence the https check; plain-http test runs fall back to Lax)."""
    secure = auth.base_url().startswith('https')
    resp.set_cookie(name, value, max_age=max_age, path=FLOW_COOKIE_PATH, httponly=True,
                    secure=secure, samesite='None' if secure else 'Lax')
    return resp


def _clear_flow_cookie(resp, name):
    resp.delete_cookie(name, path=FLOW_COOKIE_PATH)
    return resp


@bp.post('/magic')
def request_magic_link():
    body = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
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
        cookie = request.cookies.get('bb_magic')
        bound = bool(cookie) and peek['flow'] is not None and db._sha(cookie) == peek['flow']
        if not bound:
            # opened somewhere else than where it was requested (another device —
            # or an attacker's link in a victim's browser, review M2): ask first
            current = auth.current_user(con)
            log.info('magic link for %s opened unbound from %s (signed in: %s)',
                     peek['email'], auth.client_ip(), current['handle'] if current else '-')
            html = auth.confirm_signin_html(peek['email'], token, current['handle'] if current else None)
            return html, 200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}
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
        return _landing(auth='expired')
    log.info('magic link sign-in (confirmed): %s (@%s)', user['email'], user['handle'])
    return _clear_flow_cookie(_landing(), 'bb_magic')


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
        bound = request.cookies.get('bb_oidc') == state
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
