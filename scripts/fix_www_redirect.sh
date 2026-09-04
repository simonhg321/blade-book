#!/bin/bash
# certbot --redirect comments out our www→apex RewriteRule in the :443 twin as a
# "possible loop". It is not one (fires only for Host www.*, targets the apex).
# Re-enable it. Run as: sudo bash /home/shg/blade-book/scripts/fix_www_redirect.sh
set -euo pipefail
F=/etc/apache2/sites-available/blade-book-le-ssl.conf
sed -i -E 's|^#\s*(RewriteCond %\{HTTP_HOST\} \^www\\\. \[NC\])|\1|; s|^#\s*(RewriteRule \^ https://blade-book\.com%\{REQUEST_URI\} \[R=301,L\])|\1|' "$F"
grep -nE '^\s*Rewrite(Cond|Rule)' "$F"
apache2ctl configtest && systemctl reload apache2 && echo reloaded
