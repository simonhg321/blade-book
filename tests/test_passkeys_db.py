# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import sqlite3

import pytest

from bb import db


def _user(con, email='sam@example.com', handle='sam'):
    return db.create_user(con, email, handle)


def test_add_list_touch_remove(con):
    uid = _user(con)
    pid = db.add_passkey(con, uid, b'cred-1', b'pub-1', 0, ['internal'], 'Safari on Mac')
    rows = db.passkeys_for_user(con, uid)
    assert len(rows) == 1 and rows[0]['id'] == pid and rows[0]['name'] == 'Safari on Mac'
    assert rows[0]['transports'] == ['internal'] and rows[0]['last_used'] is None and rows[0]['created']
    assert 'public_key' not in rows[0] and 'credential_id' not in rows[0]
    full = db.passkey_by_credential_id(con, b'cred-1')
    assert full['user_id'] == uid and full['public_key'] == b'pub-1' and full['sign_count'] == 0
    db.touch_passkey(con, pid, 7)
    full = db.passkey_by_credential_id(con, b'cred-1')
    assert full['sign_count'] == 7 and full['last_used']
    assert db.count_passkeys(con, uid) == 1
    assert db.remove_passkey(con, uid, pid) is True
    assert db.passkeys_for_user(con, uid) == [] and db.passkey_by_credential_id(con, b'cred-1') is None


def test_transports_may_be_absent(con):
    uid = _user(con)
    db.add_passkey(con, uid, b'c', b'p', 0, None, 'passkey')
    assert db.passkeys_for_user(con, uid)[0]['transports'] is None


def test_credential_id_is_unique_across_users(con):
    a, b = _user(con), _user(con, 'bo@example.com', 'bo')
    db.add_passkey(con, a, b'same', b'p', 0, None, 'x')
    with pytest.raises(sqlite3.IntegrityError):
        db.add_passkey(con, b, b'same', b'p2', 0, None, 'y')


def test_remove_only_your_own(con):
    a, b = _user(con), _user(con, 'bo@example.com', 'bo')
    pid = db.add_passkey(con, a, b'c', b'p', 0, None, 'x')
    assert db.remove_passkey(con, b, pid) is False
    assert db.count_passkeys(con, a) == 1


def test_passkeys_go_with_the_user(con):
    """Review focus 5: cascade, else a deleted user's credential id blocks the device forever."""
    uid = _user(con)
    db.add_passkey(con, uid, b'c', b'p', 0, None, 'x')
    con.execute('DELETE FROM users WHERE id = ?', (uid,))
    con.commit()
    assert db.passkey_by_credential_id(con, b'c') is None
