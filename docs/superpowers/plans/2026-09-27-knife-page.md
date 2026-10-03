# The Knife Page (Phase 1 of "the knife leads") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A stranger who lands on a public knife page sees the knife's full name, one nav bar, the kit it came with, and a way to flip to the next knife; the photo mark becomes the plate alone.

**Architecture:** Everything public is a static file written by `bb/publish.py` (`build_user` → `_index_html`, `_knife_page`, `export_hero`) or a hand-written page under `html/`. This plan changes what those writers emit; there is no schema change, no API change and no new photo. The shared bar's CSS lives in `html/vibe.css`, which every page already links.

**Tech Stack:** Python 3.12 (system `python3`), Pillow 12, Flask app under gunicorn, pytest, plain HTML/CSS/JS (no build step), Playwright (scratch venv) for the look-before-ship step.

**Spec:** `docs/superpowers/specs/2026-09-27-knife-leads-design.md` — sections 1.1 to 1.7.

## Global Constraints

- Work in a git worktree, never in `/home/shg/blade-book` itself: the publish cron imports that working tree every five minutes.
- Tests run with the system interpreter: `python3 -m pytest -q`. Baseline before this plan: **694 passed**.
- Never run `publish.build_user` or `scripts/publish_sweep.py` against the live database for a preview. Copy the database and export `BLADEBOOK_DATA_DIR`, `BLADEBOOK_WWW_DIR`, `BLADEBOOK_LOG_DIR` first (Task 8).
- Every value written into HTML goes through `html_mod.escape` (aliased `e`). No `innerHTML` anywhere.
- Public output is built from `public_row()` only. Nothing in `db.PRIVATE_COLUMNS` may reach a bundle (`tests/test_publish_leak.py`).
- A gated register keeps the model and edition out of `<title>` and `og:title`.
- Internal links keep the `/blade-book/` prefix (`paths.URL_PREFIX`).
- Name order is model first, then the graphic: "Large Sebenza 31 — Lunar Landing".
- The mark is the plate alone: the photo is untouched, tiles are clean.
- Kit chips show only a known "yes". "No" and "unknown" show nothing.
- The word for the blue polishing cloth is "cloth" (Phase 3; do not introduce "napkin" or "rag" in copy).
- Asset version after this plan: `?v=20260927` on every `vibe.css` and `nav.js` reference.
- New files start with `# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)` (Python, shell).
- Commit messages end with the two attribution lines used in this repo's recent commits (`Co-Authored-By:` and `Claude-Session:`).

## Review Focus

1. **A register with one public knife.** Expect: no arrows, no "more from this register" strip, count chip reads "K01 · 1 of 1". (Task 6)
2. **A knife with no photo.** Expect: the page still offers next and previous, as plain links in a row. (Task 6)
3. **A name with HTML in it** (a graphic called `<b>"Glo"</b>`). Expect: escaped in the heading, the title, the register row, the strip tiles and the arrows' `aria-label`. (Tasks 2 and 6)
4. **A neighbour with no thumb in the strip.** Expect: a dark placeholder block, never a broken image. (Task 6)
5. **A 24-character handle and a long name on a 390-pixel phone.** Expect: no sideways scroll on the knife page or any static page. (Task 8, measured in the browser)

## File Structure

| File | Responsibility | Tasks |
|---|---|---|
| `bb/publish.py` | the mark, names, kit, `_topbar`, `_neighbours`, `_tile`, `_knife_page`, register hero links | 1, 2, 3, 5, 6 |
| `bb/search.py`, `bb/board.py` | card names use `full_name` | 2 |
| `html/vibe.css` | `.bb-top` rules; `.bb-strip` rules removed | 4, 7 |
| every `html/**/*.html`, `bb/publish.py`, tests | asset version bump | 4 |
| `html/{search,board,how,faq,about,terms}/index.html` | carry the top bar | 7 |
| `scripts/deploy_page.sh` | deploy: static tree, restart, sweep, check | 8 |
| `tests/test_publish.py`, `tests/test_publish_bundle.py`, `tests/test_deploy_files.py` | tests | all |

---

### Task 0: Worktree and rollback tag

- [ ] **Step 1: Tag the rollback point and make the worktree**

```bash
cd /home/shg/blade-book
git tag v1.1-pre-page-2026-09-27 && git push origin v1.1-pre-page-2026-09-27
git worktree add -b knife-page /home/shg/blade-book-wt-page main
cd /home/shg/blade-book-wt-page && python3 -m pytest -q | tail -1
```

Expected: `694 passed`.

---

### Task 1: The mark gets simple

**Files:**
- Modify: `bb/publish.py` (the block from the comment `# The mark (2026-09-26` through `def _watermark`, the PIL import, and `export_hero`)
- Test: `tests/test_publish.py`

**Interfaces:**
- Consumes: `_plate(img, text)`, `plate_text(row, handle, gated=False)` (exist).
- Produces: `export_hero(store, k, handle, img_dir, row=None, gated=False)` unchanged in signature; the display image is the untouched photo with the plate under it. `_watermark`, `_etch`, `_etch_mask`, `_ETCH_ANGLE` no longer exist.

- [ ] **Step 1: Write the failing test** (append to `tests/test_publish.py`)

```python
def test_the_photo_itself_is_untouched(tmp_path):
    # Simon 2026-09-27: "a subtle something that hooks it to us" — the plate, nothing on the knife
    display, _ = _export(tmp_path, _flat_jpeg(1200, 800, (30, 60, 160)))
    photo = display.crop((0, 0, 1200, 790))            # clear of the plate's rule line
    for band, want in zip(photo.split(), (30, 60, 160)):
        lo, hi = band.getextrema()
        assert want - 4 <= lo and hi <= want + 4, (lo, hi, want)
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest -q tests/test_publish.py::test_the_photo_itself_is_untouched`
Expected: FAIL — the etch moved pixels far outside ±4.

- [ ] **Step 3: Remove the etch**

In `bb/publish.py`:

1. Change the import back:

```python
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
```

2. Replace the comment block above `FONT_DIR` with:

```python
# The mark (2026-09-27, Simon: "just a subtle something that hooks it to us").
# The photo is untouched. A cream PLATE under it names the knife and the page
# it lives on. Photos of these knives are everywhere; scam-proofing is not the job.
```

3. Delete `_ETCH_ANGLE = 24`, the whole of `def _etch_mask`, the whole of `def _etch`, and the whole of `def _watermark`.

4. In `export_hero`, replace the `_watermark` line:

```python
    display = _plate(display, plate_text(row or {'tag': k['tag']}, handle, gated))
```

- [ ] **Step 4: Delete the two etch tests**

Remove `test_mark_keeps_the_photos_own_colour` and `test_mark_crosses_every_part_of_the_photo` from `tests/test_publish.py`. They describe behaviour that no longer exists. Update the section comment above `CREAM = (250, 246, 238)` to:

```python
# --- the mark (2026-09-27): the photo untouched, a plate under it ---
```

- [ ] **Step 5: Run the suite**

Run: `python3 -m pytest -q | tail -2`
Expected: `693 passed` (694 − 2 + 1), no warnings.

- [ ] **Step 6: Commit**

```bash
git add bb/publish.py tests/test_publish.py
git commit -m "the mark gets simple: the plate stays, the etch over the photo goes"
```

---

### Task 2: Names carry the graphic

**Files:**
- Modify: `bb/publish.py` (`_knife_page`, `_index_html`), `bb/search.py:35,71`, `bb/board.py:31`
- Test: `tests/test_publish.py`

**Interfaces:**
- Consumes: `publish.full_name(row) -> str` (exists).
- Produces: every public name is `full_name(row)`. `display_name` stays for callers outside this plan (`bb/match.py`, `bb/account.py`).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_publish.py`)

```python
# --- names carry the graphic (2026-09-27): model first, then the graphic ---

def _glorious_row(**over):
    row = publish.public_row(_glorious(**over), USER)
    row['img'], row['img_t'] = 'K80.jpg', 'K80_t.jpg'
    return row


def test_knife_page_name_carries_the_graphic():
    html = publish._knife_page(_glorious_row(), 'simon-collector', gated=False)
    assert '<h1>Large Sebenza 21 — Glorious</h1>' in html
    assert '<title>Large Sebenza 21 — Glorious — @simon-collector</title>' in html
    assert 'alt="Large Sebenza 21 — Glorious"' in html


def test_gated_knife_page_head_still_names_nothing():
    html = publish._knife_page(_glorious_row(), 'simon-collector', gated=True)
    head = html.split('</head>')[0]
    assert 'Glorious' not in head and 'Sebenza' not in head


def test_register_rows_and_the_feat_card_carry_the_graphic():
    html = publish._index_html([_glorious_row()], USER, gated=False)
    assert 'data-name="Large Sebenza 21 — Glorious"' in html
    feat = html.split('class="feat"')[1].split('</a>')[0]
    assert 'Large Sebenza 21 — Glorious' in feat


def test_search_and_board_cards_carry_the_graphic():
    from bb import board, search
    assert search._card(_glorious_row(), 'simon-collector')['name'] == 'Large Sebenza 21 — Glorious'
    k = dict(_glorious(), owner_handle='simon-collector', owner_hide_born_day=0, photos=[])
    assert board.card(k)['name'] == 'Large Sebenza 21 — Glorious'


def test_a_name_with_markup_in_it_is_escaped():
    k = _glorious()
    k['ext']['graphic_name'] = '<b>"Glo"</b>'
    row = publish.public_row(k, USER)
    row['img'], row['img_t'] = 'K80.jpg', 'K80_t.jpg'
    for html in (publish._knife_page(row, 'simon-collector', gated=False),
                 publish._index_html([row], USER, gated=False)):
        assert '<b>"Glo"</b>' not in html
        assert '&lt;b&gt;&quot;Glo&quot;&lt;/b&gt;' in html
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest -q tests/test_publish.py -k "graphic or names_nothing or markup"`
Expected: the first, third and fourth FAIL (`Large Sebenza 21` without the graphic). The gated and escape tests may already pass; they are guards.

- [ ] **Step 3: Use `full_name` on the generated pages**

In `bb/publish.py` `_knife_page`, replace:

```python
    name = display_name(row)
    if gated:
        # chat-preview link unfurls are the accidental-leak channel the key
        # gate exists for — keep the model/edition out of <title>/og:title.
        title = f'{row["tag"]} — blade-book'
    else:
        title_bits = [name] + ([row['special_edition']] if row.get('special_edition') else [])
        title = ' · '.join(title_bits) + f' — @{handle}'
```

with:

```python
    name = full_name(row)
    if gated:
        # chat-preview link unfurls are the accidental-leak channel the key
        # gate exists for — keep the model/edition out of <title>/og:title.
        title = f'{row["tag"]} — blade-book'
    else:
        title = f'{name} — @{handle}'
```

In `_index_html`, change both `display_name(` calls (`fname = display_name(hero_row)` and `name = display_name(row)`) to `full_name(`.

- [ ] **Step 4: Use `full_name` in search and the board**

`bb/search.py`: line 35 `c['name'] = publish.full_name(row)`; line 71 `text = f"{text} {handle} {publish.full_name(row)}"`.
`bb/board.py`: line 31 `c['name'] = publish.full_name(row)`.

- [ ] **Step 5: Run the suite and fix pinned expectations**

Run: `python3 -m pytest -q | tail -15`

Any test that fails now does so because its fixture has a graphic, an edition, or (for another maker) a variant, and it pinned the short name. For each, change the expected string to the full name. Do not change `full_name`. Known candidates: tests that assert a `<title>` containing `' · '` before the edition.

Expected after fixes: all pass.

- [ ] **Step 6: Commit**

```bash
git add bb/publish.py bb/search.py bb/board.py tests/
git commit -m "names carry the graphic: knife page, register, search, board"
```

---

### Task 3: The kit we already know

**Files:**
- Modify: `bb/publish.py` (`PUBLIC_EXT` neighbourhood, `public_row`, `_knife_page`, `_STYLE`)
- Test: `tests/test_publish.py`

**Interfaces:**
- Produces: `publish.PUBLIC_KIT` (tuple of `(column, label)`), and `row['kit']`: a list of labels, in `PUBLIC_KIT` order, for columns whose value is `1`. `_knife_page` renders it as `<p class="kit">`.

- [ ] **Step 1: Write the failing tests**

```python
# --- the kit (2026-09-27): what came with the knife, known "yes" only ---

def test_public_row_kit_lists_only_what_is_known_to_be_there():
    k = _knife(has_box=1, has_card=1, has_papers=0, has_pouch=None, has_lanyard=1,
               has_spare_hardware=None)
    assert publish.public_row(k, USER)['kit'] == ['BOX', 'CARD', 'LANYARD']
    assert publish.public_row(_knife(), USER)['kit'] == []      # columns absent: nothing claimed


def test_knife_page_shows_the_kit_as_chips():
    row = publish.public_row(_knife(has_box=1, has_card=1, has_lanyard=1), USER)
    html = publish._knife_page(row, 'simon-collector', gated=False)
    assert ('<p class="kit"><b>CAME WITH</b><span>BOX</span><span>CARD</span>'
            '<span>LANYARD</span></p>') in html


def test_knife_page_without_a_kit_has_no_heading():
    html = publish._knife_page(publish.public_row(_knife(), USER), 'simon-collector', gated=False)
    assert 'CAME WITH' not in html and 'class="kit"' not in html
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest -q tests/test_publish.py -k kit`
Expected: FAIL with `KeyError: 'kit'` and a missing `<p class="kit">`.

- [ ] **Step 3: Implement**

In `bb/publish.py`, after `PUBLIC_EXT`:

```python
# The kit: what came with the knife. Only a known "yes" is ever published —
# a missing chip never says the knife lacks something (spec 1.6).
PUBLIC_KIT = (('has_box', 'BOX'), ('has_card', 'CARD'), ('has_papers', 'PAPERS'),
              ('has_pouch', 'POUCH'), ('has_lanyard', 'LANYARD'),
              ('has_spare_hardware', 'SPARE HARDWARE'))
```

In `public_row`, before `return row`:

```python
    row['kit'] = [label for col, label in PUBLIC_KIT if k.get(col) == 1]
```

In `_knife_page`, right after the line `out += f'<table>{"".join(specs)}</table>\n'`:

```python
    if row.get('kit'):
        out += ('<p class="kit"><b>CAME WITH</b>'
                + ''.join(f'<span>{e(x)}</span>' for x in row['kit']) + '</p>\n')
```

In `_STYLE`, before the closing `'''`:

```python
  .kit { display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin:10px 0; }
  .kit b { font-size:.72rem; letter-spacing:.14em; color:#555; margin-right:4px; }
  .kit span { font-size:.72rem; font-weight:700; letter-spacing:.06em;
              border:1.5px solid var(--ink,#141210); border-radius:999px; padding:.18rem .55rem; }
```

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest -q | tail -2`
Expected: all pass, including `tests/test_publish_leak.py` and `tests/test_search_leak.py`.

- [ ] **Step 5: Commit**

```bash
git add bb/publish.py tests/test_publish.py
git commit -m "the kit: a public knife page shows what came with it (known yes only)"
```

---

### Task 4: The bar's CSS and the asset version

**Files:**
- Modify: `html/vibe.css`; every file that contains `?v=20260904` (16 html pages, `bb/publish.py`, `tests/test_deploy_files.py`, `tests/test_publish_bundle.py`)
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Produces: CSS class `.bb-top` with children `.brand`, `.whose`, `nav`, `nav a.bb-auth`. Asset version string `?v=20260927`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_deploy_files.py`)

```python
def test_vibe_css_has_the_top_bar_rules():
    css = _read('html/vibe.css')
    for sel in ('.bb-top{', '.bb-top .brand{', '.bb-top .whose{', '.bb-top nav{',
                '.bb-top nav a{', '.bb-top nav a.bb-auth{'):
        assert sel in css, sel
    assert '@media (max-width:640px){.bb-top .whose{display:none}' in css


def test_every_page_asks_for_the_current_assets():
    import subprocess
    old = subprocess.run(['grep', '-rl', 'v=20260904', 'html', 'bb'], cwd=ROOT,
                         capture_output=True, text=True).stdout.split()
    assert old == [], old
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest -q tests/test_deploy_files.py -k "top_bar_rules or current_assets"`
Expected: both FAIL.

- [ ] **Step 3: Add the rules** (append to `html/vibe.css`)

```css
/* the top bar (2026-09-27): one nav on every public page */
.bb-top{display:flex;align-items:center;gap:14px;padding:10px 16px;background:#fff;border-bottom:2px solid var(--ink);text-align:left}
.bb-top .brand{display:flex;align-items:center;gap:7px;font-family:'Bebas Neue',Impact,sans-serif;font-size:1.35rem;letter-spacing:.04em;font-weight:400;color:var(--ink);text-decoration:none}
.bb-top .brand img{width:22px;height:22px}
.bb-top .whose{font-size:.86rem;font-weight:400;color:var(--muted);text-decoration:none;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
.bb-top .whose b{color:var(--ink)}
.bb-top nav{margin-left:auto;display:flex;gap:14px;align-items:center;font-size:.86rem;white-space:nowrap}
.bb-top nav a{color:var(--ink);font-weight:400;text-decoration:none}
.bb-top nav a.bb-auth{border:2px solid var(--ink);border-radius:999px;padding:.25rem .7rem;font-weight:700}
@media (max-width:640px){.bb-top .whose{display:none}.bb-top nav{gap:10px;font-size:.8rem}}
```

- [ ] **Step 4: Bump the version everywhere**

```bash
grep -rl 'v=20260904' html bb tests | xargs sed -i 's/v=20260904/v=20260927/g'
grep -rn '20260904' html bb tests
```

Expected: the second command prints nothing.

- [ ] **Step 5: Run the suite**

Run: `python3 -m pytest -q | tail -2`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A html bb tests
git commit -m "vibe.css: the top bar's rules; assets to v=20260927"
```

---

### Task 5: `_topbar`, and the register hero gains search and the board

**Files:**
- Modify: `bb/publish.py` (new `_topbar` after `_foot`; `_index_html`; `_INDEX_STYLE`)
- Test: `tests/test_publish.py`, `tests/test_publish_bundle.py:84`

**Interfaces:**
- Produces: `publish._topbar(handle=None, count=None) -> str`. With no arguments it returns the exact markup the static pages carry (Task 7 compares against it). With a handle it adds the "whose register" link.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_publish.py`)

```python
# --- one top bar (2026-09-27) ---

def test_topbar_goes_home_search_board_sign_in_in_that_order():
    bar = publish._topbar()
    assert bar.startswith('<div class="bb-top">') and bar.rstrip().endswith('</div>')
    pos = [bar.index(n) for n in ('class="brand" href="/blade-book/"',
                                  'href="/blade-book/search/">search<',
                                  'href="/blade-book/board/">the board<',
                                  'class="bb-auth" href="/blade-book/me/">sign in<')]
    assert pos == sorted(pos)
    assert 'whose' not in bar and bar.count('bb-auth') == 1


def test_topbar_on_a_register_says_whose_it_is():
    bar = publish._topbar('simon-collector', 75)
    assert ('<a class="whose" href="/blade-book/@simon-collector/">in <b>@simon-collector</b>’s '
            'register · 75 knives</a>') in bar
    assert '· 1 knife</a>' in publish._topbar('simon-collector', 1)


def test_register_hero_offers_search_and_the_board_beside_sign_in():
    html = publish._index_html([_glorious_row()], USER, gated=False)
    assert ('<p class="signin"><a href="/blade-book/search/">search</a>'
            '<a href="/blade-book/board/">the board</a>'
            '<a class="bb-auth" href="/blade-book/me/">sign in</a></p>') in html
    css = publish._INDEX_STYLE
    assert '.hero .signin a.bb-auth {' in css          # only sign-in wears the border
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest -q tests/test_publish.py -k "topbar or register_hero_offers"`
Expected: FAIL with `AttributeError: module 'bb.publish' has no attribute '_topbar'` and a missing string.

- [ ] **Step 3: Implement `_topbar`** (in `bb/publish.py`, after `_foot`)

```python
def _topbar(handle=None, count=None):
    """The one top bar (2026-09-27). With no arguments this is, byte for byte,
    the markup the static pages under html/ carry — tests/test_deploy_files.py
    holds them to it. Styled by `.bb-top` in vibe.css. The mark is an <img>
    here (not the inline svg) so static and generated pages can match exactly."""
    e = html_mod.escape
    whose = ''
    if handle:
        n = '' if count is None else f' · {int(count)} {"knives" if count != 1 else "knife"}'
        whose = (f'<a class="whose" href="/blade-book/@{e(handle)}/">in <b>@{e(handle)}</b>’s '
                 f'register{n}</a>')
    return ('<div class="bb-top"><a class="brand" href="/blade-book/">'
            '<img src="/blade-book/mark.svg" alt="" width="22" height="22">BLADE-BOOK</a>'
            + whose +
            '<nav aria-label="blade-book"><a href="/blade-book/search/">search</a>'
            '<a href="/blade-book/board/">the board</a>'
            '<a class="bb-auth" href="/blade-book/me/">sign in</a></nav></div>\n')
```

- [ ] **Step 4: The register hero**

In `_index_html`, replace the `<p class="signin">…</p>` string with:

```python
    out += ('<p class="signin"><a href="/blade-book/search/">search</a>'
            '<a href="/blade-book/board/">the board</a>'
            '<a class="bb-auth" href="/blade-book/me/">sign in</a></p>\n'
```

(the rest of that expression, from `f'<a class="home" …` on, is unchanged).

In `_INDEX_STYLE`, replace the four `.signin` rules with:

```css
  .hero .signin { position:absolute; top:14px; right:18px; margin:0; z-index:3; color:#fff;
                  display:flex; gap:14px; align-items:center; }
  .hero .signin a { color:#fff; font-size:.85rem; font-weight:400; text-decoration:none;
                    text-shadow:0 1px 8px rgba(0,0,0,.6); }
  .hero .signin a.bb-auth { border:2px solid rgba(255,255,255,.85); border-radius:10px; padding:6px 12px;
                            font-weight:700; background:rgba(0,0,0,.25); text-shadow:none; }
  .hero.plain .signin a { color:var(--accent,#b8452c); text-shadow:none; }
  .hero.plain .signin a.bb-auth { border-color:var(--accent,#b8452c); background:none; }
```

The first rule must still begin `.hero .signin { position:absolute; top:14px; right:18px` — an existing test pins that prefix.

- [ ] **Step 5: Update the one pinned string**

`tests/test_publish_bundle.py` line 84: replace the expected `<p class="signin">…` string with the new three-link markup from Step 4.

- [ ] **Step 6: Run the suite**

Run: `python3 -m pytest -q | tail -2`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add bb/publish.py tests/
git commit -m "one top bar: _topbar(), and the register hero offers search and the board"
```

---

### Task 6: The knife page — bar, lead, flip, by-line, strip

**Files:**
- Modify: `bb/publish.py` (`_knife_page` rewritten; new `_neighbours`, `_tile`, `_KNIFE_STYLE`, `_FLIP_SCRIPT`, `MORE_TILES`; `build_user` passes `nav`)
- Test: `tests/test_publish.py`, `tests/test_publish_bundle.py`

**Interfaces:**
- Consumes: `_topbar(handle, count)`, `full_name(row)`, `row['kit']`.
- Produces:
  - `publish.MORE_TILES = 5`
  - `publish._neighbours(rows, i) -> dict` with keys `pos` (1-based int), `total` (int), `prev` (row or None), `next` (row or None), `more` (list of rows, at most `MORE_TILES`, never the knife itself).
  - `publish._knife_page(row, handle, gated, nav=None) -> str`. `nav=None` means a register of one.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_publish.py`)

```python
# --- the knife page (2026-09-27): look, check, wander, want one ---

def _shelf(n):
    rows = []
    for i in range(1, n + 1):
        k = _glorious()
        k['tag'] = f'K{i:02d}'
        r = publish.public_row(k, USER)
        r['img'], r['img_t'] = f'K{i:02d}.jpg', f'K{i:02d}_t.jpg'
        rows.append(r)
    return rows


def test_neighbours_follow_register_order_and_do_not_wrap():
    rows = _shelf(8)
    first, mid, last = (publish._neighbours(rows, i) for i in (0, 3, 7))
    assert first['prev'] is None and first['next']['tag'] == 'K02'
    assert mid['prev']['tag'] == 'K03' and mid['next']['tag'] == 'K05'
    assert last['next'] is None and last['prev']['tag'] == 'K07'
    assert (mid['pos'], mid['total']) == (4, 8)


def test_the_strip_is_the_next_five_and_wraps_to_stay_full():
    rows = _shelf(8)
    assert [r['tag'] for r in publish._neighbours(rows, 0)['more']] == ['K02', 'K03', 'K04', 'K05', 'K06']
    assert [r['tag'] for r in publish._neighbours(rows, 6)['more']] == ['K08', 'K01', 'K02', 'K03', 'K04']
    assert [r['tag'] for r in publish._neighbours(_shelf(3), 1)['more']] == ['K03', 'K01']


def test_a_register_of_one_has_no_arrows_and_no_strip():
    rows = _shelf(1)
    nav = publish._neighbours(rows, 0)
    assert nav == {'pos': 1, 'total': 1, 'prev': None, 'next': None, 'more': []}
    html = publish._knife_page(rows[0], 'simon-collector', gated=False, nav=nav)
    assert 'class="flip' not in html and 'class="more"' not in html
    assert '<span class="count">K01 · 1 of 1</span>' in html


def test_knife_page_opens_with_the_bar_and_flips_both_ways():
    rows = _shelf(8)
    html = publish._knife_page(rows[3], 'simon-collector', gated=False, nav=publish._neighbours(rows, 3))
    body = html.split('<body>')[1]
    assert body.lstrip().startswith('<div class="bb-top">')
    assert 'in <b>@simon-collector</b>’s register · 8 knives' in body
    assert '<a class="flip prev" rel="prev" href="../K03/"' in body
    assert '<a class="flip next" rel="next" href="../K05/"' in body
    assert '<span class="count">K04 · 4 of 8</span>' in body
    assert 'born February 6, 2014 · in <a href="../">@simon-collector</a>’s register' in body
    strip = body.split('class="more"')[1]
    assert strip.count('class="tile"') == 5 and 'href="../K05/"' in strip and '../img/K05_t.jpg' in strip
    assert 'Your knives deserve a page like this.' in body and 'href="/blade-book/how/"' in body


def test_a_knife_with_no_photo_still_flips():
    rows = _shelf(3)
    rows[1]['img'] = rows[1]['img_t'] = None
    html = publish._knife_page(rows[1], 'simon-collector', gated=False, nav=publish._neighbours(rows, 1))
    assert '<div class="lead plain">' in html and '<img class="hero"' not in html
    assert 'rel="prev" href="../K01/"' in html and 'rel="next" href="../K03/"' in html


def test_a_neighbour_with_no_thumb_gets_a_placeholder_not_a_broken_image():
    rows = _shelf(3)
    rows[2]['img'] = rows[2]['img_t'] = None
    html = publish._knife_page(rows[0], 'simon-collector', gated=False, nav=publish._neighbours(rows, 0))
    tile = html.split('href="../K03/"')[-1].split('</a>')[0]
    assert '<span class="noimg"></span>' in tile and '<img' not in tile


def test_flip_labels_and_tiles_escape_names():
    rows = _shelf(3)
    rows[1]['graphic_name'] = '<b>"Glo"</b>'
    html = publish._knife_page(rows[0], 'simon-collector', gated=False, nav=publish._neighbours(rows, 0))
    assert '<b>"Glo"</b>' not in html and '&lt;b&gt;&quot;Glo&quot;&lt;/b&gt;' in html


def test_the_flip_script_only_follows_links_that_exist():
    js = publish._FLIP_SCRIPT
    assert 'a.flip.prev' in js and 'a.flip.next' in js
    assert 'innerHTML' not in js and 'fetch(' not in js and 'eval' not in js
```

And in `tests/test_publish_bundle.py`:

```python
def test_build_user_wires_each_knife_page_to_its_neighbours(con, tmp_path):
    user, st, k = _setup(con, tmp_path)
    k2 = db.create_draft_knife(con, user['id'])
    con.execute("UPDATE knives SET confidence = '{}', model = 'Mnandi' WHERE id = ?", (k2['id'],))
    con.commit()
    db.publish_knife(con, user['id'], k2['id'])
    db.set_public(con, user['id'], [k2['id']], True)
    publish.build_user(con, user, st)
    d = publish.bundle_dir('bundle-guy')
    t1 = db.get_knife(con, user['id'], k['id'])['tag']
    t2 = db.get_knife(con, user['id'], k2['id'])['tag']
    p1 = open(os.path.join(d, t1, 'index.html')).read()
    p2 = open(os.path.join(d, t2, 'index.html')).read()
    assert f'rel="next" href="../{t2}/"' in p1 and 'rel="prev"' not in p1
    assert f'rel="prev" href="../{t1}/"' in p2 and 'rel="next"' not in p2
    assert '· 2 knives' in p1
```

- [ ] **Step 2: Run them and watch them fail**

Run: `python3 -m pytest -q tests/test_publish.py tests/test_publish_bundle.py -k "neighbours or strip or register_of_one or flips or no_photo or no_thumb or flip_ or wires_each"`
Expected: FAIL with `AttributeError: … '_neighbours'`, `'_FLIP_SCRIPT'`, and `TypeError: _knife_page() got an unexpected keyword argument 'nav'`.

- [ ] **Step 3: Add the helpers, style and script** (in `bb/publish.py`, above `def _knife_page`)

```python
MORE_TILES = 5


def _neighbours(rows, i):
    """Where knife `i` sits on the shelf. Arrows follow register order and stop
    at the ends; the strip is the next few knives and wraps so it stays full."""
    n = len(rows)
    more = [rows[(i + d) % n] for d in range(1, min(MORE_TILES, n - 1) + 1)]
    return {'pos': i + 1, 'total': n,
            'prev': rows[i - 1] if i > 0 else None,
            'next': rows[i + 1] if i + 1 < n else None,
            'more': more}


def _tile(row, prefix):
    e = html_mod.escape
    img = (f'<img src="{prefix}img/{e(row["img_t"])}" alt="" loading="lazy">' if row.get('img_t')
           else '<span class="noimg"></span>')
    return (f'<a class="tile" href="{prefix}{e(row["tag"])}/">{img}'
            f'<span><b>{e(row["tag"])}</b>{e(full_name(row))}</span></a>')


_KNIFE_STYLE = '''
  main { max-width:920px; }
  .lead { position:relative; margin:10px 0; }
  .lead img.hero { display:block; margin:0; }
  .lead .count { position:absolute; left:50%; top:12px; transform:translateX(-50%);
                 background:rgba(21,19,15,.72); color:#faf6ee; font-size:.72rem;
                 letter-spacing:.14em; padding:.3rem .7rem; border-radius:999px; white-space:nowrap; }
  a.flip { position:absolute; top:50%; transform:translateY(-50%); width:44px; height:60px;
           border-radius:12px; background:rgba(250,246,238,.92); color:var(--ink,#141210);
           font-size:1.6rem; display:grid; place-items:center; box-shadow:0 4px 14px rgba(0,0,0,.35); }
  a.flip.prev { left:10px; }
  a.flip.next { right:10px; }
  .lead.plain { display:flex; justify-content:space-between; align-items:center; gap:10px; min-height:60px; }
  .lead.plain .count, .lead.plain a.flip { position:static; transform:none; }
  .by { color:#555; margin:.2rem 0 .8rem; }
  .more h2 { font-family:'Bebas Neue',Impact,sans-serif; font-weight:400; letter-spacing:.04em;
             font-size:1.3rem; margin:26px 0 10px; display:flex; justify-content:space-between;
             align-items:baseline; }
  .more h2 a { font-family:'DM Sans',sans-serif; font-size:.84rem; }
  .strip { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(150px,1fr); gap:10px;
           overflow-x:auto; padding-bottom:6px; }
  a.tile { display:block; border:2px solid var(--ink,#141210); border-radius:12px; overflow:hidden;
           background:#fff; color:var(--ink,#141210); font-weight:400; }
  a.tile img, a.tile .noimg { display:block; width:100%; aspect-ratio:4/3; object-fit:cover;
                              background:#15130f; }
  a.tile span { display:block; padding:7px 9px 9px; font-size:.8rem; line-height:1.25; }
  a.tile b { color:var(--accent,#b8452c); margin-right:5px; }
  .join { background:var(--ink,#141210); color:#faf6ee; border-radius:14px; padding:16px 18px;
          margin:24px 0 4px; display:flex; gap:14px; align-items:center;
          justify-content:space-between; flex-wrap:wrap; }
  .join b { display:block; font-family:'Bebas Neue',Impact,sans-serif; font-weight:400;
            font-size:1.5rem; letter-spacing:.03em; }
  .join span { font-size:.86rem; color:#cfc8b8; }
  .join a.cta { margin:0; }
  @media (max-width: 699px) { a.flip { width:38px; height:54px; } }
'''

# Swipe and arrow keys are conveniences only: both follow the prev/next links
# already on the page, so the page flips with JavaScript off.
_FLIP_SCRIPT = """
(function () {
  var p = document.querySelector('a.flip.prev'), n = document.querySelector('a.flip.next');
  var x = null, y = null;
  function go(a) { if (a) location.href = a.href; }
  document.addEventListener('keydown', function (ev) {
    if (ev.target && /INPUT|TEXTAREA|SELECT/.test(ev.target.tagName)) return;
    if (ev.key === 'ArrowLeft') go(p);
    if (ev.key === 'ArrowRight') go(n);
  });
  var lead = document.querySelector('.lead');
  if (!lead) return;
  lead.addEventListener('touchstart', function (ev) {
    x = ev.touches[0].clientX; y = ev.touches[0].clientY;
  }, { passive: true });
  lead.addEventListener('touchend', function (ev) {
    if (x === null) return;
    var dx = ev.changedTouches[0].clientX - x, dy = ev.changedTouches[0].clientY - y;
    x = null;
    if (Math.abs(dx) > 60 && Math.abs(dx) > 2 * Math.abs(dy)) go(dx < 0 ? n : p);
  }, { passive: true });
})();
"""
```

- [ ] **Step 4: Rewrite `_knife_page`**

Replace the whole function with:

```python
def _knife_page(row, handle, gated, nav=None):
    e = html_mod.escape
    name = full_name(row)
    nav = nav or {'pos': 1, 'total': 1, 'prev': None, 'next': None, 'more': []}
    if gated:
        # chat-preview link unfurls are the accidental-leak channel the key
        # gate exists for — keep the model/edition out of <title>/og:title.
        title = f'{row["tag"]} — blade-book'
    else:
        title = f'{name} — @{handle}'
    desc_bits = [b for b in (
        row.get('blade_steel'),
        ' '.join(x for x in (row.get('damascus_smith'), row.get('damascus_pattern')) if x) or None,
        row.get('inlay_material'),
        f"born {row['born']}" if row.get('born') else None) if b]
    desc = '' if gated else (' · '.join(desc_bits) or 'From a private register on blade-book.')
    og_image = (f"{_public_base()}/@{handle}/img/{row['img']}" if row.get('img') and not gated else '')
    out = _head(title, desc, og_image, noindex=gated, extra_style=_KNIFE_STYLE,
                url=f"{_public_base()}/@{handle}/{row['tag']}/")
    out += _topbar(handle, nav['total']) + '<main>\n'

    def flip(rel, r, glyph, word):
        if not r:
            return '<span></span>' if not row.get('img') else ''
        return (f'<a class="flip {rel}" rel="{rel}" href="../{e(r["tag"])}/" '
                f'aria-label="{word} knife: {e(r["tag"])} {e(full_name(r))}">{glyph}</a>')

    prev_a = flip('prev', nav['prev'], '‹', 'previous')
    next_a = flip('next', nav['next'], '›', 'next')
    count = f'<span class="count">{e(row["tag"])} · {nav["pos"]} of {nav["total"]}</span>'
    if row.get('img'):
        out += (f'<div class="lead"><img class="hero" src="../img/{e(row["img"])}" alt="{e(name)}">'
                f'{count}{prev_a}{next_a}</div>\n')
    else:
        out += f'<div class="lead plain">{prev_a}{count}{next_a}</div>\n'
    out += f'<p class="tag">{e(row["tag"])}</p>\n<h1>{e(name)}</h1>\n'
    born = f'born {e(row["born"])} · ' if row.get('born') else ''
    out += f'<p class="by">{born}in <a href="../">@{e(handle)}</a>’s register</p>\n'
    if row.get('for_sale'):
        price = f" · ${row['asking_price']:g}" if row.get('asking_price') else ''
        out += f'<p class="sale">FOR SALE{price}</p>\n'
        if row.get('seller_note'):
            out += f'<p>{e(row["seller_note"])}</p>\n'
    elif row.get('for_trade'):
        out += '<p class="sale">FOR TRADE</p>\n'
    specs = []

    def spec(label, value):
        if value:
            specs.append(f'<tr><th>{e(label)}</th><td>{e(str(value))}</td></tr>')

    spec('Maker', row.get('maker_name'))
    spec('Born on', row.get('born'))
    spec('Blade', row.get('blade_shape'))
    spec('Steel', row.get('blade_steel'))
    spec('Damascus', ' '.join(x for x in (row.get('damascus_smith'),
                                          row.get('damascus_pattern')) if x))
    spec('Treatment', row.get('handle_treatment'))
    spec('Inlay', row.get('inlay_material'))
    spec('Graphic / edition', row.get('graphic_name') or row.get('special_edition'))
    spec('Variant', row.get('variant'))
    out += f'<table>{"".join(specs)}</table>\n'
    if row.get('kit'):
        out += ('<p class="kit"><b>CAME WITH</b>'
                + ''.join(f'<span>{e(x)}</span>' for x in row['kit']) + '</p>\n')
    if row.get('notes_public'):
        out += f'<div class="card">{e(row["notes_public"])}</div>\n'
    if nav['more']:
        out += (f'<section class="more"><h2>More from this register '
                f'<a href="../">all {nav["total"]} →</a></h2>\n<div class="strip">'
                + ''.join(_tile(r, '../') for r in nav['more']) + '</div></section>\n')
    out += ('<div class="join"><div><b>Your knives deserve a page like this.</b>'
            '<span>One photo of the knife with its card. We read the card.</span></div>'
            '<a class="cta" href="/blade-book/how/">START YOUR REGISTER</a></div>\n'
            + _foot() + '</main>\n'
            f'<script>{_FLIP_SCRIPT}</script>\n')
    out += _gate_snippet('../') if gated else ''
    return out + '</body>\n</html>\n'
```

- [ ] **Step 5: Pass `nav` from `build_user`**

In `build_user`, replace:

```python
                for row in rows:
                    page_dir = os.path.join(tmp, row['tag'])
                    os.makedirs(page_dir)
                    with open(os.path.join(page_dir, 'index.html'), 'w') as f:
                        f.write(_knife_page(row, handle, gated))
```

with:

```python
                for i, row in enumerate(rows):
                    page_dir = os.path.join(tmp, row['tag'])
                    os.makedirs(page_dir)
                    with open(os.path.join(page_dir, 'index.html'), 'w') as f:
                        f.write(_knife_page(row, handle, gated, nav=_neighbours(rows, i)))
```

- [ ] **Step 6: Run the suite and fix pinned expectations**

Run: `python3 -m pytest -q | tail -15`

Expected failures are tests that pinned the old page furniture. Update each expectation, never the page:

| Old expectation | New expectation |
|---|---|
| `'Keep a register like this' in page` (`tests/test_publish_bundle.py:85`) | `'Your knives deserve a page like this.' in page` (the register index keeps the old line) |
| `← @handle’s register` back link | the by-line: `in <a href="../">@handle</a>’s register` |
| `html.count('class="bb-auth"') == 1` on a knife page, if any | `== 2` (bar and footer) |

Expected after fixes: all pass.

- [ ] **Step 7: Commit**

```bash
git add bb/publish.py tests/
git commit -m "the knife page: top bar, next/previous, by-line, more from this register"
```

---

### Task 7: The top bar on the static pages

**Files:**
- Modify: `html/search/index.html`, `html/board/index.html`, `html/how/index.html`, `html/faq/index.html`, `html/about/index.html`, `html/terms/index.html`, `html/vibe.css`
- Test: `tests/test_deploy_files.py`

**Interfaces:**
- Consumes: `publish._topbar()` (no arguments) — the exact markup to paste.

Each of the six pages styles `body{max-width:…;margin:0 auto;padding:16px 16px 60px}`. A bar inside that body would be boxed in. So the body's box moves to a wrapper, and the bar sits outside it.

- [ ] **Step 1: Write the failing tests** (in `tests/test_deploy_files.py`)

Replace the whole of `test_board_carries_a_top_strip_too` with:

```python
TOP_BAR_PAGES = ('board/index.html', 'search/index.html', 'how/index.html',
                 'faq/index.html', 'about/index.html', 'terms/index.html')


def test_public_pages_share_the_top_bar():
    """One bar (2026-09-27), byte for byte what publish._topbar() writes on the
    generated pages — so the static and generated halves cannot drift."""
    from bb import publish
    bar = publish._topbar().strip()
    for rel in TOP_BAR_PAGES:
        html = _read('html/' + rel)
        assert html.count(bar) == 1, rel
        body = html[html.index('<body'):]
        assert body.index(bar) < body.index('<div class="page">') < body.index('<header>'), rel
        assert html.count('class="bb-auth"') == 2, rel          # bar + footer; nav.js flips both
        assert 'body{max-width' not in html.replace(' ', ''), rel   # the box moved to .page
    for rel in PUBLIC_PAGES:
        assert 'bb-strip' not in _read('html/' + rel), rel      # the board's old strip is retired
    assert '.bb-strip' not in _read('html/vibe.css')
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 -m pytest -q tests/test_deploy_files.py::test_public_pages_share_the_top_bar`
Expected: FAIL at `html.count(bar) == 1` for `board/index.html`.

- [ ] **Step 3: Edit the six pages**

Print the markup to paste:

```bash
python3 -c "from bb import publish; print(publish._topbar(), end='')"
```

For each of the six files:

1. In its `<style>`, change `body{max-width:NNNpx;margin:0 auto;padding:16px 16px 60px}` to:

```css
  body{margin:0}
  .page{max-width:NNNpx;margin:0 auto;padding:16px 16px 60px}
```

   keeping that page's own `NNN` (search 1000, board 640, the other four 560).

2. Directly after the `<body…>` tag, paste the bar on its own line, then open the wrapper:

```html
<div class="page">
```

3. Directly before `</body>`, close it:

```html
</div>
```

   Any `<script>` tags that sit between the footer and `</body>` stay inside the wrapper; order is unchanged.

4. `html/board/index.html` only: delete the whole `<nav class="bb-strip" …>…</nav>` line.

In `html/vibe.css`, delete the three `.bb-strip` rules.

The landing (`html/index.html`) is left as it is: its hero nav already offers search, the board and sign in, plus "how it works", which a first-time visitor needs.

- [ ] **Step 4: Run the suite**

Run: `python3 -m pytest -q | tail -5`
Expected: all pass. `test_public_pages_share_the_footer_strip` must still pass untouched: the footer did not move.

- [ ] **Step 5: Commit**

```bash
git add html tests/test_deploy_files.py
git commit -m "the top bar on search, board, how, faq, about, terms; the board's strip retires"
```

---

### Task 8: Look, then ship

**Files:**
- Create: `scripts/deploy_page.sh`
- Test: a browser run against a private preview

- [ ] **Step 1: Build a preview from a copy of the database**

```bash
PV=<session scratchpad>/pagepv
rm -rf "$PV" && mkdir -p "$PV/data" "$PV/log" "$PV/root" && chmod 700 "$PV"
python3 - "$PV/data/blade-book.db" <<'EOF'
import sqlite3, sys
src = sqlite3.connect('file:/var/lib/blade-book/blade-book.db?mode=ro', uri=True)
dst = sqlite3.connect(sys.argv[1]); src.backup(dst); dst.close(); src.close()
EOF
ln -s /var/lib/blade-book/photos "$PV/data/photos"
cd /home/shg/blade-book-wt-page
cp -r html/. "$PV/root/"
ln -s . "$PV/root/blade-book"            # pages link /blade-book/…; serve both roots
BLADEBOOK_DATA_DIR="$PV/data" BLADEBOOK_WWW_DIR="$PV/root" BLADEBOOK_LOG_DIR="$PV/log" \
  PLATE_BASE=blade-book.com python3 scripts/publish_sweep.py --all
python3 -m http.server 8765 --bind 127.0.0.1 --directory "$PV/root" >/dev/null 2>&1 &
```

Expected: `rebuilt 5 bundles`. The live database and `/var/www` are not touched.

- [ ] **Step 2: Screenshot and measure, desktop and phone**

With the scratch Playwright venv, at 1440×900 and 390×844, open:

- `http://127.0.0.1:8765/@simon-collector/K80/` (middle of the shelf)
- `http://127.0.0.1:8765/@simon-collector/K01/` (first: no previous arrow)
- `http://127.0.0.1:8765/@simon-collector/` (register hero with three links)
- `http://127.0.0.1:8765/@simonhg/` and its one knife page (a register of one)
- `http://127.0.0.1:8765/search/`, `/board/`, `/how/`, `/faq/`, `/about/`, `/terms/`

For every page and both sizes assert in the browser:

```js
document.documentElement.scrollWidth <= window.innerWidth
```

and read every screenshot. Click the next arrow on K80 and confirm the URL ends `/K81/` (or the next public tag). The API is not running in the preview, so search and the board show no cards; judge their bar and layout only.

Expected: no sideways scroll anywhere; the bar reads the same on all pages.

- [ ] **Step 3: Stop the preview and remove it**

```bash
pkill -f "[h]ttp.server 8765"; rm -rf "$PV"
```

- [ ] **Step 4: Write the deploy script** (`scripts/deploy_page.sh`, mode 755)

```bash
#!/bin/bash
# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
# 2026-09-27: the knife page — names with the graphic, one top bar, next and
# previous, the kit, the plate-only mark. Static pages, the app and the public
# bundles go live together.
# Simon: `! sudo bash /home/shg/blade-book/scripts/deploy_page.sh`
set -euo pipefail
CODE=/home/shg/blade-book; WWW=/var/www/html/blade-book
sudo -u shg -H cp -r "$CODE/html/." "$WWW/"
supervisorctl restart blade_book && sleep 1 && curl -s http://127.0.0.1:5004/blade-book/api/healthz; echo
# the sweep runs as shg: root's python has no app deps and would write root-owned files
sudo -u shg -H bash -c "cd $CODE && python3 scripts/publish_sweep.py --all"
page=$(curl -s "https://blade-book.com/@simon-collector/K80/?fresh=$(date +%s)")
for needle in 'class="bb-top"' 'rel="next"' 'Large Sebenza 21 — Glorious' 'CAME WITH'; do
  if grep -q "$needle" <<<"$page"; then echo "ok   $needle"; else echo "MISSING $needle"; fi
done
echo 'Browsers and Cloudflare keep old pages and photos for up to 4 h.'
```

Check it parses: `bash -n scripts/deploy_page.sh`.

- [ ] **Step 5: Full suite, merge, push**

```bash
cd /home/shg/blade-book-wt-page && python3 -m pytest -q | tail -2
git add scripts/deploy_page.sh && git commit -m "deploy script for the knife page"
cd /home/shg/blade-book && git merge --ff-only knife-page
git worktree remove /home/shg/blade-book-wt-page && git branch -d knife-page
python3 -m pytest -q | tail -2 && git push
```

Expected: all pass, both times.

- [ ] **Step 6: Simon deploys**

Simon runs: `! sudo bash /home/shg/blade-book/scripts/deploy_page.sh`
Expected: four `ok` lines.

- [ ] **Step 7: Verify live**

Drive the real site headless, desktop and phone: K80's page, the register, search, the board. Confirm the bar, the arrows, the kit chips and the plate-only photo (no lettering over the knife). Report what was seen, including anything that looks wrong.

Rollback, if Simon does not like how it feels: `git revert` the merge range, or check out tag `v1.1-pre-page-2026-09-27`, then rerun the deploy script.
