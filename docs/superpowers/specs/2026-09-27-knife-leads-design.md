# The knife leads — design

**Date:** 2026-09-27 · **Status:** awaiting Simon's review · **Napkin:** https://blade-book.com/abtesting/knife.html

## Why

blade-book wants to be sticky and trusted. The honest kind of sticky is pride: the page has to look better than the owner's own camera roll.

Today a stranger who taps a forum link lands on the plainest page on the site. The photo is a card shot: box, foam, paperwork, and a knife in the corner. The name drops the graphic, so five neighbours all read "Small Sebenza 31". There is no way to the next knife.

## Who it is for

The stranger arriving from a forum link. Their motions, in order:

1. **Look** at the knife.
2. **Check** it is real: the card, the date, the owner.
3. **Wander** to the next knife.
4. **Want one** of their own.

## What Simon decided

| # | Question | Decision |
|---|---|---|
| 1 | Who draws the crop box? | We propose a box. The owner approves or nudges it. We never pick a face for anyone. |
| 2 | Name order | Model first, then the graphic: "Large Sebenza 31 — Lunar Landing". There are too many variants to lead with them. |
| 3 | Is the card shot public? | Yes, whenever the knife is. Every knife is born private, so a public knife is one the owner chose to show. Scammers want knife photos, not cards. |
| 4 | Flip order | Sky's call: K-number order, the order the register already publishes in. |
| — | The mark | D: etch plus plate. Live since 2026-09-27 (`b0bf858`). |

## Two phases

Each phase ships on its own and gets its own implementation plan.

- **Phase 1, the page:** names, one top bar, next and previous. No schema change. No new photos.
- **Phase 2, the face:** the crop of the knife leads; the card shot becomes "the record". Schema v15.

---

## Phase 1 — the page

### 1.1 Names carry the graphic

`publish.full_name(row)` already exists (it writes the plate). Use it wherever a public name shows:

| Place | Code |
|---|---|
| Knife page `<h1>`, `<title>`, `og:title`, image `alt` | `bb/publish.py` `_knife_page` |
| Register list and grid, the "in the photo" card | `bb/publish.py` `_index_html` |
| Search cards | `bb/search.py` `c['name']` |
| Board cards | `bb/board.py` `c['name']` |

Rules that stay:

- A gated register keeps the model and edition out of `<title>` and `og:title`, as today.
- Register sort by name keeps working: `data-name` carries the full name.
- The owner's pages (`/me`) already show the graphic (vault step 1). No change there.

### 1.2 One top bar

One bar on every public page, replacing four different navs.

```
[mark] BLADE-BOOK    in @simon-collector's register · 75 knives        search   the board   [sign in]
```

- The middle part shows only on a register page and its knife pages. On a phone it drops out.
- `sign in` keeps class `bb-auth`, so `nav.js` still turns it into "my register".
- Generated pages get it from one function in `bb/publish.py` (`_topbar(handle=None, count=None)`).
- Static pages (`html/search`, `html/board`, `html/how`, `html/faq`, `html/about`, `html/terms`) get the same markup by hand. A test compares each page's bar with `_topbar()` so they cannot drift.
- The landing keeps its hero. Its nav items match the bar.
- The footer strip stays as it is.
- The register's full-bleed hero keeps its look. The bar sits over it, light on dark.

### 1.3 The knife page

Top to bottom:

1. The top bar.
2. **The lead photo**, with a count chip ("K80 · 74 of 81") and next and previous arrows.
3. Tag, **full name**, and a by-line: "born February 6, 2014 · in @simon-collector's register".
4. For sale or trade, when it is.
5. The spec table, as today.
6. The owner's public story, when there is one.
7. **The record** (Phase 2 fills this in).
8. **More from this register:** the next five knives, as tiles.
9. "Your knives deserve a page like this" and the start button.

### 1.4 Next and previous

- Order: K-number order, which is the order `db.public_knives` returns.
- The links are plain `<a href>` tags written at build time. No JavaScript is needed to flip.
- The first knife has no previous arrow. The last has no next arrow. No wrap-around.
- A small script adds two conveniences: swipe on a phone, arrow keys on a keyboard. Both only follow the existing links.
- The links carry `rel="prev"` and `rel="next"`.
- A register with one public knife shows no arrows and no strip.

### 1.5 What Phase 1 does not change

Photos, the mark, the schema, the API, `/me`.

---

## Phase 2 — the face

### 2.1 The idea

Every knife already has a card shot, and the knife is in it. The **face** is a crop of the knife from a photo the owner already took. The card shot moves down the page and becomes **the record**.

A real beauty shot beats a crop. When an owner adds one and picks it as the cover, it leads, whole. That is a reason to come back that pays the owner.

### 2.2 Data (schema v15)

Two new columns on `knives`. Both are private: they are not in `PUBLIC_FIELDS`, and they join `db.PRIVATE_COLUMNS` so the leak test covers them.

| Column | Holds |
|---|---|
| `face_box` | The box the **owner approved**. JSON: `{"seq":1,"x":0.40,"y":0.41,"w":0.60,"h":0.36}`. Null until they approve. |
| `face_proposal` | The box **we proposed**. Same shape. Shown to the owner only. Never published. |

- `x, y, w, h` are fractions of the upright photo (after EXIF rotation), so they survive any resize.
- `seq` ties the box to one photo. If that photo is replaced or deleted, or the owner picks a different cover, both columns are cleared.

### 2.3 Proposing a box

New module `bb/face.py`.

- One small vision call with the photo at thumb size: "where is the knife?" It returns four numbers.
- Model from env `FACE_MODEL`, default `claude-haiku-4-5`. It is a separate call from the decode, so the decode schema does not grow (the ~70-property grammar cliff).
- The box is padded by 8%, widened to at least 4:3, and clamped to the photo.
- It runs in the background after a save, the way the web lookup does. It is logged to `ai_calls.jsonl` with kind `face`.
- If the call fails or returns nonsense, there is no proposal. The owner can still draw a box.
- Existing knives: `scripts/propose_faces.py` proposes for every live knife that has no box. About 100 knives, well under $1.

The photo goes to Anthropic, as it already does for the decode. Nothing new leaves the box.

### 2.4 Approving a box

Nothing becomes a face until the owner says so.

**The pass page, `/me/faces/`:** one card per live knife that has a proposal and no approved box.

- The photo, with the proposed box drawn on it and everything outside dimmed.
- **✓ Use it** approves the box as shown.
- Drag the box or its corners to nudge it, then approve.
- **Skip** leaves the knife as it is.
- A counter: "12 of 89 done".

**One knife at a time:** the same control sits in the knife's sheet on `/me`, labelled "the face".

**After adding a knife:** `/me` says "we found the knife in your photo" with a link to approve it. It never blocks the save.

**API** (owner only, CSRF as the other knife routes):

| Route | Does |
|---|---|
| `PUT /knives/<id>/face` | Body is a box. Validates: numbers in 0–1, inside the photo, each side at least 15% of the photo, `seq` is a photo of this knife. Sets `face_box`. Schedules a publish. |
| `DELETE /knives/<id>/face` | Clears `face_box`. The page goes back to leading with the cover photo. |

Both write an event, so the history timeline (vault step 2) shows it.

### 2.5 What gets published

File names stay, so the register hero, `og:image`, search, and the board follow without changes.

| File | Without a face | With a face |
|---|---|---|
| `K80.jpg` (the lead) | the cover photo | **the crop** |
| `K80_t.jpg` (tiles) | thumb of the cover photo | **thumb of the crop** |
| `K80_r.jpg` (the record) | photo 1, only when the cover is a different photo | photo 1, the card shot |

- Photo 1 is always the card shot: it is the photo the decode read.
- The crop is cut from the original, then sized to `DISPLAY_EDGE`. A 12-megapixel card shot leaves about 1,600 pixels across the knife.
- A cardless knife has no record. The section is left out.
- The record's file name joins the public row as `img_r`. Nothing else about photo 1 is published.

### 2.6 The record on the page

A card beside the specs on a desktop, under them on a phone:

- Heading "The record", with "✓ card read" when the knife was decoded from a card.
- One line: "The birth card, as it came in the box."
- The card shot, shown at 4:3 with a "see the whole card shot" link to the full image.
- Chips for what came with it: box, card, papers, pouch, lanyard. **Open point:** these are `has_box`, `has_card` and so on, which are not public fields today. See 2.9.

### 2.7 The mark on the face

Simon's point changes my napkin. I had the light mark on the knife and the heavy one on the card shot, because I thought the card was the prize. It is the other way round: **the knife photo is what a scammer steals.**

So:

- **The face carries the full mark:** the repeat etch at strength 0.8, plus the plate. This is the mark that is live today. On the Night Sky (K75, already a beauty shot) it is quiet and it is everywhere.
- **The record carries the same mark.** One mark, one look.
- **Tiles get the etch too.** Today `K80_t.jpg` is a clean 800-pixel photo. Once tiles are knife crops, a clean 800-pixel knife photo is good enough for a scam listing. The etch at that size is nearly invisible on a tile and still there when someone zooms.

This departs from mark D as drawn on the napkin, where the crop had a single etched line. **Simon to confirm.**

### 2.8 Rules that never bend

- We never pick a face, and we never publish a proposal.
- A private knife publishes nothing: no face, no record, no thumb.
- The plate and every name come from the public row only.
- A gated register names nothing on the plate.
- Every published image is re-encoded: no EXIF, no GPS.

### 2.9 Open points for Simon

1. **The mark on the face** (2.7): the full repeat, or the single line from the napkin?
2. **The kit chips** (2.6): showing "box · card · papers" publishes five fields that are unpublished today, for public knives only. Show them, or leave them out until the "what my public page shows" switches (vault step 3)?
3. **Order of work:** Phase 1 first (quick, no risk), or Phase 2 first (the big visual win)?

---

## Testing

- Test first, for every behaviour above. `tests/test_publish.py` and `tests/test_publish_bundle.py` are the homes for most of it.
- `tests/test_publish_leak.py` must stay green: the new columns are private.
- Box validation gets its own tests: out of range, too small, wrong photo, another owner's knife.
- The proposer is tested with a fake client, as the decoder is. No network in tests.
- A look before shipping: build a preview from a **copy** of the database with the photo store read-only, then screenshot desktop and phone.

## Rollout

- Build in a git worktree. The publish cron imports the main working tree every five minutes.
- Phase 1: merge, restart, sweep. One script.
- Phase 2 changes the schema. Merge and restart in the same minute, or the cron migrates the live database while old workers still run.
- Photos are cached for four hours at the edge and in browsers.
- Rollback: tag before each phase. Phase 2's columns are additive; old code ignores them.

## Not in this design

- The board's "sign in to contact" dead end.
- Per-photo public and private (vault step 2) and the public-page switches (vault step 3).
- Follow, knife birthdays, the sign-in code.
- Internal links still use `/blade-book/...`, which redirects to the root on blade-book.com.
