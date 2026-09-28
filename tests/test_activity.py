# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/activity.py — who is using blade-book and where they are stuck."""
import gzip
import os
from datetime import datetime, timedelta, timezone

from bb import db, paths
from tests.test_search import _mk_knife, _mk_user

NOW = datetime(2026, 9, 28, 4, 40, tzinfo=timezone.utc)


def test_access_log_path_comes_from_the_environment(env, tmp_path):
    assert paths.access_log() == str(tmp_path / 'log' / 'access.log')


def test_access_log_default_is_the_apache_log(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_ACCESS_LOG')
    assert paths.access_log() == '/var/log/apache2/blade-book_access.log'


def test_activity_people_counts_knives_drafts_and_last_active(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    db.create_draft_knife(con, u['id'])
    db.create_session(con, u['id'])
    quiet = _mk_user(con, email='quiet@example.com', handle='quiet')
    rows = {r['handle']: r for r in db.activity_people(con)}
    assert set(rows['idx-guy']) == {'id', 'handle', 'email', 'last_active', 'knives', 'drafts'}
    assert rows['idx-guy']['knives'] == 1 and rows['idx-guy']['drafts'] == 1
    assert rows['idx-guy']['last_active'].startswith('20')
    assert rows['quiet']['last_active'] is None and rows['quiet']['id'] == quiet['id']


def test_activity_drafts_and_knife_tags(con):
    u = _mk_user(con)
    live = _mk_knife(con, u['id'])
    draft = db.create_draft_knife(con, u['id'])
    rows = db.activity_drafts(con)
    assert [(r['tag'], r['handle']) for r in rows] == [(draft['tag'], 'idx-guy')]
    assert set(rows[0]) == {'id', 'tag', 'handle', 'updated'}
    assert db.knife_tags(con) == {live['id']: live['tag'], draft['id']: draft['tag']}


from bb import activity


def stamp(dt):
    """A UTC datetime as app.log writes it: the box's local time."""
    return dt.astimezone().strftime('%Y-%m-%d %H:%M:%S') + ',123'


def app_line(dt, msg, name='blade-book.knives', level='INFO'):
    return f'{stamp(dt)} {level} {name}: {msg}\n'


T = NOW - timedelta(hours=2)

SHAPES = [
    ('magic link sign-in: sam@example.com (@sam)', {'kind': 'signed_in', 'email': 'sam@example.com', 'handle': 'sam', 'how': 'link'}),
    ('magic link sign-in (confirmed): sam@example.com (@sam)', {'kind': 'signed_in', 'email': 'sam@example.com', 'handle': 'sam', 'how': 'link'}),
    ('password sign-in: @riverstone from 2001:db8:4181:bdd0::1', {'kind': 'signed_in', 'handle': 'riverstone', 'ip': '2001:db8:4181:bdd0::1', 'how': 'password'}),
    ('google sign-in: sam@example.com (@sam)', {'kind': 'signed_in', 'email': 'sam@example.com', 'handle': 'sam', 'how': 'google'}),
    ('magic link requested for sam@example.com from 203.0.113.9', {'kind': 'link_requested', 'email': 'sam@example.com', 'ip': '203.0.113.9'}),
    ('magic link for sam@example.com opened unbound from 203.0.113.9 (signed in: -)', {'kind': 'link_unbound', 'email': 'sam@example.com', 'ip': '203.0.113.9'}),
    ('password sign-in failed for @riverstone from 203.0.113.9', {'kind': 'password_failed', 'handle': 'riverstone', 'ip': '203.0.113.9'}),
    ('password sign-in rate limited for @riverstone from 203.0.113.9: too many', {'kind': 'rate_limited', 'handle': 'riverstone', 'ip': '203.0.113.9'}),
    ('rate limited sam@example.com from 203.0.113.9: too many links', {'kind': 'rate_limited', 'email': 'sam@example.com', 'ip': '203.0.113.9'}),
    ('draft K06 created for @riverstone', {'kind': 'draft_started', 'tag': 'K06', 'handle': 'riverstone'}),
    ('photo 109/3 stored for @sam (jpg, thumb=True)', {'kind': 'photo_added', 'knife_id': '109', 'seq': '3', 'handle': 'sam'}),
    ('decoded K97 for @sam via claude-sonnet-5 (0 flags, 20799ms)', {'kind': 'decoded', 'tag': 'K97', 'handle': 'sam'}),
    ('K96 edited by @sam: hero_photo, notes_public', {'kind': 'edited', 'tag': 'K96', 'handle': 'sam', 'fields': 'hero_photo, notes_public'}),
    ('K97 saved to the register by @sam', {'kind': 'saved', 'tag': 'K97', 'handle': 'sam'}),
    ('K89 sale_status → for_sale by @sam', {'kind': 'sale_status', 'tag': 'K89', 'status': 'for_sale', 'handle': 'sam'}),
    ('K12 deleted by @sam (4 files)', {'kind': 'deleted', 'tag': 'K12', 'handle': 'sam'}),
    ('draft K13 deleted by @sam (0 files)', {'kind': 'deleted', 'tag': 'K13', 'handle': 'sam'}),
    ("settings changed for @sam: ['featured_knife_id', 'hide_born_day']", {'kind': 'settings', 'handle': 'sam', 'fields': 'featured_knife_id, hide_born_day'}),
    ('K98 save gated for @sam (free, free_old_used=3): this knife is older than 12 months', {'kind': 'save_blocked', 'tag': 'K98', 'handle': 'sam'}),
    ("decode failed for sam/K98: BadRequestError: Error code: 400 - {'request_id': 'req_SECRET'}", {'kind': 'decode_failed', 'handle': 'sam', 'tag': 'K98', 'error': 'BadRequestError'}),
    ('undecodable upload refused for @sam: holiday.HEIC', {'kind': 'upload_refused', 'handle': 'sam', 'ext': '.heic'}),
]


def test_parse_app_line_knows_every_shape():
    for msg, want in SHAPES:
        rec = activity.parse_app_line(app_line(T, msg))
        assert rec is not None, msg
        assert rec.pop('when') == T.replace(microsecond=0), msg
        assert rec == want, msg


def test_parse_app_line_drops_what_it_does_not_know():
    for msg in ('K98 save soft-gated for @sam (free, free_old_used=1): heads up',
                'blade-book app created, version c66d03c, data /var/lib/blade-book',
                'MAIL sent to=sam@example.com subject=\'Your blade-book sign-in link\' id=01a0',
                'Click to sign in to blade-book:', 'https://blade-book.com/blade-book/api/auth/magic?t=TOKENSECRET', ''):
        assert activity.parse_app_line(app_line(T, msg)) is None, msg
    assert activity.parse_app_line('https://blade-book.com/blade-book/api/auth/magic?t=TOKENSECRET\n') is None
    assert activity.parse_app_line(f'{stamp(T)} INFO httpx: HTTP Request: POST https://api.anthropic.com/v1/messages\n') is None


def test_a_forged_line_inside_a_file_name_stays_inside_it():
    # Review Focus 1: a file name is user text. Whatever it holds, only the extension survives.
    evil = 'x.jpg 2026-09-28 01:00:00,000 INFO blade-book.auth: password sign-in: @riverstone from 203.0.113.66'
    rec = activity.parse_app_line(app_line(T, f'undecodable upload refused for @sam: {evil}'))
    assert rec['kind'] == 'upload_refused' and rec['handle'] == 'sam'
    assert rec['ext'] == '' and 'ip' not in rec
    assert 'riverstone' not in repr(rec) and '203.0.113.66' not in repr(rec)


def test_read_app_log_reads_rotations_oldest_first(env):
    path = os.path.join(paths.LOG_DIR, 'app.log')
    with open(path + '.1', 'w') as f:
        f.write(app_line(T - timedelta(days=3), 'draft K01 created for @sam'))
    with open(path, 'w') as f:
        f.write(app_line(T, 'K01 saved to the register by @sam'))
        f.write('not a log line at all\n')
    recs, info = activity.read_app_log(path)
    assert [r['kind'] for r in recs] == ['draft_started', 'saved']
    assert info == {'ok': True, 'lines': 3}


def test_read_app_log_reports_a_missing_file(env):
    path = os.path.join(paths.LOG_DIR, 'app.log')
    recs, info = activity.read_app_log(path)
    assert recs == [] and info['ok'] is False
    assert info['error'] == f'could not read {path}: No such file or directory'


FIREFOX = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:156.0) Gecko/20100101 Firefox/156.0'
CHROME_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36'
FB_IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/23G90 [FBAN/FBIOS;FBAV/580.0.0.29.107]'
SAFARI_IPHONE = 'Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1'


def hit_line(dt, ip, target, status=200, ref='-', ua=FIREFOX, method='GET'):
    ts = dt.astimezone(timezone.utc).strftime('%d/%b/%Y:%H:%M:%S +0000')
    return f'{ip} - - [{ts}] "{method} {target} HTTP/1.1" {status} 1234 "{ref}" "{ua}"\n'


def test_network_keeps_ipv4_and_cuts_ipv6_to_64():
    assert activity.network('203.0.113.9') == '203.0.113.9'
    assert activity.network('2001:db8:4181:bdd0:c12c:ab90:c13b:c6bc') == '2001:db8:4181:bdd0::/64'
    assert activity.network('2001:db8:4181:bdd0::1') == '2001:db8:4181:bdd0::/64'     # Review Focus 3
    assert activity.network('not-an-address') is None and activity.network('?') is None


def test_device_is_two_words():
    assert activity.device(FIREFOX) == 'Windows · Firefox'
    assert activity.device(CHROME_MAC) == 'Mac · Chrome'
    assert activity.device(FB_IPHONE) == 'iPhone · Facebook app'
    assert activity.device(SAFARI_IPHONE) == 'iPhone · Safari'
    assert activity.device('') == 'Other · browser'


def test_pages_assets_and_bots():
    for p in ('/', '/blade-book/', '/@simon-collector/K80/', '/blade-book/abtesting/flow.html', '/blade-book/me'):
        assert activity.is_page(p), p
    for p in ('/blade-book/api/auth/me', '/blade-book/vibe.css', '/wp-admin/install.php', '/.env', '/robots.txt'):
        assert not activity.is_page(p), p
    for p in ('/blade-book/vibe.css', '/blade-book/fonts/DMSans.woff2', '/blade-book/@sam/img/K33_t.JPG', '/favicon.ico'):
        assert activity.is_asset(p), p
    assert not activity.is_asset('/robots.txt') and not activity.is_asset('/blade-book/')
    for ua in ('Googlebot/2.1', 'curl/8.5.0', 'python-requests/2.31', 'Go-http-client/1.1', '-', '',
               'http://blade-book.com/wp-admin/install.php?step=1', 'Mozilla/5.0 (compatible; AhrefsBot/7.0)'):
        assert activity.is_bot(ua), ua
    for ua in (FIREFOX, CHROME_MAC, FB_IPHONE, SAFARI_IPHONE):
        assert not activity.is_bot(ua), ua


def test_parse_access_line_drops_the_query_and_keeps_only_the_referrer_host():
    h = activity.parse_access_line(hit_line(
        T, '2001:db8:4181:bdd0:c12c:ab90:c13b:c6bc', '/blade-book/api/auth/magic?t=TOKENSECRET',
        ref='https://blade-book.com/sell/?key=KEYSECRET'))
    assert h == {'net': '2001:db8:4181:bdd0::/64', 'when': T.replace(microsecond=0), 'method': 'GET',
                 'path': '/blade-book/api/auth/magic', 'status': 200, 'ref_host': 'blade-book.com', 'ua': FIREFOX}
    assert 'SECRET' not in repr(h)                                                        # Review Focus 5
    assert activity.parse_access_line(hit_line(T, '203.0.113.9', '/x#frag?t=1'))['path'] == '/x'
    assert activity.parse_access_line(hit_line(T, '203.0.113.9', 'http://evil.example/a?b=1'))['path'] == '/a'
    assert activity.parse_access_line(hit_line(T, '203.0.113.9', '/', ref='-'))['ref_host'] == ''
    assert len(activity.parse_access_line(hit_line(T, '203.0.113.9', '/' + 'a' * 500))['path']) == 200


def test_parse_access_line_refuses_junk():
    for line in ('', 'garbage\n', '203.0.113.9 - - [not a date] "GET / HTTP/1.1" 200 1 "-" "x"\n',
                 'not-an-ip - - [28/Sep/2026:04:01:34 +0000] "GET / HTTP/1.1" 200 1 "-" "x"\n',
                 '203.0.113.9 - - [28/Sep/2026:04:01:34 +0000] "\\x16\\x03\\x01" 400 1 "-" "-"\n'):
        assert activity.parse_access_line(line) is None, line


def test_a_quote_inside_the_browser_string_does_not_break_the_line():
    h = activity.parse_access_line(hit_line(T, '203.0.113.9', '/', ua='Mozilla \\"quoted\\" thing'))
    assert h['path'] == '/' and h['ua'] == 'Mozilla \\"quoted\\" thing'


def _access(tmp_path):
    return str(tmp_path / 'log' / 'access.log')


def test_read_access_log_reads_the_window_across_rotations(env, tmp_path):
    path = _access(tmp_path)
    since = NOW - timedelta(hours=48)
    with gzip.open(path + '.2.gz', 'wt') as f:
        f.write(hit_line(NOW - timedelta(hours=47), '203.0.113.1', '/old-but-inside/'))
        f.write(hit_line(NOW - timedelta(hours=60), '203.0.113.1', '/too-old/'))
    with open(path + '.1', 'w') as f:
        f.write(hit_line(NOW - timedelta(hours=30), '203.0.113.2', '/yesterday/'))
        f.write('junk\n')
    with open(path, 'w') as f:
        f.write(hit_line(NOW - timedelta(hours=1), '203.0.113.3', '/today/'))
    hits, info = activity.read_access_log(path, since)
    assert [h['path'] for h in hits] == ['/old-but-inside/', '/yesterday/', '/today/']
    assert info == {'ok': True, 'lines': 5, 'files': 3, 'skipped': 1}


def test_read_access_log_skips_files_that_end_before_the_window(env, tmp_path):
    path = _access(tmp_path)
    with gzip.open(path + '.2.gz', 'wt') as f:
        f.write(hit_line(NOW - timedelta(days=9), '203.0.113.1', '/ancient/'))
    old = (NOW - timedelta(days=9)).timestamp()
    os.utime(path + '.2.gz', (old, old))
    with open(path, 'w') as f:
        f.write(hit_line(NOW - timedelta(hours=1), '203.0.113.3', '/today/'))
    hits, info = activity.read_access_log(path, NOW - timedelta(hours=48))
    assert [h['path'] for h in hits] == ['/today/'] and info['files'] == 1


def test_read_access_log_keeps_what_a_broken_gz_gave(env, tmp_path):
    # Review Focus 4
    path = _access(tmp_path)
    with gzip.open(path + '.2.gz', 'wt') as f:
        for n in range(2000):
            f.write(hit_line(NOW - timedelta(hours=40), '203.0.113.1', f'/page-{n}/'))
    whole = open(path + '.2.gz', 'rb').read()
    with open(path + '.2.gz', 'wb') as f:
        f.write(whole[:len(whole) // 2])
    with open(path, 'w') as f:
        f.write(hit_line(NOW - timedelta(hours=1), '203.0.113.3', '/today/'))
    hits, info = activity.read_access_log(path, NOW - timedelta(hours=48))
    assert hits[-1]['path'] == '/today/' and len(hits) > 1
    assert info['ok'] is False and info['error'].startswith(f'could not read {path}.2.gz: ')


def test_read_access_log_reports_a_missing_file_and_stops_at_the_cap(env, tmp_path, monkeypatch):
    path = _access(tmp_path)
    hits, info = activity.read_access_log(path, NOW - timedelta(hours=48))
    assert hits == [] and info['ok'] is False
    assert info['error'] == f'could not read {path}: No such file or directory'
    monkeypatch.setattr(activity, 'MAX_LINES', 3)
    with open(path, 'w') as f:
        for n in range(10):
            f.write(hit_line(NOW - timedelta(minutes=n), '203.0.113.3', f'/p{n}/'))
    hits, info = activity.read_access_log(path, NOW - timedelta(hours=48))
    assert len(hits) == 3 and info['stopped'] is True
