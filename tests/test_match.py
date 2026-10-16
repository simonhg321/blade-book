# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import threading
import time

from bb import db, match
from bb.mail import FakeMailer
from tests.test_search import _mk_knife
from tests.test_wants_db import _u


def _sale_knife(con, uid, price=450, **kw):
    return _mk_knife(con, uid, sale_status='for_sale', asking_price=price, **kw)


def test_intro_honors_owner_hide_born_day(con):
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy', hide_born_day=1)
    _sale_knife(con, owner['id'], born='2008-03-14')
    db.create_want(con, wanter['id'], {'model': 'Sebenza'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    for msg in m.sent:
        assert 'March 2008' in msg['text']
        assert '14' not in msg['text']


def test_keyword_does_not_span_field_boundary():
    k = {'maker': 'crk', 'model': 'Sebenza', 'blade_shape': '', 'blade_steel': '',
         'born_on': '2008-03-14', 'sale_status': 'for_sale', 'asking_price': 500,
         'ext': {'generation': '21', 'size': 'Large', 'special_edition': 'Silver Rose'},
         'notes_public': 'Ladder damascus'}
    base = {f: None for f in db.WANT_FIELDS} | {'mode': 'sale', 'maker': 'crk'}
    assert not match.knife_matches(dict(base, keyword='rose ladder'), k)
    assert match.knife_matches(dict(base, keyword='silver rose'), k)


def test_knife_matches_exact_enums_and_wildcards():
    want = {'model': 'Sebenza', 'generation': '31', 'size': None, 'blade_shape': None,
            'blade_steel': None, 'keyword': None, 'born_from': None, 'born_to': None,
            'mode': 'either', 'max_price': None, 'maker': 'crk'}
    k = {'maker': 'crk', 'model': 'Sebenza', 'blade_shape': 'Drop Point', 'blade_steel': 'S35VN',
         'born_on': '2019-06-05', 'sale_status': 'for_trade', 'asking_price': None,
         'ext': {'generation': '31', 'size': 'Large'}, 'notes_public': ''}
    assert match.knife_matches(want, k)
    assert match.knife_matches(dict(want, model='sebenza'), k)      # case-insensitive
    assert not match.knife_matches(dict(want, generation='21'), k)
    assert not match.knife_matches(dict(want, mode='sale'), k)      # for_trade doesn't fit sale
    assert not match.knife_matches(want, dict(k, sale_status='keeping'))


def test_knife_matches_born_range_price_keyword():
    k = {'maker': 'crk', 'model': 'Sebenza', 'blade_shape': '', 'blade_steel': '',
         'born_on': '2008-03-14', 'sale_status': 'for_sale', 'asking_price': 500,
         'ext': {'generation': '21', 'size': 'Large', 'damascus_pattern': 'Raindrop'},
         'notes_public': 'ladder pattern lanyard'}
    base = {f: None for f in db.WANT_FIELDS} | {'mode': 'sale', 'maker': 'crk'}
    assert match.knife_matches(dict(base, born_from=2005, born_to=2010), k)
    assert not match.knife_matches(dict(base, born_from=2009), k)
    assert not match.knife_matches(dict(base, max_price=400), k)
    assert match.knife_matches(dict(base, keyword='raindrop'), k)
    assert match.knife_matches(dict(base, keyword='ladder'), k)     # notes_public
    assert not match.knife_matches(dict(base, keyword='rain'), k)   # WHOLE word, not substring
    assert not match.knife_matches(dict(base, keyword='unicorn'), k)
    assert match.knife_matches(dict(base, born_from=2005), dict(k, born_on='c. 2008')) is False  # junk year never matches a range


def test_run_sends_both_sides_once(con):
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy')
    k = _sale_knife(con, owner['id'])
    con.execute('UPDATE users SET share_email_on_intro = 1'); con.commit()   # sharing is opt-in since review H4
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    tos = sorted(msg['to'] for msg in m.sent)
    assert tos == ['o@example.com', 'w@example.com']
    wanter_mail = next(x for x in m.sent if x['to'] == 'w@example.com')
    assert '@o-guy' in wanter_mail['text'] and k['tag'] in wanter_mail['text']
    assert f"/blade-book/@o-guy/{k['tag']}/" in wanter_mail['text']
    owner_mail = next(x for x in m.sent if x['to'] == 'o@example.com')
    assert k['tag'] in owner_mail['text'] and '@want-guy' in owner_mail['text']
    # both opted in above -> reply_to carries the other party
    assert wanter_mail['reply_to'] == 'o@example.com'
    assert owner_mail['reply_to'] == 'w@example.com'
    assert match.run(con, m) == 0          # never re-fires
    assert len(m.sent) == 2


def test_no_reply_to_unless_both_share(con):
    wanter = _u(con, share_email_on_intro=0)
    owner = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza'})
    m = FakeMailer()
    match.run(con, m)
    assert all(msg['reply_to'] is None for msg in m.sent)
    assert all('o@example.com' not in msg['text'] or msg['to'] == 'o@example.com' for msg in m.sent)
    assert all('w@example.com' not in msg['text'] or msg['to'] == 'w@example.com' for msg in m.sent)


def test_never_matches_own_or_inactive_or_new_knife_refire(con):
    u = _u(con)
    _sale_knife(con, u['id'])                       # own knife
    w = db.create_want(con, u['id'], {'model': 'Sebenza'})
    m = FakeMailer()
    assert match.run(con, m) == 0
    db.set_want_active(con, u['id'], w['id'], False)
    other = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, other['id'])
    assert match.run(con, m) == 0                   # inactive want stays quiet
    db.set_want_active(con, u['id'], w['id'], True)
    assert match.run(con, m) == 2                   # fires for the NEW knife only


def test_failed_send_retries_without_duplicate(con):
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza'})

    class Flaky(FakeMailer):
        def __init__(self):
            super().__init__(); self.fail = True
        def send(self, to, subject, text, html=None, reply_to=None):
            if self.fail:
                raise RuntimeError('resend down')
            return super().send(to, subject, text, html, reply_to)

    m = Flaky()
    assert match.run(con, m) == 0                   # claimed, send failed
    assert len(db.unsent_intros(con)) == 1
    m.fail = False
    assert match.run(con, m) == 2                   # retry lane sends BOTH sides, no re-claim
    assert match.run(con, m) == 0


def test_overlapping_wants_collapse_to_one_claim_per_knife(con):
    """Final-review finding 2: N overlapping wants from the same user
    matching the same knife must fire exactly one intro pair, not N — the
    dedupe in db.claim_intro is keyed on (from_user, knife_id), not
    (want_id, knife_id)."""
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    db.create_want(con, wanter['id'], {'size': 'Large', 'mode': 'either'})  # broader, also matches
    m = FakeMailer()
    assert match.run(con, m) == 2                   # not 4
    assert con.execute("SELECT COUNT(*) FROM intros WHERE kind='match'").fetchone()[0] == 1


def test_delete_recreate_identical_want_does_not_refire(con):
    """Final-review finding 2: intros.want_id is ON DELETE SET NULL (not
    CASCADE), and claim_intro dedupes on (from_user, knife_id) regardless
    of want_id — so deleting a want and recreating an identical one must
    NOT re-fire a pair already sent (spec §5: one fire per pair, ever)."""
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, owner['id'])
    w = db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    assert db.delete_want(con, wanter['id'], w['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    assert match.run(con, m) == 0                   # same pair, zero new emails
    assert len(m.sent) == 2


def test_send_cap_drains_over_multiple_runs(con, monkeypatch):
    """Final-review finding 2: MAX_EMAILS_PER_RUN bounds one pass's outbound
    volume; anything past the cap stays claimed-but-unsent and drains over
    later runs."""
    monkeypatch.setattr(match, 'MAX_EMAILS_PER_RUN', 4)
    wanter = _u(con)
    db.create_want(con, wanter['id'], {'size': 'Large', 'mode': 'either'})
    for i in range(4):
        owner = _u(con, email=f'o{i}@example.com', handle=f'o-guy-{i}')
        _sale_knife(con, owner['id'])
    m = FakeMailer()
    assert match.run(con, m) == 4                   # exactly the cap: 2 of 4 pairs
    assert len(db.unsent_intros(con)) == 2
    assert match.run(con, m) == 4                   # drains the rest
    assert len(db.unsent_intros(con)) == 0
    assert match.run(con, m) == 0


def test_overlapping_runs_are_serialized_by_flock(con):
    """Final-review finding 3: an flock around run() means an overlapping
    invocation (a cron run that outlives its interval) SKIPS (returns 0)
    instead of racing the send loop of a run already in flight."""
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza'})

    class SlowMailer(FakeMailer):
        def send(self, *a, **kw):
            time.sleep(0.2)
            return super().send(*a, **kw)

    m = SlowMailer()
    results = []

    def go():
        c = db.connect()
        try:
            results.append(match.run(c, m))
        finally:
            c.close()

    t1 = threading.Thread(target=go)
    t1.start()
    time.sleep(0.05)   # give t1 the lock before t2 tries
    t2 = threading.Thread(target=go)
    t2.start()
    t1.join()
    t2.join()
    assert sorted(results) == [0, 2]
    assert len(m.sent) == 2


def test_asking_price_formats_whole_dollars_without_trailing_zero():
    """Final-review finding 5: asking_price is stored as a float (bb/edit.py
    _number), so an unformatted f-string produces 'asking $1500.0'."""
    k = {'tag': 'K01', 'model': 'Sebenza', 'born_on': '2008-03-14',
         'sale_status': 'for_sale', 'asking_price': 1500.0,
         'ext': {'generation': '21', 'size': 'Large'}, 'notes_public': '',
         'owner_hide_born_day': 0}
    text = match._card_text(k)
    assert 'asking $1500' in text and '$1500.0' not in text
    text2 = match._card_text(dict(k, asking_price=499.99))
    assert 'asking $499.99' in text2


def test_owner_email_does_not_repeat_the_tag(con):
    """Final-review finding 8 (noted): the owner's email said 'your K01
    (K01 — Large Sebenza 21 · ...)' — the tag must appear once."""
    wanter = _u(con)
    owner = _u(con, email='o@example.com', handle='o-guy')
    k = _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza', 'mode': 'sale'})
    m = FakeMailer()
    match.run(con, m)
    owner_mail = next(x for x in m.sent if x['to'] == 'o@example.com')
    # the tag legitimately appears twice overall (prose + permalink path) —
    # what must NOT happen is the card repeating it right after the prose
    # mention, e.g. "your K01 (K01 — Large Sebenza 21 ..."
    assert f"({k['tag']} —" not in owner_mail['text']
    assert f"your {k['tag']} (Large" in owner_mail['text']


def test_no_share_fallback_copy_does_not_promise_a_dead_end(con):
    """Final-review finding 4: the old copy pointed to a register-page
    'contact' affordance that does not exist. Both fallbacks must be honest
    about there being no contact channel yet."""
    wanter = _u(con, share_email_on_intro=0)
    owner = _u(con, email='o@example.com', handle='o-guy')
    _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza'})
    m = FakeMailer()
    match.run(con, m)
    wanter_mail = next(x for x in m.sent if x['to'] == 'w@example.com')
    owner_mail = next(x for x in m.sent if x['to'] == 'o@example.com')
    assert "hasn't shared a contact address" in wanter_mail['text']
    assert "hasn't shared a contact address" in owner_mail['text']
    assert 'Reach the owner via their register page' not in wanter_mail['text']
    assert 'They can reach you through your public page' not in owner_mail['text']


def test_match_retry_lane_ignores_board_claims(con):
    """plan 09: a board contact claims an intros row with want_id NULL and
    sends synchronously; a cron run in that window must neither send nor
    'skip' it (which would stamp sent_at and break the board's failure
    rollback)."""
    from tests.test_board_db import _seller
    s = _seller(con)
    k = _mk_knife(con, s['id'], sale_status='for_sale', asking_price=500)
    b = _u(con, email='b@example.com', handle='b-guy')
    iid = db.claim_intro(con, None, k['id'], b['id'], s['id'], kind='board', message='hi')
    m = FakeMailer()
    assert match.run(con, m) == 0 and m.sent == []
    row = con.execute('SELECT sent_at FROM intros WHERE id = ?', (iid,)).fetchone()
    assert row['sent_at'] is None


def _bd_want(**over):
    base = {f: None for f in db.WANT_FIELDS} | {'mode': 'either', 'maker': 'crk'}
    return dict(base, **over)


def _k(**over):
    k = {'maker': 'crk', 'model': 'Sebenza', 'blade_shape': '', 'blade_steel': '',
         'born_on': '2022-07-28', 'born_on_precision': 'day', 'owner_hide_born_day': 0,
         'sale_status': 'keeping', 'asking_price': None,
         'ext': {'generation': '31', 'size': 'Small'}, 'notes_public': ''}
    return dict(k, **over)


def test_born_day_matches_any_sale_status_and_any_maker():
    w = _bd_want(born_day='07-28')
    assert match.knife_matches(w, _k())                                        # keeping
    assert match.knife_matches(w, _k(sale_status='for_trade'))
    assert match.knife_matches(w, _k(sale_status='for_sale', asking_price=900))
    assert match.knife_matches(w, _k(maker='spyderco'))                        # the date is the criterion
    assert not match.knife_matches(w, _k(born_on='2022-07-29'))
    assert not match.knife_matches(w, _k(born_on='2022-07', born_on_precision='month'))
    assert not match.knife_matches(w, _k(owner_hide_born_day=1))


def test_born_day_keeps_the_other_criteria_and_the_price_gate():
    assert not match.knife_matches(_bd_want(born_day='07-28', model='Inkosi'), _k())
    assert match.knife_matches(_bd_want(born_day='07-28', model='Sebenza'), _k())
    w = _bd_want(born_day='07-28', max_price=500)
    assert match.knife_matches(w, _k())                                        # keeping: no price to fail
    assert not match.knife_matches(w, _k(sale_status='for_sale', asking_price=900))
    assert match.knife_matches(w, _k(sale_status='for_sale', asking_price=400))


def test_ordinary_wants_still_need_a_listed_knife():
    assert not match.knife_matches(_bd_want(model='Sebenza'), _k())            # keeping, no born_day


def test_born_day_intro_email_says_so(con):
    wanter = _u(con, share_email_on_intro=1)          # both share — still no owner address: not listed
    owner = _u(con, email='o2@example.com', handle='o2-guy', share_email_on_intro=1)
    _mk_knife(con, owner['id'], born='2022-07-28')                             # keeping, public
    db.create_want(con, wanter['id'], {'born_day': '07-28'})
    m = FakeMailer()
    # the knife is not listed: only the wanter hears — the owner is reached
    # through "ask @handle", which carries the board's daily limits
    assert match.run(con, m) == 1
    assert [x['to'] for x in m.sent] == ['w@example.com']
    to_wanter = m.sent[0]
    assert to_wanter['text'].startswith('Born on your date, 7/28:')
    assert 'July 28, 2022' in to_wanter['text']
    assert 'Not listed for sale' in to_wanter['text'] and 'ask @o2-guy' in to_wanter['text']
    assert to_wanter['reply_to'] is None and 'o2@example.com' not in to_wanter['text']
    assert 'Reply to this email' not in to_wanter['text']
    assert match.run(con, FakeMailer()) == 0                                   # one intro per pair, ever


def test_born_day_intro_on_a_listed_knife_mails_both_sides(con):
    wanter = _u(con)
    owner = _u(con, email='o3@example.com', handle='o3-guy')
    _sale_knife(con, owner['id'], born='2022-07-28')
    db.create_want(con, wanter['id'], {'born_day': '07-28'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    to_owner = next(x for x in m.sent if x['to'] == 'o3@example.com')
    assert 'born on 7/28' in to_owner['text']
    to_wanter = next(x for x in m.sent if x['to'] == 'w@example.com')
    assert 'Not listed for sale' not in to_wanter['text']


def test_born_day_intro_on_a_for_trade_knife_mails_both_sides(con):
    wanter = _u(con)
    owner = _u(con, email='o4@example.com', handle='o4-guy')
    _mk_knife(con, owner['id'], born='2022-07-28', sale_status='for_trade')
    db.create_want(con, wanter['id'], {'born_day': '07-28'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    assert {x['to'] for x in m.sent} == {'w@example.com', 'o4@example.com'}


def test_ordinary_match_still_mails_both_sides(con):
    wanter = _u(con)
    owner = _u(con, email='o5@example.com', handle='o5-guy')
    _sale_knife(con, owner['id'])
    db.create_want(con, wanter['id'], {'model': 'Sebenza'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    assert {x['to'] for x in m.sent} == {'w@example.com', 'o5@example.com'}
    assert all('Not listed for sale' not in x['text'] for x in m.sent)


def test_born_day_privacy_gate_fails_closed():
    w = _bd_want(born_day='07-28')
    no_precision = _k(); del no_precision['born_on_precision']
    assert not match.knife_matches(w, no_precision)
    no_flag = _k(); del no_flag['owner_hide_born_day']
    assert not match.knife_matches(w, no_flag)


def test_born_day_run_respects_a_later_hide_born_day(con):
    first = _u(con)
    owner = _u(con, email='o6@example.com', handle='o6-guy')
    _mk_knife(con, owner['id'], born='2022-07-28')
    db.create_want(con, first['id'], {'born_day': '07-28'})
    m = FakeMailer()
    assert match.run(con, m) == 1 and m.sent[0]['to'] == 'w@example.com'
    con.execute('UPDATE users SET hide_born_day = 1 WHERE id = ?', (owner['id'],)); con.commit()
    second = _u(con, email='w2@example.com', handle='want-guy-2')
    db.create_want(con, second['id'], {'born_day': '07-28'})
    m2 = FakeMailer()
    assert match.run(con, m2) == 0 and m2.sent == []
    assert con.execute('SELECT count(*) FROM intros WHERE from_user = ?', (second['id'],)).fetchone()[0] == 0
