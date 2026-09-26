# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""bb/lookup.py — the second opinion when there is no birth card (2026-09-26).

Simon's cardless 2003 Annual came back 'Mnandi'. His spec: when the collector
ticks 'no card', decode does its best, then a web lookup tries to find the
knife and fills in what it can. Rules: only blanks are filled, filled fields
are marked medium, sources are shown, the decode never fails because the
lookup did."""
import json

import pytest

from bb import decode, lookup
from tests.conftest import ok_result


def _decoded(**over):
    r = ok_result(**over)
    return decode._to_decoded(r, 'crk', 'fake')


def _lk(**over):
    r = {'summary': 'A 2003 Annual Sebenza with snakewood; matches the GPKnives annual page.',
         'confirmed': True,
         'fields': {'model': 'Sebenza', 'blade_steel': 'S30V', 'blade_shape': None, 'blade_length_in': 3.625,
                    'handle_material': None, 'born_on': '2003', 'born_on_precision': 'year',
                    'size': 'Large', 'generation': None, 'special_edition': 'Annual 2003',
                    'inlay_material': 'snakewood'},
         'sources': [{'url': 'https://example.com/annual-2003', 'title': 'Annual Sebenza 2003', 'why': 'same inlay'}]}
    r.update(over)
    return r


def test_merge_fills_only_blanks_and_marks_them_medium():
    d = _decoded(model='Sebenza', blade_steel='', born_on='', born_on_precision='', born_on_source='',
                 blade_length_in=None)
    d.ext['size'] = ''
    d.ext['inlay_material'] = 'burl'          # the decoder read something: never overwritten
    lk = lookup._to_lookup(_lk(), 'fake')
    filled = lookup.merge(d, lk)
    assert set(filled) == {'blade_steel', 'blade_length_in', 'born_on', 'born_on_precision', 'size', 'special_edition'}
    assert d.core['blade_steel'] == 'S30V' and d.core['blade_length_in'] == 3.625
    assert d.core['born_on'] == '2003' and d.core['born_on_precision'] == 'year'
    assert d.core['born_on_source'] == 'lookup'
    assert d.ext['size'] == 'Large' and d.ext['special_edition'] == 'Annual 2003'
    assert d.ext['inlay_material'] == 'burl' and d.core['model'] == 'Sebenza'
    for f in filled:
        assert d.confidence[f] == 'medium', f
    assert 'Looked up' in d.reasoning and 'example.com/annual-2003' in d.reasoning


def test_merge_with_nothing_to_fill_changes_nothing_but_the_note():
    d = _decoded()
    before = dict(d.core), dict(d.ext), dict(d.confidence)
    lk = lookup._to_lookup(_lk(fields={k: None for k in lookup.FIELD_NAMES}), 'fake')
    assert lookup.merge(d, lk) == []
    assert (dict(d.core), dict(d.ext), dict(d.confidence)) == before
    assert 'Looked up' in d.reasoning


def test_to_lookup_rejects_malformed_and_ignores_unknown_fields():
    with pytest.raises(lookup.LookupFailed):
        lookup._to_lookup({'summary': 'x'}, 'fake')
    lk = lookup._to_lookup(_lk(fields={'model': 'Sebenza', 'made_up': 'no'}), 'fake')
    assert lk.fields == {'model': 'Sebenza'}
    assert lk.sources[0]['url'].startswith('https://')
    # a non-http source is dropped, never rendered as a link
    lk = lookup._to_lookup(_lk(sources=[{'url': 'javascript:alert(1)', 'title': 'x', 'why': ''}]), 'fake')
    assert lk.sources == []


def test_build_messages_is_text_only_and_carries_guess_and_note():
    """No photos go to the lookup: one call with photos + web search + a forced
    JSON format lost the tool and cost 140k tokens (2026-09-26)."""
    msgs = lookup.build_messages({'model': 'Mnandi', 'ext': {'size': ''}}, 'bought from a collector, no papers')
    assert len(msgs) == 1 and isinstance(msgs[0]['content'], str)
    text = msgs[0]['content']
    assert '"Mnandi"' in text and 'no papers' in text and 'web search' in text.lower()
    assert 'printed' in text.lower() and 'label_text' in text
    ex = lookup.build_extract_messages({'model': 'Mnandi'}, 'The 2003 annual used snakewood (https://x.example).')
    assert 'snakewood' in ex[0]['content'] and 'null' in ex[0]['content']


def test_from_env_switches(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'k')
    monkeypatch.delenv('BLADEBOOK_LOOKUP', raising=False)
    assert isinstance(lookup.from_env(), lookup.ClaudeLookup)
    monkeypatch.setenv('BLADEBOOK_LOOKUP', '0')
    assert isinstance(lookup.from_env(), lookup.NoLookup)
    monkeypatch.delenv('BLADEBOOK_LOOKUP', raising=False)
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    assert isinstance(lookup.from_env(), lookup.NoLookup)


def test_fake_lookup_records_calls():
    fk = lookup.FakeLookup(_lk())
    lk = fk.lookup({'model': 'Mnandi'}, 'note')
    assert fk.calls == [({'model': 'Mnandi'}, 'note')]
    assert lk.confirmed is True and lk.model == 'fake'
    assert json.dumps(lk.sources)   # serialisable for the API response
