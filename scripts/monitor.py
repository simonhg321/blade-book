#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""blade-book monitor — cron */5. Emails Simon (bb.mail) about ERROR lines,
decode failure rate, backup age and new sign-ins; texts him (billboard's
Twilio) only when the app is down or the disk is nearly full, and again when
it recovers. Pure check functions over inputs + a state file; main() wires
the real inputs. Never raises out of main(); exit 0 always.

  monitor.py            one pass (cron)
  monitor.py --dry-run  runs the checks, prints, saves nothing
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

from bb import config, logkeep, paths  # noqa: E402

log = logging.getLogger('blade-book.monitor')

Event = namedtuple('Event', 'key subject body sms')

HEALTHZ_URL = f'http://127.0.0.1:{paths.PORT}' + paths.API_PREFIX + '/healthz'
BACKUP_GLOB = '/home/backup/blade-book-*.tgz'
DISK_MIN_PCT = 10
BACKUP_MAX_H = 26
LOG_MOVE_MAX_H = 48           # scripts/rotate_log.py runs nightly; two nights missed is a stop
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
    except (ValueError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        # OSError covers FileNotFoundError/PermissionError; any of these →
        # defaults, never a crash. A permission or corruption problem is
        # still visible in the run's log via the caller.
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


def check_log_limit(log_path, state, now):
    """The terms promise the app log keeps 90 days. Says so when the nightly
    run (scripts/rotate_log.py) has stopped: the live log holds a line older
    than two nights, or a day file has outlived the promise."""
    subject = body = None
    late = [p for day, p in logkeep.day_files(log_path)
            if day < now.astimezone().date() - timedelta(days=logkeep.KEEP_DAYS)]
    try:
        first = logkeep.first_stamp(log_path)
    except FileNotFoundError:
        first = None
    if late:
        subject = f'{len(late)} app log day file(s) older than {logkeep.KEEP_DAYS} days'
        body = '\n'.join(late) + '\nscripts/rotate_log.py should have deleted them; see rotate.log'
    elif first and now - first.astimezone(timezone.utc) > timedelta(hours=LOG_MOVE_MAX_H):
        hours = int((now - first.astimezone(timezone.utc)).total_seconds() // 3600)
        subject = f'app.log has not been moved for {hours} h'
        body = f'{log_path}\nscripts/rotate_log.py runs at 00:07 from the crontab of shg; see rotate.log'
    if not subject or not due(state, 'log_limit', 24, now):
        return []
    stamp(state, 'log_limit', now)
    return [Event('log_limit', subject, body, False)]


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


_ENV_GUARD_KEYS = ('ANTHROPIC_API_KEY', 'RESEND_API_KEY', 'MAIL_FROM', 'SESSION_KEY', 'BASE_URL')


def _billboard_sender():
    """billboard's Twilio helper (same box, Simon's phone). Imported lazily so
    tests and a box without billboard still load this module.

    billboard's sms_alerter runs `load_dotenv(..., override=True)` at import
    time against /etc/billboard/.env — and both .env files define
    ANTHROPIC_API_KEY and RESEND_API_KEY. Without a guard, importing this
    module clobbers blade-book's own keys with billboard's for the rest of
    the process. Snapshot the shared names first and restore them after."""
    os.environ.setdefault('BB_ENV_FILE', '/etc/billboard/.env')
    if BILLBOARD_DIR not in sys.path:
        sys.path.insert(0, BILLBOARD_DIR)
    saved = {k: os.environ.get(k) for k in _ENV_GUARD_KEYS}
    try:
        from sms_alerter import _send_twilio_sms  # noqa: PLC0415
        return _send_twilio_sms
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def send_sms(body, sender=None):
    """Best-effort second channel; email is the record. Never raises.
    Reports what the sender actually returned — billboard's
    _send_twilio_sms returns False (no raise) when creds are missing or
    Twilio refuses, and that must not read as success."""
    try:
        sender = sender or _billboard_sender()
        return bool(sender(os.environ.get('BB_ADMIN_PHONE', DEFAULT_PHONE), body))
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


def gather(now, fetch=None, con=None, backup_glob=BACKUP_GLOB, save=True):
    """Run every check with real inputs (each overridable), persist state
    unless save=False (--dry-run: look, don't touch)."""
    from bb import db
    sp = state_path()
    state = load_state(sp)
    events = []
    steps = [
        ('health', lambda: check_health(fetch or _fetch_healthz, state, now)),
        ('errors', lambda: check_errors(os.path.join(paths.LOG_DIR, 'app.log'), state, now)),
        ('decode', lambda: check_decode(paths.ai_log(), state, now)),
        ('backup', lambda: check_backup(backup_glob, state, now)),
        ('log_limit', lambda: check_log_limit(os.path.join(paths.LOG_DIR, 'app.log'), state, now)),
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
            events += check_signups(_new_users(c, state['last_users_check']), state, now)
        finally:
            if own:
                c.close()
    except Exception as e:  # noqa: BLE001
        log.exception('check signups failed: %r', e)
    if save:
        try:
            save_state(sp, state)
        except OSError as e:
            log.error('could not save state %s: %r', sp, e)
    return events


def _run(args):
    config.load()
    now = datetime.now(timezone.utc)
    gather_ok = True
    try:
        events = gather(now, save=not args.dry_run)
    except Exception as e:  # noqa: BLE001
        log.exception('gather failed: %r', e)
        events = []
        gather_ok = False
    prefix = f'{now.isoformat()} gather FAILED — ' if not gather_ok else f'{now.isoformat()} '
    if args.dry_run:
        print(f'{prefix}dry-run: {len(events)} event(s)')
        for ev in events:
            print(f'  [{ev.key}] {ev.subject} sms={ev.sms}')
        return 0
    admin = config.get('BLADEBOOK_ADMIN_EMAIL')
    if not admin and events:
        log.warning('BLADEBOOK_ADMIN_EMAIL unset — %d event(s) not mailed', len(events))
    from bb import mail
    counts = deliver(events, mail.from_env(), admin)
    print(f'{prefix}events={len(events)} {counts} ' + ' '.join(e.key for e in events))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='runs the checks, prints, saves nothing')
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
    try:
        return _run(args)
    except Exception as e:  # noqa: BLE001 — cron noise is the enemy; the log is the record
        log.exception('monitor run failed: %r', e)
        return 0


if __name__ == '__main__':
    sys.exit(main())
