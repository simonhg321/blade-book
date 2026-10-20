# Passkeys — design

**Date:** 2026-10-19 · **Owner:** Simon · **Written by:** Sky Claude (stark)
**Status:** draft for Simon's review

## Why

Simon could not sign in from the office: the magic link lands in a personal
inbox that the work machine cannot reach. The same day a password door shipped
(33cbd15: set it under settings, sign in by handle). A passkey is the better
door: Face ID or Touch ID on his Mac and iPhone through iCloud Keychain, and on
a borrowed or work machine the browser shows a QR code he scans with the phone.
Nothing to remember, nothing to type, nothing phishable.

Questions Simon had the brainstorm (2026-10-19):

- **One button, no handle.** "Sign in with a passkey" on the landing page; the
  browser or phone picks the account (discoverable credentials).
- **Several passkeys per account, named, listed in settings**, add and remove
  only while signed in.
- Entropy question asked and answered: challenges come from `secrets`, which is
  `os.urandom` → the kernel CSPRNG (`getrandom(2)`). Same source as the box's
  SSH keys, session secrets and magic tokens. Accepted.
- Library: **py_webauthn** (`webauthn>=3.0`, PyPI). Hand-rolling CBOR/COSE
  verification was rejected; switching on Sign in with Apple was rejected
  (needs the paid developer account, not a passkey on a work PC).

## Scope

In:

1. `bb/passkeys.py`: a thin wrapper over py_webauthn: options for register and
   sign-in, verification, our RP constants.
2. Table `passkeys`, schema **v16**.
3. Four API routes: register begin/finish (settings, signed in), sign-in
   begin/finish (auth, signed out). Plus list and remove.
4. Settings page: PASSKEYS card. Landing page: the button. How page: a line.
5. Tests with a software authenticator (real signatures, no mocks of the
   library), headless screenshots with Chromium's virtual authenticator.
6. `scripts/deploy_passkeys.sh`.

Out (not asked, not now):

- Passkeys as a second factor on top of the magic link. A passkey is a full
  sign-in on its own, like the link is.
- Renaming a passkey after it is added (remove and re-add instead).
- Conditional UI / autofill of passkeys in the email field.
- Any change to the password door, OIDC, or the magic link.

## 1. Relying party and constants — `bb/passkeys.py`

```
RP_ID   = 'blade-book.com'               # from BASE_URL's host, test boxes override
RP_NAME = 'blade-book'
ORIGIN  = auth.base_url()                 # 'https://blade-book.com'
CHALLENGE_BYTES = 32                      # secrets.token_bytes; spec minimum is 16
FLOW_TTL_S = 300                          # the challenge cookie's life
MAX_PASSKEYS = 10                         # per account
```

- `rp_id()` derives from `BASE_URL` (`urlparse(...).hostname`), so tests on
  `http://localhost` and a future host change need no code edit. The alias host
  `billboard.instockornot.club` serves the same app but has a different RP id:
  passkeys registered on blade-book.com do not work there, by design. The
  landing page on that host hides the button (the begin route answers 404 when
  `request.host` is not the RP id).
- **User verification required** on both register and sign-in: Face ID, Touch
  ID, device PIN or phone unlock every time. A passkey stands in for a
  password, so no silent taps.
- **Discoverable (resident) credential required** at registration, so sign-in
  needs no handle and no allow list.
- **Attestation none.** We do not care which make of authenticator it is.
- **User handle** = the blade-book integer user id as 8 ASCII bytes (`str(id)`).
  The spec recommends 64 random bytes for cross-site privacy; blade-book
  handles are public already and the id leaks nothing new. The authenticator
  returns it on sign-in and we use the credential id, not the handle, to find
  the row; the handle is only cross-checked.
- **Challenge** is minted by us (`secrets.token_bytes(32)`) and passed to the
  library explicitly, never left to a default, so the source is visible in one
  place and a test pins it.
- The library is imported **lazily inside the functions**. If the import fails,
  `passkeys.available()` is False: both begin routes answer **503**
  `{"error": "passkeys are not set up here"}`, the list route answers an empty
  list with `"available": false`, and the pages hide the buttons. Nothing else
  in the app is affected.

Functions:

```
available() -> bool
registration_options(user, existing_credential_ids) -> (options_json: str, challenge: bytes)
verify_registration(credential_json, challenge) -> Verified(credential_id, public_key, sign_count, transports, name)
authentication_options() -> (options_json, challenge)
verify_authentication(credential_json, challenge, stored) -> new_sign_count
```

`stored` is the `passkeys` row. Every verify raises `PasskeyError(reason)` on
any failure; the reason is logged, never shown on the sign-in page (one generic
401 there) and shown verbatim on the settings page (you are signed in, it is
your own device).

`name` on registration: the browser's reported authenticator name if the
response carries one (most do not); else the client's `User-Agent` family
("Safari on Mac", "Chrome on Windows", "iPhone") via a 10-line table; else
"passkey". The settings page shows it with the date: "Safari on Mac · added
Oct 19".

## 2. Schema v16 — table `passkeys`

```sql
CREATE TABLE IF NOT EXISTS passkeys (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  credential_id BLOB NOT NULL UNIQUE,
  public_key BLOB NOT NULL,
  sign_count INTEGER NOT NULL DEFAULT 0,
  transports TEXT,                 -- JSON list as the browser reported, may be NULL
  name TEXT NOT NULL,
  created TEXT NOT NULL,
  last_used TEXT
);
CREATE INDEX IF NOT EXISTS passkeys_user ON passkeys(user_id);
```

- `SCHEMA_VERSION = 16`; the migration entry is just the DDL (CREATE IF NOT
  EXISTS is enough; old workers tolerate it).
- `ON DELETE CASCADE` + `PRAGMA foreign_keys=ON` in `db.connect` means account
  deletion removes passkeys with no change to `account.delete_account`. A test
  proves it.
- db helpers: `add_passkey`, `passkeys_for_user` (never returns `public_key` to
  callers that build JSON), `passkey_by_credential_id`, `touch_passkey(id,
  sign_count)`, `remove_passkey(user_id, id)`, `count_passkeys(user_id)`.

## 3. Routes

All JSON. The challenge travels in a flow cookie **`bb_passkey`** set and
cleared with the existing `_flow_cookie` / `_clear_flow_cookie` helpers in
`bb/routes/auth.py` (HttpOnly, `__Host-` under https, 300 s). Its value is the
base64url challenge; the cookie is browser-bound so a challenge minted in one
browser cannot be finished from another. Finish routes read the cookie, clear it
in the response whatever the outcome, and refuse if it is missing: **a challenge
is single-use as far as the browser is concerned.** The server keeps no list of
spent challenges (review ruling 2026-10-20): replaying a cookie-plus-assertion
pair inside its 300 s needs a copy of an HttpOnly, Secure cookie and the signed
response, i.e. a foothold inside the TLS session, and whoever has that already
holds the session cookie the login issues. A spent-challenge table is defense in
depth for later, not a gate for this build.

The settings routes live in `bb/routes/settings.py` under its blueprint; the
sign-in routes in `bb/routes/auth.py`. Sharing the cookie helpers means moving
them to `bb/auth.py` (module-level, no behaviour change) so both blueprints can
import them. One wrinkle: on plain http (tests) the helpers scope the cookie to
`/api/auth`, which the settings routes would never see. The moved helpers take
the path as a parameter; `bb_magic` and `bb_oidc` keep `/api/auth`,
`bb_passkey` uses `/api` (both blueprints). Under https the `__Host-` rule
forces `/` for all of them, as today.

### Registration (signed in; `@auth.login_required`)

- `POST /api/settings/passkeys/begin` → 200 `{options}` (the
  PublicKeyCredentialCreationOptions JSON from the library, `excludeCredentials`
  = the user's existing credential ids so a device cannot register twice) +
  the cookie. 503 when unavailable. 400 `"ten passkeys is the limit — remove
  one first"` at the cap.
- `POST /api/settings/passkeys/finish` body `{credential, name?}` (the
  `PublicKeyCredential` JSON the browser produced; optional name ≤ 40 chars
  overrides the derived one) → 200 `{passkeys: [...]}`. 400 with the library's
  reason on any verification failure; 400 `"that passkey is already registered"`
  on a duplicate credential id (UNIQUE). Logs `passkey added for @handle (name)`.
- `GET /api/settings/passkeys` → `{available: bool, passkeys: [{id, name,
  created, last_used, transports}]}`. Never `credential_id` or `public_key`.
- `DELETE /api/settings/passkeys/<int:id>` → 200 with the list; 404 if not
  yours. Removing the last one is always allowed: the magic link remains.

### Sign-in (signed out)

- `POST /api/auth/passkey/begin` → 200 `{options}` (request options, no
  `allowCredentials`, UV required) + the cookie. Counts against the existing
  per-IP rate limit via `auth.check_rate_limits` (so the pair costs two of the
  hourly budget, same as a magic send plus confirm). 503 when unavailable;
  404 on a host that is not the RP id.
- `POST /api/auth/passkey/finish` body `{credential}` →
  1. cookie missing/expired → 401.
  2. `credential.id` → `db.passkey_by_credential_id`; unknown → 401.
  3. `verify_authentication` (origin, RP id, challenge, UV flag, signature,
     sign count) → any `PasskeyError` → 401.
  4. **Sign count:** new > stored, or both 0 (iCloud Keychain and most synced
     passkeys always report 0) → accept; otherwise refuse 401 and log a
     warning `passkey sign count went backwards for @handle (stored X, got
     Y): possible clone`. The row is left alone; the owner removes it from
     settings if they agree.
  5. `userHandle` in the response, if present, must equal the row's user id;
     mismatch → 401.
  6. `touch_passkey`, `auth.login(con, user)` (same helper the magic link and
     password doors use: session secret, `sessions` row, cookie), return
     `auth.self_view(user)`.
  Every 401 carries the same body `{"error": "that passkey did not work"}`;
  the real reason goes to the log at WARNING with the client IP.

## 4. Pages

### Settings — PASSKEYS card, under PASSWORD

- Heading PASSKEYS. Copy: "Face ID, Touch ID or your phone. Works on a borrowed
  machine too: it shows a QR code, you scan it with your iPhone."
- Button `add a passkey` (class `btn alt`). Flow: begin → `navigator.credentials.create`
  (options parsed with `PublicKeyCredential.parseCreationOptionsFromJSON` when
  present, else a small base64url shim) → finish → re-render the list. Status
  line uses the page's existing `say()`.
- List: one row per passkey: name, "added Oct 19", "last used Oct 20" or
  "never", a `remove` link (confirm dialog). Empty state: "none yet".
- `window.PublicKeyCredential` missing → the button is replaced by the note
  "this browser cannot make passkeys". `available: false` from the API → the
  card says "passkeys are not set up here" (deploy without the library).

### Landing — the button

- `Sign in with a passkey` (class `alt`, full width) rendered into the existing
  `#providers` grid, above any OIDC buttons, only when
  `window.PublicKeyCredential` exists and the host is the RP id. The email form
  and the handle+password toggle are untouched.
- Flow: begin → `navigator.credentials.get` → finish → `location.href =
  '/blade-book/me/'` (plus the existing `bbNext` return path if one is stored).
- Errors on one line under the button via the existing `#msg`: the browser's
  `NotAllowedError` → "cancelled, or no passkey on this device"; 401 →
  "that passkey did not work — try the email link"; 503 → button hidden.
- "No password needed" line unchanged.

### How page

One sentence after the sign-in step: "Or a passkey: Face ID, Touch ID or your
phone — add one under settings."

All scripts stay inline in the three pages; the site CSP already allows
`'unsafe-inline'` for `script-src 'self'`. No CDN, no new files.

## 5. Testing

`tests/test_passkeys.py` (library wrapper) and `tests/test_passkeys_api.py`
(routes), plus a `tests/softauth.py` helper:

- **Software authenticator** built on `cryptography` (already installed as a
  PyJWT[crypto] dependency): mints a P-256 key, builds a real `authenticatorData`
  (RP id hash, flags with UP+UV, sign count, AAGUID, credential id, COSE key),
  a `none` attestation object (CBOR via `cbor2`, which py_webauthn pulls in),
  the `clientDataJSON`, and signs assertions. It returns the same JSON shape a
  browser's `PublicKeyCredential.toJSON()` returns. No mocking of the library.
- Wrapper tests: options carry our 32-byte challenge; UV and resident key
  required; RP id from BASE_URL; verify accepts the soft authenticator; wrong
  origin, wrong RP id, wrong challenge, UV flag off each raise `PasskeyError`.
- Route tests: register begin/finish happy path; finish without the cookie →
  400; replaying a finished challenge → 400; cap of 10; duplicate credential
  → 400; list hides keys; remove own / not own (404); account delete cascades;
  sign-in happy path lands a session (`/auth/me` 200); unknown credential,
  wrong challenge, replay, UV off, sign count rollback, userHandle mismatch →
  identical 401 bodies; 0→0 sign count accepted; rate limit → 429;
  `available()` False (monkeypatch) → 503 and `{available: false}`;
  alias host → 404 on begin.
- Deploy-file tests (existing `tests/test_deploy_files.py` style): the three
  pages contain the new ids; the deploy script has the STOP checks.
- **Headless:** Playwright with `CDPSession` `WebAuthn.enable` +
  `WebAuthn.addVirtualAuthenticator` (internal, resident keys, UV) registers a
  passkey from the settings page, signs out, signs in with the landing button,
  lands on /me. Screenshots of both pages at 1440×900 and 390×844 before Simon
  sees them. Scratch app on :5999 with `BLADEBOOK_*_DIR` under the session
  scratchpad, as for the date board.

Target: all current tests (907) plus the new ones pass; nothing in the suite
touches the network or the live DB.

## 6. Deploy

`scripts/deploy_passkeys.sh`, same shape as `deploy_password.sh`:

1. `pip install -r requirements.txt` as root (the app runs on system Python
   under gunicorn; `webauthn>=3.0` pulls `cbor2`, `pyOpenSSL` and a newer
   `cryptography` wheel — the script prints the resulting `cryptography`
   version and STOPs if `python3 -c 'import webauthn, jwt'` fails, before
   touching the live app).
2. Copy `html/` to `/var/www/html/blade-book/`.
3. `supervisorctl restart blade_book`, wait for `/healthz`.
4. Probe: `POST /api/auth/passkey/begin` must answer **200** with a challenge
   (proves the library loaded and v16 migrated); `POST
   /api/settings/passkeys/begin` signed out must answer **401**.
5. Fetch the live settings page and grep the PASSKEYS card id (Cloudflare may
   hold the old page up to 4 h; informational).

Rollback: `git checkout <previous>` + restart. The `passkeys` table stays; an
older app ignores it.

Simon's one-liner: `! sudo bash /home/shg/blade-book/scripts/deploy_passkeys.sh`.

## Open questions

None blocking. Two defaults Simon can flip later without a schema change:

- UV **required** → "preferred" if a security key without PIN ever matters.
- Cap of 10 per account.
