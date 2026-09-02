# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from tests.conftest import signed_in

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
    assert r.get_json()['share_email_on_intro'] == 1
    r = client.patch(S, json={'share_email_on_intro': False})
    assert r.status_code == 200 and r.get_json()['share_email_on_intro'] == 0
    r = client.get(S)
    assert r.get_json()['share_email_on_intro'] == 0
    r = client.patch(S, json={'share_email_on_intro': True})
    assert r.status_code == 200 and r.get_json()['share_email_on_intro'] == 1


def test_share_email_on_intro_validation(client, mailer):
    signed_in(client, mailer)
    assert client.patch(S, json={'share_email_on_intro': 'yes'}).status_code == 400
