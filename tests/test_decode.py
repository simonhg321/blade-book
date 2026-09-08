import io
import json
import types

import pytest
from PIL import Image

from bb import decode, paths
from bb.makers import core, crk


def _jpeg(w, h, orient=None):
    img = Image.new('RGB', (w, h), (200, 100, 50))
    buf = io.BytesIO()
    if orient:
        exif = Image.Exif(); exif[274] = orient
        img.save(buf, 'JPEG', exif=exif)
    else:
        img.save(buf, 'JPEG')
    return buf.getvalue()


def test_prep_image_downscales_and_transposes():
    out = decode.prep_image(_jpeg(4032, 3024, orient=6))
    im = Image.open(io.BytesIO(out))
    assert im.format == 'JPEG' and max(im.size) <= decode.MAX_EDGE
    assert im.size[0] < im.size[1]  # orientation 6 → portrait after transpose
    assert decode.prep_image(b'not an image') is None


def test_prep_image_leaves_small_images_alone_but_reencodes():
    out = decode.prep_image(_jpeg(800, 600))
    assert Image.open(io.BytesIO(out)).size == (800, 600)


def test_prep_image_draft_mode_large_orientation():
    # Verify draft-mode decode doesn't spike RSS on large JPEGs; orient 6 rotates landscape to portrait
    img = Image.new('RGB', (8000, 6000), (200, 100, 50))
    buf = io.BytesIO()
    exif = Image.Exif(); exif[274] = 6
    img.save(buf, 'JPEG', exif=exif, quality=30)
    out = decode.prep_image(buf.getvalue())
    result = Image.open(io.BytesIO(out))
    assert max(result.size) <= decode.MAX_EDGE
    assert result.size[0] < result.size[1]  # portrait after orientation 6 transpose


def test_build_messages_shape():
    msgs = decode.build_messages([b'a', b'b'], 'Large 31', 'crk', no_card=True)
    assert len(msgs) == 1 and msgs[0]['role'] == 'user'
    content = msgs[0]['content']
    assert [c['type'] for c in content] == ['image', 'image', 'text']
    assert content[0]['source'] == {'type': 'base64', 'media_type': 'image/jpeg', 'data': 'YQ=='}
    assert 'Large 31' in content[-1]['text'] and 'NO birth card' in content[-1]['text']
    assert 'Chris Reeve' in content[-1]['text']


def _fake_result(**over):
    r = {f: '' for f in core.CORE_FIELDS}
    r.update({'blade_length_in': None, 'condition': 1, 'has_box': True, 'has_card': True,
              'has_papers': False, 'has_pouch': True, 'has_lanyard': False, 'has_spare_hardware': False,
              'model': 'Sebenza', 'blade_steel': 'CPM MagnaCut', 'born_on': '2025-09-29',
              'born_on_precision': 'day', 'born_on_source': 'card', 'blade_shape': 'Drop Point'})
    r['ext'] = {k: '' for k in crk.EXT_PROPS}
    r['ext'].update({'generation': '31', 'size': 'Large', 'crk_sku': 'L31-1400-0004', 'hand': 'right'})
    r['card_text'] = 'LARGE SEBENZA 31\nCPM MagnaCut 63-64 RC\nBorn on 09/29/2025'
    r['no_card'] = False
    r['confidence'] = {f: 'high' for f in list(core.CORE_FIELDS) + list(crk.EXT_PROPS)}
    r['reasoning'] = 'Card read verbatim.'
    r.update(over)
    return r


class _StubClient:
    """Looks enough like anthropic.Anthropic for ClaudeDecoder: records the
    request, returns a canned response object."""
    def __init__(self, text, stop_reason='end_turn'):
        self.text, self.stop_reason, self.requests = text, stop_reason, []
        self.messages = types.SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        block = types.SimpleNamespace(type='text', text=self.text)
        usage = types.SimpleNamespace(input_tokens=5000, output_tokens=400)
        return types.SimpleNamespace(content=[block], usage=usage, stop_reason=self.stop_reason)


def test_claude_decoder_forces_schema_and_parses(env):
    stub = _StubClient(json.dumps(_fake_result()))
    d = decode.ClaudeDecoder('claude-sonnet-5', client=stub)
    out = d.decode([b'x'], 'note', 'crk')
    req = stub.requests[0]
    assert req['model'] == 'claude-sonnet-5'
    assert req['output_config']['format']['type'] == 'json_schema'
    assert req['output_config']['format']['schema']['additionalProperties'] is False
    assert 'tools' not in req and 'thinking' not in req  # one plain call, no agent
    assert out.core['model'] == 'Sebenza' and out.ext['generation'] == '31'
    assert out.card_text.startswith('LARGE SEBENZA 31')
    assert out.confidence['model'] == 'high'
    assert out.flags == []  # MagnaCut 2025 + L31 Large 31: consistent
    assert out.input_tokens == 5000 and out.output_tokens == 400 and out.model == 'claude-sonnet-5'
    assert out.latency_ms >= 0


def test_claude_decoder_flags_inconsistent_result():
    stub = _StubClient(json.dumps(_fake_result(blade_steel='S30V')))
    out = decode.ClaudeDecoder('claude-haiku-4-5', client=stub).decode([b'x'], '', 'crk')
    assert any('S30V' in f for f in out.flags)


def test_claude_decoder_refusal_and_bad_json_raise():
    with pytest.raises(decode.DecodeError):
        decode.ClaudeDecoder('m', client=_StubClient('{}', stop_reason='refusal')).decode([b'x'], '', 'crk')
    with pytest.raises(decode.DecodeError):
        decode.ClaudeDecoder('m', client=_StubClient('not json')).decode([b'x'], '', 'crk')
    with pytest.raises(decode.DecodeError):  # schema-shaped but missing keys → still an error, never a KeyError
        decode.ClaudeDecoder('m', client=_StubClient('{"model": "x"}')).decode([b'x'], '', 'crk')


def test_claude_decoder_max_tokens_raises_truncated():
    stub = _StubClient('{}', stop_reason='max_tokens')
    with pytest.raises(decode.DecodeError, match='truncated'):
        decode.ClaudeDecoder('m', client=stub).decode([b'x'], '', 'crk')


def test_claude_decoder_no_images_raises_decode_error():
    with pytest.raises(decode.DecodeError):
        decode.ClaudeDecoder('m', client=_StubClient('{}')).decode([], '', 'crk')


def test_sdk_timeout_budget_fits_gunicorn_timeout():
    # 2 attempts (1 retry) × SDK_TIMEOUT_S must stay under gunicorn's --timeout 120
    assert decode.SDK_TIMEOUT_S * (decode.SDK_MAX_RETRIES + 1) < 120


def test_claude_decoder_wraps_sdk_errors():
    class Boom(_StubClient):
        def _create(self, **kw):
            raise RuntimeError('socket closed')
    with pytest.raises(decode.DecodeError) as e:
        decode.ClaudeDecoder('m', client=Boom('')).decode([b'x'], '', 'crk')
    assert 'socket closed' in str(e.value)


def test_fake_decoder_returns_and_records():
    f = decode.FakeDecoder(_fake_result())
    out = f.decode([b'a', b'b'], 'hi', 'crk')
    assert out.core['model'] == 'Sebenza' and f.calls == [(2, 'hi', 'crk', False)]
    with pytest.raises(decode.DecodeError):
        decode.FakeDecoder(decode.DecodeError('nope')).decode([b'a'], '', 'crk')


def test_no_decoder_and_from_env(env, monkeypatch):
    with pytest.raises(decode.DecodeError):
        decode.NoDecoder().decode([b'a'], '', 'crk')
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    from bb import config
    monkeypatch.setattr(config, 'get', lambda k, d=None: {'DECODER_MODEL': 'claude-haiku-4-5'}.get(k, d))
    assert isinstance(decode.from_env(), decode.NoDecoder)
    monkeypatch.setattr(config, 'get', lambda k, d=None: {'ANTHROPIC_API_KEY': 'sk-test', 'DECODER_MODEL': 'claude-haiku-4-5'}.get(k, d))
    d = decode.from_env()
    assert isinstance(d, decode.ClaudeDecoder) and d.model == 'claude-haiku-4-5'
    monkeypatch.setattr(config, 'get', lambda k, d=None: {'ANTHROPIC_API_KEY': 'sk-test'}.get(k, d))
    assert decode.from_env().model == decode.DEFAULT_MODEL


def test_cost_usd_uses_pinned_prices():
    assert decode.cost_usd('claude-haiku-4-5', 1_000_000, 0) == pytest.approx(1.00)
    assert decode.cost_usd('claude-sonnet-5', 0, 1_000_000) == pytest.approx(10.00)
    assert decode.cost_usd('claude-opus-5', 1_000_000, 1_000_000) == pytest.approx(30.00)
    assert decode.cost_usd('unknown-model', 1_000_000, 0) is None


def test_log_call_appends_jsonl(env):
    d = decode.FakeDecoder(_fake_result()).decode([b'a'], '', 'crk')
    decode.log_call(7, 3, 'claude-sonnet-5', True, decoded=d, latency_ms=1234)
    decode.log_call(7, 3, 'claude-sonnet-5', False, error='boom', latency_ms=10)
    lines = [json.loads(l) for l in open(paths.ai_log())]
    assert lines[0]['user'] == 7 and lines[0]['knife'] == 3 and lines[0]['ok'] is True
    assert lines[0]['model'] == 'claude-sonnet-5' and 'cost_usd' in lines[0] and lines[0]['ms'] == 1234
    assert lines[1]['ok'] is False and lines[1]['error'] == 'boom'


def test_images_for_reads_slots_in_order_and_skips_junk(env):
    from bb import store as store_mod
    st = store_mod.from_paths()
    st.put('1/1/2.jpg', _jpeg(600, 400))
    st.put('1/1/1.jpg', _jpeg(500, 500))
    st.put('1/1/3.dng', b'raw bytes pillow cannot read')
    knife = {'photos': [{'seq': 1, 'store_key': '1/1/1.jpg'}, {'seq': 2, 'store_key': '1/1/2.jpg'},
                        {'seq': 3, 'store_key': '1/1/3.dng'}, {'seq': 4, 'store_key': '1/1/missing.jpg'}]}
    out = decode.images_for(st, knife)
    assert len(out) == 2
    assert Image.open(io.BytesIO(out[0])).size == (500, 500)


# --- plan 14: any maker -------------------------------------------------------

def test_build_messages_is_maker_agnostic():
    msgs = decode.build_messages([b'a'], 'a Hinderer', 'crk')
    text = msgs[0]['content'][-1]['text']
    assert 'maker_name' in text and 'Chris Reeve' in text and 'a Hinderer' in text


def test_schema_ext_is_the_union_of_all_makers(env):
    stub = _StubClient(json.dumps(_fake_result()))
    d = decode.ClaudeDecoder('claude-sonnet-5', client=stub)
    d.decode([b'x'], '', 'crk')
    schema = stub.requests[0]['output_config']['format']['schema']
    assert set(schema['properties']['ext']['required']) == set(decode.makers.union_ext_props())
    assert 'maker_name' in schema['required']


def test_decoded_resolves_maker_from_result():
    d = decode.FakeDecoder(_fake_result(maker_name='Chris Reeve Knives'))
    out = d.decode([b'x'], '', 'crk')
    assert out.maker == 'crk' and out.core['maker_name'] == 'Chris Reeve Knives'
    assert out.ext['generation'] == '31'

    d2 = decode.FakeDecoder(_fake_result(maker_name='Hinderer Knives', model='XM-18',
                                         card_text='XM-18 3.5 SPANTO'))
    out2 = d2.decode([b'x'], '', 'crk')
    assert out2.maker == 'other' and out2.ext == {} and out2.flags == []

    # empty maker_name and no card text: the hint wins
    d3 = decode.FakeDecoder(_fake_result(maker_name='', card_text=''))
    assert d3.decode([b'x'], '', 'crk').maker == 'crk'
    assert d3.decode([b'x'], '', 'other').maker == 'other'
