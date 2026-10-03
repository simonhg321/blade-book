#!/usr/bin/env bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# One-time: the shared libraries Playwright's headless Chromium needs (libatk, libnss3, …)
# so Sky can screenshot pages on stark. Runs apt-get; needs sudo.
#   sudo bash /home/shg/blade-book/scripts/headless_deps.sh
set -euo pipefail
PW=/tmp/claude-1000/-home-shg-billboard/4d04a175-7e31-4f75-8643-c936982ddab7/scratchpad/pw/bin/playwright
if [ -x "$PW" ]; then
  "$PW" install-deps chromium
else
  apt-get install -y libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libnss3 libnspr4 libdrm2 libxshmfence1
fi
