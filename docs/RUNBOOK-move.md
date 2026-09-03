# RUNBOOK — moving blade-book to its own box

Trigger: 10–30 customers (spec §11). Everything lives in four dirs and one
supervisor program; nothing imports from billboard.

1. New Linode: same OS, `apt install apache2 supervisor python3-pip sqlite3`,
   `pip install -r requirements.txt` (which now pulls `pillow-heif` — HEIC thumbs/decode depend on it; gunicorn lands in /usr/local/bin).
2. On stark: `sudo supervisorctl stop blade_book` (writes stop; static pages keep serving).
3. `rsync -a /home/shg/blade-book/ new:/home/shg/blade-book/`
   `rsync -a /var/lib/blade-book/ new:/var/lib/blade-book/`
   `sudo rsync -a /etc/blade-book/ new:/etc/blade-book/`
   `rsync -a /var/www/html/blade-book/ new:/var/www/html/blade-book/`
   `exports/` under the data dir is transient (ZIPs mid-download) — safe to exclude from the rsync;
   `publish-locks/` likewise.
4. On new: `sudo bash /home/shg/blade-book/scripts/install.sh` — but with the
   Apache include placed in a `blade-book.com` vhost instead of the billboard
   ones (certbot for TLS). install.sh already runs the one-time
   `publish_sweep.py --all` as shg (via `sudo -u shg`) — but after any manual
   restore, always run `python3 scripts/publish_sweep.py --all` as shg
   yourself too — never as root: a root-owned bundle can never be rebuilt by
   the app again, permanently breaking that user's publish.
5. `curl http://127.0.0.1:5004/blade-book/api/healthz` → ok.
6. DNS: `blade-book.com` → new box. Leave the stark `/blade-book/` alias as a
   redirect for 30 days, then remove the include + supervisor conf on stark.
7. Move the crontab — four lines, from `crontab -u shg -l` on stark:
   `30 3 * * * bash /home/shg/blade-book/scripts/backup.sh >> /var/log/blade-book/backup.log 2>&1`
   `15 4 * * * cd /home/shg/blade-book && python3 scripts/purge_drafts.py >> /var/log/blade-book/purge.log 2>&1`
   `*/5 * * * * /usr/bin/python3 /home/shg/blade-book/scripts/publish_sweep.py >> /var/log/blade-book/publish.log 2>&1`
   `*/15 * * * * cd /home/shg/blade-book && python3 scripts/match_cron.py >> /var/log/blade-book/match.log 2>&1`
   Confirm `/home/backup/blade-book-*.tgz` appears on new, check `purge.log` the following morning, monitor
   `publish.log` for publish_sweep activity, and monitor `match.log` for match_cron activity (silent unless
   it sends).
8. The search index (`search_cards`/`search_fts`) is derived data, not a
   thing to restore/rsync — after any restore, repair/backfill it the same
   way as the bundles: `python3 scripts/publish_sweep.py --all` as shg.
   Moderation state lives in the DB, not on disk: `reports` (open = resolved_at NULL) and
   `knives.hidden_at/hidden_by/hidden_note`. A hidden knife is excluded from the board, the
   owner's bundle, the search index and wants matching by the same column, so after a move a
   `publish_sweep.py --all` regenerates bundles with hidden knives already absent. Admin queue:
   /blade-book/admin/ (is_admin=1 in users — still a sqlite3 flip; subscriptions flip from the
   page's USERS table or `python3 scripts/sub.py <handle> active|free|lapsed`, `--list` to see
   everyone).
   Upgrades: the live checkout is the code the crons run, so merge and `scripts/restart.sh`
   back-to-back, then confirm `SELECT version FROM schema_version` matches bb/db.py.
   v8 = users.featured_knife_id, the pinned register hero.
