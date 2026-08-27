# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
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
