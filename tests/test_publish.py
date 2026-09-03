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
    assert 'width:84px; height:63px' in css and '92px 1fr' in css and '64px' not in css


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
    assert 'property="og:image"' not in publish._index_html(_rows(), USER, gated=True)
    assert 'property="og:image"' not in publish._index_html(_rows(with_img=False), USER, gated=False)


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
    after = html.split('.hero-bg::after')[1].split('}')[0]
    assert 'var(--hero)' in before and 'cover' in before and 'filter:blur(' in before
    assert 'var(--hero)' in after and 'contain' in after and 'blur' not in after
