# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Regression tests for security review 2026-09-04, batch A
(docs/SECURITY-REVIEW-2026-09-04.md: H1–H5, M1, L4–L6). Each test is the
reviewer's repro, inverted: it now asserts the safe behaviour."""
import datetime as dt

from bb import db, decode, edit, publish
from bb.routes import knives as kr
from tests.conftest import signed_in
from tests.test_board_db import _old_knife, _seller
from tests.test_decode_api import _draft_with_photo
from tests.test_search import _mk_knife, _mk_user

K = '/blade-book/api/knives'
B = '/blade-book/api/board'
W = '/blade-book/api/wants'


# --- H1: decode quota counts ATTEMPTS and survives knife deletion ---------------

def test_decode_cap_survives_knife_deletion(client, mailer, decoder, monkeypatch):
    monkeypatch.setenv('BLADEBOOK_HARD_GATE', '1')   # the free cap under test; early access lifts it
    monkeypatch.setattr(db, 'DECODES_PER_MINUTE', 10 ** 6)          # the daily cap is what's under test
    kid = _draft_with_photo(client, mailer)
    for _ in range(kr.FREE_DECODES_PER_DAY):
        assert client.post(f'{K}/{kid}/decode').status_code == 200
    assert client.post(f'{K}/{kid}/decode').status_code == 429
    assert client.delete(f'{K}/{kid}').status_code == 200
    kid2 = _draft_with_photo(client, mailer)
    assert client.post(f'{K}/{kid2}/decode').status_code == 429      # the day's budget is spent, knife or no knife
    assert len(decoder.calls) == kr.FREE_DECODES_PER_DAY


def test_failed_decodes_count_toward_cap(client, mailer, app, monkeypatch):
    monkeypatch.setenv('BLADEBOOK_HARD_GATE', '1')   # the free cap under test; early access lifts it
    monkeypatch.setattr(db, 'DECODES_PER_MINUTE', 10 ** 6)
    app.config['DECODER'] = decode.FakeDecoder(decode.DecodeError('model refused'))
    kid = _draft_with_photo(client, mailer)
    for _ in range(kr.FREE_DECODES_PER_DAY):
        assert client.post(f'{K}/{kid}/decode').status_code == 502
    assert client.post(f'{K}/{kid}/decode').status_code == 429       # a billed failure is still billed
    assert db.decodes_today(db.connect(), 1) == kr.FREE_DECODES_PER_DAY


# --- H2: born_on is a real calendar date ---------------------------------------

def test_bad_month_rejected_and_formatter_defensive(client, mailer, con):
    u = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    for bad in ('2025-99-99', '2025-13', '2025-02-30', '2025-00-01'):
        r = client.patch(f'{K}/{kid}', json={'born_on': bad})
        assert r.status_code == 400 and 'born_on' in r.get_json()['error'], bad
    assert client.patch(f'{K}/{kid}', json={'born_on': '2024-02-29'}).status_code == 200
    # belt and braces: a bad value already in the DB never takes the board down
    assert publish._fmt_born('2025-99-99', 'month') == '2025-99-99'
    assert publish._fmt_born('2025-13-', 'day') == '2025-13-'
    assert publish._fmt_born('2025-02-20', 'day') == 'February 20, 2025'
    assert u['id'] == 1


# --- H3: decoder output is re-validated before it is written -------------------

def test_apply_decode_revalidates_core(con):
    from tests.conftest import ok_result
    u = _mk_user(con)
    k = db.create_draft_knife(con, u['id'])
    d = decode._to_decoded(ok_result(born_on='2025-13-', blade_length_in=999), 'crk', 'fake')
    row = db.apply_decode(con, u['id'], k['id'], d)
    assert row['born_on'] is None and row['blade_length_in'] is None
    assert 'born_on' in row['decode_note'] and 'dropped' in row['decode_note']


# --- H4: wants need a criterion; email sharing is opt-in -----------------------

def test_blank_want_is_rejected(client, mailer):
    signed_in(client, mailer)
    assert client.post(W + '/', json={}).status_code == 400                      # L6: was a 500
    r = client.post(W + '/', json={'mode': 'either'})
    assert r.status_code == 400 and 'what' in r.get_json()['error']
    assert client.post(W + '/', json={'maker': 'crk', 'mode': 'either'}).status_code == 400
    assert client.post(W + '/', json={'model': 'Sebenza'}).status_code == 200


def test_share_email_is_opt_in(con):
    u = _mk_user(con)
    assert u['share_email_on_intro'] == 0


def test_migration_v10_flips_existing_users_to_opt_in(env):
    con = db.connect()
    uid = db.create_user(con, 'old@example.com', 'old-guy')
    con.execute('UPDATE users SET share_email_on_intro = 1 WHERE id = ?', (uid,))
    con.execute('UPDATE schema_version SET version = 9')
    con.commit(); con.close()
    con = db.connect()
    assert db.get_user(con, uid)['share_email_on_intro'] == 0
    con.close()


# --- H5: reports need skin in the game, and a knife on the board ---------------

def test_three_fresh_accounts_cannot_hide_a_knife(client, mailer, con):
    s = _seller(con)
    _old_knife(con, s['id'])
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500)
    for i in range(3):
        u = signed_in(client, mailer, email=f'sock{i}@example.com')
        _mk_knife(con, u['id'])                                   # a live knife, but unverified and days-fresh
        r = client.post(f"{B}/{k['id']}/report", json={'reason': 'spam spam'})
        assert r.status_code == 200 and r.get_json()['hidden'] is False
        client.post('/blade-book/api/auth/signout')
    assert db.counting_open_reports(con, k['id']) == 0
    assert db.get_knife_any(con, k['id'])['hidden_at'] is None


def test_report_only_accepts_board_listings(client, mailer, con):
    s = _seller(con)
    k = _mk_knife(con, s['id'])                                   # keeping — never on the board
    signed_in(client, mailer, email='r@example.com')
    assert client.post(f"{B}/{k['id']}/report", json={'reason': 'not for sale even'}).status_code == 404


# --- M1: state-changing API calls must come from our own origin ----------------

def test_foreign_origin_post_is_403(client, mailer):
    signed_in(client, mailer)
    r = client.post(K + '/', headers={'Origin': 'https://instockornot.club'})
    assert r.status_code == 403 and 'origin' in r.get_json()['error']
    r = client.post(K + '/', headers={'Origin': 'null'})
    assert r.status_code == 403
    r = client.post(K + '/', headers={'Sec-Fetch-Site': 'same-site'})
    assert r.status_code == 403
    assert client.post(K + '/', headers={'Origin': 'http://localhost', 'Sec-Fetch-Site': 'same-origin'}).status_code == 201
    # the alias host keeps working after BASE_URL moves to the apex (and vice versa)
    # (401 not 403: the host-bound test cookie stays home, but the origin gate let it through)
    assert client.post(K + '/', headers={'Origin': 'http://alias.example', 'Host': 'alias.example'}).status_code == 401
    from bb import auth
    assert client.post(K + '/', headers={'Origin': auth.base_url()}).status_code == 409   # BASE_URL itself: past the gate, into the one-draft rule
    assert client.get(K + '/', headers={'Origin': 'https://instockornot.club'}).status_code == 200   # reads are fine


def test_oidc_callbacks_are_exempt_from_origin_check(client):
    # Apple posts the callback cross-site by design; it must not be 403
    r = client.post('/blade-book/api/auth/apple/callback', data={'code': 'x', 'state': 'y'},
                    headers={'Origin': 'https://appleid.apple.com'})
    assert r.status_code != 403


# --- L4/L5: clamps ---------------------------------------------------------------

def test_board_offset_is_clamped_not_500(client):
    r = client.get(B + '?offset=' + str(10 ** 23))
    assert r.status_code == 200 and r.get_json()['offset'] == db.MAX_BOARD_OFFSET


def test_want_ints_and_text_are_bounded(client, mailer):
    signed_in(client, mailer)
    assert client.post(W + '/', json={'model': 'Sebenza', 'born_from': 10 ** 30}).status_code == 400
    assert client.post(W + '/', json={'model': 'Sebenza', 'max_price': -1}).status_code == 400
    assert client.post(W + '/', json={'keyword': 'x' * 201}).status_code == 400
    assert client.post(W + '/', json={'model': 'Sebenza', 'born_from': 1990, 'born_to': 2030}).status_code == 200


# --- second-opinion review of batch A: the gaps it found ------------------------

def test_new_user_on_live_shaped_db_does_not_share_email(env):
    """The live users table was created with DEFAULT 1; CREATE TABLE IF NOT
    EXISTS never changes that, so create_user must set the column itself."""
    from tests.test_db_migration import _build_v8_db
    _build_v8_db()
    con = db.connect()
    uid = db.create_user(con, 'new@example.com', 'new-guy')
    assert db.get_user(con, uid)['share_email_on_intro'] == 0
    con.close()


def test_migration_v10_runs_once_even_if_the_stamp_flip_flops(env):
    con = db.connect()
    uid = db.create_user(con, 'me@example.com', 'me-guy')
    con.execute('UPDATE schema_version SET version = 9'); con.commit(); con.close()
    con = db.connect()                                                   # migrates 9→10 once
    con.execute('UPDATE users SET share_email_on_intro = 1 WHERE id = ?', (uid,))   # user opts in
    con.execute('UPDATE schema_version SET version = 9'); con.commit(); con.close()  # an old worker re-stamps 9
    con = db.connect()                                                   # new code sees 9 again
    assert db.get_user(con, uid)['share_email_on_intro'] == 1            # the opt-in survives
    con.close()


def test_want_criteria_must_be_real(client, mailer):
    signed_in(client, mailer)
    assert client.post(W + '/', json={'model': '​'}).status_code == 400          # zero-width space
    assert client.post(W + '/', json={'born_from': 1000}).status_code == 400           # half a range
    assert client.post(W + '/', json={'born_from': 1000, 'born_to': 3000}).status_code == 400   # everything ever made
    assert client.post(W + '/', json={'born_from': 2000, 'born_to': 2010}).status_code == 200


def test_decodes_are_rate_limited_per_minute(client, mailer, decoder):
    kid = _draft_with_photo(client, mailer)
    for _ in range(db.DECODES_PER_MINUTE):
        assert client.post(f'{K}/{kid}/decode').status_code == 200
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 429 and 'slow down' in r.get_json()['error']


def test_report_needs_a_knife_that_is_actually_on_the_board(client, mailer, con):
    owner = _mk_user(con, email='unv@example.com', handle='unv-guy')     # unverified: never board-eligible
    k = _mk_knife(con, owner['id'], sale_status='for_sale', asking_price=100)
    signed_in(client, mailer, email='r@example.com')
    assert client.post(f"{B}/{k['id']}/report", json={'reason': 'not even listed'}).status_code == 404


def test_origin_gate_uses_base_url_scheme(client, mailer, monkeypatch):
    signed_in(client, mailer)
    monkeypatch.setattr('bb.auth.base_url', lambda: 'https://secure.example')
    assert client.post(K + '/', headers={'Origin': 'http://localhost'}).status_code == 403     # plain scheme refused
    assert client.post(K + '/', headers={'Origin': 'https://localhost'}).status_code != 403
