# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
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

Gate = namedtuple('Gate', 'ok reason charge')

REASON_ACCOUNT = ('your account is over a year old — adding knives now needs a subscription '
                  '(early access: email us and we will turn it on)')
REASON_OLD = ('this knife is older than 12 months (or undated) and your 3 free older-knife '
              'saves are used — adding it needs a subscription (early access: email us and '
              'we will turn it on)')


def price_text():
    return config.get('BLADEBOOK_PRICE_TEXT', DEFAULT_PRICE_TEXT)


def contact_email():
    """The early-access 'email us' address. Default: hello@ at the sender's domain."""
    env = config.get('BLADEBOOK_CONTACT_EMAIL')
    if env:
        return env
    domain = mail.DEFAULT_FROM.rsplit('@', 1)[1].rstrip('>').strip()
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
    """(ok, reason, charge). charge=True means the caller must spend one
    free_old_used slot on success. Pure — reads the user dict only."""
    today = _today(today)
    if is_active(user):
        return Gate(True, None, False)
    if account_days(user, today) >= FREE_DAYS:
        return Gate(False, REASON_ACCOUNT, False)
    if _knife_is_young(born_on, today):
        return Gate(True, None, False)
    if int(user.get('free_old_used') or 0) < FREE_OLD_KNIVES:
        return Gate(True, None, True)
    return Gate(False, REASON_OLD, False)


def summary(user, today=None):
    """What the pages show: price + where the caller stands. Own numbers only."""
    out = {'signed_in': user is not None, 'early_access': True, 'price': price_text(),
           'contact': contact_email()}
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
