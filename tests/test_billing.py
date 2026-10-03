# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/billing.py — the gate math (spec §10 can_add rules 1–3), the API summary,
the one-line price. Pure functions; no DB."""
import datetime as dt

import pytest

from bb import billing

TODAY = dt.date(2026, 9, 3)


@pytest.fixture
def hard(monkeypatch):
    """The paid gate ON (BLADEBOOK_HARD_GATE=1). Off by default while we are
    cooking out bugs — flip it on around 10–15 subs."""
    monkeypatch.setenv('BLADEBOOK_HARD_GATE', '1')


@pytest.fixture
def soft(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_HARD_GATE', raising=False)


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
    monkeypatch.setenv('BLADEBOOK_PRICE_TEXT', '')
    assert billing.price_text() == '$4/mo or $36/yr'


def test_account_days():
    assert billing.account_days(_user(created_days_ago=0), TODAY) == 0
    assert billing.account_days(_user(created_days_ago=400), TODAY) == 400


@pytest.mark.parametrize('born', ['2026-06-01', '2025-09-04', '2026', '2026-09'])
def test_rule2_young_knife_is_free_for_a_young_account(born):
    g = billing.can_add(_user(), born, TODAY)
    assert g == (True, None, False, None)


@pytest.mark.parametrize('born', ['2025-09-03', '2008-03-14', '1999', None, '', 'unknown'])
def test_rule3_old_or_undated_knife_spends_a_free_slot(hard, born):
    for used in (0, 1, 2):
        g = billing.can_add(_user(free_old_used=used), born, TODAY)
        assert g.ok and g.charge and g.reason is None and g.notice is None, (born, used)
    g = billing.can_add(_user(free_old_used=3), born, TODAY)
    assert not g.ok and not g.charge
    assert 'older than 12 months' in g.reason and '3 free' in g.reason and 'subscription' in g.reason


def test_rule3_active_sub_never_charges_a_slot():
    g = billing.can_add(_user(free_old_used=3, sub_status='active'), '2008-03-14', TODAY)
    assert g == (True, None, False, None)
    g = billing.can_add(_user(free_old_used=0, sub_status='active'), '2008-03-14', TODAY)
    assert g == (True, None, False, None)    # a subscriber's slots are never spent


def test_rule1_account_over_a_year_needs_active_even_for_a_young_knife(hard):
    g = billing.can_add(_user(created_days_ago=365), '2026-09-01', TODAY)
    assert not g.ok and not g.charge and 'over a year' in g.reason and 'subscription' in g.reason
    assert billing.can_add(_user(created_days_ago=364), '2026-09-01', TODAY).ok
    assert billing.can_add(_user(created_days_ago=800, sub_status='active'), '2008-01-01', TODAY) == (True, None, False, None)


def test_lapsed_is_not_active(hard):
    assert not billing.is_active(_user(sub_status='lapsed'))
    assert not billing.can_add(_user(created_days_ago=400, sub_status='lapsed'), '2026-09-01', TODAY).ok
    assert not billing.can_add(_user(free_old_used=3, sub_status='lapsed'), '2008-01-01', TODAY).ok


def test_no_admin_bypass_in_the_gate_math(hard):
    u = _user(free_old_used=3); u['is_admin'] = 1
    assert not billing.can_add(u, '2008-01-01', TODAY).ok


def test_soft_gate_is_the_default_until_we_flip_it(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_HARD_GATE', raising=False)
    assert billing.hard_gate() is False
    for v in ('1', 'true', 'yes', 'on'):
        monkeypatch.setenv('BLADEBOOK_HARD_GATE', v)
        assert billing.hard_gate() is True, v
    for v in ('', '0', 'false', 'no', 'off'):
        monkeypatch.setenv('BLADEBOOK_HARD_GATE', v)
        assert billing.hard_gate() is False, v


@pytest.mark.parametrize('born', ['2008-03-14', None])
def test_soft_gate_lets_a_fourth_old_knife_in_with_a_notice_and_still_counts_it(soft, born):
    g = billing.can_add(_user(free_old_used=3), born, TODAY)
    assert g.ok and g.reason is None
    assert g.charge, 'the free ride still ticks the counter so the hard gate is right when we flip it'
    assert 'free during early access' in g.notice.lower() and 'tell you before' in g.notice.lower()
    assert 'subscription' not in g.notice and '3 free' not in g.notice     # no meter, no bill (2026-09-26)
    # slots 1–3 are ordinary free saves: no notice
    assert billing.can_add(_user(free_old_used=2), born, TODAY).notice is None


def test_soft_gate_lets_an_over_a_year_account_in_with_a_notice(soft):
    g = billing.can_add(_user(created_days_ago=400), '2026-09-01', TODAY)
    assert g.ok and g.reason is None and not g.charge
    assert 'free during early access' in g.notice.lower()
    g = billing.can_add(_user(created_days_ago=400, free_old_used=3), '2008-01-01', TODAY)
    assert g.ok and g.charge and g.notice


def test_soft_gate_never_touches_young_knives_or_subscribers(soft):
    assert billing.can_add(_user(), '2026-06-01', TODAY) == (True, None, False, None)
    assert billing.can_add(_user(free_old_used=9, sub_status='active'), '2008-01-01', TODAY) == (True, None, False, None)


def test_contact_email_is_one_config_line(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_CONTACT_EMAIL', raising=False)
    monkeypatch.delenv('MAIL_FROM', raising=False)
    assert billing.contact_email() == 'hello@' + 'blade-book.com'      # derived from mail.DEFAULT_FROM's domain
    monkeypatch.setenv('BLADEBOOK_CONTACT_EMAIL', 'simon@example.com')
    assert billing.contact_email() == 'simon@example.com'
    monkeypatch.delenv('BLADEBOOK_CONTACT_EMAIL', raising=False)
    monkeypatch.setenv('MAIL_FROM', 'shop <x@example.com>')
    assert billing.contact_email() == 'hello@example.com'


def test_summary_anonymous(monkeypatch, soft):
    monkeypatch.delenv('BLADEBOOK_PRICE_TEXT', raising=False)
    monkeypatch.delenv('BLADEBOOK_CONTACT_EMAIL', raising=False)
    assert billing.summary(None) == {'signed_in': False, 'early_access': True, 'hard_gate': False,
                                     'price': '$4/mo or $36/yr', 'contact': 'hello@' + 'blade-book.com'}
    monkeypatch.setenv('BLADEBOOK_HARD_GATE', '1')
    assert billing.summary(None)['hard_gate'] is True


def test_summary_signed_in(monkeypatch, soft):
    monkeypatch.delenv('BLADEBOOK_PRICE_TEXT', raising=False)
    monkeypatch.setenv('BLADEBOOK_CONTACT_EMAIL', 'simon@example.com')
    s = billing.summary(_user(created_days_ago=100, free_old_used=2), TODAY)
    assert s == {'signed_in': True, 'early_access': True, 'hard_gate': False, 'price': '$4/mo or $36/yr',
                 'contact': 'simon@example.com',
                 'sub_status': 'free', 'active': False, 'free_old_used': 2, 'free_old_allowance': 3,
                 'free_old_left': 1, 'account_days': 100, 'account_free_days_left': 265}
    s = billing.summary(_user(created_days_ago=500, free_old_used=7, sub_status='active'), TODAY)
    assert s['active'] is True and s['free_old_left'] == 0 and s['account_free_days_left'] == 0
