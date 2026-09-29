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
    # a sender that returns falsy without raising (billboard's _send_twilio_sms
    # does this on missing creds / Twilio refusal) must report failure, not
    # be papered over as success.
    assert mon.send_sms('x', sender=lambda p, b: False) is False


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


def test_main_returns_zero_when_mail_setup_raises(mon, env, monkeypatch):
    import bb.mail
    def boom():
        raise RuntimeError('resend import failed')
    monkeypatch.setattr(bb.mail, 'from_env', boom)
    monkeypatch.setattr(mon, 'gather', lambda now, **k: [mon.Event('errors', 'x', 'y', False)])
    monkeypatch.setenv('BLADEBOOK_ADMIN_EMAIL', 'admin@example.com')
    assert mon.main([]) == 0


def test_main_delivers_via_fake_mailer(mon, env, monkeypatch):
    import bb.mail
    m = _Mailer()
    monkeypatch.setattr(bb.mail, 'from_env', lambda: m)
    monkeypatch.setattr(mon, 'gather', lambda now, **k: [mon.Event('errors', 'x', 'y', False)])
    # config.get() reads os.environ directly (config.load()'s override=False
    # never masks it), so monkeypatch.setenv is enough here.
    monkeypatch.setenv('BLADEBOOK_ADMIN_EMAIL', 'admin@example.com')
    assert mon.main([]) == 0
    assert m.sent == [('admin@example.com', '[blade-book] x', 'y')]


def test_main_dry_run_saves_no_state(mon, env):
    assert not os.path.exists(mon.state_path())
    rc = mon.main(['--dry-run'])
    assert rc == 0
    assert not os.path.exists(mon.state_path())


def test_main_reports_gather_failure(mon, env, monkeypatch, capsys):
    def boom(now, **k):
        raise RuntimeError('db is gone')
    monkeypatch.setattr(mon, 'gather', boom)
    assert mon.main([]) == 0
    assert 'gather FAILED' in capsys.readouterr().out
    capsys.readouterr()
    assert mon.main(['--dry-run']) == 0
    assert 'gather FAILED' in capsys.readouterr().out


def test_load_state_treats_os_and_unicode_errors_as_missing(mon, tmp_path):
    p = tmp_path / 'state.json'
    p.write_bytes(b'\xff\xfe\x00bad')
    assert mon.load_state(str(p)) == mon.load_state('/nonexistent')
    p.write_text('{}')
    os.chmod(p, 0o000)
    try:
        assert mon.load_state(str(p)) == mon.load_state('/nonexistent')
    finally:
        os.chmod(p, 0o644)


def test_billboard_sender_import_does_not_clobber_shared_env_keys(mon, tmp_path, monkeypatch):
    """billboard's real sms_alerter does `load_dotenv(..., override=True)` at
    import time, and both .env files define ANTHROPIC_API_KEY/RESEND_API_KEY —
    a fake stand-in module reproduces that clobbering side effect so the
    guard in _billboard_sender is actually exercised."""
    fake_dir = tmp_path / 'fakebb'
    fake_dir.mkdir()
    (fake_dir / 'sms_alerter.py').write_text(
        "import os\n"
        "os.environ['ANTHROPIC_API_KEY'] = 'billboard-key'\n"
        "os.environ['RESEND_API_KEY'] = 'billboard-resend'\n"
        "def _send_twilio_sms(to, body):\n"
        "    return True\n"
    )
    monkeypatch.setattr(mon, 'BILLBOARD_DIR', str(fake_dir))
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'blade-book-key')
    monkeypatch.setenv('RESEND_API_KEY', 'blade-book-resend')
    sys.modules.pop('sms_alerter', None)
    try:
        sender = mon._billboard_sender()
        assert sender('123', 'hi') is True
        assert os.environ['ANTHROPIC_API_KEY'] == 'blade-book-key'
        assert os.environ['RESEND_API_KEY'] == 'blade-book-resend'
    finally:
        sys.modules.pop('sms_alerter', None)
        if str(fake_dir) in sys.path:
            sys.path.remove(str(fake_dir))


def test_gather_logs_but_does_not_raise_when_state_cannot_be_saved(mon, env, monkeypatch, con, caplog):
    def boom(path, state):
        raise OSError('disk full')
    monkeypatch.setattr(mon, 'save_state', boom)
    with caplog.at_level('ERROR'):
        events = mon.gather(NOW, fetch=lambda: {'ok': True, 'disk_free_pct': 40}, con=con,
                            backup_glob=str(env.DATA_DIR) + '/nothing-*.tgz')
    assert isinstance(events, list)
    assert any('could not save state' in r.message for r in caplog.records)


def _stamp(dt):
    """A UTC datetime as app.log writes it: the box's local time."""
    return dt.astimezone().strftime('%Y-%m-%d %H:%M:%S') + ',000 INFO blade-book: x\n'


def test_check_log_limit_is_quiet_when_the_nightly_run_works(mon, tmp_path):
    # the terms promise 90 days; scripts/rotate_log.py keeps it, this check says when it stops
    log = tmp_path / 'app.log'
    s = mon.load_state('/nonexistent')
    assert mon.check_log_limit(str(log), s, NOW) == []              # no log yet
    log.write_text(_stamp(NOW - timedelta(hours=30)))
    (tmp_path / 'app.log.2026-06-06').write_text('x\n')             # 89 days before NOW's day
    assert mon.check_log_limit(str(log), s, NOW) == []
    (tmp_path / 'app.log.2026-06-05').write_text('x\n')             # 90 days: tonight's run deletes it, not late yet
    assert mon.check_log_limit(str(log), s, NOW) == []


def test_check_log_limit_waits_for_the_first_run(mon, tmp_path):
    # review I3: the monitor runs from the main tree, so it sees this check before deploy_log_limit.sh has run
    log = tmp_path / 'app.log'
    log.write_text(_stamp(NOW - timedelta(days=30)))
    s = mon.load_state('/nonexistent')
    assert mon.check_log_limit(str(log), s, NOW) == [] and 'log_limit' not in s['sent']
    (tmp_path / 'rotate.log').write_text('')                        # the first run has been asked for
    assert len(mon.check_log_limit(str(log), s, NOW)) == 1


def test_check_log_limit_names_an_old_copy_nobody_deletes(mon, tmp_path):
    # review M3: app.log.1, a hand copy, a .gz: the nightly run leaves them alone, so somebody has to be told
    log = tmp_path / 'app.log'
    log.write_text(_stamp(NOW))
    for name, days in (('app.log.1', 91), ('app.log.2026-06-01.bak', 91), ('app.log.copy', 89)):
        (tmp_path / name).write_text('x\n')
        old = (NOW - timedelta(days=days)).timestamp()
        os.utime(tmp_path / name, (old, old))
    ev = mon.check_log_limit(str(log), mon.load_state('/nonexistent'), NOW)
    assert len(ev) == 1 and 'older than 90 days' in ev[0].subject
    assert 'app.log.1\n' in ev[0].body and 'app.log.2026-06-01.bak' in ev[0].body and 'app.log.copy' not in ev[0].body


def test_check_log_limit_says_when_the_live_log_was_not_moved(mon, tmp_path):
    log = tmp_path / 'app.log'
    log.write_text(_stamp(NOW - timedelta(hours=50)) + _stamp(NOW))
    (tmp_path / 'app.log.2026-09-01').write_text('x\n')             # the nightly run has run before
    s = mon.load_state('/nonexistent')
    ev = mon.check_log_limit(str(log), s, NOW)
    assert len(ev) == 1 and ev[0].key == 'log_limit' and ev[0].sms is False
    assert 'not been moved for 50 h' in ev[0].subject and 'rotate_log.py' in ev[0].body
    assert mon.check_log_limit(str(log), s, NOW + timedelta(hours=1)) == []   # once per 24 h


def test_check_log_limit_says_when_a_day_file_outlives_the_promise(mon, tmp_path):
    log = tmp_path / 'app.log'
    log.write_text(_stamp(NOW))
    (tmp_path / 'app.log.2026-06-04').write_text('x\n')             # 91 days before NOW's day
    (tmp_path / 'app.log.2026-06-04.gz').write_text('x\n')          # not ours
    s = mon.load_state('/nonexistent')
    ev = mon.check_log_limit(str(log), s, NOW)
    assert len(ev) == 1 and 'older than 90 days' in ev[0].subject and ev[0].body.count('app.log.') == 1


def test_check_log_limit_reads_past_a_line_with_no_stamp(mon, tmp_path):
    log = tmp_path / 'app.log'
    log.write_text('stray\n' + _stamp(NOW - timedelta(hours=50)))
    (tmp_path / 'rotate.log').write_text('')
    assert len(mon.check_log_limit(str(log), mon.load_state('/nonexistent'), NOW)) == 1
