# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/account.py — the account-level operations behind /me/settings (plan 11):
the one-time handle change, the everything-ZIP export, and complete account
deletion. Pure functions over (con, store); the routes stay thin.

Deletion is complete or it is a bug (spec §11: "delete-account is real and
complete"): DB rows, store keys, the public bundle + its tmp + its lock, the
search index, and a tombstone. tests/test_account.py enumerates every surface."""
import csv
import fcntl
import html as html_mod
import io
import json
import logging
import os
import secrets
import shutil
import zipfile
from datetime import datetime, timezone

from bb import auth, cdn, db, publish, search

log = logging.getLogger('blade-book.account')

HANDLE_RULE = 'handles are 3–24 characters: letters, digits, hyphens'


# --- handle (editable once) ---------------------------------------------------------

def validate_new_handle(con, user, new):
    """The exact slug the user wants, or ValueError with the message the page shows."""
    if user.get('handle_changed_at'):
        raise ValueError('you already changed your handle')
    new = (new or '').strip()
    if new == user['handle']:
        raise ValueError('that is already your handle')
    if new in auth.RESERVED_HANDLES:
        raise ValueError('that handle is reserved')
    if (len(new) < auth.HANDLE_MIN or len(new) > auth.HANDLE_MAX
            or auth.slugify_handle(new) != new):
        raise ValueError(HANDLE_RULE)
    if db.handle_exists(con, new):
        raise ValueError('that handle is taken')
    return new


def remove_public_surface(handle):
    """Take a handle's static bundle off the web: the dir, a stranded .tmp,
    and the publish lock. Holds the same flock build_user holds for the whole
    build, so an in-flight build finishes (and is then removed) instead of
    re-creating the bundle after us. Returns True when nothing is left on
    disk; False (logged at ERROR) when something survived — the caller
    reports it, the account row is gone regardless."""
    dest = publish.bundle_dir(handle)
    lock_path = publish._lock_path(handle)
    lockf = open(lock_path, 'w')
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        gone = cdn.bundle_files(dest)              # what the edge may still hold (review L3)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(dest + '.tmp', ignore_errors=True)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    cdn.purge_later([cdn.public_url(handle, rel) for rel in gone])
    try:
        os.unlink(lock_path)
    except FileNotFoundError:
        pass
    except OSError as e:
        log.error('remove_public_surface: could not unlink %s: %r', lock_path, e)
    left = [p for p in (dest, dest + '.tmp') if os.path.exists(p)]
    if left:
        log.error('remove_public_surface: @%s still on disk after removal: %s', handle, left)
        return False
    return True


def change_handle(con, user, new):
    """Rename once. The old bundle comes down now and the search cards go with
    it (they carry the handle); the caller schedules the republish, which
    rebuilds + reindexes under the new handle."""
    new = validate_new_handle(con, user, new)
    old = user['handle']
    fresh = db.set_handle(con, user['id'], new)
    db.release_handle(con, old)
    remove_public_surface(old)
    search.deindex_user(con, user['id'])
    log.info('handle changed: @%s -> @%s (user %s)', old, new, user['id'])
    return fresh


# --- export ------------------------------------------------------------------------

# The knives table in DDL order minus id/owner_id, plus the derived columns:
# photo_count, then the three file references into the ZIP (a CSV cell cannot
# hold a picture, so the row points at photos/ and thumbs/ instead). ext and
# confidence are JSON text in their cells. Never derived from PRAGMA at run
# time: the header is a contract with whoever opens the CSV in a spreadsheet.
EXPORT_CSV_COLUMNS = (
    'tag', 'maker', 'status',
    'model', 'variant', 'blade_steel', 'blade_shape', 'blade_length_in', 'handle_material', 'lock_type',
    'born_on', 'born_on_precision', 'born_on_source', 'condition',
    'has_box', 'has_card', 'has_papers', 'has_pouch', 'has_lanyard', 'has_spare_hardware',
    'ext',
    'price_paid', 'acquired_from', 'acquired_date', 'location', 'notes_private', 'condition_note',
    'confidence', 'card_text', 'decode_note',
    'notes_public', 'is_public', 'sale_status', 'asking_price', 'seller_note', 'listed_at',
    'hidden_at', 'hidden_by', 'hidden_note', 'hero_photo', 'created', 'updated',
    'photo_count', 'hero_file', 'photo_files', 'thumb_files',
)
EXPORT_DERIVED_COLUMNS = ('photo_count', 'hero_file', 'photo_files', 'thumb_files')


def _csv_cell(v):
    if v is None:
        return ''
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return v


def _hero(k):
    """The photo dict the register treats as the knife's face: the pinned
    hero_photo seq, else the first slot. None without photos."""
    photos_ = k.get('photos') or []
    if not photos_:
        return None
    return next((p for p in photos_ if p['seq'] == k.get('hero_photo')), photos_[0])


def _zip_stored(z, name, data):
    z.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), data,
               compress_type=zipfile.ZIP_STORED)     # already-compressed JPEG bytes


# --- register.html: the owner's own paper page inside the ZIP -----------------------

_REGISTER_STYLE = '''
  body{font-family:system-ui,-apple-system,sans-serif;background:#f6f1e7;color:#1a1a1a;margin:0;padding:24px}
  main{max-width:960px;margin:0 auto}
  h1{font-size:1.8rem;margin:0 0 4px}.sub{color:#666;margin:0 0 20px;font-size:.9rem}
  .knife{display:grid;grid-template-columns:200px 1fr;gap:18px;background:#fff;border:2px solid #141210;
         border-radius:14px;padding:16px;margin-bottom:16px;break-inside:avoid}
  .knife img{width:200px;height:200px;object-fit:cover;border-radius:8px;background:#eee}
  .nophoto{width:200px;height:200px;border-radius:8px;background:#eee;display:flex;align-items:center;
           justify-content:center;color:#999;font-size:.85rem}
  .knife h2{margin:0 0 2px;font-size:1.25rem}.tag{color:#666;font-size:.85rem;margin:0 0 10px}
  dl{display:grid;grid-template-columns:max-content 1fr;gap:3px 14px;margin:0;font-size:.92rem}
  dt{color:#666}dd{margin:0}.notes{margin-top:10px;white-space:pre-wrap;font-size:.92rem}
  @media(max-width:600px){.knife{grid-template-columns:1fr}.knife img,.nophoto{width:100%;height:auto;aspect-ratio:1}}
  @media print{body{background:#fff;padding:0}.knife{border-color:#999}}
'''

# (label, column); column None = the formatted born-on date
_REGISTER_FIELDS = (
    ('born', None), ('condition', 'condition'), ('status', 'status'), ('sale', 'sale_status'),
    ('steel', 'blade_steel'), ('blade', 'blade_shape'), ('handle', 'handle_material'),
    ('paid', 'price_paid'), ('from', 'acquired_from'), ('acquired', 'acquired_date'),
    ('location', 'location'), ('asking', 'asking_price'),
)
_REGISTER_FLAGS = (('box', 'has_box'), ('card', 'has_card'), ('papers', 'has_papers'), ('pouch', 'has_pouch'))
_REGISTER_NOTES = (('public note', 'notes_public'), ('private note', 'notes_private'),
                   ('condition', 'condition_note'))


def _register_title(k):
    """'Large Sebenza 31' from model + ext, same rule as the public page."""
    ext = k.get('ext') or {}
    return publish.display_name({'model': k.get('model'), 'size': ext.get('size'),
                                 'generation': ext.get('generation'), 'tag': k['tag']})


def _register_html(user, knives, hero_thumb):
    """One self-contained page: the hero thumb beside every knife's key fields,
    private ones included — this is the owner's copy, not a public surface.
    Every interpolated value goes through html.escape. hero_thumb maps a
    knife id to its thumbs/ path in the ZIP, or None."""
    e = html_mod.escape
    parts = [f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
             f'<meta name="viewport" content="width=device-width,initial-scale=1">\n'
             f'<title>@{e(user["handle"])} — blade-book export</title>\n'
             f'<style>{_REGISTER_STYLE}</style>\n</head>\n<body>\n<main>\n'
             f'<h1>@{e(user["handle"])}</h1>\n'
             f'<p class="sub">{len(knives)} knives · exported '
             f'{datetime.now(timezone.utc).strftime("%Y-%m-%d")} · knives.csv and knives.json '
             f'hold every column; photos/ holds the originals.</p>\n']
    for k in knives:
        thumb = hero_thumb.get(k['id'])
        img = f'<img src="{e(thumb)}" alt="">' if thumb else '<div class="nophoto">no photo</div>'
        dl = ''
        for label, col in _REGISTER_FIELDS:
            val = (publish._fmt_born(k.get('born_on'), k.get('born_on_precision')) if col is None
                   else k.get(col))
            if val in (None, ''):
                continue
            if col in ('price_paid', 'asking_price'):
                val = f'${val:,.0f}' if float(val) == int(float(val)) else f'${val:,.2f}'
            elif col == 'sale_status':
                val = str(val).replace('_', ' ')
            dl += f'<dt>{label}</dt><dd>{e(str(val))}</dd>'
        flags = ', '.join(name for name, col in _REGISTER_FLAGS if k.get(col))
        if flags:
            dl += f'<dt>with</dt><dd>{e(flags)}</dd>'
        notes = ''.join(f'<p class="notes"><b>{label}:</b> {e(str(k[col]))}</p>'
                        for label, col in _REGISTER_NOTES if k.get(col))
        maker = f' · {e(k["maker"])}' if k.get('maker') else ''
        parts.append(f'<section class="knife">{img}<div>'
                     f'<h2>{e(_register_title(k))}</h2><p class="tag">{e(k["tag"])}{maker}</p>'
                     f'<dl>{dl}</dl>{notes}</div></section>\n')
    parts.append('</main>\n</body>\n</html>\n')
    return ''.join(parts)


def export_zip(con, store, user, out_dir):
    """Everything the owner has, as one ZIP: knives.json (every column,
    private ones included — it is their data), knives.csv, the original
    photos as photos/<TAG>-<seq>.jpg, the app's own thumbs as
    thumbs/<TAG>-<seq>.jpg (both stored, not deflated: JPEG), and
    register.html — a page showing each knife's hero thumb beside its key
    fields, because a spreadsheet cannot show a picture from a local path.
    A photo or thumb the store no longer has is skipped and named in
    missing_photos / missing_thumbs."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    path = os.path.join(out_dir, f"{user['handle']}-{stamp}-{secrets.token_hex(4)}.zip")
    knives = db.full_register(con, user['id'])
    missing, missing_thumbs = [], []
    hero_thumb = {}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for k in knives:
            for p in k['photos']:
                name = f"{k['tag']}-{p['seq']}"
                try:
                    data = store.get(p['store_key'])
                except KeyError:
                    missing.append(name)
                    p['file'] = None
                else:
                    p['file'] = f'photos/{name}.jpg'
                    _zip_stored(z, p['file'], data)
                try:
                    tdata = store.get(db.thumb_key(p['store_key']))
                except KeyError:
                    missing_thumbs.append(name)
                    p['thumb'] = None
                else:
                    p['thumb'] = f'thumbs/{name}.jpg'
                    _zip_stored(z, p['thumb'], tdata)
            hero = _hero(k)
            hero_thumb[k['id']] = hero['thumb'] if hero else None
        z.writestr('knives.json', json.dumps({
            'generated': datetime.now(timezone.utc).isoformat(), 'handle': user['handle'],
            'count': len(knives), 'missing_photos': missing, 'missing_thumbs': missing_thumbs,
            'knives': knives}, indent=1, default=str))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=EXPORT_CSV_COLUMNS, extrasaction='ignore')
        w.writeheader()
        for k in knives:
            row = {c: _csv_cell(k.get(c)) for c in EXPORT_CSV_COLUMNS}
            hero = _hero(k)
            row['photo_count'] = len(k['photos'])
            row['hero_file'] = (hero or {}).get('file') or ''
            row['photo_files'] = ';'.join(p['file'] for p in k['photos'] if p.get('file'))
            row['thumb_files'] = ';'.join(p['thumb'] for p in k['photos'] if p.get('thumb'))
            w.writerow(row)
        z.writestr('knives.csv', buf.getvalue())
        z.writestr('register.html', _register_html(user, knives, hero_thumb))
    log.info('export for @%s: %d knives, %d missing photos, %d missing thumbs, %d bytes',
             user['handle'], len(knives), len(missing), len(missing_thumbs), os.path.getsize(path))
    return path


# --- delete --------------------------------------------------------------------------

def delete_account(con, store, user):
    """Complete and synchronous (spec §6/§11). Order matters: the tombstone
    first (if anything below fails the email is still barred from a fresh
    allowance), then the store keys (we need the photo rows to know them),
    then the static surface and search index, then the users row — whose FK
    cascade takes knives, photos, events, wants, intros both ways, reports
    both ways. Unconditional: the ROUTE refuses admin rows, not this."""
    db.tombstone_email(con, user['email'])
    db.release_handle(con, user['handle'])
    keys = db.owner_photo_keys(con, user['id'])
    removed = 0
    failed = 0
    for key in keys:
        try:
            if store.delete(key):
                removed += 1
        except Exception as e:  # noqa: BLE001 — one bad key must not strand the deletion
            failed += 1
            log.warning('delete_account: store.delete(%s) failed: %r', key, e)
    surface_removed = remove_public_surface(user['handle'])
    search.deindex_user(con, user['id'])
    counts = db.delete_user(con, user['id'])
    counts['store_keys'] = removed
    counts['store_failed'] = failed
    counts['surface_removed'] = surface_removed
    if failed or not surface_removed:
        log.error('account deleted: @%s (user %s) with residue %s', user['handle'], user['id'], counts)
    else:
        log.info('account deleted: @%s (user %s) %s', user['handle'], user['id'], counts)
    return counts
