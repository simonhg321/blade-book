#!/bin/bash
# 2026-09-21: ship the new landing page + audit quick fixes.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_landing.sh`
set -e
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
# static pages (as shg, so nothing under the web root turns root-owned)
sudo -u shg -H bash -c "
  mkdir -p $WWW/img/landing &&
  cp $CODE/html/img/landing/*.jpg $WWW/img/landing/ &&
  cp $WWW/index.html $WWW/demo/index.before-landing.html &&
  cp $CODE/html/index.html $WWW/index.html &&
  cp $CODE/html/search/index.html $WWW/search/index.html &&
  cp $CODE/html/me/index.html $WWW/me/index.html &&
  cp $CODE/html/me/add/index.html $WWW/me/add/index.html"
bash $CODE/scripts/enable_404.sh            # 404 page + vhost line + apache reload
bash $CODE/scripts/restart_and_sweep.sh     # new register styles: restart, then rebuild every bundle
curl -s https://blade-book.com/ | grep -c 'One photo' | sed 's/^/landing live (1 = yes): /'
