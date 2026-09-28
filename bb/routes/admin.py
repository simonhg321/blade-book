# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/routes/admin.py — /blade-book/api/admin/*: the report queue and the
three moderation verbs (spec §9 abuse: "Admin can hide, restore, or delete
with a note"). Plan 10 added users + the ManualBilling flip; 2026-09-27 added
the activity view."""
import logging
import os
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request

from bb import activity, auth, db, edit, paths, publish
from bb.routes.knives import _delete_keys

log = logging.getLogger('blade-book.admin')

bp = Blueprint('admin', __name__, url_prefix=paths.API_PREFIX + '/admin')

MAX_NOTE = 500


@bp.get('/reports', strict_slashes=False)
@auth.admin_required
def list_reports():
    con = db.connect()
    try:
        rows = db.open_reports(con)
    finally:
        con.close()
    return jsonify({'reports': rows})


def _note():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise edit.EditError('body must be a JSON object')
    note = edit._text(body.get('note'), MAX_NOTE, 'note')
    if not note:
        raise edit.EditError('a note is required')
    return note


@bp.post('/knives/<int:knife_id>/<action>')
@auth.admin_required
def moderate(knife_id, action):
    if action not in ('hide', 'restore', 'delete'):
        return jsonify({'error': 'not found'}), 404
    try:
        note = _note()
    except edit.EditError as e:
        return jsonify({'error': str(e)}), 400
    con = db.connect()
    try:
        k = db.get_knife_any(con, knife_id)
        if k is None:
            return jsonify({'error': 'not found'}), 404
        if action == 'hide':
            db.hide_knife(con, knife_id, 'admin', note)
            db.resolve_reports(con, knife_id, f'hidden: {note}')
        elif action == 'restore':
            db.restore_knife(con, knife_id)
            db.resolve_reports(con, knife_id, f'restored: {note}')
        else:
            db.resolve_reports(con, knife_id, f'deleted: {note}')     # audit row survives only in the log
            keys = db.delete_knife(con, k['owner_id'], knife_id)    # reports cascade with the knife
    finally:
        con.close()
    if action == 'delete':
        removed = _delete_keys(current_app.config['STORE'], keys or [], k['tag'])
        log.warning('admin @%s DELETED knife %d (%s, owner %d): %s (%d files)',
                    g.user['handle'], knife_id, k['tag'], k['owner_id'], note, removed)
    else:
        log.warning('admin @%s %s knife %d (%s, owner %d): %s',
                    g.user['handle'], action, knife_id, k['tag'], k['owner_id'], note)
    publish.schedule(k['owner_id'])
    return jsonify({'ok': True})


# --- users + subs (plan 10, ManualBilling) -----------------------------------

@bp.get('/users', strict_slashes=False)
@auth.admin_required
def list_users():
    con = db.connect()
    try:
        rows = db.admin_users(con)
    finally:
        con.close()
    return jsonify({'users': rows})


@bp.post('/users/<int:user_id>/sub')
@auth.admin_required
def set_sub(user_id):
    body = request.get_json(silent=True)
    status = body.get('status') if isinstance(body, dict) else None
    if status not in db.SUB_STATUSES:
        return jsonify({'error': 'status must be one of ' + ', '.join(db.SUB_STATUSES)}), 400
    con = db.connect()
    try:
        u = current_app.config['BILLING'].set_status(con, user_id, status)
        if u is None:
            return jsonify({'error': 'not found'}), 404
        row = next(r for r in db.admin_users(con) if r['id'] == user_id)
    finally:
        con.close()
    log.warning('admin @%s set sub_status=%s for @%s (user %d)', g.user['handle'], status, u['handle'], user_id)
    return jsonify({'ok': True, 'user': row})


# --- activity (2026-09-27) -----------------------------------------------------

@bp.get('/activity', strict_slashes=False)
@auth.admin_required
def activity_summary():
    """Who is using blade-book and where they are stuck. Read-only."""
    raw = request.args.get('hours', '48')
    hours = int(raw) if raw.isascii() and raw.isdigit() else 0
    if hours not in activity.WINDOWS:
        return jsonify({'error': 'hours must be one of ' + ', '.join(map(str, activity.WINDOWS))}), 400
    own = {request.host.split(':')[0].lower(), (urlsplit(os.environ.get('BASE_URL', '')).hostname or '').lower()} - {''}
    con = db.connect()
    try:
        out = activity.summary(con, hours, you=g.user['handle'], own_hosts=own)
    finally:
        con.close()
    return jsonify(out)
