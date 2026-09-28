# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/activity.py — who is using blade-book and where they are stuck."""
import gzip
import os
from datetime import datetime, timedelta, timezone

from bb import db, paths
from tests.test_search import _mk_knife, _mk_user

NOW = datetime(2026, 9, 28, 4, 40, tzinfo=timezone.utc)


def test_access_log_path_comes_from_the_environment(env, tmp_path):
    assert paths.access_log() == str(tmp_path / 'log' / 'access.log')


def test_access_log_default_is_the_apache_log(monkeypatch):
    monkeypatch.delenv('BLADEBOOK_ACCESS_LOG')
    assert paths.access_log() == '/var/log/apache2/blade-book_access.log'


def test_activity_people_counts_knives_drafts_and_last_active(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    db.create_draft_knife(con, u['id'])
    db.create_session(con, u['id'])
    quiet = _mk_user(con, email='quiet@example.com', handle='quiet')
    rows = {r['handle']: r for r in db.activity_people(con)}
    assert set(rows['idx-guy']) == {'id', 'handle', 'email', 'last_active', 'knives', 'drafts'}
    assert rows['idx-guy']['knives'] == 1 and rows['idx-guy']['drafts'] == 1
    assert rows['idx-guy']['last_active'].startswith('20')
    assert rows['quiet']['last_active'] is None and rows['quiet']['id'] == quiet['id']


def test_activity_drafts_and_knife_tags(con):
    u = _mk_user(con)
    live = _mk_knife(con, u['id'])
    draft = db.create_draft_knife(con, u['id'])
    rows = db.activity_drafts(con)
    assert [(r['tag'], r['handle']) for r in rows] == [(draft['tag'], 'idx-guy')]
    assert set(rows[0]) == {'id', 'tag', 'handle', 'updated'}
    assert db.knife_tags(con) == {live['id']: live['tag'], draft['id']: draft['tag']}
