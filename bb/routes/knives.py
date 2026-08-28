# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/routes/knives.py — /blade-book/api/knives/*: drafts, notes, photo slots.
Every route is login_required and every db call carries g.user['id'], so a
knife you don't own is indistinguishable from one that doesn't exist.
"""
import logging

from flask import Blueprint, current_app, g, jsonify, request

from bb import auth, db, paths

log = logging.getLogger('blade-book.knives')

bp = Blueprint('knives', __name__, url_prefix=paths.API_PREFIX + '/knives')

MAX_NOTE = 2000


def _store():
    return current_app.config['STORE']


def _not_found():
    return jsonify({'error': 'not found'}), 404


def _public_photo(p, store):
    return {'id': p['id'], 'seq': p['seq'], 'sha256': p['sha256'],
            'width': p['width'], 'height': p['height'],
            'has_thumb': store.exists(db.thumb_key(p['store_key']))}


def _public_knife(k, store):
    out = {key: v for key, v in k.items() if key != 'photos'}
    out['photos'] = [_public_photo(p, store) for p in k['photos']]
    return out


def _delete_keys(store, keys, what):
    """Best-effort file cleanup AFTER the DB is the source of truth. Never raises:
    a file we can't remove is logged and left for the purge/backup sweep, not
    turned into a 500 for a request whose DB side already succeeded."""
    removed = 0
    for key in keys:
        try:
            removed += bool(store.delete(key))
        except Exception:  # noqa: BLE001 — surfaced in the log, never swallowed silently
            log.exception('%s: could not delete %s', what, key)
    return removed


@bp.post('/')
@auth.login_required
def create_draft():
    con = db.connect()
    try:
        k = db.create_draft_knife(con, g.user['id'])
    finally:
        con.close()
    log.info('draft %s created for @%s', k['tag'], g.user['handle'])
    return jsonify(_public_knife(k, _store())), 201


@bp.get('/')
@auth.login_required
def list_knives():
    status = request.args.get('status') or None
    if status and status not in db.KNIFE_STATUSES:
        return jsonify({'error': 'bad status'}), 400
    con = db.connect()
    try:
        rows = db.list_knives(con, g.user['id'], status=status)
    finally:
        con.close()
    return jsonify({'knives': rows})


@bp.get('/<int:knife_id>')
@auth.login_required
def get_knife(knife_id):
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
    finally:
        con.close()
    if k is None:
        return _not_found()
    return jsonify(_public_knife(k, _store()))


@bp.put('/<int:knife_id>/note')
@auth.login_required
def set_note(knife_id):
    body = request.get_json(silent=True) or {}
    note = body.get('note')
    if note is None:
        note = ''
    if not isinstance(note, str):
        return jsonify({'error': 'note must be a string'}), 400
    text = note.strip()
    if len(text) > MAX_NOTE:
        return jsonify({'error': f'note over {MAX_NOTE} characters'}), 400
    con = db.connect()
    try:
        ok = db.set_knife_note(con, g.user['id'], knife_id, text)
    finally:
        con.close()
    return (jsonify({'ok': True}), 200) if ok else _not_found()


@bp.delete('/<int:knife_id>')
@auth.login_required
def delete_draft(knife_id):
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
        keys = db.delete_draft_knife(con, g.user['id'], knife_id)
    finally:
        con.close()
    if k is None or k['status'] != 'draft':
        return _not_found()
    store = _store()
    removed = _delete_keys(store, keys, f'draft {k["tag"]}')
    log.info('draft %s deleted by @%s (%d files)', k['tag'], g.user['handle'], removed)
    return jsonify({'ok': True})
