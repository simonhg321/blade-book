# Security Review Batch B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the seven batch-B findings of the 2026-09-04 security review — M2 login CSRF, M7 publish-side pixel cap, M8 handle tombstones, M9 JPEG COM strip, L1 real sign-out, L3 Cloudflare purge, L10 `no-store` on API JSON — each with a regression test, on one branch, mergeable in one restart.

**Architecture:** Every fix lands where the review points: `bb/routes/auth.py` + `bb/auth.py` + `bb/db.py` for M2/L1, `bb/publish.py` + `bb/photos.py` + `bb/routes/knives.py` for M7/M9, `bb/account.py` + `bb/db.py` for M8, a new `bb/cdn.py` for L3, `app.py` for L10. One schema bump (v10 → v11): `magic_tokens.flow`, new `sessions` and `released_handles` tables. Sessions become rows (a cookie carries a session id whose row must exist), so plain sign-out truly revokes and sign-out-everywhere still rotates the per-user secret.

**Tech Stack:** Python 3.12, Flask 3, SQLite (WAL), Pillow, requests, pytest. App runs as gunicorn program `blade_book` (underscore) under supervisor; Apache in front; Cloudflare in front of that.

**Spec:** `/home/shg/blade-book/docs/SECURITY-REVIEW-2026-09-04.md` (rows M2, M7, M8, M9, L1, L3, L10) and the batch-A precedent in `tests/test_sec_review_a.py`.

## Global Constraints

- Repo: `/home/shg/blade-book`. Branch `sec-batch-b` off `main` (`6617609` or later), in a worktree. Never work on `main` directly.
- **LIVE-DB GUARD:** any non-pytest Python run (a shell, a script, a one-off) MUST first `export BLADEBOOK_CONFIG_DIR=/tmp/bbx/config BLADEBOOK_DATA_DIR=/tmp/bbx/data BLADEBOOK_LOG_DIR=/tmp/bbx/log BLADEBOOK_WWW_DIR=/tmp/bbx/www`. `bb.paths` defaults to `/var/lib/blade-book` and a bare `db.connect()` migrates the LIVE database. pytest is safe (conftest re-points every path).
- Tests: `cd <worktree> && python3 -m pytest -q` (system python3; no venv). 543 tests pass on `main` at the start; the suite must stay green after every task.
- Copyright header on every new file: `# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved`.
- Log addresses only where the code already does; never log tokens, cookies, or session ids. No secrets in tests or docs.
- Schema: `db.SCHEMA_VERSION` goes 10 → 11 exactly once (Task 4 does it; later tasks reuse 11). New tables go in `db.SCHEMA` (`CREATE TABLE IF NOT EXISTS`, run on every connect); only the `ALTER` needs a `MIGRATIONS[11]` entry. Old workers re-stamp the old version until restart, so every migration statement must be idempotent (`_migrate` already tolerates `duplicate column`).
- Frontend copy is lower-case, terse, cream palette (`#f6f1e7`), system-ui font — match `bb/auth.py:UNAUTHENTICATED_HTML`.
- Commit after every task with a one-line message prefixed `sec-b:`; end the body with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- No pushes, no restart, no live-DB migration from a subagent. Merge + restart is the lead's job, same minute, after an independent diff review.

---

## File map

| File | Change |
|---|---|
| `app.py` | L10: `after_app_request` sets `Cache-Control: no-store` on API JSON |
| `bb/publish.py` | M7: `export_hero` opens through `photos._open`; M9: pops `comment`; L3: purges vanished bundle files after a rebuild |
| `bb/photos.py` | M7: `ingest` raises `Undecodable` instead of storing a dimensionless photo |
| `bb/routes/knives.py` | M7: `Undecodable` → 415 |
| `bb/db.py` | v11: `magic_tokens.flow`, `sessions`, `released_handles`; helpers for each |
| `bb/auth.py` | L1: session rows; M8: `handle_exists` honours tombstones (via db) |
| `bb/routes/auth.py` | M2: OIDC flow cookie; magic-link flow cookie + confirm interstitial |
| `bb/account.py` | M8: release old handle on rename/delete; L3: purge on `remove_public_surface` |
| `bb/cdn.py` (new) | L3: Cloudflare purge-by-URL, env-gated, best-effort |
| `docs/ENV.md` | `CF_API_TOKEN`, `CF_ZONE_ID` |
| `docs/SECURITY-REVIEW-2026-09-04.md` | Status line for batch B |
| `tests/test_sec_review_b.py` (new) | one regression test per finding, reviewer repro inverted |
| `tests/test_cdn.py` (new) | purge unit tests |
| `tests/test_photos.py`, `tests/test_photos_api.py` | two tests flip from "stored without thumb" to "rejected 415" |

---

### Task 1: L10 — `Cache-Control: no-store` on API JSON

**Files:**
- Modify: `app.py:115` (before the error handlers)
- Test: `tests/test_sec_review_b.py` (create)

**Interfaces:**
- Produces: every response under `paths.API_PREFIX` whose mimetype is `application/json` carries `Cache-Control: no-store`. Non-API paths and non-JSON responses (photo bytes, HTML) are untouched.

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sec_review_b.py::test_api_json_is_no_store -q`
Expected: FAIL — `assert None == 'no-store'`

- [ ] **Step 3: Implement**

In `app.py`, after `_same_origin_only` and before `@api.app_errorhandler(404)`:

```python
@api.after_app_request
def _api_json_no_store(resp):
    """Review L10: authed JSON (register rows, settings, /me) must never sit in
    a shared or back/forward cache. Only API JSON — photo bytes keep their own
    caching, HTML is Apache's business."""
    if request.path.startswith(paths.API_PREFIX) and resp.mimetype == 'application/json':
        resp.headers['Cache-Control'] = 'no-store'
    return resp
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m pytest -q`
Expected: all pass (544).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_sec_review_b.py
git commit -m "sec-b: L10 no-store on API JSON"
```

---

### Task 2: M9 — strip the JPEG COM comment at publish

**Files:**
- Modify: `bb/publish.py:114-138` (`export_hero`)
- Test: `tests/test_sec_review_b.py`

**Interfaces:**
- Consumes: `publish.export_hero(store, k, handle, img_dir) -> (hero_name, thumb_name)`; `bb.store.LocalFSStore`-like `store.put(key, bytes)` / `store.get(key)`.
- Produces: the display and thumb JPEGs carry no COM segment.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sec_review_b.py`:

```python
import io
import os

from PIL import Image

from bb import publish


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


# --- M9: the JPEG comment does not survive the "all metadata dropped" re-encode --

def test_jpeg_comment_is_stripped_at_publish(tmp_path):
    store = _MemStore()
    data = _jpeg_with_comment()
    assert Image.open(io.BytesIO(data)).info.get('comment')            # the source really carries it
    hero, thumb = publish.export_hero(store, _knife(store, data), 'sam', str(tmp_path))
    for name in (hero, thumb):
        out = Image.open(os.path.join(tmp_path, name))
        assert 'comment' not in out.info, name
        assert b'Simon' not in open(os.path.join(tmp_path, name), 'rb').read()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sec_review_b.py::test_jpeg_comment_is_stripped_at_publish -q`
Expected: FAIL — `assert 'comment' not in out.info` (Pillow copies `info['comment']` through `convert` and writes it on save).

- [ ] **Step 3: Implement**

In `bb/publish.py` `export_hero`, replace the `try:` block body:

```python
    try:
        img = Image.open(io.BytesIO(store.get(p['store_key'])))
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')          # re-encode: EXIF/XMP/GPS dropped ...
        img.info.pop('comment', None)     # ... and the JPEG COM segment, which
                                          # Pillow would otherwise copy through
                                          # convert() and write on save (review M9)
    except (OSError, KeyError, UnidentifiedImageError):
        return None, None
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bb/publish.py tests/test_sec_review_b.py
git commit -m "sec-b: M9 strip JPEG COM at publish"
```

---

### Task 3: M7 — the pixel cap holds at publish; undecodable uploads are refused

**Files:**
- Modify: `bb/photos.py:46-52, 112-126`
- Modify: `bb/routes/knives.py:431-436`
- Modify: `bb/publish.py:114-138`
- Modify: `tests/test_photos.py:53-56`, `tests/test_photos_api.py:56-66`
- Test: `tests/test_sec_review_b.py`

**Interfaces:**
- Produces: `photos.Undecodable(ValueError)`; `photos.ingest` raises it when Pillow cannot identify or load the file (previously returned an `Ingested` with `width=None`). `POST …/photos/<seq>` → `415 {"error": "could not read that image — use JPEG, PNG, HEIC or WebP"}`. `publish.export_hero` opens through `photos._open` (declared-size cap, vetted decoders only) and returns `(None, None)` on `TooBig`, logging at WARNING.

Rationale (from the review): the cap is enforced at ingest only for files Pillow can decode; a BMP/PPM renamed `.jpg` is "undecodable", stored uncapped, then fully decoded in `export_hero` (36 Mpx confirmed). Closing both ends: ingest refuses what it cannot decode (the AI decoder could not have read it either — `bb/decode.py:77`), and publish uses the same guarded opener. HEIC on the box is decodable (pillow-heif installed); the two existing "stored without thumb" tests described the pre-pillow-heif world and flip to "rejected".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sec_review_b.py`:

```python
import pytest

from bb import photos
from tests.test_photos_api import _up


def _ppm(w, h):
    """A binary PPM: Pillow decodes it fully, no draft mode, no size check
    until the pixels are in memory — the reviewer's bypass file."""
    return b'P6\n%d %d\n255\n' % (w, h) + b'\x00' * (w * h * 3)


# --- M7: an undecodable file is refused at ingest, and publish keeps the cap ----

def test_ingest_refuses_what_it_cannot_decode():
    with pytest.raises(photos.Undecodable):
        photos.ingest(b'not really an image', 'shot.heic')
    with pytest.raises(photos.Undecodable):
        photos.ingest(_ppm(4, 4), 'renamed.jpg')                        # PPM is not a vetted decoder


def test_upload_of_undecodable_file_is_415(client, mailer):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = _up(client, kid, 1, _ppm(4, 4), name='IMG_1.jpg')
    assert r.status_code == 415
    assert 'could not read' in r.get_json()['error']
    assert client.get(f'{K}/{kid}/photos/1/original').status_code == 404   # nothing stored


def test_export_hero_refuses_oversize_via_guarded_open(tmp_path, monkeypatch):
    monkeypatch.setattr(photos, 'MAX_PIXELS_NON_JPEG', 100)              # 10x10 PNG will be "too big"
    store = _MemStore()
    buf = io.BytesIO()
    Image.new('RGB', (20, 20)).save(buf, 'PNG')
    store.put('1/1/1.png', buf.getvalue())
    k = {'tag': 'K02', 'hero_photo': 1, 'photos': [{'seq': 1, 'store_key': '1/1/1.png'}]}
    assert publish.export_hero(store, k, 'sam', str(tmp_path)) == (None, None)
    assert not os.path.exists(os.path.join(tmp_path, 'K02.jpg'))


def test_export_hero_never_opens_unvetted_formats(tmp_path):
    store = _MemStore()
    store.put('1/1/1.jpg', _ppm(4, 4))                                    # a PPM wearing a .jpg key
    k = {'tag': 'K03', 'hero_photo': 1, 'photos': [{'seq': 1, 'store_key': '1/1/1.jpg'}]}
    assert publish.export_hero(store, k, 'sam', str(tmp_path)) == (None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sec_review_b.py -q -k "undecodable or export_hero"`
Expected: 4 FAIL — `AttributeError: module 'bb.photos' has no attribute 'Undecodable'`, 201 instead of 415, and the PPM/PNG heroes get written.

- [ ] **Step 3: Implement `bb/photos.py`**

After `class BadType`:

```python
class Undecodable(ValueError):
    """Pillow could not identify or load the file with a vetted decoder.
    Refused at ingest (review M7): a file we cannot decode here cannot be
    thumbed, decoded by the model, or published — and, stored uncapped, it
    would be the one file that reaches publish's decoder at full size."""
```

Replace the tail of `ingest`:

```python
    sha = hashlib.sha256(data).hexdigest()
    img, width, height = _decode(data)
    if img is None:
        raise Undecodable(f'{filename!r}: not a decodable {ext}')
    thumb = ImageOps.exif_transpose(img).convert('RGB')
```

Update the module docstring's last sentence to: `A file Pillow can't decode with a vetted decoder is refused (photos.Undecodable) — see security review M7.`

Update the `Ingested` dataclass comment expectations: `width`, `height`, `thumb` are now always set (leave the types as they are — `decode.py` and callers still handle `None` defensively).

- [ ] **Step 4: Implement `bb/routes/knives.py`**

In `upload_photo`, extend the `except` chain:

```python
        try:
            ing = photos.ingest(data, f.filename)
        except photos.TooBig:
            return jsonify({'error': f'photo over {photos.MAX_PHOTO_BYTES // (1024 * 1024)} MB'}), 400
        except photos.BadType:
            return jsonify({'error': 'not an accepted image type'}), 415
        except photos.Undecodable:
            log.info('undecodable upload refused for @%s: %s', g.user['handle'], f.filename[:80])
            return jsonify({'error': 'could not read that image — use JPEG, PNG, HEIC or WebP'}), 415
```

- [ ] **Step 5: Implement `bb/publish.py`**

Add `from bb import photos` to the imports (check it does not create a cycle: `photos` imports only Pillow/stdlib). Replace the open in `export_hero`:

```python
    try:
        img = photos._open(store.get(p['store_key']))     # vetted decoders + declared-size cap (review M7)
        if img is None:
            return None, None
        img.load()
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')          # re-encode: EXIF/XMP/GPS dropped ...
        img.info.pop('comment', None)     # ... and the JPEG COM segment (review M9)
    except photos.TooBig as e:
        log.warning('hero for %s skipped at publish: %s', k.get('tag'), e)
        return None, None
    except (OSError, KeyError, UnidentifiedImageError):
        return None, None
```

Note `log` is defined further down in `publish.py` (`log = logging.getLogger('blade-book.publish')` after `build_user`); module-level name lookup at call time is fine, but move that line up to just below the imports for clarity.

- [ ] **Step 6: Flip the two legacy tests**

`tests/test_photos.py:53-56` becomes:

```python
def test_ingest_undecodable_but_allowed_ext_is_refused():
    with pytest.raises(photos.Undecodable):
        photos.ingest(b'not really an image', 'shot.heic')
```

(add `import pytest` at the top if missing).

`tests/test_photos_api.py:56-66` becomes:

```python
def test_undecodable_heic_is_refused_415(client, mailer, app, env):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = _up(client, kid, 1, b'\x00\x00\x00\x18ftypheic-not-really', name='IMG_9.HEIC')
    assert r.status_code == 415
    assert not app.config['STORE'].exists(f"{me['id']}/{kid}/1.heic")
    assert client.get(f'{K}/{kid}/photos/1/original').status_code == 404
```

Search the suite for other callers that relied on `width is None` uploads: `grep -n "not-really\|not really an image" tests/` and fix each the same way.

- [ ] **Step 7: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add bb/photos.py bb/routes/knives.py bb/publish.py tests/
git commit -m "sec-b: M7 guarded open at publish, refuse undecodable uploads"
```

---

### Task 4: M8 — released handles are tombstoned for 90 days (schema v11)

**Files:**
- Modify: `bb/db.py:17` (`SCHEMA_VERSION = 11`), SCHEMA (new table after `deleted_users`), `MIGRATIONS` (entry 11 — see Task 5 for the ALTER; this task adds the dict key with the tables' DDL is NOT needed since SCHEMA creates them), `handle_exists`, `purge_auth_tables`, new `release_handle`
- Modify: `bb/account.py:76-86` (`change_handle`), `157-185` (`delete_account`)
- Test: `tests/test_sec_review_b.py`, `tests/test_db_migration.py` (version assertion, if any)

**Interfaces:**
- Produces: `db.HANDLE_TOMBSTONE_DAYS = 90`; `db.release_handle(con, handle)`; `db.handle_exists(con, handle)` is True while the handle is live OR released within the window; `db.purge_auth_tables` drops expired tombstones. `auth.unique_handle` and `account.validate_new_handle` need no change — both go through `handle_exists`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sec_review_b.py`:

```python
from datetime import datetime, timedelta, timezone

from bb import account, auth, db


# --- M8: a released handle cannot be claimed by a stranger for 90 days ----------

def _user(con, email='sam@example.com', handle='sam'):
    uid = db.create_user(con, email, handle)
    db.rotate_session_secret(con, uid)
    return db.get_user(con, uid)


def test_renamed_handle_is_tombstoned(con):
    u = _user(con)
    account.change_handle(con, u, 'samuel-k')
    assert db.handle_exists(con, 'sam')                                    # still "taken"
    assert auth.unique_handle(con, 'sam') == 'sam-2'                       # sign-ups skip it
    other = _user(con, 'o@example.com', 'other')
    with pytest.raises(ValueError, match='taken'):
        account.validate_new_handle(con, other, 'sam')


def test_deleted_handle_is_tombstoned(env, con):
    from bb import store as store_mod
    u = _user(con)
    account.delete_account(con, store_mod.from_paths(), u)
    assert db.handle_exists(con, 'sam')
    assert auth.handle_for_email(con, 'sam@other.example') == 'sam-2'


def test_tombstone_expires_after_90_days(con):
    db.release_handle(con, 'ghost')
    assert db.handle_exists(con, 'ghost')
    old = (datetime.now(timezone.utc) - timedelta(days=db.HANDLE_TOMBSTONE_DAYS + 1)).isoformat()
    con.execute('UPDATE released_handles SET released_at = ? WHERE handle = ?', (old, 'ghost'))
    con.commit()
    assert not db.handle_exists(con, 'ghost')
    db.purge_auth_tables(con)
    assert con.execute('SELECT count(*) FROM released_handles').fetchone()[0] == 0


def test_schema_is_v11(con):
    assert con.execute('SELECT version FROM schema_version').fetchone()[0] == 11
    assert db.SCHEMA_VERSION == 11
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sec_review_b.py -q -k "tombstone or v11"`
Expected: FAIL — `no such table: released_handles`, `AttributeError: release_handle`, `10 == 11`.

- [ ] **Step 3: Implement `bb/db.py`**

Constants (near `MAX_BOARD_OFFSET`):

```python
HANDLE_TOMBSTONE_DAYS = 90       # a released handle stays unclaimable this long (review M8)
```

`SCHEMA_VERSION = 11`.

SCHEMA, after the `deleted_users` table:

```sql
CREATE TABLE IF NOT EXISTS released_handles (
  handle TEXT PRIMARY KEY,
  released_at TEXT NOT NULL
);
```

MIGRATIONS, add (the ALTER belongs to Task 5 but lives in the same entry — write it now so the key exists once):

```python
    # security review 2026-09-04 batch B: released handles + session rows (tables
    # come from SCHEMA on connect); magic_tokens.flow binds a link to the browser
    # that asked for it. All idempotent — old workers re-stamp 10 until restart.
    11: ['ALTER TABLE magic_tokens ADD COLUMN flow TEXT'],
```

Functions, next to `handle_exists`:

```python
def handle_exists(con, handle):
    """Live handles, plus handles released (rename/delete) within
    HANDLE_TOMBSTONE_DAYS — every shared /@handle/ link keeps pointing at
    nobody rather than at a stranger (review M8)."""
    if con.execute('SELECT 1 FROM users WHERE handle = ?', (handle,)).fetchone():
        return True
    cutoff = _plus(-HANDLE_TOMBSTONE_DAYS * 24 * 60)
    return con.execute('SELECT 1 FROM released_handles WHERE handle = ? AND released_at > ?',
                       (handle, cutoff)).fetchone() is not None


def release_handle(con, handle):
    con.execute('INSERT OR REPLACE INTO released_handles (handle, released_at) VALUES (?, ?)',
                (handle, now()))
    con.commit()
```

In `purge_auth_tables`, before `con.commit()`:

```python
    con.execute('DELETE FROM released_handles WHERE released_at < ?',
                (_plus(-HANDLE_TOMBSTONE_DAYS * 24 * 60),))
```

- [ ] **Step 4: Implement `bb/account.py`**

In `change_handle`, after `fresh = db.set_handle(...)`:

```python
    db.release_handle(con, old)
```

In `delete_account`, right after `db.tombstone_email(con, user['email'])`:

```python
    db.release_handle(con, user['handle'])
```

- [ ] **Step 5: Fix any version-pinned test**

Run: `grep -rn "== 10\b\|SCHEMA_VERSION" tests/ | grep -v test_sec_review_b` and update any assertion that pins 10 (e.g. `tests/test_db_migration.py`) to 11.

- [ ] **Step 6: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add bb/db.py bb/account.py tests/
git commit -m "sec-b: M8 released handles tombstoned 90 days (schema v11)"
```

---

### Task 5: L1 — plain sign-out revokes the cookie (session rows)

**Files:**
- Modify: `bb/db.py` SCHEMA (new `sessions` table), new `create_session` / `session_alive` / `delete_session` / `delete_user_sessions`, `purge_auth_tables`
- Modify: `bb/auth.py:86-116` (`login`, `logout`, `logout_everywhere`, `current_user`)
- Test: `tests/test_sec_review_b.py`

**Interfaces:**
- Consumes: schema v11 from Task 4.
- Produces: cookie payload `{'uid', 'ssh', 'sid'}`; `db.create_session(con, user_id) -> sid` (random, stored hashed); `db.session_alive(con, user_id, sid) -> bool`; `db.delete_session(con, sid)`; `db.delete_user_sessions(con, user_id)`. `auth.logout()` deletes the row for the current cookie; `auth.logout_everywhere` deletes all rows AND rotates the secret (unchanged semantics for "everywhere"). A cookie with no `sid` (pre-deploy) is simply not signed in — the three live users sign in once more after the restart (say so in the handoff).

Design note: rows rather than rotating the secret on plain sign-out, because rotating would turn "sign out" into "sign out everywhere" and the settings page offers both. One indexed lookup per authed request.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sec_review_b.py`:

```python
# --- L1: sign-out revokes THIS cookie; sign-out-everywhere still revokes all ----

def _cookie(client):
    c = client.get_cookie('bb_session', path='/blade-book')
    assert c is not None
    return c.value


def test_signout_invalidates_a_captured_cookie(client, mailer, app):
    signed_in(client, mailer)
    stolen = _cookie(client)
    assert client.post(A + '/signout').get_json() == {'ok': True}
    thief = app.test_client()
    thief.set_cookie('bb_session', stolen, path='/blade-book')
    assert thief.get(A + '/me').status_code == 401                       # the row is gone


def test_signout_leaves_other_devices_signed_in(client, mailer, app):
    signed_in(client, mailer)
    phone = app.test_client()
    signed_in(phone, mailer)                                              # second session, same user
    assert client.post(A + '/signout').status_code == 200
    assert phone.get(A + '/me').status_code == 200


def test_signout_all_still_revokes_every_device(client, mailer, app):
    signed_in(client, mailer)
    phone = app.test_client()
    signed_in(phone, mailer)
    assert client.post(A + '/signout-all').status_code == 200
    assert phone.get(A + '/me').status_code == 401
    assert client.get(A + '/me').status_code == 401


def test_cookie_without_session_row_is_not_signed_in(client, mailer, con):
    signed_in(client, mailer)
    con.execute('DELETE FROM sessions')
    con.commit()
    assert client.get(A + '/me').status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sec_review_b.py -q -k signout`
Expected: `test_signout_invalidates_a_captured_cookie` FAIL (thief gets 200); `test_cookie_without_session_row` FAIL (`no such table: sessions`); the other two pass already (keep them — they pin the semantics).

- [ ] **Step 3: Implement `bb/db.py`**

SCHEMA, after `released_handles`:

```sql
CREATE TABLE IF NOT EXISTS sessions (
  sid_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
```

Functions, after `rotate_session_secret`:

```python
SESSION_DAYS = 90                  # must match PERMANENT_SESSION_LIFETIME in app.py


def create_session(con, user_id):
    """One row per signed-in browser (review L1). The cookie carries the raw
    sid; only its hash is stored, so the DB never holds a usable cookie."""
    sid = secrets.token_urlsafe(24)
    con.execute('INSERT INTO sessions (sid_hash, user_id, created, last_seen) VALUES (?, ?, ?, ?)',
                (_sha(sid), user_id, now(), now()))
    con.commit()
    return sid


def session_alive(con, user_id, sid):
    row = con.execute('SELECT 1 FROM sessions WHERE sid_hash = ? AND user_id = ?',
                      (_sha(sid), user_id)).fetchone()
    return row is not None


def delete_session(con, sid):
    con.execute('DELETE FROM sessions WHERE sid_hash = ?', (_sha(sid),))
    con.commit()


def delete_user_sessions(con, user_id):
    con.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
    con.commit()
```

In `purge_auth_tables`, add:

```python
    con.execute('DELETE FROM sessions WHERE created < ?', (_plus(-SESSION_DAYS * 24 * 60),))
```

- [ ] **Step 4: Implement `bb/auth.py`**

Update the module docstring: cookie carries `{'uid', 'ssh', 'sid'}`; `sid` names a `sessions` row, deleted on sign-out.

```python
def login(con, user):
    if not user['session_secret']:
        user['session_secret'] = db.rotate_session_secret(con, user['id'])
    session.clear()
    session.permanent = True
    session['uid'] = user['id']
    session['ssh'] = _secret_hash(user['session_secret'])
    session['sid'] = db.create_session(con, user['id'])
    g.user = user


def logout():
    """Revoke THIS cookie's session row (review L1), then clear the cookie.
    Opens its own connection: the callers are routes without one."""
    sid = session.get('sid')
    if sid:
        con = db.connect()
        try:
            db.delete_session(con, sid)
        finally:
            con.close()
    session.clear()
    g.user = None


def logout_everywhere(con, user_id):
    db.delete_user_sessions(con, user_id)
    db.rotate_session_secret(con, user_id)
    session.clear()
    g.user = None


def current_user(con):
    """User dict for the session cookie, or None. Cached on g per request."""
    if 'user' in g:
        return g.user
    g.user = None
    uid, ssh, sid = session.get('uid'), session.get('ssh'), session.get('sid')
    if uid and ssh and sid:
        user = db.get_user(con, uid)
        if (user and user['session_secret'] and _secret_hash(user['session_secret']) == ssh
                and db.session_alive(con, uid, sid)):
            g.user = user
    return g.user
```

Note `logout_everywhere` no longer calls `logout()` (which would open a second connection to delete a row the bulk delete already removed).

- [ ] **Step 5: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass. If `test_db_auth.py` or `test_auth_magic.py` pins the cookie payload keys, extend the expectation with `sid`.

- [ ] **Step 6: Commit**

```bash
git add bb/db.py bb/auth.py tests/
git commit -m "sec-b: L1 session rows — plain sign-out revokes the cookie"
```

---

### Task 6: M2a — OIDC callbacks are bound to the browser that started them

**Files:**
- Modify: `bb/routes/auth.py:99-143` (`oidc_start`, `oidc_callback`)
- Test: `tests/test_sec_review_b.py`

**Interfaces:**
- Produces: cookie `bb_oidc` (HttpOnly, path `paths.API_PREFIX + '/auth'`, max-age `db.STATE_TTL_MIN * 60`, `Secure` + `SameSite=None` when BASE_URL is https, else `SameSite=Lax`) whose value is the `state`. The callback refuses (`?auth=failed`, state burned, WARNING logged) unless the cookie equals the `state` parameter. The cookie is cleared on the callback response. Apple's `form_post` is a cross-site POST, which is why the cookie is `SameSite=None` in production.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sec_review_b.py`:

```python
from tests.test_auth_oidc import _fake_exchange, _start, both  # noqa: F401 — fixture re-export


# --- M2a: an OIDC callback minted elsewhere does not sign this browser in ------

def test_oidc_callback_requires_the_flow_cookie(client, both, monkeypatch, app):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-9', 'email': 'attacker@example.com',
                                            'email_verified': True}})
    q = _start(client, 'google')                                          # attacker's browser starts
    victim = app.test_client()                                            # victim never visited /auth/google
    r = victim.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.status_code == 302 and r.headers['Location'].endswith('?auth=failed')
    assert victim.get(A + '/me').status_code == 401
    # the state was burned: replaying it in the attacker's own browser fails too
    r = client.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.headers['Location'].endswith('?auth=failed')


def test_oidc_flow_cookie_is_scoped_and_short_lived(client, both):
    r = client.get('/blade-book/api/auth/google')
    assert r.status_code == 302
    c = client.get_cookie('bb_oidc', path='/blade-book/api/auth')
    assert c is not None and c.http_only and c.path == '/blade-book/api/auth'
    assert c.max_age == db.STATE_TTL_MIN * 60


def test_oidc_same_browser_still_signs_in(client, both, monkeypatch):
    _fake_exchange(monkeypatch, {'google': {'sub': 'g-1', 'email': 'sam@example.com',
                                            'email_verified': True}})
    q = _start(client, 'google')
    r = client.get(f"/blade-book/api/auth/google/callback?code=c0de&state={q['state'][0]}")
    assert r.headers['Location'].endswith('/blade-book/')
    assert client.get(A + '/me').status_code == 200
    assert client.get_cookie('bb_oidc', path='/blade-book/api/auth') is None   # cleared on callback
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sec_review_b.py -q -k oidc`
Expected: first two FAIL (victim gets signed in; no cookie), third passes except the final `is None` assertion.

- [ ] **Step 3: Implement**

In `bb/routes/auth.py`, add helpers after `_landing`:

```python
FLOW_COOKIE_PATH = paths.API_PREFIX + '/auth'


def _flow_cookie(resp, name, value, max_age):
    """A browser-binding cookie for a sign-in flow (review M2). Scoped to the
    auth routes, HttpOnly, and — because Apple's form_post callback is a
    cross-site POST — SameSite=None (which browsers only honour with Secure,
    hence the https check; plain-http test runs fall back to Lax)."""
    secure = auth.base_url().startswith('https')
    resp.set_cookie(name, value, max_age=max_age, path=FLOW_COOKIE_PATH, httponly=True,
                    secure=secure, samesite='None' if secure else 'Lax')
    return resp


def _clear_flow_cookie(resp, name):
    resp.delete_cookie(name, path=FLOW_COOKIE_PATH)
    return resp
```

`oidc_start` ends with:

```python
    resp = redirect(oidc.authorize_url(provider, state, nonce))
    return _flow_cookie(resp, 'bb_oidc', state, db.STATE_TTL_MIN * 60)
```

`oidc_callback`: after `saved = db.pop_oauth_state(...)`, extend the rejection:

```python
        bound = request.cookies.get('bb_oidc') == state
        if params.get('error') or not code or saved is None or saved['provider'] != provider.name or not bound:
            log.warning('%s callback rejected: error=%r code=%s state_ok=%s browser_bound=%s',
                        provider.name, str(params.get('error'))[:64], bool(code),
                        saved is not None, bound)
            return _clear_flow_cookie(_landing(auth='failed'), 'bb_oidc')
```

and every other return in the callback wraps the same way: `return _clear_flow_cookie(_landing(auth='failed'), 'bb_oidc')`, `... (_landing(auth='unverified'), 'bb_oidc')`, and the success `return _clear_flow_cookie(_landing(), 'bb_oidc')`.

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass. `tests/test_auth_oidc.py` drives start and callback on the same client, so it keeps passing; if any test there posts a callback from a fresh client, give it the cookie via `client.set_cookie('bb_oidc', state, path='/blade-book/api/auth')`.

- [ ] **Step 5: Commit**

```bash
git add bb/routes/auth.py tests/
git commit -m "sec-b: M2a OIDC flow cookie binds the callback to the starting browser"
```

---

### Task 7: M2b — magic links: bound clicks sign in silently, others get a confirm page

**Files:**
- Modify: `bb/db.py` (`create_magic_token` gains `flow`, new `peek_magic_token`, `consume_magic_token` unchanged)
- Modify: `bb/routes/auth.py:28-66` (`request_magic_link`, `click_magic_link`, new `confirm_magic_link`)
- Modify: `bb/auth.py` (new `CONFIRM_SIGNIN_HTML` builder next to `UNAUTHENTICATED_HTML`)
- Test: `tests/test_sec_review_b.py`, `tests/test_auth_magic.py` (existing tests keep passing — same client requests and clicks)

**Interfaces:**
- Consumes: `_flow_cookie` / `_clear_flow_cookie` from Task 6; `db.consume_magic_token`; `auth.sign_in_by_email`; `auth.current_user`.
- Produces:
  - `db.create_magic_token(con, email, ip, flow=None) -> token` stores `flow` (already a hash — the route hashes the cookie value with `db._sha`).
  - `db.peek_magic_token(con, token) -> {'email', 'flow'} | None` for a live, unused token; does not burn it.
  - Cookie `bb_magic` (random 24-byte urlsafe, max-age `db.MAGIC_TTL_MIN * 60`) set by `POST /auth/magic`.
  - `GET /auth/magic?t=…`: token dead → `?auth=expired` (as today). Cookie present and `sha(cookie) == flow` → sign in silently (as today), cookie cleared. Otherwise → 200 HTML confirm page (`auth.confirm_signin_html(email_masked, token, current_handle)`) with a same-origin POST form to `/auth/magic/confirm` carrying `t`. The page says who is signed in now, if anyone, and that continuing switches accounts.
  - `POST /auth/magic/confirm` (form-encoded, `t`): consumes and signs in → `_landing()`; dead token → `?auth=expired`. Passes `_same_origin_only` because a browser form post carries `Origin`.
  - Masking: `sam@example.com` → `s***@example.com` (first char + `***` + domain).

Why not require the cookie outright: people request a link on the phone and open it on the laptop. A confirm click costs them one tap and removes the *silent* sign-in the attacker needs (a foreign page cannot click our form, and a cross-origin POST is refused by `_same_origin_only`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_sec_review_b.py`:

```python
import re


def _token(link):
    return re.search(r'[?&]t=([^&\s]+)', link).group(1)


# --- M2b: a magic link clicked outside the requesting browser must be confirmed --

def test_same_browser_magic_link_signs_in_silently(client, mailer):
    client.post(A + '/magic', json={'email': 'sam@example.com'})
    c = client.get_cookie('bb_magic', path='/blade-book/api/auth')
    assert c is not None and c.http_only and c.max_age == db.MAGIC_TTL_MIN * 60
    r = client.get(magic_link_from(mailer))
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert client.get(A + '/me').status_code == 200
    assert client.get_cookie('bb_magic', path='/blade-book/api/auth') is None


def test_foreign_browser_gets_confirm_page_not_a_session(client, mailer, app):
    client.post(A + '/magic', json={'email': 'attacker@example.com'})
    link = magic_link_from(mailer)
    victim = app.test_client()
    r = victim.get(link)
    assert r.status_code == 200 and r.mimetype == 'text/html'
    body = r.get_data(as_text=True)
    assert 'a***@example.com' in body and 'attacker@example.com' not in body
    assert 'action="/blade-book/api/auth/magic/confirm"' in body and 'method="post"' in body
    assert victim.get(A + '/me').status_code == 401                       # nothing happened yet
    # the token is still live: the confirm consumes it
    r = victim.post(A + '/magic/confirm', data={'t': _token(link)},
                    headers={'Origin': 'http://localhost'})
    assert r.status_code == 302 and r.headers['Location'].endswith('/blade-book/')
    assert victim.get(A + '/me').get_json()['email'] == 'attacker@example.com'
    # and only once
    r = victim.post(A + '/magic/confirm', data={'t': _token(link)}, headers={'Origin': 'http://localhost'})
    assert r.headers['Location'].endswith('?auth=expired')


def test_confirm_page_names_the_signed_in_account(client, mailer, app):
    signed_in(client, mailer, 'victim@example.com')                       # @victim is signed in here
    other = app.test_client()
    other.post(A + '/magic', json={'email': 'attacker@example.com'})
    r = client.get(magic_link_from(mailer))
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert '@victim' in body and 'switch' in body.lower()
    assert client.get(A + '/me').get_json()['email'] == 'victim@example.com'   # still the victim


def test_confirm_from_foreign_origin_is_refused(client, mailer, app):
    client.post(A + '/magic', json={'email': 'attacker@example.com'})
    link = magic_link_from(mailer)
    victim = app.test_client()
    r = victim.post(A + '/magic/confirm', data={'t': _token(link)}, headers={'Origin': 'https://evil.example'})
    assert r.status_code == 403
    assert victim.get(A + '/me').status_code == 401


def test_dead_token_still_says_expired(client, mailer):
    assert client.get(A + '/magic?t=nope').headers['Location'].endswith('?auth=expired')
    assert client.post(A + '/magic/confirm', data={'t': 'nope'}, headers={'Origin': 'http://localhost'}) \
        .headers['Location'].endswith('?auth=expired')
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_sec_review_b.py -q -k "magic or confirm or dead_token"`
Expected: FAIL — no `bb_magic` cookie; the foreign browser gets a 302 and a session; `/magic/confirm` is 404.

- [ ] **Step 3: Implement `bb/db.py`**

```python
def create_magic_token(con, email, ip, flow=None):
    """flow = hash of the requesting browser's bb_magic cookie (review M2), or
    None for a request without one (non-browser clients)."""
    token = secrets.token_urlsafe(32)
    con.execute(
        'INSERT INTO magic_tokens (token_hash, email, ip, created, expires, flow) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (_sha(token), _norm_email(email), ip, now(), _plus(MAGIC_TTL_MIN), flow))
    con.commit()
    return token


def peek_magic_token(con, token):
    """{'email', 'flow'} for a live, unused token WITHOUT burning it — the
    confirm page reads it; consume_magic_token burns it. None otherwise."""
    row = con.execute(
        'SELECT email, flow FROM magic_tokens WHERE token_hash = ? AND used_at IS NULL '
        'AND expires > ?', (_sha(token), now())).fetchone()
    return dict(row) if row else None
```

- [ ] **Step 4: Implement `bb/auth.py`**

After `UNAUTHENTICATED_HTML`:

```python
def mask_email(email):
    local, _, domain = email.partition('@')
    return f'{local[:1]}***@{domain}'


def confirm_signin_html(email, token, current_handle=None):
    """The confirm page for a magic link opened outside the browser that asked
    for it (review M2): one same-origin POST stands between the link and a
    session, so a link an attacker minted cannot sign this browser in silently.
    Only the masked email and the token go in; both are escaped."""
    from markupsafe import escape
    who = escape(mask_email(email))
    action = f'{paths.API_PREFIX}/auth/magic/confirm'
    if current_handle:
        note = (f'<p>you are signed in as <b>@{escape(current_handle)}</b> — continuing '
                f'will switch this browser to {who}.</p>')
        button = 'switch account'
    else:
        note = f'<p>sign in to blade-book as <b>{who}</b>?</p>'
        button = 'continue'
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="robots" content="noindex">'
        '<title>Confirm sign-in — blade-book</title>'
        '<style>body{font-family:system-ui,sans-serif;background:#f6f1e7;color:#1a1a1a;margin:0;'
        'display:flex;min-height:100vh;align-items:center;justify-content:center}'
        'main{text-align:center;padding:2rem;max-width:26rem}h1{font-size:1.6rem;margin:0 0 .5rem}'
        'p{margin:.4rem 0}button{font:inherit;font-weight:600;padding:.6rem 1.2rem;border:2px solid #1a1a1a;'
        'border-radius:8px;background:#1a1a1a;color:#f6f1e7;cursor:pointer;margin-top:1rem}'
        'a{color:#1a1a1a}</style></head><body><main>'
        '<h1>confirm sign-in</h1>'
        f'{note}'
        '<p style="color:#666;font-size:.9rem">this link was opened in a different browser than '
        'the one that asked for it.</p>'
        f'<form method="post" action="{action}">'
        f'<input type="hidden" name="t" value="{escape(token)}">'
        f'<button type="submit">{button}</button></form>'
        f'<p style="margin-top:1.2rem"><a href="{paths.URL_PREFIX}/">no, take me home</a></p>'
        '</main></body></html>'
    )
```

- [ ] **Step 5: Implement `bb/routes/auth.py`**

`request_magic_link`: mint the flow cookie value before creating the token, store its hash, set the cookie on the 202:

```python
    flow = secrets.token_urlsafe(24)
    con = db.connect()
    try:
        reason = auth.check_rate_limits(con, email)
        if reason:
            log.warning('rate limited %s from %s: %s', email, auth.client_ip(), reason)
            return jsonify({'error': reason}), 429
        token = db.create_magic_token(con, email, auth.client_ip(), flow=db._sha(flow))
    finally:
        con.close()
    ...
    log.info('magic link requested for %s from %s', email, auth.client_ip())
    resp = jsonify({'ok': True})
    resp.status_code = 202
    return _flow_cookie(resp, 'bb_magic', flow, db.MAGIC_TTL_MIN * 60)
```

`click_magic_link` and the new confirm route:

```python
def _finish_magic_sign_in(con, token):
    """Burn the token and sign in; None when the token is dead."""
    email = db.consume_magic_token(con, token) if token else None
    if email is None:
        return None
    return auth.sign_in_by_email(con, email)


@bp.get('/magic')
def click_magic_link():
    token = request.args.get('t', '')
    con = db.connect()
    try:
        peek = db.peek_magic_token(con, token) if token else None
        if peek is None:
            return _clear_flow_cookie(_landing(auth='expired'), 'bb_magic')
        cookie = request.cookies.get('bb_magic')
        bound = bool(cookie) and peek['flow'] is not None and db._sha(cookie) == peek['flow']
        if not bound:
            # opened somewhere else than where it was requested (another device —
            # or an attacker's link in a victim's browser, review M2): ask first
            current = auth.current_user(con)
            log.info('magic link for %s opened unbound from %s (signed in: %s)',
                     peek['email'], auth.client_ip(), current['handle'] if current else '-')
            html = auth.confirm_signin_html(peek['email'], token, current['handle'] if current else None)
            return html, 200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'}
        user = _finish_magic_sign_in(con, token)
        if user is None:
            return _clear_flow_cookie(_landing(auth='expired'), 'bb_magic')
    finally:
        con.close()
    log.info('magic link sign-in: %s (@%s)', user['email'], user['handle'])
    return _clear_flow_cookie(_landing(), 'bb_magic')


@bp.post('/magic/confirm')
def confirm_magic_link():
    """The confirm page's form. Form-encoded on purpose (no JS on that page);
    the same-origin gate in app.py is what makes this safe to accept."""
    token = request.form.get('t', '')
    con = db.connect()
    try:
        user = _finish_magic_sign_in(con, token)
    finally:
        con.close()
    if user is None:
        return _landing(auth='expired')
    log.info('magic link sign-in (confirmed): %s (@%s)', user['email'], user['handle'])
    return _clear_flow_cookie(_landing(), 'bb_magic')
```

- [ ] **Step 6: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass. `tests/conftest.py::signed_in` requests and clicks on the same client, so it stays silent. Any test that requests on one client and clicks on another (grep `magic_link_from` across tests) now sees the confirm page — update it to post the confirm, or request and click on the same client.

- [ ] **Step 7: Commit**

```bash
git add bb/db.py bb/auth.py bb/routes/auth.py tests/
git commit -m "sec-b: M2b magic-link flow cookie + confirm page for unbound clicks"
```

---

### Task 8: L3 — purge Cloudflare's cache when public files go away

**Files:**
- Create: `bb/cdn.py`
- Modify: `bb/account.py:46-73` (`remove_public_surface`)
- Modify: `bb/publish.py:641-712` (`build_user`)
- Modify: `docs/ENV.md`
- Test: `tests/test_cdn.py` (create), `tests/test_sec_review_b.py`

**Interfaces:**
- Produces: `cdn.enabled() -> bool` (both `CF_API_TOKEN` and `CF_ZONE_ID` set via `bb.config.get`); `cdn.public_url(handle, rel) -> str` = `auth.base_url() + paths.URL_PREFIX + '/@' + handle + '/' + rel`; `cdn.purge_urls(urls) -> int` (number of URLs sent; 0 when disabled or empty; batches of 30 — Cloudflare's per-call limit; `requests.post` with a 10 s timeout; any failure logged at WARNING, never raised); `cdn.bundle_files(dir) -> list[str]` (relative paths of every regular file under a bundle dir, `/`-joined). Prefix purge is Enterprise-only, so purge-by-URL is what the free plan gets.
- Callers: `account.remove_public_surface` lists the files BEFORE `rmtree` and purges them after; `publish.build_user` lists `dest` before the swap, `tmp` after the build, and purges `old - new` once the swap is done (a photo that went private, a knife that went draft, a renamed hero).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cdn.py`:

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import os

from bb import cdn


class _Resp:
    def __init__(self, status=200, ok=True):
        self.status_code = status
        self._ok = ok

    def json(self):
        return {'success': self._ok, 'errors': [] if self._ok else [{'message': 'nope'}]}


def _capture(monkeypatch, status=200, ok=True, raise_exc=None):
    calls = []

    def post(url, json=None, headers=None, timeout=None):
        if raise_exc:
            raise raise_exc
        calls.append({'url': url, 'json': json, 'headers': headers, 'timeout': timeout})
        return _Resp(status, ok)
    monkeypatch.setattr(cdn.requests, 'post', post)
    return calls


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv('CF_API_TOKEN', raising=False)
    monkeypatch.delenv('CF_ZONE_ID', raising=False)
    calls = _capture(monkeypatch)
    assert cdn.enabled() is False
    assert cdn.purge_urls(['https://blade-book.com/blade-book/@sam/index.html']) == 0
    assert calls == []


def test_purges_in_batches_of_30_with_bearer(monkeypatch):
    monkeypatch.setenv('CF_API_TOKEN', 'cf-test-token')
    monkeypatch.setenv('CF_ZONE_ID', 'zone123')
    calls = _capture(monkeypatch)
    urls = [f'https://blade-book.com/blade-book/@sam/img/K{i:02d}.jpg' for i in range(65)]
    assert cdn.purge_urls(urls) == 65
    assert [len(c['json']['files']) for c in calls] == [30, 30, 5]
    assert calls[0]['url'] == 'https://api.cloudflare.com/client/v4/zones/zone123/purge_cache'
    assert calls[0]['headers']['Authorization'] == 'Bearer cf-test-token'
    assert calls[0]['timeout'] == 10


def test_failures_are_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setenv('CF_API_TOKEN', 't')
    monkeypatch.setenv('CF_ZONE_ID', 'z')
    _capture(monkeypatch, raise_exc=ConnectionError('down'))
    assert cdn.purge_urls(['https://blade-book.com/x']) == 0
    assert 'purge failed' in caplog.text
    _capture(monkeypatch, status=403, ok=False)
    assert cdn.purge_urls(['https://blade-book.com/x']) == 0
    assert '403' in caplog.text


def test_public_url_and_bundle_files(tmp_path, monkeypatch):
    monkeypatch.setenv('BASE_URL', 'https://blade-book.com')
    from bb import config
    config.load()
    assert cdn.public_url('sam', 'img/K01.jpg') == 'https://blade-book.com/blade-book/@sam/img/K01.jpg'
    os.makedirs(tmp_path / 'img')
    (tmp_path / 'index.html').write_text('x')
    (tmp_path / 'img' / 'K01.jpg').write_bytes(b'x')
    (tmp_path / 'K01').mkdir()
    (tmp_path / 'K01' / 'index.html').write_text('x')
    assert sorted(cdn.bundle_files(str(tmp_path))) == ['K01/index.html', 'img/K01.jpg', 'index.html']
    assert cdn.bundle_files(str(tmp_path / 'missing')) == []
```

Append to `tests/test_sec_review_b.py`:

```python
from bb import cdn


# --- L3: removed public files are purged from the edge -----------------------------

def test_remove_public_surface_purges_every_file(env, monkeypatch):
    dest = publish.bundle_dir('sam')
    os.makedirs(os.path.join(dest, 'img'))
    open(os.path.join(dest, 'index.html'), 'w').close()
    open(os.path.join(dest, 'img', 'K01.jpg'), 'w').close()
    purged = []
    monkeypatch.setattr(cdn, 'purge_urls', lambda urls: purged.extend(urls) or len(urls))
    assert account.remove_public_surface('sam') is True
    assert sorted(purged) == [cdn.public_url('sam', 'img/K01.jpg'), cdn.public_url('sam', 'index.html')]


def test_rebuild_purges_files_that_vanished(env, con, monkeypatch):
    from bb import store as store_mod
    from tests.test_account import _knife_with_photo
    u = _user(con)
    store = store_mod.from_paths()
    k = _knife_with_photo(con, store, u, tag_photo=_jpeg_with_comment(b'', (40, 30)))
    publish.build_user(con, u, store)
    hero = cdn.public_url('sam', f"img/{k['tag']}.jpg")
    purged = []
    monkeypatch.setattr(cdn, 'purge_urls', lambda urls: purged.extend(urls) or len(urls))
    db.set_public(con, u['id'], [k['id']], False)                          # bb/db.py:639
    publish.build_user(con, db.get_user(con, u['id']), store)
    assert hero in purged and cdn.public_url('sam', f"{k['tag']}/index.html") in purged
    assert cdn.public_url('sam', 'index.html') not in purged                # still there, rewritten
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_cdn.py tests/test_sec_review_b.py -q -k "purge or cdn or bundle_files"`
Expected: FAIL — `ModuleNotFoundError: bb.cdn`.

- [ ] **Step 3: Create `bb/cdn.py`**

```python
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
```

Confirm `bb.config.get` reads env after `config.load()` (it does — `.env` is loaded into `os.environ`; check `bb/config.py` and, if `get` only reads a cached dict, use `os.environ.get` fallback the way `publish.DEBOUNCE_S` does).

- [ ] **Step 4: Wire `bb/account.py`**

`remove_public_surface`: import `cdn` (`from bb import auth, cdn, db, publish, search`), list files before the rmtree and purge after the lock is released:

```python
    dest = publish.bundle_dir(handle)
    lock_path = publish._lock_path(handle)
    lockf = open(lock_path, 'w')
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        gone = cdn.bundle_files(dest)              # what the edge may still hold (review L3)
        shutil.rmtree(dest, ignore_errors=True)
        shutil.rmtree(dest + '.tmp', ignore_errors=True)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()
    cdn.purge_urls(cdn.public_url(handle, rel) for rel in gone)
```

- [ ] **Step 5: Wire `bb/publish.py`**

Import `cdn` inside `build_user` next to `search` (same cycle reason). Before `shutil.rmtree(tmp, ignore_errors=True)` at the top of the build: `before = set(cdn.bundle_files(dest))`. After `os.replace(tmp, dest)`:

```python
            after = set(cdn.bundle_files(dest))
            cdn.purge_urls(cdn.public_url(handle, rel) for rel in sorted(before - after))
```

In the `profile_private` branch, purge everything that was there:

```python
        if user.get('profile_private'):
            gone = cdn.bundle_files(dest)
            search.deindex_user(con, user['id'])
            shutil.rmtree(dest, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)
            cdn.purge_urls(cdn.public_url(handle, rel) for rel in gone)
            return -1
```

- [ ] **Step 6: Document the env keys**

In `docs/ENV.md` table, after `BLADEBOOK_ADMIN_EMAIL`:

```
| `CF_API_TOKEN`, `CF_ZONE_ID` | sec-B | Optional, both or neither. Cloudflare API token with **Zone → Cache Purge** on the blade-book.com zone only (dashboard → My Profile → API Tokens → Create → custom), and the zone id (dashboard → blade-book.com → Overview, right column). With both set, removed public files (deleted register, private photo, knife back to draft) are purged from the edge at once instead of after the 4 h TTL (security review L3). **Unset → no purge, logged nothing; the edge expires them on its own.** |
```

- [ ] **Step 7: Run the suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add bb/cdn.py bb/account.py bb/publish.py docs/ENV.md tests/
git commit -m "sec-b: L3 purge vanished public files from the Cloudflare edge"
```

---

### Task 9: Review doc status + handoff notes

**Files:**
- Modify: `docs/SECURITY-REVIEW-2026-09-04.md:68-72` (Status)

- [ ] **Step 1: Append the status line**

```
- 2026-09-05: **batch B merged** (main `<sha>`, live after restart): M2 (OIDC flow cookie; magic links bound to the requesting browser, confirm page otherwise), M7 (publish opens through `photos._open`; undecodable uploads 415), M8 (`released_handles`, 90 days), M9 (COM strip), L1 (`sessions` rows — plain sign-out revokes the cookie; every live session was signed out once by the deploy), L3 (`bb/cdn.py` purge-by-URL, needs `CF_API_TOKEN` + `CF_ZONE_ID`), L10 (`no-store` on API JSON). Schema v11. Regression tests: `tests/test_sec_review_b.py`, `tests/test_cdn.py`.
```

Leave `<sha>` literal; the lead fills it at merge.

- [ ] **Step 2: Commit**

```bash
git add docs/SECURITY-REVIEW-2026-09-04.md
git commit -m "sec-b: review status for batch B"
```

---

## Lead's merge checklist (not a subagent task)

1. Independent diff review of `main..sec-batch-b` by a fresh reviewer agent (read-only, told the worktree is shared) — batch A's review caught two real gaps.
2. `python3 -m pytest -q` green on the branch; count noted.
3. Merge to `main` and restart in the same minute (`scripts/restart.sh`, Simon's sudo): the v11 stamp flip-flop is harmless here (SCHEMA creates the tables on every connect; the ALTER is duplicate-tolerant) but the session rows mean every cookie minted by an old worker after the migration is dead — one restart, not a long overlap.
4. Tell Simon: everyone is signed out once; the magic link now shows a confirm page when opened on a different device; `CF_API_TOKEN` + `CF_ZONE_ID` wanted in `/etc/blade-book/.env` for L3 (token scope: Zone.Cache Purge, this zone only).
5. Sweep AFTER the restart: `python3 scripts/publish_sweep.py --all` (COM strip and the guarded open apply to every existing hero).
