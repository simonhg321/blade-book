# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""The app log keeps 90 days, and the terms page says so.

Every night scripts/rotate_log.py moves app.log aside, files its lines under
the day they were written (app.log.2026-09-27) and deletes the day files whose
first line has turned 90 days old. The app's handler (app.py) notices the move
and opens a fresh app.log. bb/activity.py reads the day files; scripts/
monitor.py says when the nightly run stops."""
import os
import re
import time
from datetime import date, datetime, timedelta

KEEP_DAYS = 90
SETTLE_SECONDS = 2          # a worker that was mid-line when the file moved finishes into the old file
HELD = '.rotating'
_DAY = re.compile(r'\.(\d{4})-(\d\d)-(\d\d)')
_STAMP = re.compile(rb'(\d{4})-(\d\d)-(\d\d) \d\d:\d\d:\d\d,\d{3} ')


def _date(m):
    try:
        return date(*(int(g) for g in m.groups()))
    except ValueError:                                  # 2026-13-45: not a day
        return None


def day_files(log_path):
    """(day, path) for every day file beside log_path, oldest first. A name
    that is not exactly app.log.YYYY-MM-DD is not ours."""
    folder, name = os.path.split(log_path)
    out = []
    for n in os.listdir(folder) if os.path.isdir(folder) else ():
        m = _DAY.fullmatch(n[len(name):]) if n.startswith(name) else None
        day = _date(m) if m else None
        if day:
            out.append((day, os.path.join(folder, n)))
    return sorted(out)


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


def _append(path, lines):
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
    with os.fdopen(fd, 'ab') as f:
        f.writelines(lines)


def _split(held, log_path, today):
    """File every line of `held` under its record's day, then remove `held`.
    A line with no stamp belongs to the record above it (app.py indents them);
    with no record above it, to today. If this dies half way, `held` stays and
    the next run files it again: a line may then be there twice, never lost."""
    moved, day, batch = 0, today, []
    with open(held, 'rb') as f:
        for line in f:
            m = _STAMP.match(line)
            d = _date(m) if m else None
            if d and d != day:
                if batch:
                    _append(f'{log_path}.{day.isoformat()}', batch)
                day, batch = d, []
            batch.append(line)
            moved += 1
    if batch:
        _append(f'{log_path}.{day.isoformat()}', batch)
    os.remove(held)
    return moved


def rotate(log_path, now, keep_days=KEEP_DAYS, settle=SETTLE_SECONDS):
    """One nightly run. `now` is the box's local time, as the log stamps it.
    After it, no line in the app log or its day files is older than keep_days."""
    today, held, moved = now.date(), log_path + HELD, 0
    if os.path.exists(held):                            # a run that died half way
        moved += _split(held, log_path, today)
    if os.path.exists(log_path) and os.path.getsize(log_path):
        os.rename(log_path, held)
        _append(log_path, [])                           # the fresh log, 0o640, before a worker makes it
        time.sleep(settle)
        moved += _split(held, log_path, today)
    deleted = []
    for day, path in day_files(log_path):
        if day <= today - timedelta(days=keep_days):
            os.remove(path)
            deleted.append(os.path.basename(path))
    return {'moved': moved, 'deleted': deleted}
