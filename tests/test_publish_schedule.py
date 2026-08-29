# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import os
import time

from bb import db, publish


def test_schedule_marks_dirty_and_debounces(con, tmp_path, monkeypatch):
    from tests.test_publish_bundle import _setup
    user, st, _ = _setup(con, tmp_path)
    monkeypatch.setattr(publish, 'DEBOUNCE_S', 0.05)
    monkeypatch.setattr(publish, '_store_factory', lambda: st)
    publish.schedule(user['id'])
    assert db.get_user(con, user['id'])['publish_dirty_at'] is not None
    deadline = time.time() + 3
    while time.time() < deadline:
        if os.path.exists(os.path.join(publish.bundle_dir('bundle-guy'), 'index.html')):
            break
        time.sleep(0.05)
    assert os.path.exists(os.path.join(publish.bundle_dir('bundle-guy'), 'index.html'))
    assert db.get_user(con, user['id'])['publish_dirty_at'] is None


def test_run_due_builds_and_clears(con, tmp_path, monkeypatch):
    from tests.test_publish_bundle import _setup
    user, st, _ = _setup(con, tmp_path)
    monkeypatch.setattr(publish, '_store_factory', lambda: st)
    db.mark_publish_dirty(con, user['id'])
    assert publish.run_due(quiet_s=0) == 1
    assert db.get_user(con, user['id'])['publish_dirty_at'] is None
    assert publish.run_due(quiet_s=0) == 0     # nothing left


def test_run_due_survives_one_bad_user(con, tmp_path, monkeypatch):
    from tests.test_publish_bundle import _setup
    user, st, _ = _setup(con, tmp_path)
    bad = db.create_user(con, 'bad@example.com', 'bad-guy')
    monkeypatch.setattr(publish, '_store_factory', lambda: st)
    db.mark_publish_dirty(con, bad)
    db.mark_publish_dirty(con, user['id'])
    monkeypatch.setattr(publish, 'build_user',
                        _raise_for('bad-guy', publish.build_user))
    assert publish.run_due(quiet_s=0) == 1     # good user still built
    assert db.get_user(con, bad)['publish_dirty_at'] is not None  # stays dirty


def _raise_for(handle, real):
    def fake(con, user, store):
        if user['handle'] == handle:
            raise RuntimeError('boom')
        return real(con, user, store)
    return fake
