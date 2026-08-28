# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/store.py — the PhotoStore seam (spec §11). Keys, never paths:
  '{owner_id}/{knife_id}/{seq}.{ext}'        original, byte-exact, EXIF intact
  '{owner_id}/{knife_id}/{seq}.thumb.jpg'    ≤400 px JPEG for the intake screen
LocalFSStore today; an S3-compatible store later implements the same four
methods and the move is a config change. Nothing outside this module knows
where bytes live.
"""
import os
import re

from bb import paths

_KEY = re.compile(r'^[A-Za-z0-9_-]+(/[A-Za-z0-9_.-]+)*$')


class BadKey(ValueError):
    pass


def validate_key(key):
    if (not key or '\\' in key or key.startswith('/') or key.endswith('/')
            or '..' in key.split('/') or not _KEY.match(key)):
        raise BadKey(f'bad store key: {key!r}')
    return key


class PhotoStore:
    def put(self, key, data):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def exists(self, key):
        raise NotImplementedError


class LocalFSStore(PhotoStore):
    def __init__(self, root):
        self.root = root

    def _path(self, key):
        return os.path.join(self.root, *validate_key(key).split('/'))

    def put(self, key, data):
        p = self._path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.part'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, p)  # atomic: a reader never sees a half-written original

    def get(self, key):
        p = self._path(key)
        try:
            with open(p, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            raise KeyError(key)

    def delete(self, key):
        p = self._path(key)
        try:
            os.unlink(p)
            return True
        except FileNotFoundError:
            return False

    def exists(self, key):
        return os.path.isfile(self._path(key))


def from_paths():
    return LocalFSStore(paths.photos_dir())
