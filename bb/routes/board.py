# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/routes/board.py — /blade-book/api/board: the For Sale board (spec §9).
GET is public. Contact + report need a session. Contact is claim-then-send
like the match cron, but synchronous: the claim is deleted if the send
fails, so the buyer can simply try again (no retry lane for board intros)."""
import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request

from bb import auth, board, db, edit, paths, publish

log = logging.getLogger('blade-book.board')

bp = Blueprint('board', __name__, url_prefix=paths.API_PREFIX + '/board')

MAX_LIMIT = 48
DEFAULT_LIMIT = 24


def _int_arg(name, default):
    raw = request.args.get(name)
    if raw is None or raw == '':
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f'{name} must be a whole number') from None


@bp.get('', strict_slashes=False)
def list_board():
    try:
        limit = max(1, min(_int_arg('limit', DEFAULT_LIMIT), MAX_LIMIT))   # clamp, never 400
        offset = _int_arg('offset', 0)
        if offset < 0:
            raise ValueError('offset must be 0 or more')
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    con = db.connect()
    try:
        total, rows = db.board_knives(con, limit=limit, offset=offset)
        cards = [board.card(k) for k in rows]
    finally:
        con.close()
    return jsonify({'count': total, 'knives': cards, 'limit': limit, 'offset': offset})


def _day_ago():
    return (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()


@bp.post('/<int:knife_id>/contact')
@auth.login_required
def contact_seller(knife_id):
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    try:
        message = edit._text(body.get('message'), board.MAX_MESSAGE, 'message')
    except edit.EditError as e:
        return jsonify({'error': str(e)}), 400
    con = db.connect()
    try:
        k = db.board_knife(con, knife_id)
        if k is None:
            return jsonify({'error': 'not found'}), 404
        if k['owner_id'] == g.user['id']:
            return jsonify({'error': "that's your own knife"}), 400
        since = _day_ago()
        if db.board_contacts_since(con, g.user['id'], since, to_user=k['owner_id']) >= board.MAX_PER_SELLER_PER_DAY:
            return jsonify({'error': f"you've reached {board.MAX_PER_SELLER_PER_DAY} intros to this seller "
                                     "in 24 hours — give them a day"}), 429
        if db.board_contacts_since(con, g.user['id'], since) >= board.MAX_PER_BUYER_PER_DAY:
            return jsonify({'error': f"you've sent {board.MAX_PER_BUYER_PER_DAY} board intros today — "
                                     "try again tomorrow"}), 429
        intro_id = db.claim_intro(con, None, knife_id, g.user['id'], k['owner_id'],
                                  kind='board', message=message or None)
        if intro_id is None:      # cannot happen with a NULL want_id, but never send unclaimed
            return jsonify({'error': 'could not claim the intro'}), 500
        mailer = current_app.config['MAILER']
        mid = 'sent'
        try:
            for kwargs in board.contact_emails(g.user, k, message):
                mid = mailer.send(**kwargs)
        except Exception as e:  # noqa: BLE001 — surfaced in the log, claim rolled back, buyer retries
            log.error('board contact %s → %s send failed: %r', g.user['handle'], k['tag'], e)
            db.delete_intro(con, intro_id)
            return jsonify({'error': 'could not send — try again'}), 502
        db.mark_intro_sent(con, intro_id, mid)
    finally:
        con.close()
    log.info('board contact: @%s → @%s %s', g.user['handle'], k['owner_handle'], k['tag'])
    return jsonify({'ok': True})
