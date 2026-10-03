# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""The app log keeps 90 days, and the terms page says so.

Every night scripts/rotate_log.py moves app.log aside, files its lines under
the day they were written (app.log.2026-09-27) and deletes a day file on the
night its first line turns 90 days old. The app's handler (app.py) notices the
move and opens a fresh app.log. bb/activity.py reads the day files; scripts/
monitor.py says when the nightly run stops, and names any other copy of the
log that is older than 90 days, because this module only deletes files it
made itself."""
import fcntl
import os
import re
import stat
import time
from datetime import date, datetime, timedelta

KEEP_DAYS = 90
SETTLE_SECONDS = 2          # a worker that was mid-line when the file moved finishes into the old file
HELD = '.rotating'
LOCK = 'rotate.lock'
_DAY = re.compile(r'\.([0-9]{4})-([0-9]{2})-([0-9]{2})')
_STAMP = re.compile(rb'([0-9]{4})-([0-9]{2})-([0-9]{2}) [0-9]{2}:[0-9]{2}:[0-9]{2},[0-9]{3} ')


class Busy(Exception):
    """Another run holds the lock."""


def _date(m):
    try:
        return date(*(int(g) for g in m.groups()))
    except ValueError:                                  # 2026-13-45: not a day
        return None


def _plain(path):
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)     # not a folder, not a link
    except OSError:
        return False


def day_files(log_path):
    """(day, path) for every day file beside log_path, oldest first. Ours is a
    plain file named exactly app.log.YYYY-MM-DD; nothing else is touched."""
    folder, name = os.path.split(log_path)
    out = []
    for n in os.listdir(folder) if os.path.isdir(folder) else ():
        m = _DAY.fullmatch(n[len(name):]) if n.startswith(name) else None
        day = _date(m) if m else None
        if day and _plain(os.path.join(folder, n)):
            out.append((day, os.path.join(folder, n)))
    return sorted(out)


def strays(log_path):
    """Every other file beside log_path whose name starts like the log's: an
    old app.log.1, a hand copy, a .gz. The nightly run never deletes these."""
    folder, name = os.path.split(log_path)
    ours = {p for _day, p in day_files(log_path)} | {log_path, log_path + HELD}
    return sorted(os.path.join(folder, n) for n in (os.listdir(folder) if os.path.isdir(folder) else ())
                  if n.startswith(name) and os.path.join(folder, n) not in ours)


def first_stamp(path):
    """The day and time of the first record in a log file, as written (the
    box's local time), or None when no line carries a stamp."""
    with open(path, 'rb') as f:
        for line in f:
            if _STAMP.match(line):
                try:
                    return datetime.strptime(line[:19].decode('ascii'), '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    continue
    return None


def _records(path, today):
    """(day, lines) for each record in a log file, in order. A line with no
    stamp belongs to the record above it (app.py indents them); with no record
    above it, to today. A stamp later than today is filed under today: a day
    file dated in the future would never be deleted. A last line that was cut
    short gets its line end."""
    day, lines = today, []
    with open(path, 'rb') as f:
        for line in f:
            if not line.endswith(b'\n'):
                line += b'\n'
            m = _STAMP.match(line)
            d = _date(m) if m else None
            if d:
                if lines:
                    yield day, lines
                day, lines = min(d, today), []
            lines.append(line)
    if lines:
        yield day, lines


def _append(path, lines):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o640)
    with os.fdopen(fd, 'ab') as f:
        f.writelines(lines)


def _split(held, log_path, today, again=False):
    """File every record of `held` under its day, then remove `held`.
    again=True: `held` was left by a run that died, which may have filed some
    of it already; a record the day file holds is not filed twice."""
    moved, batch, batch_day, seen = 0, [], None, {}

    def flush():
        if batch:
            _append(f'{log_path}.{batch_day.isoformat()}', batch)
            batch.clear()

    for day, lines in _records(held, today):
        moved += len(lines)
        if again:
            target = f'{log_path}.{day.isoformat()}'
            if day not in seen:
                seen[day] = {b''.join(ls) for _d, ls in _records(target, today)} if _plain(target) else set()
            if b''.join(lines) in seen[day]:
                continue
        if day != batch_day:
            flush()
            batch_day = day
        batch.extend(lines)
    flush()
    os.remove(held)
    return moved


def rotate(log_path, now, keep_days=KEEP_DAYS, settle=SETTLE_SECONDS):
    """One nightly run. `now` is the box's local time, as the log stamps it.
    After it, app.log and its day files hold no record stamped more than
    keep_days before today. Raises Busy when another run is at work."""
    today, held = now.date(), log_path + HELD
    folder = os.path.dirname(log_path)
    if not os.path.isdir(folder):
        return {'moved': 0, 'deleted': []}
    fd = os.open(os.path.join(folder, LOCK), os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o640)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Busy(os.path.join(folder, LOCK)) from None
        moved = 0
        if os.path.lexists(held):                       # a run that died half way
            moved += _split(held, log_path, today, again=True)
        if _plain(log_path) and os.path.getsize(log_path):
            os.rename(log_path, held)
            _append(log_path, [])                       # the fresh log, 0o640, before a worker makes it
            time.sleep(settle)
            moved += _split(held, log_path, today)
        deleted = []
        for day, path in day_files(log_path):
            if day <= today - timedelta(days=keep_days):
                os.remove(path)
                deleted.append(os.path.basename(path))
        return {'moved': moved, 'deleted': deleted}
    finally:
        os.close(fd)
