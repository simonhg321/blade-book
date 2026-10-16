# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""GET /blade-book/api/born — the date board (spec 2026-10-15 §2)."""
from bb import db
from tests.test_search import _mk_knife, _mk_user

N = '/blade-book/api/born'


def _seed(con):
    u = _mk_user(con, email='d@example.com', handle='d-guy')
    exact = _mk_knife(con, u['id'], born='1999-07-28')
    same = _mk_knife(con, u['id'], born='2022-07-28', sale_status='for_sale', asking_price=650)
    near = _mk_knife(con, u['id'], born='2020-07-31')
    far = _mk_knife(con, u['id'], born='2020-08-10')
    return u, exact, same, near, far


def test_born_groups_and_card_shape(client, con):
    u, exact, same, near, far = _seed(con)
    r = client.get(N + '?d=7/28/99')
    assert r.status_code == 200
    j = r.get_json()
    assert j['query'] == {'year': 1999, 'month': 7, 'day': 28, 'label': 'July 28, 1999', 'url': '1999-07-28', 'short': '7/28'}
    assert [k['id'] for k in j['exact']] == [exact['id']]
    assert [k['id'] for k in j['same_day']] == [same['id']]
    assert [k['id'] for k in j['near']] == [near['id']] and j['near'][0]['days_off'] == 3
    c = j['same_day'][0]
    assert c['handle'] == 'd-guy' and c['tag'] == same['tag'] and c['born_on'] == '2022-07-28'
    assert c['for_sale'] == 1 and c['asking_price'] == 650 and c['days_off'] == 0
    assert j['exact'][0].get('for_sale', 0) == 0 and 'asking_price' not in j['exact'][0]
    for g in ('exact', 'same_day', 'near'):
        for card in j[g]:
            assert 'owner_email' not in card and 'email' not in card and 'price_paid' not in card


def test_born_without_year_and_public_access(client, con):
    _seed(con)
    j = client.get(N + '?d=July%2028').get_json()
    assert j['query']['year'] is None and j['query']['url'] == '07-28'
    assert j['exact'] == [] and len(j['same_day']) == 2 and len(j['near']) == 1
    assert [k['born_on'] for k in j['same_day']] == ['2022-07-28', '1999-07-28']


def test_born_bad_input_is_400(client):
    for q in ('', '?d=', '?d=abc', '?d=13/40', '?d=' + 'x' * 40):
        r = client.get(N + q)
        assert r.status_code == 400 and r.get_json() == {'error': 'I need at least a month and a day'}, q


def test_born_hides_what_the_board_hides(client, con):
    u = _mk_user(con, email='h@example.com', handle='h-guy')
    shown = _mk_knife(con, u['id'], born='2022-07-28')
    _mk_knife(con, u['id'], born='2022-07-28', is_public=0)
    _mk_knife(con, u['id'], born='2022-07-28', hidden_at=db.now())
    mo = _mk_knife(con, u['id'], born='2022-07')
    con.execute("UPDATE knives SET born_on_precision = 'month' WHERE id = ?", (mo['id'],)); con.commit()
    assert [k['id'] for k in client.get(N + '?d=7/28').get_json()['same_day']] == [shown['id']]
    con.execute('UPDATE users SET hide_born_day = 1 WHERE id = ?', (u['id'],)); con.commit()
    j = client.get(N + '?d=7/28').get_json()
    assert j['same_day'] == [] and j['near'] == []
    con.execute("UPDATE users SET hide_born_day = 0, profile_private = 1 WHERE id = ?", (u['id'],)); con.commit()
    assert client.get(N + '?d=7/28').get_json()['same_day'] == []


def test_born_fresh_account_knife_is_findable(client, con):
    """No board-eligibility gate on the date board (spec §2)."""
    u = _mk_user(con, email='n@example.com', handle='n-guy')          # unverified, knife created now
    k = _mk_knife(con, u['id'], born='2022-07-28')
    assert [c['id'] for c in client.get(N + '?d=07-28').get_json()['same_day']] == [k['id']]
