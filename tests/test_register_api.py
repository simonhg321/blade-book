# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import io

from PIL import Image

from bb import db
from tests.conftest import signed_in

K = '/blade-book/api/knives'


def _jpeg():
    b = io.BytesIO(); Image.new('RGB', (40, 40), (200, 30, 30)).save(b, 'JPEG'); return b.getvalue()


def _processed(kid):
    # stamp what PROCESS leaves behind, without spending a decoder call
    con = db.connect(); con.execute("UPDATE knives SET confidence = '{}' WHERE id = ?", (kid,)); con.commit(); con.close()


def _draft_with_photo(client):
    kid = client.post(K + '/').get_json()['id']
    r = client.post(f'{K}/{kid}/photos/1', data={'photo': (io.BytesIO(_jpeg()), 'a.jpg')},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    return kid


def _age_knife(kid, days=8):
    """Backdate a knife's `created` so its owner is board-eligible (plan 09:
    listing for_sale needs a live knife ≥ 7 days old on the account)."""
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    con = db.connect(); con.execute('UPDATE knives SET created = ? WHERE id = ?', (ts, kid)); con.commit(); con.close()


def test_save_publishes_and_reports_age(client, mailer):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = client.post(f'{K}/{kid}/save')
    assert r.status_code == 400 and 'photo' in r.get_json()['error']
    _processed(kid)
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
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 500})
    assert r.status_code == 409 and 'board' in r.get_json()['error']                # fresh account: not eligible
    _age_knife(kid)
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
    k1 = _draft_with_photo(client); _processed(k1); k2 = _draft_with_photo(client)
    client.post(f'{K}/{k1}/save')
    r = client.post(f'{K}/{k1}/public', json={'is_public': False})
    assert r.status_code == 200 and r.get_json()['is_public'] == 0
    assert client.post(f'{K}/{k1}/public', json={'is_public': 'no'}).status_code == 400
    other = app.test_client(); signed_in(other, mailer, 'b@example.com')
    kb = other.post(K + '/').get_json()['id']
    r = client.post(f'{K}/bulk', json={'ids': [k1, k2, kb], 'is_public': True})
    assert r.status_code == 200 and r.get_json()['changed'] == 2                       # kb is not ours
    assert other.get(f'{K}/{kb}').get_json()['is_public'] == 0             # born private; a's bulk never touched it
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


def test_publish_schedule_hooks(client, mailer, monkeypatch):
    signed_in(client, mailer)
    calls = []
    monkeypatch.setattr('bb.routes.knives.publish.schedule', calls.append)

    draft = client.post(K + '/').get_json()['id']
    r = client.patch(f'{K}/{draft}', json={'model': 'Sebenza'})
    assert r.status_code == 200 and calls == []               # draft-only PATCH never schedules
    _processed(draft)                                          # unblock the next new draft

    kid = _draft_with_photo(client)
    r = client.post(f'{K}/{kid}/save')
    assert r.status_code == 200 and len(calls) == 1            # save

    r = client.patch(f'{K}/{kid}', json={'notes_public': 'hello'})
    assert r.status_code == 200 and len(calls) == 2            # live edit with a change

    r = client.patch(f'{K}/{kid}', json={'notes_public': 'hello'})   # unchanged: no new call
    assert r.status_code == 200 and len(calls) == 2

    _age_knife(kid)
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 500})
    assert r.status_code == 200 and len(calls) == 3            # sale change

    r = client.post(f'{K}/{kid}/public', json={'is_public': False})
    assert r.status_code == 200 and len(calls) == 4            # public toggle

    r = client.post(f'{K}/bulk', json={'ids': [kid], 'is_public': True})
    assert r.status_code == 200 and len(calls) == 5            # bulk

    r = client.delete(f'{K}/{kid}')
    assert r.status_code == 200 and len(calls) == 6            # delete of a live knife


def test_new_draft_requires_previous_draft_processed(client, mailer, decoder):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = client.post(K + '/')
    assert r.status_code == 409 and 'K01' in r.get_json()['error'] and 'process' in r.get_json()['error']
    client.post(f'{K}/{kid}/photos/1', data={'photo': (io.BytesIO(_jpeg()), 'a.jpg')},
                content_type='multipart/form-data')
    assert client.post(f'{K}/{kid}/decode').status_code == 200
    assert client.post(K + '/').status_code == 201          # processed → next tag opens
    con = db.connect()
    assert db.undecoded_draft_tag(con, client.get('/blade-book/api/auth/me').get_json()['id']) == 'K02'
    con.close()


def test_for_sale_gate_admin_bypass_and_listed_at(client, mailer):
    me = signed_in(client, mailer)
    kid = _draft_with_photo(client); _processed(kid)
    assert client.post(f'{K}/{kid}/save').status_code == 200
    assert client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 9}).status_code == 409
    assert client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_trade'}).status_code == 200   # trade is never gated
    con = db.connect(); con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],)); con.commit(); con.close()
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_sale', 'asking_price': 9})
    assert r.status_code == 200 and r.get_json()['listed_at']
    r = client.post(f'{K}/{kid}/sale', json={'sale_status': 'keeping'})
    assert r.status_code == 200 and r.get_json()['listed_at'] is None


# 2026-09-26, Simon: "by default it should all be private". A new knife is born private;
# the owner says so to show it — at save, with the toggle, or by listing it.

def test_new_knife_is_born_private(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    _processed(kid)
    j = client.post(f'{K}/{kid}/save').get_json()
    assert j['status'] == 'live' and j['is_public'] == 0


def test_save_can_show_it_on_the_public_page(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    _processed(kid)
    assert client.post(f'{K}/{kid}/save', json={'is_public': True}).get_json()['is_public'] == 1
    kid2 = _draft_with_photo(client)
    _processed(kid2)
    assert client.post(f'{K}/{kid2}/save', json={'is_public': False}).get_json()['is_public'] == 0
    assert client.post(f'{K}/{kid2}/save', json={'is_public': 'yes'}).status_code == 400


def test_resaving_a_live_knife_never_flips_its_visibility(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    _processed(kid)
    client.post(f'{K}/{kid}/save', json={'is_public': True})
    assert client.post(f'{K}/{kid}/save').get_json()['is_public'] == 1


def test_listing_a_private_knife_makes_it_public_and_says_so(client, mailer):
    signed_in(client, mailer)
    kid = _draft_with_photo(client)
    _processed(kid)
    client.post(f'{K}/{kid}/save')
    _age_knife(kid)
    con = db.connect(); con.execute("UPDATE users SET verified_at = '2026-01-01'"); con.commit(); con.close()
    j = client.post(f'{K}/{kid}/sale', json={'sale_status': 'for_trade'}).get_json()
    assert j['is_public'] == 1 and j['made_public'] is True
    j = client.post(f'{K}/{kid}/sale', json={'sale_status': 'keeping'}).get_json()
    assert j['is_public'] == 1 and 'made_public' not in j            # taking it off the table keeps it shown
