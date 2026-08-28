# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/routes/knives.py — /blade-book/api/knives/*: drafts, notes, photo slots.
Every route is login_required and every db call carries g.user['id'], so a
knife you don't own is indistinguishable from one that doesn't exist.
"""
import logging

from flask import Blueprint, Response, current_app, g, jsonify, request

from bb import auth, db, paths, photos

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


# --- photo slots ---------------------------------------------------------------

def _slot_ok(seq):
    return 1 <= seq <= 3


@bp.post('/<int:knife_id>/photos/<int:seq>')
@auth.login_required
def upload_photo(knife_id, seq):
    """Upload a photo to a slot.
    ?replace=1 swaps a slot in one request: ingest first, then delete the old photo,
    then insert — a rejected file never touches the existing photo."""
    if not _slot_ok(seq):
        return jsonify({'error': 'slot must be 1, 2 or 3'}), 400
    f = request.files.get('photo')
    if f is None or not f.filename:
        return jsonify({'error': 'no photo'}), 400
    data = f.read()
    try:
        ing = photos.ingest(data, f.filename)
    except photos.TooBig:
        return jsonify({'error': f'photo over {photos.MAX_PHOTO_BYTES // (1024 * 1024)} MB'}), 400
    except photos.BadType:
        return jsonify({'error': 'not an accepted image type'}), 415
    owner = g.user['id']
    key = f'{owner}/{knife_id}/{seq}.{ing.ext}'
    tkey = db.thumb_key(key)
    store = _store()
    con = db.connect()
    # Row first, files second — UNIQUE(knife_id, seq) is the lock, so a 409 never
    # touches the store; a failed write deletes its own row.
    try:
        if db.get_knife(con, owner, knife_id) is None:
            return _not_found()
        if request.args.get('replace') == '1':
            old = db.delete_photo(con, owner, knife_id, seq)
            if old:
                _delete_keys(store, [old['store_key'], db.thumb_key(old['store_key'])],
                             f'replace {knife_id}/{seq}')
        try:
            db.add_photo(con, owner, knife_id, seq, key, ing.sha256, ing.width, ing.height)
        except db.SlotTaken:
            return jsonify({'error': f'slot {seq} is taken — delete it first'}), 409
        try:
            store.put(key, data)
            if ing.thumb:
                store.put(tkey, ing.thumb)
        except Exception:  # noqa: BLE001 — row must not outlive a failed write
            log.exception('store write failed for %s; rolling back photo row', key)
            db.delete_photo(con, owner, knife_id, seq)
            _delete_keys(store, [key, tkey], f'failed upload {key}')
            return jsonify({'error': 'could not store the photo — try again'}), 500
    finally:
        con.close()
    log.info('photo %d/%d stored for @%s (%s, thumb=%s)', knife_id, seq, g.user['handle'],
             ing.ext, bool(ing.thumb))
    return jsonify({'seq': seq, 'sha256': ing.sha256, 'width': ing.width,
                    'height': ing.height, 'has_thumb': bool(ing.thumb)}), 201


@bp.delete('/<int:knife_id>/photos/<int:seq>')
@auth.login_required
def delete_photo(knife_id, seq):
    con = db.connect()
    try:
        row = db.delete_photo(con, g.user['id'], knife_id, seq)
    finally:
        con.close()
    if row is None:
        return _not_found()
    store = _store()
    _delete_keys(store, [row['store_key'], db.thumb_key(row['store_key'])],
                 f'photo {knife_id}/{seq}')
    return jsonify({'ok': True})


def _photo_or_404(knife_id, seq):
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
        p = db.get_photo(con, g.user['id'], knife_id, seq)
    finally:
        con.close()
    return (k, p) if (k and p) else (None, None)


@bp.get('/<int:knife_id>/photos/<int:seq>/thumb')
@auth.login_required
def photo_thumb(knife_id, seq):
    _, p = _photo_or_404(knife_id, seq)
    if p is None:
        return _not_found()
    try:
        data = _store().get(db.thumb_key(p['store_key']))
    except KeyError:
        return _not_found()
    return Response(data, mimetype='image/jpeg',
                    headers={'Cache-Control': 'private, max-age=3600'})


@bp.get('/<int:knife_id>/photos/<int:seq>/original')
@auth.login_required
def photo_original(knife_id, seq):
    k, p = _photo_or_404(knife_id, seq)
    if p is None:
        return _not_found()
    try:
        data = _store().get(p['store_key'])
    except KeyError:
        return _not_found()
    ext = p['store_key'].rsplit('.', 1)[-1]
    return Response(data, mimetype=photos.MIME.get(ext, 'application/octet-stream'),
                    headers={'Cache-Control': 'private, max-age=3600',
                             'Content-Disposition': f'inline; filename="{k["tag"]}-{seq}.{ext}"'})
