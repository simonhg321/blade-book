#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Keep 90 days of the app log (bb/logkeep.py): file yesterday's lines under
their day, delete the day files that have turned 90 days old.
Cron: 00:07 daily, shg's crontab (installed by scripts/deploy_log_limit.sh,
AFTER the app restarts onto the handler that follows the move).
Never as root: the files it makes must stay shg's, or the app cannot write."""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bb import paths  # noqa: E402
from bb.logkeep import KEEP_DAYS, rotate  # noqa: E402,F401


def main():
    if os.geteuid() == 0:
        print('rotate_log: refusing to run as root; run as shg', file=sys.stderr)
        return 2
    now = datetime.now()
    out = rotate(os.path.join(paths.LOG_DIR, 'app.log'), now)
    print(f'{now.isoformat(timespec="seconds")} moved {out["moved"]} line(s), '
          f'deleted {len(out["deleted"])} day file(s) {" ".join(out["deleted"])}'.rstrip())
    return 0


if __name__ == '__main__':
    sys.exit(main())
