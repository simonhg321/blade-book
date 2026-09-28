# Admin activity — design

**Date:** 2026-09-27 · **Status:** approved to build and deploy (Simon, 2026-09-27 21:42 PDT: "yes deploy here https://blade-book.com/blade-book/admin/") · **Branch:** `admin-activity`

## Why

Simon wants to see who is using blade-book and what they are doing, so that when someone gets stuck he notices and we can help. Tonight that took a person reading three logs by hand: a collector opened one page at 21:01 PDT, and an invited collector's sign-in link from 09-26 was never clicked. The admin page should show both without anyone asking.

## Who it is for

Simon, signed in as an admin. Nobody else.

## What Simon decided

| # | Question | Decision |
|---|---|---|
| 1 | How much do we record? | **Only what we already keep.** No new recording, no script, no beacon, no new table. |
| 2 | The gate | Behind the existing admin sign-in (`auth.admin_required`). No key in a link. |
| 3 | Where | On the admin page itself, https://blade-book.com/blade-book/admin/. |

## What success looks like

Simon opens the admin page and, with no help, sees:

- a collector's visit tonight, labelled as a best guess.
- an invited collector's unclicked sign-in link.
- a collector's unfinished draft K06 and the day it purges.

## The trust line

The terms and the FAQ say what we store and end "Nothing else." That was never quite true: the web server keeps a request log, and the app logs sign-ins and saves. This feature reads those logs, so the pages say so.

**`html/terms/index.html`, "What we store"** becomes:

> Your email address, your handle, the knife records and photos you add, the notes you write, and a sign-in cookie. Like every web server, ours keeps a log of requests (address, browser, page, the page you came from, time) for two weeks. The app keeps its own log of what happens on your account: sign-ins (email and the address they came from), and what you add, change and delete. That log has no time limit yet. We read both to fix problems and to help people who get stuck. No ads, no trackers, no analytics scripts.

**`html/faq/index.html`, "What do you store?"** gets the same text.

The words "Nothing else." go. Everything after them stays true.

**"Delete is real"** said "We keep one thing" (the hash of the email). The logs keep the lines they already wrote, so it now says: "We keep a one-way hash of your email address, … The logs described below keep the lines they already wrote."

The first wording (Simon approved it 2026-09-27 22:09 PDT) left out the browser and the referrer, called the app log "sign-ins and saves", and gave two weeks for both logs. The review found each of those untrue, so the words changed. **Open for Simon:** give the app log a time limit, then say the limit here.

## Sources

All three exist today. The module reads them and writes nothing.

| Source | Path | What it gives |
|---|---|---|
| Database | `paths.db_path()` | accounts, last active (`sessions.last_seen`), drafts (`knives.status = 'draft'`) |
| App log | `LOG_DIR/app.log` and its rotations `app.log.1` … `.5` | what signed-in people did, by handle; sign-in links asked for and used; failed sign-ins; blocked saves; failed decodes |
| Web server log | `paths.access_log()`, its rotations `.1` and `.N.gz` | page views by network and browser |

`paths.access_log()` is new: `BLADEBOOK_ACCESS_LOG`, default `/var/log/apache2/blade-book_access.log`. The app runs as `shg`, which is in group `adm`, so it can read the file (checked on the box 2026-09-27: the gunicorn process has group 4).

Times: the database stores ISO times in UTC. The app log is in the box's local time, which is UTC. The web server log carries its own offset. Everything leaves the API as ISO UTC; the page shows the browser's local time.

The `magic_tokens` table is not a source. It purges itself after 24 hours (`db.purge_auth_tables`), so a link from last week would be gone. The app log keeps every request and every sign-in.

## Architecture

| Unit | Does | Depends on |
|---|---|---|
| `bb/activity.py` | Parses the two logs, reads the database through `bb/db.py`, returns one summary dict. Pure functions that take paths and a connection. | `bb/db.py`, `bb/paths.py` |
| `bb/db.py` (three new reads) | `activity_people`, `activity_drafts`, `knife_tags`. SQL stays in `db.py`, as everywhere else. | — |
| `bb/routes/admin.py` (one new route) | `GET /api/admin/activity?hours=48` behind `admin_required`. | `bb/activity.py` |
| `html/admin/index.html` | Three new empty sections above USERS, and a window switch. | — |
| `html/admin/activity.js` | Fetches the summary and draws the three sections. | the route |
| `scripts/deploy_activity.sh` | Copies the static pages, restarts the app, checks the route answers. | — |

## The API

`GET /blade-book/api/admin/activity?hours=48`

- `hours` is one of 48, 168, 336. Anything else is a 400.
- 401 signed out, 403 signed in and not an admin. Same as the other admin routes.
- `Cache-Control: no-store` comes from the existing rule for API JSON.

```json
{
  "hours": 48,
  "generated": "2026-09-28T04:40:00+00:00",
  "you": "simon-collector",
  "needs_help": [ {"kind": "link_unclicked", "who": "c…@example.com", "when": "…", "detail": "sign-in link sent, never opened"} ],
  "people":     [ {"handle": "riverstone", "email": "…", "last_active": "…", "knives": 3, "drafts": 1,
                   "trail": [ {"when": "…", "what": "saved K05"} ] } ],
  "visitors":   [ {"network": "2001:db8:4181:bdd0::/64", "guess": ["riverstone"], "device": "Windows · Firefox",
                   "came_from": "", "first": "…", "last": "…",
                   "pages": [ {"when": "…", "path": "/blade-book/abtesting/flow.html", "status": 200} ] } ],
  "hidden":     {"bots": 41, "requests": 312},
  "sources":    {"database": {"ok": true}, "app_log": {"ok": true, "lines": 902},
                 "access_log": {"ok": true, "lines": 4120, "files": 2}}
}
```

### NEEDS HELP — the stuck signals

Newest first. Each has `kind`, `who`, `when`, `detail`, and `tag` and `purge_at` where they apply.

| kind | Source | Rule |
|---|---|---|
| `link_unclicked` | app log | "magic link requested for E" in the window, more than 15 minutes old, with **no later sign-in for the same email**. `who` is the email, because the person may have no account yet. `detail` carries the count. When the link was opened in another browser and never confirmed ("opened unbound"), `detail` says so. |
| `password_failed` | app log | "password sign-in failed for @H" in the window with no later "password sign-in: @H". `detail` carries the count. |
| `rate_limited` | app log | "rate limited …" or "password sign-in rate limited …". |
| `save_blocked` | app log | "K save gated for @H", unless the same knife was saved later. The soft gate is not a stuck signal: that save went through. |
| `decode_failed` | app log | "decode failed for H/K: Error…", unless the same knife decoded later. `detail` is the error's class name only. |
| `upload_refused` | app log | "undecodable upload refused for @H: name". `detail` is the file's extension only. |
| `draft_unfinished` | `knives` | A draft untouched for an hour or more. `purge_at` is `updated` plus 7 days, the rule in `db.purge_stale_drafts`. |

`draft_unfinished` ignores the window: a draft is stuck until it is saved or purged.

### PEOPLE

One row per account, most recently active first. `last_active` is the newest `sessions.last_seen`, or empty. `trail` is what the app log says that handle did in the window, newest first, at most 50 lines:

| App log line | `what` |
|---|---|
| `magic link sign-in: E (@H)`, `… (confirmed) …`, `password sign-in: @H`, `P sign-in: E (@H)` | signed in |
| `draft K created for @H` | started draft K |
| `photo ID/SEQ stored for @H` | added photo SEQ to K (the id becomes a tag when the knife still exists) |
| `decoded K for @H …` | decoded K |
| `K edited by @H: fields` | edited K: fields |
| `K saved to the register by @H` | saved K |
| `K sale_status → S by @H` | K → S |
| `K deleted by @H`, `draft K deleted by @H` | deleted K |
| `settings changed for @H: [fields]` | changed settings: fields |

The knife history table (`events`) is not used: it repeats the app log and loses its rows when a knife is deleted.

### VISITORS

Built from the web server log.

1. **Parse** each line of the combined format. A line that does not parse is counted and skipped.
2. **Cut the address to a network:** an IPv4 address as it is; an IPv6 address to its `/64`.
3. **Drop the query string** from every path, always, and the same when it arrives encoded (`%3F`, `;`, `%23`). Sign-in links and upload keys travel in query strings.
4. **Group** by network and browser.
5. **Keep people, hide bots.** A request whose browser string looks like a bot is hidden. A group is a person when it fetched at least one asset (stylesheet, script, font, image) or made at least one API `GET` that names one of our own pages as its referrer, **and** it has at least one page that answered 2xx or 3xx. Scanners do not run scripts or fetch stylesheets; browsers do. Everything else is counted in `hidden` and not listed.
6. **Pages** are requests for a path that ends in `/` or `.html`, or has no extension, outside `/api/`. At most 30 per visitor, newest first.
7. **Device** is two words from the browser string: "iPhone · Facebook app", "Windows · Firefox", "Mac · Chrome".
8. **Came from** is the host of the first outside referrer, or empty. Our own hosts are `BASE_URL`'s host and the host of the request.
9. **Guess.** A network gets a handle when the app log shows that network signing in as that account. A password sign-in names its address. A sign-in by link does not: a confirmed one belongs to the address that opened the link; a plain one belongs to the address that asked for it within the 20 minutes before, and only when every request for that email in those 20 minutes came from one network. A failed password and an unused link prove nothing and do not count. A handle with no account today is never named. The whole of the app log counts, not only the window. Two handles on one network gives both. The page says "probably @riverstone".
10. **You.** A visitor whose guess includes the signed-in admin is marked `you: true`. The page folds it shut, so Simon's own clicks do not bury everyone else's.

## One record, one line

User text lands in the app log: a want's text, a file name, a report's reason. With a line break in it, the rest would read as a record of its own, and this view would believe a forged sign-in. So the app's log formatter (`app.py`, `_OneRecordPerLine`) indents every line after a record's first, tracebacks included. Only a real record starts a line with a stamp.

A handle that changed keeps its history: lines older than "handle changed: @old -> @new" that name `@old` count for `@new`.

## What never leaves the server

- **No raw log line.** The parsers match known shapes and return named fields. An unknown line is dropped.
- **No query string.**
- **No token, no password, no session value.** None of the matched shapes carries one, and the tests plant tokens in the sample logs to prove it.

## The page

Order on `/admin/`: a window switch (48 hours · 7 days · 14 days), NEEDS HELP, PEOPLE, VISITORS, then USERS and REPORTS as they are today.

- NEEDS HELP: one card per signal. Who, what, when. A draft shows its purge date.
- PEOPLE: one row per account. A row opens to show the trail.
- VISITORS: one row per visitor, with the guess, the device and where they came from. A row opens to show the pages.
- An empty section says so: "nobody is stuck".
- A source that failed shows its real error in the section it feeds, for example "could not read /var/log/apache2/blade-book_access.log: Permission denied". The other sections still draw.
- Times show as "12 min ago", with the full local time on hover.
- The page is 640 px wide today and stays that way. It works on a phone.

`activity.js` is a file, not an inline script, so the page stays small. It is loaded with `?v=20260928`.

## Errors

| What goes wrong | What happens |
|---|---|
| The web server log cannot be read | `sources.access_log.ok` is false with the error text; `visitors` is empty; the rest is filled |
| The app log cannot be read | the same for `sources.app_log`; the database signals still show |
| A rotated `.gz` file is cut short | that file's readable lines count; the error is reported in `sources` |
| The logs are huge | the newest 100,000 lines of each log are read, and `sources` says older ones were left out |
| A line is longer than 4,096 characters, or its date is no date | the line is skipped |

## Testing

Tests first.

- `tests/test_activity.py`: each parser against sample lines; each stuck signal, with the case that clears it (a later good sign-in, a later used link); the bot rule; the IPv6 cut; the guess; `you`; query strings gone; planted tokens absent from the whole summary; a missing log file reports its error.
- `tests/test_admin_activity_api.py`: 401 and 403; a bad `hours` is a 400; the shape; no secret bytes in the response.
- `tests/test_deploy_files.py`: the admin page has the three sections and loads `activity.js`; the terms and the FAQ carry the new sentence and no longer say "Nothing else."
- By eye: screenshots at desktop and phone width, against a copy of the database and copies of the logs, never the live files.

## Going live

`scripts/deploy_activity.sh`, run by Simon with sudo: copy `html/` to the live folder, restart the app, wait for `healthz`, check that the new route answers 401 to a signed-out request and that the terms page carries the new sentence. No schema change. No publish sweep: `publish.py` does not change.

## Left out on purpose

- Alerts. The page shows; it does not text or email.
- A page trail by name (option B). It needs new recording and a change to the terms. It can be added later without redoing this.
- A beacon for visitors (option C).
- Charts and counts over time.
- A cache. One admin, one page, a parse that takes well under a second at today's size.

## Limits Simon should know

- A page Cloudflare serves from its own cache never reaches the web server log.
- The web server log holds 14 days.
- A guess is a guess: two people behind one home network look like one.
