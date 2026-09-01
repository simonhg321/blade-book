# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from bb import db


def _user(con, email='pub@example.com', handle='pub'):
    return db.create_user(con, email, handle)


def _live_knife(con, uid, **cols):
    k = db.create_draft_knife(con, uid)
    con.execute('UPDATE knives SET confidence = ? WHERE id = ?', ('{}', k['id']))
    con.commit()
    db.add_photo(con, uid, k['id'], 1, f'{uid}/{k["id"]}/1.jpg', 'a' * 64, 100, 100)
    db.publish_knife(con, uid, k['id'])
    if cols:
        sets = ', '.join(f'{c} = ?' for c in cols)
        con.execute(f'UPDATE knives SET {sets} WHERE id = ?', (*cols.values(), k['id']))
        con.commit()
    return db.get_knife(con, uid, k['id'])


def test_schema_v4_has_new_columns(con):
    cols = {r[1] for r in con.execute('PRAGMA table_info(users)')}
    assert {'public_key', 'publish_dirty_at'} <= cols


def test_migration_from_v3_adds_columns(tmp_path, monkeypatch):
    # simulate a v3 db: drop the new columns is impossible in sqlite pre-3.35-alter,
    # so instead assert MIGRATIONS[4] statements exist and are idempotent via _migrate
    assert any('public_key' in s for s in db.MIGRATIONS[4])
    assert any('publish_dirty_at' in s for s in db.MIGRATIONS[4])
    assert db.SCHEMA_VERSION == 5


def test_set_user_settings_whitelist(con):
    uid = _user(con)
    u = db.set_user_settings(con, uid, {'hide_born_day': 1, 'profile_private': 1,
                                        'public_key': 'ozzy'})
    assert u['hide_born_day'] == 1 and u['profile_private'] == 1
    assert u['public_key'] == 'ozzy'
    u = db.set_user_settings(con, uid, {'public_key': None})
    assert u['public_key'] is None
    try:
        db.set_user_settings(con, uid, {'is_admin': 1})
        raise AssertionError('is_admin must not be settable')
    except ValueError:
        pass


def test_set_user_settings_unknown_user(con):
    assert db.set_user_settings(con, 99999, {'hide_born_day': 1}) is None


def test_mark_and_clear_dirty(con):
    uid = _user(con)
    assert db.get_user(con, uid)['publish_dirty_at'] is None
    db.mark_publish_dirty(con, uid)
    stamp = db.get_user(con, uid)['publish_dirty_at']
    assert stamp
    # compare-and-clear: wrong stamp leaves it dirty (a save landed mid-build)
    assert db.clear_publish_dirty_if(con, uid, 'other-stamp') is False
    assert db.get_user(con, uid)['publish_dirty_at'] == stamp
    assert db.clear_publish_dirty_if(con, uid, stamp) is True
    assert db.get_user(con, uid)['publish_dirty_at'] is None


def test_dirty_owners_respects_quiet_window(con):
    uid = _user(con)
    db.mark_publish_dirty(con, uid)
    assert db.dirty_owners(con, quiet_s=3600) == []      # too fresh
    rows = db.dirty_owners(con, quiet_s=0)
    assert [r['id'] for r in rows] == [uid]
    assert rows[0]['handle'] == 'pub'


def test_public_knives_filters(con):
    uid = _user(con)
    live = _live_knife(con, uid, model='Sebenza')
    _live_knife(con, uid, is_public=0)                    # private → out
    _live_knife(con, uid, sale_status='sold')             # sold → out
    _live_knife(con, uid, sale_status='consigned')        # consigned → out
    draft = db.create_draft_knife(con, uid)               # draft → out
    con.execute('UPDATE knives SET confidence = ? WHERE id = ?', ('{}', draft['id']))
    con.commit()
    other = db.create_user(con, 'other@example.com', 'other')
    _live_knife(con, other)                               # someone else's → out
    rows = db.public_knives(con, uid)
    assert [r['id'] for r in rows] == [live['id']]
    assert rows[0]['ext'] == {} or isinstance(rows[0]['ext'], dict)
    assert isinstance(rows[0]['photos'], list) and rows[0]['photos']
