# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Regression tests for security review 2026-09-04, batch B
(docs/SECURITY-REVIEW-2026-09-04.md: M2, M7, M8, M9, L1, L3, L10). Each test
is the reviewer's repro, inverted: it now asserts the safe behaviour."""
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
