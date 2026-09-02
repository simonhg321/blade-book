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
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == 7


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
