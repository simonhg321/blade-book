# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Regression tests for security review 2026-09-04, batch B
(docs/SECURITY-REVIEW-2026-09-04.md: M2, M7, M8, M9, L1, L3, L10). Each test
is the reviewer's repro, inverted: it now asserts the safe behaviour."""
import io
import os

from PIL import Image

from bb import publish
from tests.conftest import magic_link_from, signed_in

A = '/blade-book/api/auth'
K = '/blade-book/api/knives'


# --- L10: authed API JSON is never cached -------------------------------------

def test_api_json_is_no_store(client, mailer):
    signed_in(client, mailer)
    r = client.get(A + '/me')
    assert r.status_code == 200
    assert r.headers.get('Cache-Control') == 'no-store'
    # anonymous JSON too, and the healthz probe
    assert client.get('/blade-book/api/healthz').headers.get('Cache-Control') == 'no-store'
    assert client.get(A + '/providers').headers.get('Cache-Control') == 'no-store'


# --- M9: the JPEG comment does not survive the "all metadata dropped" re-encode --

class _MemStore:
    def __init__(self):
        self.d = {}

    def put(self, key, data):
        self.d[key] = data

    def get(self, key):
        return self.d[key]


def _jpeg_with_comment(comment=b'shot at 47.6N 117.4W by Simon', size=(300, 200)):
    buf = io.BytesIO()
    Image.new('RGB', size, (200, 120, 40)).save(buf, 'JPEG', comment=comment)
    return buf.getvalue()


def _knife(store, data, tag='K01'):
    store.put('1/1/1.jpg', data)
    return {'tag': tag, 'hero_photo': 1, 'photos': [{'seq': 1, 'store_key': '1/1/1.jpg'}]}


def test_jpeg_comment_is_stripped_at_publish(tmp_path):
    store = _MemStore()
    data = _jpeg_with_comment()
    assert Image.open(io.BytesIO(data)).info.get('comment')            # the source really carries it
    hero, thumb = publish.export_hero(store, _knife(store, data), 'sam', str(tmp_path))
    for name in (hero, thumb):
        out = Image.open(os.path.join(tmp_path, name))
        assert 'comment' not in out.info, name
        assert b'Simon' not in open(os.path.join(tmp_path, name), 'rb').read()
