#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
# 2026-09-28: the app log keeps 90 days, and the terms and the FAQ say so.
# The order matters: the app restarts FIRST, onto the handler that follows a
# moved log. An app that has not restarted keeps writing into the file the
# nightly run moves away, and those lines are deleted with it.
# The pages go live LAST: they promise 90 days, so the nightly run is installed
# before they say so. Safe to run again: every step repeats cleanly.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_log_limit.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book; LOGS=/var/log/blade-book
CRON='7 0 * * * cd /home/shg/blade-book && python3 scripts/rotate_log.py >> /var/log/blade-book/rotate.log 2>&1'
if ! grep -q '_PrivateWatchedFile' "$CODE/app.py" || ! test -f "$CODE/bb/logkeep.py"; then
  echo "STOP: $CODE does not have the log limit. Merge the log-limit branch into main first." >&2
  exit 1
fi
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Nothing was moved or deleted, and the pages still say what they said. Fix the app, then run this again." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Nothing was moved or deleted. Check $LOGS/app.log, then run this again." >&2
  exit 1
fi
echo "1/4 app restarted: $up"
cd "$CODE"
if ! sudo -u shg -H python3 scripts/rotate_log.py | sudo -u shg -H tee -a "$LOGS/rotate.log"; then
  echo "STOP: the first run failed. The nightly line was not installed. See above, then run this again." >&2
  exit 1
fi
# the app has to write its next line into the fresh app.log: ask for something it refuses and logs
mark="deploy-check-$(date +%s)"
curl -s -o /dev/null -X POST -H "Origin: https://$mark.invalid" http://127.0.0.1:5004/blade-book/api/auth/signout || true
sleep 1
if ! grep -q "$mark" "$LOGS/app.log"; then
  echo "STOP: the app did not write to the fresh $LOGS/app.log. The nightly line was not installed. Tell Sky." >&2
  exit 1
fi
echo "2/4 first run done, and the app writes to the fresh log: $(ls "$LOGS" | grep -c '^app\.log\.[0-9-]*$') day file(s)"
if ! bash "$CODE/scripts/add_cron_line.sh" shg 'scripts/rotate_log.py' "$CRON"; then
  echo "STOP: the nightly line was not installed. The pages still say what they said. See above, then run this again." >&2
  exit 1
fi
echo "3/4 nightly run installed: $(crontab -u shg -l | grep -c 'scripts/rotate_log.py') line, $(crontab -u shg -l | grep -c .) lines in all in the crontab of shg"
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "4/4 static pages copied"
for page in terms faq; do
  if curl -s --max-time 20 "https://blade-book.com/blade-book/$page/?fresh=$(date +%s)" | grep -q 'The app keeps that log for 90 days.'; then
    echo "ok   $page says 90 days"
  else
    echo "MISSING the 90 days sentence on $page (Cloudflare may hold the old page for up to 4 h)"
  fi
done
