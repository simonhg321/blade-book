# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/billing.py — the gate math (spec §10 can_add rules 1–3), the API summary,
the one-line price. Pure functions; no DB."""
import datetime as dt

import pytest

from bb import billing

TODAY = dt.date(2026, 9, 3)


def _user(created_days_ago=10, sub_status='free', free_old_used=0):
    created = (dt.datetime(2026, 9, 3, 12, 0, tzinfo=dt.timezone.utc)
               - dt.timedelta(days=created_days_ago)).isoformat()
    return {'id': 1, 'handle': 'sam', 'created': created, 'sub_status': sub_status,
            'free_old_used': free_old_used, 'is_admin': 0}


def test_constants_are_the_spec_numbers():
    assert billing.FREE_OLD_KNIVES == 3 and billing.FREE_DAYS == 365
    assert billing.DEFAULT_PRICE_TEXT == '$4/mo or $36/yr'


def test_price_text_is_one_config_line(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_PRICE_TEXT', raising=False)
    assert billing.price_text() == '$4/mo or $36/yr'
    monkeypatch.setenv('BLADEBOOK_PRICE_TEXT', '$5/mo')
    assert billing.price_text() == '$5/mo'


def test_account_days():
    assert billing.account_days(_user(created_days_ago=0), TODAY) == 0
    assert billing.account_days(_user(created_days_ago=400), TODAY) == 400


@pytest.mark.parametrize('born', ['2026-06-01', '2025-09-04', '2026', '2026-09'])
def test_rule2_young_knife_is_free_for_a_young_account(born):
    g = billing.can_add(_user(), born, TODAY)
    assert g == (True, None, False)


@pytest.mark.parametrize('born', ['2025-09-03', '2008-03-14', '1999', None, '', 'unknown'])
def test_rule3_old_or_undated_knife_spends_a_free_slot(born):
    for used in (0, 1, 2):
        g = billing.can_add(_user(free_old_used=used), born, TODAY)
        assert g.ok and g.charge and g.reason is None, (born, used)
    g = billing.can_add(_user(free_old_used=3), born, TODAY)
    assert not g.ok and not g.charge
    assert 'older than 12 months' in g.reason and '3 free' in g.reason and 'subscription' in g.reason


def test_rule3_active_sub_never_charges_a_slot():
    g = billing.can_add(_user(free_old_used=3, sub_status='active'), '2008-03-14', TODAY)
    assert g == (True, None, False)
    g = billing.can_add(_user(free_old_used=0, sub_status='active'), '2008-03-14', TODAY)
    assert g == (True, None, False)          # a subscriber's slots are never spent


def test_rule1_account_over_a_year_needs_active_even_for_a_young_knife():
    g = billing.can_add(_user(created_days_ago=365), '2026-09-01', TODAY)
    assert not g.ok and not g.charge and 'over a year' in g.reason and 'subscription' in g.reason
    assert billing.can_add(_user(created_days_ago=364), '2026-09-01', TODAY).ok
    assert billing.can_add(_user(created_days_ago=800, sub_status='active'), '2008-01-01', TODAY) == (True, None, False)


def test_lapsed_is_not_active():
    assert not billing.is_active(_user(sub_status='lapsed'))
    assert not billing.can_add(_user(created_days_ago=400, sub_status='lapsed'), '2026-09-01', TODAY).ok
    assert not billing.can_add(_user(free_old_used=3, sub_status='lapsed'), '2008-01-01', TODAY).ok


def test_no_admin_bypass_in_the_gate_math():
    u = _user(free_old_used=3); u['is_admin'] = 1
    assert not billing.can_add(u, '2008-01-01', TODAY).ok


def test_contact_email_is_one_config_line(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_CONTACT_EMAIL', raising=False)
    assert billing.contact_email() == 'hello@' + 'blade-book.com'      # derived from mail.DEFAULT_FROM's domain
    monkeypatch.setenv('BLADEBOOK_CONTACT_EMAIL', 'simon@example.com')
    assert billing.contact_email() == 'simon@example.com'


def test_summary_anonymous(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_PRICE_TEXT', raising=False)
    monkeypatch.delenv('BLADEBOOK_CONTACT_EMAIL', raising=False)
    assert billing.summary(None) == {'signed_in': False, 'early_access': True, 'price': '$4/mo or $36/yr',
                                     'contact': 'hello@' + 'blade-book.com'}


def test_summary_signed_in(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_PRICE_TEXT', raising=False)
    monkeypatch.setenv('BLADEBOOK_CONTACT_EMAIL', 'simon@example.com')
    s = billing.summary(_user(created_days_ago=100, free_old_used=2), TODAY)
    assert s == {'signed_in': True, 'early_access': True, 'price': '$4/mo or $36/yr', 'contact': 'simon@example.com',
                 'sub_status': 'free', 'active': False, 'free_old_used': 2, 'free_old_allowance': 3,
                 'free_old_left': 1, 'account_days': 100, 'account_free_days_left': 265}
    s = billing.summary(_user(created_days_ago=500, free_old_used=7, sub_status='active'), TODAY)
    assert s['active'] is True and s['free_old_left'] == 0 and s['account_free_days_left'] == 0
