# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
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
    ('magic link sign-in (confirmed): sam@example.com (@sam)', {'kind': 'signed_in', 'email': 'sam@example.com', 'handle': 'sam', 'how': 'confirmed'}),
    ('handle changed: @old-name -> @new-name (user 7)', {'kind': 'handle_changed', 'old': 'old-name', 'handle': 'new-name'}),
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


def test_read_app_log_reads_the_day_files_oldest_first(env):
    # scripts/rotate_log.py keeps one file per day beside app.log
    path = os.path.join(paths.LOG_DIR, 'app.log')
    with open(path + '.2026-09-24', 'w') as f:
        f.write(app_line(T - timedelta(days=4), 'draft K01 created for @sam'))
    with open(path + '.2026-09-25', 'w') as f:
        f.write(app_line(T - timedelta(days=3), 'decoded K01 for @sam via FakeDecoder'))
    with open(path + '.rotating', 'w') as f:           # a run in flight, or one that died
        f.write(app_line(T - timedelta(days=1), 'decoded K01 for @sam via FakeDecoder'))
    with open(path + '.2026-09-25.gz', 'w') as f:      # not ours
        f.write(app_line(T, 'K09 saved to the register by @sam'))
    with open(path, 'w') as f:
        f.write(app_line(T, 'K01 saved to the register by @sam'))
        f.write('not a log line at all\n')
    recs, info = activity.read_app_log(path)
    assert [(r['kind'], r['tag']) for r in recs] == [('draft_started', 'K01'), ('decoded', 'K01'), ('decoded', 'K01'), ('saved', 'K01')]
    assert info == {'ok': True, 'lines': 5}


def test_read_app_log_keeps_the_newest_lines_when_cut_short(env, monkeypatch):
    path = os.path.join(paths.LOG_DIR, 'app.log')
    for day in ('2026-09-24', '2026-09-25'):
        with open(f'{path}.{day}', 'w') as f:
            f.write(app_line(T - timedelta(days=3), f'draft K{day[-2:]} created for @sam'))
    with open(path, 'w') as f:
        f.write(app_line(T, 'K01 saved to the register by @sam'))
    monkeypatch.setattr(activity, 'MAX_LINES', 2)
    recs, info = activity.read_app_log(path)
    assert [r['tag'] for r in recs] == ['K25', 'K01'] and info['stopped'] is True


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


def test_the_cap_keeps_the_newest_lines(env, tmp_path, monkeypatch):
    # final review I5: the cap used to keep the top of the file, which is the oldest part
    path = _access(tmp_path)
    monkeypatch.setattr(activity, 'MAX_LINES', 4)
    with open(path + '.1', 'w') as f:
        for n in range(5):
            f.write(hit_line(NOW - timedelta(hours=30) + timedelta(minutes=n), '203.0.113.3', f'/old{n}/'))
    with open(path, 'w') as f:
        for n in range(2):
            f.write(hit_line(NOW - timedelta(hours=1) + timedelta(minutes=n), '203.0.113.3', f'/new{n}/'))
    hits, info = activity.read_access_log(path, NOW - timedelta(hours=48))
    assert [h['path'] for h in hits] == ['/old3/', '/old4/', '/new0/', '/new1/']
    assert info == {'ok': True, 'lines': 4, 'files': 2, 'stopped': True}
    app = os.path.join(paths.LOG_DIR, 'app.log')
    with open(app, 'w') as f:
        for n in range(9):
            f.write(app_line(h(9 - n), f'K0{n} saved to the register by @sam'))
    recs, info = activity.read_app_log(app)
    assert [r['tag'] for r in recs] == ['K05', 'K06', 'K07', 'K08'] and info == {'ok': True, 'lines': 4, 'stopped': True}


SINCE = NOW - timedelta(hours=48)


def recs_of(*pairs):
    """(datetime, message) pairs as parsed records, oldest first."""
    out = [activity.parse_app_line(app_line(dt, msg)) for dt, msg in pairs]
    assert all(out), [msg for (_dt, msg), r in zip(pairs, out) if r is None]
    return sorted(out, key=lambda r: r['when'])


def help_of(*pairs, drafts=()):
    return activity.needs_help(recs_of(*pairs), list(drafts), SINCE, NOW)


def h(n):
    return NOW - timedelta(hours=n)


def test_a_link_nobody_opened():
    got = help_of((h(30), 'magic link requested for pat@example.com from 203.0.113.9'),
                  (h(29), 'magic link requested for Pat@Example.com from 203.0.113.9'))
    assert got == [{'kind': 'link_unclicked', 'who': 'pat@example.com', 'when': h(29).isoformat(),
                    'detail': '2 sign-in links sent, never opened'}]


def test_a_link_opened_elsewhere_and_never_confirmed():
    got = help_of((h(30), 'magic link requested for pat@example.com from 203.0.113.9'),
                  (h(30) + timedelta(minutes=2), 'magic link for pat@example.com opened unbound from 198.51.100.4 (signed in: -)'))
    assert got[0]['detail'] == '1 sign-in link sent, opened in another browser, never confirmed'


def test_a_later_sign_in_clears_the_link():
    assert help_of((h(30), 'magic link requested for sam@example.com from 203.0.113.9'),
                   (h(29), 'magic link requested for sam@example.com from 203.0.113.9'),
                   (h(29) + timedelta(minutes=1), 'magic link sign-in: sam@example.com (@sam)')) == []
    # asked again after signing in, and that one went nowhere
    got = help_of((h(30), 'magic link requested for sam@example.com from 203.0.113.9'),
                  (h(30) + timedelta(minutes=1), 'magic link sign-in (confirmed): sam@example.com (@sam)'),
                  (h(3), 'magic link requested for sam@example.com from 203.0.113.9'))
    assert [(g['when'], g['detail']) for g in got] == [(h(3).isoformat(), '1 sign-in link sent, never opened')]


def test_a_link_still_alive_or_outside_the_window_is_not_stuck():
    assert help_of((NOW - timedelta(minutes=10), 'magic link requested for sam@example.com from 203.0.113.9'),
                   (h(60), 'magic link requested for old@example.com from 203.0.113.9')) == []


def test_failed_passwords_until_a_good_sign_in():
    got = help_of((h(5), 'password sign-in failed for @riverstone from 203.0.113.9'),
                  (h(4), 'password sign-in failed for @riverstone from 203.0.113.9'))
    assert got == [{'kind': 'password_failed', 'who': '@riverstone', 'when': h(4).isoformat(),
                    'detail': '2 failed password sign-ins'}]
    assert help_of((h(5), 'password sign-in failed for @riverstone from 203.0.113.9'),
                   (h(4), 'password sign-in: @riverstone from 203.0.113.9')) == []
    got = help_of((h(5), 'password sign-in: @riverstone from 203.0.113.9'),
                  (h(4), 'password sign-in failed for @riverstone from 203.0.113.9'))
    assert [g['detail'] for g in got] == ['1 failed password sign-in']


def test_rate_limits():
    got = help_of((h(5), 'rate limited sam@example.com from 203.0.113.9: too many links'),
                  (h(4), 'password sign-in rate limited for @riverstone from 203.0.113.9: too many'),
                  (h(3), 'password sign-in rate limited for @riverstone from 203.0.113.9: too many'))
    assert [(g['kind'], g['who'], g['detail']) for g in got] == [
        ('rate_limited', '@riverstone', 'rate limited 2 times'), ('rate_limited', 'sam@example.com', 'rate limited 1 time')]


def test_a_blocked_save_until_the_knife_saves():
    blocked = (h(5), 'K98 save gated for @sam (free, free_old_used=3): older than 12 months')
    got = help_of(blocked, blocked)
    assert got == [{'kind': 'save_blocked', 'who': '@sam', 'when': h(5).isoformat(), 'tag': 'K98',
                    'detail': 'save of K98 blocked by the gate, 2 times'}]
    assert help_of(blocked, (h(4), 'K98 saved to the register by @sam')) == []
    assert len(help_of(blocked, (h(4), 'K97 saved to the register by @sam'))) == 1
    assert len(help_of(blocked, (h(4), 'K98 saved to the register by @other'))) == 1


def test_a_failed_decode_until_the_knife_decodes():
    failed = (h(5), "decode failed for sam/K98: BadRequestError: Error code: 400 - {'request_id': 'req_SECRET'}")
    got = help_of(failed)
    assert got == [{'kind': 'decode_failed', 'who': '@sam', 'when': h(5).isoformat(), 'tag': 'K98',
                    'detail': 'decode of K98 failed: BadRequestError'}]
    assert help_of(failed, (h(4), 'decoded K98 for @sam via claude-sonnet-5 (0 flags, 20799ms)')) == []


def test_a_refused_upload():
    got = help_of((h(5), 'undecodable upload refused for @sam: a.heic'), (h(4), 'undecodable upload refused for @sam: b.tiff'))
    assert got == [{'kind': 'upload_refused', 'who': '@sam', 'when': h(4).isoformat(),
                    'detail': '2 photos refused, last one .tiff'}]
    assert help_of((h(5), 'undecodable upload refused for @sam: noext'))[0]['detail'] == '1 photo refused'


def test_an_unfinished_draft_ignores_the_window_and_names_its_purge():
    old = NOW - timedelta(days=5)
    drafts = [{'id': 101, 'tag': 'K06', 'handle': 'riverstone', 'updated': old.isoformat()},
              {'id': 102, 'tag': 'K07', 'handle': 'sam', 'updated': (NOW - timedelta(minutes=20)).isoformat()}]
    assert help_of(drafts=drafts) == [{'kind': 'draft_unfinished', 'who': '@riverstone', 'when': old.isoformat(), 'tag': 'K06',
                                       'detail': 'draft K06 started, never saved',
                                       'purge_at': (old + timedelta(days=7)).isoformat()}]


def test_signals_come_newest_first_and_a_nameless_handle_still_counts():
    # Review Focus 2: @gone has no account any more; the signal still shows
    got = help_of((h(9), 'password sign-in failed for @gone from 203.0.113.9'),
                  (h(2), 'undecodable upload refused for @sam: a.heic'),
                  (h(6), 'K98 save gated for @sam (free, free_old_used=3): older'))
    assert [g['kind'] for g in got] == ['upload_refused', 'save_blocked', 'password_failed']


LW6 = '2001:db8:4181:bdd0:c12c:ab90:c13b:c6bc'
LWNET = '2001:db8:4181:bdd0::/64'
OURS = {'blade-book.com'}


def test_guesses_need_a_real_sign_in():
    recs = recs_of(
        (h(200), f'password sign-in: @riverstone from {LW6}'),
        (h(100), 'magic link requested for sam@example.com from 203.0.113.9'),
        (h(100) + timedelta(minutes=5), 'magic link sign-in: sam@example.com (@sam)'),
        (h(90), 'password sign-in failed for @riverstone from 198.51.100.1'),          # proves nothing
        (h(80), 'magic link requested for sam@example.com from 198.51.100.2'),        # never used
        (h(70), 'magic link requested for sam@example.com from 198.51.100.3'),
        (h(69), 'magic link sign-in: sam@example.com (@sam)'),                          # an hour later: too late
        (h(60), 'password sign-in: @sam from 2001:db8:4181:bdd0::77'))                  # a second person, same home
    assert activity.guesses(recs) == {LWNET: {'riverstone', 'sam'}, '203.0.113.9': {'sam'}}


def test_a_stranger_who_asks_for_your_link_is_not_you():
    # final review I2: the sign-in line carries no address, so two askers make it a coin toss. No guess.
    for stranger_at, pat_at in ((h(10), h(10) + timedelta(minutes=1)), (h(10) + timedelta(minutes=1), h(10))):
        recs = recs_of((stranger_at, 'magic link requested for pat@example.com from 198.51.100.66'),
                       (pat_at, 'magic link requested for pat@example.com from 203.0.113.9'),
                       (h(10) + timedelta(minutes=3), 'magic link sign-in: pat@example.com (@pat)'))
        assert activity.guesses(recs) == {}
    # the same network asking twice is one asker
    recs = recs_of((h(10), 'magic link requested for pat@example.com from 203.0.113.9'),
                   (h(10) + timedelta(minutes=1), 'magic link requested for pat@example.com from 203.0.113.9'),
                   (h(10) + timedelta(minutes=3), 'magic link sign-in: pat@example.com (@pat)'))
    assert activity.guesses(recs) == {'203.0.113.9': {'pat'}}


def test_a_confirmed_sign_in_belongs_to_whoever_opened_the_link():
    recs = recs_of((h(10), 'magic link requested for pat@example.com from 198.51.100.66'),
                   (h(10) + timedelta(minutes=2), 'magic link for pat@example.com opened unbound from 203.0.113.9 (signed in: -)'),
                   (h(10) + timedelta(minutes=3), 'magic link sign-in (confirmed): pat@example.com (@pat)'))
    assert activity.guesses(recs) == {'203.0.113.9': {'pat'}}
    recs = recs_of((h(10), 'magic link for pat@example.com opened unbound from 203.0.113.9 (signed in: -)'),
                   (h(9), 'magic link sign-in (confirmed): pat@example.com (@pat)'))      # an hour later: too late
    assert activity.guesses(recs) == {}


def test_a_renamed_handle_keeps_its_history():
    # final review I3
    recs = activity.renamed(recs_of(
        (h(9), 'password sign-in failed for @old-name from 203.0.113.9'),
        (h(8), 'K01 saved to the register by @old-name'),
        (h(7), 'handle changed: @old-name -> @mid-name (user 7)'),
        (h(6), 'handle changed: @mid-name -> @new-name (user 7)'),
        (h(5), 'password sign-in: @new-name from 203.0.113.9'),
        (h(4), 'K01 saved to the register by @old-name')))          # somebody else took the old name later
    assert [r['handle'] for r in recs] == ['new-name', 'new-name', 'new-name', 'new-name', 'new-name', 'old-name']
    assert activity.needs_help(recs, [], SINCE, NOW) == []
    rows = [{'id': 7, 'handle': 'new-name', 'email': 'n@example.com', 'last_active': None, 'knives': 1, 'drafts': 0}]
    assert [t['what'] for t in activity.people(rows, recs, {}, SINCE)[0]['trail']] == [
        'signed in', 'changed handle', 'changed handle', 'saved K01', 'password failed']


def test_a_guess_never_names_an_account_that_is_gone(con, env, tmp_path):
    _mk_user(con, email='here@example.com', handle='here')
    _write_logs(tmp_path,
                [app_line(h(300), 'password sign-in: @gone from 203.0.113.9'),
                 app_line(h(200), 'password sign-in: @here from 203.0.113.9')],
                [hit_line(h(1), '203.0.113.9', '/blade-book/'), hit_line(h(1), '203.0.113.9', '/blade-book/vibe.css')])
    s = activity.summary(con, 48, own_hosts=OURS, now=NOW)
    assert [v['guess'] for v in s['visitors']] == [['here']]


def test_people_carry_a_trail_newest_first():
    rows = [{'id': 1, 'handle': 'sam', 'email': 'sam@example.com', 'last_active': h(1).isoformat(), 'knives': 2, 'drafts': 0},
            {'id': 2, 'handle': 'quiet', 'email': 'quiet@example.com', 'last_active': None, 'knives': 0, 'drafts': 0},
            {'id': 3, 'handle': 'riverstone', 'email': 'lw@example.com', 'last_active': h(100).isoformat(), 'knives': 3, 'drafts': 1}]
    recs = recs_of(
        (h(60), 'K01 saved to the register by @sam'),                                   # outside the window
        (h(9), 'magic link requested for sam@example.com from 203.0.113.9'),
        (h(9) + timedelta(minutes=1), 'magic link sign-in: sam@example.com (@sam)'),
        (h(8), 'draft K98 created for @sam'),
        (h(7), 'photo 109/3 stored for @sam (jpg, thumb=True)'),
        (h(7) + timedelta(minutes=1), 'photo 555/1 stored for @sam (jpg, thumb=True)'),
        (h(6), 'K98 save gated for @sam (free, free_old_used=3): older'),
        (h(5), "settings changed for @sam: ['featured_knife_id']"),
        (h(4), 'K89 sale_status → for_sale by @sam'),
        (h(3), 'K96 edited by @sam: hero_photo'),
        (h(2), 'password sign-in failed for @gone from 203.0.113.9'))
    got = activity.people(rows, recs, {109: 'K98'}, SINCE)
    assert [p['handle'] for p in got] == ['sam', 'riverstone', 'quiet']
    sam = got[0]
    assert set(sam) == {'handle', 'email', 'last_active', 'knives', 'drafts', 'trail'}
    assert [t['what'] for t in sam['trail']] == [
        'edited K96: hero_photo', 'K89 → for_sale', 'changed settings: featured_knife_id', 'save of K98 blocked',
        'added photo 1 to a knife', 'added photo 3 to K98', 'started draft K98', 'signed in', 'asked for a sign-in link']
    assert sam['trail'][0]['when'] == h(3).isoformat() and set(sam['trail'][0]) == {'when', 'what'}
    assert got[1]['trail'] == [] and got[2]['last_active'] is None


def test_the_trail_stops_at_fifty(monkeypatch):
    rows = [{'id': 1, 'handle': 'sam', 'email': 'sam@example.com', 'last_active': None, 'knives': 0, 'drafts': 0}]
    recs = recs_of(*[(h(40) + timedelta(minutes=n), 'K01 edited by @sam: notes_public') for n in range(60)])
    assert len(activity.people(rows, recs, {}, SINCE)[0]['trail']) == 50


def _visit(ip, ua, pages, at, asset=True, api=False, ref='-'):
    """A browser's visit as hits: the pages, and the stylesheet or API call a real browser makes."""
    lines = [hit_line(at + timedelta(seconds=n), ip, p, ref=ref if n == 0 else '-', ua=ua) for n, p in enumerate(pages)]
    if asset:
        lines.append(hit_line(at + timedelta(seconds=1), ip, '/blade-book/vibe.css', ua=ua))
    if api:
        lines.append(hit_line(at + timedelta(seconds=1), ip, '/blade-book/api/auth/me', status=401,
                              ref='https://blade-book.com/', ua=ua))
    return [activity.parse_access_line(x) for x in lines]


def test_visitors_keep_people_and_hide_bots():
    hits = (_visit(LW6, FIREFOX, ['/blade-book/abtesting/flow.html'], h(1))
            + _visit('2001:db8:4181:bdd0::99', FIREFOX, ['/blade-book/'], h(1) + timedelta(minutes=5))   # same home
            + _visit('47.150.153.164', FB_IPHONE, ['/'], h(3), asset=False, api=True, ref='https://m.facebook.com/x?y=SECRET')
            + _visit('71.197.159.99', CHROME_MAC, ['/blade-book/admin/', '/blade-book/me/'], h(2))
            + _visit('193.32.162.233', SAFARI_IPHONE, ['/'], h(4), asset=False)                          # no asset, no API: a scanner
            + _visit('106.75.66.25', 'Go-http-client/1.1', ['/'], h(5))
            + [activity.parse_access_line(hit_line(h(6), '198.51.100.7', '/wp-admin/install.php', status=404, ua=CHROME_MAC)),
               activity.parse_access_line(hit_line(h(6), '198.51.100.7', '/blade-book/vibe.css', ua=CHROME_MAC))])   # no page at all
    hits.sort(key=lambda x: x['when'])
    guess = {LWNET: {'riverstone'}, '71.197.159.99': {'simon-collector'}}
    got, hidden = activity.visitors(hits, guess, 'simon-collector', OURS)
    assert [(v['network'], v['guess'], v['you'], v['device'], v['came_from']) for v in got] == [
        (LWNET, ['riverstone'], False, 'Windows · Firefox', ''),
        ('71.197.159.99', ['simon-collector'], True, 'Mac · Chrome', ''),
        ('47.150.153.164', [], False, 'iPhone · Facebook app', 'm.facebook.com')]
    lw = got[0]
    assert set(lw) == {'network', 'guess', 'you', 'device', 'came_from', 'first', 'last', 'requests', 'pages'}
    assert [p['path'] for p in lw['pages']] == ['/blade-book/', '/blade-book/abtesting/flow.html']   # newest first, one visitor
    assert lw['requests'] == 4 and lw['first'] == h(1).isoformat()
    assert set(lw['pages'][0]) == {'when', 'path', 'status'}
    assert hidden == {'bots': 3, 'requests': 5}
    assert 'SECRET' not in repr(got)


def test_visitor_pages_stop_at_thirty():
    hits = _visit('203.0.113.9', FIREFOX, [f'/p{n}/' for n in range(40)], h(1))
    hits.sort(key=lambda x: x['when'])
    got, _ = activity.visitors(hits, {}, None, OURS)
    assert len(got[0]['pages']) == 30 and got[0]['pages'][0]['path'] == '/p39/'


def _write_logs(tmp_path, app_lines, access_lines):
    app = os.path.join(paths.LOG_DIR, 'app.log')
    with open(app, 'w') as f:
        f.writelines(app_lines)
    with open(_access(tmp_path), 'w') as f:
        f.writelines(access_lines)


def test_summary_joins_the_three_sources(con, env, tmp_path):
    lw = _mk_user(con, email='lw@example.com', handle='riverstone')
    db.create_draft_knife(con, lw['id'])
    con.execute('UPDATE knives SET updated = ? WHERE owner_id = ?', ((NOW - timedelta(days=4)).isoformat(), lw['id']))
    con.commit()
    _mk_user(con, email='simon@example.com', handle='simon-collector')
    _write_logs(tmp_path,
                [app_line(h(200), f'password sign-in: @riverstone from {LW6}'),
                 app_line(h(30), 'magic link requested for pat@example.com from 203.0.113.50'),
                 app_line(h(2), 'magic link requested for simon@example.com from 71.197.159.99'),
                 app_line(h(2) + timedelta(minutes=1), 'magic link sign-in: simon@example.com (@simon-collector)'),
                 f'{stamp(h(2))} INFO blade-book.mail: https://blade-book.com/blade-book/api/auth/magic?t=TOKENSECRET\n'],
                [hit_line(h(1), LW6, '/blade-book/abtesting/flow.html'),
                 hit_line(h(1), LW6, '/blade-book/vibe.css'),
                 hit_line(h(2), '71.197.159.99', '/blade-book/api/auth/magic?t=TOKENSECRET'),
                 hit_line(h(2), '71.197.159.99', '/blade-book/admin/'),
                 hit_line(h(2), '71.197.159.99', '/blade-book/vibe.css')])
    s = activity.summary(con, 48, you='simon-collector', own_hosts=OURS, now=NOW)
    assert set(s) == {'hours', 'generated', 'you', 'needs_help', 'people', 'visitors', 'hidden', 'sources'}
    assert s['hours'] == 48 and s['generated'] == NOW.isoformat() and s['you'] == 'simon-collector'
    assert [(n['kind'], n['who']) for n in s['needs_help']] == [
        ('link_unclicked', 'pat@example.com'), ('draft_unfinished', '@riverstone')]
    assert [p['handle'] for p in s['people']] == ['simon-collector', 'riverstone']
    assert [t['what'] for t in s['people'][0]['trail']] == ['signed in', 'asked for a sign-in link']
    assert [(v['guess'], v['you']) for v in s['visitors']] == [(['riverstone'], False), (['simon-collector'], True)]
    assert s['sources'] == {'database': {'ok': True}, 'app_log': {'ok': True, 'lines': 5},
                            'access_log': {'ok': True, 'lines': 5, 'files': 1}}
    assert 'TOKENSECRET' not in repr(s) and '?' not in repr(s['visitors'])


def test_summary_still_answers_when_a_log_is_missing(con, env, tmp_path):
    lw = _mk_user(con, email='lw@example.com', handle='riverstone')
    db.create_draft_knife(con, lw['id'])
    con.execute('UPDATE knives SET updated = ?', ((NOW - timedelta(days=4)).isoformat(),)); con.commit()
    s = activity.summary(con, 48, now=NOW)
    assert s['visitors'] == [] and s['hidden'] == {'bots': 0, 'requests': 0} and s['you'] is None
    assert [n['kind'] for n in s['needs_help']] == ['draft_unfinished']
    assert s['sources']['access_log']['ok'] is False and 'No such file' in s['sources']['access_log']['error']
    assert s['sources']['app_log']['ok'] is False and s['sources']['database'] == {'ok': True}


def test_a_scanner_that_fetches_a_stylesheet_is_still_a_scanner():
    # seen on the box 2026-09-27: a "Linux · Chrome" that asked for /.git/config beside real pages
    hits = (_visit('45.138.12.16', CHROME_MAC, ['/', '/.git/config', '/blade-book/'], h(1))
            + _visit('203.0.113.9', FIREFOX, ['/blade-book/', '/.well-known/security.txt'], h(2)))
    hits.sort(key=lambda x: x['when'])
    got, hidden = activity.visitors(hits, {}, None, OURS)
    assert [v['network'] for v in got] == ['203.0.113.9']
    assert hidden == {'bots': 1, 'requests': 4}
    for p in ('/.git/config', '/.env', '/wp-admin/install.php', '/wp-login.php', '/blade-book/x.php', '/cgi-bin/luci',
              '/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php', '/phpMyAdmin/', '/.aws/credentials'):
        assert activity.is_probe(p), p
    for p in ('/', '/blade-book/', '/.well-known/security.txt', '/@simon-collector/K80/', '/blade-book/abtesting/flow.html'):
        assert not activity.is_probe(p), p


def test_access_times_with_an_offset_come_out_as_utc():
    line = '203.0.113.9 - - [27/Sep/2026:21:01:34 -0700] "GET / HTTP/1.1" 200 1 "-" "x"\n'
    assert activity.parse_access_line(line)['when'] == datetime(2026, 9, 28, 4, 1, 34, tzinfo=timezone.utc)
    line = '203.0.113.9 - - [28/Sep/2026:06:01:34 +0200] "GET / HTTP/1.1" 200 1 "-" "x"\n'
    assert activity.parse_access_line(line)['when'] == datetime(2026, 9, 28, 4, 1, 34, tzinfo=timezone.utc)
    assert activity.parse_access_line('203.0.113.9 - - [31/Feb/2026:06:01:34 +0000] "GET / HTTP/1.1" 200 1 "-" "x"\n') is None


def test_link_previewers_and_helpers_are_not_people():
    # seen on the box 2026-09-27: these fetched a page and its picture, and showed as "Other · browser"
    for ua in ('facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)',
               'Mozilla/5.0 (compatible; CensysInspect/1.1; +https://about.censys.io/)',
               'Cloudflare-SSLDetector', 'NetworkingExtension/8624.2.5.10.4 Network/5812.122.1 iOS/26.5',
               'com.apple.WebKit.Networking/21624.5.1.11.3 Network/5812.160.9 macOS/26.6.2',
               'WhatsApp/2.23.20.0', 'Mozilla/5.0 (compatible; Discordbot/2.0)', 'Slackbot-LinkExpanding 1.0'):
        assert activity.is_bot(ua), ua
    # an in-app web view names no browser and is still a person
    assert not activity.is_bot('Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148')


def test_a_poisoned_line_is_dropped_not_raised():
    # final review C1: one bad line must never take the page down
    assert activity.parse_app_line('2026-02-31 04:00:00,000 INFO blade-book.knives: K01 saved to the register by @sam\n') is None
    assert activity.parse_app_line(app_line(T, 'photo ' + '9' * 5000 + '/1 stored for @sam (jpg, thumb=True)')) is None
    assert activity.parse_access_line(hit_line(T, '203.0.113.9', '/' + 'a' * 5000)) is None


def test_a_very_long_line_is_refused_fast():
    # final review I1: this line took 32 ms to reject; 200 of them took 6.4 s
    import time
    hostile = '203.0.113.9 - - [28/Sep/2026:04:01:34 +0000] "GET /' + 'a' * 8000 + ' \\"x" 400 1 "-" "-"\n'
    start = time.perf_counter()
    for _ in range(200):
        assert activity.parse_access_line(hostile) is None
    assert time.perf_counter() - start < 1.0


def test_an_encoded_query_is_cut_too():
    # final review minor 3, raised: a mail program can mangle ?t= into %3Ft=
    for target in ('/blade-book/api/auth/magic%3Ft=TOKENSECRET', '/blade-book/api/auth/magic%3ft=TOKENSECRET',
                   '/sell/;key=TOKENSECRET', '/sell/%3Bkey=TOKENSECRET', '/sell/%23TOKENSECRET'):
        h_ = activity.parse_access_line(hit_line(T, '203.0.113.9', target))
        assert 'TOKENSECRET' not in h_['path'], target
    assert not activity.is_page('/api/auth/magic') and not activity.is_page('/x/api/y')


def test_ipv4_inside_ipv6_is_still_that_ipv4():
    assert activity.network('::ffff:203.0.113.9') == '203.0.113.9'
