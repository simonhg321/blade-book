# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""/api/admin/* — report queue + hide/restore/delete with a note (plan 09)."""
from bb import db, publish
from tests.conftest import signed_in
from tests.test_board_db import _old_knife, _seller
from tests.test_search import _mk_knife, _mk_user

A = '/blade-book/api/admin'
B = '/blade-book/api/board'


def _admin(client, mailer, con):
    me = signed_in(client, mailer, email='admin@example.com')
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],)); con.commit()
    return me


def _reported_listing(con, n=1):
    s = _seller(con); _old_knife(con, s['id'])
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500)
    for i in range(n):
        r = _mk_user(con, email=f'r{i}@example.com', handle=f'r{i}-guy')
        db.create_report(con, k['id'], s['id'], r['id'], f'reason {i}')
    return s, k


def test_admin_routes_are_gated(client, mailer, con):
    s, k = _reported_listing(con)
    assert client.get(A + '/reports').status_code == 401
    signed_in(client, mailer, email='pleb@example.com')
    assert client.get(A + '/reports').status_code == 403
    for action in ('hide', 'restore', 'delete'):
        assert client.post(f"{A}/knives/{k['id']}/{action}", json={'note': 'x'}).status_code == 403
    assert db.get_knife_any(con, k['id'])['hidden_at'] is None


def test_admin_queue_hide_restore(client, mailer, con, monkeypatch):
    scheduled = []
    monkeypatch.setattr(publish, 'schedule', lambda owner_id: scheduled.append(owner_id))
    s, k = _reported_listing(con, n=2)
    _admin(client, mailer, con)
    j = client.get(A + '/reports').get_json()
    assert len(j['reports']) == 2 and j['reports'][0]['tag'] == k['tag'] and j['reports'][0]['owner_handle'] == 's-guy'
    assert client.post(f"{A}/knives/{k['id']}/hide", json={}).status_code == 400            # note required
    assert client.post(f"{A}/knives/{k['id']}/hide", json={'note': 'x' * 501}).status_code == 400
    assert client.post(f"{A}/knives/999999/hide", json={'note': 'x'}).status_code == 404
    r = client.post(f"{A}/knives/{k['id']}/hide", json={'note': 'stolen photos'})
    assert r.status_code == 200 and r.get_json() == {'ok': True}
    any_k = db.get_knife_any(con, k['id'])
    assert any_k['hidden_by'] == 'admin' and any_k['hidden_note'] == 'stolen photos'
    assert client.get(A + '/reports').get_json()['reports'] == []
    res = [r['resolution'] for r in con.execute('SELECT resolution FROM reports WHERE knife_id = ?', (k['id'],))]
    assert res == ['hidden: stolen photos'] * 2
    assert client.get(B).get_json()['count'] == 0
    r = client.post(f"{A}/knives/{k['id']}/restore", json={'note': 'owner proved it'})
    assert r.status_code == 200 and db.get_knife_any(con, k['id'])['hidden_at'] is None
    assert client.get(B).get_json()['count'] == 1
    assert scheduled == [s['id'], s['id']]


def test_admin_delete_removes_knife_and_photos(client, mailer, con, monkeypatch):
    scheduled = []
    monkeypatch.setattr(publish, 'schedule', lambda owner_id: scheduled.append(owner_id))
    s, k = _reported_listing(con)
    _admin(client, mailer, con)
    r = client.post(f"{A}/knives/{k['id']}/delete", json={'note': 'scam'})
    assert r.status_code == 200
    assert db.get_knife_any(con, k['id']) is None
    assert con.execute('SELECT count(*) FROM photos WHERE knife_id = ?', (k['id'],)).fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM reports WHERE knife_id = ?', (k['id'],)).fetchone()[0] == 0  # cascade
    assert scheduled == [s['id']]
    assert client.post(f"{A}/knives/{k['id']}/delete", json={'note': 'again'}).status_code == 404


def test_admin_reports_json_carries_no_private_bytes(client, mailer, con):
    s, k = _reported_listing(con)
    con.execute("UPDATE knives SET notes_private = 'LEAK-PRIV-9F3A', price_paid = 4242 WHERE id = ?", (k['id'],)); con.commit()
    _admin(client, mailer, con)
    body = client.get(A + '/reports').data.decode()
    assert 'LEAK-PRIV-9F3A' not in body and '4242' not in body and 's@example.com' not in body
