# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""THE leak test (spec §12): set every private column to a unique sentinel,
build the bundle, and prove no sentinel — nor the owner's email, events, or
withheld sale fields — appears in ANY file of the public bundle."""
import hashlib
import json
import os

from bb import db, publish, store as store_mod

from tests.test_publish import _jpeg_with_exif

TEXT_SENTINELS = {}


def _sentinel(col):
    return f'LEAK-{col.upper()}-9X7'


def _setup(con, tmp_path):
    uid = db.create_user(con, 'leaky@example.com', 'leaky')
    st = store_mod.LocalFSStore(str(tmp_path / 'photos'))
    k = db.create_draft_knife(con, uid)
    sets, vals = [], []
    for col in sorted(db.PRIVATE_COLUMNS):
        sets.append(f'{col} = ?')
        if col == 'price_paid':               # numeric: recognizable magic number
            vals.append(13371337.0)
            TEXT_SENTINELS[col] = '13371337'
        elif col == 'confidence':             # json-typed: _knife_row json.loads it
            vals.append(json.dumps({'leak': _sentinel(col)}))
            TEXT_SENTINELS[col] = _sentinel(col)
        else:
            vals.append(_sentinel(col))
            TEXT_SENTINELS[col] = _sentinel(col)
    sets.append("model = 'Sebenza'")
    con.execute(f'UPDATE knives SET {", ".join(sets)} WHERE id = ?', (*vals, k['id']))
    con.commit()
    key = f"{uid}/{k['id']}/1.jpg"
    st.put(key, _jpeg_with_exif(600, 400))
    db.add_photo(con, uid, k['id'], 1, key, hashlib.sha256(b'x').hexdigest(), 600, 400)
    db.publish_knife(con, uid, k['id'])
    # an event with private-ish free text + a withheld seller note
    db.add_event(con, uid, k['id'], 'edited', detail='LEAK-EVENT-DETAIL-9X7',
                 counterparty='LEAK-COUNTERPARTY-9X7')
    db.set_sale(con, uid, k['id'], 'for_trade', seller_note='LEAK-SELLERNOTE-9X7')
    return db.get_user(con, uid), st, k


def _bundle_bytes(handle):
    blobs = {}
    for dirpath, _, files in os.walk(publish.bundle_dir(handle)):
        for f in files:
            p = os.path.join(dirpath, f)
            blobs[p] = open(p, 'rb').read()
    assert blobs, 'bundle is empty — build failed?'
    return blobs


def test_no_private_column_reaches_the_bundle(con, tmp_path):
    user, st, _ = _setup(con, tmp_path)
    assert publish.build_user(con, user, st) == 1
    blobs = _bundle_bytes('leaky')
    assert len(db.PRIVATE_COLUMNS) >= 9   # the loop below must actually cover them
    for col in sorted(db.PRIVATE_COLUMNS):
        needle = TEXT_SENTINELS[col].encode()
        for path, blob in blobs.items():
            assert needle not in blob, f'{col} leaked into {path}'
    for needle in (b'LEAK-EVENT-DETAIL-9X7', b'LEAK-COUNTERPARTY-9X7',
                   b'LEAK-SELLERNOTE-9X7',        # for_trade: note withheld
                   b'leaky@example.com'):
        for path, blob in blobs.items():
            assert needle not in blob, f'{needle} leaked into {path}'


def test_draft_and_private_knives_never_appear(con, tmp_path):
    user, st, _ = _setup(con, tmp_path)
    d2 = db.create_draft_knife(con, user['id'])          # a draft with a secret note
    db.set_knife_note(con, user['id'], d2['id'], 'LEAK-DRAFTNOTE-9X7')
    publish.build_user(con, user, st)
    for path, blob in _bundle_bytes('leaky').items():
        assert b'LEAK-DRAFTNOTE-9X7' not in blob, path
        assert d2['tag'].encode() not in blob, path
