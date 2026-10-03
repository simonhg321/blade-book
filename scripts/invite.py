#!/usr/bin/env python3
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Invite a collector who will not hand out an email address (2026-09-23):
a handle + password account with a synthetic, never-mailed address.

    python3 scripts/invite.py <handle> [--name "Display Name"] [--password PW | --generate]
    python3 scripts/invite.py <handle> --reset [--password PW | --generate]

With neither --password nor --generate the password is prompted for (no
echo). --generate prints a fresh one ONCE; hand it over and never paste it
anywhere that persists. This script is the ONLY way such an account comes
to exist — there is no self-serve route, so everyone else still needs an
email. Run as shg from /home/shg/blade-book (the live checkout)."""
import getpass
import secrets
import sys

sys.path.insert(0, __file__.rsplit('/scripts/', 1)[0])

from bb import auth, config, db  # noqa: E402

USAGE = 'usage: invite.py <handle> [--name NAME] [--password PW | --generate] [--reset]'


def _parse(argv):
    opts = {'handle': None, 'name': None, 'password': None, 'generate': False, 'reset': False}
    it = iter(argv)
    for a in it:
        if a == '--name':
            opts['name'] = next(it, None)
        elif a == '--password':
            opts['password'] = next(it, None)
        elif a == '--generate':
            opts['generate'] = True
        elif a == '--reset':
            opts['reset'] = True
        elif a.startswith('-') or opts['handle']:
            return None
        else:
            opts['handle'] = a
    return opts if opts['handle'] else None


def main(argv):
    opts = _parse(argv)
    if opts is None:
        print(USAGE)
        return 1
    handle = opts['handle'].strip().lower().lstrip('@')
    if auth.slugify_handle(handle) != handle:
        print(f'bad handle {handle!r}: 3–24 chars, a–z 0–9 and hyphens, not reserved')
        return 1
    config.load()
    con = db.connect()
    try:
        existing = db.get_user_by_handle(con, handle)
        if existing and not opts['reset']:
            print(f'@{handle} already exists — add --reset to set a new password')
            return 1
        if existing and not existing['password_hash']:
            print(f'@{handle} is an email account, not an invited one — nothing to reset')
            return 1
        if not existing and opts['reset']:
            print(f'@{handle} does not exist — drop --reset to create it')
            return 1
        if opts['generate']:
            password = secrets.token_urlsafe(18)
        elif opts['password'] is not None:
            password = opts['password']
        else:
            password = getpass.getpass('password: ')
            if password != getpass.getpass('again: '):
                print('passwords differ')
                return 1
        if len(password) < 8:
            print('password: 8 characters or more')
            return 1
        if existing:
            db.set_password(con, existing['id'], password)
            print(f'@{handle}: password reset')
        else:
            uid = db.create_password_user(con, handle, opts['name'], password)
            print(f'@{handle}: created (id {uid}, no email, verified)')
        if opts['generate']:
            print(f'password: {password}')
    finally:
        con.close()
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
