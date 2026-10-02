#!/bin/bash
# Turn on blade-book.com once DNS points here (docs/RUNBOOK-domain.md).
# Simon: `! sudo bash /home/shg/blade-book/scripts/enable_domain.sh you@example.com [--force]`
#   arg 1  certbot email (falls back to CERTBOT_EMAIL= in /etc/blade-book/.env)
#   --force  skip the "resolves to this box" guard (needed behind the Cloudflare proxy,
#            where the name resolves to Cloudflare's IPs; HTTP-01 still works through it)
set -euo pipefail
CODE=/home/shg/blade-book
EMAIL=${1:-}
FORCE=${2:-}
if [ "$EMAIL" = "--force" ]; then FORCE=--force; EMAIL=; fi
if [ -z "$EMAIL" ]; then EMAIL=$(grep -s '^CERTBOT_EMAIL=' /etc/blade-book/.env | cut -d= -f2-); fi
if [ -z "$EMAIL" ]; then echo "usage: enable_domain.sh <certbot email> [--force]  (or CERTBOT_EMAIL= in /etc/blade-book/.env)" >&2; exit 2; fi
if [ "$FORCE" != "--force" ] && ! getent hosts blade-book.com | grep -q "$(hostname -I | awk '{print $1}')"; then
  echo "blade-book.com does not resolve to this box (A record → $(hostname -I | awk '{print $1}')) — set DNS first, or pass --force behind the Cloudflare proxy"; exit 1
fi
cp "$CODE/deploy/apache-blade-book.com.conf" /etc/apache2/sites-available/blade-book.conf
a2enmod -q headers proxy proxy_http rewrite >/dev/null
a2ensite -q blade-book.conf
apache2ctl configtest
systemctl reload apache2
certbot --apache -n --agree-tos --redirect -d blade-book.com -d www.blade-book.com --email "$EMAIL" || \
  { echo "certbot failed — check DNS/propagation and re-run"; exit 1; }
curl -s https://blade-book.com/api/healthz; echo
echo "next: docs/RUNBOOK-domain.md step 3 — BASE_URL=https://blade-book.com in /etc/blade-book/.env, restart, publish_sweep.py --all"
