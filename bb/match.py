# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/match.py — wants → intro emails (spec §9). Cron */15 via scripts/match_cron.py.

One email to each side per (want, knife) pair, EVER: an intros row is
claimed (UNIQUE want_id+knife_id) before anything is sent; a failed send
leaves the claim with sent_at NULL and the next run retries it. Emails
carry PUBLIC card data only (publish.public_row + handle + permalink).
Keyword is whole-word over the spec's public text fields — see the plan
ruling for why not FTS.
"""
import logging
import re

from bb import db, publish

log = logging.getLogger('blade-book.match')

KEYWORD_FIELDS = ('graphic_name', 'inlay_material', 'damascus_smith',
                  'damascus_pattern', 'special_edition', 'notes_public')
ENUM_FIELDS = ('maker', 'model', 'blade_shape', 'blade_steel')   # knife columns
EXT_ENUM_FIELDS = ('generation', 'size')                         # in ext JSON

BASE = 'https://billboard.instockornot.club/blade-book'


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
        hay = ' '.join(str(ext.get(f) or '') if f != 'notes_public' else str(k.get(f) or '')
                       for f in KEYWORD_FIELDS).lower()
        if not re.search(r'\b' + re.escape(kw) + r'\b', hay):
            return False
    return True


def _card_text(k):
    row = publish.public_row(k, {'hide_born_day': 0})
    name = publish.display_name(row)
    bits = [f"{k['tag']} — {name}", row.get('born') or '']
    if row.get('for_sale') and row.get('asking_price'):
        bits.append(f"asking ${row['asking_price']}")
    return ' · '.join(b for b in bits if b)


def _emails_for(intro_row, want, k):
    """Build (wanter_email_kwargs, owner_email_kwargs)."""
    url = f"{BASE}/@{k['owner_handle']}/{k['tag']}/"
    share = bool(want['_wanter_share']) and bool(k['owner_share_email'])
    to_wanter = {
        'to': want['_wanter_email'],
        'subject': f"blade-book: a match for your want — {k['tag']}",
        'text': (f"A knife matching your want is on @{k['owner_handle']}'s register:\n\n"
                 f"{_card_text(k)}\n{url}\n\n"
                 + ("Reply to this email to reach the owner.\n" if share else
                    f"Reach the owner via their register page: {BASE}/@{k['owner_handle']}/\n")),
        'reply_to': k['owner_email'] if share else None,
    }
    to_owner = {
        'to': k['owner_email'],
        'subject': f"blade-book: someone wants your {k['tag']}",
        'text': (f"@{want['_wanter_handle']} has a want matching your {k['tag']} "
                 f"({_card_text(k)}).\n{url}\n\n"
                 + ("Reply to this email to reach them.\n" if share else
                    "They can reach you through your public page.\n")),
        'reply_to': want['_wanter_email'] if share else None,
    }
    return to_wanter, to_owner


def run(con, mailer):
    """One matching pass: claim new pairs, then send everything unsent
    (including claims left over from a previous failed run). Returns
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
        w, k = wants_by_id.get(intro['want_id']), by_id.get(intro['knife_id'])
        if not w or not k:      # want deactivated or knife gone since the claim
            db.mark_intro_sent(con, intro['id'], 'skipped')
            continue
        try:
            mid = 'sent'
            for kwargs in _emails_for(intro, w, k):
                mid = mailer.send(**kwargs)
                sent += 1
            db.mark_intro_sent(con, intro['id'], mid)
        except Exception as e:
            log.error('intro %s send failed: %r', intro['id'], e)
    return sent
