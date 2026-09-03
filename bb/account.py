# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/account.py — the account-level operations behind /me/settings (plan 11):
the one-time handle change, the everything-ZIP export, and complete account
deletion. Pure functions over (con, store); the routes stay thin.

Deletion is complete or it is a bug (spec §11: "delete-account is real and
complete"): DB rows, store keys, the public bundle + its tmp + its lock, the
search index, and a tombstone. tests/test_account.py enumerates every surface."""
import csv
import io
import json
import logging
import os
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
    and the publish lock (DATA_DIR/publish-locks/<handle>.lock). Missing
    pieces are fine — this runs for handles that never published too."""
    dest = publish.bundle_dir(handle)
    shutil.rmtree(dest, ignore_errors=True)
    shutil.rmtree(dest + '.tmp', ignore_errors=True)
    try:
        os.unlink(publish._lock_path(handle))
    except FileNotFoundError:
        pass


def change_handle(con, user, new):
    """Rename once. The old bundle comes down now and the search cards go with
    it (they carry the handle); the caller schedules the republish, which
    rebuilds + reindexes under the new handle."""
    new = validate_new_handle(con, user, new)
    old = user['handle']
    fresh = db.set_handle(con, user['id'], new)
    remove_public_surface(old)
    search.deindex_user(con, user['id'])
    log.info('handle changed: @%s -> @%s (user %s)', old, new, user['id'])
    return fresh
