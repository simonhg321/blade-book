# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/billing.py — the gate (spec §10) and the Billing interface (spec §11).

can_add(user, born_on) is the ONE write gate in the product, called from
POST /knives/<id>/save (draft → live — the first moment born_on is known):

  1. account age ≥ FREE_DAYS            → needs sub_status == 'active'
  2. else knife born within FREE_DAYS   → free
  3. else (older or undated)            → free while free_old_used < FREE_OLD_KNIVES
                                          (the save spends a slot), then needs 'active'

Never gated: editing, publishing, wants, board, export, delete. A live knife
never locks. Paid search filters are the only READ gate (bb/routes/search.py).
No admin bypass here — Simon is flipped 'active' by scripts/sub.py.

SOFT GATE (early access, 2026-09-25; opened fully 2026-09-26): while the hard
gate is off blade-book is FREE AND OPEN. A save that would have been refused
goes through with Gate.notice set (one friendly line, no meter) and still
spends a slot, so the counter is exact the day we flip the wall on. The read
gate (search filters) and the free decode cap are open too — see hard_gate()
callers. Flip: BLADEBOOK_HARD_GATE=1 in /etc/blade-book/.env + restart —
Simon's call, "once we get 50 people we can think about charging".

Billing impls share one method, set_status(con, user_id, status). v1 is
ManualBilling (an admin flips the column). StripeBilling later: Checkout
Session + signature-verified, idempotent webhook — same interface.
"""
import datetime as dt
from collections import namedtuple

from bb import config, db, mail
from bb.makers import core

FREE_OLD_KNIVES = 3          # free "older knife" saves per account, ever
FREE_DAYS = 365              # knife-age window AND account-age backstop (spec §10)
DEFAULT_PRICE_TEXT = '$4/mo or $36/yr'
ACTIVE = 'active'

Gate = namedtuple('Gate', 'ok reason charge notice', defaults=(None,))

REASON_ACCOUNT = ('your account is over a year old — adding knives now needs a subscription '
                  '(early access: email us and we will turn it on)')
REASON_OLD = ('this knife is older than 12 months (or undated) and your 3 free older-knife '
              'saves are used — adding it needs a subscription (early access: email us and '
              'we will turn it on)')
# Early access copy (2026-09-26, Simon: "free and open for now … once we get 50 people we
# can think about charging"). No meter, no bill, no nag: one friendly line.
NOTICE_ACCOUNT = ('blade-book is free during early access. We will tell you before that changes.')
NOTICE_OLD = ('blade-book is free during early access. We will tell you before that changes.')
TRUE_WORDS = ('1', 'true', 'yes', 'on')


def hard_gate():
    """False (the default) = early access: refusals become notices. Set
    BLADEBOOK_HARD_GATE=1 to make can_add refuse for real."""
    return (config.get('BLADEBOOK_HARD_GATE') or '').strip().lower() in TRUE_WORDS


def price_text():
    return config.get('BLADEBOOK_PRICE_TEXT') or DEFAULT_PRICE_TEXT


def contact_email():
    """The early-access 'email us' address. Default: hello@ at MAIL_FROM's domain
    (falling back to mail.DEFAULT_FROM's domain when MAIL_FROM is unset)."""
    env = config.get('BLADEBOOK_CONTACT_EMAIL')
    if env:
        return env
    domain = config.get('MAIL_FROM') or mail.DEFAULT_FROM
    domain = domain.rsplit('@', 1)[1].rstrip('>').strip()
    return 'hello@' + domain


def is_active(user):
    return bool(user) and user.get('sub_status') == ACTIVE


def _today(today):
    return today or dt.datetime.now(dt.timezone.utc).date()


def account_days(user, today=None):
    created = dt.datetime.fromisoformat(user['created']).date()
    return (_today(today) - created).days


def _knife_is_young(born_on, today):
    born = core.born_date(born_on)
    return born is not None and (today - born).days < FREE_DAYS


def can_add(user, born_on, today=None):
    """(ok, reason, charge, notice). charge=True means the caller must spend
    one free_old_used slot on success. notice is set only when the soft gate
    let through a save the hard gate would refuse. Pure — reads the user
    dict and the BLADEBOOK_HARD_GATE flag only."""
    today = _today(today)
    if is_active(user):
        return Gate(True, None, False)
    if account_days(user, today) >= FREE_DAYS:
        if hard_gate():
            return Gate(False, REASON_ACCOUNT, False)
        old = not _knife_is_young(born_on, today)
        return Gate(True, None, old, NOTICE_ACCOUNT)
    if _knife_is_young(born_on, today):
        return Gate(True, None, False)
    if int(user.get('free_old_used') or 0) < FREE_OLD_KNIVES:
        return Gate(True, None, True)
    if hard_gate():
        return Gate(False, REASON_OLD, False)
    return Gate(True, None, True, NOTICE_OLD)


def summary(user, today=None):
    """What the pages show: price + where the caller stands. Own numbers only."""
    out = {'signed_in': user is not None, 'early_access': True, 'hard_gate': hard_gate(),
           'price': price_text(), 'contact': contact_email()}
    if user is None:
        return out
    days = account_days(user, today)
    used = int(user.get('free_old_used') or 0)
    out.update({'sub_status': user['sub_status'], 'active': is_active(user),
                'free_old_used': used, 'free_old_allowance': FREE_OLD_KNIVES,
                'free_old_left': max(0, FREE_OLD_KNIVES - used),
                'account_days': days, 'account_free_days_left': max(0, FREE_DAYS - days)})
    return out


class ManualBilling:
    """v1: an admin flips sub_status (admin page or scripts/sub.py)."""
    name = 'manual'

    def set_status(self, con, user_id, status):
        return db.set_sub_status(con, user_id, status, source=self.name)


def from_env():
    return ManualBilling()
