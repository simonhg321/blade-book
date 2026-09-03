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
7. Move the crontab — five lines, from `crontab -u shg -l` on stark:
   `30 3 * * * bash /home/shg/blade-book/scripts/backup.sh >> /var/log/blade-book/backup.log 2>&1`
   `15 4 * * * cd /home/shg/blade-book && python3 scripts/purge_drafts.py >> /var/log/blade-book/purge.log 2>&1`
   `*/5 * * * * /usr/bin/python3 /home/shg/blade-book/scripts/publish_sweep.py >> /var/log/blade-book/publish.log 2>&1`
   `*/15 * * * * cd /home/shg/blade-book && python3 scripts/match_cron.py >> /var/log/blade-book/match.log 2>&1`
   `*/5 * * * * cd /home/shg/blade-book && python3 /home/shg/blade-book/scripts/monitor.py >> /var/log/blade-book/monitor.log 2>&1`
   Confirm `/home/backup/blade-book-*.tgz` appears on new, check `purge.log` the following morning, monitor
   `publish.log` for publish_sweep activity, monitor `match.log` for match_cron activity (silent unless
   it sends), and check `monitor.log`/mail for `scripts/monitor.py` (needs `BLADEBOOK_ADMIN_EMAIL`, §ENV.md,
   plus billboard's Twilio env for outage SMS).
   Backups (`scripts/backup.sh`, 03:30 nightly) hold `blade-book/photos/…` plus a top-level
   `blade-book.db` snapshot taken with `sqlite3 .backup` (consistent; the live db is never in the
   tar). Restore: `mkdir -p /tmp/r && tar xzf blade-book-<stamp>.tgz -C /tmp/r && rsync -a /tmp/r/blade-book/ /var/lib/blade-book/
   && rm -f /var/lib/blade-book/blade-book.db-wal /var/lib/blade-book/blade-book.db-shm && mv /tmp/r/blade-book.db /var/lib/blade-book/blade-book.db
   && chown shg:shg /var/lib/blade-book/blade-book.db && chmod 600 /var/lib/blade-book/blade-book.db`
   with `blade_book` stopped. The `-wal`/`-shm` removal matters: the app runs WAL mode, and a stale WAL
   replays over the restored file on first open. `exports/` and `publish-locks/` are not backed up
   (transient). Off-box = the Linode backup service (Simon, 2026-09-03).
   Run the restore as shg where possible; the app runs as shg and a root-owned db file breaks it. Same for
   `scripts/monitor.py`: never run it as root — a root-owned `monitor_state.json` disables the monitor.
8. The search index (`search_cards`/`search_fts`) is derived data, not a
   thing to restore/rsync — after any restore, repair/backfill it the same
   way as the bundles: `python3 scripts/publish_sweep.py --all` as shg.
   Also in the data dir but transient: `monitor_state.json` (`scripts/monitor.py`'s
   throttle/offset bookkeeping) — safe to drop on a move, it re-creates itself
   on the next `*/5` run.
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
