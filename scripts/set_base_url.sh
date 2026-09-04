#!/bin/bash
# RUNBOOK-domain §3: point the app's public origin at the new domain and restart.
# Run as: sudo bash /home/shg/blade-book/scripts/set_base_url.sh https://blade-book.com
# Then, as shg: python3 /home/shg/blade-book/scripts/publish_sweep.py --all
set -euo pipefail
URL=${1:?usage: set_base_url.sh https://host}
ENV=/etc/blade-book/.env
if grep -q '^BASE_URL=' "$ENV"; then sed -i "s#^BASE_URL=.*#BASE_URL=$URL#" "$ENV"; else echo "BASE_URL=$URL" >> "$ENV"; fi
echo "BASE_URL now: $(grep '^BASE_URL=' "$ENV")"
supervisorctl restart blade_book && sleep 1 && curl -s "$URL/api/healthz"; echo
