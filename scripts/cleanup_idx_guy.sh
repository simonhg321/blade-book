#!/bin/bash
# One-time: remove the test user 'idx-guy' (idx@example.com) that leaked into the
# live DB during plan-07 development (2026-09-01). Surgical: this user only.
set -euo pipefail
DB=/var/lib/blade-book/blade-book.db
UID3=$(sqlite3 "$DB" "SELECT id FROM users WHERE email='idx@example.com' AND handle='idx-guy'")
[ -n "$UID3" ] || { echo "idx-guy not found — nothing to do"; exit 0; }
sqlite3 "$DB" "PRAGMA foreign_keys=ON; DELETE FROM search_fts WHERE rowid IN (SELECT knife_id FROM search_cards WHERE owner_id=$UID3); DELETE FROM users WHERE id=$UID3;"
rm -rf "/var/lib/blade-book/photos/$UID3" "/var/www/html/blade-book/@idx-guy"
echo "removed user $UID3 (idx-guy), photos dir, bundle. Remaining users:"
sqlite3 "$DB" "SELECT id, handle FROM users"
