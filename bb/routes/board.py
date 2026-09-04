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
        offset = min(_int_arg('offset', 0), db.MAX_BOARD_OFFSET)          # clamp: 10**23 was a sqlite 500
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
        to_seller, to_buyer = board.contact_emails(g.user, k, message)
        # The seller leg IS the intro — it must land, or the claim (and the
        # buyer's quota) is rolled back so they can simply retry. The buyer's
        # own copy is a courtesy: once the seller has been notified, a failed
        # copy must NOT delete the claim (the intro already happened) or let
        # a retry re-mail the seller for free — mark it 'partial' and keep it.
        try:
            mailer.send(**to_seller)
        except Exception as e:  # noqa: BLE001 — surfaced in the log, claim rolled back, buyer retries
            log.error('board contact %s → %s seller send failed: %r', g.user['handle'], k['tag'], e)
            db.delete_intro(con, intro_id)
            return jsonify({'error': 'could not send — try again'}), 502
        try:
            mid = mailer.send(**to_buyer)
        except Exception as e:  # noqa: BLE001 — seller already notified; keep the claim, tell the buyer
            log.error('board contact %s → %s buyer copy failed: %r', g.user['handle'], k['tag'], e)
            db.mark_intro_sent(con, intro_id, 'partial')
            return jsonify({'ok': True, 'copy': False})
        db.mark_intro_sent(con, intro_id, mid)
    finally:
        con.close()
    log.info('board contact: @%s → @%s %s', g.user['handle'], k['owner_handle'], k['tag'])
    return jsonify({'ok': True})


@bp.post('/<int:knife_id>/report')
@auth.login_required
def report_knife(knife_id):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({'error': 'body must be a JSON object'}), 400
    try:
        reason = edit._text(body.get('reason'), board.MAX_REASON, 'reason')
    except edit.EditError as e:
        return jsonify({'error': str(e)}), 400
    if len(reason) < board.MIN_REASON:
        return jsonify({'error': 'say why (a few words)'}), 400
    con = db.connect()
    try:
        k = db.get_knife_any(con, knife_id)
        # only knives that are (or were, until hidden) ON the board are reportable —
        # the board is the only place a stranger can see them (review H5/L7)
        if k is None or k['status'] != 'live' or not k['is_public'] or k['sale_status'] != 'for_sale':
            return jsonify({'error': 'not found'}), 404
        owner = db.get_user(con, k['owner_id'])
        # private or key-gated owners never appear on the board — a reporter
        # could not legitimately see such a knife, so treat it as not found.
        if owner.get('profile_private') or (owner.get('public_key') or '').strip():
            return jsonify({'error': 'not found'}), 404
        if k['owner_id'] == g.user['id']:
            return jsonify({'error': "that's your own knife"}), 400
        try:
            rid = db.create_report(con, knife_id, k['owner_id'], g.user['id'], reason)
        except ValueError as e:
            return jsonify({'error': str(e)}), 409
        if rid is None:
            return jsonify({'error': 'you already reported this knife'}), 409
        hidden = bool(k['hidden_at'])
        if not hidden and db.counting_open_reports(con, knife_id) >= db.AUTO_HIDE_REPORTS:
            db.hide_knife(con, knife_id, 'reports', f'auto-hidden: {db.AUTO_HIDE_REPORTS} open reports')
            hidden = True
            log.warning('%s (owner %d) auto-hidden after %d reports', k['tag'], k['owner_id'], db.AUTO_HIDE_REPORTS)
            publish.schedule(k['owner_id'])
    finally:
        con.close()
    log.info('report on knife %d by @%s: %s', knife_id, g.user['handle'], reason[:80])
    return jsonify({'ok': True, 'hidden': hidden})
