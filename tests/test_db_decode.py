# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import datetime as dt
import sqlite3

from bb import db, decode, paths
from bb.makers import core, crk


def _user(con, email='a@example.com'):
    # create_user returns a bare id (see tests/test_db_knives.py); wrap so u['id'] works below.
    uid = db.create_user(con, email, email.split('@')[0] + '-collector')
    return db.get_user(con, uid)


def _decoded(**over):
    core_ = {f: '' for f in core.CORE_FIELDS}
    core_.update({'model': 'Sebenza', 'blade_steel': 'CPM MagnaCut', 'blade_shape': 'Drop Point',
                  'blade_length_in': None, 'born_on': '2025-09-29', 'born_on_precision': 'day',
                  'born_on_source': 'card', 'condition': 1, 'has_box': True, 'has_card': True,
                  'has_papers': False, 'has_pouch': True, 'has_lanyard': True, 'has_spare_hardware': False})
    ext = {k: '' for k in crk.EXT_PROPS}
    ext.update({'generation': '31', 'size': 'Large', 'crk_sku': 'L31-1400-0004', 'hand': 'right'})
    d = decode.Decoded(core=core_, ext=ext, card_text='LARGE SEBENZA 31\nBorn on 09/29/2025', no_card=False,
                       confidence={'model': 'high', 'born_on': 'high'}, reasoning='card read',
                       flags=['a flag'], model='claude-sonnet-5', input_tokens=5000, output_tokens=300, latency_ms=900)
    for k, v in over.items():
        setattr(d, k, v)
    return d


def test_v3_migration_adds_private_columns_to_a_v2_db(env):
    # build a v2-shaped DB by hand: knives without the two new columns
    con = sqlite3.connect(paths.db_path())
    v2 = db.SCHEMA.replace('  card_text TEXT, decode_note TEXT,\n', '')
    assert v2 != db.SCHEMA
    con.executescript(v2)
    con.execute('INSERT INTO schema_version VALUES (2)')
    con.commit(); con.close()
    con = db.connect()
    cols = {r[1] for r in con.execute('PRAGMA table_info(knives)')}
    assert {'card_text', 'decode_note'} <= cols
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
    con.close()
    db.connect().close()  # idempotent — second connect must not fail on duplicate column


def test_private_columns_include_decode_fields():
    assert {'card_text', 'decode_note', 'confidence'} <= db.PRIVATE_COLUMNS


def test_apply_decode_writes_core_ext_confidence_and_event(env):
    con = db.connect()
    u = _user(con)
    k = db.create_draft_knife(con, u['id'])
    out = db.apply_decode(con, u['id'], k['id'], _decoded())
    assert out['model'] == 'Sebenza' and out['blade_steel'] == 'CPM MagnaCut'
    assert out['has_box'] == 1 and out['has_papers'] == 0 and out['condition'] == 1
    assert out['variant'] is None            # '' → NULL
    assert out['blade_length_in'] is None
    assert out['ext']['generation'] == '31' and out['ext']['crk_sku'] == 'L31-1400-0004'
    assert out['confidence'] == {'model': 'high', 'born_on': 'high'}
    assert out['card_text'].startswith('LARGE SEBENZA 31')
    assert 'card read' in out['decode_note'] and 'a flag' in out['decode_note']
    assert out['status'] == 'draft'          # decode never publishes
    ev = db.list_events(con, u['id'], k['id'])
    assert len(ev) == 1 and ev[0]['type'] == 'decoded' and 'claude-sonnet-5' in ev[0]['detail']
    assert ev[0]['public_visible'] == 0


def test_apply_decode_is_owner_scoped_and_idempotent(env):
    con = db.connect()
    a, b = _user(con, 'a@example.com'), _user(con, 'b@example.com')
    k = db.create_draft_knife(con, a['id'])
    assert db.apply_decode(con, b['id'], k['id'], _decoded()) is None
    assert db.get_knife(con, a['id'], k['id'])['model'] is None
    db.apply_decode(con, a['id'], k['id'], _decoded())
    out = db.apply_decode(con, a['id'], k['id'], _decoded(card_text='SECOND'))
    assert out['card_text'] == 'SECOND'
    assert len(db.list_events(con, a['id'], k['id'])) == 2


def test_decodes_today_counts_only_today_and_only_decoded(env):
    con = db.connect()
    u = _user(con)
    k = db.create_draft_knife(con, u['id'])
    assert db.decodes_today(con, u['id']) == 0
    db.add_event(con, u['id'], k['id'], 'decoded', detail='x')
    db.add_event(con, u['id'], k['id'], 'photographed')
    assert db.decodes_today(con, u['id']) == 1
    yesterday = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()
    con.execute("INSERT INTO events (knife_id, owner_id, date, type) VALUES (?, ?, ?, 'decoded')",
                (k['id'], u['id'], yesterday))
    con.commit()
    assert db.decodes_today(con, u['id']) == 1
    other = _user(con, 'b@example.com')
    assert db.decodes_today(con, other['id']) == 0


def test_add_event_rejects_unknown_type(env):
    import pytest
    con = db.connect()
    u = _user(con)
    k = db.create_draft_knife(con, u['id'])
    with pytest.raises(sqlite3.IntegrityError):
        db.add_event(con, u['id'], k['id'], 'exploded')
