#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Delete draft knives untouched for 7 days, and their files (spec §7).
Cron: 04:15 daily (installed by scripts/install.sh into shg's crontab)."""
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bb import config, db, store  # noqa: E402

log = logging.getLogger('blade-book.purge')

STALE_PART_AGE_SECONDS = 60 * 60  # 1 hour

# purged() reports the row/file count; a per-key delete failure never stops the
# rest of the sweep, but must not be silently lost either — this is the tally
# for the caller (and __main__'s exit code) to notice.
last_failed = 0


def _sweep_stale_parts(root, max_age_seconds=STALE_PART_AGE_SECONDS):
    """A '*.part' file is a LocalFSStore.put() temp file whose os.replace()
    never happened (worker died mid-write). Anything younger than max_age is
    presumably still being written; only unlink the old ones."""
    now = time.time()
    swept = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith('.part'):
                continue
            p = os.path.join(dirpath, name)
            try:
                if now - os.path.getmtime(p) > max_age_seconds:
                    os.unlink(p)
                    swept += 1
            except OSError:
                log.exception('could not sweep stale part file %s', p)
    return swept


def run(days=7):
    global last_failed
    config.load()
    s = store.from_paths()
    con = db.connect()
    try:
        purged = db.purge_stale_drafts(con, days=days)
    finally:
        con.close()
    failed = 0
    for k in purged:
        missing = []
        for key in k['keys']:
            try:
                if not s.delete(key):
                    missing.append(key)
            except Exception:  # noqa: BLE001 — logged, never lets one bad key stop the sweep
                failed += 1
                log.exception('purged draft %s (owner %s, knife %s): could not delete %s',
                              k['tag'], k['owner_id'], k['id'], key)
        log.info('purged draft %s (owner %s, knife %s): %d files%s', k['tag'], k['owner_id'],
                 k['id'], len(k['keys']) - len(missing),
                 f', {len(missing)} already missing' if missing else '')
        # Best-effort: the knife's own dir (and now-empty parents, e.g. the
        # owner dir once their last draft is gone) — never raises.
        try:
            os.removedirs(os.path.join(s.root, str(k['owner_id']), str(k['id'])))
        except OSError:
            pass
    swept = _sweep_stale_parts(s.root)
    if swept:
        log.info('swept %d stale .part file(s)', swept)
    last_failed = failed
    return len(purged)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    n = run()
    print(f'purged {n} stale draft(s)' + (f', {last_failed} failed delete(s)' if last_failed else ''))
    sys.exit(1 if last_failed else 0)
