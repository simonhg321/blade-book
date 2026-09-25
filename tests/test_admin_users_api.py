# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""/api/admin/users + /sub — the ManualBilling flip from the admin page (plan 10)."""
import json

from bb import db
from tests.conftest import signed_in
from tests.test_search import _mk_knife, _mk_user

A = '/blade-book/api/admin'
K = '/blade-book/api/knives'


def _admin(client, mailer, con):
    me = signed_in(client, mailer, email='admin@example.com')
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],)); con.commit()
    return me


def test_users_routes_are_gated(client, mailer, con):
    u = _mk_user(con)
    assert client.get(A + '/users').status_code == 401
    assert client.post(f"{A}/users/{u['id']}/sub", json={'status': 'active'}).status_code == 401
    signed_in(client, mailer, email='pleb@example.com')
    assert client.get(A + '/users').status_code == 403
    assert client.post(f"{A}/users/{u['id']}/sub", json={'status': 'active'}).status_code == 403
    assert db.get_user(con, u['id'])['sub_status'] == 'free'


def test_users_list_and_flip(client, mailer, con):
    u = _mk_user(con, verified_at=db.now())
    _mk_knife(con, u['id'])
    me = _admin(client, mailer, con)
    j = client.get(A + '/users').get_json()
    handles = [r['handle'] for r in j['users']]
    assert set(handles) == {'idx-guy', me['handle']}
    row = next(r for r in j['users'] if r['handle'] == 'idx-guy')
    assert set(row) == set(db.ADMIN_USER_FIELDS) and row['knives'] == 1 and row['sub_status'] == 'free'
    for bad in ({'status': 'gold'}, {}, None, {'status': 3}):
        r = client.post(f"{A}/users/{u['id']}/sub", json=bad)
        assert r.status_code == 400 and 'free, active, lapsed' in r.get_json()['error'], bad
    assert client.post(f"{A}/users/999999/sub", json={'status': 'active'}).status_code == 404
    r = client.post(f"{A}/users/{u['id']}/sub", json={'status': 'active'})
    assert r.status_code == 200
    assert r.get_json()['ok'] is True and r.get_json()['user']['sub_status'] == 'active'
    assert set(r.get_json()['user']) == set(db.ADMIN_USER_FIELDS)   # Flask's JSON provider sorts keys
    assert db.get_user(con, u['id'])['sub_status'] == 'active'
    r = client.post(f"{A}/users/{u['id']}/sub", json={'status': 'lapsed'})
    assert r.status_code == 200 and db.get_user(con, u['id'])['sub_status'] == 'lapsed'


def test_flip_unlocks_the_gate_end_to_end(client, mailer, con, monkeypatch):
    monkeypatch.setenv('BLADEBOOK_HARD_GATE', '1')
    """The day-one path: a collector hits the 402, the admin flips them, the same draft saves."""
    from tests.test_billing_api import _draft
    victim = signed_in(client, mailer, email='collector@example.com')
    con.execute('UPDATE users SET free_old_used = 3 WHERE id = ?', (victim['id'],)); con.commit()
    k = _draft(con, victim['id'])
    assert client.post(f"{K}/{k['id']}/save").status_code == 402
    admin_client = client.application.test_client()
    me = signed_in(admin_client, mailer, email='admin@example.com')
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],)); con.commit()
    assert admin_client.post(f"{A}/users/{victim['id']}/sub", json={'status': 'active'}).status_code == 200
    assert client.post(f"{K}/{k['id']}/save").status_code == 200


def test_admin_users_json_leaks_no_secret_bytes(client, mailer, con):
    u = _mk_user(con, public_key='KEYSECRET1234', stripe_customer_id='cus_SECRET', session_secret='SESSSECRET')
    con.execute("UPDATE users SET auth_subjects = ? WHERE id = ?", (json.dumps({'google': 'SUBSECRET'}), u['id'])); con.commit()
    _admin(client, mailer, con)
    for body in (client.get(A + '/users').data,
                 client.post(f"{A}/users/{u['id']}/sub", json={'status': 'active'}).data):
        s = body.decode()
        for needle in ('KEYSECRET1234', 'cus_SECRET', 'SESSSECRET', 'SUBSECRET', 'session_secret',
                       'public_key', 'auth_subjects', 'stripe_customer_id', 'publish_dirty_at'):
            assert needle not in s, needle
