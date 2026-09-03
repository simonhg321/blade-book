# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""The gate at save (spec §10) and GET /api/billing (plan 10)."""
import hashlib

from bb import db
from tests.conftest import signed_in

K = '/blade-book/api/knives'
BILLING = '/blade-book/api/billing'


def _draft(con, uid, born='2008-03-14'):
    """A decoded draft with one photo, ready to save."""
    k = db.create_draft_knife(con, uid)
    con.execute("UPDATE knives SET confidence = '{}', model = 'Sebenza', born_on = ?, "
                "born_on_precision = 'day' WHERE id = ?", (born, k['id']))
    con.commit()
    db.add_photo(con, uid, k['id'], 1, f'{uid}/{k["id"]}/1.jpg',
                 hashlib.sha256(str(k['id']).encode()).hexdigest(), 800, 600)
    return k


def _user(con, me, **cols):
    if cols:
        sets = ', '.join(f'{c} = ?' for c in cols)
        con.execute(f'UPDATE users SET {sets} WHERE id = ?', (*cols.values(), me['id'])); con.commit()
    return db.get_user(con, me['id'])


def test_young_knife_saves_free_and_spends_nothing(client, mailer, con):
    me = signed_in(client, mailer)
    k = _draft(con, me['id'], born='2026-08-01')
    r = client.post(f"{K}/{k['id']}/save")
    assert r.status_code == 200 and r.get_json()['status'] == 'live'
    assert db.get_user(con, me['id'])['free_old_used'] == 0


def test_old_knives_three_free_then_402_and_the_draft_stays(client, mailer, con):
    me = signed_in(client, mailer)
    for i in range(3):
        k = _draft(con, me['id'])
        assert client.post(f"{K}/{k['id']}/save").status_code == 200
        assert db.get_user(con, me['id'])['free_old_used'] == i + 1
    k4 = _draft(con, me['id'], born=None)                       # undated counts as old
    r = client.post(f"{K}/{k4['id']}/save")
    assert r.status_code == 402
    j = r.get_json()
    assert j['gated'] is True and j['sub_status'] == 'free' and j['price'] == '$4/mo or $36/yr'
    assert j['contact'] == 'hello@' + 'blade-book.com' and 'subscription' in j['error']
    assert db.get_knife(con, me['id'], k4['id'])['status'] == 'draft'
    assert db.get_user(con, me['id'])['free_old_used'] == 3
    # the admin flips them active → the same draft saves, no slot spent
    db.set_sub_status(con, me['id'], 'active')
    assert client.post(f"{K}/{k4['id']}/save").status_code == 200
    assert db.get_user(con, me['id'])['free_old_used'] == 3
    # lapsed is not active
    db.set_sub_status(con, me['id'], 'lapsed')
    k5 = _draft(con, me['id'])
    assert client.post(f"{K}/{k5['id']}/save").status_code == 402


def test_account_over_a_year_is_gated_even_for_a_young_knife(client, mailer, con):
    me = signed_in(client, mailer)
    _user(con, me, created='2020-01-01T00:00:00+00:00')
    k = _draft(con, me['id'], born='2026-08-01')
    r = client.post(f"{K}/{k['id']}/save")
    assert r.status_code == 402 and 'over a year' in r.get_json()['error']
    db.set_sub_status(con, me['id'], 'active')
    assert client.post(f"{K}/{k['id']}/save").status_code == 200


def test_live_knife_never_locks_and_resave_never_charges(client, mailer, con):
    me = signed_in(client, mailer)
    k = _draft(con, me['id'])
    assert client.post(f"{K}/{k['id']}/save").status_code == 200
    _user(con, me, free_old_used=3, created='2020-01-01T00:00:00+00:00')   # now fully gated
    r = client.post(f"{K}/{k['id']}/save")                                  # re-save = no-op
    assert r.status_code == 200 and r.get_json()['status'] == 'live'
    assert db.get_user(con, me['id'])['free_old_used'] == 3
    # editing a live knife is never gated (spec §10)
    r = client.patch(f"{K}/{k['id']}", json={'notes_private': 'still mine'})
    assert r.status_code == 200


def test_missing_photo_is_400_and_spends_nothing(client, mailer, con):
    me = signed_in(client, mailer)
    k = db.create_draft_knife(con, me['id'])
    con.execute("UPDATE knives SET confidence = '{}', born_on = '2008-01-01' WHERE id = ?", (k['id'],)); con.commit()
    r = client.post(f"{K}/{k['id']}/save")
    assert r.status_code == 400 and 'photo' in r.get_json()['error']
    assert db.get_user(con, me['id'])['free_old_used'] == 0


def test_missing_photo_beats_the_gate_even_when_fully_gated(client, mailer, con):
    """A gated user with no free slots left still gets the photo error, not the
    billing 402 — a subscription would not fix a missing photo."""
    me = signed_in(client, mailer)
    _user(con, me, free_old_used=3)
    k = db.create_draft_knife(con, me['id'])
    con.execute("UPDATE knives SET confidence = '{}', born_on = '2008-01-01' WHERE id = ?", (k['id'],)); con.commit()
    r = client.post(f"{K}/{k['id']}/save")
    assert r.status_code == 400 and 'photo' in r.get_json()['error']
    assert db.get_user(con, me['id'])['free_old_used'] == 3
    assert db.get_knife(con, me['id'], k['id'])['status'] == 'draft'


def test_no_admin_bypass_at_save(client, mailer, con):
    me = signed_in(client, mailer, email='admin@example.com')
    _user(con, me, is_admin=1, free_old_used=3)
    k = _draft(con, me['id'])
    assert client.post(f"{K}/{k['id']}/save").status_code == 402


def test_billing_summary_anonymous_and_signed_in(client, mailer, con):
    j = client.get(BILLING).get_json()
    assert j == {'signed_in': False, 'early_access': True, 'price': '$4/mo or $36/yr',
                 'contact': 'hello@' + 'blade-book.com'}
    me = signed_in(client, mailer)
    _user(con, me, free_old_used=1)
    j = client.get(BILLING).get_json()
    assert j['signed_in'] is True and j['sub_status'] == 'free' and j['active'] is False
    assert j['free_old_left'] == 2 and j['free_old_allowance'] == 3 and j['account_days'] == 0
    assert j['account_free_days_left'] == 365
    assert set(j) == {'signed_in', 'early_access', 'price', 'contact', 'sub_status', 'active', 'free_old_used',
                      'free_old_allowance', 'free_old_left', 'account_days', 'account_free_days_left'}


def test_app_carries_a_billing_impl(app):
    from bb import billing
    assert isinstance(app.config['BILLING'], billing.ManualBilling)
