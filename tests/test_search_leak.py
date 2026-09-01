# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Spec §5: PRIVATE_COLUMNS content never appears in /search — not in the
index tables, not in any API byte. Enumerates db.PRIVATE_COLUMNS
programmatically so a future column is caught the day it's added.
Mirrors tests/test_publish_leak.py's sentinel-seeding approach."""
import json

from bb import db, search
from tests.test_search import _mk_knife, _mk_user, _rows

S = '/blade-book/api/search'


def _sentinel(col):
    return f'LEAK-{col.upper()}-9F3A'


def _poisoned_user(con):
    # handle/email deliberately avoid the token "leak" — FTS5's unicode61
    # tokenizer splits on '-', so a handle like "leak-guy" would make the
    # public handle itself match a "LEAK*" query and produce a false positive
    u = _mk_user(con, email='sentinel@example.com', handle='sentinel-guy')
    k = _mk_knife(con, u['id'])
    sentinels = {}
    for col in sorted(db.PRIVATE_COLUMNS):
        val = _sentinel(col)
        if col == 'confidence':               # json-typed column: must stay valid JSON
            con.execute('UPDATE knives SET confidence = ? WHERE id = ?',
                        (json.dumps({'leak': val}), k['id']))
        else:
            con.execute(f'UPDATE knives SET {col} = ? WHERE id = ?', (val, k['id']))
        sentinels[col] = val
    con.commit()
    return u, sentinels


def test_sentinels_actually_landed_in_the_db(con):
    """Prove the seeding worked before trusting the negative assertions below."""
    u, sentinels = _poisoned_user(con)
    row = dict(con.execute('SELECT * FROM knives WHERE owner_id = ?', (u['id'],)).fetchone())
    for col, val in sentinels.items():
        assert val in repr(row.get(col)), f'{col} sentinel missing from seeded row'


def test_no_private_bytes_in_index_or_run_query(con):
    u, sentinels = _poisoned_user(con)
    n = search.reindex_user(con, u, _rows(con, u))
    assert n == 1

    blob = b''
    for t in ('search_cards', 'search_fts'):
        for row in con.execute(f'SELECT * FROM {t}'):
            blob += repr(tuple(row)).encode()
    r = search.run_query(con, '')
    blob += json.dumps(r).encode()

    assert len(db.PRIVATE_COLUMNS) >= 8   # the loop below must actually cover them
    for col, val in sentinels.items():
        assert val.encode() not in blob, f'{col} leaked into search_cards/search_fts/run_query'

    # and the sentinel terms are not findable via FTS at all
    assert search.run_query(con, 'LEAK')['count'] == 0


def test_no_private_bytes_in_the_actual_api_route(con, client):
    """Extra hardening beyond run_query: scan the real HTTP response bytes,
    so any future field the route bolts on gets caught too."""
    u, sentinels = _poisoned_user(con)
    search.reindex_user(con, u, _rows(con, u))

    r_empty = client.get(S)
    assert r_empty.status_code == 200
    for col, val in sentinels.items():
        assert val.encode() not in r_empty.data, f'{col} leaked into /api/search (empty q)'

    r_hit = client.get(S, query_string={'q': 'Sebenza'})
    assert r_hit.status_code == 200
    assert r_hit.get_json()['count'] == 1   # confirms this actually matched the poisoned knife
    for col, val in sentinels.items():
        assert val.encode() not in r_hit.data, f'{col} leaked into /api/search (q=Sebenza)'
