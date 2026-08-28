#!/bin/bash
# Wire blade-book's decoder: copy billboard's ANTHROPIC_API_KEY into
# /etc/blade-book/.env, set DECODER_MODEL (default claude-sonnet-5 until the
# eval says otherwise), install pillow-heif for the gunicorn user, restart.
# Run as: sudo bash /home/shg/blade-book/scripts/wire_anthropic.sh [model]
# Idempotent: existing keys are left alone. Never prints the key.
set -euo pipefail
SRC=/etc/billboard/.env
DST=/etc/blade-book/.env
MODEL="${1:-claude-sonnet-5}"
KEY=$(grep -o '^ANTHROPIC_API_KEY=.*' "$SRC" | head -1 | cut -d= -f2- | tr -d '"')
[ -n "$KEY" ] || { echo "no ANTHROPIC_API_KEY in $SRC"; exit 1; }
if grep -q '^ANTHROPIC_API_KEY=' "$DST"; then
  echo "ANTHROPIC_API_KEY already set in $DST — leaving it"
else
  printf 'ANTHROPIC_API_KEY=%s\n' "$KEY" >> "$DST"; echo "ANTHROPIC_API_KEY added"
fi
if grep -q '^DECODER_MODEL=' "$DST"; then
  echo "DECODER_MODEL already set — leaving it ($(grep -o '^DECODER_MODEL=.*' "$DST"))"
else
  printf 'DECODER_MODEL=%s\n' "$MODEL" >> "$DST"; echo "DECODER_MODEL=$MODEL added"
fi
chmod 640 "$DST"; chown root:shg "$DST"
RUNAS=$(grep -o '^user=.*' /etc/supervisor/conf.d/blade_book.conf 2>/dev/null | cut -d= -f2 || true)
RUNAS="${RUNAS:-shg}"
echo "gunicorn runs as: $RUNAS — installing pillow-heif for that user"
# --break-system-packages: stark's system pip is externally-managed and there is no apt package
sudo -u "$RUNAS" python3 -m pip install --user --quiet --break-system-packages pillow-heif 2>&1 | tail -2 || echo "pip install failed — HEIC thumbs stay off (JPEG unaffected)"
supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
tail -2 /var/log/blade-book/app.log | grep -o 'decoder [A-Za-z]*' || true
