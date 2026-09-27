#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
# 2026-09-27: the knife page — names with the graphic, one top bar, next and
# previous, the kit, the plate-only mark. Static pages, the app and the public
# bundles go live together. Safe to run again: every step repeats cleanly.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_page.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
# static pages and vibe.css first: a page must never ask for a stylesheet that is not there yet
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "1/3 static pages copied"
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Static pages are new; knife pages are not rebuilt. Fix the app, then run this again." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Knife pages are not rebuilt. Check /var/log/blade-book/app.log, then run this again." >&2
  exit 1
fi
echo "2/3 app restarted: $up"
# the sweep runs as shg: root's python has no app deps and would write root-owned files
sudo -u shg -H bash -c "cd $CODE && python3 scripts/publish_sweep.py --all"
echo "3/3 public pages rebuilt"
page=$(curl -s --max-time 20 "https://blade-book.com/@simon-collector/K80/?fresh=$(date +%s)" || true)
for needle in 'class="bb-top"' 'rel="next"' 'Large Sebenza 21 — Glorious' 'CAME WITH'; do
  if grep -q "$needle" <<<"$page"; then echo "ok   $needle"; else echo "MISSING $needle"; fi
done
echo 'Browsers and Cloudflare keep old pages and photos for up to 4 h.'
