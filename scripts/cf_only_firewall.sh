#!/bin/bash
# Security review 2026-09-04 M6 — let only Cloudflare reach 80/443.
# Without --apply this only PRINTS what it would do. With --apply it:
#   1. allows SSH (22) FIRST so an enable can never lock us out,
#   2. allows every current Cloudflare IPv4/IPv6 range on 80,443/tcp,
#   3. denies 80,443/tcp from everywhere else,
#   4. removes any older "allow 80/tcp" / "allow 443/tcp" from Anywhere — ufw is
#      first-match, and those sit ABOVE the new denies, so without this step the
#      denies never fire (found on stark 2026-10-02, the first --apply),
#   5. enables ufw if it was inactive.
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
run ufw deny 80/tcp
run ufw deny 443/tcp
# the open rules from before: delete AFTER the Cloudflare allows exist, so the
# proxied sites never drop. `ufw delete` of a rule that is not there is a no-op.
for p in 80 443; do
  if ufw status | grep -qE "^$p/tcp +ALLOW +Anywhere"; then run ufw delete allow $p/tcp; fi
done
run ufw --force enable
[ "$APPLY" = "--apply" ] && ufw status numbered | tail -8
[ "$APPLY" = "--apply" ] || echo "(dry run — add --apply to do it)"
