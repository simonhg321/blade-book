# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from bb import db
from tests.conftest import magic_link_from


def test_request_link_sends_mail_and_never_reveals_existence(client, mailer):
    r = client.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    assert r.status_code == 202 and r.get_json() == {'ok': True}
    assert len(mailer.sent) == 1
    assert mailer.sent[0]['to'] == 'sam@example.com'
    link = magic_link_from(mailer)
    assert link.startswith('http://test/blade-book/api/auth/magic?t=')


def test_request_link_rejects_bad_email(client, mailer):
    for bad in ('', 'nope', 'a@b', 'x' * 300 + '@example.com'):
        r = client.post('/blade-book/api/auth/magic', json={'email': bad})
        assert r.status_code == 400, bad
    assert mailer.sent == []


def test_click_creates_user_signs_in_and_verifies(client, mailer, env):
    client.post('/blade-book/api/auth/magic', json={'email': 'Sam.Smith@example.com'})
    link = magic_link_from(mailer)
    r = client.get(link)
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    cookie = r.headers.get('Set-Cookie', '')
    assert 'bb_session=' in cookie and 'HttpOnly' in cookie
    assert 'Path=/blade-book' in cookie and 'SameSite=Lax' in cookie
    assert 'Secure' not in cookie  # BASE_URL is http in tests

    me = client.get('/blade-book/api/auth/me')
    assert me.status_code == 200
    body = me.get_json()
    assert body['email'] == 'sam.smith@example.com'
    assert body['handle'] == 'sam-smith'
    assert body['verified_at'] and body['is_admin'] == 0
    assert 'session_secret' not in body and 'auth_subjects' not in body

    con = db.connect()
    assert db.get_user(con, body['id'])['session_secret'] != ''


def test_click_twice_fails_second_time(client, mailer):
    client.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    link = magic_link_from(mailer)
    assert client.get(link).status_code == 302
    r = client.get(link)
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/?auth=expired')


def test_bad_or_missing_token_redirects_expired(client):
    for url in ('/blade-book/api/auth/magic', '/blade-book/api/auth/magic?t=garbage'):
        r = client.get(url)
        assert r.status_code == 302 and r.headers['Location'].endswith('?auth=expired')


def test_second_sign_in_reuses_the_same_user(client, mailer):
    for _ in range(2):
        client.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
        client.get(magic_link_from(mailer))
    con = db.connect()
    assert con.execute('SELECT count(*) FROM users').fetchone()[0] == 1
    assert client.get('/blade-book/api/auth/me').get_json()['handle'] == 'sam'


def test_me_is_401_when_signed_out(client):
    assert client.get('/blade-book/api/auth/me').status_code == 401
    assert client.get('/blade-book/api/auth/me').get_json() == {'error': 'sign in required'}


def test_signout_clears_this_device_only(app, mailer):
    a, b = app.test_client(), app.test_client()
    a.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    a.get(magic_link_from(mailer))
    b.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    b.get(magic_link_from(mailer))
    assert a.post('/blade-book/api/auth/signout').status_code == 200
    assert a.get('/blade-book/api/auth/me').status_code == 401
    assert b.get('/blade-book/api/auth/me').status_code == 200


def test_signout_all_rotates_secret_and_kills_every_device(app, mailer):
    a, b = app.test_client(), app.test_client()
    a.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    a.get(magic_link_from(mailer))
    b.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    b.get(magic_link_from(mailer))
    assert a.post('/blade-book/api/auth/signout-all').status_code == 200
    assert a.get('/blade-book/api/auth/me').status_code == 401
    assert b.get('/blade-book/api/auth/me').status_code == 401
    assert b.post('/blade-book/api/auth/signout-all').status_code == 401


def test_secure_cookie_when_base_url_is_https(env, mailer, monkeypatch):
    monkeypatch.setenv('SESSION_KEY', 'k')
    monkeypatch.setenv('BASE_URL', 'https://blade-book.com')
    from app import create_app
    c = create_app(mailer=mailer).test_client()
    c.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    link = magic_link_from(mailer)
    assert link.startswith('https://blade-book.com/blade-book/api/auth/magic?t=')
    r = c.get(link.replace('https://blade-book.com', ''), base_url='https://blade-book.com')
    assert 'Secure' in r.headers.get('Set-Cookie', '')


def test_login_required_decorator_sets_g_user(app, mailer):
    from flask import g, jsonify
    from bb import auth

    @app.route('/blade-book/api/_whoami')
    @auth.login_required
    def whoami():
        return jsonify({'handle': g.user['handle']})

    c = app.test_client()
    assert c.get('/blade-book/api/_whoami').status_code == 401
    c.post('/blade-book/api/auth/magic', json={'email': 'sam@example.com'})
    c.get(magic_link_from(mailer))
    assert c.get('/blade-book/api/_whoami').get_json() == {'handle': 'sam'}
