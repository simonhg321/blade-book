#!/bin/bash
# nightly: photos + a CONSISTENT db snapshot (sqlite3 .backup, never the live
# file) → /home/backup, keep 14. Off-box: the Linode backup service covers the
# box nightly (Simon, 2026-09-03) — no bucket copy. Restore: see RUNBOOK-move.md.
set -euo pipefail
umask 077   # the tarball holds the whole DB + every photo — owner-only from birth
DATA=${BLADEBOOK_DATA_DIR:-/var/lib/blade-book}
BK=${BLADEBOOK_BACKUP_DIR:-/home/backup}
OUT=$BK/blade-book-$(date +%Y%m%d-%H%M%S).tgz
STAGE=$(mktemp -d "$BK/.stage.XXXXXX")
trap 'rm -rf "$STAGE"; [ "${OK:-}" = 1 ] || rm -f "$OUT"' EXIT
sqlite3 "$DATA/blade-book.db" ".backup '$STAGE/blade-book.db'"
NAME=$(basename "$DATA")
# tar exits 1 when something changed while it read, 2 when it failed. The data
# dir is live (03:30 is a cron minute: the monitor writes its state there), so 1
# is a note in the log, not a stop: photos are written once and the db in the
# tarball is the snapshot above. 2026-09-04 to 09-28 this stopped every run.
RC=0
tar czf "$OUT" \
  --exclude="$NAME/blade-book.db*" --exclude="$NAME/exports" --exclude="$NAME/publish-locks" \
  -C "$(dirname "$DATA")" "$NAME" \
  -C "$STAGE" blade-book.db || RC=$?
if [ "$RC" -gt 1 ]; then
  echo "$(date -Is) tar failed (exit $RC); no tarball kept" >&2
  exit "$RC"
fi
if [ "$RC" = 1 ]; then
  echo "$(date -Is) the data dir changed while it was read (tar exit 1); the tarball is kept"
fi
gzip -t "$OUT"
OK=1
rm -rf "$STAGE"
ls -1t "$BK"/blade-book-*.tgz | tail -n +15 | xargs -r rm -f
echo "$(date -Is) wrote $OUT ($(du -h "$OUT" | cut -f1))"
