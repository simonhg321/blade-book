# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from bb import auth, db


def _req(client, email, ip='10.0.0.1'):
    return client.post('/blade-book/api/auth/magic', json={'email': email},
                       headers={'X-Forwarded-For': ip})


def test_sixth_link_for_same_email_in_an_hour_is_429(client, mailer):
    for _ in range(auth.EMAIL_LINKS_PER_HOUR):
        assert _req(client, 'sam@example.com').status_code == 202
    r = _req(client, 'sam@example.com')
    assert r.status_code == 429
    assert 'try again' in r.get_json()['error']
    assert len(mailer.sent) == auth.EMAIL_LINKS_PER_HOUR
    # a different address from the same IP is still fine
    assert _req(client, 'other@example.com').status_code == 202


def test_ip_limit_applies_across_emails(client, mailer):
    for i in range(auth.IP_ATTEMPTS_PER_HOUR):
        assert _req(client, f'u{i}@example.com', ip='10.9.9.9').status_code == 202
    assert _req(client, 'fresh@example.com', ip='10.9.9.9').status_code == 429
    assert _req(client, 'fresh@example.com', ip='10.9.9.10').status_code == 202


def test_last_forwarded_hop_is_the_client(client):
    _req(client, 'sam@example.com', ip='1.2.3.4, 203.0.113.5')
    con = db.connect()
    assert con.execute('SELECT ip FROM auth_attempts').fetchone()[0] == '203.0.113.5'


def test_spoofed_first_hop_does_not_bypass_ip_limit(client):
    for i in range(auth.IP_ATTEMPTS_PER_HOUR):
        assert _req(client, f'u{i}@example.com', ip=f'{i}.{i}.{i}.{i}, 10.9.9.9').status_code == 202
    r = _req(client, 'fresh@example.com', ip='30.30.30.30, 10.9.9.9')
    assert r.status_code == 429


def test_check_rate_limits_purges_periodically(env):
    con = db.connect()
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    con.execute("INSERT INTO auth_attempts (ip, ts) VALUES ('1.1.1.1', ?)", (old,))
    con.commit()
    from app import create_app
    app = create_app()
    with app.test_request_context('/', headers={'X-Forwarded-For': '2.2.2.2'}):
        for _ in range(auth.PURGE_EVERY):
            auth.check_rate_limits(con)
    assert con.execute("SELECT count(*) FROM auth_attempts WHERE ip = '1.1.1.1'").fetchone()[0] == 0
