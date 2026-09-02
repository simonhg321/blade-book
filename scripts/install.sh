#!/bin/bash
# blade-book one-time install / re-run-safe. Simon: `! sudo bash /home/shg/blade-book/scripts/install.sh`
set -euo pipefail
CODE=/home/shg/blade-book
mkdir -p /etc/blade-book /var/lib/blade-book/photos /var/log/blade-book /var/www/html/blade-book /home/backup
chown shg:shg /var/lib/blade-book /var/lib/blade-book/photos /var/log/blade-book /var/www/html/blade-book
chmod 700 /var/lib/blade-book
if [ ! -f /etc/blade-book/.env ]; then
  printf '# blade-book secrets — never commit\nSESSION_KEY=%s\n' "$(openssl rand -hex 32)" > /etc/blade-book/.env
fi
chown root:shg /etc/blade-book/.env && chmod 640 /etc/blade-book/.env
cp "$CODE/deploy/supervisor-blade_book.conf" /etc/supervisor/conf.d/blade_book.conf
cp "$CODE/deploy/apache-blade-book.conf" /etc/apache2/conf-available/blade-book.conf
for v in /etc/apache2/sites-enabled/billboard.conf /etc/apache2/sites-enabled/billboard-le-ssl.conf; do
  grep -q 'conf-available/blade-book.conf' "$v" || \
    sed -i 's#^\(\s*\)ProxyPass        /api/#\1Include /etc/apache2/conf-available/blade-book.conf\n\1ProxyPass        /api/#' "$v"
done
mkdir -p /var/www/html/blade-book && cp -r "$CODE/html/." /var/www/html/blade-book/
apache2ctl configtest
systemctl reload apache2
supervisorctl reread && supervisorctl update && supervisorctl restart blade_book || supervisorctl start blade_book
( crontab -u shg -l 2>/dev/null | grep -v 'blade-book/scripts/backup.sh' | grep -v 'scripts/purge_drafts.py' | grep -v 'blade-book/scripts/publish_sweep.py' | grep -v 'scripts/match_cron.py'; \
  echo '30 3 * * * bash /home/shg/blade-book/scripts/backup.sh >> /var/log/blade-book/backup.log 2>&1'; \
  echo '15 4 * * * cd /home/shg/blade-book && python3 scripts/purge_drafts.py >> /var/log/blade-book/purge.log 2>&1'; \
  echo '*/5 * * * * /usr/bin/python3 /home/shg/blade-book/scripts/publish_sweep.py >> /var/log/blade-book/publish.log 2>&1'; \
  echo '*/15 * * * * cd /home/shg/blade-book && python3 scripts/match_cron.py >> /var/log/blade-book/match.log 2>&1' ) | crontab -u shg -
# one-time first-publish: this script runs as root (sudo), so drop privileges
# explicitly — a bundle built as root can never be rebuilt by the app (which
# runs as shg) again, permanently breaking that user's publish.
sudo -u shg /usr/bin/python3 "$CODE/scripts/publish_sweep.py" --all
sleep 1; curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
