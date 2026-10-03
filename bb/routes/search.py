# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""GET /blade-book/api/search — public, auth optional (spec §9).
Free: text search + card-lite results + aggregate counts.
Paid (sub_status 'active' or admin): year range, damascus smith/pattern,
special edition, who-has-≥N. The ONLY read gate in the product (spec §10),
and only when billing.hard_gate() is on — free and open during early access.
The write gate is bb/billing.can_add at save."""
import logging

from flask import Blueprint, jsonify, request

from bb import auth, billing, db, paths, search

bp = Blueprint('search', __name__, url_prefix=paths.API_PREFIX + '/search')
log = logging.getLogger('blade-book.search')

FILTER_PARAMS = ('year_from', 'year_to', 'smith', 'pattern', 'edition', 'who_min')


@bp.get('', strict_slashes=False)
def search_route():
    con = db.connect()
    try:
        wants_filters = any(request.args.get(p) for p in FILTER_PARAMS)
        if wants_filters and billing.hard_gate():        # early access: filters are open to everyone
            user = auth.current_user(con)
            if not (user and (user.get('sub_status') == 'active' or user.get('is_admin'))):
                return jsonify({'error': "filters are early-access — email us and we'll turn them on"}), 402
        filters, who_min = {}, None
        try:
            for p in ('year_from', 'year_to'):
                if request.args.get(p):
                    val = int(request.args[p])
                    if val < 0 or val > 9999:
                        return jsonify({'error': 'bad filter value'}), 400
                    filters[p] = val
            for p in ('smith', 'pattern', 'edition'):
                if request.args.get(p):
                    filters[p] = request.args[p][:60]
            if request.args.get('who_min'):
                val = int(request.args['who_min'])
                if val < 1 or val > 1000:
                    return jsonify({'error': 'bad filter value'}), 400
                who_min = val
        except ValueError:
            return jsonify({'error': 'bad filter value'}), 400
        return jsonify(search.run_query(con, request.args.get('q', ''),
                                        filters or None, who_min))
    finally:
        con.close()
