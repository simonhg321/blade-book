# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""scripts/sub.py — the ManualBilling flip from a shell (plan 10). In-process
with the conftest env active; never run it from a shell without BLADEBOOK_*_DIR."""
import importlib.util
import os

from bb import db
from tests.test_search import _mk_knife, _mk_user

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location('sub', os.path.join(ROOT, 'scripts', 'sub.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flip_and_list(con, capsys):
    sub = _load()
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    assert sub.main(['idx-guy', 'active']) == 0
    assert 'free → active' in capsys.readouterr().out
    assert db.get_user(con, u['id'])['sub_status'] == 'active'
    assert sub.main(['idx-guy', 'lapsed']) == 0
    assert db.get_user(con, u['id'])['sub_status'] == 'lapsed'
    assert sub.main(['--list']) == 0
    out = capsys.readouterr().out
    assert 'idx-guy' in out and 'lapsed' in out and 'knives=1' in out


def test_usage_errors(con, capsys):
    sub = _load()
    _mk_user(con)
    assert sub.main([]) == 2
    assert sub.main(['idx-guy']) == 2
    assert sub.main(['idx-guy', 'gold']) == 2
    assert 'free, active, lapsed' in capsys.readouterr().err
    assert sub.main(['nobody', 'active']) == 2
    assert 'no such handle' in capsys.readouterr().err
    assert sub.main(['@idx-guy', 'active']) == 0       # a leading @ is tolerated
    assert db.get_user_by_handle(con, 'idx-guy')['sub_status'] == 'active'


def test_install_doc_mentions_the_script():
    doc = open(os.path.join(ROOT, 'docs', 'RUNBOOK-move.md')).read()
    assert 'scripts/sub.py' in doc and 'flip with sqlite3' not in doc
