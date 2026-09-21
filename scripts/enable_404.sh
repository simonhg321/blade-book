#!/bin/bash
# One-time: serve html/404.html for missing static pages on blade-book.com.
# Simon: `! sudo bash /home/shg/blade-book/scripts/enable_404.sh`   (re-run-safe)
set -e
CONF=/etc/apache2/sites-enabled/blade-book-le-ssl.conf
install -o shg -g shg -m 644 /home/shg/blade-book/html/404.html /var/www/html/blade-book/404.html
if ! grep -q 'ErrorDocument 404' "$CONF"; then
  cp "$CONF" "$CONF.bak-$(date +%Y%m%d%H%M%S)"
  sed -i 's|^\(\s*\)DocumentRoot /var/www/html/blade-book$|&\n\1ErrorDocument 404 /404.html|' "$CONF"
fi
apache2ctl configtest && systemctl reload apache2
curl -s -o /dev/null -w 'missing page -> %{http_code}\n' https://blade-book.com/blade-book/nope/
curl -s https://blade-book.com/blade-book/nope/ | grep -c 'NOTHING ON THIS PAGE' | sed 's/^/styled 404 served: /'
