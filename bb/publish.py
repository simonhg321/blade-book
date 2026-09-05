# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/publish.py — the public projection (spec §8): a static bundle per collector
at WWW_DIR/@{handle}/, regenerated on save (debounced 30 s). Built ONLY from
PUBLIC_FIELDS below — the whitelist is the security boundary, and
tests/test_publish_leak.py proves nothing in db.PRIVATE_COLUMNS survives it.
The DB never sees public traffic; Apache serves the files.
"""
import fcntl
import hashlib
import html as html_mod
import io
import json
import logging
import os
import re
import shutil
import threading
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from bb import auth, db, paths

SAFE_HANDLE = re.compile(r'[a-z0-9-]{3,24}')

# spec §8 whitelist, verbatim — core columns and (for crk) ext keys.
PUBLIC_FIELDS = ('tag', 'maker', 'model', 'variant', 'blade_steel', 'blade_shape',
                 'born_on', 'born_on_precision', 'notes_public')
PUBLIC_EXT = ('generation', 'size', 'handle_treatment', 'graphic_name',
              'inlay_material', 'damascus_smith', 'damascus_pattern',
              'special_edition')

_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December']


def _fmt_born(d, precision):
    if not d:
        return ''
    parts = d.split('-')
    y = parts[0]
    if precision == 'year' or len(parts) == 1:
        return f'circa {y}'
    try:
        m = _MONTHS[int(parts[1]) - 1]
        if precision == 'month' or len(parts) == 2:
            return f'{m} {y}'
        return f'{m} {int(parts[2])}, {y}'
    except (ValueError, IndexError):
        return d          # never let one bad stored value 500 the board (review H2)


def public_row(k, user):
    """One knife → its public dict. Everything not named here stays private."""
    row = {f: k.get(f) or '' for f in PUBLIC_FIELDS}
    ext = k.get('ext') or {}
    for f in PUBLIC_EXT:
        row[f] = ext.get(f) or ''
    precision = k.get('born_on_precision') or 'day'
    if user.get('hide_born_day') and precision == 'day' and row['born_on']:
        row['born_on'] = row['born_on'][:7]
        precision = 'month'
    row['born_on_precision'] = precision
    row['born'] = _fmt_born(row['born_on'], precision)
    row['for_sale'] = 1 if k.get('sale_status') == 'for_sale' else 0
    row['for_trade'] = 1 if k.get('sale_status') == 'for_trade' else 0
    if row['for_sale']:
        if k.get('asking_price'):
            row['asking_price'] = k['asking_price']
        if k.get('seller_note'):
            row['seller_note'] = k['seller_note']
    return row


def display_name(row):
    """'Large Sebenza 31', 'Mnandi', 'Small Inkosi' — from public fields only."""
    if row.get('model') == 'Sebenza':
        return ' '.join(x for x in (row.get('size'), 'Sebenza', row.get('generation')) if x)
    name = row.get('model') or row['tag']
    if row.get('size') and row.get('size') not in name:
        return f"{row['size']} {name}"
    return name


DISPLAY_EDGE = 1600
THUMB_EDGE = 800


def _watermark(img, handle):
    """Diagonal '@handle · blade-book' burned into the display image — makes
    the photo worthless for scam listings without wrecking it (crkinv port)."""
    text = f'@{handle} · blade-book'
    w, h = img.size
    layer = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    size = max(24, w // 24)
    try:
        font = ImageFont.truetype(
            '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', size)
    except OSError:
        font = ImageFont.load_default()
    tw = draw.textlength(text, font=font)
    step = int(tw) + size * 3
    for y in range(0, h + step, step):
        draw.text((w * 0.04, y), text, font=font,
                  fill=(255, 255, 255, 64),
                  stroke_width=max(1, size // 16),
                  stroke_fill=(20, 20, 20, 48))
    layer = layer.rotate(24, expand=False, center=(w // 2, h // 2))
    return Image.alpha_composite(img.convert('RGBA'), layer).convert('RGB')


def export_hero(store, k, handle, img_dir):
    """Re-encoded (EXIF/GPS-free), watermarked display + clean thumb for the
    knife's hero photo. Returns (hero_name, thumb_name) or (None, None)."""
    photos_ = k.get('photos') or []
    if not photos_:
        return None, None
    by_seq = {p['seq']: p for p in photos_}
    p = by_seq.get(k.get('hero_photo')) or photos_[0]
    try:
        img = Image.open(io.BytesIO(store.get(p['store_key'])))
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')          # re-encode: EXIF/XMP/GPS dropped ...
        img.info.pop('comment', None)     # ... and the JPEG COM segment, which
                                          # Pillow would otherwise copy through
                                          # convert() and write on save (review M9)
    except (OSError, KeyError, UnidentifiedImageError):
        return None, None
    os.makedirs(img_dir, exist_ok=True)
    out, out_t = f"{k['tag']}.jpg", f"{k['tag']}_t.jpg"
    display = img.copy()
    display.thumbnail((DISPLAY_EDGE, DISPLAY_EDGE))
    display = _watermark(display, handle)
    display.save(os.path.join(img_dir, out), 'JPEG', quality=85, optimize=True)
    thumb = img.copy()
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
    thumb.save(os.path.join(img_dir, out_t), 'JPEG', quality=80, optimize=True)
    return out, out_t


def bundle_dir(handle):
    if not SAFE_HANDLE.fullmatch(handle or ''):
        raise ValueError(f'unsafe handle {handle!r}')
    return os.path.join(paths.WWW_DIR, f'@{handle}')


def _public_base():
    return auth.base_url() + paths.URL_PREFIX


def _gate_snippet(prefix):
    """Client-side key gate (crkinv port, see billboard/crkinv/export.py).
    Lightweight on purpose: keeps drive-by scrapers and strangers out while
    staying a static page. Keys are checked as lowercase sha-256 against
    keys.json; a good key is remembered in localStorage. NOTE: fixed strings
    only — never interpolate user data into this snippet."""
    return '''<style>
  #gate { position:fixed; inset:0; background:var(--cream, #f6f1e7); z-index:99;
          display:flex; align-items:center; justify-content:center; padding:20px; }
  #gate .box { background:#fff; border:2px solid #141210; border-radius:14px;
               padding:22px 24px; max-width:360px; width:100%; text-align:center;
               font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; }
  #gate h2 { margin:0 0 6px; font-size:1.05rem; color:#b8452c; border:none;
             text-transform:none; letter-spacing:0; padding:0; }
  #gate p { color:#666; font-size:.85rem; margin:0 0 12px; }
  #gate input { width:100%; padding:10px 12px; font-size:1rem; border:2px solid #141210;
                border-radius:10px; margin-bottom:10px; text-align:center; }
  #gate button { background:#b8452c; color:#fff; border:none; border-radius:10px;
                 padding:10px 22px; font-weight:800; font-size:.95rem; cursor:pointer; }
  #gate .bad { color:#b8452c; font-weight:700; font-size:.85rem; min-height:1.2em; margin-top:8px; }
</style>
<script>
(function () {
  fetch('%PREFIX%keys.json', { cache: 'no-store' })
    .then(function (r) { if (!r.ok) throw 0; return r.json(); })
    .then(function (g) {
      function hash(s) {
        return crypto.subtle.digest('SHA-256', new TextEncoder().encode(
          s.toLowerCase().trim())).then(function (b) {
            return Array.prototype.map.call(new Uint8Array(b), function (x) {
              return x.toString(16).padStart(2, '0'); }).join(''); });
      }
      function pass(h) { return g.hashes.indexOf(h) >= 0; }
      hash(localStorage.getItem('bb_key') || '').then(function (h) {
        if (pass(h)) return;
        var ov = document.createElement('div'); ov.id = 'gate';
        ov.innerHTML = '<div class="box"><h2>Key holders only</h2>' +
          '<p>This register is for friends &amp; fellow collectors. ' +
          'Enter your key — you only do this once.</p>' +
          '<input id="gkey" placeholder="your key" autocomplete="off">' +
          '<button id="ggo">unlock</button><div class="bad" id="gbad"></div></div>';
        document.body.appendChild(ov);
        function attempt() {
          var v = document.getElementById('gkey').value;
          hash(v).then(function (h2) {
            if (pass(h2)) { localStorage.setItem('bb_key', v); ov.remove(); }
            else { document.getElementById('gbad').textContent = 'not a current key'; }
          });
        }
        document.getElementById('ggo').addEventListener('click', attempt);
        document.getElementById('gkey').addEventListener('keydown',
          function (e) { if (e.key === 'Enter') attempt(); });
      });
    }).catch(function () {});
})();
</script>'''.replace('%PREFIX%', prefix)


_STYLE = '''
  body { background:var(--cream,#f6f1e7); color:var(--ink,#141210); margin:0;
         font-family:'DM Sans',-apple-system,sans-serif; }
  main { max-width:720px; margin:0 auto; padding:20px 16px 60px; line-height:1.5; }
  h1 { font-family:'Bebas Neue',Impact,sans-serif; font-weight:400;
       letter-spacing:.06em; font-size:2.6rem; margin:.2rem 0; }
  .tag { color:var(--accent,#b8452c); font-weight:800; letter-spacing:.08em; }
  img.hero { width:100%; border-radius:14px; border:2px solid var(--ink,#141210); margin:10px 0; }
  table { border-collapse:collapse; width:100%; margin:14px 0; }
  th, td { text-align:left; padding:7px 10px; border-bottom:1px solid var(--line,#e2d9c8);
           vertical-align:top; }
  th { width:150px; font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; color:#555; }
  .sale { display:inline-block; background:var(--accent,#b8452c); color:#fff; font-weight:800;
          border-radius:10px; padding:6px 14px; margin:8px 0; }
  .card { background:#fff; border:1px solid var(--line,#e2d9c8); border-radius:14px;
          padding:14px; margin:12px 0; }
  .card img { width:100%; border-radius:10px; }
  a { color:var(--accent,#b8452c); font-weight:700; text-decoration:none; }
  .signin { position:absolute; top:14px; right:16px; font-size:.78rem;
            letter-spacing:.08em; text-transform:uppercase; }
  main { position:relative; }
  a.cta { display:inline-block; background:var(--accent,#b8452c); color:#fff; font-weight:800;
          border-radius:12px; padding:10px 16px; text-decoration:none; margin:8px 0 4px; }
'''


def _mark_svg(px):
    """The blade-book mark (a register with a blade as its bookmark), inlined so
    the bundle needs no extra request. Fixed literal colors, no data — safe to
    interpolate into any page."""
    return (f'<svg class="bbmark" viewBox="24 14 200 200" width="{int(px)}" aria-hidden="true">'
            '<rect x="40" y="64" width="160" height="136" rx="12" fill="#1a1a1a"/>'
            '<g fill="#faf6ee" opacity=".92">'
            '<rect x="62" y="146" width="72" height="7" rx="3.5"/>'
            '<rect x="62" y="163" width="56" height="7" rx="3.5"/>'
            '<rect x="62" y="180" width="64" height="7" rx="3.5"/></g>'
            '<path d="M143 30 L169 30 L169 142 L156 178 L143 150 Z" fill="#b8452c"/>'
            '<path d="M156 172 L150 52" stroke="#a8adb0" stroke-width="2.5" '
            'stroke-linecap="round" opacity=".65" fill="none"/></svg>')


def _foot():
    """The shared footer strip (plan 13-nav): identical markup on every
    generated page, matching html/about/index.html and styled by the
    `.bb-foot` rules in vibe.css (both pages already link that stylesheet).
    No collector data — safe on gated pages too."""
    return ('<footer class="bb-foot">' + _mark_svg(16) + ' <a href="/blade-book/search/">search</a> · '
            '<a href="/blade-book/board/">board</a> · <a href="/blade-book/how/">how</a> · '
            '<a href="/blade-book/about/">about</a> · <a href="/blade-book/terms/">terms</a> · '
            '<a class="bb-auth" href="/blade-book/me/">sign in</a></footer>\n'
            '<script src="/blade-book/nav.js?v=20260904" defer></script>\n')


def _head(title, desc, og_image, noindex, extra_style=''):
    """Document head through `<body>`. Callers open their own `<main>` (the
    register index puts a full-bleed hero BEFORE main)."""
    e = html_mod.escape
    robots = '<meta name="robots" content="noindex, nofollow">\n' if noindex else ''
    og_img = f'<meta property="og:image" content="{e(og_image)}">\n' if og_image else ''
    return (f'<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">\n{robots}'
            f'<title>{e(title)}</title>\n'
            f'<meta property="og:title" content="{e(title)}">\n'
            f'<meta property="og:description" content="{e(desc)}">\n'
            f'{og_img}<meta property="og:type" content="website">\n'
            f'<link rel="icon" type="image/svg+xml" href="/blade-book/mark.svg">\n'
            f'<link rel="apple-touch-icon" href="/blade-book/apple-touch-icon.png">\n'
            f'<link rel="stylesheet" href="/blade-book/vibe.css?v=20260904">\n'
            f'<style>{_STYLE}{extra_style}</style>\n</head>\n<body>\n')


def _knife_page(row, handle, gated):
    e = html_mod.escape
    name = display_name(row)
    if gated:
        # chat-preview link unfurls are the accidental-leak channel the key
        # gate exists for — keep the model/edition out of <title>/og:title.
        title = f'{row["tag"]} — blade-book'
    else:
        title_bits = [name] + ([row['special_edition']] if row.get('special_edition') else [])
        title = ' · '.join(title_bits) + f' — @{handle}'
    desc_bits = [b for b in (
        row.get('blade_steel'),
        ' '.join(x for x in (row.get('damascus_smith'), row.get('damascus_pattern')) if x) or None,
        row.get('inlay_material'),
        f"born {row['born']}" if row.get('born') else None) if b]
    desc = '' if gated else (' · '.join(desc_bits) or 'From a private register on blade-book.')
    og_image = (f"{_public_base()}/@{handle}/img/{row['img']}" if row.get('img') and not gated else '')
    out = _head(title, desc, og_image, noindex=gated) + '<main>\n'
    out += f'<p class="tag">{e(row["tag"])}</p>\n<h1>{e(name)}</h1>\n'
    if row.get('img'):
        out += f'<img class="hero" src="../img/{e(row["img"])}" alt="{e(name)}">\n'
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
    if row.get('notes_public'):
        out += f'<div class="card">{e(row["notes_public"])}</div>\n'
    out += (f'<p><a href="../">← @{e(handle)}’s register</a></p>\n'
            '<p><a class="cta" href="/blade-book/how/">Keep a register like this — how it works →</a></p>\n'
            + _foot() + '</main>\n')
    out += _gate_snippet('../') if gated else ''
    return out + '</body>\n</html>\n'


# Register index (2026-09-02 UI pass): full-bleed parallax hero over the
# collector's first display photo, then the register as a crk-style sortable
# list or a thumb grid (toggle remembered in localStorage). Everything below
# is server-rendered + escaped; the script reads data-* attributes and
# reorders existing nodes only — no innerHTML, no data reaches JS as markup.
_INDEX_STYLE = """
  body { overflow-x:hidden; }
  /* Blur-fill framing: the photo sits whole (contain, padded) on a blurred, darkened copy of
     itself (cover). A portrait card spread used to be center/cover-cropped to its middle band. */
  .hero-bg { position:fixed; inset:-12vh 0 0 0; z-index:-1; background:#1a1a1a; overflow:hidden;
             will-change:transform; }
  .hero-bg::before { content:""; position:absolute; inset:-6%; background:var(--hero) center/cover no-repeat;
                     filter:blur(26px) brightness(.55) saturate(1.1); }
  /* The sharp photo lives INSIDE the hero band (not on the fixed backdrop, whose box is taller
     than the visible band — that hid the bottom of a portrait photo, i.e. the knife). It scrolls
     with the content; only the blurred backdrop parallaxes, which reads as depth. */
  /* Phone: the photo takes the top of the band, the words sit under it. Desktop (≥700px):
     a split — words left, the whole photo right at band height. Never text over photo. */
  .hero-photo { position:absolute; inset:2vh 4vw auto; height:38vh; z-index:0; pointer-events:none;
                background:var(--hero) center/contain no-repeat;
                filter:drop-shadow(0 12px 28px rgba(0,0,0,.45)); }
  .hero { position:relative; min-height:54vh; display:flex; flex-direction:column; justify-content:flex-end;
          padding:42vh 20px 32px; color:#fff; }
  .hero::after { content:""; position:absolute; inset:0; z-index:1; pointer-events:none;
                 background:linear-gradient(180deg, rgba(26,26,26,0) 0%, rgba(26,26,26,.08) 50%, rgba(26,26,26,.78) 100%); }
  .hero > * { position:relative; z-index:2; }
  .hero > .hero-photo { position:absolute; z-index:0; }   /* must re-assert absolute: the rule above wins on specificity */
  .hero.plain { background:none; color:var(--ink,#141210); min-height:0; padding-bottom:10px; }
  .hero.plain::after { display:none; }
  .hero .tag { color:#f3d5c9; text-shadow:0 1px 8px rgba(0,0,0,.6); }
  .hero .feat .tag { color:var(--accent,#b8452c); text-shadow:none; }   /* on the cream card, not the photo */
  .hero.plain .tag { color:var(--accent,#b8452c); text-shadow:none; }
  .hero h1 { font-size:3.4rem; line-height:1; margin:.15rem 0 0; text-shadow:0 2px 14px rgba(0,0,0,.55); }
  .hero.plain h1 { text-shadow:none; }
  .hero .count { color:#ece4d4; font-weight:600; text-shadow:0 1px 8px rgba(0,0,0,.6); }
  .hero.plain .count { color:#555; text-shadow:none; }
  .hero .signin { color:#fff; }
  .hero.plain .signin { color:var(--accent,#b8452c); }
  /* the mark is the way home — a flex child, so pin it to its own width or the
     whole row becomes the click target */
  .hero .home { display:inline-block; align-self:flex-start; line-height:0; border-radius:8px; }
  .hero .home:hover svg, .hero .home:focus-visible svg { transform:translateY(-2px); }
  .hero .home svg { transition:transform .15s ease; }
  .feat { display:inline-flex; flex-direction:column; gap:2px; align-self:flex-start; margin-top:16px;
          background:var(--cream,#f6f1e7); color:var(--ink,#141210); border:2px solid var(--ink,#141210);
          border-radius:14px; padding:10px 14px; text-decoration:none; text-shadow:none;
          box-shadow:0 10px 30px rgba(0,0,0,.35); max-width:100%; }
  .feat .flabel { font-size:.66rem; letter-spacing:.14em; color:#666; font-weight:700; }
  .feat .fname { font-weight:800; font-size:1.05rem; line-height:1.2; }
  .feat .fname .tag { margin-right:6px; }
  .feat .fborn { color:var(--accent,#b8452c); font-weight:700; font-size:.85rem; }
  .feat .badge-sale { margin-top:4px; align-self:flex-start; }
  .feat:hover { transform:translateY(-2px); }
  .stats { display:flex; flex-wrap:wrap; gap:8px 22px; padding:14px 4px 4px; color:#555; font-size:.88rem; }
  .stats b { color:var(--ink,#141210); font-family:'Bebas Neue',Impact,sans-serif; font-weight:400;
             font-size:1.5rem; letter-spacing:.04em; margin-right:4px; vertical-align:-2px; }
  main.reg { position:relative; max-width:none; margin:-22px 0 0; padding:0; background:var(--cream,#f6f1e7);
             border-radius:24px 24px 0 0; box-shadow:0 -10px 34px rgba(0,0,0,.28); }
  .hero.plain + main.reg { margin-top:0; border-radius:0; box-shadow:none; }
  main.reg .inner { max-width:960px; margin:0 auto; padding:18px 16px 60px; }
  .toolbar { display:flex; gap:10px; align-items:center; position:sticky; top:0; z-index:2;
             background:var(--cream,#f6f1e7); padding:12px 0 10px; }
  #q { flex:1; min-width:0; padding:10px 12px; font:inherit; border:2px solid var(--ink,#141210);
       border-radius:12px; background:#fff; }
  #view { display:flex; border:2px solid var(--ink,#141210); border-radius:12px; overflow:hidden; }
  #view button { font:inherit; font-weight:800; font-size:.8rem; letter-spacing:.06em; padding:9px 12px;
                 border:0; background:#fff; color:var(--ink,#141210); cursor:pointer; }
  #view button.on { background:var(--ink,#141210); color:var(--cream,#f6f1e7); }
  #cols { display:grid; grid-template-columns:92px 1fr 118px 96px; gap:8px; padding:6px 8px 4px;
          border-bottom:2px solid var(--ink,#141210); }
  #cols button { font:inherit; font-size:.7rem; letter-spacing:.09em; text-transform:uppercase; color:#555;
                 background:none; border:0; padding:0; text-align:left; cursor:pointer; }
  #cols button.asc::after { content:" ▲"; color:var(--accent,#b8452c); }
  #cols button.desc::after { content:" ▼"; color:var(--accent,#b8452c); }
  .k { transition:opacity .45s ease-out, transform .45s ease-out, box-shadow .2s; }
  .k.pre { opacity:0; transform:translateY(16px); }
  #reg.list .k { display:grid; grid-template-columns:92px 1fr 118px 96px; gap:8px; align-items:center;
                 padding:6px 8px; border-bottom:1px solid var(--line,#e2d9c8); border-radius:8px; }
  #reg.list .k:hover { background:#fff; box-shadow:0 6px 18px rgba(0,0,0,.08); transform:translateY(-1px); }
  #reg.list .t { width:84px; height:63px; object-fit:cover; border-radius:6px; background:var(--soft,#e8e0d0);
                 display:block; cursor:zoom-in; }
  #reg.list span.t { cursor:default; }
  #reg .name { color:var(--ink,#141210); font-weight:700; }
  #reg .name .tag { margin-right:4px; }
  #reg .born { color:var(--accent,#b8452c); font-weight:700; font-size:.9rem; }
  .badge-sale { display:inline-block; background:var(--accent,#b8452c); color:#fff; font-size:.66rem; font-weight:800;
                letter-spacing:.06em; padding:3px 8px; border-radius:12px; white-space:nowrap; }
  #reg.grid { display:grid; grid-template-columns:repeat(3, 1fr); gap:10px; margin-top:10px; }
  #reg.grid .k { display:flex; flex-direction:column; background:#fff; border:1px solid var(--line,#e2d9c8);
                 border-radius:12px; overflow:hidden; }
  #reg.grid .k:hover { transform:translateY(-3px); box-shadow:0 10px 24px rgba(0,0,0,.12); }
  #reg.grid .t { width:100%; aspect-ratio:1; object-fit:cover; display:block; background:var(--soft,#e8e0d0);
                 cursor:zoom-in; }
  #reg.grid .name { padding:6px 8px 0; font-size:.82rem; line-height:1.25; }
  #reg.grid .born { padding:2px 8px 0; font-size:.78rem; }
  #reg.grid .badge-sale { margin:6px 8px 8px; align-self:flex-start; }
  #reg.grid .k > :last-child { margin-bottom:8px; }
  .empty { text-align:center; color:#888; padding:30px 0; }
  dialog#lb { border:0; padding:0; background:#000; border-radius:14px; max-width:94vw; }
  dialog#lb img { max-width:90vw; max-height:82vh; display:block; }
  dialog#lb::backdrop { background:rgba(10,8,4,.75); }
  @media (min-width: 700px) {
    .hero { min-height:60vh; padding:28px 40px 52px; }
    .hero h1 { font-size:4.6rem; }
    .hero-photo { inset:3vh 4vw 3vh auto; height:auto; width:50vw; background-position:center; }
    .hero > :not(.hero-photo) { max-width:46vw; }
    #reg.grid { grid-template-columns:repeat(5, 1fr); gap:12px; }
  }
  @media (prefers-reduced-motion: reduce) {
    .hero-bg { position:absolute; inset:0; height:60vh; }
    .k, .k.pre { transition:none; opacity:1; transform:none; }
  }
"""

_INDEX_SCRIPT = """
(function () {
  var reg = document.getElementById('reg'), q = document.getElementById('q');
  var view = document.getElementById('view'), cols = document.getElementById('cols');
  var rows = Array.prototype.slice.call(reg.querySelectorAll('.k'));
  function setView(v) {
    reg.className = v;
    cols.hidden = (v === 'grid');
    Array.prototype.forEach.call(view.querySelectorAll('button'), function (b) {
      b.classList.toggle('on', b.getAttribute('data-view') === v);
    });
    try { localStorage.setItem('bb_view', v); } catch (e) {}
  }
  var saved = 'list';
  try { saved = localStorage.getItem('bb_view') || 'list'; } catch (e) {}
  setView(saved === 'grid' ? 'grid' : 'list');
  view.addEventListener('click', function (ev) {
    var b = ev.target.closest('button'); if (b) setView(b.getAttribute('data-view'));
  });
  var empty = document.getElementById('empty');
  // haystack = visible text + every public descriptor (data-q), lowercased once;
  // stored on the element because the sort below reorders `rows` in place
  rows.forEach(function (r) {
    r.bbHay = ((r.getAttribute('data-q') || '') + ' ' + r.textContent).toLowerCase();
  });
  q.addEventListener('input', function () {
    var t = q.value.trim().toLowerCase(), shown = 0;
    rows.forEach(function (r) {
      var hit = !t || r.bbHay.indexOf(t) >= 0;
      r.hidden = !hit; if (hit) shown++;
    });
    empty.hidden = shown > 0;
  });
  var dir = {};
  var heads = Array.prototype.slice.call(cols.querySelectorAll('[data-sort]'));
  heads.forEach(function (h) {
    h.addEventListener('click', function () {
      var k = h.getAttribute('data-sort');
      dir[k] = dir[k] === 'asc' ? 'desc' : 'asc';
      var d = dir[k] === 'asc' ? 1 : -1;
      rows.sort(function (a, b) {
        var x = a.getAttribute('data-' + k) || '', y = b.getAttribute('data-' + k) || '';
        if (k === 'tag') { x = parseInt(x.slice(1), 10) || 0; y = parseInt(y.slice(1), 10) || 0; return (x - y) * d; }
        if (!x && y) return 1; if (x && !y) return -1;
        return x < y ? -d : x > y ? d : 0;
      });
      rows.forEach(function (r) { reg.appendChild(r); });
      heads.forEach(function (o) { o.classList.remove('asc', 'desc'); });
      h.classList.add(dir[k]);
    });
  });
  var lb = document.getElementById('lb'), li = lb.querySelector('img');
  reg.addEventListener('click', function (ev) {
    var t = ev.target;
    if (t.tagName === 'IMG' && t.getAttribute('data-full')) {
      li.src = t.getAttribute('data-full'); li.alt = t.alt; lb.showModal();
    }
  });
  lb.addEventListener('click', function () { lb.close(); });
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!still && 'IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (en) { if (en.isIntersecting) { en.target.classList.remove('pre'); io.unobserve(en.target); } });
    }, { rootMargin: '0px 0px -6% 0px' });
    rows.forEach(function (r) {
      if (r.getBoundingClientRect().top > window.innerHeight) { r.classList.add('pre'); io.observe(r); }
    });
  }
  var bg = document.querySelector('.hero-bg');
  if (bg && !still) {
    var ticking = false;
    window.addEventListener('scroll', function () {
      if (ticking) return; ticking = true;
      window.requestAnimationFrame(function () {
        bg.style.transform = 'translateY(' + (window.scrollY * 0.32) + 'px)'; ticking = false;
      });
    }, { passive: true });
  }
})();
"""


# Register-page search haystack: every public text descriptor of a knife
# (same whitelist the knife page renders and the /search FTS indexes), so
# "annual" / "damascus" / "leopardwood" hit even though the row only SHOWS
# tag + name + born. Built from public_row() output only — the leak test
# scans the whole page, so nothing outside the whitelist can ride along.
_Q_FIELDS = tuple(f for f in PUBLIC_FIELDS if f != 'born_on_precision') + PUBLIC_EXT


def _row_q(row):
    parts = [str(row.get(f) or '') for f in _Q_FIELDS]
    parts.append(display_name(row))
    if row.get('for_sale'):
        parts.append('for sale')
    elif row.get('for_trade'):
        parts.append('for trade')
    return ' '.join(p for p in parts if p)


def _index_html(rows, user, gated, featured_tag=None):
    e = html_mod.escape
    handle = user['handle']
    n = len(rows)
    count = f'{n} {"knives" if n != 1 else "knife"}'
    title = f'@{handle} — blade-book register'
    desc = '' if gated else f'{count} in a collector’s public register.'
    # Simon picks the hero (settings.featured_knife_id); a hidden/private/draft
    # or photo-less pin isn't in `rows` (or has no img) and falls back below —
    # same rule as an unset pin.
    hero_row = None
    if featured_tag:
        hero_row = next((r for r in rows if r.get('tag') == featured_tag and r.get('img')), None)
    if hero_row is None:
        hero_row = next((r for r in rows if r.get('img')), None)
    # OG card for a shared register link = the hero photo (watermarked display
    # image, already public); never on a gated register.
    og_image = (f"{_public_base()}/@{handle}/img/{hero_row['img']}" if hero_row and not gated else '')
    out = _head(title, desc, og_image, noindex=gated, extra_style=_INDEX_STYLE)
    if hero_row:
        hero_var = f'--hero:url(img/{e(hero_row["img"])})'
        out += (f'<div class="hero-bg" style="{hero_var}"></div>\n'
                f'<header class="hero" style="{hero_var}">\n'
                '<div class="hero-photo"></div>\n')
    else:
        out += '<header class="hero plain">\n'
    out += ('<p class="signin"><a class="bb-auth" href="/blade-book/me/">sign in</a></p>\n'
            f'<a class="home" href="/blade-book/" aria-label="blade-book home" title="blade-book">'
            f'{_mark_svg(46)}</a>\n<p class="tag">BLADE-BOOK REGISTER</p>\n<h1>@{e(handle)}</h1>\n'
            f'<p class="count">{count}</p>\n')
    if hero_row:
        fname = display_name(hero_row)
        fborn = f'<span class="fborn">born {e(hero_row["born"])}</span>' if hero_row.get('born') else ''
        fbadge = ''
        if hero_row.get('for_sale'):
            fprice = f" · ${hero_row['asking_price']:g}" if hero_row.get('asking_price') else ''
            fbadge = f'<span class="badge-sale">FOR SALE{fprice}</span>'
        elif hero_row.get('for_trade'):
            fbadge = '<span class="badge-sale">FOR TRADE</span>'
        out += (f'<a class="feat" href="{e(hero_row["tag"])}/"><span class="flabel">IN THE PHOTO</span>'
                f'<span class="fname"><span class="tag">{e(hero_row["tag"])}</span>{e(fname)}</span>'
                f'{fborn}{fbadge}</a>\n')
    out += '</header>\n<main class="reg">\n<div class="inner">\n'
    years = sorted({r['born_on'][:4] for r in rows if r.get('born_on')})
    for_sale = sum(1 for r in rows if r.get('for_sale'))
    stats = [f'<span><b>{n}</b> {"knives" if n != 1 else "knife"}</span>']
    if years:
        span = years[0] if len(years) == 1 else f'{years[0]}–{years[-1]}'
        stats.append(f'<span><b>{span}</b> born</span>')
    if for_sale:
        stats.append(f'<span><b>{for_sale}</b> for sale</span>')
    out += f'<div class="stats">{"".join(stats)}</div>\n'
    out += (
            '<div class="toolbar"><input id="q" type="search" placeholder="search this register — model, born, steel…" '
            'autocomplete="off" aria-label="search this register">'
            '<div id="view" role="group" aria-label="view"><button type="button" data-view="list">LIST</button>'
            '<button type="button" data-view="grid">GRID</button></div></div>\n'
            '<div id="cols"><span></span><button type="button" data-sort="name">knife</button>'
            '<button type="button" data-sort="born">born</button><button type="button" data-sort="tag">tag</button></div>\n'
            '<div id="reg" class="list">\n')
    for row in rows:
        name = display_name(row)
        badge = '<span></span>'
        if row.get('for_sale'):
            price = f" · ${row['asking_price']:g}" if row.get('asking_price') else ''
            badge = f'<span class="badge-sale">FOR SALE{price}</span>'
        elif row.get('for_trade'):
            badge = '<span class="badge-sale">FOR TRADE</span>'
        if row.get('img_t'):
            full = f' data-full="img/{e(row["img"])}"' if row.get('img') else ''
            img = f'<img class="t" src="img/{e(row["img_t"])}"{full} alt="{e(name)}" loading="lazy">'
        else:
            img = '<span class="t"></span>'
        born = e(row['born']) if row.get('born') else ''
        out += (f'<article class="k" data-tag="{e(row["tag"])}" data-name="{e(name)}" '
                f'data-born="{e(row.get("born_on") or "")}" data-q="{e(_row_q(row))}">{img}'
                f'<a class="name" href="{e(row["tag"])}/"><span class="tag">{e(row["tag"])}</span>{e(name)}</a>'
                f'<span class="born">{born}</span>{badge}</article>\n')
    out += ('</div>\n<p id="empty" class="empty" hidden>nothing matches</p>\n'
            '<p style="text-align:center;margin-top:22px"><a class="cta" href="/blade-book/how/">'
            'Keep a register like this — how it works →</a></p>\n'
            + _foot() +
            '</div>\n</main>\n'
            '<dialog id="lb"><img alt=""></dialog>\n'
            f'<script>{_INDEX_SCRIPT}</script>\n')
    out += _gate_snippet('') if gated else ''
    return out + '</body>\n</html>\n'


def _lock_path(handle):
    lock_dir = os.path.join(paths.DATA_DIR, 'publish-locks')
    os.makedirs(lock_dir, exist_ok=True)
    return os.path.join(lock_dir, f'{handle}.lock')


def build_user(con, user, store):
    """Regenerate one collector's public bundle. Returns the knife count, or
    -1 when the page was removed (profile_private). Build lands in a temp dir
    first; the swap is delete-then-rename, and the tmp dir is removed on
    failure, so a half-built bundle never serves. Builds for one user are
    serialized end-to-end via an flock in DATA_DIR/publish-locks (never
    WWW_DIR — Apache must never be able to serve the lock file) — two gunicorn
    workers' debounce timers and the cron sweep can all try to build the same
    handle at once, and without this lock their overlapping tmp/rmtree/replace
    calls interleave into a corrupt or incomplete bundle."""
    # Import inside build_user to avoid circular dependency (search imports publish)
    from bb import search

    handle = user['handle']
    dest = bundle_dir(handle)               # validates the handle
    tmp = dest + '.tmp'
    lockf = open(_lock_path(handle), 'w')
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        if user.get('profile_private'):
            search.deindex_user(con, user['id'])
            shutil.rmtree(dest, ignore_errors=True)
            shutil.rmtree(tmp, ignore_errors=True)
            return -1
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            img_dir = os.path.join(tmp, 'img')
            os.makedirs(img_dir, exist_ok=True)
            rows = []
            featured_tag = None
            for k in db.public_knives(con, user['id']):
                row = public_row(k, user)
                hero, thumb = export_hero(store, k, handle, img_dir)
                row['img'], row['img_t'] = hero, thumb
                if user.get('featured_knife_id') and k['id'] == user['featured_knife_id']:
                    featured_tag = row['tag']
                rows.append(row)
            key = (user.get('public_key') or '').strip()
            gated = bool(key)
            if gated:
                with open(os.path.join(tmp, 'keys.json'), 'w') as f:
                    json.dump({'hashes': [hashlib.sha256(key.lower().encode()).hexdigest()]}, f)
            for row in rows:
                page_dir = os.path.join(tmp, row['tag'])
                os.makedirs(page_dir)
                with open(os.path.join(page_dir, 'index.html'), 'w') as f:
                    f.write(_knife_page(row, handle, gated))
            with open(os.path.join(tmp, 'index.html'), 'w') as f:
                f.write(_index_html(rows, user, gated, featured_tag=featured_tag))
            with open(os.path.join(tmp, 'knives.json'), 'w') as f:
                json.dump({'generated': datetime.now(timezone.utc).isoformat(),
                           'handle': handle, 'count': len(rows), 'knives': rows}, f, indent=1)
            shutil.rmtree(dest, ignore_errors=True)
            os.replace(tmp, dest)
            search.reindex_user(con, user, rows)
            return len(rows)
        finally:
            # no-op after a successful os.replace (tmp no longer exists); on any
            # raise above, removes the partial bundle so it never gets served
            shutil.rmtree(tmp, ignore_errors=True)
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


log = logging.getLogger('blade-book.publish')

DEBOUNCE_S = float(os.environ.get('BLADEBOOK_PUBLISH_DEBOUNCE_S', '30'))

_timers = {}
_timers_lock = threading.Lock()


def _store_factory():
    from bb import store as store_mod
    return store_mod.from_paths()


def _build_one(con, user, store):
    """Build with compare-and-clear: read the stamp first so a save landing
    mid-build keeps the owner dirty for the next pass."""
    stamp = user.get('publish_dirty_at')
    build_user(con, user, store)
    if stamp:
        db.clear_publish_dirty_if(con, user['id'], stamp)


def _fire(owner_id):
    with _timers_lock:
        _timers.pop(owner_id, None)
    try:
        con = db.connect()
        try:
            user = db.get_user(con, owner_id)
            if user is None or user['publish_dirty_at'] is None:
                return
            _build_one(con, user, _store_factory())
        finally:
            con.close()
    except Exception:  # noqa: BLE001 — a failed build stays dirty for the sweep
        log.exception('debounced publish failed for user %s', owner_id)


def schedule(owner_id):
    """Called after any save that changes the public surface: stamp dirty and
    (re)start this process's 30 s timer. The cron sweep is the backstop for a
    restart that eats a pending timer."""
    con = db.connect()
    try:
        db.mark_publish_dirty(con, owner_id)
    finally:
        con.close()
    with _timers_lock:
        t = _timers.pop(owner_id, None)
        if t:
            t.cancel()
        t = threading.Timer(DEBOUNCE_S, _fire, args=(owner_id,))
        t.daemon = True
        _timers[owner_id] = t
        t.start()


def run_due(quiet_s=None):
    """Build every dirty owner whose last save is at least quiet_s old.
    Returns the number built. One bad user never blocks the rest."""
    if quiet_s is None:
        quiet_s = DEBOUNCE_S
    built = 0
    con = db.connect()
    try:
        users = db.dirty_owners(con, quiet_s=quiet_s)
        store = _store_factory()
        for user in users:
            try:
                _build_one(con, user, store)
                built += 1
            except Exception:  # noqa: BLE001
                log.exception('publish sweep failed for @%s', user['handle'])
    finally:
        con.close()
    return built
