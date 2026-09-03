# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""db helpers behind the gate and the admin users table (plan 10)."""
import pytest

from bb import billing, db
from tests.test_search import _mk_knife, _mk_user


def test_set_sub_status_validates_and_records_source(con):
    u = _mk_user(con)
    assert u['sub_status'] == 'free' and u['sub_source'] == 'manual'
    u2 = db.set_sub_status(con, u['id'], 'active')
    assert u2['sub_status'] == 'active' and u2['sub_source'] == 'manual'
    u3 = db.set_sub_status(con, u['id'], 'lapsed', source='stripe')
    assert u3['sub_status'] == 'lapsed' and u3['sub_source'] == 'stripe'
    with pytest.raises(ValueError):
        db.set_sub_status(con, u['id'], 'gold')
    assert db.get_user(con, u['id'])['sub_status'] == 'lapsed'      # the bad call wrote nothing
    assert db.set_sub_status(con, 999999, 'active') is None


def test_manual_billing_flips_through_the_interface(con):
    u = _mk_user(con)
    out = billing.ManualBilling().set_status(con, u['id'], 'active')
    assert out['sub_status'] == 'active' and out['sub_source'] == 'manual'
    assert billing.from_env().set_status(con, 999999, 'active') is None


def test_increment_free_old(con):
    u = _mk_user(con)
    assert db.increment_free_old(con, u['id']) == 1
    assert db.increment_free_old(con, u['id']) == 2
    assert db.get_user(con, u['id'])['free_old_used'] == 2


def test_admin_users_shape_and_order(con):
    a = _mk_user(con, email='a@example.com', handle='a-guy')
    b = _mk_user(con, email='b@example.com', handle='b-guy', sub_status='active', free_old_used=2)
    con.execute("UPDATE users SET created = '2020-01-01T00:00:00+00:00' WHERE id = ?", (a['id'],)); con.commit()
    _mk_knife(con, b['id']); _mk_knife(con, b['id'])
    db.create_draft_knife(con, b['id'])            # a draft does not count
    rows = db.admin_users(con)
    assert [r['handle'] for r in rows] == ['b-guy', 'a-guy']          # newest first
    assert all(tuple(r.keys()) == db.ADMIN_USER_FIELDS for r in rows)
    bb_ = rows[0]
    assert bb_['knives'] == 2 and bb_['sub_status'] == 'active' and bb_['free_old_used'] == 2
    assert bb_['email'] == 'b@example.com' and bb_['is_admin'] == 0
    assert rows[1]['knives'] == 0
    for r in rows:
        for secret in ('session_secret', 'auth_subjects', 'public_key', 'stripe_customer_id', 'publish_dirty_at'):
            assert secret not in r
