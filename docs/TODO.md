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
