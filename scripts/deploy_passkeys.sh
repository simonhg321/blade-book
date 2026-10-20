#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-10-19: passkeys. Installs py_webauthn for the app's user, copies the
# static pages, restarts the app (schema v16 migrates itself), probes both
# begin routes. Safe to run again.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_passkeys.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
# 1/4 the library, for the user gunicorn runs as (same precedent as wire_anthropic.sh)
sudo -u shg -H python3 -m pip install --user --quiet --break-system-packages 'webauthn>=3.0'
if ! sudo -u shg -H python3 -c 'import webauthn, jwt, cbor2; print("webauthn", webauthn.__version__)'; then
  echo "STOP: python3 cannot import webauthn (or PyJWT broke with the newer cryptography). Nothing restarted." >&2
  exit 1
fi
echo "1/4 library present"
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "2/4 static pages copied"
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Static pages are new; the API is old. Check /var/log/blade-book/app.log." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf --max-time 10 http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Check /var/log/blade-book/app.log." >&2
  exit 1
fi
echo "3/4 app restarted: $up"
# the sign-in begin must answer 200 with a challenge: proves the library loaded under gunicorn and v16 is in
begin=$(curl -s --max-time 10 -X POST -H 'Host: blade-book.com' http://127.0.0.1:5004/blade-book/api/auth/passkey/begin || true)
if grep -q '"challenge"' <<<"$begin"; then echo "ok   /blade-book/api/auth/passkey/begin answers with a challenge"; else echo "STOP: passkey begin did not answer with a challenge: $begin" >&2; exit 1; fi
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -X POST http://127.0.0.1:5004/blade-book/api/settings/passkeys/begin || true)
if [ "$code" = "401" ]; then echo "4/4 /blade-book/api/settings/passkeys/begin is live (401 signed out)"; else echo "STOP: settings passkeys begin answered $code, expected 401" >&2; exit 1; fi
page=$(curl -s --max-time 20 "https://blade-book.com/me/settings/?fresh=$(date +%s)" || true)
if grep -q 'id="passkeycard"' <<<"$page"; then echo "ok   settings page has the PASSKEYS card"; else echo "settings page not updated yet (Cloudflare may hold the old page for up to 4 h)"; fi
