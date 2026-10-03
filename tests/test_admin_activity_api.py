# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""GET /api/admin/activity — who is using blade-book and where they are stuck."""
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from bb import activity, paths
from tests.conftest import signed_in
from tests.test_activity import FIREFOX, app_line, hit_line, stamp

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


STAMPED = re.compile(r'^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3} ')


def test_a_line_break_in_user_text_cannot_start_a_log_record(client, mailer, con):
    # final review C1, through the real route: a want's text is user text and lands in app.log
    signed_in(client, mailer, email='sam@example.com')
    now = datetime.now(timezone.utc)
    forged = f'{stamp(now)} INFO blade-book.auth: password sign-in: @riverstone from 203.0.113.66'
    for sep in ('\n', '\r', '\r\n', '\u2028', '\x0b'):
        r = client.post('/blade-book/api/wants', json={'model': 'Sebenza' + sep + forged, 'mode': 'sale'})
        assert r.status_code == 200, r.data
    path = os.path.join(paths.LOG_DIR, 'app.log')
    lines = open(path, encoding='utf-8').read().splitlines()
    assert any('password sign-in: @riverstone' in ln for ln in lines)              # it was logged...
    assert not [ln for ln in lines if STAMPED.match(ln) and 'want created' not in ln and 'riverstone' in ln]   # ...inside its record
    recs, info = activity.read_app_log(path)
    assert info['ok'] and not [r for r in recs if r.get('handle') == 'riverstone']
    assert activity.guesses(recs) == {'127.0.0.1': {'sam'}}       # sam's own real sign-in, and nothing forged


def test_a_traceback_cannot_start_a_log_record_either(app):
    forged = f'{stamp(datetime.now(timezone.utc))} INFO blade-book.auth: password sign-in: @riverstone from 203.0.113.66'
    try:
        raise ValueError('boom\n' + forged)
    except ValueError:
        logging.getLogger('blade-book.test').exception('it broke')
    lines = open(os.path.join(paths.LOG_DIR, 'app.log'), encoding='utf-8').read().split('\n')
    mine = lines[next(i for i, ln in enumerate(lines) if 'it broke' in ln):]
    assert STAMPED.match(mine[0]) and 'Traceback' in mine[1]
    assert [ln for ln in mine[1:] if ln and not ln.startswith('\t')] == []
