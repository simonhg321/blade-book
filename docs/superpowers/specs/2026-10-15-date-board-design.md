# The date board — design

**Date:** 2026-10-15 · **Owner:** Simon · **Written by:** Sky Claude (stark)
**Status:** draft for Simon's review

## Why

blade-book leads with "catalog your collection". That asks for work before it
gives anything, and uptake is slow. CRK people already talk in birthdays: "ISO a
knife born 7/28/99" is a common Facebook post, and Simon answers them today by
looking in his own blade-book, where day-and-month alone usually finds one or
two close ones.

So blade-book leads with the question instead: **is there a knife born on your
date?** Simon's collection is the seed, for sale or not. The hit is the hook.
A miss leaves a standing want behind. Either way the person has a reason to
come back, and photographing one knife they would sell is now a small ask.

Decisions Simon made in the brainstorm (2026-10-15):

- On a miss, show the nearest knives **and** offer a standing want (option 3).
- The board becomes the date board; the landing page leads with the same box.
- "Ask the owner" works on **any** public hit, listed or not. Simon makes his
  collection mostly public as bait; he would sell almost any knife at the right
  price.

## What the live data says (2026-10-15)

99 live knives, 90 with a born date, 81 of those Simon's, 65 public. Twenty
birth years, mostly 2019 and later. An exact date almost always misses (about 1
in 100); month and day across years hits about 1 in 4. Hence the three groups
below: the page must always show something.

## Scope

In:

1. `GET /api/born?d=<text>`: parse a date, return three ranked groups of public
   knives with real ids.
2. The board page re-laid out around that search, for-sale feed underneath.
3. The landing hero leads with the same box.
4. "Ask the owner" on any public knife (generalised board contact).
5. A `born_day` want that fires when a public knife with that month and day
   appears, whatever its sale status.

Out: the vault sheet, the search page, the register pages, month-only dates,
owners who hide the day, any change to how knives are added or decoded.

## 1. Date parsing — `bb/born.py`

`parse(text) -> (year | None, month, day)`; raises `BornError('I need at least
a month and a day')` on anything else. Accepted, whitespace-tolerant, max 32
characters:

| input | result |
|---|---|
| `7/28`, `7-28`, `07/28` | (None, 7, 28) |
| `7/28/99`, `7/28/1999`, `07-28-1999` | (1999, 7, 28) |
| `1999-07-28`, `1999/7/28` | (1999, 7, 28) |
| `July 28`, `Jul 28`, `July 28, 1999`, `28 July 1999` | as above |

Two-digit years: `2000 + yy` when that is not after the current year, else
`1900 + yy` (so 99 → 1999, 21 → 2021). Month and day are validated against a
leap year, so 2/29 is legal. Four-digit years outside 1900–2100 are rejected.

Canonical URL form: `MM-DD` or `YYYY-MM-DD` (`/blade-book/board/?born=07-28`).
The page rewrites its URL to that form after a successful parse, so what Simon
pastes in a thread is short and stable.

## 2. The endpoint — `GET /blade-book/api/born`

Public, no sign-in, `d` required. 400 with the parser's message on bad input.

Visible set, same shape as the board predicate without the for-sale clause:
knife `status='live'`, `is_public=1`, `hidden_at IS NULL`, owner
`profile_private=0` and no `public_key`, **and** `born_on_precision='day'`,
owner `hide_born_day=0`, `born_on` matching `YYYY-MM-DD`. The board-owner
eligibility gate (account age) is **not** applied: a hit is not a listing, and
the abuse limits live on the asker's side.

Response:

```json
{"query": {"year": 1999, "month": 7, "day": 28, "label": "July 28, 1999", "url": "1999-07-28"},
 "exact":    [card, ...],
 "same_day": [card, ...],
 "near":     [card, ...]}
```

- `exact`: year, month and day equal. Empty when no year was given.
- `same_day`: month and day equal, other years (all years when no year was
  given). Most recent year first.
- `near`: month-day within ±7 days by circular day-of-year distance, any year,
  not already placed above. Closest first, then most recent year. Each card
  carries `days_off` (signed integer, negative = before the date).
- A knife appears in one group only. Each group is capped at 24 cards.

A card is the board card (`publish.public_row` through `board.card`) plus
`id`, `handle`, `born_on`, `for_sale`, `asking_price`, `days_off`. Never the
owner's email or any private column.

Implementation: one query on `knives JOIN users` with the predicate above and
`substr(born_on, 6) IN (...)` for the 15 candidate month-days, ranked in
Python. 99 rows today; fine to six figures without an index.

## 3. Ask the owner

`POST /blade-book/api/board/<id>/contact` stays the route. `db.board_knife`
gains a sibling `db.public_knife` with the same select minus the for-sale
clause; the contact route uses it. Everything else is unchanged: sign-in
required, 3 intros a day to one owner, 10 a day total, "that's your own knife"
400, undeliverable-owner 409, claim rows, report route untouched.

Email copy branches on `for_sale`:

- for sale: today's wording ("interested in your K80 on the board").
- otherwise: subject `blade-book: someone asked about your K80`, body
  "@buyer asked about your K80 (card text · not listed for sale).
  They found it on the date board." Buyer copy mirrors it. `match.card_text`
  already omits the price when there is none.

The button label is "contact seller" on a for-sale card and "ask @handle" on
any other.

## 4. The `born_day` want

Schema v15: `wants.born_day TEXT` nullable, `MM-DD`. `WANT_FIELDS` and
`WANT_CRITERIA` gain `born_day`; validation: string matching `^\d{2}-\d{2}$`
with a legal month and day (leap-year table), else `ValueError('bad field')`.
`born_day` on its own satisfies the "say what you want" rule.

`match.knife_matches`, when `want['born_day']` is set:

- require `k.born_on_precision == 'day'`, owner `hide_born_day == 0`, and
  `k.born_on[5:] == born_day`;
- **skip** the mode gate (any sale status matches), because the ask path works
  on any knife;
- skip the maker gate (the date is the criterion, not the brand);
- the other criteria, if the want has any, still apply.

Everything downstream is unchanged: `claim_intro` keeps one intro per
(want, knife), the `*/15` match cron sends, the 20-active-wants cap holds, the
wanter deactivates from the wants list as today. The intro email for a
`born_day` match says "born on your date, 7/28" in the first line.

## 5. Pages

**Board (`html/board/index.html`).** Heading `BORN ON YOUR DATE`. Lede: "Type
a date — a birthday, an anniversary, the day you got the first one. See every
registered knife born on it. Not for sale? Ask the owner anyway." One text
input, placeholder `7/28 or 7/28/1999`, button `FIND IT`. On submit: call
`/api/born`, rewrite the URL to `?born=…`, render the three groups under their
headings:

- "Born on July 28, 1999"
- "Born on July 28, other years"
- "Born within a week of July 28" with "born 3 days after your date" on each
  card

Empty groups are omitted; if all three are empty: "Nothing within a week of
July 28 yet." Under the results, always: a button `tell me when one is born on
7/28` that posts a `born_day` want (signed out: go to sign-in and come back to
this URL; signed in: confirm inline, "we'll email you"). Then `LATEST FOR
SALE`, the existing newest-first feed, unchanged. A visit with no `?born=`
shows the box and the feed.

Cards reuse the existing template: photo, name, born, `@handle`, price or
`not listed`, note, then `contact seller` / `ask @handle` and `report`.

**Landing (`html/index.html`).** The hero headline becomes `A knife born on
your day.` with the same input and `FIND IT` button directly under it; submit
navigates to `/blade-book/board/?born=…`. The lede says what the site is in one
sentence. The "one photo" pitch is already the first section below the hero and
stays there. `START YOUR REGISTER` and `SEE A REAL ONE` remain as the second
row of the hero. The K75 caption stays.

**Sign-in return.** The existing `?auth=required` path must bring the person
back to the board URL they left, including `?born=`. If it does not today, the
plan adds a `next` parameter limited to `/blade-book/` paths.

## 6. Seeding

No code. Simon flips his private knives public from the vault's bulk toggle. All
five current users have `hide_born_day=0`.

## 7. Testing

- `test_born_parse.py`: the table above plus rejects (empty, `13/40`, `7`,
  `abc`, 33 characters, year 1800).
- `test_born_api.py`: a fixed set of dates → exact / same_day / near membership
  and order, `days_off` sign, caps, one-group-only; invisible knives never
  appear (private, hidden, month precision, owner hides day, owner profile
  private, owner has public_key, draft); 400 on bad input; no email field in
  any card.
- `test_board_api.py`: contact on a keeping knife succeeds with the "asked
  about" wording; limits still apply; for-sale wording unchanged.
- `test_match.py` / `test_wants_api.py`: `born_day` validation, matches a
  keeping knife, ignores month-precision and hidden-day knives, one intro per
  pair, cap of 20.
- `test_deploy_files.py`: board and landing carry the box and the new
  headings; no stale "newest first" lede at the top.
- Playwright screenshots of board (no query, hit, miss) and landing, desktop
  and phone, before Simon sees it.

## 8. Deploy

Static pages copy, app restart (schema v15 migration runs at start), health
check. No publish sweep needed: register pages do not change. One script in
`scripts/`, run by Simon with `! sudo bash …`.

## Open questions

None blocking. Later, not now: month-only knives as a fourth group; a
"born this week" strip on the home page; the same box on the register pages.
