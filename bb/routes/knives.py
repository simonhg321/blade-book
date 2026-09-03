# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/routes/knives.py — /blade-book/api/knives/*: drafts, notes, photo slots.
Every route is login_required and every db call carries g.user['id'], so a
knife you don't own is indistinguishable from one that doesn't exist.
"""
import logging
import time

from flask import Blueprint, Response, current_app, g, jsonify, request

from bb import auth, billing, db, decode, edit, makers, paths, photos, publish
from bb.makers import core as maker_core

log = logging.getLogger('blade-book.knives')

bp = Blueprint('knives', __name__, url_prefix=paths.API_PREFIX + '/knives')

MAX_NOTE = 2000
MAX_OPEN_DRAFTS = 20
MAX_SELLER_NOTE = 500
MAX_BULK = 200
LIVE_KEEPS_ONE = 'a live knife keeps at least one photo'
FREE_DECODES_PER_DAY = 20
PAID_DECODES_PER_DAY = 200


def _store():
    return current_app.config['STORE']


def _not_found():
    return jsonify({'error': 'not found'}), 404


def _norm_cmp(x):
    """'' and NULL are the same absence for change-detection: an empty core field
    from validate() must not read as a change against a never-decoded NULL."""
    return '' if x is None else x


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


def _register_view(k, store):
    out = _owner_knife(k, store)
    out['flags'] = edit.flags_for(k)
    out['age_months'] = maker_core.age_months(k.get('born_on'))
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
        tag = db.undecoded_draft_tag(con, g.user['id'])
        if tag is not None:
            return jsonify({'error': f'process (or discard) {tag} before adding another knife'}), 409
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


@bp.get('/full')
@auth.login_required
def full_register():
    """The owner's whole register: every knife with photos, events, flags, age."""
    con = db.connect()
    try:
        ks = db.full_register(con, g.user['id'])
    finally:
        con.close()
    store = _store()
    out = []
    for k in ks:
        v = _register_view(k, store)
        v['events'] = k['events']
        out.append(v)
    return jsonify({'knives': out})


@bp.patch('/<int:knife_id>')
@auth.login_required
def edit_knife(knife_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
        if k is None:
            return _not_found()
        try:
            fields = edit.validate(k['maker'], body, photo_seqs=[p['seq'] for p in k['photos']])
        except edit.EditError as e:
            return jsonify({'error': str(e)}), 400
        if 'ext' in fields:
            fields['ext'] = {**k['ext'], **fields['ext']}
        changed = sorted(c for c, v in fields.items() if _norm_cmp(k.get(c)) != _norm_cmp(v))
        k2 = db.update_knife(con, g.user['id'], knife_id, fields)
        if k2 is None:
            return _not_found()
        if changed and k['status'] == 'live':
            db.add_event(con, g.user['id'], knife_id, 'edited', detail=', '.join(changed))
    finally:
        con.close()
    if changed:
        log.info('%s edited by @%s: %s', k['tag'], g.user['handle'], ', '.join(changed))
    if changed and k['status'] == 'live':
        publish.schedule(g.user['id'])
    return jsonify(_register_view(k2, _store()))


@bp.post('/<int:knife_id>/save')
@auth.login_required
def save_knife(knife_id):
    """draft → live, through the ONE write gate in the product (spec §10,
    bb/billing.can_add): first-year account + knife born within 12 months →
    free; older/undated knife → spends one of 3 free slots; otherwise an
    active subscription. 402 leaves the draft in place. A live knife never
    locks — re-saving is a no-op and spends nothing."""
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
        if k is None:
            return _not_found()
        charge = False
        if k['status'] == 'draft':
            user = db.get_user(con, g.user['id'])          # fresh counter, not the session's cached row
            gate = billing.can_add(user, k.get('born_on'))
            if not gate.ok:
                log.info('%s save gated for @%s (%s, free_old_used=%s): %s', k['tag'], user['handle'],
                         user['sub_status'], user['free_old_used'], gate.reason)
                return jsonify({'error': gate.reason, 'gated': True, 'sub_status': user['sub_status'],
                                'price': billing.price_text(), 'contact': billing.contact_email()}), 402
            charge = gate.charge
        k2, err = db.publish_knife(con, g.user['id'], knife_id)
        if k2 is None:
            return _not_found()
        if err:
            return jsonify({'error': err}), 400
        if charge:
            used = db.increment_free_old(con, g.user['id'])
            log.info('%s spent free older-knife slot %d/%d for @%s', k2['tag'], used,
                     billing.FREE_OLD_KNIVES, g.user['handle'])
    finally:
        con.close()
    log.info('%s saved to the register by @%s', k2['tag'], g.user['handle'])
    publish.schedule(g.user['id'])
    return jsonify(_register_view(k2, _store()))


@bp.post('/<int:knife_id>/sale')
@auth.login_required
def sale_knife(knife_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    status = body.get('sale_status')
    if status not in db.SALE_STATUSES:
        return jsonify({'error': f'sale_status must be one of {list(db.SALE_STATUSES)}'}), 400
    try:
        asking = edit._number(body.get('asking_price'), 'asking_price', 0, 10_000_000)
        amount = edit._number(body.get('amount'), 'amount', 0, 10_000_000)
        note = edit._text(body.get('seller_note'), MAX_SELLER_NOTE, 'seller_note') or None
        counterparty = edit._text(body.get('counterparty'), edit.SHORT, 'counterparty') or None
    except edit.EditError as e:
        return jsonify({'error': str(e)}), 400
    if status == 'for_sale' and asking is None:
        return jsonify({'error': 'asking price required to list for sale'}), 400
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
        if k is None:
            return _not_found()
        if k['status'] != 'live':
            return jsonify({'error': 'save the knife first'}), 409
        if status == 'for_sale':
            ok, why = db.board_eligible(con, g.user)
            if not ok:
                return jsonify({'error': why}), 409
        k2 = db.set_sale(con, g.user['id'], knife_id, status, asking_price=asking, seller_note=note,
                         amount=amount, counterparty=counterparty)
        if k2 is None:
            return _not_found()
    finally:
        con.close()
    log.info('%s sale_status → %s by @%s', k['tag'], status, g.user['handle'])
    publish.schedule(g.user['id'])
    return jsonify(_register_view(k2, _store()))


@bp.post('/<int:knife_id>/public')
@auth.login_required
def public_knife(knife_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    if not isinstance(body.get('is_public'), bool):
        return jsonify({'error': 'is_public must be true or false'}), 400
    con = db.connect()
    try:
        n = db.set_public(con, g.user['id'], [knife_id], body['is_public'])
    finally:
        con.close()
    if n == 0:
        return _not_found()
    publish.schedule(g.user['id'])
    return jsonify({'ok': True, 'is_public': 1 if body['is_public'] else 0})


@bp.post('/bulk')
@auth.login_required
def bulk_public():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    ids = body.get('ids')
    if (not isinstance(ids, list) or len(ids) > MAX_BULK
            or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
            or not isinstance(body.get('is_public'), bool)):
        return jsonify({'error': f'ids must be a list of up to {MAX_BULK} knife ids; is_public true/false'}), 400
    con = db.connect()
    try:
        n = db.set_public(con, g.user['id'], ids, body['is_public'])
    finally:
        con.close()
    if n:
        publish.schedule(g.user['id'])
    return jsonify({'changed': n})


@bp.post('/<int:knife_id>/decode')
@auth.login_required
def decode_knife(knife_id):
    """⚡ PROCESS: the draft's photos + note → one model call → core/ext/
    confidence written onto the draft. Any status (re-decode overwrites the card fields); metered per day; the
    record is written even when consistency rules flag it (flags returned)."""
    owner = g.user['id']
    body = request.get_json(silent=True)
    no_card = bool(body.get('no_card')) if isinstance(body, dict) else False
    decoder = current_app.config['DECODER']
    store = _store()
    # Stage 1: read-side checks on one short-lived connection — closed before the
    # model call so the sqlite handle never sits open (idle, WAL-holding) across
    # what can be up to ~100 s of network time.
    con = db.connect()
    try:
        k = db.get_knife(con, owner, knife_id)
        if k is None:
            return _not_found()
        if not k['photos']:
            return jsonify({'error': 'add the box + card photo first'}), 400
        if isinstance(decoder, decode.NoDecoder):
            return jsonify({'error': 'decoder not configured'}), 503
        maker = k.get('maker') or 'crk'
        if maker not in makers.MAKERS:
            return jsonify({'error': 'unknown maker'}), 400
        cap = PAID_DECODES_PER_DAY if g.user.get('sub_status') == 'active' else FREE_DECODES_PER_DAY
        if db.decodes_today(con, owner) >= cap:
            return jsonify({'error': f'{cap} decodes today already — try again tomorrow'}), 429
        jpegs = decode.images_for(store, k)
        if not jpegs:
            return jsonify({'error': 'none of the photos are decodable — re-shoot as JPEG/HEIC'}), 400
        note = k.get('notes_private') or ''
    finally:
        con.close()

    # Stage 2: the model call — no connection open.
    t0 = time.monotonic()
    try:
        d = decoder.decode(jpegs, note, maker, no_card=no_card)
    except decode.DecodeError as e:
        ms = int((time.monotonic() - t0) * 1000)
        log.warning('decode failed for %s/%s: %s', g.user['handle'], k['tag'], e)
        decode.log_call(owner, knife_id, getattr(decoder, 'model', None), False,
                        error=str(e)[:300], latency_ms=ms)
        return jsonify({'error': 'the decoder failed — try again in a minute'}), 502
    decode.log_call(owner, knife_id, d.model, True, decoded=d)

    # Stage 3: a fresh connection to write the result and return the owner view.
    con = db.connect()
    try:
        k2 = db.apply_decode(con, owner, knife_id, d)
    finally:
        con.close()
    log.info('decoded %s for @%s via %s (%d flags, %dms)', k['tag'], g.user['handle'], d.model,
             len(d.flags), d.latency_ms)
    if k['status'] == 'live':
        publish.schedule(owner)
    out = _owner_knife(k2, store)
    out['decoded'] = {'flags': d.flags, 'reasoning': d.reasoning, 'card_text': d.card_text,
                      'no_card': d.no_card, 'model': d.model,
                      'age_months': maker_core.age_months(d.core.get('born_on'))}
    return jsonify(out)


@bp.delete('/<int:knife_id>')
@auth.login_required
def delete_knife(knife_id):
    con = db.connect()
    try:
        k = db.get_knife(con, g.user['id'], knife_id)
        keys = db.delete_knife(con, g.user['id'], knife_id)
    finally:
        con.close()
    if keys is None:
        return _not_found()
    store = _store()
    removed = _delete_keys(store, keys, k['tag'])
    log.info('%s deleted by @%s (%d files)', k['tag'], g.user['handle'], removed)
    if k['status'] == 'live':
        publish.schedule(g.user['id'])
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
        # Ownership check BEFORE ingest — don't decode up to 20 MB
        # for a knife you don't own.
        k = db.get_knife(con, owner, knife_id)
        if k is None:
            return _not_found()
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
    if k['status'] == 'live':
        publish.schedule(owner)
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
        if k['status'] == 'live' and len(k['photos']) <= 1:
            return jsonify({'error': LIVE_KEEPS_ONE}), 409
        row = db.delete_photo(con, owner, knife_id, seq)
        if row is not None and k.get('hero_photo') == seq:
            db.update_knife(con, owner, knife_id, {'hero_photo': None})
    finally:
        con.close()
    if row is None:
        return _not_found()
    store = _store()
    _delete_keys(store, [row['store_key'], db.thumb_key(row['store_key'])],
                 f'photo {knife_id}/{seq}')
    if k['status'] == 'live':
        publish.schedule(owner)
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
