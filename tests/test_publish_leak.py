# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""THE leak test (spec §12): set every private column to a unique sentinel,
build the bundle, and prove no sentinel — nor the owner's email, session
secret, page key, events, sold-knife fields, or withheld sale fields —
appears in ANY file of the public bundle."""
import hashlib
import json
import os

from bb import db, publish, store as store_mod

from tests.test_publish import _jpeg_with_exif


def _sentinel(col):
    return f'LEAK-{col.upper()}-9X7'


def _setup(con, tmp_path):
    uid = db.create_user(con, 'leaky@example.com', 'leaky')
    con.execute('UPDATE users SET public_key = ?, session_secret = ? WHERE id = ?',
                ('LEAK-PAGEKEY-9X7', 'LEAK-SESSECRET-9X7', uid))
    con.commit()
    st = store_mod.LocalFSStore(str(tmp_path / 'photos'))
    k = db.create_draft_knife(con, uid)
    sentinels = {}
    sets, vals = [], []
    for col in sorted(db.PRIVATE_COLUMNS):
        sets.append(f'{col} = ?')
        if col == 'price_paid':               # numeric: recognizable magic number
            vals.append(13371337.0)
            sentinels[col] = '13371337'
        elif col == 'confidence':             # json-typed: _knife_row json.loads it
            vals.append(json.dumps({'leak': _sentinel(col)}))
            sentinels[col] = _sentinel(col)
        else:
            vals.append(_sentinel(col))
            sentinels[col] = _sentinel(col)
    sets.append("model = 'Sebenza'")
    con.execute(f'UPDATE knives SET {", ".join(sets)} WHERE id = ?', (*vals, k['id']))
    con.commit()
    key = f"{uid}/{k['id']}/1.jpg"
    st.put(key, _jpeg_with_exif(600, 400))
    db.add_photo(con, uid, k['id'], 1, key, hashlib.sha256(b'x').hexdigest(), 600, 400)
    db.publish_knife(con, uid, k['id'])
    db.set_public(con, uid, [k['id']], True)   # born private; these fixtures are public
    # an event with private-ish free text + a withheld seller note
    db.add_event(con, uid, k['id'], 'edited', detail='LEAK-EVENT-DETAIL-9X7',
                 counterparty='LEAK-COUNTERPARTY-9X7')
    db.set_sale(con, uid, k['id'], 'for_trade', seller_note='LEAK-SELLERNOTE-9X7')

    # a second, SOLD knife: live + public but sale_status='sold' must never
    # reach the public surface (crkinv rule, db.public_knives excludes it)
    k2 = db.create_draft_knife(con, uid)
    con.execute("UPDATE knives SET model = 'Mnandi', notes_public = ? WHERE id = ?",
                ('LEAK-SOLD-9X7', k2['id']))
    con.commit()
    key2 = f"{uid}/{k2['id']}/1.jpg"
    st.put(key2, _jpeg_with_exif(600, 400))
    db.add_photo(con, uid, k2['id'], 1, key2, hashlib.sha256(b'y').hexdigest(), 600, 400)
    db.publish_knife(con, uid, k2['id'])
    db.set_public(con, uid, [k2['id']], True)   # born private; these fixtures are public
    db.set_sale(con, uid, k2['id'], 'sold', amount=999.0, counterparty='buyer')

    return db.get_user(con, uid), st, k, sentinels


def _bundle_bytes(handle):
    blobs = {}
    for dirpath, _, files in os.walk(publish.bundle_dir(handle)):
        for f in files:
            p = os.path.join(dirpath, f)
            blobs[p] = open(p, 'rb').read()
    assert blobs, 'bundle is empty — build failed?'
    return blobs


def test_no_private_column_reaches_the_bundle(con, tmp_path):
    user, st, _, sentinels = _setup(con, tmp_path)
    assert publish.build_user(con, user, st) == 1
    blobs = _bundle_bytes('leaky')
    assert len(db.PRIVATE_COLUMNS) >= 9   # the loop below must actually cover them
    for col in sorted(db.PRIVATE_COLUMNS):
        needle = sentinels[col].encode()
        for path, blob in blobs.items():
            assert needle not in blob, f'{col} leaked into {path}'
    for needle in (b'LEAK-EVENT-DETAIL-9X7', b'LEAK-COUNTERPARTY-9X7',
                   b'LEAK-SELLERNOTE-9X7',        # for_trade: note withheld
                   b'leaky@example.com',
                   b'LEAK-PAGEKEY-9X7',           # keys.json holds only its sha-256
                   b'LEAK-SESSECRET-9X7',
                   b'LEAK-SOLD-9X7'):             # sold knife: excluded entirely
        for path, blob in blobs.items():
            assert needle not in blob, f'{needle} leaked into {path}'


def test_draft_and_private_knives_never_appear(con, tmp_path):
    user, st, _, _sentinels = _setup(con, tmp_path)
    d2 = db.create_draft_knife(con, user['id'])          # a draft with a secret note
    db.set_knife_note(con, user['id'], d2['id'], 'LEAK-DRAFTNOTE-9X7')
    publish.build_user(con, user, st)
    for path, blob in _bundle_bytes('leaky').items():
        assert b'LEAK-DRAFTNOTE-9X7' not in blob, path
        assert d2['tag'].encode() not in blob, path
