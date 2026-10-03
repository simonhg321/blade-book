# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
import re

from bb import auth, db


def test_slugify_basic_cases():
    assert auth.slugify_handle('Sam Smith') == 'sam-smith'
    assert auth.slugify_handle('  sam.smith+knives ') == 'sam-smith-knives'
    assert auth.slugify_handle('--Sam__Smith--') == 'sam-smith'
    assert auth.slugify_handle('Ünïcödé Nâme') == 'unicode-name'


def test_slugify_enforces_length_and_minimum():
    long = auth.slugify_handle('a' * 60)
    assert len(long) == 24
    assert re.fullmatch(r'[a-z0-9][a-z0-9-]{1,22}[a-z0-9]', long)
    assert auth.slugify_handle('ab') == 'ab-collector'
    assert auth.slugify_handle('') == 'collector'
    assert auth.slugify_handle('!!!') == 'collector'


def test_slugify_avoids_reserved_words():
    for word in ('admin', 'crk', 'chrisreeve', 'blade-book', 'api', 'me',
                 'search', 'about', 'terms', 'board', 'settings', 'wants', 'add'):
        assert word in auth.RESERVED_HANDLES
        assert auth.slugify_handle(word) == f'{word}-collector'
        assert auth.slugify_handle(word.upper()) == f'{word}-collector'


def test_unique_handle_appends_counter(env):
    con = db.connect()
    assert auth.unique_handle(con, 'sam') == 'sam'
    db.create_user(con, 'sam@example.com', 'sam')
    assert auth.unique_handle(con, 'sam') == 'sam-2'
    db.create_user(con, 'sam2@example.com', 'sam-2')
    assert auth.unique_handle(con, 'sam') == 'sam-3'


def test_unique_handle_keeps_max_length_when_suffixing(env):
    con = db.connect()
    base = 'a' * 24
    db.create_user(con, 'a@example.com', base)
    h = auth.unique_handle(con, base)
    assert h == 'a' * 22 + '-2' and len(h) == 24


def test_handle_for_email_uses_local_part(env):
    con = db.connect()
    assert auth.handle_for_email(con, 'Sam.Smith@example.com') == 'sam-smith'
    db.create_user(con, 'sam.smith@example.com', 'sam-smith')
    assert auth.handle_for_email(con, 'sam.smith@example.com') == 'sam-smith-2'
    assert auth.handle_for_email(con, 'admin@example.com') == 'admin-collector'
