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
    rot.rotate(_log(env), datetime.now(), settle=0)
    logging.getLogger('blade-book').info('after')
    today = datetime.now().strftime('%Y-%m-%d')
    assert 'before' in open(f'{_log(env)}.{today}').read()
    live = open(_log(env)).read()
    assert 'after' in live and 'before' not in live
