#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Delete draft knives untouched for 7 days, and their files (spec §7).
Cron: 04:15 daily (installed by scripts/install.sh into shg's crontab)."""
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bb import config, db, store  # noqa: E402

log = logging.getLogger('blade-book.purge')


def run(days=7):
    config.load()
    s = store.from_paths()
    con = db.connect()
    try:
        purged = db.purge_stale_drafts(con, days=days)
    finally:
        con.close()
    for k in purged:
        missing = [key for key in k['keys'] if not s.delete(key)]
        log.info('purged draft %s (owner %s, knife %s): %d files%s', k['tag'], k['owner_id'],
                 k['id'], len(k['keys']) - len(missing),
                 f', {len(missing)} already missing' if missing else '')
    return len(purged)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    n = run()
    print(f'purged {n} stale draft(s)')
