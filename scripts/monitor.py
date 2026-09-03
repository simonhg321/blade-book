#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""blade-book monitor — cron */5. Emails Simon (bb.mail) about ERROR lines,
decode failure rate, backup age and new sign-ins; texts him (billboard's
Twilio) only when the app is down or the disk is nearly full, and again when
it recovers. Pure check functions over inputs + a state file; main() wires
the real inputs. Never raises out of main(); exit 0 always.

  monitor.py            one pass (cron)
  monitor.py --dry-run  run the checks, print the events, send nothing
"""
import argparse
import glob
import json
import logging
import os
import sys
from collections import namedtuple
from datetime import datetime, timedelta, timezone

sys.path.insert(0, __file__.rsplit('/scripts/', 1)[0])

from bb import config, paths  # noqa: E402

log = logging.getLogger('blade-book.monitor')

Event = namedtuple('Event', 'key subject body sms')

HEALTHZ_URL = 'http://127.0.0.1:5004' + paths.API_PREFIX + '/healthz'
BACKUP_GLOB = '/home/backup/blade-book-*.tgz'
DISK_MIN_PCT = 10
BACKUP_MAX_H = 26
ERROR_LINES_MAX = 40
DECODE_MIN_CALLS = 5
DECODE_MAX_FAIL = 0.20
DEFAULT_STATE = {'log_offset': 0, 'log_inode': None, 'last_users_check': None,
                 'sent': {}, 'down': False, 'disk': False}


# --- state + throttle -------------------------------------------------------------

def state_path():
    return os.path.join(paths.DATA_DIR, 'monitor_state.json')


def load_state(path):
    try:
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError('state is not an object')
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        data = {}
    s = dict(DEFAULT_STATE)
    s['sent'] = {}
    s.update({k: v for k, v in data.items() if k in DEFAULT_STATE})
    if not isinstance(s['sent'], dict):
        s['sent'] = {}
    return s


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def due(state, key, hours, now):
    last = state['sent'].get(key)
    if not last:
        return True
    return now - datetime.fromisoformat(last) >= timedelta(hours=hours)


def stamp(state, key, now):
    state['sent'][key] = now.isoformat()


# --- checks (pure: inputs + state + now → events) ------------------------------------

def check_health(fetch, state, now):
    """fetch() → healthz dict or raises. Transitions only; outages text."""
    events = []
    try:
        h = fetch()
        up = bool(h.get('ok'))
        disk_pct = h.get('disk_free_pct')
    except Exception as e:  # noqa: BLE001 — a dead app is the finding, not a crash
        up, disk_pct, h = False, None, {'error': repr(e)}
    if not up and not state['down']:
        events.append(Event('down', 'DOWN — healthz failed', json.dumps(h), True))
    elif up and state['down']:
        events.append(Event('up', 'UP again — healthz ok', json.dumps(h), True))
    state['down'] = not up
    if disk_pct is not None:
        low = disk_pct < DISK_MIN_PCT
        if low and not state['disk']:
            events.append(Event('disk', f'DISK {disk_pct}% free on {paths.DATA_DIR}', json.dumps(h), True))
        elif not low and state['disk']:
            events.append(Event('disk_ok', f'disk ok again — {disk_pct}% free', json.dumps(h), True))
        state['disk'] = low
    return events


def check_errors(log_path, state, now):
    """New ` ERROR ` lines since the last offset. Offset always advances;
    the mail is throttled to one per hour, so a burst is reported once."""
    try:
        st = os.stat(log_path)
    except FileNotFoundError:
        return []
    if state['log_inode'] != st.st_ino or state['log_offset'] > st.st_size:
        state['log_offset'] = 0
        state['log_inode'] = st.st_ino
    with open(log_path, 'rb') as f:
        f.seek(state['log_offset'])
        chunk = f.read()
        state['log_offset'] = f.tell()
    lines = [ln for ln in chunk.decode('utf-8', 'replace').splitlines() if ' ERROR ' in ln]
    if not lines or not due(state, 'errors', 1, now):
        return []
    stamp(state, 'errors', now)
    shown = lines[:ERROR_LINES_MAX]
    body = '\n'.join(shown) + (f'\n… {len(lines) - len(shown)} more' if len(lines) > len(shown) else '')
    return [Event('errors', f'{len(lines)} ERROR lines in app.log', body, False)]


def check_decode(ai_log_path, state, now):
    """Decode failure rate over the last 24 h from ai_calls.jsonl."""
    try:
        with open(ai_log_path) as f:
            raw = f.read().splitlines()
    except FileNotFoundError:
        return []
    since = now - timedelta(hours=24)
    n = failed = 0
    for ln in raw:
        try:
            r = json.loads(ln)
            ts = datetime.fromisoformat(r['ts'])
        except (ValueError, KeyError, TypeError):
            continue
        if ts < since:
            continue
        n += 1
        failed += 0 if r.get('ok') else 1
    if n < DECODE_MIN_CALLS or failed / n <= DECODE_MAX_FAIL or not due(state, 'decode', 24, now):
        return []
    stamp(state, 'decode', now)
    return [Event('decode', f'decode failures {failed}/{n} in 24 h', f'{failed} of {n} decodes failed since {since.isoformat()}', False)]


def check_backup(backup_glob, state, now):
    files = glob.glob(backup_glob)
    if not files:
        subject, body = 'no backup file found', backup_glob
    else:
        newest = max(files, key=os.path.getmtime)
        age = now - datetime.fromtimestamp(os.path.getmtime(newest), timezone.utc)
        if age <= timedelta(hours=BACKUP_MAX_H):
            return []
        subject, body = f'backup is {int(age.total_seconds() // 3600)} h old', newest
    if not due(state, 'backup', 24, now):
        return []
    stamp(state, 'backup', now)
    return [Event('backup', subject, body, False)]


def check_signups(rows, state, now):
    """rows = (email, handle, created) for users created since last_users_check."""
    state['last_users_check'] = now.isoformat()
    if not rows:
        return []
    body = '\n'.join(f'@{h}  {e}  {c}' for e, h, c in rows)
    return [Event('signups', f'{len(rows)} new sign-in{"s" if len(rows) != 1 else ""}', body, False)]
