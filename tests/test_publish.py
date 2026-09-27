# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import io
import os

from PIL import Image

from bb import db, publish


def _knife(**over):
    k = {'tag': 'K07', 'maker': 'crk', 'model': 'Sebenza', 'variant': '',
         'blade_steel': 'CPM MagnaCut', 'blade_shape': 'Drop Point',
         'born_on': '2023-02-20', 'born_on_precision': 'day',
         'notes_public': 'my story', 'sale_status': 'keeping',
         'asking_price': None, 'seller_note': None, 'hero_photo': 1,
         'ext': {'generation': '31', 'size': 'Large', 'handle_treatment': 'inlay',
                 'inlay_material': 'box elder burl', 'graphic_name': '',
                 'damascus_smith': '', 'damascus_pattern': '', 'special_edition': ''},
         'photos': [{'seq': 1, 'store_key': '1/1/1.jpg'}],
         # private columns present, must never survive projection
         'price_paid': 1337.0, 'acquired_from': 'SECRET-DEALER',
         'acquired_date': '2023-01-01', 'location': 'SECRET-SAFE',
         'notes_private': 'SECRET-NOTE', 'condition_note': 'SECRET-COND',
         'confidence': {'model': 'high'}, 'card_text': 'SECRET-CARD',
         'decode_note': 'SECRET-DECODE', 'id': 1, 'owner_id': 1, 'status': 'live',
         'is_public': 1}
    k.update(over)
    return k


USER = {'handle': 'simon-collector', 'hide_born_day': 0, 'profile_private': 0}


def test_public_row_is_whitelist_only():
    row = publish.public_row(_knife(), USER)
    for col in db.PRIVATE_COLUMNS:
        assert col not in row, col
    for banned in ('id', 'owner_id', 'status', 'is_public', 'photos'):
        assert banned not in row, banned
    assert row['tag'] == 'K07' and row['model'] == 'Sebenza'
    assert row['generation'] == '31' and row['size'] == 'Large'
    assert row['born'] == 'February 20, 2023'


def test_private_values_never_in_row_repr():
    text = repr(publish.public_row(_knife(), USER))
    for s in ('SECRET', '1337'):
        assert s not in text


def test_hide_born_day_demotes_to_month():
    u = dict(USER, hide_born_day=1)
    row = publish.public_row(_knife(), u)
    assert row['born'] == 'February 2023'
    assert row['born_on'] == '2023-02'


def test_fmt_born_precisions():
    assert publish._fmt_born('2023-02-20', 'day') == 'February 20, 2023'
    assert publish._fmt_born('2023-02-01', 'month') == 'February 2023'
    assert publish._fmt_born('2023-01-01', 'year') == 'circa 2023'
    assert publish._fmt_born(None, 'day') == ''
    assert publish._fmt_born('2023', 'year') == 'circa 2023'
    assert publish._fmt_born('2023-02', 'month') == 'February 2023'


def test_sale_projection():
    keeping = publish.public_row(_knife(), USER)
    assert keeping['for_sale'] == 0 and keeping['for_trade'] == 0
    assert 'asking_price' not in keeping and 'seller_note' not in keeping
    trade = publish.public_row(_knife(sale_status='for_trade', asking_price=999.0,
                                      seller_note='call me'), USER)
    assert trade['for_trade'] == 1 and 'asking_price' not in trade and 'seller_note' not in trade
    sale = publish.public_row(_knife(sale_status='for_sale', asking_price=999.0,
                                     seller_note='call me'), USER)
    assert sale['for_sale'] == 1 and sale['asking_price'] == 999.0
    assert sale['seller_note'] == 'call me'


def test_display_name():
    assert publish.display_name(publish.public_row(_knife(), USER)) == 'Large Sebenza 31'
    mnandi = _knife(model='Mnandi', ext=dict(_knife()['ext'], generation='', size=''))
    assert publish.display_name(publish.public_row(mnandi, USER)) == 'Mnandi'


def test_safe_handle():
    assert publish.SAFE_HANDLE.fullmatch('simon-collector')
    assert not publish.SAFE_HANDLE.fullmatch('../etc')
    assert not publish.SAFE_HANDLE.fullmatch('Simon')


class MemStore:
    def __init__(self, files):
        self.files = files

    def get(self, key):
        try:
            return self.files[key]
        except KeyError:
            raise KeyError(key)


def _jpeg_with_exif(w=2400, h=1600):
    img = Image.new('RGB', (w, h), (200, 180, 140))
    exif = Image.Exif()
    exif[0x010F] = b'SECRET-CAMERA'      # Make
    buf = io.BytesIO()
    img.save(buf, 'JPEG', exif=exif)
    return buf.getvalue()


def _hero_knife(key='1/1/1.jpg', hero=1):
    return {'tag': 'K07', 'hero_photo': hero,
            'photos': [{'seq': 1, 'store_key': key},
                       {'seq': 2, 'store_key': '1/1/2.jpg'}]}


def test_export_hero_strips_exif_and_caps_size(tmp_path):
    store = MemStore({'1/1/1.jpg': _jpeg_with_exif()})
    name, tname = publish.export_hero(store, _hero_knife(), 'pub', str(tmp_path))
    assert name == 'K07.jpg' and tname == 'K07_t.jpg'
    out = Image.open(os.path.join(tmp_path, name))
    assert max(out.size) <= publish.DISPLAY_EDGE
    assert not out.getexif()
    raw = open(os.path.join(tmp_path, name), 'rb').read()
    assert b'SECRET-CAMERA' not in raw
    thumb = Image.open(os.path.join(tmp_path, tname))
    assert max(thumb.size) <= publish.THUMB_EDGE


def test_export_hero_watermarks_display_not_thumb(tmp_path):
    store = MemStore({'1/1/1.jpg': _jpeg_with_exif(1200, 800)})
    publish.export_hero(store, _hero_knife(), 'pub', str(tmp_path))
    display = Image.open(os.path.join(tmp_path, 'K07.jpg'))
    # a flat-colour source stays flat unless the watermark drew on it
    assert len(display.convert('L').getcolors(maxcolors=100000)) > 20


def test_export_hero_falls_back_when_hero_seq_missing(tmp_path):
    store = MemStore({'1/1/1.jpg': _jpeg_with_exif(800, 600)})
    k = _hero_knife(hero=9)
    k['photos'] = [{'seq': 1, 'store_key': '1/1/1.jpg'}]
    name, _ = publish.export_hero(store, k, 'pub', str(tmp_path))
    assert name == 'K07.jpg'


def test_export_hero_no_photos_or_bad_bytes(tmp_path):
    assert publish.export_hero(MemStore({}), {'tag': 'K07', 'hero_photo': None,
                                              'photos': []}, 'pub', str(tmp_path)) == (None, None)
    store = MemStore({'1/1/1.jpg': b'not an image'})
    assert publish.export_hero(store, _hero_knife(), 'pub', str(tmp_path)) == (None, None)


def test_export_hero_missing_store_key(tmp_path):
    # photo row references a store key the store doesn't actually have
    assert publish.export_hero(MemStore({}), _hero_knife(), 'pub', str(tmp_path)) == (None, None)


# --- register index: parallax hero + list/grid views (2026-09-02 UI pass) ---

def _rows(n=3, with_img=True):
    rows = []
    for i in range(n):
        r = publish.public_row(_knife(tag=f'K0{i + 1}', born_on=f'20{20 + i}-0{i + 1}-1{i}'), USER)
        if with_img:
            r['img'], r['img_t'] = f'K0{i + 1}.jpg', f'K0{i + 1}_t.jpg'
        rows.append(r)
    return rows


def test_index_hero_uses_first_display_photo():
    html = publish._index_html(_rows(), USER, gated=False)
    assert 'class="hero"' in html and 'img/K01.jpg' in html
    assert 'BLADE-BOOK REGISTER' in html and '@simon-collector' in html and '3 knives' in html
    assert 'prefers-reduced-motion' in html


def test_index_hero_mark_links_home():
    """Simon 2026-09-03: the register mark top-left should click back to /blade-book/."""
    for gated in (False, True):
        html = publish._index_html(_rows(), USER, gated=gated)
        home = html.split('<header class="hero')[1].split('<p class="tag">')[0]
        assert '<a class="home" href="/blade-book/"' in home
        link = home.split('<a class="home"')[1].split('</a>')[0]
        assert '<svg class="bbmark"' in link
    assert '.hero .home' in publish._INDEX_STYLE


def test_index_without_photos_has_no_hero_image():
    html = publish._index_html(_rows(with_img=False), USER, gated=False)
    assert 'class="hero-bg"' not in html and 'img/K01.jpg' not in html and 'class="hero plain"' in html
    assert '@simon-collector' in html and '3 knives' in html


def test_index_rows_carry_sort_data_and_view_toggle():
    html = publish._index_html(_rows(), USER, gated=False)
    for needle in ('id="q"', 'id="view"', 'id="reg"', 'data-sort="tag"', 'data-sort="name"',
                   'data-sort="born"', 'data-tag="K02"', 'data-born="2021-02-11"',
                   'data-name="Large Sebenza 31"', 'bb_view', 'localStorage', '<dialog id="lb">',
                   'IntersectionObserver', 'href="K02/"', 'img/K02_t.jpg', 'data-full="img/K02.jpg"'):
        assert needle in html, needle


def test_index_rows_carry_searchable_public_descriptors():
    """Simon 2026-09-03: register search only hit name + born. 'annual' /
    'damascus' live in special_edition, blade_steel, smith/pattern — every
    public descriptor rides a data-q attribute the filter reads; nothing new
    is rendered, and nothing outside public_row() reaches it."""
    k = _knife(blade_steel='Damascus', variant='Annual 2024 (Galactic Bacon)',
               notes_public='left-handed sibling',
               ext={'generation': '31', 'size': 'Small', 'handle_treatment': 'inlay',
                    'inlay_material': 'leopardwood', 'graphic_name': 'Night Sky',
                    'damascus_smith': 'Chad Nichols', 'damascus_pattern': 'Stainless Ladder',
                    'special_edition': 'Annual 2024'})
    row = publish.public_row(k, USER)
    row['for_sale'], row['asking_price'] = 1, 500
    html = publish._index_html([row], USER, gated=False)
    q = html.split('data-q="')[1].split('"')[0].lower()
    for needle in ('annual 2024', 'damascus', 'galactic bacon', 'chad nichols', 'stainless ladder',
                   'leopardwood', 'night sky', 'inlay', 'small', 'left-handed sibling', 'for sale',
                   'drop point', '2023-02-20'):
        assert needle in q, needle
    for secret in ('1337', 'secret', 'keeping'):
        assert secret not in q, secret
    assert "getAttribute('data-q')" in publish._INDEX_SCRIPT


def test_index_search_data_omits_trade_when_keeping():
    html = publish._index_html(_rows(1), USER, gated=False)
    q = html.split('data-q="')[1].split('"')[0].lower()
    assert 'for sale' not in q and 'for trade' not in q and 'sebenza' in q


def test_index_escapes_data_attributes():
    row = _rows(1)[0]
    row['model'] = '<b>x</b>"'
    html = publish._index_html([row], USER, gated=False)
    assert '<b>x</b>' not in html and '&lt;b&gt;x&lt;/b&gt;&quot;' in html


def test_index_ungated_has_no_innerhtml():
    assert 'innerHTML' not in publish._index_html(_rows(), USER, gated=False)


def test_index_gated_keeps_gate_and_noindex():
    html = publish._index_html(_rows(), USER, gated=True)
    assert 'noindex' in html and 'id="gate"' in html or "ov.id = 'gate'" in html


def test_index_list_thumbs_are_a_hair_bigger():
    """Simon on his phone, 2026-09-02: 64×48 read small — 84×63 with a 92px column."""
    css = publish._INDEX_STYLE
    assert 'width:84px; height:63px' in css and '92px minmax(0,1fr)' in css and '64px' not in css


def test_index_phone_rows_fit_the_screen_and_signin_sits_top_right():
    """Audit 2026-09-19: four fixed columns (92+118+96 + gaps) overflowed a 390px phone and the whole
    register scrolled sideways; 'sign in' stacked on top of the mark. Thumbs stay 84×63 (Simon, 09-02)."""
    css = publish._INDEX_STYLE
    phone = css.split('@media (max-width: 699px)')[1].split('@media')[0]
    assert 'grid-template-columns:92px minmax(0,1fr) 88px' in phone
    assert 'span:empty:not(.t)' in phone              # the no-photo placeholder is an empty span too — keep it
    assert 'width:' not in phone                      # no shrinking the thumbs on a phone
    assert '.hero .signin { position:absolute; top:14px; right:18px' in css


def test_index_hero_has_featured_knife_card_and_stats_strip():
    rows = _rows()
    rows[1]['for_sale'], rows[1]['asking_price'] = 1, 500
    html = publish._index_html(rows, USER, gated=False)
    feat = html.split('class="feat"')[1][:200]
    assert 'href="K01/"' in feat and 'IN THE PHOTO' in feat and 'Large Sebenza 31' in feat
    assert 'class="stats"' in html and '2020–2022' in html and '>1</b> for sale' in html and '>3</b> knives' in html


def test_index_stats_omit_missing_born_and_sale():
    rows = _rows(with_img=False)
    for r in rows:
        r['born_on'], r['born'] = '', ''
    html = publish._index_html(rows, USER, gated=False)
    assert 'class="feat"' not in html          # no photo → no featured card
    assert 'for sale' not in html and 'born</span>' not in html and '>3</b> knives' in html


def test_index_has_og_image_from_hero_when_not_gated():
    html = publish._index_html(_rows(), USER, gated=False)
    assert '<meta property="og:image" content="' in html
    assert '/@simon-collector/img/K01.jpg">' in html.split('property="og:image"')[1][:120]
    # gated / photo-less → the generic site card stands in, never a knife photo
    for h in (publish._index_html(_rows(), USER, gated=True),
              publish._index_html(_rows(with_img=False), USER, gated=False)):
        assert '/img/' not in h.split('property="og:image"')[1][:120]


# --- featured_knife_id pins the hero (hero-pin, 2026-09-03) -----------------

def test_index_featured_tag_becomes_hero_and_card():
    rows = _rows()
    html = publish._index_html(rows, USER, gated=False, featured_tag='K02')
    assert 'img/K02.jpg' in html.split('class="hero-bg"')[1][:60]
    feat = html.split('class="feat"')[1][:200]
    assert 'href="K02/"' in feat and 'IN THE PHOTO' in feat
    assert '<meta property="og:image" content="' in html
    assert '/@simon-collector/img/K02.jpg">' in html.split('property="og:image"')[1][:120]


def test_index_unset_featured_falls_back_to_first_with_img():
    html = publish._index_html(_rows(), USER, gated=False, featured_tag=None)
    assert 'img/K01.jpg' in html.split('class="hero-bg"')[1][:60]


def test_index_featured_without_photo_falls_back():
    rows = _rows(with_img=False)
    rows[1]['img'], rows[1]['img_t'] = None, None    # K02 exists but has no photo
    rows[0]['img'], rows[0]['img_t'] = 'K01.jpg', 'K01_t.jpg'
    html = publish._index_html(rows, USER, gated=False, featured_tag='K02')
    assert 'img/K01.jpg' in html.split('class="hero-bg"')[1][:60]


def test_index_featured_hidden_or_gone_falls_back():
    """A hidden/private/draft knife never makes it into `rows` — its tag
    resolving to nothing must fall back exactly like unset."""
    html = publish._index_html(_rows(), USER, gated=False, featured_tag='K99')
    assert 'img/K01.jpg' in html.split('class="hero-bg"')[1][:60]


# --- hero framing: blur-fill, never crop (Simon, 2026-09-02) ----------------

def test_index_hero_is_blur_filled_not_cropped():
    """A portrait card-spread pinned as the hero used to be center/cover-cropped to the
    middle band (pivot + cards, blade gone). Now the photo sits whole (contain) on a
    blurred, darkened copy of itself (cover) — framed, whatever its aspect."""
    html = publish._index_html(_rows(), USER, gated=False)
    assert '--hero:url(img/K01.jpg)' in html.split('class="hero-bg"')[1][:60]
    assert 'background-image:url(' not in html                       # the old cropping style is gone
    before = html.split('.hero-bg::before')[1].split('}')[0]
    assert 'var(--hero)' in before and 'cover' in before and 'filter:blur(' in before
    assert '.hero-bg::after' not in html                               # the sharp layer is NOT on the fixed backdrop
    # the sharp photo lives INSIDE the hero band (so its bottom — the knife — is never hidden
    # behind the page) and scrolls with the content; only the blurred backdrop parallaxes
    header = html.split('<header class="hero"')[1]
    assert header.startswith(' style="--hero:url(img/K01.jpg)">')
    assert header.split('>', 1)[1].lstrip().startswith('<div class="hero-photo"></div>')
    photo = html.split('.hero-photo {')[1].split('}')[0]
    assert 'var(--hero)' in photo and 'contain' in photo and 'blur' not in photo and 'position:absolute' in photo
    # `.hero > *` sets position:relative for stacking — the photo layer must re-assert absolute
    # or it collapses to nothing (found by screenshot, 2026-09-02)
    assert 'position:absolute' in html.split('.hero > .hero-photo {')[1].split('}')[0]
    assert '<div class="hero-photo"' not in publish._index_html(_rows(with_img=False), USER, gated=False)


# --- plan 14: any maker on the public side ---

def test_public_row_carries_maker_name_and_display_name_prefixes_other_brands():
    row = publish.public_row(_knife(maker_name='Chris Reeve Knives'), USER)
    assert row['maker_name'] == 'Chris Reeve Knives'
    assert publish.display_name(row) == 'Large Sebenza 31'          # CRK names unchanged
    h = _knife(maker='other', maker_name='Hinderer Knives', model='XM-18', ext={})
    assert publish.display_name(publish.public_row(h, USER)) == 'Hinderer Knives XM-18'
    s = _knife(maker='other', maker_name='Strider', model='Strider SNG', ext={})
    assert publish.display_name(publish.public_row(s, USER)) == 'Strider SNG'   # brand already in the model
    nameless = _knife(maker='other', maker_name='', model='Custom slipjoint', ext={})
    assert publish.display_name(publish.public_row(nameless, USER)) == 'Custom slipjoint'
    bare = _knife(maker='other', maker_name='Hinderer Knives', model='', ext={})
    assert publish.display_name(publish.public_row(bare, USER)) == 'Hinderer Knives K07'


def test_knife_page_shows_the_maker_row():
    row = publish.public_row(_knife(maker='other', maker_name='Hinderer Knives', model='XM-18', ext={}), USER)
    html = publish._knife_page(row, 'simon-collector', gated=False)
    assert '<th>Maker</th><td>Hinderer Knives</td>' in html
    assert '<h1>Hinderer Knives XM-18</h1>' in html


# --- OG card (2026-09-15): site card fallback + twitter/site_name/url ---------

def test_head_carries_site_name_twitter_card_and_url():
    html = publish._index_html(_rows(), USER, gated=False)
    head = html.split('<body>')[0]
    assert '<meta property="og:site_name" content="blade-book">' in head
    assert '<meta name="twitter:card" content="summary_large_image">' in head
    base = publish._public_base()
    assert f'<meta property="og:url" content="{base}/@simon-collector/">' in head
    knife = publish._knife_page(_rows()[0], USER['handle'], gated=False).split('<body>')[0]
    assert f'<meta property="og:url" content="{base}/@simon-collector/K01/">' in knife


def test_head_falls_back_to_site_card_when_no_hero():
    """No hero (no photos, or gated) → the generic site card, never a knife photo."""
    for html in (publish._index_html(_rows(with_img=False), USER, gated=False),
                 publish._index_html(_rows(), USER, gated=True),
                 publish._knife_page(_rows()[0], USER['handle'], gated=True)):
        head = html.split('<body>')[0]
        og = head.split('property="og:image"')[1][:120]
        assert f'content="{publish._public_base()}/og.jpg">' in og
        assert '/img/' not in og
        assert '<meta property="og:image:width" content="1200">' in head
        assert '<meta property="og:image:height" content="630">' in head
    # a real hero keeps its own photo and carries no (wrong) 1200x630 dims
    hero = publish._index_html(_rows(), USER, gated=False).split('<body>')[0]
    assert '/@simon-collector/img/K01.jpg">' in hero.split('property="og:image"')[1][:120]
    assert 'og:image:width' not in hero


# --- the mark (2026-09-27): the photo untouched, a plate under it ---

CREAM = (250, 246, 238)


def _flat_jpeg(w, h, rgb):
    buf = io.BytesIO()
    Image.new('RGB', (w, h), rgb).save(buf, 'JPEG', quality=95)
    return buf.getvalue()


def _glorious(**over):
    return _knife(tag='K80', born_on='2014-02-06', variant='21 CGG "Glorious"',
                  maker_name='Chris Reeve Knives',
                  ext={'generation': '21', 'size': 'Large', 'handle_treatment': 'CGG',
                       'graphic_name': 'Glorious'}, **over)


def _export(tmp_path, jpeg, row=None, **kw):
    store = MemStore({'1/1/1.jpg': jpeg})
    name, tname = publish.export_hero(store, _hero_knife(), 'pub', str(tmp_path), row=row, **kw)
    return (Image.open(os.path.join(tmp_path, name)).convert('RGB'),
            Image.open(os.path.join(tmp_path, tname)).convert('RGB'))


def _pixels(im):
    raw = im.convert('RGB').tobytes()
    return zip(raw[0::3], raw[1::3], raw[2::3])


def _near(px, rgb, tol=8):
    return all(abs(a - b) <= tol for a, b in zip(px, rgb))


def test_full_name_carries_the_graphic():
    row = publish.public_row(_glorious(), USER)
    assert publish.full_name(row) == 'Large Sebenza 21 — Glorious'


def test_full_name_falls_back_to_the_edition_then_the_plain_name():
    row = publish.public_row(_knife(ext={'generation': '31', 'size': 'Small',
                                         'special_edition': 'Annual 2025'}), USER)
    assert publish.full_name(row) == 'Small Sebenza 31 — Annual 2025'
    assert publish.full_name(publish.public_row(_knife(), USER)) == 'Large Sebenza 31'


def test_plate_text_names_the_knife_and_its_page(monkeypatch):
    monkeypatch.setattr(publish, '_plate_base', lambda: 'blade-book.com')
    t = publish.plate_text(publish.public_row(_glorious(), USER), 'simon-collector')
    assert t['tag'] == 'K80'
    assert t['title'] == 'LARGE SEBENZA 21 — GLORIOUS'
    assert t['sub'] == 'born February 6, 2014  ·  Chris Reeve Knives  ·  @simon-collector'
    assert t['url'] == 'blade-book.com/@simon-collector/K80'


def test_plate_text_honours_a_hidden_birth_day():
    row = publish.public_row(_glorious(), dict(USER, hide_born_day=1))
    sub = publish.plate_text(row, 'simon-collector')['sub']
    assert 'born February 2014' in sub and '6,' not in sub


def test_plate_text_on_a_gated_register_names_nothing():
    # the key gate keeps model/edition out of anything that travels
    t = publish.plate_text(publish.public_row(_glorious(), USER), 'simon-collector', gated=True)
    assert t['title'] == '' and t['sub'] == '@simon-collector'
    assert 'Glorious' not in ' '.join(t.values()) and 'Sebenza' not in ' '.join(t.values())


def test_plate_base_defaults_to_the_public_base_without_the_scheme(monkeypatch):
    monkeypatch.setattr(publish, '_public_base', lambda: 'https://example.test/blade-book')
    monkeypatch.setattr(publish.config, 'get', lambda k, d=None: d)
    assert publish._plate_base() == 'example.test/blade-book'
    monkeypatch.setattr(publish.config, 'get',
                        lambda k, d=None: 'https://blade-book.com/' if k == 'PLATE_BASE' else d)
    assert publish._plate_base() == 'blade-book.com'


def test_export_hero_puts_a_cream_plate_under_the_photo(tmp_path):
    row = publish.public_row(_glorious(), USER)
    display, thumb = _export(tmp_path, _flat_jpeg(1200, 800, (90, 90, 90)), row=row)
    w, h = display.size
    assert w == 1200 and h > 800                       # the photo kept its size; the plate is extra
    assert _near(display.getpixel((3, h - 3)), CREAM)  # plate margin, clear of any type
    assert _near(display.getpixel((w - 3, h - 3)), CREAM)
    assert thumb.size == (800, 533)                    # the thumb stays clean and plateless


def test_export_hero_plate_has_type_on_it(tmp_path):
    row = publish.public_row(_glorious(), USER)
    display, _ = _export(tmp_path, _flat_jpeg(1200, 800, (90, 90, 90)), row=row)
    plate = display.crop((0, 802, 1200, display.size[1]))
    dark = sum(1 for p in _pixels(plate) if max(p) < 90)
    assert dark > 400                                  # ink on the cream


def test_export_hero_with_plate_still_fits_the_display_edge(tmp_path):
    row = publish.public_row(_glorious(), USER)
    display, _ = _export(tmp_path, _flat_jpeg(1800, 2400, (90, 90, 90)), row=row)
    assert max(display.size) <= publish.DISPLAY_EDGE


def test_plate_type_never_runs_off_the_plate(tmp_path):
    k = _knife(tag='K123', model='Extraordinarily Long Model Name That Goes On',
               maker='other', maker_name='A Very Long Knife Maker Name & Sons Cutlery Works',
               ext={'graphic_name': 'An Equally Long Graphic Name For Good Measure'})
    row = publish.public_row(k, USER)
    store = MemStore({'1/1/1.jpg': _flat_jpeg(900, 1200, (90, 90, 90))})
    name, _ = publish.export_hero(store, dict(_hero_knife(), tag='K123'),
                                  'a-handle-of-24-characters'[:24], str(tmp_path), row=row)
    display = Image.open(os.path.join(tmp_path, name)).convert('RGB')
    w, h = display.size
    top = next(y for y in range(h - 1, 0, -1) if not _near(display.getpixel((2, y)), CREAM)) + 3
    for y in range(top + 2, h):
        assert _near(display.getpixel((w - 2, y)), CREAM), y   # right margin stays clear
        assert _near(display.getpixel((1, y)), CREAM), y       # and the left


def test_export_hero_survives_a_missing_font(tmp_path, monkeypatch):
    # a publish must never fail over type
    monkeypatch.setattr(publish, 'FONT_DIR', str(tmp_path / 'nowhere'))
    row = publish.public_row(_glorious(), USER)
    display, _ = _export(tmp_path, _flat_jpeg(1200, 800, (90, 90, 90)), row=row)
    assert display.size[1] > 800


def test_export_hero_without_a_row_still_marks_and_plates(tmp_path):
    display, _ = _export(tmp_path, _flat_jpeg(1200, 800, (90, 90, 90)))
    assert display.size[1] > 800 and _near(display.getpixel((3, display.size[1] - 3)), CREAM)


def test_full_name_uses_the_variant_for_other_makers():
    # other makers have no graphic/edition fields; the variant is where it lives
    k = _knife(maker='mcnees', maker_name='McNees Knives', model='MAC 2',
               variant='Atomic Shockwave', ext={})
    assert publish.full_name(publish.public_row(k, USER)) == 'McNees Knives MAC 2 — Atomic Shockwave'
    # a CRK variant is shorthand for fields already in the name — never appended
    crk = _knife(variant='31 Inlay', ext={'generation': '31', 'size': 'Large'})
    assert publish.full_name(publish.public_row(crk, USER)) == 'Large Sebenza 31'


def test_the_photo_itself_is_untouched(tmp_path):
    # Simon 2026-09-27: "a subtle something that hooks it to us" — the plate, nothing on the knife
    display, _ = _export(tmp_path, _flat_jpeg(1200, 800, (30, 60, 160)))
    photo = display.crop((0, 0, 1200, 790))            # clear of the plate's rule line
    for band, want in zip(photo.split(), (30, 60, 160)):
        lo, hi = band.getextrema()
        assert want - 4 <= lo and hi <= want + 4, (lo, hi, want)


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


def test_a_tall_photo_never_pushes_the_name_off_the_screen():
    """Look step, 2026-09-27: a portrait card shot at full column width was ~1300px
    tall on a desktop, so the name and specs sat below the fold. The lead is capped
    to the viewport and letterboxed on the dark ground instead."""
    css = publish._KNIFE_STYLE
    assert '.lead img.hero { display:block; margin:0; width:100%; max-height:82vh; object-fit:contain;' in css
    assert '.lead { position:relative; margin:10px 0; background:#15130f;' in css


# --- final review fixes (2026-09-27) ---

def test_flip_script_leaves_browser_shortcuts_and_pinches_alone():
    """Review: Alt/Cmd+Left is the browser's Back, and a pinch on the photo is a
    zoom — neither may flip the page. Behaviour is proven in a browser
    (docs/superpowers/plans/2026-09-27-knife-page.md, final review); this pins the guards."""
    js = publish._FLIP_SCRIPT
    assert 'ev.altKey || ev.ctrlKey || ev.metaKey || ev.shiftKey' in js
    assert 'ev.touches.length > 1' in js
    assert 'visualViewport' in js
