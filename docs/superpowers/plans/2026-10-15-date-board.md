# The Date Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** blade-book leads with "is there a knife born on your date?": a public date search over every public knife, "ask the owner" on any hit, and a standing want on a miss.

**Architecture:** A new pure module `bb/born.py` parses dates and ranks knives into exact / same_day / near. A new route `GET /api/born` queries `knives JOIN users` directly (real knife ids). The board contact route is generalised from "for-sale knife" to "public knife". Wants gain a `born_day` (`MM-DD`) criterion that matches any sale status. The board page is re-laid out around the search; the landing hero gets a plain GET form that lands on the board.

**Tech Stack:** Python 3 / Flask / sqlite3 (no ORM), pytest, vanilla JS static pages served by Apache, gunicorn under supervisor.

**Spec:** `docs/superpowers/specs/2026-10-15-date-board-design.md`

## Global Constraints

- Tests run from the repo root with `python3 -m pytest -q` (816 pass today; this plan must leave every one of them green).
- Any non-pytest python that touches the app MUST export `BLADEBOOK_DATA_DIR`, `BLADEBOOK_CONFIG_DIR`, `BLADEBOOK_LOG_DIR`, `BLADEBOOK_WWW_DIR` to scratch directories first. Never run app code against `/var/lib/blade-book` from a shell.
- No `innerHTML` and no Google Fonts in any page (existing deploy-files tests enforce it).
- Every new `.py` and `.html` file starts with the licence line: `# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)` (HTML: the same text inside an HTML comment, after `<!doctype html>`).
- The repo is PUBLIC. No owner names, emails, tokens or internal IPs in code, tests, fixtures or commit messages.
- Dates in the DB are `YYYY-MM-DD` text in `knives.born_on`, with `knives.born_on_precision` in `('day','month','year')`.
- API prefix is `/blade-book/api`; page prefix is `/blade-book`.
- Copy rules from the spec: board heading `BORN ON YOUR DATE`; button `FIND IT`; group headings "Born on {label}", "Born on {month day}, other years", "Born within a week of {month day}"; want button "tell me when one is born on {M/D}"; non-listed cards say `not listed`; contact button says `contact seller` on a for-sale card and `ask @handle` otherwise.
- Commit after every task, with the attribution trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf
  ```
- Do not push; the whole branch is reviewed and pushed at the end.

## Review Focus

1. `d=2/29` with no year: must parse, must rank 2/29 knives as `same_day`, and 3/1 as one day after. Pinned in Task 1 and Task 3.
2. A query on 12/30 or 1/2: the ±7 window must wrap the year end (12/30 is 3 days before 1/2). Pinned in Task 1 (`window`) and Task 3 (ranking).
3. A knife whose owner later turns on `hide_born_day`: it must vanish from `/api/born` and from `born_day` matching without any republish. Pinned in Task 3 and Task 6.
4. A signed-out person clicking `ask @handle`: must land on sign-in and, after the magic link, come back to the exact board URL. Only `/blade-book/...` paths are honoured; `//evil` and `https://` are dropped. Pinned in Task 9.
5. A `born_day` want plus a `max_price`: the mode gate is skipped, but the price gate must still only apply to for-sale knives (a keeping knife has no price and must still match). Pinned in Task 6.

## File Structure

| File | Responsibility |
|---|---|
| `bb/born.py` (new) | `parse(text)`, `window(month, day)`, `rank(query, rows)`, `label(...)`, `url_form(...)`. Pure, no DB. |
| `bb/routes/born.py` (new) | `GET /blade-book/api/born` — parse, query, rank, card. |
| `bb/db.py` | schema v15 (`wants.born_day`), `WANT_FIELDS`/`WANT_CRITERIA`, `create_want` validation, `public_knife()`, `date_board_knives()`. |
| `bb/match.py` | `knife_matches` honours `born_day`; `_emails_for` first line for born-day matches. |
| `bb/board.py` | `contact_emails` wording branches on `for_sale`. |
| `bb/routes/board.py` | contact uses `db.public_knife`. |
| `app.py` | registers the born blueprint. |
| `html/board/index.html` | the date board page. |
| `html/index.html` | hero leads with the date form; sign-in return via `next`. |
| `html/me/wants/index.html` | wants list summarises a `born_day` want. |
| `scripts/deploy_date_board.sh` (new) | copy static, restart, health check. |
| `tests/test_born_parse.py`, `tests/test_born_api.py` (new); `tests/test_board_api.py`, `tests/test_board_db.py`, `tests/test_match.py`, `tests/test_wants_db.py`, `tests/test_wants_api.py`, `tests/test_deploy_files.py` (modified). |

---

### Task 1: `bb/born.py` — parse, window, label

**Files:**
- Create: `bb/born.py`
- Test: `tests/test_born_parse.py`

**Interfaces:**
- Produces:
  - `class BornError(ValueError)`; message `'I need at least a month and a day'`.
  - `parse(text: str) -> tuple[int | None, int, int]` → `(year, month, day)`.
  - `window(month: int, day: int, days: int = 7) -> list[tuple[int, str]]` → `[(days_off, 'MM-DD'), ...]` for every offset in `-days..days`, computed in the leap year 2000 so 02-29 is included and the year end wraps.
  - `label(year, month, day) -> str` → `'July 28, 1999'` or `'July 28'`.
  - `url_form(year, month, day) -> str` → `'1999-07-28'` or `'07-28'`.
  - `short(month, day) -> str` → `'7/28'`.
  - `MAX_LEN = 32`, `WINDOW_DAYS = 7`.

- [ ] **Step 1: Write the failing tests**

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/born.py — date parsing, the ±7 day window, labels (date board spec §1)."""
import datetime as dt

import pytest

from bb import born


@pytest.mark.parametrize('text, expected', [
    ('7/28', (None, 7, 28)),
    ('7-28', (None, 7, 28)),
    ('07/28', (None, 7, 28)),
    (' 7 / 28 ', (None, 7, 28)),
    ('7/28/99', (1999, 7, 28)),
    ('7/28/1999', (1999, 7, 28)),
    ('07-28-1999', (1999, 7, 28)),
    ('11/6/21', (2021, 11, 6)),
    ('1999-07-28', (1999, 7, 28)),
    ('1999/7/28', (1999, 7, 28)),
    ('July 28', (None, 7, 28)),
    ('jul 28', (None, 7, 28)),
    ('July 28, 1999', (1999, 7, 28)),
    ('July 28 1999', (1999, 7, 28)),
    ('28 July 1999', (1999, 7, 28)),
    ('28 jul', (None, 7, 28)),
    ('2/29', (None, 2, 29)),
    ('2/29/2000', (2000, 2, 29)),
])
def test_parse_accepts(text, expected):
    assert born.parse(text) == expected


def test_two_digit_year_pivots_on_the_current_year():
    this_year = dt.date.today().year
    yy = this_year % 100
    assert born.parse(f'7/28/{yy:02d}')[0] == this_year               # this year: 2000s
    assert born.parse(f'7/28/{(yy + 1) % 100:02d}')[0] == 1900 + (yy + 1) % 100   # next year: 1900s


@pytest.mark.parametrize('text', [
    '', '   ', '7', 'abc', '13/40', '0/10', '7/0', '7/32', '2/30', '4/31',
    '7/28/1800', '7/28/2101', '1800-07-28', 'Smarch 1', 'July', '7/28/999',
    'x' * 33, '7/28/1999/1', '7//28',
])
def test_parse_rejects(text):
    with pytest.raises(born.BornError) as e:
        born.parse(text)
    assert str(e.value) == 'I need at least a month and a day'


def test_parse_is_a_value_error():
    assert issubclass(born.BornError, ValueError)


def test_window_is_fifteen_days_centred_and_wraps_the_year():
    w = born.window(1, 2)
    assert len(w) == 15
    assert w[0] == (-7, '12-26') and w[7] == (0, '01-02') and w[-1] == (7, '01-09')
    assert ('-3', '12-30') not in w and (-3, '12-30') in w


def test_window_includes_leap_day():
    assert (1, '02-29') in born.window(2, 28)
    assert (1, '03-01') in born.window(2, 29)
    assert (-1, '02-28') in born.window(2, 29)


def test_window_offsets_are_unique_month_days():
    mds = [md for _, md in born.window(7, 28)]
    assert len(set(mds)) == 15


def test_labels():
    assert born.label(1999, 7, 28) == 'July 28, 1999'
    assert born.label(None, 7, 28) == 'July 28'
    assert born.url_form(1999, 7, 28) == '1999-07-28'
    assert born.url_form(None, 7, 28) == '07-28'
    assert born.short(7, 28) == '7/28'
    assert born.short(11, 6) == '11/6'
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest -q tests/test_born_parse.py`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'bb.born'`

- [ ] **Step 3: Write `bb/born.py`**

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/born.py — the date board's date logic (spec §1, §2). Pure: no DB, no Flask.

parse()  : free text → (year | None, month, day). Year optional. Two-digit years
           go to the 2000s unless that lands after this year (99 → 1999, 21 → 2021).
window() : the ±7 day neighbourhood of a month-day, computed in the leap year
           2000 so 02-29 is a real day and the year end wraps (12-30 is 3 days
           before 01-02).
rank()   : see Task 3 — added there.
"""
import datetime as dt
import re

MAX_LEN = 32
WINDOW_DAYS = 7
YEAR_MIN, YEAR_MAX = 1900, 2100
MESSAGE = 'I need at least a month and a day'

_MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
           'august', 'september', 'october', 'november', 'december']
MONTH_NAMES = [m.capitalize() for m in _MONTHS]

_NUMERIC_MDY = re.compile(r'^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?$')
_NUMERIC_YMD = re.compile(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$')
_NAME_FIRST = re.compile(r'^([a-z]+)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?$')
_DAY_FIRST = re.compile(r'^(\d{1,2})\s+([a-z]+)\.?(?:,?\s+(\d{4}))?$')


class BornError(ValueError):
    pass


def _month_from_name(name):
    if len(name) < 3:
        return None
    for i, m in enumerate(_MONTHS):
        if m.startswith(name):
            return i + 1
    return None


def _year(raw):
    if raw is None:
        return None
    n = int(raw)
    if len(raw) == 2:
        this_year = dt.date.today().year
        n = 2000 + n if 2000 + n <= this_year else 1900 + n
    if not YEAR_MIN <= n <= YEAR_MAX:
        raise BornError(MESSAGE)
    return n


def _check(month, day):
    try:
        dt.date(2000, month, day)          # leap year: 2/29 is legal with no year
    except ValueError:
        raise BornError(MESSAGE) from None


def parse(text):
    """(year | None, month, day) or BornError. See module doc for the formats."""
    s = re.sub(r'\s+', ' ', (text or '').strip().lower())
    s = re.sub(r'\s*([/-])\s*', r'\1', s)       # '7 / 28' → '7/28'
    if not s or len(s) > MAX_LEN:
        raise BornError(MESSAGE)
    m = _NUMERIC_MDY.match(s)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), _year(m.group(3))
    else:
        m = _NUMERIC_YMD.match(s)
        if m:
            year, month, day = _year(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = _NAME_FIRST.match(s)
            if m:
                month, day, year = _month_from_name(m.group(1)), int(m.group(2)), _year(m.group(3))
            else:
                m = _DAY_FIRST.match(s)
                if not m:
                    raise BornError(MESSAGE)
                day, month, year = int(m.group(1)), _month_from_name(m.group(2)), _year(m.group(3))
    if month is None:
        raise BornError(MESSAGE)
    _check(month, day)
    if year is not None and month == 2 and day == 29:
        try:
            dt.date(year, 2, 29)
        except ValueError:
            raise BornError(MESSAGE) from None
    return year, month, day


def window(month, day, days=WINDOW_DAYS):
    """[(days_off, 'MM-DD'), ...] for days_off in -days..days, leap-year calendar."""
    centre = dt.date(2000, month, day)
    out = []
    for off in range(-days, days + 1):
        d = centre + dt.timedelta(days=off)
        out.append((off, f'{d.month:02d}-{d.day:02d}'))
    return out


def label(year, month, day):
    base = f'{MONTH_NAMES[month - 1]} {day}'
    return f'{base}, {year}' if year else base


def url_form(year, month, day):
    md = f'{month:02d}-{day:02d}'
    return f'{year}-{md}' if year else md


def short(month, day):
    return f'{month}/{day}'
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q tests/test_born_parse.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bb/born.py tests/test_born_parse.py
git commit -m "born: parse a birthday in any common form, the ±7 day window, labels (date board 1/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 2: `db.date_board_knives` and `db.public_knife`

**Files:**
- Modify: `bb/db.py` (after `board_knife`, around line 1053)
- Test: `tests/test_board_db.py` (append)

**Interfaces:**
- Produces:
  - `db.date_board_knives(con, month_days: list[str]) -> list[dict]` — every visible knife whose `born_on[5:]` is in `month_days`, as `_knife_row` dicts carrying `owner_handle`, `owner_email`, `owner_share_email`, `owner_hide_born_day` (the same extra columns as `_BOARD_SELECT`). Order: `k.id`.
  - `db.public_knife(con, knife_id) -> dict | None` — one knife by id under the public predicate (board predicate minus the for-sale clause; owner eligibility gate NOT applied). Same extra columns.
  - `db._PUBLIC_WHERE` string constant (the shared predicate).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_board_db.py`)

```python
def test_public_knife_is_any_visible_public_knife(con):
    u = _mk_user(con, email='p@example.com', handle='p-guy')
    keeping = _mk_knife(con, u['id'])
    assert db.public_knife(con, keeping['id'])['id'] == keeping['id']
    assert db.public_knife(con, keeping['id'])['owner_handle'] == 'p-guy'
    private = _mk_knife(con, u['id'], is_public=0)
    assert db.public_knife(con, private['id']) is None
    hidden = _mk_knife(con, u['id'], hidden_at=db.now())
    assert db.public_knife(con, hidden['id']) is None
    draft = db.create_draft_knife(con, u['id'])
    assert db.public_knife(con, draft['id']) is None
    assert db.public_knife(con, 999999) is None
    # a private profile or a key-gated register hides every knife
    con.execute('UPDATE users SET profile_private = 1 WHERE id = ?', (u['id'],)); con.commit()
    assert db.public_knife(con, keeping['id']) is None
    con.execute("UPDATE users SET profile_private = 0, public_key = 'k' WHERE id = ?", (u['id'],)); con.commit()
    assert db.public_knife(con, keeping['id']) is None


def test_public_knife_ignores_the_board_owner_gate(con):
    """A fresh, unverified account's public knife is still a public knife (spec §2)."""
    u = _mk_user(con, email='new@example.com', handle='new-guy')      # verified_at NULL, knife created now
    k = _mk_knife(con, u['id'])
    assert db.board_knife(con, k['id']) is None                        # not for sale, owner not eligible
    assert db.public_knife(con, k['id'])['id'] == k['id']


def test_date_board_knives_filters_by_month_day_and_visibility(con):
    u = _mk_user(con, email='d@example.com', handle='d-guy')
    a = _mk_knife(con, u['id'], born='2022-07-28')
    b = _mk_knife(con, u['id'], born='1999-07-28', sale_status='for_sale', asking_price=500)
    _mk_knife(con, u['id'], born='2022-08-01')                                     # outside the list
    _mk_knife(con, u['id'], born='2022-07-28', is_public=0)                        # private
    _mk_knife(con, u['id'], born='2022-07-28', hidden_at=db.now())                 # hidden
    month_only = _mk_knife(con, u['id'], born='2022-07')
    con.execute("UPDATE knives SET born_on_precision = 'month' WHERE id = ?", (month_only['id'],))
    con.commit()
    rows = db.date_board_knives(con, ['07-28', '07-29'])
    assert [r['id'] for r in rows] == [a['id'], b['id']]
    assert rows[0]['owner_handle'] == 'd-guy' and 'owner_email' in rows[0]
    # owner hides the day → every knife of theirs leaves the date board
    con.execute('UPDATE users SET hide_born_day = 1 WHERE id = ?', (u['id'],)); con.commit()
    assert db.date_board_knives(con, ['07-28']) == []


def test_date_board_knives_requires_a_full_date(con):
    u = _mk_user(con, email='f@example.com', handle='f-guy')
    k = _mk_knife(con, u['id'], born='2022-07-28')
    con.execute("UPDATE knives SET born_on = '2022-07' WHERE id = ?", (k['id'],)); con.commit()   # precision still 'day'
    assert db.date_board_knives(con, ['07-28', '07']) == []
    assert db.date_board_knives(con, []) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest -q tests/test_board_db.py -k "public_knife or date_board"`
Expected: FAIL with `AttributeError: module 'bb.db' has no attribute 'public_knife'`

- [ ] **Step 3: Add the predicate and the two functions to `bb/db.py`** right after `board_knife`

```python
# The date board (spec 2026-10-15 §2): any PUBLIC knife, for sale or not. The
# board predicate minus the for-sale clause and minus the owner eligibility
# gate — a hit is not a listing; the abuse limits sit on the asker's side.
_PUBLIC_WHERE = ("k.status = 'live' AND k.is_public = 1 AND k.hidden_at IS NULL "
                 "AND u.profile_private = 0 AND (u.public_key IS NULL OR trim(u.public_key) = '')")
_PUBLIC_SELECT = ("SELECT k.*, u.handle AS owner_handle, u.email AS owner_email, "
                  "u.share_email_on_intro AS owner_share_email, "
                  "u.hide_born_day AS owner_hide_born_day "
                  "FROM knives k JOIN users u ON u.id = k.owner_id WHERE " + _PUBLIC_WHERE)
# day-precision only: month-only knives and owners who hide the day are not on the date board
_DATED = ("k.born_on_precision = 'day' AND u.hide_born_day = 0 "
          "AND k.born_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'")


def public_knife(con, knife_id):
    """One public knife by id (any sale status), or None when it is not visible
    for ANY reason (private, hidden, draft, private/gated owner, no such id)."""
    return _knife_row(con, con.execute(_PUBLIC_SELECT + ' AND k.id = ?', (knife_id,)).fetchone())


def date_board_knives(con, month_days):
    """Every visible, day-dated knife whose month-day is in `month_days`
    (['07-28', ...]). Ranking happens in bb/born.rank(); this is the candidate pull."""
    mds = [md for md in month_days if re.fullmatch(r'\d{2}-\d{2}', md or '')]
    if not mds:
        return []
    ph = ','.join('?' * len(mds))
    return [_knife_row(con, r) for r in con.execute(
        _PUBLIC_SELECT + f' AND {_DATED} AND substr(k.born_on, 6) IN ({ph}) ORDER BY k.id', mds)]
```

Check `import re` is already at the top of `bb/db.py` (it is used by `create_want`); if not, add it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest -q tests/test_board_db.py`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add bb/db.py tests/test_board_db.py
git commit -m "db: public_knife + date_board_knives — the date board's candidate pull (date board 2/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 3: `born.rank` and `GET /api/born`

**Files:**
- Modify: `bb/born.py` (append `rank`)
- Create: `bb/routes/born.py`
- Modify: `app.py:187-196` (register the blueprint)
- Test: `tests/test_born_parse.py` (append rank tests), `tests/test_born_api.py` (new)

**Interfaces:**
- Consumes: `born.parse/window/label/url_form/short` (Task 1); `db.date_board_knives` (Task 2); `board.card(k)` (existing, `bb/board.py:24`).
- Produces:
  - `born.rank(year, month, day, rows) -> dict` with keys `exact`, `same_day`, `near`, each a list of `(days_off, row)`; a row appears once; each list capped at `born.GROUP_CAP = 24`.
  - Route `GET /blade-book/api/born?d=<text>` → 200 `{"query": {"year","month","day","label","url","short"}, "exact": [card...], "same_day": [card...], "near": [card...]}`; each card is `board.card(k)` plus `days_off` (int). 400 `{"error": "I need at least a month and a day"}` when `d` is missing or bad.

- [ ] **Step 1: Write the failing rank tests** (append to `tests/test_born_parse.py`)

```python
def _row(i, born):
    return {'id': i, 'born_on': born}


def test_rank_three_groups_one_row_each_closest_first():
    rows = [_row(1, '1999-07-28'), _row(2, '2022-07-28'), _row(3, '2010-07-28'),
            _row(4, '2020-07-31'), _row(5, '2021-07-25'), _row(6, '2019-07-30'),
            _row(7, '2022-08-05'), _row(8, '2018-07-20')]
    r = born.rank(1999, 7, 28, rows)
    assert [x['id'] for _, x in r['exact']] == [1]
    assert [x['id'] for _, x in r['same_day']] == [2, 3]                 # most recent year first
    assert [(off, x['id']) for off, x in r['near']] == [(2, 6), (3, 4), (-3, 5)]   # |off| asc, then year desc
    # 8/05 is +8 and 7/20 is -8: outside the week
    ids = [x['id'] for g in r.values() for _, x in g]
    assert 7 not in ids and 8 not in ids and len(ids) == len(set(ids))


def test_rank_without_a_year_has_no_exact_group():
    rows = [_row(1, '1999-07-28'), _row(2, '2022-07-28')]
    r = born.rank(None, 7, 28, rows)
    assert r['exact'] == [] and [x['id'] for _, x in r['same_day']] == [2, 1]


def test_rank_wraps_the_year_end_and_handles_leap_day():
    r = born.rank(None, 1, 2, [_row(1, '2021-12-30'), _row(2, '2020-01-05')])
    assert [(off, x['id']) for off, x in r['near']] == [(-3, 1), (3, 2)]     # same distance: newest year first
    r = born.rank(None, 2, 29, [_row(1, '2020-02-29'), _row(2, '2021-03-01'), _row(3, '2019-02-28')])
    assert [x['id'] for _, x in r['same_day']] == [1]
    assert [(off, x['id']) for off, x in r['near']] == [(1, 2), (-1, 3)]


def test_rank_caps_each_group():
    rows = [_row(i, f'{1950 + i}-07-28') for i in range(40)]
    r = born.rank(None, 7, 28, rows)
    assert len(r['same_day']) == born.GROUP_CAP == 24
    assert r['same_day'][0][1]['id'] == 39                                 # newest years survive the cap


def test_rank_ignores_rows_without_a_full_date():
    r = born.rank(None, 7, 28, [_row(1, '2022-07'), _row(2, ''), _row(3, None), _row(4, '2022-07-28')])
    assert [x['id'] for _, x in r['same_day']] == [4]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_born_parse.py -k rank`
Expected: FAIL with `AttributeError: module 'bb.born' has no attribute 'rank'`

- [ ] **Step 3: Append `rank` to `bb/born.py`**

```python
GROUP_CAP = 24
_FULL = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')


def rank(year, month, day, rows):
    """Split candidate rows (dicts with 'born_on') into exact / same_day / near.
    Each value is a list of (days_off, row). A row lands in one group only.
    exact: y-m-d equal (empty when no year). same_day: m-d equal, newest year
    first. near: within WINDOW_DAYS by the leap-year calendar, |days_off| asc
    then newest year, then days_off (after before before). Caps at GROUP_CAP."""
    offsets = {md: off for off, md in window(month, day)}
    exact, same, near = [], [], []
    for r in rows:
        m = _FULL.match(r.get('born_on') or '')
        if not m:
            continue
        y, md = int(m.group(1)), f'{m.group(2)}-{m.group(3)}'
        if md not in offsets:
            continue
        off = offsets[md]
        if off == 0:
            if year is not None and y == year:
                exact.append((0, r, y))
            else:
                same.append((0, r, y))
        else:
            near.append((off, r, y))
    exact.sort(key=lambda t: t[1].get('id') or 0)
    same.sort(key=lambda t: (-t[2], t[1].get('id') or 0))
    near.sort(key=lambda t: (abs(t[0]), -t[2], -t[0], t[1].get('id') or 0))
    return {'exact': [(o, r) for o, r, _ in exact[:GROUP_CAP]],
            'same_day': [(o, r) for o, r, _ in same[:GROUP_CAP]],
            'near': [(o, r) for o, r, _ in near[:GROUP_CAP]]}
```

- [ ] **Step 4: Run the rank tests to verify they pass**

Run: `python3 -m pytest -q tests/test_born_parse.py`
Expected: all PASS

- [ ] **Step 5: Write the failing API tests** — create `tests/test_born_api.py`

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""GET /blade-book/api/born — the date board (spec 2026-10-15 §2)."""
from bb import db
from tests.test_search import _mk_knife, _mk_user

N = '/blade-book/api/born'


def _seed(con):
    u = _mk_user(con, email='d@example.com', handle='d-guy')
    exact = _mk_knife(con, u['id'], born='1999-07-28')
    same = _mk_knife(con, u['id'], born='2022-07-28', sale_status='for_sale', asking_price=650)
    near = _mk_knife(con, u['id'], born='2020-07-31')
    far = _mk_knife(con, u['id'], born='2020-08-10')
    return u, exact, same, near, far


def test_born_groups_and_card_shape(client, con):
    u, exact, same, near, far = _seed(con)
    r = client.get(N + '?d=7/28/99')
    assert r.status_code == 200
    j = r.get_json()
    assert j['query'] == {'year': 1999, 'month': 7, 'day': 28, 'label': 'July 28, 1999', 'url': '1999-07-28', 'short': '7/28'}
    assert [k['id'] for k in j['exact']] == [exact['id']]
    assert [k['id'] for k in j['same_day']] == [same['id']]
    assert [k['id'] for k in j['near']] == [near['id']] and j['near'][0]['days_off'] == 3
    c = j['same_day'][0]
    assert c['handle'] == 'd-guy' and c['tag'] == same['tag'] and c['born_on'] == '2022-07-28'
    assert c['for_sale'] == 1 and c['asking_price'] == 650 and c['days_off'] == 0
    assert j['exact'][0].get('for_sale', 0) == 0 and 'asking_price' not in j['exact'][0]
    for g in ('exact', 'same_day', 'near'):
        for card in j[g]:
            assert 'owner_email' not in card and 'email' not in card and 'price_paid' not in card


def test_born_without_year_and_public_access(client, con):
    _seed(con)
    j = client.get(N + '?d=July%2028').get_json()
    assert j['query']['year'] is None and j['query']['url'] == '07-28'
    assert j['exact'] == [] and len(j['same_day']) == 2 and len(j['near']) == 1
    assert [k['born_on'] for k in j['same_day']] == ['2022-07-28', '1999-07-28']


def test_born_bad_input_is_400(client):
    for q in ('', '?d=', '?d=abc', '?d=13/40', '?d=' + 'x' * 40):
        r = client.get(N + q)
        assert r.status_code == 400 and r.get_json() == {'error': 'I need at least a month and a day'}, q


def test_born_hides_what_the_board_hides(client, con):
    u = _mk_user(con, email='h@example.com', handle='h-guy')
    shown = _mk_knife(con, u['id'], born='2022-07-28')
    _mk_knife(con, u['id'], born='2022-07-28', is_public=0)
    _mk_knife(con, u['id'], born='2022-07-28', hidden_at=db.now())
    mo = _mk_knife(con, u['id'], born='2022-07')
    con.execute("UPDATE knives SET born_on_precision = 'month' WHERE id = ?", (mo['id'],)); con.commit()
    assert [k['id'] for k in client.get(N + '?d=7/28').get_json()['same_day']] == [shown['id']]
    con.execute('UPDATE users SET hide_born_day = 1 WHERE id = ?', (u['id'],)); con.commit()
    j = client.get(N + '?d=7/28').get_json()
    assert j['same_day'] == [] and j['near'] == []
    con.execute("UPDATE users SET hide_born_day = 0, profile_private = 1 WHERE id = ?", (u['id'],)); con.commit()
    assert client.get(N + '?d=7/28').get_json()['same_day'] == []


def test_born_fresh_account_knife_is_findable(client, con):
    """No board-eligibility gate on the date board (spec §2)."""
    u = _mk_user(con, email='n@example.com', handle='n-guy')          # unverified, knife created now
    k = _mk_knife(con, u['id'], born='2022-07-28')
    assert [c['id'] for c in client.get(N + '?d=07-28').get_json()['same_day']] == [k['id']]
```

- [ ] **Step 6: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_born_api.py`
Expected: FAIL — status 404 on every request (no route yet)

- [ ] **Step 7: Create `bb/routes/born.py`**

```python
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""GET /blade-book/api/born?d=<text> — the date board (spec 2026-10-15 §2).
Public, no session. Parse → candidate pull (db.date_board_knives) → rank →
board cards (bb/board.card strips everything private) plus days_off."""
import logging

from flask import Blueprint, jsonify, request

from bb import board, born, db, paths

bp = Blueprint('born', __name__, url_prefix=paths.API_PREFIX + '/born')
log = logging.getLogger('blade-book.born')


@bp.get('', strict_slashes=False)
def born_route():
    try:
        year, month, day = born.parse(request.args.get('d', ''))
    except born.BornError as e:
        return jsonify({'error': str(e)}), 400
    con = db.connect()
    try:
        rows = db.date_board_knives(con, [md for _, md in born.window(month, day)])
        groups = born.rank(year, month, day, rows)
        out = {g: [dict(board.card(k), days_off=off) for off, k in items] for g, items in groups.items()}
    finally:
        con.close()
    out['query'] = {'year': year, 'month': month, 'day': day,
                    'label': born.label(year, month, day),
                    'url': born.url_form(year, month, day),
                    'short': born.short(month, day)}
    return jsonify(out)
```

- [ ] **Step 8: Register the blueprint in `app.py`** — in the import line at `app.py:187` add `born as born_routes`, and after `app.register_blueprint(board_routes.bp)` add:

```python
    app.register_blueprint(born_routes.bp)
```

- [ ] **Step 9: Run the API tests and the whole suite**

Run: `python3 -m pytest -q tests/test_born_api.py && python3 -m pytest -q`
Expected: all PASS (816 + new)

- [ ] **Step 10: Commit**

```bash
git add bb/born.py bb/routes/born.py app.py tests/test_born_parse.py tests/test_born_api.py
git commit -m "api: GET /api/born — exact / same day / within a week, real knife ids (date board 3/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 4: Ask the owner — contact any public knife

**Files:**
- Modify: `bb/routes/board.py:66-67` (`db.board_knife` → `db.public_knife` in `contact_seller` only; the report route keeps `board_knife`)
- Modify: `bb/board.py:43-75` (`contact_emails` wording branch)
- Test: `tests/test_board_api.py` (append)

**Interfaces:**
- Consumes: `db.public_knife` (Task 2).
- Produces: unchanged route `POST /blade-book/api/board/<id>/contact`; `board.contact_emails(buyer, k, message)` returns the same two dicts, with "asked about" wording when `k['sale_status'] != 'for_sale'`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_board_api.py`)

```python
def test_contact_on_a_keeping_knife_asks_the_owner(client, mailer, con):
    """The date board: 'ask the owner' works on any public knife (spec §3)."""
    owner = _mk_user(con, email='keep@example.com', handle='keeper')
    k = _mk_knife(con, owner['id'], born='2022-07-28')                       # sale_status keeping, fresh account
    assert db.board_knife(con, k['id']) is None
    buyer = signed_in(client, mailer, email='asker@example.com')
    mailer.sent.clear()
    r = client.post(f"{B}/{k['id']}/contact", json={'message': 'Born on my son\'s birthday — would you sell?'})
    assert r.status_code == 200 and r.get_json() == {'ok': True}
    to_owner = next(m for m in mailer.sent if m['to'] == 'keep@example.com')
    to_buyer = next(m for m in mailer.sent if m['to'] == 'asker@example.com')
    assert to_owner['subject'] == f"blade-book: someone asked about your {k['tag']}"
    assert f"@{buyer['handle']} asked about your {k['tag']}" in to_owner['text']
    assert 'not listed for sale' in to_owner['text'] and 'date board' in to_owner['text']
    assert 'on the board' not in to_owner['text'] and 'interested' not in to_owner['subject']
    assert f"We asked @keeper about their {k['tag']}" in to_buyer['text'] and 'not listed for sale' in to_buyer['text']
    rows = [dict(r) for r in con.execute("SELECT * FROM intros WHERE kind = 'board'")]
    assert len(rows) == 1 and rows[0]['to_user'] == owner['id']


def test_contact_for_sale_wording_is_unchanged(client, mailer, con):
    s, k = _listing(con)
    signed_in(client, mailer, email='buyer@example.com')
    mailer.sent.clear()
    assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 200
    to_seller = next(m for m in mailer.sent if m['to'] == 's@example.com')
    assert 'someone on the board is interested in your' in to_seller['subject']
    assert 'not listed' not in to_seller['text']


def test_contact_still_refuses_private_and_hidden_knives(client, mailer, con):
    owner = _mk_user(con, email='pv@example.com', handle='pv-guy')
    private = _mk_knife(con, owner['id'], is_public=0)
    hidden = _mk_knife(con, owner['id'], hidden_at=db.now())
    signed_in(client, mailer, email='asker@example.com')
    assert client.post(f"{B}/{private['id']}/contact", json={}).status_code == 404
    assert client.post(f"{B}/{hidden['id']}/contact", json={}).status_code == 404


def test_contact_limits_apply_to_keeping_knives_too(client, mailer, con):
    from bb import board
    owner = _mk_user(con, email='lim@example.com', handle='lim-guy')
    ks = [_mk_knife(con, owner['id']) for _ in range(board.MAX_PER_SELLER_PER_DAY + 1)]
    signed_in(client, mailer, email='asker@example.com')
    for k in ks[:board.MAX_PER_SELLER_PER_DAY]:
        assert client.post(f"{B}/{k['id']}/contact", json={}).status_code == 200
    assert client.post(f"{B}/{ks[-1]['id']}/contact", json={}).status_code == 429
```

Add `from tests.test_search import _mk_knife, _mk_user` is already at the top of `tests/test_board_api.py` (line 6); confirm and reuse.

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_board_api.py -k "keeping or for_sale_wording or refuses_private or limits_apply"`
Expected: the keeping and limits tests FAIL with 404 (board_knife returns None for a keeping knife); the other two PASS already.

- [ ] **Step 3: Switch the contact route to `public_knife`** — in `bb/routes/board.py` `contact_seller`, change

```python
        k = db.board_knife(con, knife_id)
```
to
```python
        k = db.public_knife(con, knife_id)      # any public knife: the date board's "ask the owner"
```
Leave `report_listing` on `db.board_knife`.

- [ ] **Step 4: Branch the wording in `bb/board.py` `contact_emails`** — replace the function body's `to_seller` / `to_buyer` construction with:

```python
    url = _permalink(k)
    share = (bool(buyer.get('share_email_on_intro')) and bool(k.get('owner_share_email'))
             and mail.deliverable(buyer['email']) and mail.deliverable(k['owner_email']))
    text = match.card_text(k)
    listed = k.get('sale_status') == 'for_sale'
    if not listed:
        text = f'{text} · not listed for sale'
    msg_block = (f"Their message (written by @{buyer['handle']} on blade-book — "
                 f"we haven't checked it):\n{message}\n\n") if message else ''
    if listed:
        subject_owner = f"blade-book: someone on the board is interested in your {k['tag']}"
        lead_owner = f"@{buyer['handle']} is interested in your {k['tag']} on the board ({text})."
        subject_buyer = f"blade-book: we told @{k['owner_handle']} you're interested in {k['tag']}"
        lead_buyer = f"We let @{k['owner_handle']} know you're interested in their {k['tag']} ({text})."
    else:
        subject_owner = f"blade-book: someone asked about your {k['tag']}"
        lead_owner = (f"@{buyer['handle']} asked about your {k['tag']} ({text}). "
                      f"They found it on the date board.")
        subject_buyer = f"blade-book: we asked @{k['owner_handle']} about {k['tag']}"
        lead_buyer = f"We asked @{k['owner_handle']} about their {k['tag']} ({text})."
    to_seller = {
        'to': k['owner_email'],
        'subject': subject_owner,
        'text': (f"{lead_owner}\n{url}\n\n{msg_block}"
                 + ("Reply to this email to reach them.\n" if share else
                    "They haven't shared a contact address. If you both turn on email "
                    "sharing in blade-book, future intros will connect you directly.\n")),
        'reply_to': buyer['email'] if share else None,
    }
    my_block = f"Your message:\n{message}\n\n" if message else ''
    to_buyer = {
        'to': buyer['email'],
        'subject': subject_buyer,
        'text': (f"{lead_buyer}\n{url}\n\n{my_block}"
                 + ("Reply to this email to reach the seller.\n" if share else
                    "The seller hasn't shared a contact address. Watch their register — "
                    "and if you both turn on email sharing in blade-book, future intros "
                    "will connect you directly.\n")),
        'reply_to': k['owner_email'] if share else None,
    }
    return to_seller, to_buyer
```

- [ ] **Step 5: Run the board tests and the whole suite**

Run: `python3 -m pytest -q tests/test_board_api.py && python3 -m pytest -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add bb/board.py bb/routes/board.py tests/test_board_api.py
git commit -m "board: ask the owner — contact works on any public knife, 'asked about' wording when not listed (date board 4/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 5: Schema v15 — `wants.born_day` and its validation

**Files:**
- Modify: `bb/db.py:17` (`SCHEMA_VERSION = 15`), `bb/db.py:26-31` (`WANT_FIELDS`, `WANT_CRITERIA`), `bb/db.py:47-58` (`WANTS_DDL`), `MIGRATIONS` (add `15`), `create_want` (validation)
- Test: `tests/test_wants_db.py`, `tests/test_wants_api.py` (append)

**Interfaces:**
- Produces: `wants.born_day TEXT` nullable `MM-DD`; `db.create_want(con, uid, {'born_day': '07-28'})` succeeds on its own; invalid strings raise `ValueError('bad field')`; `WANT_FIELDS` and `WANT_CRITERIA` both contain `'born_day'`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_wants_db.py`:

```python
def test_schema_v15_born_day_column(con):
    assert 'born_day' in {r[1] for r in con.execute('PRAGMA table_info(wants)')}
    assert 'born_day' in db.WANT_FIELDS and 'born_day' in db.WANT_CRITERIA


def test_born_day_want_validation(con):
    u = _u(con)
    w = db.create_want(con, u['id'], {'born_day': '07-28'})          # on its own it says what is wanted
    assert w['born_day'] == '07-28' and w['mode'] == 'either'
    assert db.create_want(con, u['id'], {'born_day': '02-29'})['born_day'] == '02-29'
    for bad in ('7/28', '7-28', '13-01', '02-30', '00-10', '07-28-1999', 728, '', '  ', ['07-28']):
        with pytest.raises(ValueError) as e:
            db.create_want(con, u['id'], {'born_day': bad})
        assert str(e.value) == 'bad field', bad
```

Append to `tests/test_wants_api.py`:

```python
def test_born_day_want_over_the_api(client, mailer):
    signed_in(client, mailer)
    r = client.post(W, json={'born_day': '07-28'})
    assert r.status_code == 200 and r.get_json()['born_day'] == '07-28'
    assert client.get(W).get_json()['wants'][0]['born_day'] == '07-28'
    assert client.post(W, json={'born_day': '7/28'}).status_code == 400
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_wants_db.py tests/test_wants_api.py -k born_day`
Expected: FAIL (`born_day` not in `PRAGMA table_info`; `create_want` raises `bad field` for the valid value because the key is unknown)

- [ ] **Step 3: Schema + constants in `bb/db.py`**

1. `SCHEMA_VERSION = 15`
2. `WANT_FIELDS = (..., 'born_from', 'born_to', 'born_day', 'mode', 'max_price')`
3. `WANT_CRITERIA = (..., 'born_from', 'born_to', 'born_day')`
4. In `WANTS_DDL`, after `born_from INTEGER, born_to INTEGER,` add a line `born_day TEXT,`
5. In `MIGRATIONS`, after the `14:` entry add:

```python
    # 2026-10-15 date board: a want on a birthday (MM-DD), any sale status.
    # 'duplicate column' tolerated for old workers; fresh DBs get it from WANTS_DDL.
    15: ['ALTER TABLE wants ADD COLUMN born_day TEXT'],
```

- [ ] **Step 4: Validation in `create_want`** — after the `for field in ('born_from', 'born_to', 'max_price'):` block add:

```python
    if 'born_day' in fields and fields['born_day'] is not None:
        bd = fields['born_day']
        if not isinstance(bd, str) or not re.fullmatch(r'\d{2}-\d{2}', bd):
            raise ValueError('bad field')
        try:
            datetime(2000, int(bd[:2]), int(bd[3:]))       # leap year: 02-29 is a legal birthday
        except ValueError:
            raise ValueError('bad field') from None
```

and in the `_real(c)` helper make `born_day` count as a real criterion: change `if c.startswith('born'):` to `if c in ('born_from', 'born_to'):`. `datetime` and `re` are already imported in `bb/db.py`; confirm, otherwise import them.

- [ ] **Step 5: Run the want tests and the whole suite**

Run: `python3 -m pytest -q tests/test_wants_db.py tests/test_wants_api.py && python3 -m pytest -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add bb/db.py tests/test_wants_db.py tests/test_wants_api.py
git commit -m "db: schema v15 — wants.born_day (MM-DD), validated, a criterion on its own (date board 5/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 6: `born_day` matching and the intro's first line

**Files:**
- Modify: `bb/match.py:57-90` (`knife_matches`), `bb/match.py:108-137` (`_emails_for`)
- Test: `tests/test_match.py` (append)

**Interfaces:**
- Consumes: `wants.born_day` (Task 5); `k['owner_hide_born_day']` and `k['born_on_precision']` (already on `db.all_public_knives` rows).
- Produces: `match.knife_matches(want, k)` honours `born_day`; the wanter's email for a `born_day` match opens with `Born on your date, 7/28:`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_match.py`)

```python
def _bd_want(**over):
    base = {f: None for f in db.WANT_FIELDS} | {'mode': 'either', 'maker': 'crk'}
    return dict(base, **over)


def _k(**over):
    k = {'maker': 'crk', 'model': 'Sebenza', 'blade_shape': '', 'blade_steel': '',
         'born_on': '2022-07-28', 'born_on_precision': 'day', 'owner_hide_born_day': 0,
         'sale_status': 'keeping', 'asking_price': None,
         'ext': {'generation': '31', 'size': 'Small'}, 'notes_public': ''}
    return dict(k, **over)


def test_born_day_matches_any_sale_status_and_any_maker():
    w = _bd_want(born_day='07-28')
    assert match.knife_matches(w, _k())                                        # keeping
    assert match.knife_matches(w, _k(sale_status='for_trade'))
    assert match.knife_matches(w, _k(sale_status='for_sale', asking_price=900))
    assert match.knife_matches(w, _k(maker='spyderco'))                        # the date is the criterion
    assert not match.knife_matches(w, _k(born_on='2022-07-29'))
    assert not match.knife_matches(w, _k(born_on='2022-07', born_on_precision='month'))
    assert not match.knife_matches(w, _k(owner_hide_born_day=1))


def test_born_day_keeps_the_other_criteria_and_the_price_gate():
    assert not match.knife_matches(_bd_want(born_day='07-28', model='Inkosi'), _k())
    assert match.knife_matches(_bd_want(born_day='07-28', model='Sebenza'), _k())
    w = _bd_want(born_day='07-28', max_price=500)
    assert match.knife_matches(w, _k())                                        # keeping: no price to fail
    assert not match.knife_matches(w, _k(sale_status='for_sale', asking_price=900))
    assert match.knife_matches(w, _k(sale_status='for_sale', asking_price=400))


def test_ordinary_wants_still_need_a_listed_knife():
    assert not match.knife_matches(_bd_want(model='Sebenza'), _k())            # keeping, no born_day


def test_born_day_intro_email_says_so(con):
    wanter = _u(con)
    owner = _u(con, email='o2@example.com', handle='o2-guy', share_email_on_intro=1)
    _mk_knife(con, owner['id'], born='2022-07-28')                             # keeping, public
    db.create_want(con, wanter['id'], {'born_day': '07-28'})
    m = FakeMailer()
    assert match.run(con, m) == 2
    to_wanter = next(x for x in m.sent if x['to'] == 'w@example.com')
    assert to_wanter['text'].startswith('Born on your date, 7/28:')
    assert 'July 28, 2022' in to_wanter['text']
    to_owner = next(x for x in m.sent if x['to'] == 'o2@example.com')
    assert 'born on 7/28' in to_owner['text']
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_match.py -k born_day`
Expected: FAIL (the mode gate rejects a keeping knife; email has no first line)

- [ ] **Step 3: Teach `knife_matches` about `born_day`** — replace the top of the function through the mode gate with:

```python
def knife_matches(want, k):
    ext = k.get('ext') or {}
    born_day = want.get('born_day')
    for f in ENUM_FIELDS:
        if f == 'maker' and born_day:
            continue                                   # a birthday want is brand-blind
        if want.get(f) and (k.get(f) or '').lower() != want[f].lower():
            return False
    for f in EXT_ENUM_FIELDS:
        if want.get(f) and (ext.get(f) or '').lower() != want[f].lower():
            return False
    if born_day:
        # day precision, owner shows the day, month-day equal — any sale status
        if (k.get('born_on_precision') or 'day') != 'day' or k.get('owner_hide_born_day'):
            return False
        if (k.get('born_on') or '')[5:] != born_day:
            return False
    else:
        mode = want.get('mode') or 'either'
        ss = k.get('sale_status')
        if mode == 'trade' and ss != 'for_trade':
            return False
        if mode == 'sale' and ss != 'for_sale':
            return False
        if mode == 'either' and ss not in ('for_trade', 'for_sale'):
            return False
    ss = k.get('sale_status')
    if want.get('max_price') and ss == 'for_sale':
        if not k.get('asking_price') or k['asking_price'] > want['max_price']:
            return False
```

Keep the rest of the function (born_from / born_to / keyword) exactly as it is.

- [ ] **Step 4: First line in `_emails_for`** — in `bb/match.py` `_emails_for`, before `to_wanter = {`, add:

```python
    bd = want.get('born_day')
    bd_short = f"{int(bd[:2])}/{int(bd[3:])}" if bd else ''
```

Then replace the `'text':` value of `to_wanter` with:

```python
        'text': ((f"Born on your date, {bd_short}: a knife on @{k['owner_handle']}'s register.\n\n" if bd else
                  f"A knife matching your want is on @{k['owner_handle']}'s register:\n\n")
                 + f"{card_text(k)}\n{url}\n\n"
                 + ("Reply to this email to reach the owner.\n" if share else
                    "The owner hasn't shared a contact address. Watch their register — "
                    "and if you both turn on email sharing in blade-book, future intros "
                    "will connect you directly.\n")),
```
and the `'text':` value of `to_owner` with:
```python
        'text': (f"@{want['_wanter_handle']} has a want matching your {k['tag']} "
                 + (f"— born on {bd_short} — " if bd else '')
                 + f"({card_text(k, with_tag=False)}).\n{url}\n\n"
                 + ("Reply to this email to reach them.\n" if share else
                    "The wanter hasn't shared a contact address. If you both turn on "
                    "email sharing in blade-book, future intros will connect you "
                    "directly.\n")),
```

- [ ] **Step 5: Run the match tests and the whole suite**

Run: `python3 -m pytest -q tests/test_match.py && python3 -m pytest -q`
Expected: all PASS. If `test_intro_honors_owner_hide_born_day` or other existing match tests break, the mode gate was moved wrongly: ordinary wants must still require a listed knife.

- [ ] **Step 6: Commit**

```bash
git add bb/match.py tests/test_match.py
git commit -m "match: born_day wants fire on any public knife with that birthday; intro says 'born on your date' (date board 6/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 7: The wants page shows a `born_day` want

**Files:**
- Modify: `html/me/wants/index.html:141` (`summarize`)
- Test: `tests/test_deploy_files.py` (append)

**Interfaces:**
- Consumes: wants JSON now carries `born_day` (Task 5).

- [ ] **Step 1: Write the failing test** (append to `tests/test_deploy_files.py`)

```python
def test_wants_page_summarises_a_born_day_want():
    html = _read('html/me/wants/index.html')
    assert "w.born_day" in html and "'born on '" in html
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest -q tests/test_deploy_files.py -k born_day_want`
Expected: FAIL

- [ ] **Step 3: Edit `summarize` in `html/me/wants/index.html`** — right before the line `if (w.born_from || w.born_to) parts.push(...)` add:

```javascript
    if (w.born_day) parts.push('born on ' + Number(w.born_day.slice(0, 2)) + '/' + Number(w.born_day.slice(3)) + ', any year, for sale or not');
```

and change `parts.push(w.mode);` to `if (!w.born_day) parts.push(w.mode);`.

- [ ] **Step 4: Run the test**

Run: `python3 -m pytest -q tests/test_deploy_files.py -k born_day_want`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add html/me/wants/index.html tests/test_deploy_files.py
git commit -m "wants page: summarise a born-day want (date board 7/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 8: The board page becomes the date board

**Files:**
- Modify: `html/board/index.html` (whole page)
- Test: `tests/test_deploy_files.py:438-449` (`test_board_page_wiring`) and a new test

**Interfaces:**
- Consumes: `GET /api/born?d=` (Task 3), `POST /api/board/<id>/contact` on any public knife (Task 4), `POST /api/wants {born_day}` (Task 5).
- Produces: URL contract `/blade-book/board/?born=<text>`; after a successful search the URL is rewritten to `?born=<query.url>`; sign-out redirect target `/blade-book/?auth=required&next=<encoded current path+query>` (consumed in Task 9).

- [ ] **Step 1: Update `test_board_page_wiring` and add the new test** in `tests/test_deploy_files.py`

Replace the needle tuple in `test_board_page_wiring` with:

```python
    for needle in ('BORN ON YOUR DATE', "'/blade-book/api/board'", "'/blade-book/api/born'", "'/auth/me'",
                   'id="cards"', 'id="more"', 'id="tpl"', 'id="cdlg"', 'id="rdlg"', '/contact', '/report',
                   'contact seller', "'ask @' + k.handle", 'report', 'maxlength="500"', '429', '502',
                   'sign in to ask', 'bbmark', '/blade-book/mark.svg', 'href="/blade-book/vibe.css?v=20260927"',
                   'seller_note', 'asking_price', 'listed_at', "'/blade-book/@' + k.handle + '/img/' + k.img_t",
                   'id="bornform"', 'name="born"', 'FIND IT', 'id="results"', 'LATEST FOR SALE',
                   "'Born on '", "', other years'", "'Born within a week of '", 'days_off',
                   "'tell me when one is born on '", "'/blade-book/api/wants'", 'born_day',
                   'history.replaceState', "'?born='", 'not listed', "'&next='", 'encodeURIComponent'):
        assert needle in html, needle
    assert 'innerHTML' not in html and 'fonts.googleapis.com' not in html
    assert 'THE BOARD</h1>' not in html and 'newest first.</p>' not in html
    for rel in ('index.html', 'search/index.html'):
        assert '/blade-book/board/' in open(os.path.join(ROOT, 'html', rel)).read(), rel
```

Append:

```python
def test_board_page_order_search_then_results_then_feed():
    html = _read('html/board/index.html')
    assert html.index('id="bornform"') < html.index('id="results"') < html.index('LATEST FOR SALE') < html.index('id="cards"')
    assert 'og:description" content="Is there a knife born on your date?' in html
    assert "days after your date" in html and "days before your date" in html and "the day before your date" in html
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_deploy_files.py -k board_page`
Expected: FAIL on `BORN ON YOUR DATE`

- [ ] **Step 3: Rewrite `html/board/index.html`**

Keep the `<head>`, the `<style>` block, the `bb-top` nav, the two dialogs, the bottom `<a class="cta" href="/blade-book/me/">📖 LIST YOUR OWN — FROM YOUR REGISTER</a>` link, the footer and the `nav.js` script tag as they are, with these changes:

Head:
```html
<title>blade-book — born on your date</title>
<meta property="og:title" content="Born on your date — blade-book">
<meta property="og:description" content="Is there a knife born on your date? Search every registered collector's knives by birthday — for sale or not, you can ask the owner.">
```

Add to the `<style>` block:
```css
  #bornform{display:flex;gap:8px;margin:0 0 6px}
  #bornform input{flex:1;font:inherit;font-size:1.1rem;padding:.7rem .8rem;border:2px solid var(--ink);border-radius:10px;min-width:0}
  #bornform .btn{white-space:nowrap}
  .hint{text-align:center;color:var(--muted);font-size:.85rem;margin:0 0 14px}
  #results h2,#feed h2{font-family:'Bebas Neue',Impact,sans-serif;font-weight:400;font-size:1.6rem;letter-spacing:.06em;margin:22px 0 10px}
  .group{display:grid;gap:14px}
  .knife .off{color:var(--muted);font-size:.82rem;margin-top:2px}
  .knife .price.nl{background:var(--soft);color:var(--ink)}
  #wantrow{text-align:center;margin:22px 0 6px}
  #wantrow .btn{font-size:.95rem}
```

Body, replacing everything from `<header>` through `<button id="more" ...>`:
```html
<header>
  <a href="/blade-book/"><img class="bbmark" src="/blade-book/mark.svg" alt="blade-book" width="72"></a>
  <h1>BORN ON YOUR DATE</h1>
</header>
<p class="lede">Type a date — a birthday, an anniversary, the day you got the first one. See every registered knife born on it. Not for sale? Ask the owner anyway.</p>
<form id="bornform" autocomplete="off">
  <input id="born" name="born" type="text" inputmode="numeric" maxlength="32" placeholder="7/28 or 7/28/1999" aria-label="a date">
  <button class="btn accent" type="submit">FIND IT</button>
</form>
<p class="hint">month and day are enough — the year is optional</p>
<div id="status" aria-live="polite"></div>
<div id="results"></div>
<div id="wantrow" hidden><button id="wantbtn" class="btn alt" type="button"></button></div>
<section id="feed">
  <h2>LATEST FOR SALE</h2>
  <div id="cards"></div>
  <button id="more" class="btn alt" type="button" hidden>show more</button>
</section>
```

Template: add `<div class="off"></div>` after `<div class="sub"></div>`.

Dialog `#cdlg` heading: `<h2>Contact the owner</h2>`.

Script: replace the whole IIFE with:

```javascript
(function () {
  var API = '/blade-book/api';
  var BOARD = '/blade-book/api/board';
  var BORN = '/blade-book/api/born';
  var WANTS = '/blade-book/api/wants';
  var LIMIT = 24;
  var $ = function (id) { return document.getElementById(id); };
  var me = null, offset = 0, total = 0, current = null, loading = false, query = null;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function say(t, err) { var s = $('status'); s.textContent = t || ''; s.className = err ? 'err' : ''; }
  function req(url, opts) {
    opts = opts || {}; opts.credentials = 'same-origin';
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return { ok: r.ok, status: r.status, j: j }; });
    });
  }
  function post(url, body) {
    return req(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  }
  function money(n) { return '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 }); }
  function signIn() { location.href = '/blade-book/?auth=required' + '&next=' + encodeURIComponent(location.pathname + location.search); }
  function offText(n) {
    if (!n) return '';
    var a = Math.abs(n), w = a === 1 ? 'the day' : a + ' days';
    return 'born ' + w + (n > 0 ? ' after your date' : ' before your date');
  }

  function renderCard(k) {
    var art = $('tpl').content.firstElementChild.cloneNode(true);
    var pic = art.querySelector('a.pic');
    pic.href = '/blade-book/@' + k.handle + '/' + k.tag + '/';
    var img = pic.querySelector('img');
    if (k.img_t) {
      img.src = '/blade-book/@' + k.handle + '/img/' + k.img_t;
      img.alt = k.name || k.tag;
      img.hidden = false;
    }
    art.querySelector('.name').textContent = k.name || k.tag;
    var bits = [];
    if (k.born) bits.push('born ' + k.born);
    bits.push('@' + k.handle);
    if (k.for_sale && k.listed_at) bits.push('listed ' + k.listed_at.slice(0, 10));
    art.querySelector('.sub').textContent = bits.join(' · ');
    art.querySelector('.off').textContent = offText(k.days_off);
    var price = art.querySelector('.price');
    if (k.for_sale) price.textContent = k.asking_price ? 'asking ' + money(k.asking_price) : 'FOR SALE · inquire';
    else { price.textContent = 'not listed'; price.classList.add('nl'); }
    if (k.seller_note) { var n = art.querySelector('.note'); n.textContent = k.seller_note; n.classList.remove('hidden'); }
    var c = art.querySelector('.contact');
    var label = k.for_sale ? 'contact seller' : 'ask @' + k.handle;
    if (!me) { c.textContent = k.for_sale ? 'sign in to contact' : 'sign in to ask'; c.addEventListener('click', signIn); }
    else if (me.handle === k.handle) { c.disabled = true; c.textContent = k.for_sale ? 'your listing' : 'your knife'; }
    else { c.textContent = label; c.addEventListener('click', function () { openContact(k); }); }
    var r = art.querySelector('.rep');
    if (!me || me.handle === k.handle || !k.for_sale) r.hidden = true;
    else r.addEventListener('click', function () { openReport(k); });
    return art;
  }

  function renderGroup(box, heading, cards) {
    if (!cards || !cards.length) return 0;
    box.appendChild(el('h2', null, heading));
    var g = el('div', 'group');
    cards.forEach(function (k) { g.appendChild(renderCard(k)); });
    box.appendChild(g);
    return cards.length;
  }

  function search(text, push) {
    text = (text || '').trim();
    if (!text) return;
    say('looking…');
    req(BORN + '?d=' + encodeURIComponent(text)).then(function (res) {
      var box = $('results'); clear(box);
      if (res.status !== 200) { say(res.j.error || 'could not search', true); $('wantrow').hidden = true; return; }
      say('');
      query = res.j.query;
      $('born').value = text;
      if (push) history.replaceState(null, '', '?born=' + query.url);
      var md = query.label.replace(/, \d{4}$/, '');
      var n = renderGroup(box, 'Born on ' + query.label, res.j.exact)
            + renderGroup(box, 'Born on ' + md + ', other years', res.j.same_day)
            + renderGroup(box, 'Born within a week of ' + md, res.j.near);
      if (!n) box.appendChild(el('div', 'empty', 'Nothing within a week of ' + md + ' yet.'));
      $('wantbtn').textContent = 'tell me when one is born on ' + query.short;
      $('wantrow').hidden = false;
    }).catch(function () { say('could not reach the server', true); });
  }

  $('bornform').addEventListener('submit', function (ev) { ev.preventDefault(); search($('born').value, true); });

  $('wantbtn').addEventListener('click', function () {
    if (!query) return;
    if (!me) { signIn(); return; }
    var bd = ('0' + query.month).slice(-2) + '-' + ('0' + query.day).slice(-2);
    $('wantbtn').disabled = true;
    post(WANTS, { born_day: bd }).then(function (res) {
      $('wantbtn').disabled = false;
      if (res.status === 401) { signIn(); return; }
      if (!res.ok) { say('✗ ' + (res.j.error || 'could not save that'), true); return; }
      say('✓ we’ll email you when a knife born on ' + query.short + ' goes public');
      window.scrollTo(0, 0);
    }).catch(function () { $('wantbtn').disabled = false; say('could not reach the server', true); });
  });

  function load() {
    if (loading) return;
    loading = true;
    $('more').disabled = true;
    req(BOARD + '?limit=' + LIMIT + '&offset=' + offset).then(function (res) {
      loading = false;
      $('more').disabled = false;
      if (res.status !== 200) { say(res.j.error || 'could not load the board', true); return; }
      total = res.j.count;
      var box = $('cards');
      if (offset === 0) clear(box);
      if (!total) { box.appendChild(el('div', 'empty', 'nothing for sale yet — list a knife from your register')); }
      (res.j.knives || []).forEach(function (k) { box.appendChild(renderCard(k)); });
      offset += (res.j.knives || []).length;
      $('more').hidden = offset >= total;
    }).catch(function () { loading = false; $('more').disabled = false; say('could not reach the server', true); });
  }

  function openContact(k) {
    current = k;
    $('cwho').textContent = 'One intro email goes to @' + k.handle + ' about ' + (k.name || k.tag) + ', and a copy to you. Up to three intros per owner per day.';
    $('cmsg').value = '';
    $('cdlg').showModal();
  }
  $('ccancel').addEventListener('click', function () { $('cdlg').close(); });
  $('csend').addEventListener('click', function () {
    var k = current; if (!k) return;
    $('csend').disabled = true;
    post(BOARD + '/' + k.id + '/contact', { message: $('cmsg').value.trim() }).then(function (res) {
      $('csend').disabled = false;
      if (res.status === 401) { signIn(); return; }
      if (res.status === 429 || res.status === 502 || !res.ok) { say('✗ ' + (res.j.error || 'could not send'), true); $('cdlg').close(); return; }
      $('cdlg').close();
      say('✓ intro sent to @' + k.handle + ' — check your email for the copy');
      window.scrollTo(0, 0);
    }).catch(function () { $('csend').disabled = false; say('could not reach the server', true); });
  });

  function openReport(k) {
    current = k;
    $('rmsg').value = '';
    $('rdlg').showModal();
  }
  $('rcancel').addEventListener('click', function () { $('rdlg').close(); });
  $('rsend').addEventListener('click', function () {
    var k = current; if (!k) return;
    var reason = $('rmsg').value.trim();
    if (reason.length < 3) { say('say why — a few words', true); $('rmsg').focus(); return; }
    $('rsend').disabled = true;
    post(BOARD + '/' + k.id + '/report', { reason: reason }).then(function (res) {
      $('rsend').disabled = false;
      $('rdlg').close();
      if (res.status === 401) { signIn(); return; }
      if (!res.ok) { say('✗ ' + (res.j.error || 'could not report'), true); return; }
      say(res.j.hidden ? '✓ reported — the listing is now hidden pending review' : '✓ reported — thank you');
      window.scrollTo(0, 0);
    }).catch(function () { $('rsend').disabled = false; say('could not reach the server', true); });
  });

  $('more').addEventListener('click', load);

  req(API + '/auth/me').then(function (res) { me = res.ok ? res.j : null; }).catch(function () { me = null; }).then(function () {
    load();
    var born = new URLSearchParams(location.search).get('born');
    if (born) search(born, true);
  });
})();
```

- [ ] **Step 4: Run the deploy-files tests**

Run: `python3 -m pytest -q tests/test_deploy_files.py`
Expected: all PASS (if `test_landing_board_strip_and_admin_link` or others referenced old board text, fix the page, not the test, unless the text was the heading/lede the spec changed).

- [ ] **Step 5: Smoke the page against a scratch app and screenshot it.** In the scratchpad directory:

```bash
cd /tmp/claude-1000/-home-shg-billboard/*/scratchpad 2>/dev/null || cd "$SCRATCH"
python3 -m venv pw && pw/bin/pip -q install playwright && pw/bin/playwright install chromium
```

Write `shot_board.py` in the scratchpad. It must be run with the scratchpad venv's python **after** `pw/bin/pip -q install -r /home/shg/blade-book/requirements.txt` (the app's deps) — or, if that file does not exist, with system `python3` plus `PYTHONPATH` pointing at the venv's `site-packages` for playwright. The env exports are mandatory (Global Constraints: never the live DB):

```python
# scratch only — never committed. Serves the real app on :5999 with a scratch DB,
# and answers every non-API /blade-book/ URL from the repo's html/ tree, the way Apache does.
import os, sys, threading, mimetypes, pathlib
S = pathlib.Path(__file__).resolve().parent
for d in ('data', 'config', 'log', 'www'):
    (S / d).mkdir(exist_ok=True)
    os.environ[f'BLADEBOOK_{d.upper()}_DIR'] = str(S / d)
sys.path.insert(0, '/home/shg/blade-book')
from bb import db, mail
import app as appmod
application = appmod.create_app(mailer=mail.FakeMailer())

con = db.connect()
uid = db.create_user(con, 'demo@example.com', 'demo')
con.execute('UPDATE users SET verified_at = ? WHERE id = ?', (db.now(), uid)); con.commit()
import json, hashlib
for born, sale, price in (('1999-07-28', 'keeping', None), ('2022-07-28', 'for_sale', 650),
                          ('2020-07-31', 'keeping', None), ('2010-07-28', 'keeping', None)):
    k = db.create_draft_knife(con, uid)
    con.execute("UPDATE knives SET confidence = '{}', model = 'Sebenza', born_on = ?, born_on_precision = 'day', "
                "ext = ?, is_public = 1, sale_status = ?, asking_price = ? WHERE id = ?",
                (born, json.dumps({'generation': '31', 'size': 'Small'}), sale, price, k['id']))
    con.commit()
    db.add_photo(con, uid, k['id'], 1, f'{uid}/{k["id"]}/1.jpg', hashlib.sha256(born.encode()).hexdigest(), 800, 600)
    db.publish_knife(con, uid, k['id'])
con.close()

HTML = pathlib.Path('/home/shg/blade-book/html')
def serve_static(route):
    p = route.request.url.split('://', 1)[1].split('/', 1)[1].split('?')[0]   # 'blade-book/board/'
    rel = p[len('blade-book/'):] if p.startswith('blade-book/') else p
    f = HTML / rel
    if f.is_dir() or rel.endswith('/') or rel == '':
        f = HTML / rel / 'index.html'
    if f.is_file():
        route.fulfill(status=200, body=f.read_bytes(), content_type=mimetypes.guess_type(str(f))[0] or 'text/html')
    else:
        route.fulfill(status=404, body=b'not here')

def run():
    from werkzeug.serving import make_server
    make_server('127.0.0.1', 5999, application).serve_forever()
threading.Thread(target=run, daemon=True).start()

from playwright.sync_api import sync_playwright
shots = [('/blade-book/board/?born=7/28/99', 'board_hit', 1800), ('/blade-book/board/?born=1/2', 'board_miss', 1200),
         ('/blade-book/board/', 'board_plain', 1200), ('/blade-book/', 'landing', 1100)]
with sync_playwright() as p:
    b = p.chromium.launch()
    for path, name, h in shots:
        for w, tag in ((1280, 'desktop'), (390, 'phone')):
            pg = b.new_page(viewport={'width': w, 'height': h})
            pg.route('**/*', lambda r: r.continue_() if '/blade-book/api/' in r.request.url else serve_static(r))
            pg.goto('http://127.0.0.1:5999' + path, wait_until='networkidle')
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(S / f'{name}_{tag}.png'), full_page=True)
            pg.close()
    b.close()
print('done', sorted(f.name for f in S.glob('*.png')))
```

Run it, then look at every PNG with the Read tool: `board_hit_*` (three groups, "born 3 days after your date" on the 7/31 card, "not listed" chips, "sign in to ask" buttons), `board_miss_*` ("Nothing within a week of January 2 yet." plus the want button), `board_plain_*` (box + feed only), `landing_*` (headline, input and FIND IT above the fold on desktop; stacked and not overflowing at 390px). Fix anything cut off, overlapping or unreadable. Delete the scratch `data/`, `www/` and PNGs when done; nothing from the scratchpad is committed.

- [ ] **Step 6: Commit**

```bash
git add html/board/index.html tests/test_deploy_files.py
git commit -m "board page: BORN ON YOUR DATE — search box, three groups, ask the owner, standing want, feed below (date board 8/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 9: Landing hero leads with the date; sign-in returns to `next`

**Files:**
- Modify: `html/index.html:66-76` (hero CSS), `html/index.html:161-177` (hero markup), `html/index.html:366-380` (sign-in JS)
- Test: `tests/test_deploy_files.py` (append; `test_landing_pitches_before_it_asks_and_keeps_the_app_home` must stay green)

**Interfaces:**
- Consumes: `/blade-book/board/?born=` (Task 8).
- Produces: `GET /blade-book/?auth=required&next=<path>` stores `next` in `sessionStorage['bbNext']`; once `/api/auth/me` is OK on the landing, a stored `bbNext` that starts with `/blade-book/` and does not start with `/blade-book//` is cleared and navigated to.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_deploy_files.py`)

```python
def test_landing_leads_with_a_knife_born_on_your_day():
    html = _read('html/index.html')
    hero = html.split('<header class="hero">')[1].split('</header>')[0]
    assert 'A knife born on <em>your day.</em>' in hero
    assert 'action="/blade-book/board/"' in hero and 'method="get"' in hero and 'name="born"' in hero
    assert 'FIND IT' in hero and 'placeholder="7/28 or 7/28/1999"' in hero
    assert 'One photo. Your whole collection, on the record.' in hero          # the old headline lives on as the lede
    assert 'START YOUR REGISTER' in hero and 'SEE A REAL ONE' in hero
    assert hero.index('name="born"') < hero.index('START YOUR REGISTER')
    assert 'K75' in hero                                                       # the Night Sky caption stays


def test_landing_sign_in_returns_to_a_blade_book_path_only():
    html = _read('html/index.html')
    for needle in ("get('next')", "sessionStorage.setItem('bbNext'", "sessionStorage.getItem('bbNext')",
                   "sessionStorage.removeItem('bbNext')", "startsWith('/blade-book/')", "startsWith('/blade-book//')",
                   "location.replace(", 'Sign in to continue'):
        assert needle in html, needle
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m pytest -q tests/test_deploy_files.py -k "born_on_your_day or returns_to"`
Expected: FAIL

- [ ] **Step 3: Hero CSS** — add after line 75 (`.cta .ghost{...}`):

```css
.hero form.born{display:flex;gap:10px;max-width:34em;margin:0 0 18px}
.hero form.born input{flex:1;min-width:0;font:inherit;font-size:1.15rem;padding:.85rem 1rem;border:2px solid #fff;border-radius:12px;background:rgba(255,255,255,.96);color:#111}
.hero form.born .btn{white-space:nowrap}
.hero p.sub{font-size:.95rem;opacity:.85;margin:0 0 22px;max-width:34em}
```

- [ ] **Step 4: Hero markup** — replace the `<h1>`, `<p class="lede">` and `<div class="cta">` inside `<header class="hero">` with:

```html
    <h1 class="bb-display">A knife born on <em>your day.</em></h1>
    <p class="lede">Type a date — a birthday, an anniversary — and see every registered knife born on it. Not for sale? Ask the owner anyway.</p>
    <form class="born" action="/blade-book/board/" method="get" autocomplete="off">
      <input name="born" type="text" inputmode="numeric" maxlength="32" placeholder="7/28 or 7/28/1999" aria-label="a date" required>
      <button class="btn accent" type="submit">FIND IT</button>
    </form>
    <p class="sub">blade-book is a register for knife collectors. One photo. Your whole collection, on the record.</p>
    <div class="cta">
      <a class="btn accent" href="#start">START YOUR REGISTER</a>
      <a class="btn ghost" href="/@simon-collector/">SEE A REAL ONE →</a>
    </div>
```

Leave the K75 `<span class="cap">` as it is. Check the phone media query block (around line 142-151) still looks right: add `.hero form.born{flex-direction:column}` inside it so the input and button stack at 390px.

- [ ] **Step 5: Sign-in return in the landing JS** — right after `const flag = new URLSearchParams(location.search).get('auth');` add:

```javascript
  const nextRaw = new URLSearchParams(location.search).get('next');
  if (nextRaw && nextRaw.startsWith('/blade-book/') && !nextRaw.startsWith('/blade-book//')) {
    try { sessionStorage.setItem('bbNext', nextRaw); } catch (e) {}
  }
  let bbNext = null;
  try { bbNext = sessionStorage.getItem('bbNext'); } catch (e) {}
```

Change the `required` message line to:

```javascript
  if (flag === 'required') msg(bbNext ? 'Sign in to continue — we’ll take you back.' : 'Sign in to see your register.', true);
```

Right after `const me = await fetch(API + '/me', {credentials: 'same-origin'});` add:

```javascript
  if (me.ok && bbNext && bbNext.startsWith('/blade-book/') && !bbNext.startsWith('/blade-book//')) {
    try { sessionStorage.removeItem('bbNext'); } catch (e) {}
    location.replace(bbNext);
    return;
  }
```

- [ ] **Step 6: Run the deploy-files tests and the whole suite**

Run: `python3 -m pytest -q tests/test_deploy_files.py && python3 -m pytest -q`
Expected: all PASS. `test_landing_pitches_before_it_asks_and_keeps_the_app_home` still finds `One photo.` (now in the sub line) and all four landing images.

- [ ] **Step 7: Screenshot the landing** — re-run the Task 8 `shot_board.py` (it already shoots `/blade-book/` as `landing_desktop.png` and `landing_phone.png`). Read both PNGs. The input must not overflow the hero at 390px and the headline, input and FIND IT must sit above the fold on desktop.

- [ ] **Step 8: Commit**

```bash
git add html/index.html tests/test_deploy_files.py
git commit -m "landing: lead with 'A knife born on your day' — GET form onto the board; sign-in returns to next (date board 9/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

### Task 10: Deploy script and docs

**Files:**
- Create: `scripts/deploy_date_board.sh`
- Modify: `CLAUDE.md` (blade-book's, if it lists pages/routes — add one line each for `/api/born` and the date board; skip if it has no such list)
- Test: `tests/test_deploy_files.py` (append)

- [ ] **Step 1: Write the failing test**

```python
def test_deploy_date_board_script():
    s = _read('scripts/deploy_date_board.sh')
    for needle in ('set -euo pipefail', 'cp -r "$CODE/html/." "$WWW/"', 'supervisorctl restart blade_book',
                   '/blade-book/api/healthz', "/blade-book/api/born?d=7/28", 'BORN ON YOUR DATE', 'sudo -u shg'):
        assert needle in s, needle
    assert 'publish_sweep' not in s                      # register pages do not change
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m pytest -q tests/test_deploy_files.py -k deploy_date_board`
Expected: FAIL (file missing)

- [ ] **Step 3: Write `scripts/deploy_date_board.sh`**

```bash
#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-10-15: the date board. Static pages (board, landing, wants) + the app
# (/api/born, ask-the-owner, schema v15 born_day). No publish sweep: register
# pages do not change. Safe to run again.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_date_board.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
echo "1/3 static pages copied"
if ! supervisorctl restart blade_book; then
  echo "STOP: the app did not restart. Static pages are new; the API is old. Check /var/log/blade-book/app.log." >&2
  exit 1
fi
up=''
for i in 1 2 3 4 5 6 7 8 9 10; do
  if up=$(curl -sf http://127.0.0.1:5004/blade-book/api/healthz); then break; fi
  up=''; sleep 1
done
if [ -z "$up" ]; then
  echo "STOP: the app restarted but is not answering after 10 s. Check /var/log/blade-book/app.log." >&2
  exit 1
fi
echo "2/3 app restarted: $up"
born=$(curl -s --max-time 10 "http://127.0.0.1:5004/blade-book/api/born?d=7/28" || true)
if grep -q '"same_day"' <<<"$born"; then echo "3/3 /api/born answers"; else echo "MISSING: /api/born did not answer with groups: $born" >&2; exit 1; fi
page=$(curl -s --max-time 20 "https://blade-book.com/board/?fresh=$(date +%s)" || true)
for needle in 'BORN ON YOUR DATE' 'FIND IT' 'LATEST FOR SALE'; do
  if grep -q "$needle" <<<"$page"; then echo "ok   $needle"; else echo "MISSING $needle (Cloudflare may still hold the old page for up to 4 h)"; fi
done
```

`chmod +x scripts/deploy_date_board.sh`.

- [ ] **Step 4: Run the test and the whole suite**

Run: `python3 -m pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/deploy_date_board.sh tests/test_deploy_files.py CLAUDE.md
git commit -m "deploy: date board script — static copy, restart, /api/born smoke (date board 10/10)

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01VfqgG6C2YWucfnqGWoS7Xf"
```

---

## After the last task

1. Whole-branch review (fresh reviewer, most capable model) against the spec.
2. Push to `origin/main` (the repo is public; re-check no owner names or emails in fixtures).
3. Simon runs `! sudo bash /home/shg/blade-book/scripts/deploy_date_board.sh`.
4. Simon flips his private knives public from the vault's bulk toggle (seeding, spec §6).
5. Post the session blog entry and update the handoff.
