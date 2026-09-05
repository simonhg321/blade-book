# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/cdn.py — Cloudflare edge cache purge (security review L3).

The edge caches img/*.jpg for 4 h, so a photo that went private, a knife that
went back to draft, or a whole register that was deleted stays fetchable by
URL until the TTL runs out. Purge-by-URL is the free-plan tool (prefix purge
is Enterprise), 30 URLs per call. Best effort: disabled without the two env
keys, and a failure is a WARNING, never an exception — the bundle on disk is
the source of truth and the edge catches up on its own within 4 h anyway.

Env (docs/ENV.md): CF_API_TOKEN (Zone → Cache Purge permission, this zone
only), CF_ZONE_ID (Cloudflare dashboard → blade-book.com → Overview).
"""
import logging
import os

import requests

from bb import auth, config, paths

log = logging.getLogger('blade-book.cdn')

BATCH = 30            # Cloudflare's per-call limit for purge-by-URL
TIMEOUT_S = 10


def enabled():
    return bool(config.get('CF_API_TOKEN') and config.get('CF_ZONE_ID'))


def public_url(handle, rel):
    return f'{auth.base_url()}{paths.URL_PREFIX}/@{handle}/{rel}'


def bundle_files(root):
    """Relative '/'-joined paths of every regular file under a bundle dir."""
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            out.append(os.path.relpath(os.path.join(dirpath, f), root).replace(os.sep, '/'))
    return out


def purge_urls(urls):
    """Purge these absolute URLs from the edge. Returns how many were sent
    (0 when disabled, empty, or every call failed)."""
    urls = list(urls)
    if not urls or not enabled():
        return 0
    endpoint = f"https://api.cloudflare.com/client/v4/zones/{config.get('CF_ZONE_ID')}/purge_cache"
    headers = {'Authorization': f"Bearer {config.get('CF_API_TOKEN')}", 'Content-Type': 'application/json'}
    sent = 0
    for i in range(0, len(urls), BATCH):
        batch = urls[i:i + BATCH]
        try:
            r = requests.post(endpoint, json={'files': batch}, headers=headers, timeout=TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — never let the edge take the app down
            log.warning('cloudflare purge failed for %d urls: %r', len(batch), e)
            continue
        ok = r.status_code == 200
        try:
            ok = ok and bool(r.json().get('success'))
        except ValueError:
            ok = False
        if ok:
            sent += len(batch)
        else:
            log.warning('cloudflare purge refused (%s) for %d urls', r.status_code, len(batch))
    if sent:
        log.info('cloudflare purge: %d urls', sent)
    return sent
