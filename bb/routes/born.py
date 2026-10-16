# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""GET /blade-book/api/born?d=<text> — the date board (spec 2026-10-15 §2).
Public, no session. Parse → candidate pull (db.date_board_knives) → rank →
board cards (bb/board.card strips everything private) plus days_off."""
from flask import Blueprint, jsonify, request

from bb import board, born, db, paths

bp = Blueprint('born', __name__, url_prefix=paths.API_PREFIX + '/born')


@bp.get('', strict_slashes=False)
def born_route():
    try:
        year, month, day = born.parse(request.args.get('d', ''))
    except born.BornError as e:
        return jsonify({'error': str(e)}), 400
    con = db.connect()
    try:
        rows = db.date_board_knives(con, [md for _, md in born.window(month, day)])
        groups = born.rank(year, month, day, rows)
        out = {g: [dict(board.card(k), days_off=off) for off, k in items] for g, items in groups.items()}
    finally:
        con.close()
    out['query'] = {'year': year, 'month': month, 'day': day,
                    'label': born.label(year, month, day),
                    'url': born.url_form(year, month, day),
                    'short': born.short(month, day)}
    return jsonify(out)
