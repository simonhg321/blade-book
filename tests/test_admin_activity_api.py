# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""GET /api/admin/activity — who is using blade-book and where they are stuck."""
import os
from datetime import datetime, timedelta, timezone

from bb import paths
from tests.conftest import signed_in
from tests.test_activity import FIREFOX, app_line, hit_line

URL = '/blade-book/api/admin/activity'


def _admin(client, mailer, con):
    me = signed_in(client, mailer, email='admin@example.com')
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],)); con.commit()
    return me


def test_the_route_is_gated(client, mailer, con):
    assert client.get(URL).status_code == 401
    signed_in(client, mailer, email='pleb@example.com')
    assert client.get(URL).status_code == 403


def test_hours_must_be_a_known_window(client, mailer, con):
    _admin(client, mailer, con)
    for bad in ('1', '49', '0', '-48', 'abc', '', '48.0', '100000'):
        r = client.get(URL, query_string={'hours': bad})
        assert r.status_code == 400 and r.get_json() == {'error': 'hours must be one of 48, 168, 336'}, bad
    for good in ('48', '168', '336'):
        r = client.get(URL, query_string={'hours': good})
        assert r.status_code == 200 and r.get_json()['hours'] == int(good)
    assert client.get(URL).get_json()['hours'] == 48


def test_the_summary_names_you_and_leaks_nothing(client, mailer, con, tmp_path):
    me = _admin(client, mailer, con)
    now = datetime.now(timezone.utc)
    with open(os.path.join(paths.LOG_DIR, 'app.log'), 'a') as f:
        f.write(app_line(now - timedelta(hours=3), 'magic link requested for pat@example.com from 203.0.113.50', name='blade-book.auth'))
        f.write(app_line(now - timedelta(hours=3), 'https://localhost/blade-book/api/auth/magic?t=TOKENSECRET', name='blade-book.mail'))
    with open(paths.access_log(), 'w') as f:
        f.write(hit_line(now - timedelta(hours=1), '203.0.113.50', '/blade-book/api/auth/magic?t=TOKENSECRET', ua=FIREFOX))
        f.write(hit_line(now - timedelta(hours=1), '203.0.113.50', '/sell/?key=KEYSECRET', ref='https://localhost/x?t=REFSECRET', ua=FIREFOX))
        f.write(hit_line(now - timedelta(hours=1), '203.0.113.50', '/blade-book/vibe.css', ua=FIREFOX))
    r = client.get(URL)
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'
    j = r.get_json()
    assert j['you'] == me['handle']
    assert ('link_unclicked', 'pat@example.com') in [(n['kind'], n['who']) for n in j['needs_help']]
    assert [v['pages'][0]['path'] for v in j['visitors']] == ['/sell/']
    body = r.data.decode()
    for needle in ('TOKENSECRET', 'KEYSECRET', 'REFSECRET', 'session_secret', 'token_hash', 'sid_hash', 'password_hash'):
        assert needle not in body, needle


def test_our_own_host_is_not_where_a_visitor_came_from(client, mailer, con):
    _admin(client, mailer, con)
    now = datetime.now(timezone.utc)
    with open(paths.access_log(), 'w') as f:
        f.write(hit_line(now - timedelta(hours=1), '203.0.113.50', '/blade-book/', ref='http://localhost/blade-book/me/', ua=FIREFOX))
        f.write(hit_line(now - timedelta(hours=1), '203.0.113.50', '/blade-book/api/auth/me', status=401, ref='http://localhost/blade-book/', ua=FIREFOX))
    v = client.get(URL).get_json()['visitors']
    assert len(v) == 1 and v[0]['came_from'] == ''
