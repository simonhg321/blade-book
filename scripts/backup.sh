#!/bin/bash
# nightly: tar the whole data dir (db + photos) to /home/backup, keep 14.
# Off-box copy (object storage) is added when the bucket exists — spec §11.
set -euo pipefail
DATA=${BLADEBOOK_DATA_DIR:-/var/lib/blade-book}
OUT=/home/backup/blade-book-$(date +%Y%m%d-%H%M%S).tgz
sqlite3 "$DATA/blade-book.db" "PRAGMA wal_checkpoint(TRUNCATE);" >/dev/null 2>&1 || true
tar czf "$OUT" -C "$(dirname "$DATA")" "$(basename "$DATA")"
ls -1t /home/backup/blade-book-*.tgz | tail -n +15 | xargs -r rm -f
echo "$(date -Is) wrote $OUT ($(du -h "$OUT" | cut -f1))"
