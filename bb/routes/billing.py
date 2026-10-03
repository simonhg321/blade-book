# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/routes/billing.py — GET /blade-book/api/billing: the price line and, when
signed in, where the caller stands against the gate (own numbers only). The
intake and search pages read it so no price copy is baked into HTML."""
from flask import Blueprint, jsonify

from bb import auth, billing, db, paths

bp = Blueprint('billing', __name__, url_prefix=paths.API_PREFIX + '/billing')


@bp.get('', strict_slashes=False)
def status():
    con = db.connect()
    try:
        user = auth.current_user(con)
    finally:
        con.close()
    return jsonify(billing.summary(user))
