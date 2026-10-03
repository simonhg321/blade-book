#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""One-off importer: Simon's crkinv collection → blade-book register.

  import_crkinv.py --handle simon-collector            dry run (prints the plan)
  import_crkinv.py --handle simon-collector --write    actually import

Reads /var/lib/billboard/crk/crk.db + originals/<TAG>/. Creates LIVE knives on
the target account with fresh blade-book tags; the crkinv tag is recorded as a
provenance line in notes_private. Photos go through bb.photos.ingest (same
validation/thumbs as the app). Hero photo → slot 1. Skips nothing silently:
every skipped knife/photo is printed. Idempotent-ish: a knife whose provenance
line already exists on the account is skipped.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3  # noqa: E402

from bb import config, db, photos, store as store_mod  # noqa: E402

CRK_DB = '/var/lib/billboard/crk/crk.db'
CRK_ORIGINALS = '/var/lib/billboard/crk/originals'

# crkinv column → blade-book CORE column (same name unless noted)
CORE_MAP = {
    'steel': 'blade_steel',
    'born_on': 'born_on', 'born_on_precision': 'born_on_precision',
    'born_on_source': 'born_on_source',
    'condition': 'condition',
    'has_box': 'has_box', 'has_card': 'has_card', 'has_papers': 'has_papers',
    'has_pouch': 'has_pouch', 'has_lanyard': 'has_lanyard',
    'has_spare_hardware': 'has_spare_hardware',
    'notes_public': 'notes_public',
    'price_paid': 'price_paid', 'acquired_from': 'acquired_from',
    'acquired_date': 'acquired_date', 'location': 'location',
    'condition_note': 'condition_note',
}
EXT_MAP = ('generation', 'size', 'crk_sku', 'hand', 'hardness_note',
           'handle_treatment', 'inlay_material', 'damascus_smith',
           'damascus_pattern', 'graphic_name', 'special_edition',
           'surface_finish', 'hardware_note', 'box_type')
SALE_OK = set(db.SALE_STATUSES)


def crk_rows():
    con = sqlite3.connect(CRK_DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        'SELECT k.*, m.family, m.model AS m_model, m.generation AS m_generation, '
        'm.size AS m_size, m.blade_shape AS m_blade_shape, m.knife_type, '
        'm.blade_length_mm FROM knives k JOIN models m ON m.id = k.model_id '
        'ORDER BY k.id').fetchall()
    con.close()
    return rows


def photo_files(tag, hero_name):
    d = os.path.join(CRK_ORIGINALS, tag)
    try:
        names = sorted(f for f in os.listdir(d)
                       if f.lower().endswith(('.jpg', '.jpeg', '.png', '.heic', '.webp')))
    except OSError:
        return []
    if hero_name in names:                      # hero first → slot 1
        names.remove(hero_name)
        names.insert(0, hero_name)
    return [os.path.join(d, n) for n in names[:3]]


def to_fields(r):
    """crkinv row → (core dict, ext dict, sale, notes_private)."""
    core = {}
    for src, dst in CORE_MAP.items():
        v = r[src]
        core[dst] = v if v not in ('',) else None
    core['model'] = r['m_model'] or r['family']
    core['blade_shape'] = r['m_blade_shape'] or None
    core['lock_type'] = ('fixed' if r['knife_type'] == 'fixed'
                         or r['family'] == 'Fixed Blade' else 'framelock')
    if r['blade_length_mm']:
        core['blade_length_in'] = round(r['blade_length_mm'] / 25.4, 2)
    ext = {}
    for f in EXT_MAP:
        v = r[f] if f not in ('generation', 'size') else (r[f'm_{f}'] or r[f] if f in r.keys() else r[f'm_{f}'])
        if v:
            ext[f] = v
    if r['m_generation']:
        ext['generation'] = r['m_generation']  # Regular and Classic are distinct pre-21 models
    if r['m_size']:
        ext['size'] = r['m_size']
    if r['inlay_note']:
        ext.setdefault('inlay_material', '')
        core['condition_note'] = core.get('condition_note')  # inlay_note folded below
    prov = f'[imported from crkinv {r["tag"]}]'
    notes_priv = '\n'.join(x for x in (r['notes_private'], r['inlay_note']
                                       and f'inlay: {r["inlay_note"]}',
                                       r['modifications']
                                       and f'mods: {r["modifications"]}', prov) if x)
    sale = r['sale_status'] if r['sale_status'] in SALE_OK else 'keeping'
    return core, ext, sale, notes_priv, prov


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--handle', required=True)
    ap.add_argument('--write', action='store_true')
    args = ap.parse_args()
    config.load()
    con = db.connect()
    user = db.get_user_by_handle(con, args.handle)
    if user is None:
        sys.exit(f'no such handle: {args.handle}')
    existing = {r['notes_private'] for r in con.execute(
        'SELECT notes_private FROM knives WHERE owner_id = ? AND notes_private IS NOT NULL',
        (user['id'],))}
    st = store_mod.from_paths()
    done = skipped = photo_fails = 0
    for r in crk_rows():
        core, ext, sale, notes_priv, prov = to_fields(r)
        if any(prov in (e or '') for e in existing):
            print(f'  skip {r["tag"]}: already imported')
            skipped += 1
            continue
        files = photo_files(r['tag'], r['hero_photo'] or '')
        name = ' '.join(x for x in (ext.get('size'), core['model'],
                                    ext.get('generation')) if x)
        line = (f'{r["tag"]} → {name:28s} born {core.get("born_on") or "?":10s} '
                f'{sale:9s} photos:{len(files)}')
        if not args.write:
            print('  plan', line)
            done += 1
            continue
        k = db.create_draft_knife(con, user['id'])
        fields = {c: v for c, v in core.items() if c in db.EDITABLE_COLUMNS and v is not None}
        fields['ext'] = ext
        fields['notes_private'] = notes_priv or None
        db.update_knife(con, user['id'], k['id'], fields)
        # arrive PRIVATE — Simon reviews and flips public from /me (2026-08-30)
        con.execute("UPDATE knives SET confidence = '{}', is_public = 0 WHERE id = ?", (k['id'],))
        con.commit()
        for seq, path in enumerate(files, start=1):
            with open(path, 'rb') as f:
                data = f.read()
            try:
                ing = photos.ingest(data, os.path.basename(path))
            except (photos.TooBig, photos.BadType, photos.Undecodable) as e:
                print(f'  photo skip {r["tag"]}/{os.path.basename(path)}: {e}')
                photo_fails += 1
                continue
            key = f'{user["id"]}/{k["id"]}/{seq}.{ing.ext}'
            db.add_photo(con, user['id'], k['id'], seq, key, ing.sha256,
                         ing.width, ing.height)
            st.put(key, data)
            if ing.thumb:
                st.put(db.thumb_key(key), ing.thumb)
        if files:
            db.update_knife(con, user['id'], k['id'], {'hero_photo': 1})
            # operator-only import: db.publish_knife is deliberately ungated (the gate is bb/routes/knives.py save_knife)
            k2, err = db.publish_knife(con, user['id'], k['id'])
            if err:
                print(f'  NOT LIVE {r["tag"]} ({k["tag"]}): {err}')
            elif sale != 'keeping':
                db.set_sale(con, user['id'], k['id'], sale,
                            asking_price=r['asking_price'])
        else:
            print(f'  DRAFT (no photos) {r["tag"]} → {k["tag"]}')
        print('  done', k['tag'], '←', line)
        done += 1
    con.close()
    mode = 'imported' if args.write else 'would import'
    print(f'{mode}: {done}, skipped: {skipped}, photo failures: {photo_fails}')
    if args.write:
        print('now run: python3 scripts/publish_sweep.py --user', args.handle)


if __name__ == '__main__':
    main()
