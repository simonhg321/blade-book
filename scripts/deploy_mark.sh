#!/bin/bash
# 2026-09-26: the new mark — etched repeat over the photo + the plate under it.
# Sets the address printed on the plate, restarts the app (so a save never
# re-publishes with the old white mark), then rebuilds every public bundle.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_mark.sh`
set -euo pipefail
ENV=/etc/blade-book/.env
grep -q '^PLATE_BASE=' "$ENV" || echo 'PLATE_BASE=blade-book.com' >> "$ENV"
echo "plate address: $(grep '^PLATE_BASE=' "$ENV")"
supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
# the sweep runs as shg: root's python has no app deps and would write root-owned files
sudo -u shg -H bash -c 'cd /home/shg/blade-book && python3 scripts/publish_sweep.py --all'
sudo -u shg -H python3 - <<'PY'
from PIL import Image
im = Image.open('/var/www/html/blade-book/@simon-collector/img/K80.jpg').convert('RGB')
w, h = im.size
plate = all(abs(a - b) <= 8 for a, b in zip(im.getpixel((3, h - 3)), (250, 246, 238)))
print(f'K80.jpg {w}x{h} — plate under the photo: {"yes" if plate else "NO"}')
PY
echo 'Cloudflare and browsers keep the old photos for up to 4 h (max-age=14400).'
