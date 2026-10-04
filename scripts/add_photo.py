#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Add a photo to a knife from a shell — the same path as the upload route
(ingest → row → store → thumb), for a file that never went through the phone:

    python3 scripts/add_photo.py <handle> <tag> <seq 1-3> <file> [--hero] [--replace]

--hero also makes it the knife's hero_photo. The owner is stamped publish-dirty
so the */5 sweep rebuilds the public page (or run publish_sweep.py yourself).
Run as shg from /home/shg/blade-book (the live checkout)."""
import argparse
import sys

sys.path.insert(0, __file__.rsplit('/scripts/', 1)[0])

from bb import config, db, photos  # noqa: E402
from bb import store as store_mod  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('handle')
    ap.add_argument('tag')
    ap.add_argument('seq', type=int, choices=range(1, db.MAX_PHOTO_SLOTS + 1))
    ap.add_argument('file')
    ap.add_argument('--hero', action='store_true')
    ap.add_argument('--replace', action='store_true')
    a = ap.parse_args(argv)
    config.load()
    with open(a.file, 'rb') as f:
        data = f.read()
    try:
        ing = photos.ingest(data, a.file)
    except (photos.TooBig, photos.BadType, photos.Undecodable) as e:
        sys.exit(f'{a.file}: {e}')
    store = store_mod.from_paths()
    con = db.connect()
    try:
        user = db.get_user_by_handle(con, a.handle.lstrip('@'))
        if user is None:
            sys.exit(f'no such handle: {a.handle}')
        row = con.execute('SELECT id FROM knives WHERE owner_id = ? AND tag = ?',
                          (user['id'], a.tag)).fetchone()
        if row is None:
            sys.exit(f'@{a.handle} has no knife {a.tag}')
        knife_id = row['id']
        key = f'{user["id"]}/{knife_id}/{a.seq}.{ing.ext}'
        tkey = db.thumb_key(key)
        if a.replace:
            old = db.delete_photo(con, user['id'], knife_id, a.seq)
            if old:
                for k in (old['store_key'], db.thumb_key(old['store_key'])):
                    try:
                        store.delete(k)
                    except Exception as e:  # noqa: BLE001
                        print(f'  (old file {k} not removed: {e})')
        try:
            db.add_photo(con, user['id'], knife_id, a.seq, key, ing.sha256, ing.width, ing.height)
        except db.SlotTaken:
            sys.exit(f'{a.tag} slot {a.seq} is taken — use --replace')
        try:
            store.put(key, data)
            if ing.thumb:
                store.put(tkey, ing.thumb)
        except Exception:
            db.delete_photo(con, user['id'], knife_id, a.seq)
            raise
        if a.hero:
            db.update_knife(con, user['id'], knife_id, {'hero_photo': a.seq})
        db.mark_publish_dirty(con, user['id'])
    finally:
        con.close()
    print(f'@{a.handle} {a.tag} slot {a.seq}: {key} {ing.width}x{ing.height} sha {ing.sha256[:12]}'
          f'{" hero" if a.hero else ""} — publish-dirty, sweep will rebuild')


if __name__ == '__main__':
    main()
