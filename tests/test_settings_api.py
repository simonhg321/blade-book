# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
from bb import db
from tests.conftest import signed_in
from tests.test_search import _mk_knife

S = '/blade-book/api/settings'


def test_settings_roundtrip(client, mailer):
    signed_in(client, mailer)
    r = client.get('/blade-book/api/settings')
    assert r.status_code == 200
    j = r.get_json()
    assert j['hide_born_day'] == 0 and j['profile_private'] == 0 and j['has_key'] is False
    assert j['public_url'].endswith('/blade-book/@' + j['handle'])
    r = client.patch('/blade-book/api/settings', json={'hide_born_day': True,
                                                       'public_key': 'ozzy'})
    j = r.get_json()
    assert r.status_code == 200 and j['hide_born_day'] == 1 and j['has_key'] is True
    assert 'public_key' not in j            # never echo the key
    r = client.patch('/blade-book/api/settings', json={'public_key': ''})
    assert r.get_json()['has_key'] is False


def test_settings_validation(client, mailer):
    signed_in(client, mailer)
    assert client.patch(S, json=[1]).status_code == 400
    assert client.patch(S, json={'hide_born_day': 'yes'}).status_code == 400
    assert client.patch(S, json={'public_key': 'x' * 65}).status_code == 400
    assert client.patch(S, json={'is_admin': True}).status_code == 400
    assert client.patch(S, json={}).status_code == 200


def test_settings_requires_auth(client):
    assert client.get(S).status_code == 401
    assert client.patch(S, json={}).status_code == 401


def test_settings_change_schedules_publish(client, mailer, monkeypatch):
    signed_in(client, mailer)
    calls = []
    monkeypatch.setattr('bb.routes.settings.publish',
                        type('P', (), {'schedule': staticmethod(calls.append)})())
    client.patch(S, json={'profile_private': True})
    assert len(calls) == 1


def test_share_email_on_intro_roundtrip(client, mailer):
    signed_in(client, mailer)
    r = client.get(S)
    assert r.get_json()['share_email_on_intro'] == 0          # opt-in since review H4
    r = client.patch(S, json={'share_email_on_intro': True})
    assert r.status_code == 200 and r.get_json()['share_email_on_intro'] == 1
    r = client.patch(S, json={'share_email_on_intro': False})
    assert r.status_code == 200 and r.get_json()['share_email_on_intro'] == 0
    r = client.get(S)
    assert r.get_json()['share_email_on_intro'] == 0
    r = client.patch(S, json={'share_email_on_intro': True})
    assert r.status_code == 200 and r.get_json()['share_email_on_intro'] == 1


def test_share_email_on_intro_validation(client, mailer):
    signed_in(client, mailer)
    assert client.patch(S, json={'share_email_on_intro': 'yes'}).status_code == 400


def test_featured_knife_id_roundtrip(client, mailer, con):
    me = signed_in(client, mailer)
    k = _mk_knife(con, me['id'])
    r = client.get(S)
    assert r.get_json()['featured_knife_id'] is None
    r = client.patch(S, json={'featured_knife_id': k['id']})
    assert r.status_code == 200 and r.get_json()['featured_knife_id'] == k['id']
    r = client.get(S)
    assert r.get_json()['featured_knife_id'] == k['id']
    r = client.patch(S, json={'featured_knife_id': None})
    assert r.status_code == 200 and r.get_json()['featured_knife_id'] is None


def test_featured_knife_id_validation(client, mailer, con):
    me = signed_in(client, mailer)
    other = db.create_user(con, 'other@example.com', 'other-guy')
    other_knife = _mk_knife(con, other)                           # not owned by the caller
    draft = db.create_draft_knife(con, me['id'])                  # no photo, still a draft

    for bad in (other_knife['id'], draft['id'], 9999999, 'K01', 1.5, True):
        r = client.patch(S, json={'featured_knife_id': bad})
        assert r.status_code == 400, bad
        assert r.get_json()['error'] == 'featured_knife_id must be one of your live knives with a photo'
    assert client.get(S).get_json()['featured_knife_id'] is None


def test_featured_knife_id_change_schedules_publish(client, mailer, con, monkeypatch):
    me = signed_in(client, mailer)
    k = _mk_knife(con, me['id'])
    calls = []
    monkeypatch.setattr('bb.routes.settings.publish',
                        type('P', (), {'schedule': staticmethod(calls.append)})())
    client.patch(S, json={'featured_knife_id': k['id']})
    assert len(calls) == 1


# --- a password on any account (2026-10-19) ----------------------------------------
# Sign in from a machine where the email is out of reach: the owner sets a
# password from settings and the existing handle+password route takes it.

AUTH = '/blade-book/api/auth'


def _pw_sign_in(client, handle, password):
    return client.post(AUTH + '/password', json={'handle': handle, 'password': password},
                       headers={'X-Forwarded-For': '10.0.0.9'})


def test_set_password_then_sign_in_by_handle(client, mailer):
    me = signed_in(client, mailer)
    assert client.get(S).get_json()['has_password'] is False
    r = client.post(S + '/password', json={'password': 'correct horse battery'})
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j['has_password'] is True
    assert j['email'] == 'sam@example.com'           # the email stays; this is an extra door
    client.post(AUTH + '/signout')
    assert client.get(AUTH + '/me').status_code == 401
    r = _pw_sign_in(client, me['handle'], 'correct horse battery')
    assert r.status_code == 200, r.data
    assert client.get(AUTH + '/me').get_json()['handle'] == me['handle']


def test_set_password_replaces_the_old_one(client, mailer):
    me = signed_in(client, mailer)
    client.post(S + '/password', json={'password': 'first password here'})
    client.post(S + '/password', json={'password': 'second password here'})
    client.post(AUTH + '/signout')
    assert _pw_sign_in(client, me['handle'], 'first password here').status_code == 401
    assert _pw_sign_in(client, me['handle'], 'second password here').status_code == 200


def test_remove_password_closes_that_door(client, mailer):
    me = signed_in(client, mailer)
    client.post(S + '/password', json={'password': 'correct horse battery'})
    r = client.post(S + '/password', json={'password': None})
    assert r.status_code == 200 and r.get_json()['has_password'] is False
    client.post(AUTH + '/signout')
    assert _pw_sign_in(client, me['handle'], 'correct horse battery').status_code == 401


def test_password_rules(client, mailer):
    signed_in(client, mailer)
    assert client.post(S + '/password', json={'password': 'short'}).status_code == 400
    assert client.post(S + '/password', json={'password': 'x' * 257}).status_code == 400
    assert client.post(S + '/password', json={'password': 12345678901234}).status_code == 400
    assert client.post(S + '/password', json={}).status_code == 400
    assert client.post(S + '/password', json=[1]).status_code == 400
    assert client.get(S).get_json()['has_password'] is False


def test_password_requires_auth(client):
    assert client.post(S + '/password', json={'password': 'correct horse battery'}).status_code == 401


def test_invited_account_cannot_remove_its_only_door(client, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'correct horse battery')
    assert _pw_sign_in(client, 'riverstone', 'correct horse battery').status_code == 200
    r = client.post(S + '/password', json={'password': None})
    assert r.status_code == 400
    assert 'email' in r.get_json()['error']
    assert client.get(S).get_json()['has_password'] is True
