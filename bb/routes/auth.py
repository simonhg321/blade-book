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


@bp.post('/magic')
def request_magic_link():
    body = request.get_json(silent=True) or {}
    email = (body.get('email') or '').strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        return jsonify({'error': 'enter a valid email'}), 400
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con, email)
        if reason:
            log.warning('rate limited %s from %s: %s', email, auth.client_ip(), reason)
            return jsonify({'error': reason}), 429
        token = db.create_magic_token(con, email, auth.client_ip())
    finally:
        con.close()
    link = f'{auth.base_url()}{paths.API_PREFIX}/auth/magic?t={token}'
    subject, text, html = mail.magic_link_message(link)
    current_app.config['MAILER'].send(email, subject, text, html)
    log.info('magic link requested for %s from %s', email, auth.client_ip())
    return jsonify({'ok': True}), 202


@bp.get('/magic')
def click_magic_link():
    token = request.args.get('t', '')
    con = db.connect()
    try:
        email = db.consume_magic_token(con, token) if token else None
        if email is None:
            return _landing(auth='expired')
        user = auth.sign_in_by_email(con, email)
    finally:
        con.close()
    log.info('magic link sign-in: %s (@%s)', user['email'], user['handle'])
    return _landing()


@bp.get('/me')
@auth.login_required
def me():
    return jsonify(auth.public_user(g.user))


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
    return redirect(oidc.authorize_url(provider, state, nonce))


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
        if params.get('error') or not code or saved is None or saved['provider'] != provider.name:
            log.warning('%s callback rejected: error=%s code=%s state_ok=%s',
                        provider.name, params.get('error'), bool(code), saved is not None)
            return _landing(auth='failed')
        try:
            claims = oidc.exchange_code(provider, code, saved['nonce'])
        except oidc.OIDCError as e:
            log.warning('%s exchange failed: %s', provider.name, e)
            return _landing(auth='failed')
        if not claims['email_verified']:
            log.warning('%s sign-in rejected: email not verified by provider', provider.name)
            return _landing(auth='failed')
        user = auth.sign_in_by_email(con, claims['email'], provider=provider.name,
                                     sub=claims['sub'])
    finally:
        con.close()
    log.info('%s sign-in: %s (@%s)', provider.name, user['email'], user['handle'])
    return _landing()
