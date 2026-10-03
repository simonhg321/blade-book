# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""
bb/paths.py — single source of truth for every blade-book path.

Production (stark, later its own box — the move is a rsync because of this file):
  CODE_DIR   = /home/shg/blade-book      git repo, code only
  CONFIG_DIR = /etc/blade-book           .env (secrets), 640
  DATA_DIR   = /var/lib/blade-book       blade-book.db + photos/, 700
  LOG_DIR    = /var/log/blade-book       app log + ai_calls.jsonl
  WWW_DIR    = /var/www/html/blade-book  static bundles Apache serves

Override any of them with BLADEBOOK_<NAME>_DIR (tests do; dev boxes do).
"""
import os

CODE_DIR = os.environ.get(
    'BLADEBOOK_CODE_DIR',
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_DIR = os.environ.get('BLADEBOOK_CONFIG_DIR', '/etc/blade-book')
DATA_DIR = os.environ.get('BLADEBOOK_DATA_DIR', '/var/lib/blade-book')
LOG_DIR = os.environ.get('BLADEBOOK_LOG_DIR', '/var/log/blade-book')
WWW_DIR = os.environ.get('BLADEBOOK_WWW_DIR', '/var/www/html/blade-book')

PORT = int(os.environ.get('BLADEBOOK_PORT', '5004'))
URL_PREFIX = '/blade-book'
API_PREFIX = URL_PREFIX + '/api'


def db_path():
    return os.path.join(DATA_DIR, 'blade-book.db')


def env_file():
    return os.path.join(CONFIG_DIR, '.env')


def photos_dir():
    return os.path.join(DATA_DIR, 'photos')


def ai_log():
    return os.path.join(LOG_DIR, 'ai_calls.jsonl')


def access_log():
    """The web server's request log for this site. The admin activity view
    reads it; nothing here writes it. Rotations sit beside it: .1, then .N.gz."""
    return os.environ.get('BLADEBOOK_ACCESS_LOG', '/var/log/apache2/blade-book_access.log')


def ensure_dirs():
    """Create the runtime dirs we own. CONFIG_DIR is root-owned in prod and
    created by scripts/install.sh; here we only create it if we can."""
    for d in (DATA_DIR, photos_dir(), LOG_DIR, WWW_DIR):
        os.makedirs(d, exist_ok=True)
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
    except PermissionError:
        pass
