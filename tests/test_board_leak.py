# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Spec §5 invariant, board edition: no db.PRIVATE_COLUMNS byte, no owner
email/session_secret/public_key, may reach /api/board, /api/admin/reports,
or either contact email (outside the designated to/reply_to slots). Seeds
every private column with a sentinel (tests/test_intro_leak._poisoned_owner)
on a board-ELIGIBLE owner and checks every byte of every surface."""
import json

from bb import db
from tests.conftest import signed_in
from tests.test_board_db import _ago
from tests.test_intro_leak import SESSION_SECRET_SENTINEL, _blob, _poisoned_owner

B = '/blade-book/api/board'
A = '/blade-book/api/admin'
EMAIL_SENTINEL = 'leak-owner-9f3a@example.com'


def _eligible_poisoned(con):
    p = _poisoned_owner(con, email=EMAIL_SENTINEL, handle='o-guy', verified_at=db.now())
    con.execute('UPDATE knives SET created = ? WHERE id = ?', (_ago(8), p['knife_id']))
    con.execute("UPDATE knives SET seller_note = 'genuine seller note' WHERE id = ?", (p['knife_id'],))
    con.commit()
    p['sentinels']['email'] = EMAIL_SENTINEL
    return p


def test_seeding_landed_and_knife_is_on_the_board(client, con):
    p = _eligible_poisoned(con)
    j = client.get(B).get_json()
    assert j['count'] == 1 and j['knives'][0]['id'] == p['knife_id']
    assert len(p['sentinels']) >= 11


def test_board_json_has_no_private_bytes(client, con):
    p = _eligible_poisoned(con)
    body = client.get(B).data.decode()
    for col, val in p['sentinels'].items():
        assert val not in body, f'{col} leaked into /api/board'
    for key in ('owner_email', 'owner_share_email', 'session_secret', 'public_key', 'photos', 'ext', 'confidence'):
        assert f'"{key}"' not in body, key
    assert 'genuine seller note' in body                     # the public bits DO come through


def test_contact_emails_have_no_private_bytes(client, mailer, con):
    p = _eligible_poisoned(con)
    signed_in(client, mailer, email='buyer@example.com')
    con.execute('UPDATE users SET share_email_on_intro = 1'); con.commit()   # sharing is opt-in since review H4
    mailer.sent.clear()
    assert client.post(f"{B}/{p['knife_id']}/contact", json={'message': 'interested'}).status_code == 200
    assert len(mailer.sent) == 2
    for msg in mailer.sent:
        blob = _blob(msg)
        for col, val in p['sentinels'].items():
            if col == 'email':
                continue                                      # checked slot-by-slot below
            assert val not in blob, f'{col} leaked into contact email {msg!r}'
    to_owner = next(m for m in mailer.sent if m['to'] == EMAIL_SENTINEL)
    to_buyer = next(m for m in mailer.sent if m['to'] == 'buyer@example.com')
    for msg in (to_owner, to_buyer):
        for field in ('subject', 'text', 'html'):
            val = str(msg.get(field) or '')
            assert EMAIL_SENTINEL not in val and 'buyer@example.com' not in val, (field, msg)
    assert to_buyer['reply_to'] == EMAIL_SENTINEL            # both opted in → designated slot only
    assert to_owner['reply_to'] == 'buyer@example.com'


def test_contact_emails_no_addresses_when_sharing_off(client, mailer, con):
    p = _eligible_poisoned(con)
    con.execute('UPDATE users SET share_email_on_intro = 0 WHERE id = ?', (p['user']['id'],)); con.commit()
    signed_in(client, mailer, email='buyer@example.com')
    mailer.sent.clear()
    assert client.post(f"{B}/{p['knife_id']}/contact", json={}).status_code == 200
    for msg in mailer.sent:
        assert msg['reply_to'] is None
        for field in ('subject', 'text', 'html'):
            val = str(msg.get(field) or '')
            assert EMAIL_SENTINEL not in val and 'buyer@example.com' not in val


def test_admin_reports_json_has_no_private_bytes(client, mailer, con):
    p = _eligible_poisoned(con)
    reporter = signed_in(client, mailer, email='rep@example.com')
    assert client.post(f"{B}/{p['knife_id']}/report", json={'reason': 'looks off'}).status_code == 200
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (reporter['id'],)); con.commit()
    body = client.get(A + '/reports').data.decode()
    for col, val in p['sentinels'].items():
        assert val not in body, f'{col} leaked into /api/admin/reports'
    assert 'looks off' in body
