# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""GET /blade-book/api/search — public, auth optional (spec §9).
Free: text search + card-lite results + aggregate counts.
Paid (sub_status 'active' or admin): year range, damascus smith/pattern,
special edition, who-has-≥N. The ONLY read gate in the product (spec §10)."""
import logging

from flask import Blueprint, jsonify, request

from bb import auth, db, paths, search

bp = Blueprint('search', __name__, url_prefix=paths.API_PREFIX + '/search')
log = logging.getLogger('blade-book.search')

FILTER_PARAMS = ('year_from', 'year_to', 'smith', 'pattern', 'edition', 'who_min')


@bp.get('', strict_slashes=False)
def search_route():
    con = db.connect()
    try:
        wants_filters = any(request.args.get(p) for p in FILTER_PARAMS)
        if wants_filters:
            user = auth.current_user(con)
            if not (user and (user.get('sub_status') == 'active' or user.get('is_admin'))):
                return jsonify({'error': "filters are early-access — email us and we'll turn them on"}), 402
        filters, who_min = {}, None
        try:
            for p in ('year_from', 'year_to'):
                if request.args.get(p):
                    filters[p] = int(request.args[p])
            for p in ('smith', 'pattern', 'edition'):
                if request.args.get(p):
                    filters[p] = request.args[p][:60]
            if request.args.get('who_min'):
                who_min = max(1, int(request.args['who_min']))
        except ValueError:
            return jsonify({'error': 'bad filter value'}), 400
        return jsonify(search.run_query(con, request.args.get('q', ''),
                                        filters or None, who_min))
    finally:
        con.close()
