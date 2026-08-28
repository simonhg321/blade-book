# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/routes/knives.py — /blade-book/api/knives/*: drafts, notes, photo slots.
Every route is login_required and every db call carries g.user['id'], so a
knife you don't own is indistinguishable from one that doesn't exist.
"""
import logging

from flask import Blueprint, Response, current_app, g, jsonify, request

from bb import auth, db, decode, paths, photos
from bb.makers import core as maker_core

log = logging.getLogger('blade-book.knives')

bp = Blueprint('knives', __name__, url_prefix=paths.API_PREFIX + '/knives')

MAX_NOTE = 2000
MAX_OPEN_DRAFTS = 20
DRAFT_ONLY = 'photos can only change on a draft — plan 05 adds editing'
FREE_DECODES_PER_DAY = 20
PAID_DECODES_PER_DAY = 200


def _store():
    return current_app.config['STORE']


def _not_found():
    return jsonify({'error': 'not found'}), 404


def _owner_photo(p, store):
    """the signed-in owner's own view — includes private columns; never use for /@handle"""
    return {'id': p['id'], 'seq': p['seq'], 'sha256': p['sha256'],
            'width': p['width'], 'height': p['height'],
            'has_thumb': store.exists(db.thumb_key(p['store_key'])),
            'has_original': store.exists(p['store_key'])}


def _owner_knife(k, store):
    """the signed-in owner's own view — includes private columns; never use for /@handle"""
    out = {key: v for key, v in k.items() if key != 'photos'}
    out['photos'] = [_owner_photo(p, store) for p in k['photos']]
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
        if db.count_drafts(con, g.user['id']) >= MAX_OPEN_DRAFTS:
            return jsonify({'error': f'you have {MAX_OPEN_DRAFTS} open drafts — '
                                      'finish or discard some first'}), 429
        k = db.create_draft_knife(con, g.user['id'])
    finally:
        con.close()
    log.info('draft %s created for @%s', k['tag'], g.user['handle'])
    return jsonify(_owner_knife(k, _store())), 201


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
    return jsonify(_owner_knife(k, _store()))


@bp.put('/<int:knife_id>/note')
@auth.login_required
def set_note(knife_id):
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        body = {}
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


@bp.post('/<int:knife_id>/decode')
@auth.login_required
def decode_knife(knife_id):
    """⚡ PROCESS: the draft's photos + note → one model call → core/ext/
    confidence written onto the draft. Drafts only; metered per day; the
    record is written even when consistency rules flag it (flags returned)."""
    owner = g.user['id']
    body = request.get_json(silent=True)
    no_card = bool(body.get('no_card')) if isinstance(body, dict) else False
    decoder = current_app.config['DECODER']
    store = _store()
    con = db.connect()
    try:
        k = db.get_knife(con, owner, knife_id)
        if k is None:
            return _not_found()
        if k['status'] != 'draft':
            return jsonify({'error': 'decode only runs on a draft — plan 05 adds re-decode'}), 409
        if not k['photos']:
            return jsonify({'error': 'add the box + card photo first'}), 400
        if isinstance(decoder, decode.NoDecoder):
            return jsonify({'error': 'decoder not configured'}), 503
        cap = PAID_DECODES_PER_DAY if g.user.get('sub_status') == 'active' else FREE_DECODES_PER_DAY
        if db.decodes_today(con, owner) >= cap:
            return jsonify({'error': f'{cap} decodes today already — try again tomorrow'}), 429
        jpegs = decode.images_for(store, k)
        if not jpegs:
            return jsonify({'error': 'none of the photos are decodable — re-shoot as JPEG/HEIC'}), 400
        try:
            d = decoder.decode(jpegs, k.get('notes_private') or '', k['maker'], no_card=no_card)
        except decode.DecodeError as e:
            log.warning('decode failed for %s/%s: %s', g.user['handle'], k['tag'], e)
            decode.log_call(owner, knife_id, getattr(decoder, 'model', None), False, error=str(e)[:300])
            return jsonify({'error': 'the decoder failed — try again in a minute'}), 502
        decode.log_call(owner, knife_id, d.model, True, decoded=d)
        k2 = db.apply_decode(con, owner, knife_id, d)
    finally:
        con.close()
    log.info('decoded %s for @%s via %s (%d flags, %dms)', k['tag'], g.user['handle'], d.model,
             len(d.flags), d.latency_ms)
    out = _owner_knife(k2, store)
    out['decoded'] = {'flags': d.flags, 'reasoning': d.reasoning, 'card_text': d.card_text,
                      'no_card': d.no_card, 'model': d.model,
                      'age_months': maker_core.age_months(d.core.get('born_on'))}
    return jsonify(out)


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
    owner = g.user['id']
    store = _store()
    con = db.connect()
    # Row first, files second — UNIQUE(knife_id, seq) is the lock, so a 409 never
    # touches the store; a failed write deletes its own row.
    try:
        # Ownership + draft-status check BEFORE ingest — don't decode up to 20 MB
        # for a knife you don't own or can no longer edit.
        k = db.get_knife(con, owner, knife_id)
        if k is None:
            return _not_found()
        if k['status'] != 'draft':
            return jsonify({'error': DRAFT_ONLY}), 409
        data = f.read()
        try:
            ing = photos.ingest(data, f.filename)
        except photos.TooBig:
            return jsonify({'error': f'photo over {photos.MAX_PHOTO_BYTES // (1024 * 1024)} MB'}), 400
        except photos.BadType:
            return jsonify({'error': 'not an accepted image type'}), 415
        key = f'{owner}/{knife_id}/{seq}.{ing.ext}'
        tkey = db.thumb_key(key)
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
    owner = g.user['id']
    con = db.connect()
    try:
        k = db.get_knife(con, owner, knife_id)
        if k is None:
            return _not_found()
        if k['status'] != 'draft':
            return jsonify({'error': DRAFT_ONLY}), 409
        row = db.delete_photo(con, owner, knife_id, seq)
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
