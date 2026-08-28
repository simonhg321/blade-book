# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from bb import db
from tests.conftest import signed_in

K = '/blade-book/api/knives'


def test_everything_requires_sign_in(client):
    assert client.post(K + '/').status_code == 401
    assert client.get(K + '/').status_code == 401
    assert client.get(K + '/1').status_code == 401
    assert client.put(K + '/1/note', json={'note': 'x'}).status_code == 401
    assert client.delete(K + '/1').status_code == 401


def test_create_list_get(client, mailer):
    me = signed_in(client, mailer)
    r = client.post(K + '/')
    assert r.status_code == 201
    k = r.get_json()
    assert k['tag'] == 'K01' and k['status'] == 'draft' and k['photos'] == []
    assert client.post(K + '/').get_json()['tag'] == 'K02'
    lst = client.get(K + '/').get_json()['knives']
    assert [x['tag'] for x in lst] == ['K02', 'K01']
    assert lst[0]['photo_count'] == 0
    assert client.get(K + '/?status=live').get_json() == {'knives': []}
    one = client.get(f"{K}/{k['id']}").get_json()
    assert one['id'] == k['id'] and one['owner_id'] == me['id']
    assert 'store_key' not in str(one)


def test_other_users_knives_are_404(app, mailer):
    a, b = app.test_client(), app.test_client()
    signed_in(a, mailer, 'a@example.com')
    kid = a.post(K + '/').get_json()['id']
    signed_in(b, mailer, 'b@example.com')
    assert b.get(f'{K}/{kid}').status_code == 404
    assert b.put(f'{K}/{kid}/note', json={'note': 'mine now'}).status_code == 404
    assert b.delete(f'{K}/{kid}').status_code == 404
    assert b.get(K + '/').get_json() == {'knives': []}
    assert a.get(f'{K}/{kid}').status_code == 200


def test_note_round_trip_and_limits(client, mailer):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = client.put(f'{K}/{kid}/note', json={'note': '  Large 21, from a collector  '})
    assert r.status_code == 200 and r.get_json() == {'ok': True}
    assert client.get(f'{K}/{kid}').get_json()['notes_private'] == 'Large 21, from a collector'
    assert client.put(f'{K}/{kid}/note', json={'note': 'x' * 2001}).status_code == 400
    assert client.put(f'{K}/{kid}/note', json={}).status_code == 200  # empty clears
    assert client.get(f'{K}/{kid}').get_json()['notes_private'] == ''
    assert client.get(f'{K}/999').status_code == 404


def test_delete_draft_only(client, mailer, env):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    assert client.delete(f'{K}/{kid}').status_code == 200
    assert client.get(f'{K}/{kid}').status_code == 404
    kid2 = client.post(K + '/').get_json()['id']
    con = db.connect()
    con.execute("UPDATE knives SET status='live' WHERE id=?", (kid2,)); con.commit()
    assert client.delete(f'{K}/{kid2}').status_code == 404
    assert client.get(f'{K}/{kid2}').status_code == 200
    assert client.post(K + '/').get_json()['tag'] == 'K03'
