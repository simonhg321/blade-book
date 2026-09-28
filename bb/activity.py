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
