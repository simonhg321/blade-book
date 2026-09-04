#!/bin/bash
# Security review 2026-09-04 items H6, M5, M10 — three permission fixes, no code.
# Run as: sudo /home/shg/blade-book/scripts/harden_perms.sh
set -e
chmod 700 /home/backup && chmod 600 /home/backup/*.tgz
chown shg:shg /var/www/html && chmod 755 /var/www/html
chmod 750 /var/log/blade-book && chmod 640 /var/log/blade-book/*.log /var/log/blade-book/*.jsonl 2>/dev/null || true
ls -ld /home/backup /var/www/html /var/log/blade-book
