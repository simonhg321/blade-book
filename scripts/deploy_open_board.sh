#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-10-23: free and open for now. The For Sale board opens to any verified
# account with a live knife while the hard gate is off (bb/db.board_min_knife_age_days);
# the settings page drops the price card and the slot meter during early access.
# Copies the static pages, restarts the app, probes it. Safe to run again.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_open_board.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
if ! grep -q 'def board_min_knife_age_days' "$CODE/bb/db.py"; then
  echo "STOP: $CODE is not on the open-board commit — pull first. Nothing restarted." >&2
  exit 1
fi
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
billing=$(curl -s --max-time 10 http://127.0.0.1:5004/blade-book/api/billing || true)
if grep -q '"hard_gate":false' <<<"$billing"; then echo "3/3 hard gate is off: the board is open to any verified account with a live knife"; else echo "STOP: /api/billing does not say hard_gate false: $billing" >&2; exit 1; fi
page=$(curl -s --max-time 20 "https://blade-book.com/me/settings/?fresh=$(date +%s)" || true)
if grep -q 'free during early access' <<<"$page"; then echo "ok   settings page carries the early-access line"; else echo "settings page not updated yet (Cloudflare may hold the old page for up to 4 h)"; fi
