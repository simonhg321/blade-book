# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Handle + password sign-in for INVITED accounts only (2026-09-23, for one
collector who will not hand out an email address). There is no route that
creates such an account: only scripts/invite.py on the box does, so nobody
else can register without an email. The account's email is synthetic under
an RFC-reserved domain and nothing is ever mailed to it."""
from bb import auth, db, mail, match
from bb.mail import FakeMailer
from tests.conftest import signed_in
from tests.test_search import _mk_knife
from tests.test_wants_db import _u

API = '/blade-book/api/auth'


def _pw(client, handle, password, ip='10.0.0.1'):
    return client.post(API + '/password', json={'handle': handle, 'password': password},
                       headers={'X-Forwarded-For': ip})


def test_create_password_user_is_verified_with_synthetic_undeliverable_email(con):
    uid = db.create_password_user(con, 'riverstone', 'a collector', 'correct horse battery')
    u = db.get_user(con, uid)
    assert u['handle'] == 'riverstone' and u['display_name'] == 'a collector'
    assert u['email'] == f'riverstone@{db.NO_EMAIL_DOMAIN}'
    assert u['email'].endswith('.invalid')
    assert u['verified_at'] and u['session_secret']
    assert u['password_hash'] and 'correct horse battery' not in u['password_hash']
    assert not mail.deliverable(u['email'])
    assert mail.deliverable('sam@example.com')


def test_password_hash_is_salted_scrypt_and_verifies(con):
    a = auth.hash_password('pw-one')
    b = auth.hash_password('pw-one')
    assert a != b and a.startswith('scrypt$')
    assert auth.verify_password('pw-one', a) and auth.verify_password('pw-one', b)
    assert not auth.verify_password('pw-two', a)
    assert not auth.verify_password('pw-one', None) and not auth.verify_password('pw-one', '')


def test_password_route_signs_in_invited_user(client, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'correct horse battery')
    r = _pw(client, 'RiverStone ', 'correct horse battery')       # case/space tolerant on the handle
    assert r.status_code == 200, r.data
    assert r.get_json()['handle'] == 'riverstone'
    me = client.get(API + '/me')
    assert me.status_code == 200 and me.get_json()['handle'] == 'riverstone'
    assert me.get_json()['verified_at']


def test_wrong_password_and_unknown_handle_look_the_same(client, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'correct horse battery')
    bad = _pw(client, 'riverstone', 'wrong')
    unknown = _pw(client, 'nobody', 'wrong')
    assert bad.status_code == unknown.status_code == 401
    assert bad.get_json() == unknown.get_json()
    assert client.get(API + '/me').status_code == 401


def test_email_users_have_no_password_and_cannot_use_the_route(client, mailer, con):
    signed_in(client, mailer, 'sam@example.com')
    client.post(API + '/signout')
    u = db.get_user_by_email(con, 'sam@example.com')
    assert u['password_hash'] is None
    assert _pw(client, u['handle'], '').status_code == 400
    assert _pw(client, u['handle'], 'anything').status_code == 401


def test_missing_fields_are_400(client):
    assert client.post(API + '/password', json={}).status_code == 400
    assert client.post(API + '/password', json={'handle': 'x'}).status_code == 400
    assert client.post(API + '/password', json={'password': 'x'}).status_code == 400


def test_per_account_lockout_after_repeated_failures(client, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'correct horse battery')
    for i in range(auth.PASSWORD_FAILS_PER_HOUR):
        assert _pw(client, 'riverstone', 'wrong', ip=f'10.1.{i}.1').status_code == 401
    # locked — even the right password from a fresh IP waits an hour
    r = _pw(client, 'riverstone', 'correct horse battery', ip='10.2.2.2')
    assert r.status_code == 429 and 'hour' in r.get_json()['error']


def test_ip_limit_applies_to_password_attempts(client, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'correct horse battery')
    for i in range(auth.IP_ATTEMPTS_PER_HOUR):
        _pw(client, f'ghost{i}', 'x', ip='10.9.9.9')
    assert _pw(client, 'riverstone', 'correct horse battery', ip='10.9.9.9').status_code == 429


def test_set_password_replaces_the_old_one(client, con):
    uid = db.create_password_user(con, 'riverstone', 'a collector', 'old one')
    db.set_password(con, uid, 'new one')
    assert _pw(client, 'riverstone', 'old one').status_code == 401
    assert _pw(client, 'riverstone', 'new one').status_code == 200


def test_magic_link_refuses_the_synthetic_domain(client, mailer, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'pw')
    r = client.post(API + '/magic', json={'email': f'riverstone@{db.NO_EMAIL_DOMAIN}'})
    assert r.status_code == 400 and mailer.sent == []


def test_settings_view_has_no_email_for_a_password_user(client, con):
    db.create_password_user(con, 'riverstone', 'a collector', 'pw')
    _pw(client, 'riverstone', 'pw')
    s = client.get('/blade-book/api/settings/').get_json()
    assert s['email'] is None
    assert s['sign_in'] == 'password'


def test_settings_view_keeps_email_for_email_users(client, mailer):
    signed_in(client, mailer, 'sam@example.com')
    s = client.get('/blade-book/api/settings/').get_json()
    assert s['email'] == 'sam@example.com' and s['sign_in'] == 'email'


def test_wants_intro_skips_the_undeliverable_side(con):
    wanter_id = db.create_password_user(con, 'riverstone', 'a collector', 'pw')
    con.execute('UPDATE users SET share_email_on_intro = 1 WHERE id = ?', (wanter_id,))
    owner = _u(con, email='o@example.com', handle='o-guy', share_email_on_intro=1)
    _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=450)
    db.create_want(con, wanter_id, {'model': 'Sebenza'})
    m = FakeMailer()
    assert match.run(con, m) == 1
    assert [x['to'] for x in m.sent] == ['o@example.com']
    assert m.sent[0]['reply_to'] is None            # never hand out the synthetic address
    assert "hasn't shared a contact address" in m.sent[0]['text']


def test_board_contact_from_a_password_user_mails_only_the_seller(client, mailer, con):
    from tests.test_board_db import _old_knife, _seller
    s = _seller(con, share_email_on_intro=1)
    _old_knife(con, s['id'])
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500)
    db.create_password_user(con, 'riverstone', 'a collector', 'pw')
    _pw(client, 'riverstone', 'pw')
    r = client.post(f'/blade-book/api/board/{k["id"]}/contact', json={'message': 'is it mint?'})
    assert r.status_code == 200, r.data
    assert [x['to'] for x in mailer.sent] == [s['email']]
    assert mailer.sent[0]['reply_to'] is None
    assert con.execute('SELECT sent_at FROM intros').fetchone()[0]


def test_board_contact_to_a_password_user_seller_is_refused_upfront(client, mailer, con):
    from tests.test_board_db import _old_knife
    sid = db.create_password_user(con, 'riverstone', 'a collector', 'pw')
    con.execute("UPDATE users SET verified_at = '2026-01-01T00:00:00+00:00' WHERE id = ?", (sid,))
    con.commit()
    _old_knife(con, sid)
    k = _mk_knife(con, sid, sale_status='for_sale', asking_price=500)
    signed_in(client, mailer, 'buyer@example.com')
    n = len(mailer.sent)
    r = client.post(f'/blade-book/api/board/{k["id"]}/contact', json={'message': 'hi'})
    assert r.status_code == 409
    assert 'riverstone' in r.get_json()['error']
    assert len(mailer.sent) == n
    assert con.execute('SELECT count(*) FROM intros').fetchone()[0] == 0


def test_invite_script_creates_and_resets(con, capsys):
    from scripts import invite
    assert invite.main(['riverstone', '--name', 'a collector', '--password', 'first pw']) == 0
    u = db.get_user_by_handle(con, 'riverstone')
    assert u and auth.verify_password('first pw', u['password_hash'])
    # second run without --reset refuses to touch an existing account
    assert invite.main(['riverstone', '--password', 'second pw']) == 1
    assert auth.verify_password('first pw', db.get_user_by_handle(con, 'riverstone')['password_hash'])
    assert invite.main(['riverstone', '--reset', '--password', 'second pw']) == 0
    assert auth.verify_password('second pw', db.get_user_by_handle(con, 'riverstone')['password_hash'])
    out = capsys.readouterr().out
    assert 'second pw' not in out          # never echo the password back


def test_invite_script_generates_a_password_when_none_given(con, capsys):
    from scripts import invite
    assert invite.main(['riverstone', '--generate']) == 0
    out = capsys.readouterr().out
    pw = [ln.split(': ', 1)[1] for ln in out.splitlines() if ln.startswith('password: ')][0]
    assert len(pw) >= 16
    assert auth.verify_password(pw, db.get_user_by_handle(con, 'riverstone')['password_hash'])


def test_invite_script_rejects_bad_or_taken_handles(con, mailer):
    from scripts import invite
    assert invite.main(['ad', '--password', 'x']) == 1          # too short
    assert invite.main(['admin', '--password', 'x']) == 1       # reserved
    db.create_user(con, 'sam@example.com', 'sam')
    assert invite.main(['sam', '--password', 'x']) == 1         # taken by an email account, not ours to reset
    assert invite.main(['sam', '--reset', '--password', 'x']) == 1
    assert db.get_user_by_handle(con, 'sam')['password_hash'] is None
