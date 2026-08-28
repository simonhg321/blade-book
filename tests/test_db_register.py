import pytest

from bb import db


def _owner(con, email='sam@example.com'):
    return db.create_user(con, email, email.split('@')[0] + '-collector')


def _knife(con, owner):
    return db.create_draft_knife(con, owner)


def test_update_knife_sets_columns_ext_and_updated(env):
    con = db.connect(); o = _owner(con); k = _knife(con, o)
    before = k['updated']
    k2 = db.update_knife(con, o, k['id'], {'model': 'Sebenza', 'ext': {'size': 'Large'}, 'price_paid': 450.0,
                                          'notes_public': 'first one'})
    assert k2['model'] == 'Sebenza' and k2['ext'] == {'size': 'Large'} and k2['price_paid'] == 450.0
    assert k2['notes_public'] == 'first one' and k2['updated'] >= before
    assert db.update_knife(con, o + 999, k['id'], {'model': 'x'}) is None       # not yours
    with pytest.raises(ValueError):
        db.update_knife(con, o, k['id'], {'status': 'live'})                   # not editable here
    with pytest.raises(ValueError):
        db.update_knife(con, o, k['id'], {'tag': 'K99'})


def test_publish_requires_a_photo_and_writes_photographed_once(env):
    con = db.connect(); o = _owner(con); k = _knife(con, o)
    assert db.publish_knife(con, o, k['id']) == (db.get_knife(con, o, k['id']), 'add a photo first')
    db.add_photo(con, o, k['id'], 1, 'p/1.jpg', 'a' * 64, 10, 10)
    k2, err = db.publish_knife(con, o, k['id'])
    assert err is None and k2['status'] == 'live'
    k3, err = db.publish_knife(con, o, k['id'])                                # idempotent
    assert err is None and k3['status'] == 'live'
    ev = db.list_events(con, o, k['id'])
    assert [e['type'] for e in ev] == ['photographed'] and '1 photo' in ev[0]['detail']
    assert db.publish_knife(con, o + 999, k['id']) == (None, 'not found')


def test_set_sale_events_only_on_change(env):
    con = db.connect(); o = _owner(con); k = _knife(con, o)
    kid = k['id']
    k = db.set_sale(con, o, kid, 'for_sale', asking_price=500, seller_note='mint')
    assert k['sale_status'] == 'for_sale' and k['asking_price'] == 500 and k['seller_note'] == 'mint'
    db.set_sale(con, o, kid, 'for_sale', asking_price=475)                    # price change, same status → no event
    db.set_sale(con, o, kid, 'keeping')                                       # leaving a listed state → withdrawn
    db.set_sale(con, o, kid, 'keeping')                                       # no-op
    db.set_sale(con, o, kid, 'sold', amount=450, counterparty='@bob')
    types = [(e['type'], e['amount']) for e in db.list_events(con, o, kid)]
    assert types == [('for_sale', 500), ('withdrawn', None), ('sold', 450)]
    assert db.list_events(con, o, kid)[-1]['counterparty'] == '@bob'
    with pytest.raises(ValueError):
        db.set_sale(con, o, kid, 'lost')
    assert db.set_sale(con, o + 999, kid, 'keeping') is None


def test_set_public_is_bulk_and_owner_scoped(env):
    con = db.connect(); a = _owner(con, 'a@example.com'); b = _owner(con, 'b@example.com')
    ka, kb = _knife(con, a), _knife(con, b)
    assert db.set_public(con, a, [ka['id'], kb['id']], False) == 1
    assert db.get_knife(con, a, ka['id'])['is_public'] == 0
    assert db.get_knife(con, b, kb['id'])['is_public'] == 1
    assert db.set_public(con, a, [], False) == 0
    assert db.set_public(con, a, [ka['id']], True) == 1


def test_delete_knife_any_status_returns_keys_and_removes_events(env):
    con = db.connect(); o = _owner(con); k = _knife(con, o)
    db.add_photo(con, o, k['id'], 1, 'p/1.jpg', 'a' * 64, 10, 10)
    db.publish_knife(con, o, k['id'])
    assert db.delete_knife(con, o + 999, k['id']) is None
    keys = db.delete_knife(con, o, k['id'])
    assert keys == ['p/1.jpg', db.thumb_key('p/1.jpg')]
    assert db.get_knife(con, o, k['id']) is None
    assert con.execute('SELECT count(*) FROM events WHERE knife_id = ?', (k['id'],)).fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM photos WHERE knife_id = ?', (k['id'],)).fetchone()[0] == 0


def test_full_register_newest_first_with_photos_and_events(env):
    con = db.connect(); o = _owner(con)
    k1, k2 = _knife(con, o), _knife(con, o)
    db.add_photo(con, o, k1['id'], 1, 'p/1.jpg', 'a' * 64, 10, 10)
    db.publish_knife(con, o, k1['id'])
    reg = db.full_register(con, o)
    assert [k['tag'] for k in reg] == [k2['tag'], k1['tag']]
    assert reg[1]['photos'][0]['seq'] == 1 and reg[1]['events'][0]['type'] == 'photographed'
    assert reg[0]['photos'] == [] and reg[0]['events'] == []
    assert db.full_register(con, o + 999) == []
