# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/routes/auth.py — /blade-book/api/auth/*: magic link, whoami, sign-out.
OIDC (google/apple) routes are added in the same blueprint by plan 02 task 7.
"""
import logging
import re

from flask import Blueprint, current_app, g, jsonify, redirect, request

from bb import auth, db, mail, paths

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
