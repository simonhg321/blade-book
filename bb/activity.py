# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/activity.py — who is using blade-book and where they are stuck, for the
admin page (spec 2026-09-27-admin-activity-design.md).

Reads the database, the app log and the web server's request log. Writes
nothing. No raw log line ever leaves: the parsers match known shapes and
return named fields, and an unknown line is dropped."""
import functools
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
_EXT = re.compile(r'^\.[a-z][a-z0-9]{1,7}$')      # .heic, .tiff, .mp4 — a letter first, so '.66' is not one


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


# --- the web server log ---------------------------------------------------------

_ACCESS = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<ts>[^\]]+)\] "(?P<method>[A-Z]+) (?P<target>\S+)[^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<ref>(?:[^"\\]|\\.)*)" "(?P<ua>(?:[^"\\]|\\.)*)"')
_BOT = re.compile(r'bot|crawl|spider|slurp|curl|wget|python|go-http|scrapy|headless|monitor|uptime|scan|'
                  r'libwww|okhttp|java/|node|axios|httpclient|^https?://|^-?$', re.I)
_ASSETS = ('.css', '.js', '.woff2', '.woff', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.ico')
_PROBE = re.compile(r'/\.(?:git|env|aws|ssh|svn|hg)\b|/wp-|\.php\b|/cgi-bin/|/vendor/|/phpmyadmin', re.I)
PATH_MAX = 200


@functools.lru_cache(maxsize=4096)
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


def is_probe(path):
    """A path nobody reaches by following our links: a scanner's guess."""
    return bool(_PROBE.search(path))


def is_asset(path):
    return path.lower().endswith(_ASSETS)


def is_page(path):
    if path.startswith(paths.API_PREFIX + '/'):
        return False
    last = path.rsplit('/', 1)[-1]
    return last == '' or last.endswith('.html') or '.' not in last


_MONTHS = {m: n for n, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split(), 1)}
_STAMP = re.compile(r'^(\d\d)/([A-Z][a-z]{2})/(\d{4}):(\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)$')


def _stamp(text):
    """'28/Sep/2026:04:01:34 +0000' as UTC. By hand: strptime was a third of
    the whole read. ValueError for anything else."""
    m = _STAMP.match(text)
    if not m or m.group(2) not in _MONTHS:
        raise ValueError(text)
    d, mon, y, H, M, S, sign, oh, om = m.groups()
    off = timedelta(hours=int(oh), minutes=int(om))
    when = datetime(int(y), _MONTHS[mon], int(d), int(H), int(M), int(S), tzinfo=timezone.utc)
    return when - off if sign == '+' else when + off


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
        when = _stamp(m.group('ts'))
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
    """People who came by, newest first, and a count of what was hidden: bots by
    their browser string, and groups that never behaved like a browser or that
    went looking for things we do not have."""
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
        if not browser or not any(p['status'] < 400 for p in pages) or any(is_probe(h['path']) for h in hs):
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
