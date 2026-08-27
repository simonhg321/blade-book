#!/bin/bash
# Turn on blade-book.com once DNS (A record → <ORIGIN-IP>) resolves.
# Simon: `! sudo bash /home/shg/blade-book/scripts/enable_domain.sh`
set -euo pipefail
CODE=/home/shg/blade-book
if ! getent hosts blade-book.com | grep -q "$(hostname -I | awk '{print $1}')"; then
  echo "blade-book.com does not resolve to this box yet — set the A record first"; exit 1
fi
cp "$CODE/deploy/apache-blade-book.com.conf" /etc/apache2/sites-available/blade-book.conf
a2enmod -q headers proxy proxy_http rewrite >/dev/null
a2ensite -q blade-book.conf
apache2ctl configtest
systemctl reload apache2
certbot --apache -n --agree-tos --redirect -d blade-book.com -d www.blade-book.com \
  --email "$(grep -s '^CERTBOT_EMAIL=' /etc/blade-book/.env | cut -d= -f2-)" || \
  echo "certbot failed — add CERTBOT_EMAIL=... to /etc/blade-book/.env and re-run"
curl -s https://blade-book.com/api/healthz; echo
