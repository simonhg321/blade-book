# RUNBOOK — pointing blade-book.com at stark

Registered 2026-09-03 (`.com` + `.net`). Until DNS lands the app lives at
`https://billboard.instockornot.club/blade-book/`; that alias keeps working
after the switch (links already in the wild never break).

## 0. Before touching DNS (once)

- Resend: the sending domain in `MAIL_FROM` (`noreply@blade-book.com` by
  default) must be **verified** in the Resend dashboard (SPF + DKIM records on
  blade-book.com) or magic links stop after the switch. Add those DNS records
  at the same time as the A record.
- Cloudflare: if the zone is proxied (orange cloud) note that Cloudflare
  caches `vibe.css`/`nav.js` at the edge for 4 h — asset URLs are versioned
  (`?v=YYYYMMDD`, `tests/test_deploy_files.py::ASSET_V`) so deploys bypass it;
  turn **Web Analytics off** for the zone (its beacon is CSP-blocked and
  `/terms` promises no analytics scripts).

## 1. DNS

| record | value |
|---|---|
| `blade-book.com` A | `<ORIGIN-IP>` |
| `www.blade-book.com` CNAME | `blade-book.com` |
| `blade-book.net`, `www.blade-book.net` | same A / CNAME (redirects to .com in step 4) |
| Resend SPF/DKIM | from the Resend domain page |

Check from the box: `dig @1.1.1.1 +short blade-book.com A`.

## 2. Vhost + certificate (sudo, Simon)

```
! sudo bash /home/shg/blade-book/scripts/enable_domain.sh you@example.com
```

- The script installs `deploy/apache-blade-book.com.conf` as
  `sites-available/blade-book.conf`, enables it, and runs certbot for
  `blade-book.com` + `www.blade-book.com` (HTTP-01; works through the
  Cloudflare proxy too). It refuses to run unless the name resolves to this
  box; behind the Cloudflare proxy add `--force` as the second argument.
- Certbot email: first argument, or `CERTBOT_EMAIL=` in `/etc/blade-book/.env`.
- Verify: `curl -s https://blade-book.com/api/healthz` and
  `https://blade-book.com/` renders the landing.

## 3. Switch the app's public origin (sudo for the env edit, then restart)

```
! sudo bash -c 'grep -q ^BASE_URL= /etc/blade-book/.env && sed -i "s#^BASE_URL=.*#BASE_URL=https://blade-book.com#" /etc/blade-book/.env || echo BASE_URL=https://blade-book.com >> /etc/blade-book/.env'
! bash /home/shg/blade-book/scripts/restart.sh
```

Then, as shg (no sudo): `cd /home/shg/blade-book && python3 scripts/publish_sweep.py --all`
— every bundle's OG tags, permalinks and `settings.public_url` are built from
`BASE_URL`, so they must be regenerated. Magic links and intro mails now point
at blade-book.com.

Smoke test: request a magic link from `https://blade-book.com/`, click it,
land signed in on blade-book.com; open `/@simon-collector/K75/` and check the
`og:url`; `/api/billing` answers at `/api/` and `/blade-book/api/` alike.

## 4. Later

- `.net`: add `ServerAlias blade-book.net www.blade-book.net` to the vhost and
  a `Redirect permanent / https://blade-book.com/` block, re-run certbot with
  `-d blade-book.net -d www.blade-book.net` once its DNS resolves.
- Consider a 301 from `billboard.instockornot.club/blade-book/` to
  `blade-book.com/` once search engines have moved (keep the alias for the
  API either way — the pages call `/blade-book/api/`).
- The `.com` vhost CSP still allowlists `fonts.googleapis.com`/`fonts.gstatic.com`
  from before fonts were self-hosted — tighten to `'self'` when convenient.
