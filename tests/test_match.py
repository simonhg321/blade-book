# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
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
    # both default share_email_on_intro=1 -> reply_to carries the other party
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
