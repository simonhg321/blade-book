import datetime as dt
import json

import pytest

from bb import makers
from bb.makers import core, crk


def test_registry_knows_crk_only():
    assert makers.get('crk') is crk
    with pytest.raises(KeyError):
        makers.get('spyderco')


def test_schema_is_closed_and_covers_core_ext_confidence():
    s = core.build_schema(crk.EXT_PROPS)
    assert s['additionalProperties'] is False
    for f in core.CORE_FIELDS:
        assert f in s['properties'], f
        assert f in s['required'], f
    ext = s['properties']['ext']
    assert ext['additionalProperties'] is False
    assert set(ext['required']) == set(crk.EXT_PROPS)
    conf = s['properties']['confidence']
    assert conf['additionalProperties'] is False
    # Wire shape is two lists of field names (unlisted = high): a per-field enum
    # object put the whole schema over the API's grammar-size cliff (2026-09-09).
    assert set(conf['required']) == {'low', 'medium'}
    assert conf['properties']['low'] == {'type': 'array', 'items': {'type': 'string'}}
    for k in ('card_text', 'no_card', 'reasoning'):
        assert k in s['required'], k
    assert s['properties']['condition']['anyOf'][0]['enum'] == [1, 2, 3, 4]
    assert s['properties']['blade_length_in']['anyOf'][1] == {'type': 'null'}
    assert not any(isinstance(p.get('type'), list) for p in s['properties'].values())  # strict mode: no type arrays
    json.dumps(s)  # serialisable


def test_prompt_mentions_note_and_no_card_flag():
    p = core.BASE_PROMPT.format(maker_prompt=crk.PROMPT, note='Large 31, S35VN', no_card='')
    assert 'Large 31, S35VN' in p and 'Chris Reeve' in p
    p2 = core.BASE_PROMPT.format(maker_prompt=crk.PROMPT, note='', no_card=core.NO_CARD_LINE)
    assert 'no birth card' in p2


def test_age_months():
    today = dt.date(2026, 8, 28)
    assert core.age_months('2025-09-29', today) == 10
    assert core.age_months('2022-03-30', today) == 52
    assert core.age_months('2026-08', today) == 0
    assert core.age_months('2026', today) == 7
    assert core.age_months(None, today) is None
    assert core.age_months('garbage', today) is None


def test_norm_collapses_case_prefixes_and_aliases():
    assert crk.norm('blade_steel', 'CPM MagnaCut') == 'magnacut'
    assert crk.norm('blade_steel', 'CPM S35VN') == 'S35VN'.lower()
    assert crk.norm('blade_steel', 'Damascus (Chad Nichols ladder)') == 'damascus'
    assert crk.norm('blade_shape', 'Drop Point') == 'drop point'
    assert crk.norm('model', 'Large Sebenza 31') == 'sebenza'
    assert crk.norm('model', 'Inkosi') == 'inkosi'
    assert crk.norm('generation', 'Sebenza 31') == '31'
    assert crk.norm('size', 'large') == 'large'
    assert crk.norm('hand', 'Left-handed') == 'left'
    assert crk.norm('hand', '') == crk.norm('hand', None) == crk.norm('hand', 'right') == 'right'
    # Regular (1996-2008) and Classic (2000-2008) were separate pre-21 models — never fold them
    assert crk.norm('generation', 'Regular') == crk.norm('generation', 'Regular Sebenza') == 'regular'
    assert crk.norm('generation', 'Classic') == 'classic'
    assert crk.norm('generation', 'Regular') != crk.norm('generation', 'Classic')
    assert crk.norm('born_on', '2022-03-30') == '2022-03-30'
    assert crk.norm('born_on', '2011-12-01') == crk.norm('born_on', '2011-12') == '2011-12'
    assert crk.norm('born_on', '2008-01-01') == crk.norm('born_on', '2008') == '2008'
    assert crk.norm('inlay_material', 'Elforyn (Super Tusk)') == crk.norm('inlay_material', 'elforyn') == 'elforyn'
    assert crk.norm('has_box', True) == '1' and crk.norm('has_box', 0) == '0'
    assert crk.norm('crk_sku', 'l31-1633') == 'L31-1633'
    assert crk.norm('anything', None) == ''


def test_flags_sku_vs_size_and_model():
    core_ = {'model': 'Sebenza', 'blade_steel': 'S35VN', 'born_on': '2022-03-30'}
    assert crk.flags(core_, {'crk_sku': 'L31-1633', 'size': 'Large', 'generation': '31'}) == []
    f = crk.flags(core_, {'crk_sku': 'S31-1633', 'size': 'Large', 'generation': '31'})
    assert any('SKU S31-' in x and 'Small' in x for x in f)
    f = crk.flags({'model': 'Inkosi', 'born_on': '2022-03-30'}, {'crk_sku': 'L31-0001', 'size': 'Large', 'generation': '31'})
    assert any('Sebenza' in x for x in f)


def test_flags_steel_vs_era():
    base = {'model': 'Sebenza'}
    assert crk.flags({**base, 'blade_steel': 'S30V', 'born_on': '2014-01-01'}, {}) == []
    assert any('S30V' in x for x in crk.flags({**base, 'blade_steel': 'S30V', 'born_on': '2019-01-01'}, {}))
    assert any('MagnaCut' in x for x in crk.flags({**base, 'blade_steel': 'MagnaCut', 'born_on': '2020-06-01'}, {}))
    assert crk.flags({**base, 'blade_steel': 'MagnaCut', 'born_on': '2025-09-29'}, {}) == []
    assert any('S45VN' in x for x in crk.flags({**base, 'blade_steel': 'S45VN', 'born_on': '2019-06-01'}, {}))
    assert crk.flags({**base, 'blade_steel': 'S45VN', 'born_on': '2022-06-01'}, {}) == []


def test_flags_future_date_and_hand_mismatch():
    f = crk.flags({'model': 'Sebenza', 'born_on': '2999-01-01'}, {})
    assert any('future' in x for x in f)
    f = crk.flags({'model': 'Sebenza', 'born_on': '2022-01-01'}, {'hand': 'left', 'hand_on_box': 'right'})
    assert any('hand' in x.lower() for x in f)
    assert crk.flags({'model': 'Sebenza', 'born_on': None}, {}) == []


def test_flags_today_parameter():
    f = crk.flags({'model': 'Sebenza', 'born_on': '2027-01-01'}, {}, today=dt.date(2026, 8, 28))
    assert any('future' in x for x in f)
    f = crk.flags({'model': 'Sebenza', 'born_on': '2027-01-01'}, {}, today=dt.date(2027, 6, 1))
    assert not any('future' in x for x in f)


def test_core_born_date_precisions():
    import datetime as dt
    from bb.makers import core
    assert core.born_date('2008-03-14') == dt.date(2008, 3, 14)
    assert core.born_date('2008-03') == dt.date(2008, 3, 1)
    assert core.born_date('2008') == dt.date(2008, 1, 1)
    assert core.born_date(None) is None and core.born_date('') is None
    assert core.born_date('2008-13-40') is None and core.born_date('March 2008') is None


# --- plan 14: any maker -------------------------------------------------------
from bb.makers import other


def test_registry_knows_crk_and_other():
    assert makers.get('crk') is crk and makers.get('other') is other
    assert makers.MAKERS == ('crk', 'other')


def test_maker_name_is_a_core_field():
    assert 'maker_name' in core.CORE_FIELDS
    assert core.CORE_PROPS['maker_name']['type'] == 'string'


def test_other_module_is_minimal_and_never_flags():
    assert other.EXT_PROPS == {}
    assert other.flags({'model': 'XM-18', 'born_on': '2099-01-01'}, {}) == []
    assert other.norm('model', ' XM-18 ') == 'xm-18'
    assert 'maker' in other.PROMPT.lower()


def test_resolve_maker_from_brand_or_model_names():
    assert makers.resolve('Chris Reeve Knives') == 'crk'
    assert makers.resolve('CRK') == 'crk'
    assert makers.resolve('chris reeve') == 'crk'
    assert makers.resolve('Hinderer Knives') == 'other'
    assert makers.resolve('Strider') == 'other'
    # empty brand: the card text can still say
    assert makers.resolve('', card_text='LARGE SEBENZA 31\nBorn on 09/29/2025') == 'crk'
    assert makers.resolve('', card_text='XM-18 3.5" Spanto') == 'other'
    # nothing readable → the caller's fallback
    assert makers.resolve('', card_text='', fallback='crk') == 'crk'
    assert makers.resolve('', card_text='', fallback='other') == 'other'
    assert makers.resolve(None) == 'other'


def test_union_ext_props_and_combined_prompt():
    u = makers.union_ext_props()
    assert set(u) == set(crk.EXT_PROPS)          # 'other' adds nothing today
    p = makers.combined_prompt()
    assert 'Chris Reeve' in p and 'maker_name' in p
    assert p.index('maker_name') < p.index('Chris Reeve')   # detection rule comes first


def test_db_maker_keys_mirror_the_registry():
    from bb import db
    assert db.MAKER_KEYS == makers.MAKERS


def _property_count(schema):
    n = 0
    def walk(o):
        nonlocal n
        if isinstance(o, dict):
            if 'properties' in o:
                n += len(o['properties'])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(schema)
    return n


def test_decode_schema_stays_under_the_grammar_budget():
    """The API compiles a strict json_schema to a grammar and 400s above a size
    cliff ('The compiled grammar is too large'). Probed 2026-09-09 on sonnet-5:
    60 total properties compiled, 70 did not, and plan 14's one extra field
    took the live schema from 69 to 71 — every decode failed. Keep 10 in hand."""
    from bb.makers import union_ext_props
    assert _property_count(core.build_schema(union_ext_props())) <= 60


def test_crk_prompt_explains_handwritten_blade_line_and_double_lug():
    """2026-09-23: the first outside collector's card said 'Polished, Double Lug'
    by hand after the ticked steel line; the decoder read 'Dartcurg' and left
    hardware_note empty. The brief has to say what that line is."""
    p = crk.PROMPT.lower()
    assert 'handwritten' in p and 'double lug' in p and 'hardware_note' in p


def test_crk_knows_the_decorated_sebenza():
    """2026-09-23: a collector's 1995 card reads 'Large Decorated Sebenza' by hand;
    the decoder called it 'inlay, possibly mother of pearl'. Decorated is a 1990s
    engraved-handle series and must be a handle treatment the brief names."""
    assert 'decorated' in crk.EXT_PROPS['handle_treatment']['enum']
    p = crk.PROMPT.lower()
    assert 'decorated' in p and 'engrav' in p


def test_crk_knows_the_original_sebenza_before_july_1996():
    """2026-09-23: a collector's Dec 1995 card came back 'Regular'. She: 'it's an
    Original'. CRK-LORE: Original 1987 → July 1996, Regular from July 1996."""
    assert 'Original' in crk.EXT_PROPS['generation']['enum']
    assert crk.norm('generation', 'Original') == 'original'
    assert crk.norm('generation', 'Large Sebenza Regular') == 'regular'
    p = crk.PROMPT.lower()
    assert 'original' in p and '1996' in p


def test_crk_prompt_keeps_damascus_pattern_out_of_graphic_name():
    """2026-09-23: a collector's March 2000 card — Blade: 'Devin Thomas Damascus,
    Bronze Spiro Graph'. The decoder copied the damascus pattern into
    graphic_name. Her: 'the handle graphic has no name.'"""
    p = crk.PROMPT.lower()
    assert 'graphic_name' in p and 'damascus_pattern' in p
    assert 'no name' in p or 'unnamed' in p



def test_crk_knows_the_annual_sebenza_from_the_handle_engraving():
    """2026-09-26: Simon's cardless K90 — CR logo and '2003' cut into the handle,
    snakewood inlay — came back 'Mnandi, 2.75 in'. The year engraved beside the
    logo with a wood inlay is the Annual Sebenza (1999–2011, revived 2023); CRK
    never made an annual that was not a Sebenza. The brief must say so, and say
    the engraving is a legitimate born_on source when there is no card."""
    p = crk.PROMPT.lower()
    assert 'annual' in p and 'engrav' in p and 'special_edition' in p
    assert 'handle engraving' in p
    assert crk.norm('special_edition', ' Annual  2003 ') == 'annual 2003'
