#!/bin/bash
# Restart the app, then rebuild every public bundle + the search index.
# The sweep must run as shg (root's python has no app deps and would write
# root-owned files) — so this script drops privileges for that step even
# when invoked under sudo.
set -e
sudo supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
cd /home/shg/blade-book
if [ "$(id -un)" = "root" ]; then
  sudo -u shg -H python3 scripts/publish_sweep.py --all
else
  python3 scripts/publish_sweep.py --all
fi
