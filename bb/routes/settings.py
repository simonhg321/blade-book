# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/routes/settings.py — the owner's public-page settings. The key is
write-only: the API reports has_key, never the key itself."""
import logging

from flask import Blueprint, g, jsonify, request

from bb import auth, db, paths, publish

log = logging.getLogger('blade-book.settings')

bp = Blueprint('settings', __name__, url_prefix=paths.API_PREFIX + '/settings')

MAX_KEY = 64


def _view(u):
    return {'handle': u['handle'],
            'public_url': auth.base_url() + paths.URL_PREFIX + '/@' + u['handle'],
            'hide_born_day': u['hide_born_day'],
            'profile_private': u['profile_private'],
            'share_email_on_intro': u['share_email_on_intro'],
            'has_key': bool(u.get('public_key'))}


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
    bad = set(body) - {'hide_born_day', 'profile_private', 'public_key', 'share_email_on_intro'}
    if bad:
        return jsonify({'error': f'unknown setting: {sorted(bad)}'}), 400
    con = db.connect()
    try:
        u = db.set_user_settings(con, g.user['id'], fields)
    finally:
        con.close()
    if fields:
        publish.schedule(g.user['id'])
        log.info('settings changed for @%s: %s', u['handle'], sorted(fields))
    return jsonify(_view(u))
