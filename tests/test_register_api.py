# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import io

from PIL import Image

from bb import db
from tests.conftest import signed_in

K = '/blade-book/api/knives'


def _jpeg():
    b = io.BytesIO(); Image.new('RGB', (40, 40), (200, 30, 30)).save(b, 'JPEG'); return b.getvalue()


def _draft_with_photo(client):
    kid = client.post(K + '/').get_json()['id']
    r = client.post(f'{K}/{kid}/photos/1', data={'photo': (io.BytesIO(_jpeg()), 'a.jpg')},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    return kid


def test_save_publishes_and_reports_age(client, mailer):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = client.post(f'{K}/{kid}/save')
    assert r.status_code == 400 and 'photo' in r.get_json()['error']
    kid = _draft_with_photo(client)
    client.patch(f'{K}/{kid}', json={'born_on': '2011-12'})
    r = client.post(f'{K}/{kid}/save')
    assert r.status_code == 200
    j = r.get_json()
    assert j['status'] == 'live' and j['age_months'] > 12
    assert client.post(f'{K}/{kid}/save').status_code == 200          # idempotent
    con = db.connect()
    assert [e['type'] for e in db.list_events(con, j['owner_id'], kid)] == ['photographed']


def test_patch_validates_merges_ext_and_logs_edited_on_live(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    r = client.patch(f'{K}/{kid}', json={'model': 'Sebenza', 'ext': {'size': 'Large', 'generation': '31'},
                                          'price_paid': 450, 'hero_photo': 1})
    assert r.status_code == 200
    j = r.get_json()
    assert j['model'] == 'Sebenza' and j['ext']['size'] == 'Large' and j['price_paid'] == 450
    assert j['hero_photo'] == 1 and j['flags'] == []
    r = client.patch(f'{K}/{kid}', json={'ext': {'crk_sku': 'S31-0001'}})               # partial ext merges
    assert r.get_json()['ext'] == {'size': 'Large', 'generation': '31', 'crk_sku': 'S31-0001'}
    assert any('Small' in f for f in r.get_json()['flags'])                            # SKU vs size flag
    r = client.patch(f'{K}/{kid}', json={'condition': 9})
    assert r.status_code == 400 and 'condition' in r.get_json()['error']
    r = client.patch(f'{K}/{kid}', json={'status': 'live'})
    assert r.status_code == 400
    r = client.patch(f'{K}/{kid}', json={'hero_photo': 3})
    assert r.status_code == 400
    assert client.patch(f'{K}/{kid}', data='nope', content_type='text/plain').status_code == 400
    con = db.connect(); owner = client.get('/blade-book/api/auth/me').get_json()['id']
    assert db.list_events(con, owner, kid) == []                                       # drafts: no edited events
    client.post(f'{K}/{kid}/save')
    client.patch(f'{K}/{kid}', json={'model': 'Sebenza', 'notes_public': 'my first'})  # model unchanged
    ev = db.list_events(con, owner, kid)
    assert [e['type'] for e in ev] == ['photographed', 'edited'] and ev[-1]['detail'] == 'notes_public'
    client.patch(f'{K}/{kid}', json={'model': 'Sebenza'})                              # nothing changed → no event
    assert len(db.list_events(con, owner, kid)) == 2
    client.patch(f'{K}/{kid}', json={'variant': ''})                                   # '' vs NULL: no new event
    assert len(db.list_events(con, owner, kid)) == 2


def test_sale_controls(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 500})
    assert r.status_code == 409                                                        # draft
    client.post(f'{K}/{kid}/save')
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale'})
    assert r.status_code == 400 and 'asking' in r.get_json()['error']
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 500, 'seller_note': 'mint'})
    assert r.status_code == 200 and r.get_json()['sale_status'] == 'for_sale' and r.get_json()['asking_price'] == 500
    assert client.post(f'{K}/{kid}/sale', json={'sale_status': 'lost'}).status_code == 400
    assert client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': -5}).status_code == 400
    assert client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 5, 'seller_note': 'x' * 501}).status_code == 400
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'sold', 'amount': 450, 'counterparty': '@bob'})
    assert r.status_code == 200 and r.get_json()['sale_status'] == 'sold'
    con = db.connect(); owner = r.get_json()['owner_id']
    assert [(e['type'], e['amount']) for e in db.list_events(con, owner, kid)][1:] == [('for_sale', 500), ('sold', 450)]


def test_public_toggle_bulk_and_full(client, mailer, app):
    signed_in(client, mailer)
    k1 = _draft_with_photo(client); k2 = _draft_with_photo(client)
    client.post(f'{K}/{k1}/save')
    r = client.post(f'{K}/{k1}/public', json={'is_public': False})
    assert r.status_code == 200 and r.get_json()['is_public'] == 0
    assert client.post(f'{K}/{k1}/public', json={'is_public': 'no'}).status_code == 400
    other = app.test_client(); signed_in(other, mailer, 'b@example.com')
    kb = other.post(K + '/').get_json()['id']
    r = client.post(f'{K}/bulk', json={'ids': [k1, k2, kb], 'is_public': True})
    assert r.status_code == 200 and r.get_json()['changed'] == 2                       # kb is not ours
    assert other.get(f'{K}/{kb}').get_json()['is_public'] == 1
    assert client.post(f'{K}/bulk', json={'ids': 'all', 'is_public': True}).status_code == 400
    assert client.post(f'{K}/bulk', json={'ids': list(range(201)), 'is_public': True}).status_code == 400
    r = client.get(f'{K}/full')
    assert r.status_code == 200
    ks = r.get_json()['knives']
    assert [k['id'] for k in ks] == [k2, k1]
    assert ks[1]['status'] == 'live' and ks[1]['events'][0]['type'] == 'photographed'
    assert ks[1]['photos'][0]['seq'] == 1 and 'flags' in ks[1] and 'age_months' in ks[1]
    assert 'card_text' in ks[1] and 'price_paid' in ks[1]                               # owner view: private columns present
    assert other.get(f'{K}/full').get_json()['knives'][0]['id'] == kb


def test_live_knife_photos_delete_and_redecode(client, mailer, decoder):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    client.post(f'{K}/{kid}/save')
    r = client.post(f'{K}/{kid}/photos/2', data={'photo': (io.BytesIO(_jpeg()), 'b.jpg')}, content_type='multipart/form-data')
    assert r.status_code == 201                                                        # live: upload allowed
    assert client.delete(f'{K}/{kid}/photos/1').status_code == 200                     # two → one: fine
    r = client.delete(f'{K}/{kid}/photos/2')
    assert r.status_code == 409 and 'at least one' in r.get_json()['error']            # last photo stays
    assert client.post(f'{K}/{kid}/decode').status_code == 200                         # live: re-decode allowed
    assert len(decoder.calls) == 1
    assert client.delete(f'{K}/{kid}').status_code == 200                              # live: delete allowed
    assert client.get(f'{K}/{kid}').status_code == 404


def test_delete_photo_clears_dangling_hero_photo(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    r = client.post(f'{K}/{kid}/photos/2', data={'photo': (io.BytesIO(_jpeg()), 'b.jpg')},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    r = client.patch(f'{K}/{kid}', json={'hero_photo': 2})
    assert r.status_code == 200 and r.get_json()['hero_photo'] == 2
    assert client.delete(f'{K}/{kid}/photos/2').status_code == 200        # delete the hero
    r = client.get(f'{K}/{kid}')
    assert r.status_code == 200 and r.get_json()['hero_photo'] is None


def test_non_object_json_bodies_are_400(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    client.post(f'{K}/{kid}/save')
    for path in (f'{K}/{kid}/sale', f'{K}/{kid}/public', f'{K}/bulk', f'{K}/{kid}'):
        for body in ([1, 2, 3], 'x', 7):
            r = client.open(path, method='PATCH' if path.endswith(str(kid)) else 'POST', json=body)
            assert r.status_code == 400, (path, body, r.status_code)
