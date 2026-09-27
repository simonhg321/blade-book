# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from bb import db, search
from tests.conftest import signed_in
from tests.test_search import _mk_user, _mk_knife, _rows

S = '/blade-book/api/search'


def _seed(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    search.reindex_user(con, u, _rows(con, u))
    return u


def _entitle(con, email, sub_status='free', is_admin=0):
    con.execute('UPDATE users SET sub_status = ?, is_admin = ? WHERE email = ?',
                (sub_status, is_admin, email))
    con.commit()


def test_free_text_search_anonymous(client, con):
    _seed(con)
    r = client.get(S + '?q=sebenza')
    assert r.status_code == 200
    j = r.get_json()
    assert j['count'] == 1 and j['knives'][0]['name'] == 'Large Sebenza 21'
    assert j['aggregates']['models'] == [['Sebenza', 1]]


def test_empty_q_returns_everything(client, con):
    _seed(con)
    j = client.get(S).get_json()
    assert j['count'] == 1 and len(j['knives']) == 1


def test_filters_are_open_to_everyone_during_early_access(client, con, mailer, monkeypatch):
    """2026-09-26, Simon: 'make sure this stays free and open for now … once we get 50
    people we can think about charging.' With the hard gate off (the default) the read
    gate is open too: anonymous and free accounts can use every filter."""
    monkeypatch.delenv('BLADEBOOK_HARD_GATE', raising=False)
    _seed(con)
    assert client.get(S + '?year_from=2005').status_code == 200
    signed_in(client, mailer, email='free@example.com')
    assert client.get(S + '?smith=devin').status_code == 200
    assert client.get(S + '?who_min=2').status_code == 200


def test_filters_402_for_anonymous_and_free(client, con, mailer, monkeypatch):
    monkeypatch.setenv('BLADEBOOK_HARD_GATE', '1')
    _seed(con)
    assert client.get(S + '?year_from=2005').status_code == 402
    signed_in(client, mailer, email='free@example.com')   # sub_status defaults 'free'
    assert client.get(S + '?smith=devin').status_code == 402
    assert client.get(S + '?who_min=2').status_code == 402


def test_filters_pass_for_active_and_admin(client, con, mailer):
    _seed(con)
    signed_in(client, mailer, email='paid@example.com')
    _entitle(con, 'paid@example.com', sub_status='active')
    r = client.get(S + '?year_from=2005&year_to=2010')
    assert r.status_code == 200 and r.get_json()['count'] == 1
    _entitle(con, 'paid@example.com', sub_status='free', is_admin=1)
    assert client.get(S + '?who_min=1').status_code == 200


def test_bad_year_is_400_when_entitled(client, con, mailer):
    _seed(con)
    signed_in(client, mailer, email='adm@example.com')
    _entitle(con, 'adm@example.com', is_admin=1)
    assert client.get(S + '?year_from=x').status_code == 400
    assert client.get(S + '?who_min=x').status_code == 400
    # Regression: huge but parseable numbers overflow SQLite — must be 400, not 500
    assert client.get(S + '?year_from=999999999999999999999999999999').status_code == 400
    assert client.get(S + '?who_min=999999999999999999999999999999').status_code == 400


def test_q_length_capped_and_hostile_safe(client, con):
    _seed(con)
    assert client.get(S + '?q=' + 'a' * 500).status_code == 200
    assert client.get(S + '?q=%22%20OR%20%22').status_code == 200
