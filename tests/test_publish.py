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
