# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
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
