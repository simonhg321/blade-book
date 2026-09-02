# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/routes/wants.py — /blade-book/api/wants/*: user wishlists."""
import logging

from flask import Blueprint, g, jsonify, request

from bb import auth, db, paths

log = logging.getLogger('blade-book.wants')

bp = Blueprint('wants', __name__, url_prefix=paths.API_PREFIX + '/wants')


@bp.get('/', strict_slashes=False)
@auth.login_required
def list_wants():
    con = db.connect()
    try:
        wants = db.list_wants(con, g.user['id'])
    finally:
        con.close()
    return jsonify({'wants': wants})


@bp.post('/', strict_slashes=False)
@auth.login_required
def create_want():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    con = db.connect()
    try:
        try:
            want = db.create_want(con, g.user['id'], body)
        except ValueError as e:
            msg = str(e)
            if msg == 'too many wants':
                return jsonify({'error': msg}), 409
            else:
                return jsonify({'error': msg}), 400
    finally:
        con.close()
    log.info('want created for @%s: %s', g.user['handle'], want.get('model'))
    return jsonify(want), 200


@bp.patch('/<int:want_id>', strict_slashes=False)
@auth.login_required
def set_want_active(want_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    if 'active' not in body or not isinstance(body['active'], bool):
        return jsonify({'error': 'active must be true or false'}), 400
    con = db.connect()
    try:
        ok = db.set_want_active(con, g.user['id'], want_id, body['active'])
    finally:
        con.close()
    if not ok:
        return jsonify({'error': 'not found'}), 404
    return jsonify({'ok': True})


@bp.delete('/<int:want_id>', strict_slashes=False)
@auth.login_required
def delete_want(want_id):
    con = db.connect()
    try:
        ok = db.delete_want(con, g.user['id'], want_id)
    finally:
        con.close()
    if not ok:
        return jsonify({'error': 'not found'}), 404
    log.info('want %d deleted by @%s', want_id, g.user['handle'])
    return jsonify({'ok': True})
