# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/account.py — the account-level operations behind /me/settings (plan 11):
the one-time handle change, the everything-ZIP export, and complete account
deletion. Pure functions over (con, store); the routes stay thin.

Deletion is complete or it is a bug (spec §11: "delete-account is real and
complete"): DB rows, store keys, the public bundle + its tmp + its lock, the
search index, and a tombstone. tests/test_account.py enumerates every surface."""
import csv
import fcntl
import io
import json
import logging
import os
import secrets
import shutil
import zipfile
from datetime import datetime, timezone

from bb import auth, db, publish, search

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
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(dest + '.tmp', ignore_errors=True)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
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

# The knives table in DDL order minus id/owner_id, plus photo_count. ext and
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
    'photo_count',
)


def _csv_cell(v):
    if v is None:
        return ''
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True)
    return v


def export_zip(con, store, user, out_dir):
    """Everything the owner has, as one ZIP: knives.json (every column,
    private ones included — it is their data), knives.csv, and the original
    photos as photos/<TAG>-<seq>.jpg (stored, not deflated: JPEG). A photo the
    store no longer has is skipped and named in missing_photos."""
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    path = os.path.join(out_dir, f"{user['handle']}-{stamp}-{secrets.token_hex(4)}.zip")
    knives = db.full_register(con, user['id'])
    missing = []
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for k in knives:
            for p in k['photos']:
                name = f"{k['tag']}-{p['seq']}"
                try:
                    data = store.get(p['store_key'])
                except KeyError:
                    missing.append(name)
                    p['file'] = None
                    continue
                p['file'] = f'photos/{name}.jpg'
                z.writestr(zipfile.ZipInfo(p['file'], date_time=(1980, 1, 1, 0, 0, 0)), data,
                           compress_type=zipfile.ZIP_STORED)
        z.writestr('knives.json', json.dumps({
            'generated': datetime.now(timezone.utc).isoformat(), 'handle': user['handle'],
            'count': len(knives), 'missing_photos': missing, 'knives': knives}, indent=1, default=str))
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=EXPORT_CSV_COLUMNS, extrasaction='ignore')
        w.writeheader()
        for k in knives:
            row = {c: _csv_cell(k.get(c)) for c in EXPORT_CSV_COLUMNS}
            row['photo_count'] = len(k['photos'])
            w.writerow(row)
        z.writestr('knives.csv', buf.getvalue())
    log.info('export for @%s: %d knives, %d missing photos, %d bytes',
             user['handle'], len(knives), len(missing), os.path.getsize(path))
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
