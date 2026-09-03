#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""ManualBilling from a shell (spec §10):

    python3 scripts/sub.py <handle> active|free|lapsed
    python3 scripts/sub.py --list

Run as shg from /home/shg/blade-book (the live checkout; paths come from
bb/paths.py). The admin page does the same thing with buttons."""
import sys

sys.path.insert(0, __file__.rsplit('/scripts/', 1)[0])

from bb import billing, config, db  # noqa: E402

USAGE = 'usage: sub.py <handle> ' + '|'.join(db.SUB_STATUSES) + '   or   sub.py --list'


def main(argv):
    config.load()
    if argv == ['--list']:
        con = db.connect()
        try:
            for r in db.admin_users(con):
                print(f"@{r['handle']:<24} {r['sub_status']:<7} free_old_used={r['free_old_used']} "
                      f"knives={r['knives']} created={str(r['created'])[:10]}"
                      f"{'  admin' if r['is_admin'] else ''}")
        finally:
            con.close()
        return 0
    if len(argv) != 2:
        print(USAGE, file=sys.stderr)
        return 2
    handle, status = argv[0].lstrip('@'), argv[1]
    if status not in db.SUB_STATUSES:
        print(f'status must be one of {", ".join(db.SUB_STATUSES)}', file=sys.stderr)
        return 2
    con = db.connect()
    try:
        u = db.get_user_by_handle(con, handle)
        if u is None:
            print(f'no such handle: @{handle}', file=sys.stderr)
            return 2
        before = u['sub_status']
        billing.from_env().set_status(con, u['id'], status)
    finally:
        con.close()
    print(f'@{handle}: {before} → {status}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
