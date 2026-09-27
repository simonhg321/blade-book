#!/bin/bash
# 2026-09-26: knives are born private. Static pages and the app go live together,
# or the new add page would say "private" while the old app still publishes.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_born_private.sh`
set -e
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
sudo supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
sudo -u shg -H bash -c "
  cp $CODE/html/index.html $WWW/index.html &&
  cp $CODE/html/how/index.html $WWW/how/index.html &&
  cp $CODE/html/faq/index.html $WWW/faq/index.html &&
  cp $CODE/html/me/index.html $WWW/me/index.html &&
  cp $CODE/html/me/add/index.html $WWW/me/add/index.html"
curl -s https://blade-book.com/blade-book/me/add/ | grep -c 'showpubbox' | sed 's/^/add page live (1+ = yes): /'
