# /etc/blade-book/.env — every key the code reads

Secrets only. Paths never live here (bb/paths.py). `install.sh` creates the
file with `SESSION_KEY`; add the rest by hand (`sudo nano /etc/blade-book/.env`)
and restart with `bash scripts/restart.sh`.

Paths and the port are NOT here — `BLADEBOOK_*_DIR` / `BLADEBOOK_PORT` are read by `bb/paths.py` and set by the supervisor program, not `.env`.

| key | plan | value |
|---|---|---|
| `SESSION_KEY` | 01 | 64 hex chars from `openssl rand -hex 32`. Rotating it signs everyone out. |
| `BASE_URL` | 02 | Public origin, no trailing slash. `https://billboard.instockornot.club` until DNS lands, then `https://blade-book.com`. Magic links and OIDC redirect URIs are built from it, so the OIDC consoles must list `<BASE_URL>/blade-book/api/auth/google/callback` and `/apple/callback`. Also feeds the public bundle: `settings.public_url` and every published page's OG tags are built from it (plan 06). |
| `RESEND_API_KEY` | 02 | From resend.com. **Unset → LogMailer**: the magic link is written to `/var/log/blade-book/app.log` instead of being emailed (how sign-in works before Resend is wired). |
| `MAIL_FROM` | 02 | Default `blade-book <noreply@blade-book.com>`; the domain must be verified in Resend. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 02 | Google Cloud console → OAuth client (Web). Both unset → the Google button does not render. |
| `APPLE_CLIENT_ID` | 02 | The **Services ID** (e.g. `com.blade-book.web`), not the App ID. |
| `APPLE_TEAM_ID`, `APPLE_KEY_ID` | 02 | From the Apple developer portal; the key must have Sign in with Apple enabled. |
| `APPLE_PRIVATE_KEY` | 02 | The `.p8` contents on **one line** with `\n` for newlines. Any of the four Apple keys unset → the Apple button does not render. |
| `ANTHROPIC_API_KEY` | 04 | console.anthropic.com. **Unset → decode disabled**: `POST …/decode` answers 503 and the app still boots. |
| `DECODER_MODEL` | 04 | Exact model id, e.g. `claude-sonnet-5` (default), `claude-haiku-4-5`, `claude-opus-5`. Choose from `scripts/eval_decode.py`'s table, not taste. |
| `BLADEBOOK_PUBLISH_DEBOUNCE_S` | 06 | Optional, default `30` — seconds the public-bundle rebuild waits after the last save before it fires (`bb/publish.py`'s per-process timer, plus the cron sweep's quiet window). Tests set this to a tiny value; production leaves it at the default. |
| `BLADEBOOK_PRICE_TEXT` | 10 | Optional, default `$4/mo or $36/yr` — the one price line (spec §10), shown by `GET /api/billing` on the intake and search early-access cards. Change it here, restart, done. |
| `BLADEBOOK_CONTACT_EMAIL` | 10 | Optional, default `hello@` at MAIL_FROM's domain — the early-access "email us" address the intake dialog and the search card link to. Must be a mailbox someone reads. |
| `BLADEBOOK_ADMIN_EMAIL` | 12 | Where `scripts/monitor.py` (cron `*/5`) sends its mail: ERROR lines in app.log (1/h), decode failure rate (1/day), backup age (1/day), new sign-ins (immediate), and outage/recovery notices. **Unset → the monitor logs and sends nothing.** SMS for outages reuses billboard's Twilio env (`/etc/billboard/.env`, `BB_ADMIN_PHONE`). First run: one mail lists every existing user (the sign-in watermark starts empty) and the ERROR mail carries the newest 40 lines of the existing app.log — expected once. |
| `CF_API_TOKEN`, `CF_ZONE_ID` | sec-B | Optional, both or neither. Cloudflare API token with **Zone → Cache Purge** on the blade-book.com zone only (dashboard → My Profile → API Tokens → Create → custom), and the zone id (dashboard → blade-book.com → Overview, right column). With both set, removed public files (deleted register, private photo, knife back to draft) are purged from the edge at once instead of after the 4 h TTL (security review L3). **Unset → no purge, logged nothing; the edge expires them on its own.** |

Plan 11 (settings: handle once, export, delete) adds **no keys**. Exports are built under
`/var/lib/blade-book/exports/` and unlinked after download; the dir is created on first use.
