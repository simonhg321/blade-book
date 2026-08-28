# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/decode.py — the Decoder seam. Photos (≤1568 px JPEGs) + the owner's note go
to ONE schema-forced model call and come back as a `Decoded` record. No agent
loop, no tools, no in-decoder retries (the SDK's own 429/5xx retry, max 1, is
the only one). Every call — success or failure — is one line in ai_calls.jsonl.

`ClaudeDecoder` is the real one; `FakeDecoder` is what tests use; `NoDecoder`
is what runs when the key is missing so the app still boots.
"""
import base64
import dataclasses
import datetime as dt
import io
import json
import logging
import os
import time

from PIL import Image, ImageOps

from bb import config, makers, paths, photos
from bb.makers import core

log = logging.getLogger('blade-book.decode')

MAX_EDGE = 1568          # Anthropic's vision sweet spot; a 4032 px shot costs 4× the tokens
MAX_TOKENS = 4000
DEFAULT_MODEL = 'claude-sonnet-5'   # pending scripts/eval_decode.py — set DECODER_MODEL in .env
PRICES = {  # USD per 1M input / output tokens (Anthropic first-party, 2026-08-28)
    'claude-haiku-4-5': (1.00, 5.00),
    'claude-sonnet-5': (2.00, 10.00),
    'claude-opus-5': (5.00, 25.00),
}
SDK_TIMEOUT_S = 90       # gunicorn --timeout is 120; leave room to answer
SDK_MAX_RETRIES = 1


class DecodeError(Exception):
    pass


@dataclasses.dataclass
class Decoded:
    core: dict
    ext: dict
    card_text: str
    no_card: bool
    confidence: dict
    reasoning: str
    flags: list
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def prep_image(data):
    """Original bytes → ≤MAX_EDGE JPEG bytes (EXIF-transposed, RGB), or None if
    Pillow cannot decode it (raw DNG, HEIC without pillow-heif…)."""
    try:
        img = photos._open(data)
        if img is None:
            return None
        img = ImageOps.exif_transpose(img).convert('RGB')
    except (photos.TooBig, OSError, ValueError):
        return None
    img.thumbnail((MAX_EDGE, MAX_EDGE))
    buf = io.BytesIO()
    img.save(buf, 'JPEG', quality=85, optimize=True)
    return buf.getvalue()


def images_for(store, knife):
    """The knife's photos in slot order as prepped JPEGs; missing or undecodable
    ones are skipped (logged), never fatal."""
    out = []
    for p in sorted(knife['photos'], key=lambda p: p['seq']):
        try:
            data = store.get(p['store_key'])
        except KeyError:
            log.warning('decode: photo %s missing from store', p['store_key'])
            continue
        jpeg = prep_image(data)
        if jpeg is None:
            log.warning('decode: photo %s not decodable — skipped', p['store_key'])
            continue
        out.append(jpeg)
    return out


def build_messages(jpegs, note, maker, no_card=False):
    mod = makers.get(maker)
    content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                            'data': base64.standard_b64encode(j).decode()}}
               for j in jpegs]
    content.append({'type': 'text', 'text': core.BASE_PROMPT.format(
        maker_prompt=mod.PROMPT, note=note or '', no_card=core.NO_CARD_LINE if no_card else '')})
    return [{'role': 'user', 'content': content}]


def _to_decoded(data, maker, model):
    """Raw JSON dict (schema-shaped) → Decoded. Anything missing → DecodeError,
    never a KeyError leaking to the route."""
    mod = makers.get(maker)
    try:
        core_ = {f: data[f] for f in core.CORE_FIELDS}
        ext = {k: data['ext'][k] for k in mod.EXT_PROPS}
        conf = dict(data['confidence'])
        card_text, no_card, reasoning = data['card_text'], bool(data['no_card']), data['reasoning']
    except (KeyError, TypeError) as e:
        raise DecodeError(f'model output missing {e}') from e
    return Decoded(core=core_, ext=ext, card_text=card_text or '', no_card=no_card,
                   confidence=conf, reasoning=reasoning or '', flags=mod.flags(core_, ext),
                   model=model)


class Decoder:
    def decode(self, jpegs, note, maker='crk', no_card=False):
        raise NotImplementedError


class ClaudeDecoder(Decoder):
    def __init__(self, model, client=None, api_key=None):
        self.model = model
        self._client = client
        self._api_key = api_key

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=SDK_TIMEOUT_S,
                                               max_retries=SDK_MAX_RETRIES)
        return self._client

    def decode(self, jpegs, note, maker='crk', no_card=False):
        if not jpegs:
            raise DecodeError('no decodable photos')
        schema = core.build_schema(makers.get(maker).EXT_PROPS)
        t0 = time.monotonic()
        try:
            resp = self.client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS,
                output_config={'format': {'type': 'json_schema', 'schema': schema}},
                messages=build_messages(jpegs, note, maker, no_card))
        except Exception as e:  # SDK errors are many classes; the route only needs "failed"
            raise DecodeError(f'{type(e).__name__}: {e}') from e
        ms = int((time.monotonic() - t0) * 1000)
        if getattr(resp, 'stop_reason', None) == 'refusal':
            raise DecodeError('model refused')
        text = next((b.text for b in resp.content if getattr(b, 'type', '') == 'text'), '')
        try:
            data = json.loads(text)
        except ValueError as e:
            raise DecodeError(f'model output was not JSON: {text[:80]!r}') from e
        d = _to_decoded(data, maker, self.model)
        d.input_tokens = int(getattr(resp.usage, 'input_tokens', 0) or 0)
        d.output_tokens = int(getattr(resp.usage, 'output_tokens', 0) or 0)
        d.latency_ms = ms
        return d


class FakeDecoder(Decoder):
    """Tests: returns a canned schema-shaped dict (or raises the given exception)
    and records (n_images, note, maker, no_card) per call."""
    def __init__(self, result, model='fake'):
        self.result, self.model, self.calls = result, model, []

    def decode(self, jpegs, note, maker='crk', no_card=False):
        self.calls.append((len(jpegs), note, maker, no_card))
        if isinstance(self.result, Exception):
            raise self.result
        d = _to_decoded(self.result, maker, self.model)
        d.input_tokens, d.output_tokens, d.latency_ms = 1000, 100, 5
        return d


class NoDecoder(Decoder):
    model = None

    def decode(self, jpegs, note, maker='crk', no_card=False):
        raise DecodeError('decoder not configured')


def from_env():
    key = config.get('ANTHROPIC_API_KEY')
    if not key:
        log.warning('ANTHROPIC_API_KEY missing from .env — decode disabled (503)')
        return NoDecoder()
    return ClaudeDecoder(config.get('DECODER_MODEL', DEFAULT_MODEL), api_key=key)


def cost_usd(model, tin, tout):
    p = PRICES.get(model)
    if p is None:
        return None
    return round(tin / 1e6 * p[0] + tout / 1e6 * p[1], 6)


def log_call(owner_id, knife_id, model, ok, decoded=None, error=None, latency_ms=0):
    rec = {'ts': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
           'user': owner_id, 'knife': knife_id, 'model': model, 'ok': ok,
           'input_tokens': decoded.input_tokens if decoded else 0,
           'output_tokens': decoded.output_tokens if decoded else 0,
           'ms': latency_ms or (decoded.latency_ms if decoded else 0),
           'error': error}
    rec['cost_usd'] = cost_usd(model, rec['input_tokens'], rec['output_tokens'])
    try:
        os.makedirs(os.path.dirname(paths.ai_log()), exist_ok=True)
        with open(paths.ai_log(), 'a') as f:
            f.write(json.dumps(rec) + '\n')
    except OSError:
        log.exception('could not append to %s', paths.ai_log())
