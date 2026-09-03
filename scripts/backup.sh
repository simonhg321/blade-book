#!/bin/bash
# nightly: photos + a CONSISTENT db snapshot (sqlite3 .backup, never the live
# file) → /home/backup, keep 14. Off-box: the Linode backup service covers the
# box nightly (Simon, 2026-09-03) — no bucket copy. Restore: see RUNBOOK-move.md.
set -euo pipefail
DATA=${BLADEBOOK_DATA_DIR:-/var/lib/blade-book}
OUT=/home/backup/blade-book-$(date +%Y%m%d-%H%M%S).tgz
STAGE=$(mktemp -d /home/backup/.stage.XXXXXX)
trap 'rm -rf "$STAGE"' EXIT
sqlite3 "$DATA/blade-book.db" ".backup '$STAGE/blade-book.db'"
NAME=$(basename "$DATA")
tar czf "$OUT" \
  --exclude="$NAME/blade-book.db*" --exclude="$NAME/exports" --exclude="$NAME/publish-locks" \
  -C "$(dirname "$DATA")" "$NAME" \
  -C "$STAGE" blade-book.db
rm -rf "$STAGE"
ls -1t /home/backup/blade-book-*.tgz | tail -n +15 | xargs -r rm -f
echo "$(date -Is) wrote $OUT ($(du -h "$OUT" | cut -f1))"
