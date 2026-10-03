#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""*/15 cron: run one wants-matching pass. Safe to run concurrently with
itself or the app — bb/match.py's run() is flock-serialized, so an
overlapping invocation (this run outliving 15 minutes) skips rather than
double-sending."""
import sys

sys.path.insert(0, __file__.rsplit('/scripts/', 1)[0])

from bb import config, db, mail, match  # noqa: E402


def main():
    config.load()
    con = db.connect()
    try:
        n = match.run(con, mail.from_env())
        if n:
            print(f'sent {n} intro emails')
    finally:
        con.close()


if __name__ == '__main__':
    main()
