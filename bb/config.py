# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/config.py — load /etc/blade-book/.env into os.environ (secrets only:
DECODER_MODEL, ANTHROPIC_API_KEY, RESEND_API_KEY, OIDC client ids, SESSION_KEY).
Paths never live in .env; they come from bb/paths.py."""
import os

from dotenv import load_dotenv

from bb import paths


def load():
    f = paths.env_file()
    if os.path.exists(f):
        load_dotenv(f, override=False)


def get(name, default=None):
    return os.environ.get(name, default)


TRUE_WORDS = ('1', 'true', 'yes', 'on')


def hard_gate():
    """False (the default) = early access: blade-book is free and open. The
    paid gate (bb/billing.can_add), the paid search filters and the board's
    7-day rule (bb/db.board_min_knife_age_days) all switch on together with
    BLADEBOOK_HARD_GATE=1 in /etc/blade-book/.env + restart. Simon's call."""
    return (get('BLADEBOOK_HARD_GATE') or '').strip().lower() in TRUE_WORDS
