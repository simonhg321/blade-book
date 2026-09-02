#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""*/15 cron: run one wants-matching pass. Safe to run concurrently with the
app — claims are UNIQUE-constrained, sends are idempotent per pair."""
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
