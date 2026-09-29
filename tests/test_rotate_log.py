# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import importlib.util
import os
import stat
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOW = datetime(2026, 12, 1, 0, 7)                  # the box's local time, as app.log stamps it


@pytest.fixture
def rot(env):
    spec = importlib.util.spec_from_file_location('rotate_log', os.path.join(ROOT, 'scripts', 'rotate_log.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules['rotate_log'] = mod
    spec.loader.exec_module(mod)
    return mod


def line(day, text, clock='10:00:00'):
    return f'{day} {clock},000 INFO blade-book: {text}\n'


def _log(env):
    return os.path.join(env.LOG_DIR, 'app.log')


def _names(env):
    return sorted(os.listdir(env.LOG_DIR))


def test_lines_move_to_a_file_per_day(rot, env):
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-29', 'one') + line('2026-11-30', 'two') + line('2026-11-30', 'three'))
    out = rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env) + '.2026-11-29').read() == line('2026-11-29', 'one')
    assert open(_log(env) + '.2026-11-30').read() == line('2026-11-30', 'two') + line('2026-11-30', 'three')
    assert open(_log(env)).read() == ''
    assert out == {'moved': 3, 'deleted': []}


def test_a_record_of_many_lines_stays_whole(rot, env):
    # app.py's formatter indents every line after a record's first; a late traceback belongs to its record's day
    text = line('2026-11-29', 'boom', '23:59:59') + '\tTraceback (most recent call last):\n\t  File "x"\n' \
        + line('2026-11-30', 'next')
    with open(_log(env), 'w') as f:
        f.write(text)
    rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env) + '.2026-11-29').read().endswith('boom\n\tTraceback (most recent call last):\n\t  File "x"\n')
    assert open(_log(env) + '.2026-11-30').read() == line('2026-11-30', 'next')


def test_a_line_with_no_stamp_before_any_record_is_kept_under_today(rot, env):
    with open(_log(env), 'w') as f:
        f.write('stray\n' + line('2026-11-30', 'two'))
    rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env) + '.2026-12-01').read() == 'stray\n'


def test_a_second_run_adds_to_the_day_file(rot, env):
    with open(_log(env), 'w') as f:
        f.write(line('2026-12-01', 'early', '00:03:00'))
    rot.rotate(_log(env), NOW, settle=0)
    with open(_log(env), 'a') as f:
        f.write(line('2026-12-01', 'late', '18:00:00'))
    rot.rotate(_log(env), datetime(2026, 12, 2, 0, 7), settle=0)
    assert open(_log(env) + '.2026-12-01').read() == line('2026-12-01', 'early', '00:03:00') + line('2026-12-01', 'late', '18:00:00')


def test_no_line_is_older_than_90_days_after_a_run(rot, env):
    # 2026-12-01 minus 90 days is 2026-09-02: that day's first line turns 90 days old today, so the file goes
    for day in ('2026-08-27', '2026-09-02', '2026-09-03'):
        with open(f'{_log(env)}.{day}', 'w') as f:
            f.write(line(day, 'old'))
    out = rot.rotate(_log(env), NOW, settle=0)
    assert [n for n in _names(env) if n.startswith('app.log.')] == ['app.log.2026-09-03']
    assert out['deleted'] == ['app.log.2026-08-27', 'app.log.2026-09-02']


def test_old_lines_in_the_live_file_are_dropped_too(rot, env):
    # the first run meets one app.log that holds everything since the first day
    with open(_log(env), 'w') as f:
        f.write(line('2026-08-27', 'first day') + line('2026-11-30', 'fresh'))
    rot.rotate(_log(env), NOW, settle=0)
    assert [n for n in _names(env) if n.startswith('app.log.')] == ['app.log.2026-11-30']


def test_files_that_are_not_ours_are_left_alone(rot, env):
    for name in ('app.log.1', 'app.log.2026-13-45', 'app.log.2026-01-01.gz', 'publish.log', 'ai_calls.jsonl'):
        with open(os.path.join(env.LOG_DIR, name), 'w') as f:
            f.write('x\n')
    rot.rotate(_log(env), NOW, settle=0)
    assert set(_names(env)) >= {'app.log.1', 'app.log.2026-13-45', 'app.log.2026-01-01.gz', 'publish.log', 'ai_calls.jsonl'}


def test_day_files_and_the_fresh_log_are_not_world_readable(rot, env):
    # sign-in links land in this log (LogMailer)
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-30', 'two'))
    rot.rotate(_log(env), NOW, settle=0)
    for p in (_log(env), _log(env) + '.2026-11-30'):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o640, p


def test_a_run_that_died_half_way_is_finished_by_the_next(rot, env):
    with open(_log(env) + '.rotating', 'w') as f:
        f.write(line('2026-11-29', 'left behind'))
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-30', 'two'))
    out = rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env) + '.2026-11-29').read() == line('2026-11-29', 'left behind')
    assert open(_log(env) + '.2026-11-30').read() == line('2026-11-30', 'two')
    assert not os.path.exists(_log(env) + '.rotating') and out['moved'] == 2


def test_no_log_at_all_is_not_an_error(rot, env):
    assert rot.rotate(_log(env), NOW, settle=0) == {'moved': 0, 'deleted': []}
    assert not os.path.exists(_log(env))


def test_the_app_writes_to_the_fresh_log_after_a_run(rot, env):
    import logging
    from app import create_app
    create_app()
    logging.getLogger('blade-book').info('before')
    rot.rotate(_log(env), datetime.now(), settle=0, keep_days=10 ** 5)   # keep everything: only the move is under test
    logging.getLogger('blade-book').info('after')
    (day_file,) = [n for n in _names(env) if n.startswith('app.log.')]
    assert 'before' in open(os.path.join(env.LOG_DIR, day_file)).read()
    live = open(_log(env)).read()
    assert 'after' in live and 'before' not in live


def test_a_stamp_from_the_future_is_filed_under_today(rot, env):
    # review M2: a day file dated 2099 would never be deleted
    with open(_log(env), 'w') as f:
        f.write(line('2099-01-01', 'from the future') + line('2026-11-30', 'two'))
    rot.rotate(_log(env), NOW, settle=0)
    assert [n for n in _names(env) if n.startswith('app.log.')] == ['app.log.2026-11-30', 'app.log.2026-12-01']
    assert open(_log(env) + '.2026-12-01').read() == line('2099-01-01', 'from the future')


def test_a_last_line_cut_short_gets_its_line_end(rot, env):
    # review M5: the next night's first record would be glued to it and lost to every reader
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-30', 'whole') + '2026-11-30 23:00:00,000 INFO blade-book: cut sh')
    rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env) + '.2026-11-30').read().endswith('cut sh\n')


def test_only_plain_files_with_our_exact_name_are_deleted(rot, env):
    # review M4: a folder, a link, and digits that are not 0-9
    os.mkdir(_log(env) + '.2020-01-01')
    target = os.path.join(env.DATA_DIR, 'elsewhere')
    open(target, 'w').write('x\n')
    os.symlink(target, _log(env) + '.2020-01-02')
    open(_log(env) + '.\u0662\u0660\u0662\u0660-\u0660\u0661-\u0660\u0661', 'w').write('x\n')
    open(_log(env) + '.2020-01-03', 'w').write(line('2020-01-03', 'old'))
    out = rot.rotate(_log(env), NOW, settle=0)
    assert out['deleted'] == ['app.log.2020-01-03']
    assert len([n for n in _names(env) if n.startswith('app.log.')]) == 3 and os.path.exists(target)


def test_a_day_file_that_is_a_link_is_not_written_through(rot, env):
    target = os.path.join(env.DATA_DIR, 'elsewhere')
    open(target, 'w').write('')
    os.symlink(target, _log(env) + '.2026-11-30')
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-30', 'two'))
    with pytest.raises(OSError):
        rot.rotate(_log(env), NOW, settle=0)
    assert open(target).read() == ''
    assert open(_log(env) + '.rotating').read() == line('2026-11-30', 'two')      # kept for the next run


def test_a_second_run_at_the_same_time_stands_down(rot, env):
    # review M1: two runs could move the fresh log over the first run's held file and lose a day
    import fcntl
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-30', 'two'))
    with open(os.path.join(env.LOG_DIR, 'rotate.lock'), 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(rot.Busy):
            rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env)).read() == line('2026-11-30', 'two')
    assert rot.rotate(_log(env), NOW, settle=0)['moved'] == 1                    # the lock is free again


def test_a_run_that_died_after_filing_does_not_file_twice(rot, env):
    # review M6: the admin page would count every record of that night twice, for 90 days
    boom = line('2026-11-30', 'boom') + '\tTraceback\n\t  File "x"\n'
    with open(_log(env) + '.2026-11-30', 'w') as f:
        f.write(line('2026-11-30', 'one') + boom)                                # filed before it died
    with open(_log(env) + '.rotating', 'w') as f:
        f.write(line('2026-11-30', 'one') + boom + line('2026-11-30', 'other') + '\tTraceback\n'
                + line('2026-11-30', 'three'))
    rot.rotate(_log(env), NOW, settle=0)
    assert open(_log(env) + '.2026-11-30').read() == line('2026-11-30', 'one') + boom \
        + line('2026-11-30', 'other') + '\tTraceback\n' + line('2026-11-30', 'three')


def test_the_script_refuses_root(rot, env, monkeypatch, capsys):
    with open(_log(env), 'w') as f:
        f.write(line('2026-11-30', 'two'))
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    assert rot.main() == 2
    assert 'refusing to run as root' in capsys.readouterr().err
    assert _names(env) == ['app.log']


def test_the_script_says_what_it_did(rot, env, monkeypatch, capsys):
    import time
    with open(_log(env), 'w') as f:
        f.write(line('2020-01-01', 'old') + line('2020-01-02', 'old'))
    slept = []
    monkeypatch.setattr(time, 'sleep', slept.append)
    assert rot.main() == 0
    assert capsys.readouterr().out.endswith(' moved 2 line(s), deleted 2 day file(s) app.log.2020-01-01 app.log.2020-01-02\n')
    assert slept == [2]                       # a worker that was mid-line gets its two seconds


def test_the_script_stands_down_when_another_run_holds_the_lock(rot, env, capsys):
    import fcntl
    with open(os.path.join(env.LOG_DIR, 'rotate.lock'), 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert rot.main() == 1
    assert 'another run' in capsys.readouterr().err
