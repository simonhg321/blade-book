# TODO — blade-book

Small items that don't warrant a plan. Bigger work lives in the plan roadmap
(`billboard/docs/superpowers/plans/2026-08-27-blade-book-01-foundation.md`, end of file).

- [ ] **Magic-link burst limit** (Simon, 2026-08-28). Today: 5 links/hour/email + 30/hour/IP
  (`bb/auth.py` `EMAIL_LINKS_PER_HOUR`, `IP_ATTEMPTS_PER_HOUR`). Add a short-window backoff on
  top — e.g. max 3 per email per 10 minutes, and/or a growing cooldown per email (30 s → 60 s
  → 5 min) — with the 429 body saying how long to wait so the landing page can show it. The
  landing page already holds the resend button for 60 s after a send (client-side only).
- [ ] Decode prompt: `variant` and `special_edition` came back identical on the PDW Inkosi
  (2026-08-28 live K01) — tell the model `special_edition` is the run/collab name and
  `variant` the configuration, or drop one.
- [ ] Vhost `Permissions-Policy: camera=()` → `camera=(self)` if iPhone capture is blocked
  on `/blade-book/me/add/` (sudo; unverified).
- [ ] /me: photo re-order / replace from the register (today: only via /me/add before save, or re-upload by slot through the API).
- [ ] board: contact / report buttons on the static `/@handle/K07` permalink pages (they're
  static bundles; today both live on `/blade-book/board/` only — plan 09 ruling).
- [ ] board: `listed_at` is a UTC ISO stamp shown as a date; localise if anyone asks.
- [ ] admin: report resolutions are only visible via sqlite3
  (`SELECT * FROM reports WHERE resolved_at IS NOT NULL`).
- [ ] rulings to revisit with real traffic: 10 board intros/day/buyer; 10 open reports/reporter;
  auto-hide counts only reporters who own a live knife.
- [ ] moderation loop (final review, plan 09): (a) auto-hide has no terminal state after an admin
  restore — the same 3 accounts can re-report and re-hide; skip the auto-hide branch when a
  resolved report on the knife has resolution LIKE 'restored:%'. (b) sockpuppet cost to hide is
  3 accounts × any live knife; consider requiring board-eligibility (verified + 7-day live knife)
  to COUNT toward auto-hide, symmetric with listing.
- [ ] db.connect() downgrades the schema stamp when an OLDER worker opens a NEWER DB
  (`row[0] != SCHEMA_VERSION` → migrate-nothing → UPDATE to the old number). Harmless today
  (next cron re-runs the idempotent migration) but: always merge → restart in the same minute,
  and change the check to `<` so old workers read-as-is.
- [ ] billing (plan 10 deferrals): (a) "your account turns one next month" mail (spec §10 Mail) — first
  account turns one 2027-08-29; a daily cron over users where created ∈ [335, 336) days ago, one send
  ever (needs a `turns_one_sent_at` column). (c) free_old_used TOCTOU: two concurrent saves of the 3rd
  and 4th old knife can both pass `< 3` (over-by-one, same shape as the plan-09 caps; not worth a
  transaction at this scale); same shape again for two concurrent saves of the SAME draft on
  different workers — both read status=draft, both charge; hardening = publish_knife's UPDATE
  `WHERE … AND status='draft'` returning whether a transition happened, charge only then. (d) the
  early-access mailto defaults to `hello@` at MAIL_FROM's domain (`BLADEBOOK_CONTACT_EMAIL`
  overrides) — make that mailbox real or set the env before the first outside 402.
- [ ] `account_days` uses users.created; an account whose created stamp predates the gate (all three
  live users: 08-29 → 09-02) gets its full first year from that date — intended, no backfill.
- [ ] ops: off-box backup copy (spec §11 bucket) is NOT built — Simon 2026-09-03: the Linode backup service
  covers the box nightly. Revisit only if the box moves off Linode (RUNBOOK-move).
- [ ] ops: the monitor's SMS leg imports billboard's `sms_alerter` from /home/shg/billboard and reads
  /etc/billboard/.env — a cross-project dependency by design (same box, same phone). If billboard ever
  leaves stark, give blade-book its own `TWILIO_*` keys.
- [ ] ops: monitor thresholds are constants in scripts/monitor.py (disk 10 %, backup 26 h, decode 20 % over
  ≥5 calls, ERROR mail 1/h) — tune with real traffic.
- [ ] ops: the health probe (`scripts/monitor.py` HEALTHZ_URL) hits gunicorn on loopback, not
  Apache/TLS — a future ops pass should also curl the public URL to catch a broken proxy/cert.
- [ ] pages: /about and /terms fetch the contact address from `/api/billing` at page load, so an
  API outage loses it from both pages — consider baking it into the bundle at publish time instead.
- [ ] pages: the "Who" sentence on /about is a placeholder for Simon's own words (mascot line included).
- [ ] settings: **email change** (spec §3 lists it; deferred from plan 11, Simon 2026-09-03). Shape when it
  lands: a `change_email` magic token kind sent to the NEW address; on click, swap `users.email` (unique
  check), keep `auth_subjects`; the old address gets a plain notice. Until then: sign in with the new
  address = a new account.
- [ ] settings: a changed-away handle is neither reserved nor redirected (plan 11 ruling). If a squatter
  ever takes an old handle to impersonate, add the old slug to a `retired_handles` table for 90 days.
- [ ] settings: export rate limit is in-process (`bb/routes/settings._last_export`) — resets on restart,
  per-worker under gunicorn (2 workers = up to 2 ZIPs per 10 min). Fine at this scale; a `users.export_at`
  column if it ever isn't.
- [ ] settings: the public-page toggles (hide born day / private / key / hero pin) still live on `/me`,
  not `/me/settings` — stays on `/me` (plan 13 ruling: `/me/settings` is account-level, `/me` is page-level).
- [ ] settings: the export route sets `resp.direct_passthrough = False` so Werkzeug's `call_on_close`
  unlink actually fires (with the default the ZIP leaks under gunicorn); a crash-safe follow-up keeps
  `send_file` but opens → fstat → unlink → `send_file(fh)`, so the ZIP is gone from disk the instant the
  fd is open, restart or not.
- [ ] ops: Cloudflare Web Analytics injects `static.cloudflareinsights.com/beacon.min.js` into every page and our
  CSP blocks it (console error on every load) — and `/terms` promises "no analytics scripts". Simon: turn Web
  Analytics OFF for the zone in the Cloudflare dashboard (not allowlisted on purpose).
- [ ] nav: PWA manifest + service worker (roadmap item for plan 12, not built) — belongs with the next `/me/add`
  camera work.
- [ ] assets: Cloudflare caches `/blade-book/vibe.css` and `nav.js` at the edge for 4 h (its own TTL overrides
  our `max-age=300`; seen 2026-09-03 after plan 13 — stale CSS for the new footer). Fix: version the asset
  URLs (`vibe.css?v=N`, `nav.js?v=N`) in every page + `bb/publish.py` and bump N on change (tests pin the
  `href="/blade-book/vibe.css"` needle — update to a shared constant). Until then: purge the zone cache
  after any CSS/JS deploy (Simon, Cloudflare dashboard).
