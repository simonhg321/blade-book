# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""GET /api/board + POST /api/board/<id>/contact (plan 09)."""
from bb import db
from tests.conftest import signed_in
from tests.test_board_db import _old_knife, _seller
from tests.test_search import _mk_knife, _mk_user

B = '/blade-book/api/board'


def _listing(con, **over):
    """A board-eligible seller with one for_sale knife; returns (seller, knife)."""
    s = _seller(con, **over)
    _old_knife(con, s['id'])
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500, seller_note='mint')
    return s, k


def test_board_list_is_public_and_paged(client, con):
    s, k = _listing(con)
    k2 = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=700, listed_at='2027-01-01T00:00:00+00:00')
    j = client.get(B).get_json()
    assert j['count'] == 2 and [x['id'] for x in j['knives']] == [k2['id'], k['id']]
    assert j['knives'][1]['handle'] == 's-guy' and j['knives'][1]['asking_price'] == 500
    assert j['knives'][1]['seller_note'] == 'mint' and j['knives'][1]['name'] == 'Large Sebenza 21'
    j = client.get(B + '?limit=1&offset=1').get_json()
    assert j['count'] == 2 and [x['id'] for x in j['knives']] == [k['id']] and j['limit'] == 1 and j['offset'] == 1
    assert client.get(B + '?limit=0').get_json()['limit'] == 1
    assert client.get(B + '?limit=999').get_json()['limit'] == 48
    assert client.get(B + '?limit=abc').status_code == 400
    assert client.get(B + '?offset=-1').status_code == 400


def test_board_list_empty(client):
    assert client.get(B).get_json() == {'count': 0, 'knives': [], 'limit': 24, 'offset': 0}


def test_contact_sends_both_sides_and_records_claim(client, mailer, con):
    s, k = _listing(con)
    buyer = signed_in(client, mailer, email='buyer@example.com')
    con.execute('UPDATE users SET share_email_on_intro = 1'); con.commit()   # sharing is opt-in since review H4
    mailer.sent.clear()
    r = client.post(f"{B}/{k['id']}/contact", json={'message': 'Would you take 450?'})
    assert r.status_code == 200 and r.get_json() == {'ok': True}
    assert len(mailer.sent) == 2
    to_seller = next(m for m in mailer.sent if m['to'] == 's@example.com')
    to_buyer = next(m for m in mailer.sent if m['to'] == 'buyer@example.com')
    assert k['tag'] in to_seller['subject'] and 'Would you take 450?' in to_seller['text']
    assert f"@{buyer['handle']}" in to_seller['text'] and '/@s-guy/' in to_seller['text']
    assert 'Would you take 450?' in to_buyer['text'] and '@s-guy' in to_buyer['subject']
    assert to_seller['reply_to'] == 'buyer@example.com' and to_buyer['reply_to'] == 's@example.com'   # both opted in above
    rows = [dict(r) for r in con.execute("SELECT * FROM intros WHERE kind = 'board'")]
    assert len(rows) == 1 and rows[0]['want_id'] is None and rows[0]['message'] == 'Would you take 450?'
    assert rows[0]['from_user'] == buyer['id'] and rows[0]['to_user'] == s['id'] and rows[0]['sent_at']


def test_contact_without_message_and_no_sharing(client, mailer, con):
    s, k = _listing(con, share_email_on_intro=0)
    signed_in(client, mailer, email='buyer@example.com')
    mailer.sent.clear()
    assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 200
    assert client.post(f"{B}/{k['id']}/contact").status_code == 200        # no body at all is fine
    for m in mailer.sent:
        assert m['reply_to'] is None and 'message:' not in m['text'].lower()


def test_contact_validation_and_not_found(client, mailer, con):
    s, k = _listing(con)
    assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 401
    signed_in(client, mailer, email='buyer@example.com')
    assert client.post(f"{B}/{k['id']}/contact", json={'message': 'x' * 501}).status_code == 400
    assert client.post(f"{B}/{k['id']}/contact", json={'message': 5}).status_code == 400
    assert client.post(f"{B}/{k['id']}/contact", json=[1]).status_code == 400
    assert client.post(f"{B}/999999/contact", json={}).status_code == 404
    keeping = _mk_knife(con, s['id'])
    assert client.post(f"{B}/{keeping['id']}/contact", json={}).status_code == 404   # not on the board
    db.hide_knife(con, k['id'], 'admin', 'x')
    assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 404         # hidden → not on the board


def test_contact_own_knife_is_400(client, mailer, con):
    me = signed_in(client, mailer, email='own@example.com')
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],)); con.commit()
    k = _mk_knife(con, me['id'], sale_status='for_sale', asking_price=5)
    assert client.get(B).get_json()['count'] == 1
    r = client.post(f"{B}/{k['id']}/contact", json={})
    assert r.status_code == 400 and 'your own' in r.get_json()['error']


def test_contact_daily_limits(client, mailer, con):
    from bb import board
    s, _ = _listing(con)
    ks = [_mk_knife(con, s['id'], sale_status='for_sale', asking_price=5) for _ in range(board.MAX_PER_SELLER_PER_DAY + 1)]
    signed_in(client, mailer, email='buyer@example.com')
    for k in ks[:board.MAX_PER_SELLER_PER_DAY]:
        assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 200
    r = client.post(f"{B}/{ks[-1]['id']}/contact", json={})
    assert r.status_code == 429 and 'seller' in r.get_json()['error']
    # a claim older than 24 h no longer counts
    con.execute("UPDATE intros SET created = '2020-01-01T00:00:00+00:00' WHERE kind = 'board'"); con.commit()
    assert client.post(f"{B}/{ks[-1]['id']}/contact", json={}).status_code == 200
    # per-buyer total across sellers
    con.execute("UPDATE intros SET created = '2020-01-01T00:00:00+00:00' WHERE kind = 'board'"); con.commit()
    sellers = []
    for i in range(board.MAX_PER_BUYER_PER_DAY):
        sx = _seller(con, email=f'x{i}@example.com', handle=f'x{i}-guy')
        _old_knife(con, sx['id'])
        sellers.append(_mk_knife(con, sx['id'], sale_status='for_sale', asking_price=5))
    for k in sellers:
        assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 200
    sy = _seller(con, email='y@example.com', handle='y-guy'); _old_knife(con, sy['id'])
    ky = _mk_knife(con, sy['id'], sale_status='for_sale', asking_price=5)
    r = client.post(f"{B}/{ky['id']}/contact", json={})
    assert r.status_code == 429 and 'today' in r.get_json()['error']


def test_contact_send_failure_deletes_claim_and_returns_502(client, mailer, con, monkeypatch):
    s, k = _listing(con)
    signed_in(client, mailer, email='buyer@example.com')

    def boom(**kw):
        raise RuntimeError('resend down')
    monkeypatch.setattr(mailer, 'send', boom)
    r = client.post(f"{B}/{k['id']}/contact", json={'message': 'hi'})
    assert r.status_code == 502
    assert con.execute("SELECT count(*) FROM intros WHERE kind = 'board'").fetchone()[0] == 0
    monkeypatch.undo()
    assert client.post(f"{B}/{k['id']}/contact", json={'message': 'hi'}).status_code == 200   # retry works


def test_board_json_never_carries_owner_email(client, con):
    _listing(con)
    body = client.get(B).data.decode()
    assert 's@example.com' not in body and 'owner_email' not in body and 'session_secret' not in body


def test_contact_partial_send_keeps_claim_and_quota(client, mailer, con, monkeypatch):
    """Seller leg landed, buyer copy failed: the intro happened, so the claim
    stays (sent_at set, 'partial') and counts toward the buyer's quota."""
    s, k = _listing(con)
    signed_in(client, mailer, email='buyer@example.com')
    real = mailer.send
    calls = []

    def flaky(**kw):
        calls.append(kw['to'])
        if kw['to'] == 'buyer@example.com':
            raise RuntimeError('buyer bounce')
        return real(**kw)
    monkeypatch.setattr(mailer, 'send', flaky)
    r = client.post(f"{B}/{k['id']}/contact", json={'message': 'hi'})
    assert r.status_code == 200 and r.get_json() == {'ok': True, 'copy': False}
    assert calls == ['s@example.com', 'buyer@example.com']          # seller first, then the copy
    rows = [dict(x) for x in con.execute("SELECT * FROM intros WHERE kind = 'board'")]
    assert len(rows) == 1 and rows[0]['sent_at'] and rows[0]['resend_msg_id'] == 'partial'
    assert db.board_contacts_since(con, rows[0]['from_user'], '2000-01-01T00:00:00+00:00', to_user=s['id']) == 1


def test_contact_no_reply_to_when_only_buyer_shares_off(client, mailer, con):
    """share is an AND: seller shares, buyer does not → no reply_to either way."""
    s, k = _listing(con)                                   # seller shares (default 1)
    me = signed_in(client, mailer, email='quiet@example.com')
    con.execute('UPDATE users SET share_email_on_intro = 0 WHERE id = ?', (me['id'],)); con.commit()
    mailer.sent.clear()
    assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 200
    assert len(mailer.sent) == 2
    for m in mailer.sent:
        assert m['reply_to'] is None
        assert 'quiet@example.com' not in m['text'] and 's@example.com' not in m['text']
