#!/bin/bash
# Wire blade-book to Resend using billboard's existing key + verified domain.
# Run as: sudo bash /home/shg/blade-book/scripts/wire_resend.sh
# Idempotent: skips keys already present in /etc/blade-book/.env. No secrets printed.
set -euo pipefail
SRC=/etc/billboard/.env
DST=/etc/blade-book/.env
KEY=$(grep -o '^RESEND_API_KEY=.*' "$SRC" | head -1 | cut -d= -f2- | tr -d '"')
[ -n "$KEY" ] || { echo "no RESEND_API_KEY in $SRC"; exit 1; }
if grep -q '^RESEND_API_KEY=' "$DST"; then
  echo "RESEND_API_KEY already set in $DST — leaving it"
else
  printf 'RESEND_API_KEY=%s\n' "$KEY" >> "$DST"; echo "RESEND_API_KEY added"
fi
if grep -q '^MAIL_FROM=' "$DST"; then
  echo "MAIL_FROM already set — leaving it"
else
  printf 'MAIL_FROM=blade-book <noreply@instockornot.club>\n' >> "$DST"; echo "MAIL_FROM added (instockornot.club is verified in Resend)"
fi
chmod 640 "$DST"; chown root:shg "$DST"
supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
grep -c 'ResendMailer' /var/log/blade-book/app.log >/dev/null 2>&1 && tail -1 /var/log/blade-book/app.log | grep -o 'ResendMailer' || true
