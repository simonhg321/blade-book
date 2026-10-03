# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""POST /api/board/<id>/report + auto-hide at 3 counting reports (plan 09)."""
from bb import db, publish
from tests.conftest import signed_in
from tests.test_board_db import _old_knife, _seller
from tests.test_search import _mk_knife, _mk_user

B = '/blade-book/api/board'


def _target(con):
    s = _seller(con)
    _old_knife(con, s['id'])
    return s, _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500)


def _reporter_with_knife(client, mailer, con, email):
    """Sign a reporter in as a board-eligible collector (verified, a knife live
    for the board age) so their report COUNTS — review H5."""
    u = signed_in(client, mailer, email=email)
    con.execute('UPDATE users SET verified_at = ? WHERE id = ?', (db.now(), u['id'])); con.commit()
    _old_knife(con, u['id'])
    return u


def test_report_validation(client, mailer, con):
    s, k = _target(con)
    assert client.post(f"{B}/{k['id']}/report", json={'reason': 'fake'}).status_code == 401
    signed_in(client, mailer, email='r1@example.com')
    assert client.post(f"{B}/{k['id']}/report", json={}).status_code == 400
    assert client.post(f"{B}/{k['id']}/report", json={'reason': 'no'}).status_code == 400       # < 3 chars
    assert client.post(f"{B}/{k['id']}/report", json={'reason': 'x' * 501}).status_code == 400
    assert client.post(f"{B}/{k['id']}/report", json=[1]).status_code == 400
    assert client.post(f"{B}/999999/report", json={'reason': 'fake listing'}).status_code == 404
    draft = db.create_draft_knife(con, s['id'])
    assert client.post(f"{B}/{draft['id']}/report", json={'reason': 'fake listing'}).status_code == 404
    r = client.post(f"{B}/{k['id']}/report", json={'reason': 'stolen photos'})
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'hidden': False}
    r = client.post(f"{B}/{k['id']}/report", json={'reason': 'again'})
    assert r.status_code == 409 and 'already' in r.get_json()['error']


def test_report_own_knife_is_400(client, mailer, con):
    me = signed_in(client, mailer, email='own@example.com')
    k = _mk_knife(con, me['id'], sale_status='for_sale', asking_price=100)
    r = client.post(f"{B}/{k['id']}/report", json={'reason': 'testing'})
    assert r.status_code == 400 and 'your own' in r.get_json()['error']


def test_report_rejects_private_and_gated_owners(client, mailer, con):
    priv = _seller(con, email='p@example.com', handle='p-guy', profile_private=1)
    kp = _mk_knife(con, priv['id'])
    gated = _seller(con, email='g@example.com', handle='g-guy', public_key='GATE')
    kg = _mk_knife(con, gated['id'])
    signed_in(client, mailer, email='r@example.com')
    assert client.post(f"{B}/{kp['id']}/report", json={'reason': 'fake listing'}).status_code == 404
    assert client.post(f"{B}/{kg['id']}/report", json={'reason': 'fake listing'}).status_code == 404
    assert con.execute('SELECT count(*) FROM reports').fetchone()[0] == 0


def test_three_counting_reports_auto_hide_and_republish(client, mailer, con, monkeypatch):
    s, k = _target(con)
    scheduled = []
    monkeypatch.setattr(publish, 'schedule', lambda owner_id: scheduled.append(owner_id))
    # two reports from accounts WITHOUT a live knife: recorded, but don't count
    for e in ('n1@example.com', 'n2@example.com'):
        signed_in(client, mailer, email=e)
        assert client.post(f"{B}/{k['id']}/report", json={'reason': 'sock puppet'}).get_json()['hidden'] is False
        client.post('/blade-book/api/auth/signout')
    for e in ('c1@example.com', 'c2@example.com'):
        _reporter_with_knife(client, mailer, con, e)
        assert client.post(f"{B}/{k['id']}/report", json={'reason': 'stolen photos'}).get_json()['hidden'] is False
        client.post('/blade-book/api/auth/signout')
    assert db.counting_open_reports(con, k['id']) == 2
    assert con.execute('SELECT count(*) FROM reports WHERE knife_id = ?', (k['id'],)).fetchone()[0] == 4
    assert scheduled == []
    _reporter_with_knife(client, mailer, con, 'c3@example.com')
    r = client.post(f"{B}/{k['id']}/report", json={'reason': 'stolen photos'})
    assert r.status_code == 200 and r.get_json()['hidden'] is True
    any_k = db.get_knife_any(con, k['id'])
    assert any_k['hidden_by'] == 'reports' and '3' in any_k['hidden_note']
    assert scheduled == [s['id']]                                   # owner's bundle rebuild queued
    assert client.get(B).get_json()['count'] == 0                   # off the board
    # gone from public/all_public listings too (the owner's OTHER old knife,
    # made public by _target()/_old_knife() purely for board eligibility,
    # is untouched — hide_knife is scoped to the reported knife alone, same
    # as the task-4 precedent: hiding one knife never hides a sibling).
    assert k['id'] not in {x['id'] for x in db.public_knives(con, s['id'])}
    assert k['id'] not in {x['id'] for x in db.all_public_knives(con)}
    # already hidden: a 4th counting report is recorded, no second hide/republish
    client.post('/blade-book/api/auth/signout')
    _reporter_with_knife(client, mailer, con, 'c4@example.com')
    r = client.post(f"{B}/{k['id']}/report", json={'reason': 'still up'})
    assert r.status_code == 200 and r.get_json()['hidden'] is True
    assert scheduled == [s['id']]


def test_reporter_open_report_cap(client, mailer, con):
    s = _seller(con); _old_knife(con, s['id'])
    ks = [_mk_knife(con, s['id'], sale_status='for_sale', asking_price=100) for _ in range(db.MAX_OPEN_REPORTS_PER_REPORTER + 1)]
    signed_in(client, mailer, email='spam@example.com')
    for k in ks[:-1]:
        assert client.post(f"{B}/{k['id']}/report", json={'reason': 'meh meh'}).status_code == 200
    r = client.post(f"{B}/{ks[-1]['id']}/report", json={'reason': 'meh meh'})
    assert r.status_code == 409 and 'too many' in r.get_json()['error']
    db.resolve_reports(con, ks[0]['id'], 'dismissed')               # one resolved → room for one more
    assert client.post(f"{B}/{ks[-1]['id']}/report", json={'reason': 'meh meh'}).status_code == 200


def test_open_reports_listing_shape(con):
    s, k = _target(con)
    r1 = _mk_user(con, email='r1@example.com', handle='r1-guy')
    assert db.create_report(con, k['id'], s['id'], r1['id'], 'fake') is not None
    assert db.create_report(con, k['id'], s['id'], r1['id'], 'fake again') is None
    rows = db.open_reports(con)
    assert len(rows) == 1
    row = rows[0]
    assert row['tag'] == k['tag'] and row['owner_handle'] == 's-guy' and row['reporter_handle'] == 'r1-guy'
    assert row['reason'] == 'fake' and row['hidden_at'] is None and row['knife_id'] == k['id']
    assert db.resolve_reports(con, k['id'], 'restored: fine') == 1
    assert db.open_reports(con) == []
