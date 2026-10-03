#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# Put ONE line in a user's crontab and keep every other line as it is.
#   add_cron_line.sh <user> <pattern> <line>
# Any line that contains <pattern> is replaced by <line>, so a second run adds
# nothing. The table is read first and written only when the read worked:
# `( crontab -l | grep -v …; echo … ) | crontab -` installs an EMPTY table when
# the read fails under set -e (review, 2026-09-28).
set -euo pipefail
if [ $# -ne 3 ]; then echo "usage: $0 <user> <pattern> <line>" >&2; exit 2; fi
WHO=$1; PATTERN=$2; LINE=$3
ERR=$(mktemp); trap 'rm -f "$ERR"' EXIT
if OLD=$(crontab -u "$WHO" -l 2>"$ERR"); then
  :
elif grep -q 'no crontab for' "$ERR"; then
  OLD=''
else
  echo "STOP: could not read the crontab of $WHO: $(cat "$ERR"). Nothing was written." >&2
  exit 1
fi
KEPT=$(printf '%s\n' "$OLD" | grep -vF -- "$PATTERN" | grep -v '^$' || true)
if [ -n "$KEPT" ]; then NEW=$(printf '%s\n%s' "$KEPT" "$LINE"); else NEW=$LINE; fi
WANT=$(( $(printf '%s' "$KEPT" | grep -c . || true) + 1 ))
HAVE=$(printf '%s\n' "$NEW" | grep -c . || true)
if [ "$HAVE" -ne "$WANT" ] || ! printf '%s\n' "$NEW" | grep -qxF -- "$LINE"; then
  echo "STOP: the new crontab of $WHO does not add up ($HAVE lines, wanted $WANT). Nothing was written." >&2
  exit 1
fi
printf '%s\n' "$NEW" | crontab -u "$WHO" -
