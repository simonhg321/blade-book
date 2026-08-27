# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return f.read()


def test_supervisor_conf_matches_paths_and_port():
    s = _read('deploy/supervisor-blade_book.conf')
    assert '[program:blade_book]' in s
    assert '127.0.0.1:5004' in s and 'app:app' in s
    assert 'directory=/home/shg/blade-book' in s
    for d in ('/etc/blade-book', '/var/lib/blade-book', '/var/log/blade-book'):
        assert d in s
    assert 'billboard' not in s


def test_apache_conf_proxies_only_the_api_prefix():
    s = _read('deploy/apache-blade-book.conf')
    assert 'ProxyPass        /blade-book/api/  http://127.0.0.1:5004/blade-book/api/' in s
    assert 'Alias /blade-book /var/www/html/blade-book' in s
    assert '5003' not in s


def test_install_and_backup_scripts_are_idempotent_shell():
    inst = _read('scripts/install.sh')
    assert inst.startswith('#!/bin/bash')
    assert 'set -euo pipefail' in inst
    assert 'mkdir -p' in inst and 'supervisorctl' in inst and 'apache2ctl configtest' in inst
    bk = _read('scripts/backup.sh')
    assert '/home/backup' in bk and 'blade-book-' in bk
    assert re.search(r'ls -1t .*\| tail -n \+15 \| xargs', bk)  # keep 14


def test_runbook_move_exists_and_names_the_steps():
    s = _read('docs/RUNBOOK-move.md')
    for word in ('supervisorctl stop blade_book', 'rsync', '/var/lib/blade-book',
                 '/etc/blade-book', 'DNS'):
        assert word in s
