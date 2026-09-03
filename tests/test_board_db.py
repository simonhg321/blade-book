# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Schema v7 + board/hide helpers (plan 09). Helpers here (_seller, _old_knife)
are imported by the other plan-09 test files."""
from datetime import datetime, timedelta, timezone

from bb import db
from tests.test_search import _mk_knife, _mk_user


def _ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _seller(con, email='s@example.com', handle='s-guy', **over):
    """A verified user (board-eligible once _old_knife gives them an old live knife)."""
    return _mk_user(con, email=email, handle=handle, verified_at=db.now(), **over)


def _old_knife(con, uid, days=8, **cols):
    """A live knife whose `created` is `days` ago — what makes an account board-eligible."""
    k = _mk_knife(con, uid, **cols)
    con.execute('UPDATE knives SET created = ? WHERE id = ?', (_ago(days), k['id']))
    con.commit()
    return db.get_knife(con, uid, k['id'])


def test_schema_v7_columns_and_open_report_index(con):
    kcols = {r[1] for r in con.execute('PRAGMA table_info(knives)')}
    assert {'listed_at', 'hidden_at', 'hidden_by', 'hidden_note'} <= kcols
    icols = {r[1] for r in con.execute('PRAGMA table_info(intros)')}
    assert 'message' in icols
    idx = [r[1] for r in con.execute('PRAGMA index_list(reports)')]
    assert 'idx_reports_open' in idx
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION


def test_set_sale_stamps_and_clears_listed_at(con):
    u = _seller(con)
    k = _mk_knife(con, u['id'])
    assert k['listed_at'] is None
    k = db.set_sale(con, u['id'], k['id'], 'for_sale', asking_price=500)
    first = k['listed_at']
    assert first
    k = db.set_sale(con, u['id'], k['id'], 'for_sale', asking_price=450)   # price change keeps the stamp
    assert k['listed_at'] == first
    k = db.set_sale(con, u['id'], k['id'], 'keeping')
    assert k['listed_at'] is None
    k = db.set_sale(con, u['id'], k['id'], 'for_trade', asking_price=1)
    assert k['listed_at'] is None


def test_hide_restore_and_get_knife_any(con):
    u = _seller(con)
    k = _mk_knife(con, u['id'])
    assert db.hide_knife(con, k['id'], 'admin', 'scam photos') is True
    any_k = db.get_knife_any(con, k['id'])
    assert any_k['owner_id'] == u['id'] and any_k['hidden_at'] and any_k['hidden_by'] == 'admin'
    assert any_k['hidden_note'] == 'scam photos'
    assert db.restore_knife(con, k['id']) is True
    any_k = db.get_knife_any(con, k['id'])
    assert any_k['hidden_at'] is None and any_k['hidden_by'] is None and any_k['hidden_note'] is None
    assert db.hide_knife(con, 999999, 'admin', 'x') is False
    assert db.get_knife_any(con, 999999) is None


def test_hidden_knife_leaves_public_and_matching_queries(con):
    u = _seller(con)
    k = _mk_knife(con, u['id'], sale_status='for_sale', asking_price=500)
    assert [x['id'] for x in db.public_knives(con, u['id'])] == [k['id']]
    assert [x['id'] for x in db.all_public_knives(con)] == [k['id']]
    db.hide_knife(con, k['id'], 'reports', 'auto')
    assert db.public_knives(con, u['id']) == []
    assert db.all_public_knives(con) == []
    db.restore_knife(con, k['id'])
    assert [x['id'] for x in db.public_knives(con, u['id'])] == [k['id']]


def test_board_intro_claim_returns_id_with_null_want_and_allows_many(con):
    seller = _seller(con)
    k = _mk_knife(con, seller['id'], sale_status='for_sale', asking_price=500)
    b1 = _mk_user(con, email='b1@example.com', handle='b1-guy')
    b2 = _mk_user(con, email='b2@example.com', handle='b2-guy')
    i1 = db.claim_intro(con, None, k['id'], b1['id'], seller['id'], kind='board', message='hi')
    i2 = db.claim_intro(con, None, k['id'], b2['id'], seller['id'], kind='board')
    assert i1 and i2 and i1 != i2                   # NULL want_id never collides
    row = dict(con.execute('SELECT * FROM intros WHERE id = ?', (i1,)).fetchone())
    assert row['kind'] == 'board' and row['message'] == 'hi' and row['want_id'] is None
    assert db.unsent_intros(con) == []              # default kind='match' hides board claims
    assert {r['id'] for r in db.unsent_intros(con, kind='board')} == {i1, i2}
    db.delete_intro(con, i1)
    assert {r['id'] for r in db.unsent_intros(con, kind='board')} == {i2}


def test_open_report_index_is_partial(con):
    seller = _seller(con)
    k = _mk_knife(con, seller['id'])
    r = _mk_user(con, email='r@example.com', handle='r-guy')
    con.execute('INSERT INTO reports (knife_id, owner_id, reporter_id, reason, created) VALUES (?,?,?,?,?)',
                (k['id'], seller['id'], r['id'], 'fake', db.now()))
    cur = con.execute('INSERT OR IGNORE INTO reports (knife_id, owner_id, reporter_id, reason, created) VALUES (?,?,?,?,?)',
                      (k['id'], seller['id'], r['id'], 'fake again', db.now()))
    assert cur.rowcount == 0                         # second OPEN report ignored
    con.execute("UPDATE reports SET resolved_at = ?, resolution = 'x' WHERE knife_id = ?", (db.now(), k['id']))
    cur = con.execute('INSERT OR IGNORE INTO reports (knife_id, owner_id, reporter_id, reason, created) VALUES (?,?,?,?,?)',
                      (k['id'], seller['id'], r['id'], 'new round', db.now()))
    assert cur.rowcount == 1                         # resolved → a new open report is allowed


def test_board_eligible_rules(con):
    fresh = _mk_user(con, email='f@example.com', handle='f-guy')            # unverified, no knives
    ok, why = db.board_eligible(con, fresh)
    assert ok is False and 'verify' in why
    v = _seller(con, email='v@example.com', handle='v-guy')                  # verified, no knives
    ok, why = db.board_eligible(con, v)
    assert ok is False and '7 days' in why
    _mk_knife(con, v['id'])                                                  # a live knife, created now
    assert db.board_eligible(con, v)[0] is False
    _old_knife(con, v['id'], days=8)
    assert db.board_eligible(con, v) == (True, None)
    admin = _mk_user(con, email='a@example.com', handle='a-guy', is_admin=1) # admin bypasses everything
    assert db.board_eligible(con, admin) == (True, None)


def test_board_knives_filters_and_order(con):
    s = _seller(con)
    old = _old_knife(con, s['id'], days=9)                                   # makes s eligible; keeping
    a = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500, listed_at='2026-09-01T00:00:00+00:00')
    b = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=600, listed_at='2026-09-02T00:00:00+00:00')
    _mk_knife(con, s['id'], sale_status='for_trade')                         # never on the board
    _mk_knife(con, s['id'], sale_status='for_sale', asking_price=1, is_public=0)
    hidden = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=1)
    db.hide_knife(con, hidden['id'], 'reports', 'auto')
    # ineligible sellers: unverified / no old knife / private / gated
    u1 = _mk_user(con, email='u1@example.com', handle='u1-guy')
    _mk_knife(con, u1['id'], sale_status='for_sale', asking_price=1)
    u2 = _seller(con, email='u2@example.com', handle='u2-guy')
    _mk_knife(con, u2['id'], sale_status='for_sale', asking_price=1)         # verified but nothing old
    u3 = _seller(con, email='u3@example.com', handle='u3-guy', profile_private=1)
    _old_knife(con, u3['id'], sale_status='for_sale', asking_price=1)
    u4 = _seller(con, email='u4@example.com', handle='u4-guy', public_key='GATE')
    _old_knife(con, u4['id'], sale_status='for_sale', asking_price=1)
    total, rows = db.board_knives(con)
    assert total == 2
    assert [r['id'] for r in rows] == [b['id'], a['id']]                    # newest listed first
    assert rows[0]['owner_handle'] == 's-guy' and rows[0]['owner_email'] == 's@example.com'
    assert 'owner_share_email' in rows[0] and 'owner_hide_born_day' in rows[0]
    total, page = db.board_knives(con, limit=1, offset=1)
    assert total == 2 and [r['id'] for r in page] == [a['id']]
    assert db.board_knife(con, a['id'])['id'] == a['id']
    assert db.board_knife(con, old['id']) is None                            # keeping
    assert db.board_knife(con, hidden['id']) is None
    assert db.board_knife(con, 999999) is None


def test_board_card_is_public_only(env, con):
    import os
    from bb import board, paths, publish
    s = _seller(con)
    _old_knife(con, s['id'])
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500, seller_note='mint, box',
                  price_paid=123, notes_private='SECRET', location='safe')
    row = db.board_knife(con, k['id'])
    c = board.card(row)
    assert c['id'] == k['id'] and c['tag'] == k['tag'] and c['handle'] == 's-guy'
    assert c['name'] == 'Large Sebenza 21' and c['asking_price'] == 500 and c['seller_note'] == 'mint, box'
    assert c['for_sale'] == 1 and c['born'].startswith('March') and c['listed_at']
    for bad in ('price_paid', 'notes_private', 'location', 'owner_email', 'owner_share_email', 'email', 'photos', 'ext'):
        assert bad not in c
    assert 'img_t' not in c                                                  # no thumb on disk yet
    img_dir = os.path.join(publish.bundle_dir('s-guy'), 'img')
    os.makedirs(img_dir)
    open(os.path.join(img_dir, f"{k['tag']}_t.jpg"), 'wb').write(b'x')
    assert board.card(db.board_knife(con, k['id']))['img_t'] == f"{k['tag']}_t.jpg"


def test_board_card_honours_hide_born_day(con):
    from bb import board
    s = _seller(con, hide_born_day=1)
    _old_knife(con, s['id'])
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=5)
    c = board.card(db.board_knife(con, k['id']))
    assert c['born'] == 'March 2008' and c['born_on'] == '2008-03'
