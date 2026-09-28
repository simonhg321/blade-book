#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
# 2026-09-27: admin activity — the admin page shows who is stuck, who did what
# and who came by; the terms and the FAQ say that we keep logs. Static pages and
# the app go live together. Safe to run again: every step repeats cleanly.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_activity.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
LOG=/var/log/apache2/blade-book_access.log
if ! grep -q 'def activity_summary' "$CODE/bb/routes/admin.py"; then
  echo "STOP: $CODE does not have the activity route. Merge the admin-activity branch into main first." >&2
  exit 1
fi
if ! sudo -u shg -H test -r "$LOG"; then
  echo "STOP: shg cannot read $LOG. The page would show no visitors. Add shg to group adm, then run this again." >&2
  exit 1
fi
# static pages first: the page must never ask for a script that is not there yet
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "1/2 static pages copied"
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Static pages are new; the activity sections will say they could not load. Fix the app, then run this again." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Check /var/log/blade-book/app.log, then run this again." >&2
  exit 1
fi
echo "2/2 app restarted: $up"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5004/blade-book/api/admin/activity || true)
if [ "$code" = "401" ]; then echo "ok   the activity route answers, and refuses a signed-out caller"; else echo "MISSING the activity route (got $code, wanted 401)"; fi
for page in terms faq; do
  if curl -s --max-time 20 "https://blade-book.com/blade-book/$page/?fresh=$(date +%s)" | grep -q 'Like every web server'; then
    echo "ok   $page says we keep logs"
  else
    echo "MISSING the new sentence on $page"
  fi
done
echo 'Open https://blade-book.com/blade-book/admin/ — browsers and Cloudflare keep old pages for up to 4 h; reload hard if the sections are missing.'
