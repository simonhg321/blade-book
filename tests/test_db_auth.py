# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
from datetime import datetime, timedelta, timezone

from bb import db


def _iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def test_magic_token_is_single_use_and_stored_hashed(env):
    con = db.connect()
    tok = db.create_magic_token(con, 'sam@example.com', '10.0.0.1')
    assert len(tok) >= 32
    stored = con.execute('SELECT token_hash FROM magic_tokens').fetchone()[0]
    assert stored != tok and len(stored) == 64
    assert db.consume_magic_token(con, tok) == 'sam@example.com'
    assert db.consume_magic_token(con, tok) is None
    assert db.consume_magic_token(con, 'nope') is None


def test_magic_token_expires_after_15_minutes(env):
    con = db.connect()
    tok = db.create_magic_token(con, 'sam@example.com', '10.0.0.1')
    con.execute('UPDATE magic_tokens SET expires = ?', (_iso(minutes=-1),))
    con.commit()
    assert db.consume_magic_token(con, tok) is None


def test_magic_token_normalises_email(env):
    con = db.connect()
    tok = db.create_magic_token(con, '  ' + 'sam@example.com'.upper() + ' ', '10.0.0.1')
    assert db.consume_magic_token(con, tok) == 'sam@example.com'


def test_counts_for_rate_limits(env):
    con = db.connect()
    for _ in range(3):
        db.create_magic_token(con, 'sam@example.com', '10.0.0.1')
    db.record_attempt(con, '10.0.0.1')
    db.record_attempt(con, '10.0.0.2')
    hour_ago = _iso(hours=-1)
    assert db.count_magic_tokens_since(con, 'sam@example.com', hour_ago) == 3
    assert db.count_magic_tokens_since(con, 'other@example.com', hour_ago) == 0
    assert db.count_attempts_since(con, '10.0.0.1', hour_ago) == 1
    assert db.count_attempts_since(con, '10.0.0.1', _iso(minutes=1)) == 0


def test_oauth_state_round_trip_single_use(env):
    con = db.connect()
    state = db.create_oauth_state(con, 'google', 'nonce-1')
    assert db.pop_oauth_state(con, state) == {'provider': 'google', 'nonce': 'nonce-1'}
    assert db.pop_oauth_state(con, state) is None
    old = db.create_oauth_state(con, 'apple', 'n2')
    con.execute('UPDATE oauth_states SET expires = ?', (_iso(minutes=-1),))
    con.commit()
    assert db.pop_oauth_state(con, old) is None


def test_session_secret_verified_and_subjects(env):
    con = db.connect()
    uid = db.create_user(con, 'sam@example.com', 'sam')
    s1 = db.rotate_session_secret(con, uid)
    s2 = db.rotate_session_secret(con, uid)
    assert s1 and s2 and s1 != s2
    assert db.get_user(con, uid)['session_secret'] == s2

    assert db.get_user(con, uid)['verified_at'] is None
    db.set_verified(con, uid)
    first = db.get_user(con, uid)['verified_at']
    db.set_verified(con, uid)
    assert db.get_user(con, uid)['verified_at'] == first

    db.set_auth_subject(con, uid, 'google', 'g-123')
    db.set_auth_subject(con, uid, 'apple', 'a-456')
    assert db.get_user(con, uid)['auth_subjects'] == {'google': 'g-123', 'apple': 'a-456'}
    assert db.get_user_by_subject(con, 'google', 'g-123')['id'] == uid
    assert db.get_user_by_subject(con, 'apple', 'a-456')['id'] == uid
    assert db.get_user_by_subject(con, 'google', 'a-456') is None


def test_handle_exists_and_purge(env):
    con = db.connect()
    db.create_user(con, 'sam@example.com', 'sam')
    assert db.handle_exists(con, 'sam') is True
    assert db.handle_exists(con, 'sam-2') is False
    db.create_magic_token(con, 'sam@example.com', '10.0.0.1')
    db.record_attempt(con, '10.0.0.1')
    db.create_oauth_state(con, 'google', 'n')
    two_days_ago = _iso(days=-2)
    con.execute('UPDATE magic_tokens SET created = ?', (two_days_ago,))
    con.execute('UPDATE auth_attempts SET ts = ?', (two_days_ago,))
    con.execute('UPDATE oauth_states SET created = ?', (two_days_ago,))
    con.commit()
    db.purge_auth_tables(con)
    for t in ('magic_tokens', 'auth_attempts', 'oauth_states'):
        assert con.execute(f'SELECT count(*) FROM {t}').fetchone()[0] == 0


def test_schema_version_matches_constant(env):
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION


def test_connect_stamps_old_version_up(env):
    con = db.connect()
    con.execute('UPDATE schema_version SET version = 1')
    con.commit()
    con.close()
    con = db.connect()
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == db.SCHEMA_VERSION
