# Admin Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The admin page shows who is using blade-book, what they did, and where they are stuck, from the logs and the database we already keep.

**Architecture:** One read-only module, `bb/activity.py`, parses the app log and the web server log into named fields and joins them with three small database reads. One admin route returns the summary as JSON. The admin page gains three sections drawn by `html/admin/activity.js`.

**Tech Stack:** Python 3 standard library (`re`, `gzip`, `ipaddress`), Flask, SQLite, plain JavaScript. No new dependency.

**Spec:** `docs/superpowers/specs/2026-09-27-admin-activity-design.md`

## Global Constraints

- No new recording: no new table, no new log line, no script on public pages, no beacon.
- The module writes nothing.
- No raw log line, no query string, no token leaves the server.
- The route sits behind `auth.admin_required`.
- `hours` is one of 48, 168, 336.
- SQL lives in `bb/db.py`.
- The page draws with `textContent` only. `innerHTML` never appears.
- Tests never read the box: `BLADEBOOK_ACCESS_LOG` is pointed at `tmp_path` by the autouse fixture.
- Every new file starts with the copyright line the other files carry.
- Work in the worktree `.worktrees/admin-activity`. Run tests with `python3 -m pytest` from the worktree root.

## Review Focus

1. **A log line forged through user input.** An upload's file name is user text and lands in the app log. A name that carries a line break and a fake "password sign-in" line must not become a trail entry or a guess. Test in Task 2: the parser reads one physical line at a time and the fake second line has no timestamp prefix of its own unless the attacker writes one, so the test plants a full fake line inside a file name and checks that the record's fields hold only the extension.
2. **A handle that changed.** Old log lines carry the old handle. The trail for the new handle misses them, and nothing crashes. Test in Task 5: records for a handle with no account are ignored by `people` and still count in `needs_help`.
3. **An IPv6 visitor whose address changes inside the same home network.** Two addresses in one `/64` are one visitor. Test in Task 3 and Task 5.
4. **A huge or broken rotated file.** A truncated `.gz` reports its error and keeps the lines it could read. Test in Task 3.
5. **A path that carries a secret in its query string.** `/blade-book/api/auth/magic?t=SECRET` and `/sell/?key=SECRET` must appear without the query. Test in Task 3 and Task 6 (the whole response is searched for the planted values).

---

## File Structure

| File | Responsibility |
|---|---|
| `bb/paths.py` (modify) | `access_log()`: where the web server log is |
| `bb/db.py` (modify) | `activity_people`, `activity_drafts`, `knife_tags` |
| `bb/activity.py` (create) | parsers, stuck signals, people, visitors, `summary` |
| `bb/routes/admin.py` (modify) | `GET /api/admin/activity` |
| `html/admin/index.html` (modify) | three sections, window switch, styles |
| `html/admin/activity.js` (create) | fetch and draw |
| `html/terms/index.html`, `html/faq/index.html` (modify) | the trust line |
| `docs/ENV.md` (modify) | names `BLADEBOOK_ACCESS_LOG` |
| `scripts/deploy_activity.sh` (create) | go live |
| `tests/conftest.py` (modify) | point `BLADEBOOK_ACCESS_LOG` at `tmp_path` |
| `tests/test_activity.py` (create) | the module |
| `tests/test_admin_activity_api.py` (create) | the route |
| `tests/test_deploy_files.py` (modify) | page, terms, FAQ, script wiring |

---

### Task 1: The path and the three database reads

**Files:**
- Modify: `bb/paths.py`, `bb/db.py`, `tests/conftest.py`, `docs/ENV.md`
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces: `paths.access_log() -> str`; `db.activity_people(con) -> list[dict]` with keys `id, handle, email, last_active, knives, drafts`; `db.activity_drafts(con) -> list[dict]` with keys `id, tag, handle, updated`; `db.knife_tags(con) -> dict[int, str]`.

- [ ] **Step 1: Guard the tests.** In `tests/conftest.py`, inside `env`, after the `for name in (...)` loop:

```python
    monkeypatch.setenv('BLADEBOOK_ACCESS_LOG', str(tmp_path / 'log' / 'access.log'))   # never the box's Apache log
```

- [ ] **Step 2: Write the failing tests.** Create `tests/test_activity.py`:

```python
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
    _mk_knife(con, u['id'], status='live')
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
    live = _mk_knife(con, u['id'], status='live')
    draft = db.create_draft_knife(con, u['id'])
    rows = db.activity_drafts(con)
    assert [(r['tag'], r['handle']) for r in rows] == [(draft['tag'], 'idx-guy')]
    assert set(rows[0]) == {'id', 'tag', 'handle', 'updated'}
    assert db.knife_tags(con) == {live['id']: live['tag'], draft['id']: draft['tag']}
```

- [ ] **Step 3: Run them and see them fail.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: FAIL, `AttributeError: module 'bb.paths' has no attribute 'access_log'` and the same for the `db` functions.

If `_mk_knife` does not return the knife row, read `tests/test_search.py` and use what it returns; the assertions need the knife's `id` and `tag`.

- [ ] **Step 4: Implement.** In `bb/paths.py`, after `ai_log()`:

```python
def access_log():
    """The web server's request log for this site. The admin activity view
    reads it; nothing here writes it. Rotations sit beside it: .1, then .N.gz."""
    return os.environ.get('BLADEBOOK_ACCESS_LOG', '/var/log/apache2/blade-book_access.log')
```

In `bb/db.py`, after `admin_users`:

```python
def activity_people(con):
    """Every account for the admin activity view: who, when last active, how
    many knives and drafts. A whitelist, like admin_users."""
    rows = con.execute(
        'SELECT u.id, u.handle, u.email, '
        '(SELECT max(s.last_seen) FROM sessions s WHERE s.user_id = u.id) AS last_active, '
        "(SELECT count(*) FROM knives k WHERE k.owner_id = u.id AND k.status = 'live') AS knives, "
        "(SELECT count(*) FROM knives k WHERE k.owner_id = u.id AND k.status = 'draft') AS drafts "
        'FROM users u ORDER BY u.id').fetchall()
    return [dict(r) for r in rows]


def activity_drafts(con):
    """Every draft and its owner, oldest first. purge_stale_drafts removes a
    draft 7 days after `updated`."""
    rows = con.execute(
        'SELECT k.id, k.tag, u.handle, k.updated FROM knives k JOIN users u ON u.id = k.owner_id '
        "WHERE k.status = 'draft' ORDER BY k.updated, k.id").fetchall()
    return [dict(r) for r in rows]


def knife_tags(con):
    """Knife id to tag, for log lines that name a knife by id."""
    return {r['id']: r['tag'] for r in con.execute('SELECT id, tag FROM knives')}
```

In `docs/ENV.md`, extend the paragraph that starts "Paths and the port are NOT here":

```markdown
Paths and the port are NOT here — `BLADEBOOK_*_DIR` / `BLADEBOOK_PORT` are read by `bb/paths.py` and set by the supervisor program, not `.env`. The same goes for `BLADEBOOK_ACCESS_LOG` (default `/var/log/apache2/blade-book_access.log`), the web server log the admin activity view reads; the app's user must be able to read it (group `adm` on stark).
```

- [ ] **Step 5: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: 4 passed.

- [ ] **Step 6: Commit.**

```bash
git add bb/paths.py bb/db.py tests/conftest.py tests/test_activity.py docs/ENV.md
git commit -m "activity: the access log path and three database reads"
```

---

### Task 2: The app log parser

**Files:**
- Create: `bb/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces: `activity.parse_app_line(line) -> dict | None`. A record has `kind` (str), `when` (aware `datetime`, UTC), and whichever of `handle, email, ip, tag, knife_id, seq, fields, status, error, ext, how` the line carries. `activity.read_app_log(path) -> (list[dict], dict)`: records oldest first, and an info dict `{'ok': bool, 'lines': int, 'error'?: str, 'stopped'?: True}`.
- Kinds: `signed_in, link_requested, link_unbound, password_failed, rate_limited, draft_started, photo_added, decoded, edited, saved, sale_status, deleted, settings, save_blocked, decode_failed, upload_refused`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_activity.py`:

```python
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
```

- [ ] **Step 2: Run them and see them fail.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: FAIL, `ImportError: cannot import name 'activity' from 'bb'`.

- [ ] **Step 3: Implement.** Create `bb/activity.py`:

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/activity.py — who is using blade-book and where they are stuck, for the
admin page (spec 2026-09-27-admin-activity-design.md).

Reads the database, the app log and the web server's request log. Writes
nothing. No raw log line ever leaves: the parsers match known shapes and
return named fields, and an unknown line is dropped."""
import glob
import gzip
import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from bb import db, paths

WINDOWS = (48, 168, 336)                  # hours
MAX_LINES = 400_000                       # per log, newest files first
LINK_TTL = timedelta(minutes=15)          # db.MAGIC_TTL_MIN
LINK_USED_WITHIN = timedelta(minutes=20)
DRAFT_QUIET = timedelta(hours=1)
DRAFT_PURGE = timedelta(days=7)           # scripts/purge_drafts.py
TRAIL_MAX = 50
PAGES_MAX = 30

# --- the app log ---------------------------------------------------------------

_APP_LINE = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d{3} [A-Z]+ blade-book[\w.]*: (.*)$')
_H = r'(?P<handle>[A-Za-z0-9][A-Za-z0-9-]*)'
_T = r'(?P<tag>[A-Z]{1,3}\d{1,6})'
_E = r'(?P<email>[^@\s]+@[^@\s]+)'
_IP = r'(?P<ip>[0-9A-Fa-f.:]+)'

_SHAPES = tuple((kind, re.compile(rx), extra) for kind, rx, extra in (
    ('signed_in', rf'^magic link sign-in(?: \(confirmed\))?: {_E} \(@{_H}\)$', {'how': 'link'}),
    ('signed_in', rf'^password sign-in: @{_H} from {_IP}$', {'how': 'password'}),
    ('signed_in', rf'^(?P<how>(?!magic\b|password\b)[a-z]+) sign-in: {_E} \(@{_H}\)$', {}),
    ('link_requested', rf'^magic link requested for {_E} from {_IP}$', {}),
    ('link_unbound', rf'^magic link for {_E} opened unbound from {_IP} \(signed in: [A-Za-z0-9-]+\)$', {}),
    ('password_failed', rf'^password sign-in failed for @{_H} from {_IP}$', {}),
    ('rate_limited', rf'^password sign-in rate limited for @{_H} from {_IP}: ', {}),
    ('rate_limited', rf'^rate limited {_E} from {_IP}: ', {}),
    ('draft_started', rf'^draft {_T} created for @{_H}$', {}),
    ('photo_added', rf'^photo (?P<knife_id>\d+)/(?P<seq>\d+) stored for @{_H} ', {}),
    ('decoded', rf'^decoded {_T} for @{_H} via ', {}),
    ('edited', rf'^{_T} edited by @{_H}: (?P<fields>[a-z_]+(?:, [a-z_]+)*)$', {}),
    ('saved', rf'^{_T} saved to the register by @{_H}$', {}),
    ('sale_status', rf'^{_T} sale_status → (?P<status>[a-z_]+) by @{_H}$', {}),
    ('deleted', rf'^(?:draft )?{_T} deleted by @{_H} ', {}),
    ('settings', rf"^settings changed for @{_H}: \[(?P<fields>[a-z_', ]*)\]$", {}),
    ('save_blocked', rf'^{_T} save gated for @{_H} ', {}),
    ('decode_failed', rf'^decode failed for {_H}/{_T}: (?P<error>[A-Za-z_][A-Za-z0-9_]*)', {}),
    ('upload_refused', rf'^undecodable upload refused for @{_H}: (?P<name>.*)$', {}),
))
_EXT = re.compile(r'^\.[a-z0-9]{1,8}$')


def _local_to_utc(text):
    """app.log is stamped in the box's local time, no offset."""
    return datetime.strptime(text, '%Y-%m-%d %H:%M:%S').astimezone(timezone.utc)


def parse_app_line(line):
    """One app.log line as named fields, or None for a line we do not know."""
    m = _APP_LINE.match(line.rstrip('\n'))
    if not m:
        return None
    for kind, rx, extra in _SHAPES:
        hit = rx.match(m.group(2))
        if not hit:
            continue
        rec = {k: v for k, v in hit.groupdict().items() if v is not None}
        rec.update(extra, kind=kind, when=_local_to_utc(m.group(1)))
        if 'email' in rec:
            rec['email'] = rec['email'].lower()
        if kind == 'settings':
            rec['fields'] = rec['fields'].replace("'", '')
        if kind == 'upload_refused':                    # a file name is user text: keep the extension, nothing else
            ext = os.path.splitext(rec.pop('name'))[1].lower()
            rec['ext'] = ext if _EXT.match(ext) else ''
        return rec
    return None


def _lines(path, opener=open):
    with opener(path, 'rt', encoding='utf-8', errors='replace') as f:
        yield from f


def _why(path, e):
    return f'could not read {path}: {getattr(e, "strerror", None) or e}'


def read_app_log(path):
    """Every known line in app.log and its rotations (.1 to .5, plain text),
    oldest first, and how the read went."""
    info = {'ok': True, 'lines': 0}
    recs = []
    for p in [path] + [f'{path}.{n}' for n in range(1, 6)]:
        if p != path and not os.path.exists(p):
            continue
        try:
            for line in _lines(p):
                info['lines'] += 1
                rec = parse_app_line(line)
                if rec:
                    recs.append(rec)
                if info['lines'] >= MAX_LINES:
                    info['stopped'] = True
                    break
        except OSError as e:
            info.update(ok=False, error=_why(p, e))
        if info.get('stopped'):
            break
    recs.sort(key=lambda r: r['when'])
    return recs, info
```

- [ ] **Step 4: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit.**

```bash
git add bb/activity.py tests/test_activity.py
git commit -m "activity: the app log parser — known shapes in, named fields out"
```

---

### Task 3: The web server log parser

**Files:**
- Modify: `bb/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Produces: `activity.network(ip) -> str | None`; `activity.device(ua) -> str`; `activity.is_page(path) -> bool`; `activity.is_asset(path) -> bool`; `activity.is_bot(ua) -> bool`; `activity.parse_access_line(line) -> dict | None` with keys `net, when, method, path, status, ref_host, ua`; `activity.read_access_log(path, since) -> (list[dict], dict)`: hits at or after `since`, oldest first, and `{'ok', 'lines', 'files', 'skipped'?, 'error'?, 'stopped'?}`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_activity.py`:

```python
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
```

- [ ] **Step 2: Run them and see them fail.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: FAIL, `AttributeError: module 'bb.activity' has no attribute 'network'`.

- [ ] **Step 3: Implement.** Append to `bb/activity.py`:

```python
# --- the web server log ---------------------------------------------------------

_ACCESS = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<target>\S+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<ref>(?:[^"\\]|\\.)*)" "(?P<ua>(?:[^"\\]|\\.)*)"')
_BOT = re.compile(r'bot|crawl|spider|slurp|curl|wget|python|go-http|scrapy|headless|monitor|uptime|scan|'
                  r'libwww|okhttp|java/|node|axios|httpclient|^https?://|^-?$', re.I)
_ASSETS = ('.css', '.js', '.woff2', '.woff', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico')
PATH_MAX = 200


def network(ip):
    """An IPv4 address as it is; an IPv6 address cut to its /64, because a
    home's devices share the /64 and change the rest. None for junk."""
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if a.version == 4:
        return str(a)
    return str(ipaddress.ip_network((int(a) >> 64 << 64, 64)))


def device(ua):
    os_ = next((name for mark, name in (('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Android', 'Android'),
                                        ('Windows', 'Windows'), ('Macintosh', 'Mac'), ('Linux', 'Linux'))
                if mark in ua), 'Other')
    app = next((name for marks, name in ((('FBAN', 'FBAV'), 'Facebook app'), (('Instagram',), 'Instagram app'),
                                         (('Edg/',), 'Edge'), (('Firefox/', 'FxiOS'), 'Firefox'),
                                         (('Chrome/', 'CriOS'), 'Chrome'), (('Safari/',), 'Safari'))
                if any(m in ua for m in marks)), 'browser')
    return f'{os_} · {app}'


def is_bot(ua):
    return bool(_BOT.search(ua))


def is_asset(path):
    return path.lower().endswith(_ASSETS)


def is_page(path):
    if path.startswith(paths.API_PREFIX + '/'):
        return False
    last = path.rsplit('/', 1)[-1]
    return last == '' or last.endswith('.html') or '.' not in last


def _host(url):
    if url in ('', '-'):
        return ''
    try:
        return (urlsplit(url).hostname or '').lower()
    except ValueError:
        return ''


def parse_access_line(line):
    """One combined-format line as named fields, or None. The query string is
    dropped, always: sign-in links and upload keys travel there. Of the
    referrer only the host is kept, for the same reason."""
    m = _ACCESS.match(line)
    if not m:
        return None
    net = network(m.group('ip'))
    if net is None:
        return None
    try:
        when = datetime.strptime(m.group('ts'), '%d/%b/%Y:%H:%M:%S %z').astimezone(timezone.utc)
        path = urlsplit(m.group('target')).path or '/'
    except ValueError:
        return None
    return {'net': net, 'when': when, 'method': m.group('method'), 'path': path[:PATH_MAX],
            'status': int(m.group('status')), 'ref_host': _host(m.group('ref')), 'ua': m.group('ua')}


def _access_files(path):
    """Newest first: the live file, .1, then .2.gz upward."""
    out = [path]
    if os.path.exists(path + '.1'):
        out.append(path + '.1')
    gz = []
    for p in glob.glob(glob.escape(path) + '.*.gz'):
        n = p[len(path) + 1:-3]
        if n.isdigit():
            gz.append((int(n), p))
    return out + [p for _n, p in sorted(gz)]


def read_access_log(path, since):
    """Every request at or after `since`, oldest first, and how the read went.
    A rotated file last written before `since` ends the walk: it and every
    older file hold nothing from the window."""
    info = {'ok': True, 'lines': 0, 'files': 0}
    hits = []
    for p in _access_files(path):
        try:
            if p != path and datetime.fromtimestamp(os.path.getmtime(p), timezone.utc) < since:
                break
            counted = False
            for line in _lines(p, gzip.open if p.endswith('.gz') else open):
                if not counted:
                    info['files'] += 1
                    counted = True
                info['lines'] += 1
                hit = parse_access_line(line)
                if hit is None:
                    info['skipped'] = info.get('skipped', 0) + 1
                elif hit['when'] >= since:
                    hits.append(hit)
                if info['lines'] >= MAX_LINES:
                    info['stopped'] = True
                    break
        except (OSError, EOFError) as e:
            info.update(ok=False, error=_why(p, e))
        if info.get('stopped'):
            break
    hits.sort(key=lambda h: h['when'])
    return hits, info
```

- [ ] **Step 4: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: 19 passed.

- [ ] **Step 5: Commit.**

```bash
git add bb/activity.py tests/test_activity.py
git commit -m "activity: the web server log parser — networks, devices, no query strings"
```

---

### Task 4: The stuck signals

**Files:**
- Modify: `bb/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: records from `parse_app_line`; draft rows from `db.activity_drafts`.
- Produces: `activity.needs_help(recs, drafts, since, now) -> list[dict]`, newest first. Each item: `kind, who, when` (ISO str), `detail`, and `tag`, `purge_at` (ISO str) where they apply. `who` is `@handle` or an email.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_activity.py`:

```python
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
```

- [ ] **Step 2: Run them and see them fail.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: FAIL, `AttributeError: module 'bb.activity' has no attribute 'needs_help'`.

- [ ] **Step 3: Implement.** Append to `bb/activity.py`:

```python
# --- the stuck signals ----------------------------------------------------------

def _n(n, one, many=None):
    return f'{n} {one if n == 1 else (many or one + "s")}'


def _latest(recs, kind, key):
    """key(record) -> when of the newest record of that kind."""
    out = {}
    for r in recs:                                      # oldest first, so the last write is the newest
        if r['kind'] == kind:
            out[key(r)] = r['when']
    return out


def _stuck(recs, kind, key, cleared, since):
    """Records of `kind` in the window, grouped by key, that came after the
    last thing that cleared them. key -> records, oldest first."""
    out = {}
    for r in recs:
        if r['kind'] == kind and r['when'] >= since and r['when'] > cleared.get(key(r), since - timedelta(seconds=1)):
            out.setdefault(key(r), []).append(r)
    return out


def needs_help(recs, drafts, since, now):
    """Where people are stuck, newest first (spec: NEEDS HELP)."""
    out = []
    EPOCH = datetime.min.replace(tzinfo=timezone.utc)

    signed_in = _latest(recs, 'signed_in', lambda r: r.get('email'))
    asked = _stuck([r for r in recs if r['when'] <= now - LINK_TTL], 'link_requested',
                   lambda r: r['email'], signed_in, since)
    opened = _stuck(recs, 'link_unbound', lambda r: r['email'], signed_in, since)
    for email, rs in asked.items():
        tail = 'opened in another browser, never confirmed' if email in opened else 'never opened'
        out.append({'kind': 'link_unclicked', 'who': email, 'when': rs[-1]['when'],
                    'detail': f'{_n(len(rs), "sign-in link")} sent, {tail}'})

    got_in = _latest(recs, 'signed_in', lambda r: r['handle'])
    for handle, rs in _stuck(recs, 'password_failed', lambda r: r['handle'], got_in, since).items():
        out.append({'kind': 'password_failed', 'who': '@' + handle, 'when': rs[-1]['when'],
                    'detail': _n(len(rs), 'failed password sign-in')})

    who = lambda r: '@' + r['handle'] if 'handle' in r else r['email']
    for name, rs in _stuck(recs, 'rate_limited', who, {}, since).items():
        out.append({'kind': 'rate_limited', 'who': name, 'when': rs[-1]['when'],
                    'detail': f'rate limited {_n(len(rs), "time")}'})

    knife = lambda r: (r['handle'], r['tag'])
    for (handle, tag), rs in _stuck(recs, 'save_blocked', knife, _latest(recs, 'saved', knife), since).items():
        times = '' if len(rs) == 1 else f', {_n(len(rs), "time")}'
        out.append({'kind': 'save_blocked', 'who': '@' + handle, 'when': rs[-1]['when'], 'tag': tag,
                    'detail': f'save of {tag} blocked by the gate{times}'})
    for (handle, tag), rs in _stuck(recs, 'decode_failed', knife, _latest(recs, 'decoded', knife), since).items():
        out.append({'kind': 'decode_failed', 'who': '@' + handle, 'when': rs[-1]['when'], 'tag': tag,
                    'detail': f'decode of {tag} failed: {rs[-1]["error"]}'})

    for handle, rs in _stuck(recs, 'upload_refused', lambda r: r['handle'], {}, since).items():
        ext = rs[-1]['ext']
        out.append({'kind': 'upload_refused', 'who': '@' + handle, 'when': rs[-1]['when'],
                    'detail': _n(len(rs), 'photo') + ' refused' + (f', last one {ext}' if ext and len(rs) > 1 else '')})

    for d in drafts:                                    # the window does not apply: stuck until saved or purged
        updated = datetime.fromisoformat(d['updated'])
        if now - updated >= DRAFT_QUIET:
            out.append({'kind': 'draft_unfinished', 'who': '@' + d['handle'], 'when': updated, 'tag': d['tag'],
                        'detail': f'draft {d["tag"]} started, never saved', 'purge_at': (updated + DRAFT_PURGE).isoformat()})

    out.sort(key=lambda s: s['when'] or EPOCH, reverse=True)
    for s in out:
        s['when'] = s['when'].isoformat()
    return out
```

Note for the implementer: the single-photo test expects `'1 photo refused'` with no extension even when there is one; the two-photo test expects `', last one .tiff'`. The code above does that (`len(rs) > 1`). The blocked-save test with one block expects no `, 1 time` tail.

- [ ] **Step 4: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: 30 passed.

- [ ] **Step 5: Commit.**

```bash
git add bb/activity.py tests/test_activity.py
git commit -m "activity: the stuck signals, and what clears each one"
```

---

### Task 5: People, visitors, the guess, the summary

**Files:**
- Modify: `bb/activity.py`
- Test: `tests/test_activity.py`

**Interfaces:**
- Consumes: everything above; `db.activity_people`, `db.activity_drafts`, `db.knife_tags`.
- Produces: `activity.guesses(recs) -> dict[str, set[str]]`; `activity.people(rows, recs, tags, since) -> list[dict]`; `activity.visitors(hits, guess, you, own_hosts) -> (list[dict], dict)`; `activity.summary(con, hours, you=None, own_hosts=(), now=None, app_log=None, access_log=None) -> dict` in the shape the spec's API section shows.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_activity.py`:

```python
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
```

- [ ] **Step 2: Run them and see them fail.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: FAIL, `AttributeError: module 'bb.activity' has no attribute 'guesses'`.

- [ ] **Step 3: Implement.** Append to `bb/activity.py`:

```python
# --- people ---------------------------------------------------------------------

def _what(r, tags):
    k = r['kind']
    if k == 'photo_added':
        tag = tags.get(int(r['knife_id']))
        return f"added photo {r['seq']} to {tag or 'a knife'}"
    return {
        'signed_in': 'signed in',
        'link_requested': 'asked for a sign-in link',
        'link_unbound': 'opened a sign-in link in another browser',
        'password_failed': 'password failed',
        'rate_limited': 'rate limited',
        'draft_started': 'started draft {tag}',
        'decoded': 'decoded {tag}',
        'edited': 'edited {tag}: {fields}',
        'saved': 'saved {tag}',
        'sale_status': '{tag} → {status}',
        'deleted': 'deleted {tag}',
        'settings': 'changed settings: {fields}',
        'save_blocked': 'save of {tag} blocked',
        'decode_failed': 'decode of {tag} failed',
        'upload_refused': 'photo refused',
    }[k].format(**{f: r.get(f, '') for f in ('tag', 'fields', 'status')})


def people(rows, recs, tags, since):
    """One row per account, most recently active first, each with what the app
    log says that handle did in the window. A line that names an email and no
    handle (a link request) belongs to the account with that email."""
    by_email = {r['email']: r['handle'] for r in rows}
    trails = {}
    for r in reversed(recs):                            # newest first
        if r['when'] < since:
            break
        handle = r.get('handle') or by_email.get(r.get('email'))
        trail = trails.setdefault(handle, [])
        if len(trail) < TRAIL_MAX:
            trail.append({'when': r['when'].isoformat(), 'what': _what(r, tags)})
    out = [{'handle': r['handle'], 'email': r['email'], 'last_active': r['last_active'], 'knives': r['knives'],
            'drafts': r['drafts'], 'trail': trails.get(r['handle'], [])} for r in rows]
    newest = lambda p: max(p['last_active'] or '', p['trail'][0]['when'] if p['trail'] else '')
    out.sort(key=lambda p: (newest(p), p['handle']), reverse=True)
    return out


# --- visitors -------------------------------------------------------------------

def guesses(recs):
    """network -> the handles that signed in from it: a password sign-in from
    that address, or a link asked for from it and used within 20 minutes. A
    failed password or an unused link proves nothing."""
    out = {}
    asked = []
    for r in recs:                                      # oldest first
        net = network(r['ip']) if 'ip' in r else None
        if r['kind'] == 'link_requested' and net:
            asked.append((r['email'], net, r['when']))
        elif r['kind'] == 'signed_in' and r['how'] == 'password' and net:
            out.setdefault(net, set()).add(r['handle'])
        elif r['kind'] == 'signed_in' and 'email' in r:
            for email, asked_net, when in asked:
                if email == r['email'] and timedelta(0) <= r['when'] - when <= LINK_USED_WITHIN:
                    out.setdefault(asked_net, set()).add(r['handle'])
    return out


def visitors(hits, guess, you, own_hosts):
    """People who came by, newest first, and a count of what was hidden."""
    hidden = {'bots': 0, 'requests': 0}
    api = paths.API_PREFIX + '/'
    bots, groups = set(), {}
    for h in hits:                                      # oldest first
        if is_bot(h['ua']):
            bots.add((h['net'], h['ua']))
            hidden['requests'] += 1
        else:
            groups.setdefault((h['net'], device(h['ua'])), []).append(h)
    hidden['bots'] = len(bots)
    out = []
    for (net, dev), hs in groups.items():
        pages = [h for h in hs if h['method'] == 'GET' and is_page(h['path'])]
        browser = any((is_asset(h['path']) and h['status'] < 400)
                      or (h['method'] == 'GET' and h['path'].startswith(api) and h['ref_host'] in own_hosts)
                      for h in hs)
        if not browser or not any(p['status'] < 400 for p in pages):
            hidden['bots'] += 1
            hidden['requests'] += len(hs)
            continue
        names = sorted(guess.get(net, ()))
        out.append({'network': net, 'guess': names, 'you': bool(you and you in names), 'device': dev,
                    'came_from': next((h['ref_host'] for h in hs if h['ref_host'] and h['ref_host'] not in own_hosts), ''),
                    'first': hs[0]['when'].isoformat(), 'last': hs[-1]['when'].isoformat(), 'requests': len(hs),
                    'pages': [{'when': p['when'].isoformat(), 'path': p['path'], 'status': p['status']}
                              for p in reversed(pages)][:PAGES_MAX]})
    out.sort(key=lambda v: v['last'], reverse=True)
    return out, hidden


# --- the summary ----------------------------------------------------------------

def summary(con, hours, you=None, own_hosts=(), now=None, app_log=None, access_log=None):
    """Everything the admin page draws. A source that cannot be read reports
    its error in `sources`; the rest is still filled."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    own = set(own_hosts) | {'www.' + h for h in own_hosts}
    recs, app_info = read_app_log(app_log or os.path.join(paths.LOG_DIR, 'app.log'))
    hits, access_info = read_access_log(access_log or paths.access_log(), since)
    seen, hidden = visitors(hits, guesses(recs), you, own)
    return {'hours': hours, 'generated': now.isoformat(), 'you': you,
            'needs_help': needs_help(recs, db.activity_drafts(con), since, now),
            'people': people(db.activity_people(con), recs, db.knife_tags(con), since),
            'visitors': seen, 'hidden': hidden,
            'sources': {'database': {'ok': True}, 'app_log': app_info, 'access_log': access_info}}
```

- [ ] **Step 4: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_activity.py -q`
Expected: 37 passed.

- [ ] **Step 5: Commit.**

```bash
git add bb/activity.py tests/test_activity.py
git commit -m "activity: people, visitors, the guess, the summary"
```

---

### Task 6: The route

**Files:**
- Modify: `bb/routes/admin.py`
- Test: `tests/test_admin_activity_api.py`

**Interfaces:**
- Consumes: `activity.summary`, `activity.WINDOWS`.
- Produces: `GET /blade-book/api/admin/activity?hours=48|168|336`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_admin_activity_api.py`:

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
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
```

- [ ] **Step 2: Run them and see them fail.**

Run: `python3 -m pytest tests/test_admin_activity_api.py -q`
Expected: FAIL, the first assertion gets 404 where it wants 401.

- [ ] **Step 3: Implement.** In `bb/routes/admin.py`:

Change the module docstring's last sentence to: `Plan 10 added users + the ManualBilling flip; 2026-09-27 added the activity view.`

Change the imports:

```python
import logging
import os
from urllib.parse import urlsplit

from flask import Blueprint, current_app, g, jsonify, request

from bb import activity, auth, db, edit, paths, publish
```

Append at the end of the file:

```python
# --- activity (2026-09-27) -----------------------------------------------------

@bp.get('/activity', strict_slashes=False)
@auth.admin_required
def activity_summary():
    """Who is using blade-book and where they are stuck. Read-only."""
    raw = request.args.get('hours', '48')
    hours = int(raw) if raw.isascii() and raw.isdigit() else 0
    if hours not in activity.WINDOWS:
        return jsonify({'error': 'hours must be one of ' + ', '.join(map(str, activity.WINDOWS))}), 400
    own = {request.host.split(':')[0].lower(), (urlsplit(os.environ.get('BASE_URL', '')).hostname or '').lower()} - {''}
    con = db.connect()
    try:
        out = activity.summary(con, hours, you=g.user['handle'], own_hosts=own)
    finally:
        con.close()
    return jsonify(out)
```

- [ ] **Step 4: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_admin_activity_api.py tests/test_admin_api.py tests/test_admin_users_api.py -q`
Expected: all pass.

- [ ] **Step 5: Commit.**

```bash
git add bb/routes/admin.py tests/test_admin_activity_api.py
git commit -m "activity: GET /api/admin/activity behind the admin gate"
```

---

### Task 7: The page

**Files:**
- Modify: `html/admin/index.html`
- Create: `html/admin/activity.js`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Consumes: the route's JSON.
- Produces: elements `#window`, `#help`, `#people`, `#visitors` on `/admin/`.

- [ ] **Step 1: Write the failing test.** Append to `tests/test_deploy_files.py`:

```python
def test_admin_activity_wiring():
    html = _read('html/admin/index.html')
    for needle in ('NEEDS HELP', 'PEOPLE', 'VISITORS', 'id="window"', 'id="help"', 'id="people"', 'id="visitors"',
                   'data-hours="48"', 'data-hours="168"', 'data-hours="336"',
                   '<script src="/blade-book/admin/activity.js?v=20260928" defer></script>'):
        assert needle in html, needle
    assert html.index('NEEDS HELP') < html.index('USERS') < html.index('REPORTS')
    js = _read('html/admin/activity.js')
    for needle in ("'/blade-book/api/admin/activity'", 'needs_help', 'people', 'visitors', 'hidden', 'sources',
                   'probably', 'nobody is stuck', 'purge_at', 'textContent', 'Copyright (c) 2026 Simon SGH'):
        assert needle in js, needle
    assert 'innerHTML' not in js and 'insertAdjacentHTML' not in js and 'document.write' not in js
```

- [ ] **Step 2: Run it and see it fail.**

Run: `python3 -m pytest tests/test_deploy_files.py::test_admin_activity_wiring -q`
Expected: FAIL, `AssertionError: NEEDS HELP`.

- [ ] **Step 3: Implement the page.** In `html/admin/index.html`, add to the end of the `<style>` block:

```css
  #window{display:flex;gap:6px;justify-content:center;margin:10px 0 2px}
  #window .btn{padding:.35rem .7rem;font-size:.8rem}
  #window .btn[aria-pressed="true"]{background:var(--ink);color:#fff}
  .sig{background:#fff;border:2px solid var(--ink);border-left:8px solid var(--accent);border-radius:12px;padding:8px 12px;margin-bottom:8px;font-size:.9rem}
  .sig .top{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
  .sig .who{font-weight:700;overflow-wrap:anywhere}
  .when{color:var(--muted);font-size:.8rem;margin-left:auto;white-space:nowrap}
  .sig .detail{margin-top:2px}
  .sig .purge{color:var(--accent);font-weight:700;font-size:.82rem}
  details.arow{background:#fff;border:2px solid var(--ink);border-radius:12px;padding:8px 12px;margin-bottom:8px;font-size:.9rem}
  details.arow.you{opacity:.6}
  details.arow summary{cursor:pointer;display:flex;gap:8px;align-items:center;flex-wrap:wrap;list-style:none}
  details.arow summary::-webkit-details-marker{display:none}
  details.arow summary .name{font-weight:700;overflow-wrap:anywhere}
  details.arow .meta{color:var(--muted);font-size:.8rem;overflow-wrap:anywhere}
  details.arow ul{margin:8px 0 2px;padding-left:18px}
  details.arow li{margin:2px 0;overflow-wrap:anywhere}
  details.arow li span{color:var(--muted);font-size:.8rem}
  .srcerr{color:var(--accent);font-weight:700;font-size:.85rem;margin:0 0 8px;overflow-wrap:anywhere}
  .note{color:var(--muted);font-size:.8rem;text-align:center;margin:4px 0 0}
```

Replace the two lines

```html
<div id="status" aria-live="polite"></div>
<h2 class="bb-display" style="margin:18px 0 6px">USERS</h2>
```

with

```html
<div id="status" aria-live="polite"></div>
<div id="window" role="group" aria-label="how far back">
  <button class="btn alt" type="button" data-hours="48" aria-pressed="true">48 hours</button>
  <button class="btn alt" type="button" data-hours="168" aria-pressed="false">7 days</button>
  <button class="btn alt" type="button" data-hours="336" aria-pressed="false">14 days</button>
</div>
<h2 class="bb-display" style="margin:18px 0 6px">NEEDS HELP</h2>
<div id="help"></div>
<h2 class="bb-display" style="margin:18px 0 6px">PEOPLE</h2>
<div id="people"></div>
<h2 class="bb-display" style="margin:18px 0 6px">VISITORS</h2>
<div id="visitors"></div>
<h2 class="bb-display" style="margin:18px 0 6px">USERS</h2>
```

Before the `nav.js` script tag at the end, add:

```html
<script src="/blade-book/admin/activity.js?v=20260928" defer></script>
```

- [ ] **Step 4: Implement the script.** Create `html/admin/activity.js`:

```javascript
/* Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE) */
/* The admin page's activity sections: who is stuck, who did what, who came by.
   Text goes in with textContent only. The page's own script sends a signed-out
   or non-admin visitor home; this one stays quiet for them. */
(function () {
  var URL = '/blade-book/api/admin/activity';
  var KIND = { link_unclicked: 'sign-in link', password_failed: 'password', rate_limited: 'rate limit',
               save_blocked: 'save blocked', decode_failed: 'decode failed', upload_refused: 'photo refused',
               draft_unfinished: 'unfinished draft' };
  var $ = function (id) { return document.getElementById(id); };
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function fill(id, nodes, empty) {
    var box = $(id); clear(box);
    if (!nodes.length) { box.appendChild(el('div', 'empty', empty)); return box; }
    nodes.forEach(function (n) { box.appendChild(n); });
    return box;
  }
  function ago(iso) {
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return 'just now';
    if (s < 5400) return Math.round(s / 60) + ' min ago';
    if (s < 129600) return Math.round(s / 3600) + ' h ago';
    return Math.round(s / 86400) + ' days ago';
  }
  function local(iso) { return new Date(iso).toLocaleString(); }
  function when(iso) { var w = el('span', 'when', ago(iso)); w.title = local(iso); return w; }
  function plural(n, one) { return n + ' ' + one + (n === 1 ? '' : 's'); }

  function signal(s) {
    var card = el('div', 'sig');
    var top = el('div', 'top');
    top.appendChild(el('span', 'badge', KIND[s.kind] || s.kind));
    top.appendChild(el('span', 'who', s.who));
    top.appendChild(when(s.when));
    card.appendChild(top);
    card.appendChild(el('div', 'detail', s.detail));
    if (s.purge_at) card.appendChild(el('div', 'purge', 'purges ' + local(s.purge_at)));
    return card;
  }

  function list(items, line) {
    var ul = el('ul');
    items.forEach(function (it) {
      var li = el('li', null, line(it) + ' ');
      var t = el('span', null, ago(it.when)); t.title = local(it.when);
      li.appendChild(t);
      ul.appendChild(li);
    });
    return ul;
  }

  function person(p) {
    var row = el('details', 'arow');
    var sum = el('summary');
    sum.appendChild(el('span', 'name', '@' + p.handle));
    if (p.drafts) sum.appendChild(el('span', 'badge warn', plural(p.drafts, 'draft')));
    if (p.trail.length) sum.appendChild(el('span', 'badge', plural(p.trail.length, 'action')));
    if (p.last_active) sum.appendChild(when(p.last_active));
    else sum.appendChild(el('span', 'when', 'never seen'));
    row.appendChild(sum);
    row.appendChild(el('div', 'meta', p.email + ' · ' + plural(p.knives, 'knife').replace('knifes', 'knives')));
    if (p.trail.length) row.appendChild(list(p.trail, function (t) { return t.what; }));
    else row.appendChild(el('div', 'meta', 'nothing in this window'));
    return row;
  }

  function visitor(v) {
    var row = el('details', 'arow' + (v.you ? ' you' : ''));
    var sum = el('summary');
    var name = v.you ? 'you' : v.guess.length ? 'probably @' + v.guess.join(' or @') : 'a visitor';
    sum.appendChild(el('span', 'name', name));
    sum.appendChild(el('span', 'badge', v.device));
    if (v.came_from) sum.appendChild(el('span', 'badge ok', 'from ' + v.came_from));
    sum.appendChild(when(v.last));
    row.appendChild(sum);
    row.appendChild(el('div', 'meta', v.network + ' · ' + plural(v.pages.length, 'page') + ' · ' + plural(v.requests, 'request')));
    row.appendChild(list(v.pages, function (p) { return p.path + (p.status >= 400 ? ' (' + p.status + ')' : ''); }));
    return row;
  }

  function warn(id, src) {
    if (!src || src.ok) return;
    var box = $(id);
    box.insertBefore(el('div', 'srcerr', src.error || 'a source could not be read'), box.firstChild);
  }

  function draw(j) {
    fill('help', (j.needs_help || []).map(signal), 'nobody is stuck');
    fill('people', (j.people || []).map(person), 'no accounts');
    var seen = (j.visitors || []).slice().sort(function (a, b) { return (a.you ? 1 : 0) - (b.you ? 1 : 0); });
    var box = fill('visitors', seen.map(visitor), 'nobody came by');
    var hid = j.hidden || {};
    if (hid.bots) box.appendChild(el('div', 'note', plural(hid.bots, 'bot') + ' and scanners hidden (' + plural(hid.requests, 'request') + ')'));
    var src = j.sources || {};
    warn('help', src.app_log); warn('people', src.app_log); warn('visitors', src.access_log);
    if (src.access_log && src.access_log.stopped) box.appendChild(el('div', 'note', 'the log is long: only the newest part was read'));
  }

  function fail(text) {
    ['help', 'people', 'visitors'].forEach(function (id) { fill(id, [], text); });
  }

  function load(hours) {
    Array.prototype.forEach.call($('window').querySelectorAll('button'), function (b) {
      b.setAttribute('aria-pressed', b.getAttribute('data-hours') === String(hours) ? 'true' : 'false');
    });
    fail('loading…');
    fetch(URL + '?hours=' + hours, { credentials: 'same-origin' }).then(function (r) {
      if (r.status === 401 || r.status === 403) { fail(''); return null; }
      return r.json().then(function (j) {
        if (!r.ok) { fail(j.error || 'could not load activity (' + r.status + ')'); return; }
        draw(j);
      });
    }).catch(function (e) { fail('could not reach the server: ' + e.message); });
  }

  $('window').addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('button[data-hours]') : null;
    if (b) load(parseInt(b.getAttribute('data-hours'), 10));
  });
  load(48);
})();
```

- [ ] **Step 5: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_deploy_files.py -q`
Expected: all pass, `test_admin_page_wiring` included.

- [ ] **Step 6: Commit.**

```bash
git add html/admin/index.html html/admin/activity.js tests/test_deploy_files.py
git commit -m "activity: the admin page draws who is stuck, who did what, who came by"
```

---

### Task 8: The trust line

**Files:**
- Modify: `html/terms/index.html:60`, `html/faq/index.html:143`
- Test: `tests/test_deploy_files.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_deploy_files.py`:

```python
def test_terms_and_faq_say_we_keep_logs():
    line = ('Like every web server, ours keeps a log of requests (address, page, time) for two weeks, and the app '
            'keeps a log of sign-ins and saves. We read them to fix problems and to help people who get stuck.')
    for page in ('html/terms/index.html', 'html/faq/index.html'):
        html = _read(page)
        assert line in html, page
        assert 'Nothing else' not in html, page
        assert 'No ads, no trackers, no analytics scripts.' in html, page
```

- [ ] **Step 2: Run it and see it fail.**

Run: `python3 -m pytest tests/test_deploy_files.py::test_terms_and_faq_say_we_keep_logs -q`
Expected: FAIL on `html/terms/index.html`.

- [ ] **Step 3: Implement.** In `html/terms/index.html`, replace

```html
<p>Your email address, your handle, the knife records and photos you add, the notes you write, and a sign-in cookie. Nothing else. No ads, no trackers, no analytics scripts.</p>
```

with

```html
<p>Your email address, your handle, the knife records and photos you add, the notes you write, and a sign-in cookie. Like every web server, ours keeps a log of requests (address, page, time) for two weeks, and the app keeps a log of sign-ins and saves. We read them to fix problems and to help people who get stuck. No ads, no trackers, no analytics scripts.</p>
```

In `html/faq/index.html`, replace

```html
  <p>Your email address, your handle, the records and photos you add, the notes you write, and a sign-in cookie. No ads, no trackers, no analytics scripts.</p>
```

with

```html
  <p>Your email address, your handle, the records and photos you add, the notes you write, and a sign-in cookie. Like every web server, ours keeps a log of requests (address, page, time) for two weeks, and the app keeps a log of sign-ins and saves. We read them to fix problems and to help people who get stuck. No ads, no trackers, no analytics scripts.</p>
```

- [ ] **Step 4: Run the tests and see them pass.**

Run: `python3 -m pytest tests/test_deploy_files.py -q`
Expected: all pass. `test_terms_page_wiring` holds the terms page under 900 words; it still passes.

- [ ] **Step 5: Commit.**

```bash
git add html/terms/index.html html/faq/index.html tests/test_deploy_files.py
git commit -m "terms + faq: say plainly that we keep logs and why"
```

---

### Task 9: Going live

**Files:**
- Create: `scripts/deploy_activity.sh`
- Test: `tests/test_deploy_files.py`

- [ ] **Step 1: Write the failing test.** Append to `tests/test_deploy_files.py`:

```python
def test_deploy_activity_script():
    path = os.path.join(ROOT, 'scripts', 'deploy_activity.sh')
    sh = open(path).read()
    assert subprocess.run(['bash', '-n', path]).returncode == 0
    for needle in ('set -euo pipefail', 'supervisorctl restart blade_book', '/blade-book/api/healthz',
                   '/blade-book/api/admin/activity', 'def activity_summary', 'blade-book_access.log',
                   'sudo -u shg -H cp -r', 'Like every web server', 'STOP:'):
        assert needle in sh, needle
    assert 'publish_sweep' not in sh          # publish.py did not change
```

- [ ] **Step 2: Run it and see it fail.**

Run: `python3 -m pytest tests/test_deploy_files.py::test_deploy_activity_script -q`
Expected: FAIL, `FileNotFoundError`.

- [ ] **Step 3: Implement.** Create `scripts/deploy_activity.sh`:

```bash
#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-09-27: admin activity — the admin page shows who is stuck, who did what
# and who came by; the terms and the FAQ say that we keep logs. Static pages and
# the app go live together. Safe to run again: every step repeats cleanly.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_activity.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
LOG=/var/log/apache2/blade-book_access.log
if ! grep -q 'def activity_summary' "$CODE/bb/routes/admin.py"; then
  echo "STOP: $CODE does not have the activity route. Merge the admin-activity branch into main first." >&2
  exit 1
fi
if ! sudo -u shg -H test -r "$LOG"; then
  echo "STOP: shg cannot read $LOG. The page would show no visitors. Add shg to group adm, then run this again." >&2
  exit 1
fi
# static pages first: the page must never ask for a script that is not there yet
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "1/2 static pages copied"
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Static pages are new; the activity sections will say they could not load. Fix the app, then run this again." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Check /var/log/blade-book/app.log, then run this again." >&2
  exit 1
fi
echo "2/2 app restarted: $up"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5004/blade-book/api/admin/activity || true)
if [ "$code" = "401" ]; then echo "ok   the activity route answers, and refuses a signed-out caller"; else echo "MISSING the activity route (got $code, wanted 401)"; fi
for page in terms faq; do
  if curl -s --max-time 20 "https://blade-book.com/blade-book/$page/?fresh=$(date +%s)" | grep -q 'Like every web server'; then
    echo "ok   $page says we keep logs"
  else
    echo "MISSING the new sentence on $page"
  fi
done
echo 'Open https://blade-book.com/blade-book/admin/ — browsers and Cloudflare keep old pages for up to 4 h; reload hard if the sections are missing.'
```

Then: `chmod +x scripts/deploy_activity.sh`

- [ ] **Step 4: Run the whole suite.**

Run: `python3 -m pytest -q`
Expected: every test passes (719 before this plan, plus the new ones).

- [ ] **Step 5: Commit.**

```bash
git add scripts/deploy_activity.sh tests/test_deploy_files.py
git commit -m "activity: the deploy script"
```

- [ ] **Step 6: Look at it.** Run the app from the worktree against copies, never the live files:

```bash
S=<scratch dir>; mkdir -p $S/data $S/log $S/www $S/config
sqlite3 /var/lib/blade-book/blade-book.db ".backup $S/data/blade-book.db"
cp /var/log/blade-book/app.log $S/log/app.log
cp /var/log/apache2/blade-book_access.log $S/log/access.log
cp /var/log/apache2/blade-book_access.log.1 $S/log/access.log.1
cp -r html/. $S/www/
export BLADEBOOK_DATA_DIR=$S/data BLADEBOOK_LOG_DIR=$S/log BLADEBOOK_WWW_DIR=$S/www BLADEBOOK_CONFIG_DIR=$S/config \
       BLADEBOOK_ACCESS_LOG=$S/log/access.log BLADEBOOK_PORT=5099
```

Call `activity.summary` on the copies and read the result: a collector's visit at 2026-09-28 04:01 UTC is there with the guess `riverstone`; an invited collector's unclicked link is there; draft K06 is there with its purge date. Then serve the page with the summary and take screenshots at 1280 px and 390 px wide (memory: `reference_headless_screenshots`).
