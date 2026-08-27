# RUNBOOK — moving blade-book to its own box

Trigger: 10–30 customers (spec §11). Everything lives in four dirs and one
supervisor program; nothing imports from billboard.

1. New Linode: same OS, `apt install apache2 supervisor python3-pip sqlite3`,
   `pip install -r requirements.txt` (gunicorn lands in /usr/local/bin).
2. On stark: `sudo supervisorctl stop blade_book` (writes stop; static pages keep serving).
3. `rsync -a /home/shg/blade-book/ new:/home/shg/blade-book/`
   `rsync -a /var/lib/blade-book/ new:/var/lib/blade-book/`
   `sudo rsync -a /etc/blade-book/ new:/etc/blade-book/`
   `rsync -a /var/www/html/blade-book/ new:/var/www/html/blade-book/`
4. On new: `sudo bash /home/shg/blade-book/scripts/install.sh` — but with the
   Apache include placed in a `blade-book.com` vhost instead of the billboard
   ones (certbot for TLS).
5. `curl http://127.0.0.1:5004/blade-book/api/healthz` → ok.
6. DNS: `blade-book.com` → new box. Leave the stark `/blade-book/` alias as a
   redirect for 30 days, then remove the include + supervisor conf on stark.
7. Move the backup cron; confirm `/home/backup/blade-book-*.tgz` appears on new.
