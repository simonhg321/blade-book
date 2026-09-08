#!/bin/bash
# plan 14: restart the app on the v12 code, then rebuild every public bundle
# (Maker row) and the search index (maker_name). Sweep runs as shg, never root.
set -e
sudo supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
cd /home/shg/blade-book && python3 scripts/publish_sweep.py --all
