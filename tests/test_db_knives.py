# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
from datetime import datetime, timedelta, timezone

import pytest

from bb import db


def _two_users(con):
    a = db.create_user(con, 'a@example.com', 'a-collector')
    b = db.create_user(con, 'b@example.com', 'b-collector')
    return a, b


def test_create_draft_reserves_tags_per_owner(env):
    con = db.connect()
    a, b = _two_users(con)
    k1 = db.create_draft_knife(con, a)
    k2 = db.create_draft_knife(con, a)
    k3 = db.create_draft_knife(con, b)
    assert (k1['tag'], k2['tag'], k3['tag']) == ('K01', 'K02', 'K01')
    assert k1['status'] == 'draft' and k1['maker'] == 'crk' and k1['owner_id'] == a
    assert k1['photos'] == []


def test_get_and_list_are_owner_scoped(env):
    con = db.connect()
    a, b = _two_users(con)
    k = db.create_draft_knife(con, a)
    assert db.get_knife(con, a, k['id'])['tag'] == 'K01'
    assert db.get_knife(con, b, k['id']) is None
    assert [x['tag'] for x in db.list_knives(con, a)] == ['K01']
    assert db.list_knives(con, b) == []
    assert db.list_knives(con, a, status='live') == []
    assert db.list_knives(con, a)[0]['photo_count'] == 0


def test_note_bumps_updated_and_is_owner_scoped(env):
    con = db.connect()
    a, b = _two_users(con)
    k = db.create_draft_knife(con, a)
    before = db.get_knife(con, a, k['id'])['updated']
    assert db.set_knife_note(con, b, k['id'], 'nope') is False
    assert db.set_knife_note(con, a, k['id'], 'Large 21 from 2008') is True
    after = db.get_knife(con, a, k['id'])
    assert after['notes_private'] == 'Large 21 from 2008' and after['updated'] >= before


def test_photos_add_get_delete_and_slot_rules(env):
    con = db.connect()
    a, b = _two_users(con)
    k = db.create_draft_knife(con, a)
    pid = db.add_photo(con, a, k['id'], 1, f"{a}/{k['id']}/1.jpg", 'ab' * 32, 1200, 900)
    assert isinstance(pid, int)
    with pytest.raises(db.SlotTaken):
        db.add_photo(con, a, k['id'], 1, f"{a}/{k['id']}/1.png", 'cd' * 32, 10, 10)
    p = db.get_photo(con, a, k['id'], 1)
    assert p['store_key'].endswith('/1.jpg') and p['width'] == 1200 and p['seq'] == 1
    assert db.get_photo(con, b, k['id'], 1) is None
    assert db.get_knife(con, a, k['id'])['photos'][0]['seq'] == 1
    assert db.list_knives(con, a)[0]['photo_count'] == 1
    assert db.delete_photo(con, b, k['id'], 1) is None
    gone = db.delete_photo(con, a, k['id'], 1)
    assert gone['id'] == pid
    assert db.get_photo(con, a, k['id'], 1) is None
    # slot free again after delete
    db.add_photo(con, a, k['id'], 1, f"{a}/{k['id']}/1.heic", 'ef' * 32, None, None)


def test_add_photo_to_foreign_knife_is_not_found(env):
    con = db.connect()
    a, b = _two_users(con)
    k = db.create_draft_knife(con, a)
    with pytest.raises(LookupError):
        db.add_photo(con, b, k['id'], 1, 'x/y/1.jpg', 'ab' * 32, 1, 1)


def test_delete_draft_returns_keys_and_refuses_live(env):
    con = db.connect()
    a, b = _two_users(con)
    k = db.create_draft_knife(con, a)
    db.add_photo(con, a, k['id'], 1, f"{a}/{k['id']}/1.jpg", 'ab' * 32, 1, 1)
    db.add_photo(con, a, k['id'], 3, f"{a}/{k['id']}/3.png", 'cd' * 32, 1, 1)
    assert db.delete_draft_knife(con, b, k['id']) == []      # foreign owner: nothing happens
    assert db.get_knife(con, a, k['id']) is not None
    assert con.execute('SELECT count(*) FROM photos').fetchone()[0] == 2
    keys = db.delete_draft_knife(con, a, k['id'])
    assert sorted(keys) == sorted([f"{a}/{k['id']}/1.jpg", f"{a}/{k['id']}/1.thumb.jpg",
                                   f"{a}/{k['id']}/3.png", f"{a}/{k['id']}/3.thumb.jpg"])
    assert db.get_knife(con, a, k['id']) is None
    assert con.execute('SELECT count(*) FROM photos').fetchone()[0] == 0
    live = db.create_draft_knife(con, a)
    con.execute("UPDATE knives SET status='live' WHERE id=?", (live['id'],)); con.commit()
    assert db.delete_draft_knife(con, a, live['id']) == []
    assert db.get_knife(con, a, live['id']) is not None
    assert db.create_draft_knife(con, a)['tag'] == 'K03'  # tags never reused


def test_purge_stale_drafts(env):
    con = db.connect()
    a, _ = _two_users(con)
    old = db.create_draft_knife(con, a)
    db.add_photo(con, a, old['id'], 2, f"{a}/{old['id']}/2.jpg", 'ab' * 32, 1, 1)
    fresh = db.create_draft_knife(con, a)
    live_old = db.create_draft_knife(con, a)
    stale = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    con.execute('UPDATE knives SET updated=? WHERE id IN (?, ?)', (stale, old['id'], live_old['id']))
    con.execute("UPDATE knives SET status='live' WHERE id=?", (live_old['id'],))
    con.commit()
    purged = db.purge_stale_drafts(con, days=7)
    assert [p['id'] for p in purged] == [old['id']]
    assert sorted(purged[0]['keys']) == [f"{a}/{old['id']}/2.jpg", f"{a}/{old['id']}/2.thumb.jpg"]
    assert purged[0]['tag'] == 'K01' and purged[0]['owner_id'] == a
    assert db.get_knife(con, a, old['id']) is None
    assert db.get_knife(con, a, fresh['id']) is not None
    assert db.get_knife(con, a, live_old['id']) is not None
    assert db.purge_stale_drafts(con, days=7) == []


def test_thumb_key():
    assert db.thumb_key('1/7/2.jpg') == '1/7/2.thumb.jpg'
    assert db.thumb_key('1/7/2.heic') == '1/7/2.thumb.jpg'
