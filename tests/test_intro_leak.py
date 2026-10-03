# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Spec §9/§5: bb/match.py's intro emails are the OUTBOUND email surface —
no db.PRIVATE_COLUMNS byte, and no owner session_secret, may ever leave in a
sent message's to/subject/text/html/reply_to. Email addresses only appear
where BOTH sides' share_email_on_intro consent allows it. A key-gated
owner's knife is never matched at all (insurance beyond test_search.py's
gating logic tests). Mirrors tests/test_search_leak.py's sentinel-seeding
approach and needle style."""
import json

from bb import db, match
from bb.mail import FakeMailer
from tests.test_search import _mk_knife, _mk_user
from tests.test_wants_db import _u

SESSION_SECRET_SENTINEL = 'LEAK-SESSIONSECRET-9F3A'


def _sentinel(col):
    return f'LEAK-{col.upper()}-9F3A'


def _poisoned_owner(con, email='o@example.com', handle='o-guy', **over):
    """A for_sale-knife owner: every db.PRIVATE_COLUMNS knife column, plus
    the owner's session_secret, carry a sentinel value."""
    u = _mk_user(con, email=email, handle=handle, **over)
    k = _mk_knife(con, u['id'], sale_status='for_sale', asking_price=500)
    sentinels = {}
    for col in sorted(db.PRIVATE_COLUMNS):
        val = _sentinel(col)
        if col == 'confidence':               # json-typed column: must stay valid JSON
            con.execute('UPDATE knives SET confidence = ? WHERE id = ?',
                        (json.dumps({'leak': val}), k['id']))
        else:
            con.execute(f'UPDATE knives SET {col} = ? WHERE id = ?', (val, k['id']))
        sentinels[col] = val
    con.execute('UPDATE users SET session_secret = ? WHERE id = ?',
                (SESSION_SECRET_SENTINEL, u['id']))
    con.commit()
    sentinels['session_secret'] = SESSION_SECRET_SENTINEL
    return {'user': db.get_user(con, u['id']), 'knife_id': k['id'], 'sentinels': sentinels}


def _blob(msg):
    """Every byte a sent message could carry, concatenated."""
    return ' '.join(str(msg.get(f) or '') for f in ('to', 'subject', 'text', 'html', 'reply_to'))


def test_sentinels_actually_landed_in_the_db(con):
    """Prove the seeding worked before trusting the negative assertions below."""
    p = _poisoned_owner(con)
    assert len(p['sentinels']) >= 10   # 9 PRIVATE_COLUMNS + session_secret
    row = dict(con.execute('SELECT * FROM knives WHERE id = ?', (p['knife_id'],)).fetchone())
    for col, val in p['sentinels'].items():
        if col == 'session_secret':
            continue
        assert val in repr(row.get(col)), f'{col} sentinel missing from seeded knife row'
    u = dict(con.execute('SELECT * FROM users WHERE id = ?', (p['user']['id'],)).fetchone())
    assert u['session_secret'] == SESSION_SECRET_SENTINEL


def test_no_private_bytes_in_any_sent_intro_email(con):
    p = _poisoned_owner(con)
    wanter = _u(con)
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    n = match.run(con, m)
    assert n == 2   # both sides fire — this pair actually matched
    values = list(p['sentinels'].values())
    for msg in m.sent:
        blob = _blob(msg)
        for val in values:
            assert val not in blob, f'{val} leaked into intro email {msg!r}'


def test_owner_email_appears_only_in_designated_slots_when_sharing_on(con):
    """Both users opted in to share_email_on_intro: the owner's email may
    appear ONLY as the wanter message's reply_to, and as the to/reply_to of
    the owner's own message — never in a subject/text/html body."""
    owner = _u(con, email='o@example.com', handle='o-guy')
    _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=500)
    wanter = _u(con)
    con.execute('UPDATE users SET share_email_on_intro = 1'); con.commit()   # sharing is opt-in since review H4
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    to_wanter = next(x for x in m.sent if x['to'] == wanter['email'])
    to_owner = next(x for x in m.sent if x['to'] == owner['email'])
    assert to_wanter['reply_to'] == owner['email']
    assert to_owner['reply_to'] == wanter['email']
    for msg in m.sent:
        for field in ('subject', 'text', 'html'):
            val = str(msg.get(field) or '')
            assert owner['email'] not in val, (field, msg)
            assert wanter['email'] not in val, (field, msg)


def test_no_email_address_anywhere_when_sharing_off(con):
    """A fresh pair with BOTH share_email_on_intro=0: neither party's email
    address appears in the other's message body anywhere, and reply_to is
    None for both."""
    owner = _u(con, email='o2@example.com', handle='o2-guy', share_email_on_intro=0)
    _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=500)
    wanter = _u(con, email='w2@example.com', handle='w2-guy', share_email_on_intro=0)
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    for msg in m.sent:
        assert msg['reply_to'] is None
        for field in ('subject', 'text', 'html'):
            val = str(msg.get(field) or '')
            assert owner['email'] not in val, (field, msg)
            assert wanter['email'] not in val, (field, msg)


def test_gated_owner_knife_never_matched_zero_emails_zero_intros(con):
    owner = _u(con, email='g@example.com', handle='g-guy', public_key='GATEKEY123')
    _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=500)
    wanter = _u(con, email='w3@example.com', handle='w3-guy')
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    assert match.run(con, m) == 0
    assert m.sent == []
    assert con.execute('SELECT COUNT(*) FROM intros').fetchone()[0] == 0
