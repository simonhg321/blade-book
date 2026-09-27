#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
# 2026-09-27: the knife page — names with the graphic, one top bar, next and
# previous, the kit, the plate-only mark. Static pages, the app and the public
# bundles go live together.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_page.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
# the sweep runs as shg: root's python has no app deps and would write root-owned files
sudo -u shg -H bash -c "cd $CODE && python3 scripts/publish_sweep.py --all"
page=$(curl -s "https://blade-book.com/@simon-collector/K80/?fresh=$(date +%s)")
for needle in 'class="bb-top"' 'rel="next"' 'Large Sebenza 21 — Glorious' 'CAME WITH'; do
  if grep -q "$needle" <<<"$page"; then echo "ok   $needle"; else echo "MISSING $needle"; fi
done
echo 'Browsers and Cloudflare keep old pages and photos for up to 4 h.'
