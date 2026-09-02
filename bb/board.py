# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/board.py — the For Sale board (spec §9): the public card, the contact
intro emails, and the abuse limits. Cards are publish.public_row() output
plus id/tag/handle/name/listed_at/img_t — the same whitelist the bundle and
the search index use, so tests/test_board_leak.py can prove nothing in
db.PRIVATE_COLUMNS (or an owner's email) ever reaches the browser.
"""
import os

from bb import auth, match, paths, publish

MAX_MESSAGE = 500
MAX_PER_SELLER_PER_DAY = 3      # spec §9: "≤3/day from one user to the same seller"
MAX_PER_BUYER_PER_DAY = 10      # ruling: email-storm hygiene across all sellers
MAX_REASON = 500
MIN_REASON = 3

CARD_FIELDS = ('model', 'variant', 'generation', 'size', 'born', 'born_on',
               'damascus_smith', 'damascus_pattern', 'special_edition',
               'notes_public', 'for_sale', 'asking_price', 'seller_note')


def card(k):
    """The browser-facing board card for one db.board_knife()/board_knives() row."""
    row = publish.public_row(k, {'hide_born_day': k.get('owner_hide_born_day', 0)})
    c = {f: row[f] for f in CARD_FIELDS if row.get(f) not in (None, '')}
    c['id'] = k['id']
    c['tag'] = k['tag']
    c['handle'] = k['owner_handle']
    c['name'] = publish.display_name(row)
    c['listed_at'] = k.get('listed_at') or k.get('updated') or ''
    thumb = f"{k['tag']}_t.jpg"
    if k.get('photos') and os.path.exists(os.path.join(publish.bundle_dir(k['owner_handle']), 'img', thumb)):
        c['img_t'] = thumb
    return c


def _permalink(k):
    return f"{auth.base_url()}{paths.URL_PREFIX}/@{k['owner_handle']}/{k['tag']}/"


def contact_emails(buyer, k, message):
    """(to_seller_kwargs, to_buyer_kwargs) for mailer.send(**kw). Mirrors
    bb/match._emails_for: PUBLIC card text only; reply_to only when BOTH
    sides share_email_on_intro; otherwise handle-only."""
    url = _permalink(k)
    share = bool(buyer.get('share_email_on_intro')) and bool(k.get('owner_share_email'))
    text = match.card_text(k)
    msg_block = (f"Their message (written by @{buyer['handle']} on blade-book — "
                 f"we haven't checked it):\n{message}\n\n") if message else ''
    to_seller = {
        'to': k['owner_email'],
        'subject': f"blade-book: someone on the board is interested in your {k['tag']}",
        'text': (f"@{buyer['handle']} is interested in your {k['tag']} on the board "
                 f"({text}).\n{url}\n\n{msg_block}"
                 + ("Reply to this email to reach them.\n" if share else
                    "They haven't shared a contact address. If you both turn on email "
                    "sharing in blade-book, future intros will connect you directly.\n")),
        'reply_to': buyer['email'] if share else None,
    }
    my_block = f"Your message:\n{message}\n\n" if message else ''
    to_buyer = {
        'to': buyer['email'],
        'subject': f"blade-book: we told @{k['owner_handle']} you're interested in {k['tag']}",
        'text': (f"We let @{k['owner_handle']} know you're interested in their {k['tag']} "
                 f"({text}).\n{url}\n\n{my_block}"
                 + ("Reply to this email to reach the seller.\n" if share else
                    "The seller hasn't shared a contact address. Watch their register — "
                    "and if you both turn on email sharing in blade-book, future intros "
                    "will connect you directly.\n")),
        'reply_to': k['owner_email'] if share else None,
    }
    return to_seller, to_buyer
