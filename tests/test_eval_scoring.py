import importlib.util
import os

from bb import decode
from bb.makers import core, crk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load():
    spec = importlib.util.spec_from_file_location('eval_decode', os.path.join(ROOT, 'scripts', 'eval_decode.py'))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def _row():
    return dict(tag='K02', crk_sku='L31-1633', steel='Damascus', born_on='2022-03-30', hand='left',
                damascus_smith='Chad Nichols', inlay_material='box elder burl', handle_treatment='inlay',
                has_box=1, has_card=1, has_pouch=1, has_lanyard=1)


def _model_row():
    return dict(family='Sebenza', model='Sebenza', generation='31', size='Large', blade_shape='Drop Point')


def test_truth_flattens_core_and_ext():
    ev = _load()
    t = ev.truth_from_row(_row(), _model_row())
    assert t['model'] == 'Sebenza' and t['blade_shape'] == 'Drop Point' and t['blade_steel'] == 'Damascus'
    assert t['generation'] == '31' and t['size'] == 'Large' and t['hand'] == 'left'
    assert t['crk_sku'] == 'L31-1633' and t['has_pouch'] == 1
    assert set(ev.SCORED_FIELDS) <= set(t)


def test_score_uses_norm_and_skips_blank_truth():
    ev = _load()
    truth = ev.truth_from_row(_row(), _model_row())
    pred = {**truth, 'blade_steel': 'DAMASCUS (Chad Nichols)', 'model': 'Large Sebenza 31', 'hand': 'Left-handed',
            'born_on': '2022-03-31', 'crk_sku': 'l31-1633'}
    s = ev.score(truth, pred)
    assert s['blade_steel'] is True and s['model'] is True and s['hand'] is True and s['crk_sku'] is True
    assert s['born_on'] is False
    truth['inlay_material'] = ''
    assert ev.score(truth, pred)['inlay_material'] is None


def test_predicted_flat_merges_core_and_ext():
    ev = _load()
    core_ = {f: '' for f in core.CORE_FIELDS}; core_['model'] = 'Inkosi'
    ext = {k: '' for k in crk.EXT_PROPS}; ext['size'] = 'Small'
    d = decode.Decoded(core=core_, ext=ext, card_text='', no_card=False, confidence={}, reasoning='', flags=[], model='m')
    p = ev.predicted_flat(d)
    assert p['model'] == 'Inkosi' and p['size'] == 'Small'


def test_summarize_and_table():
    ev = _load()
    results = [
        {'tag': 'K01', 'scores': {'model': True, 'born_on': False, 'size': None}, 'cost_usd': 0.02, 'ms': 900},
        {'tag': 'K02', 'scores': {'model': True, 'born_on': True, 'size': True}, 'cost_usd': 0.03, 'ms': 1100},
    ]
    s = ev.summarize(results)
    assert s['n'] == 2 and s['fields']['model'] == (2, 2) and s['fields']['born_on'] == (1, 2) and s['fields']['size'] == (1, 1)
    assert abs(s['mean_acc'] - (1.0 + 0.5 + 1.0) / 3) < 1e-9
    assert abs(s['cost_usd'] - 0.05) < 1e-9 and s['median_ms'] == 1000
    out = ev.format_table({'claude-haiku-4-5': s, 'claude-sonnet-5': s})
    assert 'claude-haiku-4-5' in out and 'born_on' in out and '50%' in out
