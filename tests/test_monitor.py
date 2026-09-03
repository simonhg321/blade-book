# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location('monitor', os.path.join(ROOT, 'scripts', 'monitor.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['monitor'] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mon(env):
    return _load()


NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def test_state_roundtrip(mon, tmp_path):
    p = str(tmp_path / 'state.json')
    s = mon.load_state(p)
    assert s == {'log_offset': 0, 'log_inode': None, 'last_users_check': None, 'sent': {}, 'down': False, 'disk': False}
    s['down'] = True
    mon.save_state(p, s)
    assert mon.load_state(p)['down'] is True
    open(p, 'w').write('{not json')
    assert mon.load_state(p)['down'] is False          # corrupt state → defaults, never a crash


def test_due_and_stamp(mon):
    s = mon.load_state('/nonexistent/never-read')
    assert mon.due(s, 'errors', 1, NOW) is True
    mon.stamp(s, 'errors', NOW)
    assert mon.due(s, 'errors', 1, NOW + timedelta(minutes=30)) is False
    assert mon.due(s, 'errors', 1, NOW + timedelta(minutes=61)) is True


def test_check_health_transitions(mon):
    s = mon.load_state('/nonexistent')
    ok = lambda: {'ok': True, 'db': True, 'disk_free_pct': 40, 'version': 'abc'}
    assert mon.check_health(ok, s, NOW) == []                       # up, nothing to say
    def boom():
        raise ConnectionError('refused')
    ev = mon.check_health(boom, s, NOW)
    assert len(ev) == 1 and ev[0].sms is True and 'DOWN' in ev[0].subject and s['down'] is True
    assert mon.check_health(boom, s, NOW) == []                     # still down: no repeat
    ev = mon.check_health(ok, s, NOW)
    assert len(ev) == 1 and ev[0].sms is True and 'UP' in ev[0].subject and s['down'] is False
    low = lambda: {'ok': True, 'db': True, 'disk_free_pct': 7, 'version': 'abc'}
    ev = mon.check_health(low, s, NOW)
    assert len(ev) == 1 and ev[0].sms is True and 'DISK' in ev[0].subject and s['disk'] is True
    assert mon.check_health(low, s, NOW) == []
    assert mon.check_health(ok, s, NOW)[0].subject.startswith('disk ok') and s['disk'] is False
    bad_db = lambda: {'ok': False, 'db': False, 'disk_free_pct': 40, 'version': 'abc'}
    assert 'DOWN' in mon.check_health(bad_db, s, NOW)[0].subject   # 503 body with ok=false counts as down


def test_check_errors_offset_and_rotation(mon, tmp_path):
    log = tmp_path / 'app.log'
    log.write_text('2026-09-03 INFO x: fine\n2026-09-03 ERROR x: delete for @sam left residue\n')
    s = mon.load_state('/nonexistent')
    ev = mon.check_errors(str(log), s, NOW)
    assert len(ev) == 1 and 'left residue' in ev[0].body and ev[0].sms is False
    assert s['log_offset'] == log.stat().st_size and s['log_inode'] == log.stat().st_ino
    assert mon.check_errors(str(log), s, NOW) == []                 # nothing new
    with open(log, 'a') as f:
        f.write('2026-09-03 ERROR y: again\n')
    assert mon.check_errors(str(log), s, NOW + timedelta(minutes=10)) == []   # throttled: 1/hour
    assert s['log_offset'] == log.stat().st_size                    # but the offset still advanced
    with open(log, 'a') as f:
        f.write('2026-09-03 ERROR z: third\n')
    ev = mon.check_errors(str(log), s, NOW + timedelta(hours=2))
    assert len(ev) == 1 and 'third' in ev[0].body and 'again' not in ev[0].body   # only unread lines
    # rotation: new inode → read from 0
    log.unlink()
    log.write_text('2026-09-03 ERROR r: after rotate\n')
    ev = mon.check_errors(str(log), s, NOW + timedelta(hours=4))
    assert len(ev) == 1 and 'after rotate' in ev[0].body
    assert mon.check_errors(str(tmp_path / 'missing.log'), s, NOW) == []       # no file, no crash


def test_check_errors_caps_lines(mon, tmp_path):
    log = tmp_path / 'app.log'
    log.write_text(''.join(f'2026-09-03 ERROR n: line {i}\n' for i in range(100)))
    ev = mon.check_errors(str(log), mon.load_state('/nonexistent'), NOW)
    assert ev[0].body.count('\n') <= 41 and '100 ERROR lines' in ev[0].subject


def _ai(ts, ok):
    return json.dumps({'ts': ts.isoformat(), 'user': 1, 'knife': 1, 'model': 'm', 'ok': ok,
                       'input_tokens': 1, 'output_tokens': 1, 'ms': 1, 'error': None if ok else 'x', 'cost_usd': 0})


def test_check_decode_rate(mon, tmp_path):
    p = tmp_path / 'ai_calls.jsonl'
    s = mon.load_state('/nonexistent')
    p.write_text('\n'.join([_ai(NOW - timedelta(hours=1), False)] * 4) + '\n')
    assert mon.check_decode(str(p), s, NOW) == []                  # < 5 calls: too few to judge
    p.write_text('\n'.join([_ai(NOW - timedelta(hours=1), False)] * 2 + [_ai(NOW - timedelta(hours=2), True)] * 8) + '\n')
    assert mon.check_decode(str(p), s, NOW) == []                  # exactly 20 %: not over the line
    p.write_text('\n'.join([_ai(NOW - timedelta(hours=1), False)] * 3 + [_ai(NOW - timedelta(hours=2), True)] * 7) + '\n')
    ev = mon.check_decode(str(p), s, NOW)
    assert len(ev) == 1 and '3/10' in ev[0].subject and ev[0].sms is False
    assert mon.check_decode(str(p), s, NOW + timedelta(hours=1)) == []   # once per 24 h
    old = '\n'.join([_ai(NOW - timedelta(days=2), False)] * 10) + '\n'
    p.write_text(old)
    s2 = mon.load_state('/nonexistent')
    assert mon.check_decode(str(p), s2, NOW) == []                  # outside the window
    p.write_text('not json\n' + _ai(NOW, True) + '\n')
    assert mon.check_decode(str(p), s2, NOW) == []                  # bad line skipped, no crash
    assert mon.check_decode(str(tmp_path / 'none.jsonl'), s2, NOW) == []


def test_check_backup_age(mon, tmp_path):
    s = mon.load_state('/nonexistent')
    g = str(tmp_path / 'blade-book-*.tgz')
    ev = mon.check_backup(g, s, NOW)
    assert len(ev) == 1 and 'no backup' in ev[0].subject.lower()
    f = tmp_path / 'blade-book-20260903-033001.tgz'
    f.write_bytes(b'x')
    fresh = (NOW - timedelta(hours=9)).timestamp()
    os.utime(f, (fresh, fresh))
    s = mon.load_state('/nonexistent')
    assert mon.check_backup(g, s, NOW) == []
    stale = (NOW - timedelta(hours=30)).timestamp()
    os.utime(f, (stale, stale))
    ev = mon.check_backup(g, s, NOW)
    assert len(ev) == 1 and '30 h' in ev[0].subject
    assert mon.check_backup(g, s, NOW + timedelta(hours=1)) == []  # once per 24 h


def test_check_signups(mon):
    s = mon.load_state('/nonexistent')
    assert mon.check_signups([], s, NOW) == [] and s['last_users_check'] == NOW.isoformat()
    ev = mon.check_signups([('new@example.com', 'new-guy', '2026-09-03T11:50:00+00:00')], s, NOW)
    assert len(ev) == 1 and 'new-guy' in ev[0].body and 'new@example.com' in ev[0].body and ev[0].sms is False
    assert '1 new sign-in' in ev[0].subject


class _Mailer:
    def __init__(self):
        self.sent = []

    def send(self, to, subject, text, html=None, reply_to=None):
        self.sent.append((to, subject, text))
        return 'id'


def test_deliver_routes_mail_and_sms(mon):
    m = _Mailer()
    texts = []
    ev = [mon.Event('down', 'DOWN — healthz failed', '{}', True), mon.Event('errors', '3 ERROR lines', 'x', False)]
    counts = mon.deliver(ev, m, 'admin@example.com', sms=lambda body: texts.append(body) or True)
    assert counts == {'mail': 2, 'sms': 1, 'skipped': 0}
    assert m.sent[0][0] == 'admin@example.com' and m.sent[0][1] == '[blade-book] DOWN — healthz failed'
    assert texts == ['[blade-book] DOWN — healthz failed']


def test_deliver_without_admin_email_skips_mail_but_texts(mon):
    m = _Mailer()
    texts = []
    ev = [mon.Event('down', 'DOWN', '{}', True)]
    assert mon.deliver(ev, m, None, sms=lambda b: texts.append(b) or True) == {'mail': 0, 'sms': 1, 'skipped': 1}
    assert m.sent == []


def test_deliver_survives_channel_failures(mon):
    class Broken:
        def send(self, *a, **k):
            raise RuntimeError('resend down')
    ev = [mon.Event('down', 'DOWN', '{}', True)]
    counts = mon.deliver(ev, Broken(), 'admin@example.com', sms=lambda b: (_ for _ in ()).throw(RuntimeError('twilio')))
    assert counts == {'mail': 0, 'sms': 0, 'skipped': 2}


def test_send_sms_uses_injected_sender_and_never_raises(mon, monkeypatch):
    got = []
    assert mon.send_sms('hello', sender=lambda phone, body: got.append((phone, body)) or {'sid': 'x'}) is True
    assert got == [('5550002222', 'hello')]
    monkeypatch.setenv('BB_ADMIN_PHONE', '15550001111')
    mon.send_sms('hi', sender=lambda phone, body: got.append((phone, body)))
    assert got[-1][0] == '15550001111'
    assert mon.send_sms('x', sender=lambda p, b: (_ for _ in ()).throw(RuntimeError('boom'))) is False


def test_gather_runs_every_check_against_scratch_paths(mon, env, con):
    from bb import db
    db.create_user(con, 'fresh@example.com', 'fresh-guy')
    con.execute("UPDATE users SET created = '2026-09-03T11:50:00+00:00'")
    con.commit()
    os.makedirs(env.LOG_DIR, exist_ok=True)
    open(os.path.join(env.LOG_DIR, 'app.log'), 'w').write('2026-09-03 ERROR a: b\n')
    events = mon.gather(NOW, fetch=lambda: (_ for _ in ()).throw(ConnectionError('no app')), con=con,
                        backup_glob=str(env.DATA_DIR) + '/nothing-*.tgz')
    keys = sorted(e.key for e in events)
    assert keys == ['backup', 'down', 'errors', 'signups']
    assert os.path.exists(mon.state_path())
    again = mon.gather(NOW + timedelta(minutes=5), fetch=lambda: (_ for _ in ()).throw(ConnectionError('no app')), con=con,
                       backup_glob=str(env.DATA_DIR) + '/nothing-*.tgz')
    assert again == []                                                   # all throttled / unchanged


def test_main_dry_run_exits_zero_and_sends_nothing(mon, env, capsys):
    rc = mon.main(['--dry-run'])
    assert rc == 0
    out = capsys.readouterr().out
    assert 'dry-run' in out
