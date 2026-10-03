# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Spec §5: PRIVATE_COLUMNS content never appears in /search — not in the
index tables, not in any API byte. Enumerates db.PRIVATE_COLUMNS
programmatically so a future column is caught the day it's added.
Mirrors tests/test_publish_leak.py's sentinel-seeding approach, including its
extra needles beyond the bare PRIVATE_COLUMNS list: a withheld seller_note on
a for_trade knife, a sold knife excluded from the public surface entirely,
and owner-identity fields (email, session_secret, a second gated owner's
public_key)."""
import json

from bb import db, search
from tests.test_search import _mk_knife, _mk_user, _rows

S = '/blade-book/api/search'

EMAIL_SENTINEL = 'leak-email-9f3a@example.com'
SESSION_SECRET_SENTINEL = 'LEAK-SESSIONSECRET-9F3A'
SELLER_NOTE_SENTINEL = 'LEAK-SELLERNOTE-9F3A'
SOLD_NOTES_SENTINEL = 'LEAK-SOLDNOTES-9F3A'
GATED_PUBLIC_KEY_SENTINEL = 'LEAK-PUBLICKEY-9F3A'


def _sentinel(col):
    return f'LEAK-{col.upper()}-9F3A'


def _poisoned_user(con):
    """The primary indexed user:
    - a published knife (model Sebenza) with every db.PRIVATE_COLUMNS sentinel
    - a for_trade knife (model Mnandi) whose seller_note is a sentinel — the
      knife itself stays public, only the note must stay withheld
    - a sold knife (model Inkosi) with a sentinel in notes_public — sold
      knives are excluded from db.public_knives() entirely (crkinv rule), so
      nothing about it, sentinel or otherwise, should reach the index
    - owner-identity sentinels: email and session_secret

    handle/email deliberately avoid the bare token "leak" as its own word
    except in the email itself (which is never tokenized into the FTS text
    or the card) — FTS5's unicode61 tokenizer splits on '-', so a handle like
    "leak-guy" would make the public handle itself match a "LEAK*" query and
    produce a false positive.
    """
    u = _mk_user(con, email=EMAIL_SENTINEL, handle='sentinel-guy')
    con.execute('UPDATE users SET session_secret = ? WHERE id = ?',
                (SESSION_SECRET_SENTINEL, u['id']))
    con.commit()
    u = db.get_user(con, u['id'])

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

    kt = _mk_knife(con, u['id'], model='Mnandi')
    db.set_sale(con, u['id'], kt['id'], 'for_trade', seller_note=SELLER_NOTE_SENTINEL)

    ks = _mk_knife(con, u['id'], model='Inkosi')
    con.execute('UPDATE knives SET notes_public = ? WHERE id = ?',
                (SOLD_NOTES_SENTINEL, ks['id']))
    con.commit()
    db.set_sale(con, u['id'], ks['id'], 'sold', amount=999.0, counterparty='buyer')

    return {'user': u, 'sentinels': sentinels, 'knife_id': k['id'],
            'trade_knife_id': kt['id'], 'sold_knife_id': ks['id']}


def _gated_user(con):
    """A second, key-gated owner. reindex_user must return 0 rows for it, so
    nothing it holds — including its own public_key — ever reaches the
    index. (test_search.py's test_gated_and_private_owners_are_excluded
    already covers the gating logic itself; this is insurance that the
    gated owner's secret doesn't show up in THIS test's byte-scans.)"""
    u = _mk_user(con, email='gated@example.com', handle='gated-guy',
                 public_key=GATED_PUBLIC_KEY_SENTINEL)
    _mk_knife(con, u['id'])
    return u


def _all_sentinel_values(poisoned):
    return (list(poisoned['sentinels'].values()) +
            [SELLER_NOTE_SENTINEL, SOLD_NOTES_SENTINEL, EMAIL_SENTINEL,
             SESSION_SECRET_SENTINEL, GATED_PUBLIC_KEY_SENTINEL])


def test_sentinels_actually_landed_in_the_db(con):
    """Prove the seeding worked before trusting the negative assertions below."""
    p = _poisoned_user(con)
    row = dict(con.execute('SELECT * FROM knives WHERE id = ?', (p['knife_id'],)).fetchone())
    for col, val in p['sentinels'].items():
        assert val in repr(row.get(col)), f'{col} sentinel missing from seeded row'
    trade_row = dict(con.execute('SELECT * FROM knives WHERE id = ?',
                                  (p['trade_knife_id'],)).fetchone())
    assert trade_row['seller_note'] == SELLER_NOTE_SENTINEL
    assert trade_row['sale_status'] == 'for_trade'
    sold_row = dict(con.execute('SELECT * FROM knives WHERE id = ?',
                                 (p['sold_knife_id'],)).fetchone())
    assert sold_row['notes_public'] == SOLD_NOTES_SENTINEL
    assert sold_row['sale_status'] == 'sold'
    u = dict(con.execute('SELECT * FROM users WHERE id = ?', (p['user']['id'],)).fetchone())
    assert u['email'] == EMAIL_SENTINEL
    assert u['session_secret'] == SESSION_SECRET_SENTINEL


def test_no_private_bytes_in_index_or_run_query(con):
    p = _poisoned_user(con)
    _gated_user(con)
    u = p['user']
    n = search.reindex_user(con, u, _rows(con, u))
    assert n == 2   # base Sebenza + the for_trade Mnandi; the sold Inkosi is excluded

    blob = b''
    for t in ('search_cards', 'search_fts'):
        for row in con.execute(f'SELECT * FROM {t}'):
            blob += repr(tuple(row)).encode()
    r = search.run_query(con, '')
    blob += json.dumps(r).encode()

    assert len(db.PRIVATE_COLUMNS) >= 9   # the loop below must actually cover them
    for val in _all_sentinel_values(p):
        assert val.encode() not in blob, f'{val} leaked into search_cards/search_fts/run_query'

    # and the sentinel terms are not findable via FTS at all
    assert search.run_query(con, 'LEAK')['count'] == 0


def test_no_private_bytes_in_the_actual_api_route(con, client):
    """Extra hardening beyond run_query: scan the real HTTP response bytes,
    so any future field the route bolts on gets caught too."""
    p = _poisoned_user(con)
    _gated_user(con)
    u = p['user']
    search.reindex_user(con, u, _rows(con, u))
    values = _all_sentinel_values(p)

    r_empty = client.get(S)
    assert r_empty.status_code == 200
    for val in values:
        assert val.encode() not in r_empty.data, f'{val} leaked into /api/search (empty q)'

    r_hit = client.get(S, query_string={'q': 'Sebenza'})
    assert r_hit.status_code == 200
    assert r_hit.get_json()['count'] == 1   # confirms this actually matched the poisoned knife
    for val in values:
        assert val.encode() not in r_hit.data, f'{val} leaked into /api/search (q=Sebenza)'

    r_leak = client.get(S, query_string={'q': 'LEAK'})
    assert r_leak.status_code == 200
    assert r_leak.get_json()['count'] == 0
    for val in values:
        assert val.encode() not in r_leak.data, f'{val} leaked into /api/search (q=LEAK)'

    term = p['sentinels'][sorted(db.PRIVATE_COLUMNS)[0]]
    r_term = client.get(S, query_string={'q': term})
    assert r_term.status_code == 200
    assert r_term.get_json()['count'] == 0
    for val in values:
        assert val.encode() not in r_term.data, f'{val} leaked into /api/search (q={term!r})'
