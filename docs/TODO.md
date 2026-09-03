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
- [ ] account deletion (plan 11) MUST remove /var/www/html/blade-book/@handle, its .tmp, and its DATA_DIR publish lock — otherwise a deleted user's public page serves forever (final review, plan 06).
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
  ever (needs a `turns_one_sent_at` column). (b) `sign_in_by_email` must consult `deleted_users`
  (email hash) so a re-created account gets no fresh free_old_used allowance — lands with plan 11's
  delete, which is what writes that table. (c) free_old_used TOCTOU: two concurrent saves of the 3rd
  and 4th old knife can both pass `< 3` (over-by-one, same shape as the plan-09 caps; not worth a
  transaction at this scale); same shape again for two concurrent saves of the SAME draft on
  different workers — both read status=draft, both charge; hardening = publish_knife's UPDATE
  `WHERE … AND status='draft'` returning whether a transition happened, charge only then. (d) the
  early-access mailto defaults to `hello@` at MAIL_FROM's domain (`BLADEBOOK_CONTACT_EMAIL`
  overrides) — make that mailbox real or set the env before the first outside 402.
- [ ] billing surfaces still missing from spec §3: `/me/settings` should show the sub row (plan 11).
- [ ] `account_days` uses users.created; an account whose created stamp predates the gate (all three
  live users: 08-29 → 09-02) gets its full first year from that date — intended, no backfill.
- [ ] ops: off-box backup copy (spec §11 bucket) is NOT built — Simon 2026-09-03: the Linode backup service
  covers the box nightly. Revisit only if the box moves off Linode (RUNBOOK-move).
- [ ] ops: the monitor's SMS leg imports billboard's `sms_alerter` from /home/shg/billboard and reads
  /etc/billboard/.env — a cross-project dependency by design (same box, same phone). If billboard ever
  leaves stark, give blade-book its own `TWILIO_*` keys.
- [ ] ops: monitor thresholds are constants in scripts/monitor.py (disk 10 %, backup 26 h, decode 20 % over
  ≥5 calls, ERROR mail 1/h) — tune with real traffic.
- [ ] pages: the "Who" sentence on /about is a placeholder for Simon's own words (mascot line included).
