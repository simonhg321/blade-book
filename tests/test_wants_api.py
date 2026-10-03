# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
from tests.conftest import signed_in

W = '/blade-book/api/wants'


def test_wants_crud_roundtrip(client, mailer):
    signed_in(client, mailer)
    assert client.get(W).get_json() == {'wants': []}
    r = client.post(W, json={'model': 'Sebenza', 'mode': 'sale', 'max_price': 500})
    assert r.status_code == 200
    w = r.get_json()
    assert w['model'] == 'Sebenza' and w['active'] == 1
    assert client.patch(f"{W}/{w['id']}", json={'active': False}).status_code == 200
    assert client.get(W).get_json()['wants'][0]['active'] == 0
    assert client.delete(f"{W}/{w['id']}").status_code == 200
    assert client.get(W).get_json() == {'wants': []}


def test_wants_validation_and_cap(client, mailer):
    signed_in(client, mailer)
    assert client.post(W, json={'mode': 'steal'}).status_code == 400
    assert client.post(W, json={'nope': 1}).status_code == 400
    assert client.post(W, json=[1]).status_code == 400
    from bb import db
    for _ in range(db.MAX_ACTIVE_WANTS):
        assert client.post(W, json={'model': 'Sebenza'}).status_code == 200
    assert client.post(W, json={'model': 'Inkosi'}).status_code == 409


def test_wants_require_auth(client):
    assert client.get(W).status_code == 401
    assert client.post(W, json={}).status_code == 401


def test_wants_are_owner_scoped(client, mailer):
    signed_in(client, mailer)
    w = client.post(W, json={'model': 'Sebenza'}).get_json()
    client.post('/blade-book/api/auth/signout')
    signed_in(client, mailer, email='two@example.com')
    assert client.get(W).get_json() == {'wants': []}
    assert client.patch(f"{W}/{w['id']}", json={'active': False}).status_code == 404
    assert client.delete(f"{W}/{w['id']}").status_code == 404


def test_wants_json_validation(client, mailer):
    signed_in(client, mailer)
    # Test that non-dict JSON comes back 400
    assert client.post(W, json={'model': ['x']}).status_code == 400
