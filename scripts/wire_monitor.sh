#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
# Plan 12 deploy, one shot (sudo): set BLADEBOOK_ADMIN_EMAIL in /etc/blade-book/.env
# (idempotent), then re-run install.sh (adds the */5 monitor cron, re-copies html,
# restarts the app). Simon: `! sudo bash /home/shg/blade-book/scripts/wire_monitor.sh you@example.com`
set -euo pipefail
ADDR=${1:-}
if [ -z "$ADDR" ]; then echo "usage: wire_monitor.sh <admin email>" >&2; exit 2; fi
ENV=/etc/blade-book/.env
if grep -q '^BLADEBOOK_ADMIN_EMAIL=' "$ENV"; then
  sed -i "s#^BLADEBOOK_ADMIN_EMAIL=.*#BLADEBOOK_ADMIN_EMAIL=$ADDR#" "$ENV"
else
  printf 'BLADEBOOK_ADMIN_EMAIL=%s\n' "$ADDR" >> "$ENV"
fi
chown root:shg "$ENV" && chmod 640 "$ENV"
bash /home/shg/blade-book/scripts/install.sh
crontab -u shg -l | grep -c 'blade-book/scripts/monitor.py' | sed 's/^/monitor cron lines: /'
echo "first monitor pass on the next */5 minute; log: /var/log/blade-book/monitor.log"
