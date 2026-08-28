# /etc/blade-book/.env — every key the code reads

Secrets only. Paths never live here (bb/paths.py). `install.sh` creates the
file with `SESSION_KEY`; add the rest by hand (`sudo nano /etc/blade-book/.env`)
and restart with `bash scripts/restart.sh`.

Paths and the port are NOT here — `BLADEBOOK_*_DIR` / `BLADEBOOK_PORT` are read by `bb/paths.py` and set by the supervisor program, not `.env`.

| key | plan | value |
|---|---|---|
| `SESSION_KEY` | 01 | 64 hex chars from `openssl rand -hex 32`. Rotating it signs everyone out. |
| `BASE_URL` | 02 | Public origin, no trailing slash. `https://billboard.instockornot.club` until DNS lands, then `https://blade-book.com`. Magic links and OIDC redirect URIs are built from it, so the OIDC consoles must list `<BASE_URL>/blade-book/api/auth/google/callback` and `/apple/callback`. |
| `RESEND_API_KEY` | 02 | From resend.com. **Unset → LogMailer**: the magic link is written to `/var/log/blade-book/app.log` instead of being emailed (how sign-in works before Resend is wired). |
| `MAIL_FROM` | 02 | Default `blade-book <noreply@blade-book.com>`; the domain must be verified in Resend. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | 02 | Google Cloud console → OAuth client (Web). Both unset → the Google button does not render. |
| `APPLE_CLIENT_ID` | 02 | The **Services ID** (e.g. `com.blade-book.web`), not the App ID. |
| `APPLE_TEAM_ID`, `APPLE_KEY_ID` | 02 | From the Apple developer portal; the key must have Sign in with Apple enabled. |
| `APPLE_PRIVATE_KEY` | 02 | The `.p8` contents on **one line** with `\n` for newlines. Any of the four Apple keys unset → the Apple button does not render. |
| `ANTHROPIC_API_KEY` | 04 | console.anthropic.com. **Unset → decode disabled**: `POST …/decode` answers 503 and the app still boots. |
| `DECODER_MODEL` | 04 | Exact model id, e.g. `claude-sonnet-5` (default), `claude-haiku-4-5`, `claude-opus-5`. Choose from `scripts/eval_decode.py`'s table, not taste. |
