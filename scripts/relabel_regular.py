#!/usr/bin/env python3
"""One-off (2026-09-16): flip pre-21 Sebenzas that were folded into "Classic" back to
"Regular". Regular (1996-2008) and Classic (2000-2008) are distinct models; the old
importer + normalizer collapsed them. Tags verified against crkinv by born-on date.

  relabel_regular.py            dry run
  relabel_regular.py --write    apply + mark @simon-collector dirty (then run publish_sweep)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from bb import db  # noqa: E402

HANDLE = 'simon-collector'
TAGS = ('K09', 'K33', 'K34', 'K82')


def main():
    write = '--write' in sys.argv
    con = db.connect()
    u = con.execute('SELECT id FROM users WHERE handle = ?', (HANDLE,)).fetchone()
    if not u:
        sys.exit(f'no user {HANDLE}')
    rows = con.execute('SELECT id, tag, ext, born_on FROM knives WHERE owner_id = ? AND tag IN (?,?,?,?)',
                       (u['id'], *TAGS)).fetchall()
    for r in rows:
        ext = json.loads(r['ext'] or '{}')
        print(f"{r['tag']} born {r['born_on']}: {ext.get('generation')!r} -> 'Regular'")
        if write and ext.get('generation') != 'Regular':
            ext['generation'] = 'Regular'
            db.update_knife(con, u['id'], r['id'], {'ext': ext})
    if write:
        db.mark_publish_dirty(con, u['id'])
        print('written + marked dirty; now: scripts/publish_sweep.py --user', HANDLE)
    else:
        print('dry run (add --write)')


if __name__ == '__main__':
    main()
