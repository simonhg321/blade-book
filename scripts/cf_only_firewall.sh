#!/bin/bash
# Security review 2026-09-04 M6 — let only Cloudflare reach 80/443.
# Without --apply this only PRINTS what it would do. With --apply it:
#   1. allows SSH (22) FIRST so an enable can never lock us out,
#   2. allows every current Cloudflare IPv4/IPv6 range on 80,443/tcp,
#   3. removes any older "allow 80/tcp" / "allow 443/tcp" from Anywhere (the
#      Cloudflare allows are already in, so the proxied sites stay up),
#   4. denies 80,443/tcp from everywhere else — APPENDED, so it sits below the allows,
#   5. enables ufw if it was inactive,
#   6. checks the site through Cloudflare and ROLLS BACK (deletes the denies,
#      re-opens 80/443) if it does not answer 200.
# ORDER MATTERS. ufw is first-match, and `ufw deny 80/tcp` with an existing
# `allow 80/tcp` does not append — it rewrites that rule IN PLACE, keeping its
# slot above every Cloudflare allow. Doing the deny before the delete put both
# sites behind a 522 for seven minutes on stark, 2026-10-02 23:50 UTC. Not
# applied there since (Simon: not worth it); kept correct for the next box.
# Run as root. Re-run after Cloudflare updates its ranges (rare).
set -euo pipefail
APPLY=${1:-}
V4=$(curl -fsS https://www.cloudflare.com/ips-v4) || { echo "could not fetch Cloudflare v4 ranges"; exit 1; }
V6=$(curl -fsS https://www.cloudflare.com/ips-v6) || { echo "could not fetch Cloudflare v6 ranges"; exit 1; }
N4=$(printf '%s\n' "$V4" | grep -c '/'); N6=$(printf '%s\n' "$V6" | grep -c '/')
[ "$N4" -ge 10 ] && [ "$N6" -ge 5 ] || { echo "range lists look wrong ($N4 v4, $N6 v6) — refusing"; exit 1; }
run() { if [ "$APPLY" = "--apply" ]; then "$@"; else echo "would: $*"; fi; }
echo "== ufw status now:"; ufw status | head -3
run ufw allow 22/tcp comment 'ssh — always first'
for ip in $V4 $V6; do run ufw allow proto tcp from "$ip" to any port 80,443 comment cloudflare; done
# the open rules from before go BEFORE the denies are written (see header).
for p in 80 443; do
  if ufw status | grep -qE "^$p/tcp +ALLOW +Anywhere"; then run ufw delete allow $p/tcp; fi
done
run ufw deny 80/tcp
run ufw deny 443/tcp
run ufw --force enable
if [ "$APPLY" = "--apply" ]; then
  ufw status numbered | grep -E "DENY|cloudflare" | head -4
  sleep 2
  code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://blade-book.com/ || true)
  if [ "$code" != "200" ]; then
    echo "!! blade-book.com answered $code through Cloudflare — rolling back"
    ufw delete deny 80/tcp; ufw delete deny 443/tcp; ufw allow 80/tcp; ufw allow 443/tcp
    exit 1
  fi
  echo "ok: 200 through Cloudflare, origin closed to the rest"
fi
[ "$APPLY" = "--apply" ] || echo "(dry run — add --apply to do it)"
