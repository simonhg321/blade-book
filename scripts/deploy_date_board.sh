#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-10-15: the date board. Static pages (board, landing, wants) + the app
# (/api/born, ask-the-owner, schema v15 born_day). No publish sweep: register
# pages do not change. Safe to run again.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_date_board.sh`
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
born=$(curl -s --max-time 10 "http://127.0.0.1:5004/blade-book/api/born?d=7/28" || true)
if grep -q '"same_day"' <<<"$born"; then echo "3/3 /api/born answers"; else echo "MISSING: /api/born did not answer with groups: $born" >&2; exit 1; fi
page=$(curl -s --max-time 20 "https://blade-book.com/board/?fresh=$(date +%s)" || true)
for needle in 'BORN ON YOUR DATE' 'FIND IT' 'LATEST FOR SALE'; do
  if grep -q "$needle" <<<"$page"; then echo "ok   $needle"; else echo "MISSING $needle (Cloudflare may still hold the old page for up to 4 h)"; fi
done
