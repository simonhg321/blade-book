#!/bin/bash
# Point outbound mail at the blade-book.com Resend domain and restart the app.
# Run as: sudo bash /home/shg/blade-book/scripts/set_mail_from.sh [address]
# Default address: noreply@blade-book.com (the domain must be verified in Resend first).
set -euo pipefail
ADDR=${1:-noreply@blade-book.com}
ENV=/etc/blade-book/.env
FROM="blade-book <$ADDR>"
if grep -q '^MAIL_FROM=' "$ENV"; then sed -i "s#^MAIL_FROM=.*#MAIL_FROM=$FROM#" "$ENV"; else echo "MAIL_FROM=$FROM" >> "$ENV"; fi
echo "MAIL_FROM now: $(grep '^MAIL_FROM=' "$ENV")"
supervisorctl restart blade_book && sleep 1 && curl -s https://blade-book.com/api/healthz; echo
