# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""
bb/match.py — wants → intro emails (spec §9). Cron */15 via scripts/match_cron.py.

One email to each side per (want, knife) pair, EVER: an intros row is
claimed (db.claim_intro — deduped on from_user+knife_id, UNIQUE on
want_id+knife_id) before anything is sent; a failed send leaves the claim
with sent_at NULL and the next run retries it. Emails carry PUBLIC card
data only (publish.public_row + handle + permalink). Keyword is whole-word
over the spec's public text fields — see the plan ruling for why not FTS.

Two abuse/correctness guards live here rather than in db.py:
- MAX_EMAILS_PER_RUN bounds a single pass's outbound volume; anything past
  the cap stays claimed-but-unsent and drains over later runs.
- run() is flock-serialized (mirrors bb/publish.py's build_user precedent)
  so an overlapping cron run SKIPS (returns 0) instead of racing the send
  loop of a run already in flight — the UNIQUE claim alone is not enough,
  because two runs can both see the same claim as sent_at NULL right up
  until the first one's mark_intro_sent commits.
"""
import fcntl
import logging
import os
import re

from bb import auth, db, mail, paths, publish

log = logging.getLogger('blade-book.match')

KEYWORD_FIELDS = ('graphic_name', 'inlay_material', 'damascus_smith',
                  'damascus_pattern', 'special_edition', 'notes_public')
ENUM_FIELDS = ('maker', 'model', 'blade_shape', 'blade_steel')   # knife columns
EXT_ENUM_FIELDS = ('generation', 'size')                         # in ext JSON

# Per-run outbound cap (spec §5/§9 abuse-rate bound — see final-review
# finding 2): an account at the wants cap with broad wants x a large public
# inventory can otherwise generate thousands of claims in one pass. Claims
# beyond the cap stay in the sent_at NULL lane and drain on later */15 runs.
MAX_EMAILS_PER_RUN = 40


def _base():
    return auth.base_url() + paths.URL_PREFIX


def _lock_path():
    lock_dir = os.path.join(paths.DATA_DIR, 'publish-locks')
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, 'match.lock')


def _born_year(k):
    m = re.match(r'^(\d{4})', k.get('born_on') or '')
    return int(m.group(1)) if m else None


def knife_matches(want, k):
    ext = k.get('ext') or {}
    for f in ENUM_FIELDS:
        if want.get(f) and (k.get(f) or '').lower() != want[f].lower():
            return False
    for f in EXT_ENUM_FIELDS:
        if want.get(f) and (ext.get(f) or '').lower() != want[f].lower():
            return False
    mode = want.get('mode') or 'either'
    ss = k.get('sale_status')
    if mode == 'trade' and ss != 'for_trade':
        return False
    if mode == 'sale' and ss != 'for_sale':
        return False
    if mode == 'either' and ss not in ('for_trade', 'for_sale'):
        return False
    if want.get('max_price') and ss == 'for_sale':
        if not k.get('asking_price') or k['asking_price'] > want['max_price']:
            return False
    y = _born_year(k)
    if want.get('born_from') and (y is None or y < want['born_from']):
        return False
    if want.get('born_to') and (y is None or y > want['born_to']):
        return False
    kw = (want.get('keyword') or '').strip().lower()
    if kw:
        pattern = r'\b' + re.escape(kw) + r'\b'
        fields = (str(ext.get(f) or '') if f != 'notes_public' else str(k.get(f) or '')
                  for f in KEYWORD_FIELDS)
        if not any(re.search(pattern, field.lower()) for field in fields):
            return False
    return True


def _fmt_price(p):
    p = float(p)
    return f"${int(p)}" if p.is_integer() else f"${p}"


def card_text(k, with_tag=True):
    row = publish.public_row(k, {'hide_born_day': k.get('owner_hide_born_day', 0)})
    name = publish.display_name(row)
    bits = [f"{k['tag']} — {name}" if with_tag else name, row.get('born') or '']
    if row.get('for_sale') and row.get('asking_price'):
        bits.append(f"asking {_fmt_price(row['asking_price'])}")
    return ' · '.join(b for b in bits if b)


_card_text = card_text   # kept for tests/test_match.py


def _emails_for(intro_row, want, k):
    """Build (wanter_email_kwargs, owner_email_kwargs)."""
    url = f"{_base()}/@{k['owner_handle']}/{k['tag']}/"
    share = (bool(want['_wanter_share']) and bool(k['owner_share_email'])
             and mail.deliverable(want['_wanter_email']) and mail.deliverable(k['owner_email']))
    to_wanter = {
        'to': want['_wanter_email'],
        'subject': f"blade-book: a match for your want — {k['tag']}",
        'text': (f"A knife matching your want is on @{k['owner_handle']}'s register:\n\n"
                 f"{card_text(k)}\n{url}\n\n"
                 + ("Reply to this email to reach the owner.\n" if share else
                    "The owner hasn't shared a contact address. Watch their register — "
                    "and if you both turn on email sharing in blade-book, future intros "
                    "will connect you directly.\n")),
        'reply_to': k['owner_email'] if share else None,
    }
    to_owner = {
        'to': k['owner_email'],
        'subject': f"blade-book: someone wants your {k['tag']}",
        'text': (f"@{want['_wanter_handle']} has a want matching your {k['tag']} "
                 f"({card_text(k, with_tag=False)}).\n{url}\n\n"
                 + ("Reply to this email to reach them.\n" if share else
                    "The wanter hasn't shared a contact address. If you both turn on "
                    "email sharing in blade-book, future intros will connect you "
                    "directly.\n")),
        'reply_to': want['_wanter_email'] if share else None,
    }
    return to_wanter, to_owner


def run(con, mailer):
    """One matching pass, serialized end-to-end via an flock so an
    overlapping cron run skips (returns 0) instead of double-sending."""
    lockf = open(_lock_path(), 'w')
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log.warning('match.run: another run holds the lock — skipping this pass')
        lockf.close()
        return 0
    try:
        return _run_locked(con, mailer)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


def _run_locked(con, mailer):
    """Claim new pairs, then send everything unsent (including claims left
    over from a previous failed run), up to MAX_EMAILS_PER_RUN. Returns
    emails successfully sent."""
    knives = db.all_public_knives(con)
    wants = db.active_wants(con)
    users = {}
    for w in wants:
        if w['owner_id'] not in users:
            users[w['owner_id']] = db.get_user(con, w['owner_id'])
        u = users[w['owner_id']]
        w['_wanter_email'], w['_wanter_handle'] = u['email'], u['handle']
        w['_wanter_share'] = u['share_email_on_intro']
        for k in knives:
            if k['owner_id'] == w['owner_id']:
                continue
            if knife_matches(w, k):
                db.claim_intro(con, w['id'], k['id'], w['owner_id'], k['owner_id'])
    sent = 0
    by_id = {k['id']: k for k in knives}
    wants_by_id = {w['id']: w for w in wants}
    for intro in db.unsent_intros(con):
        if sent >= MAX_EMAILS_PER_RUN:
            break
        w, k = wants_by_id.get(intro['want_id']), by_id.get(intro['knife_id'])
        if not w or not k:      # want deactivated or knife gone since the claim
            db.mark_intro_sent(con, intro['id'], 'skipped')
            continue
        try:
            mid = 'sent'
            for kwargs in _emails_for(intro, w, k):
                if not mail.deliverable(kwargs['to']):   # an invited (no-email) account: skip that leg
                    continue
                mid = mailer.send(**kwargs)
                sent += 1
            db.mark_intro_sent(con, intro['id'], mid)
        except Exception as e:
            log.error('intro %s send failed: %r', intro['id'], e)
    return sent
