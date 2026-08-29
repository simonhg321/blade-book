#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Cron backstop for the in-process publish debounce (restarts eat timers),
and the manual rebuild tool.

  publish_sweep.py            build every dirty owner quiet ≥30 s (cron */5)
  publish_sweep.py --all      rebuild EVERY user's bundle (deploy/migration)
  publish_sweep.py --user H   rebuild @H now
"""
import argparse
import sys

sys.path.insert(0, __file__.rsplit('/scripts/', 1)[0])

from bb import config, db, publish  # noqa: E402


def main():
    config.load()
    ap = argparse.ArgumentParser()
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--user')
    args = ap.parse_args()
    con = db.connect()
    try:
        store = publish._store_factory()
        if args.user:
            user = db.get_user_by_handle(con, args.user)
            if user is None:
                sys.exit(f'no such handle: {args.user}')
            n = publish.build_user(con, user, store)
            print(f'@{args.user}: {n} knives' if n >= 0 else f'@{args.user}: page removed (private)')
            return
        if args.all:
            built = 0
            for r in con.execute('SELECT id FROM users'):
                user = db.get_user(con, r['id'])
                publish.build_user(con, user, store)
                built += 1
            print(f'rebuilt {built} bundles')
            return
    finally:
        con.close()
    print(f'built {publish.run_due()} dirty bundles')


if __name__ == '__main__':
    main()
