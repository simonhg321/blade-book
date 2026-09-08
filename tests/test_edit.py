import pytest

from bb import edit


def test_core_fields_strings_enums_numbers_bools():
    out = edit.validate('crk', {
        'model': ' Sebenza ', 'born_on_precision': 'day', 'born_on_source': 'card',
        'condition': 2, 'blade_length_in': '3.625', 'has_box': True, 'has_pouch': 0,
    })
    assert out == {'model': 'Sebenza', 'born_on_precision': 'day', 'born_on_source': 'card',
                   'condition': 2, 'blade_length_in': 3.625, 'has_box': 1, 'has_pouch': 0}
    for bad in ({'born_on_precision': 'week'}, {'condition': 7}, {'blade_length_in': 'long'},
                {'blade_length_in': 99}, {'has_box': 'yes'}, {'model': 'x' * 201}, {'model': 5}):
        with pytest.raises(edit.EditError):
            edit.validate('crk', bad)


def test_born_on_shape_and_owner_defaults():
    out = edit.validate('crk', {'born_on': '2022-03'})
    assert out == {'born_on': '2022-03', 'born_on_precision': 'month', 'born_on_source': 'owner'}
    assert edit.validate('crk', {'born_on': '2022'})['born_on_precision'] == 'year'
    assert edit.validate('crk', {'born_on': '2022-03-30'})['born_on_precision'] == 'day'
    out = edit.validate('crk', {'born_on': '2022-03-30', 'born_on_source': 'card', 'born_on_precision': 'day'})
    assert out['born_on_source'] == 'card'                      # explicit wins
    assert edit.validate('crk', {'born_on': ''}) == {'born_on': None, 'born_on_precision': '', 'born_on_source': ''}
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'born_on': '30/03/2022'})


def test_ext_enums_and_unknown_keys():
    out = edit.validate('crk', {'ext': {'size': 'Large', 'crk_sku': ' l31-1633 ', 'hand': ''}})
    assert out == {'ext': {'size': 'Large', 'crk_sku': 'l31-1633', 'hand': ''}}
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'ext': {'size': 'Medium'}})
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'ext': {'colour': 'red'}})
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'ext': 'Large'})
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'tag': 'K99'})
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'status': 'live'})
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'sale_status': 'for_sale'})       # sale has its own route
    with pytest.raises(edit.EditError):
        edit.validate('crk', ['not', 'a', 'dict'])
    with pytest.raises(KeyError):
        edit.validate('spyderco', {'model': 'x'})


def test_private_public_money_dates_and_hero():
    out = edit.validate('crk', {'price_paid': '450', 'acquired_from': 'GPK', 'acquired_date': '2024-01-05',
                                'location': 'safe', 'notes_private': 'x' * 2000, 'condition_note': '',
                                'notes_public': 'story', 'hero_photo': 2}, photo_seqs=(1, 2))
    assert out['price_paid'] == 450.0 and out['acquired_date'] == '2024-01-05' and out['hero_photo'] == 2
    assert out['condition_note'] is None and len(out['notes_private']) == 2000
    assert edit.validate('crk', {'price_paid': ''})['price_paid'] is None
    assert edit.validate('crk', {'hero_photo': None})['hero_photo'] is None
    for bad in ({'price_paid': -1}, {'price_paid': 'lots'}, {'acquired_date': '2024-1-5'},
                {'notes_private': 'x' * 2001}, {'hero_photo': 3}, {'hero_photo': 'two'}):
        with pytest.raises(edit.EditError):
            edit.validate('crk', bad, photo_seqs=(1, 2))


def test_flags_for_uses_the_maker_rules():
    k = {'maker': 'crk', 'model': 'Inkosi', 'blade_steel': 'S35VN', 'born_on': '2022-01-01',
         'ext': {'crk_sku': 'L31-0001', 'size': 'Large', 'generation': '31'}}
    assert any('Sebenza' in f for f in edit.flags_for(k))
    assert edit.flags_for({'maker': 'crk', 'model': 'Sebenza', 'ext': {}}) == []


# --- plan 14: editing maker_name re-files the knife ---

def test_maker_name_edit_resolves_maker_and_clears_crk_ext_on_the_way_out():
    out = edit.validate('crk', {'maker_name': ' Hinderer Knives '})
    assert out == {'maker_name': 'Hinderer Knives', 'maker': 'other', 'ext': {}}
    out = edit.validate('other', {'maker_name': 'Chris Reeve Knives'})
    assert out == {'maker_name': 'Chris Reeve Knives', 'maker': 'crk'}     # ext untouched coming in
    out = edit.validate('crk', {'maker_name': 'CRK', 'model': 'Inkosi'})
    assert out == {'maker_name': 'CRK', 'maker': 'crk', 'model': 'Inkosi'}  # same module: no clear
    out = edit.validate('other', {'maker_name': ''})
    assert out == {'maker_name': '', 'maker': 'other'}                      # blank keeps the module
    with pytest.raises(edit.EditError):
        edit.validate('crk', {'maker': 'other'})                            # maker itself is not editable
