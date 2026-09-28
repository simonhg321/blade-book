# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
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
