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


# --- channels -------------------------------------------------------------------------

BILLBOARD_DIR = '/home/shg/billboard'
DEFAULT_PHONE = '5550002222'
PREFIX = '[blade-book] '


def _billboard_sender():
    """billboard's Twilio helper (same box, Simon's phone). Imported lazily so
    tests and a box without billboard still load this module."""
    os.environ.setdefault('BB_ENV_FILE', '/etc/billboard/.env')
    if BILLBOARD_DIR not in sys.path:
        sys.path.insert(0, BILLBOARD_DIR)
    from sms_alerter import _send_twilio_sms  # noqa: PLC0415
    return _send_twilio_sms


def send_sms(body, sender=None):
    """Best-effort second channel; email is the record. Never raises."""
    try:
        sender = sender or _billboard_sender()
        sender(os.environ.get('BB_ADMIN_PHONE', DEFAULT_PHONE), body)
        return True
    except Exception as e:  # noqa: BLE001
        log.error('sms failed: %r', e)
        return False


def deliver(events, mailer, admin_email, sms=send_sms):
    counts = {'mail': 0, 'sms': 0, 'skipped': 0}
    for ev in events:
        subject = PREFIX + ev.subject
        if admin_email:
            try:
                mailer.send(admin_email, subject, ev.body)
                counts['mail'] += 1
            except Exception as e:  # noqa: BLE001
                log.error('mail failed for %s: %r', ev.key, e)
                counts['skipped'] += 1
        else:
            counts['skipped'] += 1
        if ev.sms:
            try:
                if sms(subject):
                    counts['sms'] += 1
                else:
                    counts['skipped'] += 1
            except Exception as e:  # noqa: BLE001
                log.error('sms failed for %s: %r', ev.key, e)
                counts['skipped'] += 1
    return counts


# --- wiring ---------------------------------------------------------------------------

def _fetch_healthz():
    import urllib.request
    with urllib.request.urlopen(HEALTHZ_URL, timeout=3) as r:  # noqa: S310 — loopback only
        return json.loads(r.read().decode())


def _new_users(con, since_iso):
    rows = con.execute('SELECT email, handle, created FROM users WHERE created > ? ORDER BY created',
                       (since_iso or '1970-01-01',)).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def gather(now, fetch=None, con=None, backup_glob=BACKUP_GLOB):
    """Run every check with real inputs (each overridable), persist state."""
    from bb import db
    sp = state_path()
    state = load_state(sp)
    events = []
    steps = [
        ('health', lambda: check_health(fetch or _fetch_healthz, state, now)),
        ('errors', lambda: check_errors(os.path.join(paths.LOG_DIR, 'app.log'), state, now)),
        ('decode', lambda: check_decode(paths.ai_log(), state, now)),
        ('backup', lambda: check_backup(backup_glob, state, now)),
    ]
    for name, fn in steps:
        try:
            events += fn()
        except Exception as e:  # noqa: BLE001 — one broken check never hides the others
            log.exception('check %s failed: %r', name, e)
    try:
        own = con is None
        c = con or db.connect()
        try:
            # Watermark against the real clock, not the caller's `now`: db rows
            # are stamped by db.now() (real wall-clock) regardless of what `now`
            # this poll was handed for the other checks' throttle windows.
            events += check_signups(_new_users(c, state['last_users_check']), state, datetime.now(timezone.utc))
        finally:
            if own:
                c.close()
    except Exception as e:  # noqa: BLE001
        log.exception('check signups failed: %r', e)
    save_state(sp, state)
    return events


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    config.load()
    now = datetime.now(timezone.utc)
    try:
        events = gather(now)
    except Exception as e:  # noqa: BLE001
        log.exception('gather failed: %r', e)
        events = []
    if args.dry_run:
        print(f'{now.isoformat()} dry-run: {len(events)} event(s)')
        for ev in events:
            print(f'  [{ev.key}] {ev.subject} sms={ev.sms}')
        return 0
    admin = config.get('BLADEBOOK_ADMIN_EMAIL')
    if not admin and events:
        log.warning('BLADEBOOK_ADMIN_EMAIL unset — %d event(s) not mailed', len(events))
    from bb import mail
    counts = deliver(events, mail.from_env(), admin)
    print(f'{now.isoformat()} events={len(events)} {counts} ' + ' '.join(e.key for e in events))
    return 0


if __name__ == '__main__':
    sys.exit(main())
