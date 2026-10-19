#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-10-19: a password on any account. Static pages (settings, landing, how)
# + the app (POST /api/settings/password). No schema change, no publish
# sweep. Safe to run again.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_password.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "1/3 static pages copied"
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Static pages are new; the API is old. Check /var/log/blade-book/app.log." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf --max-time 10 http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Check /var/log/blade-book/app.log." >&2
  exit 1
fi
echo "2/3 app restarted: $up"
# signed out, the new route must answer 401 (not 404): proves the new code is serving
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST -H 'Content-Type: application/json' \
       -d '{"password":"probe-probe-probe"}' http://127.0.0.1:5004/blade-book/api/settings/password || true)
if [ "$code" = "401" ]; then echo "3/3 /api/settings/password is live"; else echo "MISSING: /api/settings/password answered $code, expected 401" >&2; exit 1; fi
page=$(curl -s --max-time 20 "https://blade-book.com/me/settings/?fresh=$(date +%s)" || true)
if grep -q 'id="passwordcard"' <<<"$page"; then echo "ok   settings page has the PASSWORD card"; else echo "settings page not updated yet (Cloudflare may hold the old page for up to 4 h)"; fi
