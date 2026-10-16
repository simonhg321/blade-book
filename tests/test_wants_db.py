# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import pytest

from bb import db


def _u(con, email='w@example.com', handle='want-guy', **over):
    uid = db.create_user(con, email, handle)
    if over:
        cols = ', '.join(f'{c} = ?' for c in over)
        con.execute(f'UPDATE users SET {cols} WHERE id = ?', (*over.values(), uid))
        con.commit()
    return db.get_user(con, uid)


def test_create_and_list_wants(con):
    u = _u(con)
    w = db.create_want(con, u['id'], {'model': 'Sebenza', 'mode': 'sale', 'max_price': 500})
    assert w['id'] and w['model'] == 'Sebenza' and w['active'] == 1
    assert db.list_wants(con, u['id'])[0]['id'] == w['id']


def test_want_validation(con):
    u = _u(con)
    with pytest.raises(ValueError):
        db.create_want(con, u['id'], {'mode': 'steal'})
    with pytest.raises(ValueError):
        db.create_want(con, u['id'], {'nope': 1})
    with pytest.raises(ValueError):
        db.create_want(con, u['id'], {'born_from': 'x'})
    with pytest.raises(ValueError):
        db.create_want(con, u['id'], {'model': ['x']})
    w = db.create_want(con, u['id'], {'born_from': 1990.7, 'born_to': 2000})
    assert w['born_from'] == 1990


def test_active_wants_cap(con):
    u = _u(con)
    for _ in range(db.MAX_ACTIVE_WANTS):
        db.create_want(con, u['id'], {'model': 'Sebenza'})
    with pytest.raises(ValueError):
        db.create_want(con, u['id'], {'model': 'Inkosi'})
    db.set_want_active(con, u['id'], db.list_wants(con, u['id'])[0]['id'], False)
    db.create_want(con, u['id'], {'model': 'Inkosi'})   # room again


def test_toggle_and_delete_scoped_to_owner(con):
    a, b = _u(con), _u(con, email='b@example.com', handle='b-guy')
    w = db.create_want(con, a['id'], {'model': 'Sebenza'})
    assert db.set_want_active(con, b['id'], w['id'], False) is False
    assert db.delete_want(con, b['id'], w['id']) is False
    assert db.set_want_active(con, a['id'], w['id'], False) is True
    assert db.active_wants(con) == []
    assert db.delete_want(con, a['id'], w['id']) is True


def test_all_public_knives_exclusions(con):
    from tests.test_search import _mk_knife
    ok = _u(con, email='ok@example.com', handle='ok-guy')
    gated = _u(con, email='g@example.com', handle='g-guy', public_key='shh')
    private = _u(con, email='p@example.com', handle='p-guy', profile_private=1)
    for u in (ok, gated, private):
        _mk_knife(con, u['id'])
    rows = db.all_public_knives(con)
    assert [r['owner_handle'] for r in rows] == ['ok-guy']
    assert rows[0]['owner_email'] == 'ok@example.com'
    assert rows[0]['owner_share_email'] == 0            # opt-in default (review H4)
    assert 'ext' in rows[0] and 'photos' in rows[0]


def test_claim_intro_fires_once(con):
    from tests.test_search import _mk_knife
    a, b = _u(con), _u(con, email='b@example.com', handle='b-guy')
    k = _mk_knife(con, b['id'])
    w = db.create_want(con, a['id'], {'model': 'Sebenza'})
    i = db.claim_intro(con, w['id'], k['id'], a['id'], b['id'])
    assert i is not None
    assert db.claim_intro(con, w['id'], k['id'], a['id'], b['id']) is None
    assert len(db.unsent_intros(con)) == 1
    db.mark_intro_sent(con, i, 're_123')
    assert db.unsent_intros(con) == []


def test_schema_v15_born_day_column(con):
    assert 'born_day' in {r[1] for r in con.execute('PRAGMA table_info(wants)')}
    assert 'born_day' in db.WANT_FIELDS and 'born_day' in db.WANT_CRITERIA


def test_born_day_want_validation(con):
    u = _u(con)
    w = db.create_want(con, u['id'], {'born_day': '07-28'})          # on its own it says what is wanted
    assert w['born_day'] == '07-28' and w['mode'] == 'either'
    assert db.create_want(con, u['id'], {'born_day': '02-29'})['born_day'] == '02-29'
    for bad in ('7/28', '7-28', '13-01', '02-30', '00-10', '07-28-1999', 728, '', '  ', ['07-28']):
        with pytest.raises(ValueError) as e:
            db.create_want(con, u['id'], {'born_day': bad})
        assert str(e.value) == 'bad field', bad
