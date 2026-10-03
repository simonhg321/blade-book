# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""
bb/lookup.py — the second opinion when there is no birth card (2026-09-26).

The decoder reads the knife; when the collector ticks "no birth card" (or the
model finds none in frame) the route runs two small TEXT-ONLY calls:
  stage 1  the decoder's guess + the owner's note, web search on → findings
           with URLs (the model searches the way a collector would:
           "Chris Reeve Annual Sebenza 2003 inlay");
  stage 2  those findings → the schema below, no tools.
No photos go to the lookup: the decoder already described the knife, and one
call that carried photos + web search + a forced JSON format silently lost the
tool and cost 140k tokens (probed 2026-09-26). Two text calls: ~10 s, ~$0.05.

Rules, in order of importance:
  1. Only blanks are filled. Anything the decoder read off the knife stands.
  2. Every filled field is marked medium and the sources are shown.
  3. The decode never fails because the lookup did.
  4. Opt-out with BLADEBOOK_LOOKUP=0. Nothing runs when there is a card.
  5. It runs in the BACKGROUND (a daemon thread per lookup; ~40 s at low
     effort, ~$0.15) and writes its result onto the knife + a state file at
     DATA_DIR/lookups/<knife_id>.json that GET /knives/<id>/lookup serves and
     the add page polls. Stage timings probed 2026-09-26: 38 s / 2 searches at
     effort low, 104 s / 3 searches at medium — neither fits in a request.

Simon's spec: "If I tick it can the decoder do its best job then do an image
search of the knife and try to find a match with details and add them?"
"""
import dataclasses
import datetime as dt
import json
import logging
import os
import re
import threading
import time

from bb import config, paths

log = logging.getLogger('blade-book.lookup')

DEFAULT_MODEL = 'claude-sonnet-5'
MAX_SEARCHES = 2
EFFORT = 'low'           # 38 s vs 104 s at medium; the findings were the same knife
MAX_TOKENS = 4000        # findings + JSON are short; thinking shares this budget
SDK_TIMEOUT_S = 100      # per stage; runs in a background thread, gunicorn's --timeout does not apply
STALE_S = 300            # a pending state older than this is reported as failed (worker died)
SDK_MAX_RETRIES = 0      # never double a paid search

CORE_FIELDS = ('model', 'blade_steel', 'blade_shape', 'blade_length_in', 'handle_material',
               'born_on', 'born_on_precision')
EXT_FIELDS = ('size', 'generation', 'special_edition', 'inlay_material')
FIELD_NAMES = CORE_FIELDS + EXT_FIELDS
_URL = re.compile(r'^https?://', re.I)


def _nullable(t):
    return {'anyOf': [{'type': t}, {'type': 'null'}]}


SCHEMA = {
    'type': 'object',
    'properties': {
        'summary': {'type': 'string', 'description': 'Two sentences: what the knife most likely is and how sure'},
        'confirmed': {'type': 'boolean', 'description': 'true only if a source shows the same configuration'},
        'fields': {
            'type': 'object',
            'properties': {**{f: _nullable('string') for f in FIELD_NAMES if f != 'blade_length_in'},
                           'blade_length_in': _nullable('number')},
            'required': list(FIELD_NAMES),
            'additionalProperties': False,
        },
        'sources': {
            'type': 'array',
            'items': {'type': 'object',
                      'properties': {'url': {'type': 'string'}, 'title': {'type': 'string'}, 'why': {'type': 'string'}},
                      'required': ['url', 'title', 'why'], 'additionalProperties': False},
        },
    },
    'required': ['summary', 'confirmed', 'fields', 'sources'],
    'additionalProperties': False,
}

SEARCH_PROMPT = (
    'A collector photographed a knife that has NO birth card or paperwork. A first pass read the knife from the '
    'photos and produced the description below; the empty fields are what the photos alone could not settle.\n\n'
    'Use web search the way a collector would (maker + model + year + inlay, "annual", "limited", dealer and '
    'forum listings; two or three queries at most). Then write your findings as plain text: what the knife most '
    'likely is, which of the empty fields the sources settle and to what value, which remain unknown, and the URL '
    'you took each claim from. Never invent a birth date to the day: year precision at most, from an engraving '
    'or a documented production year. Be brief and concrete.\n\n'
    'If label_text or first_pass_reasoning quotes a printed model name or SKU, that printed text outranks the '
    'first pass\'s model field: search for what is PRINTED and say so in the findings.\n\n'
    'First-pass description (JSON, empty = unknown): {guess}\n'
    "Owner's note: {note!r}\n"
)

EXTRACT_PROMPT = (
    'Below are web findings about a knife with no paperwork, and the first-pass description it was matched '
    'against. Fill the schema from the findings ONLY. A field is null unless a finding names its value for THIS '
    'configuration; never copy a value from the description itself. `confirmed` is true only if a source shows '
    'the same configuration. `sources` lists the URLs the findings cite, with one line each on why they matter. '
    'born_on is YYYY, YYYY-MM or YYYY-MM-DD; born_on_precision is year, month or day; blade_length_in is inches; '
    'size is Small or Large; special_edition e.g. "Annual 2003".\n\n'
    'First-pass description: {guess}\n\nFindings:\n{findings}\n'
)


class LookupFailed(Exception):
    pass


@dataclasses.dataclass
class Lookup:
    summary: str
    confirmed: bool
    fields: dict
    sources: list
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0


def build_messages(guess, note):
    """Stage 1 (search): text only. No photos — see the module docstring."""
    return [{'role': 'user', 'content': SEARCH_PROMPT.format(guess=json.dumps(guess, sort_keys=True),
                                                             note=note or '')}]


def build_extract_messages(guess, findings):
    """Stage 2 (extract): the findings → SCHEMA, no tools."""
    return [{'role': 'user', 'content': EXTRACT_PROMPT.format(guess=json.dumps(guess, sort_keys=True),
                                                              findings=findings)}]


def _to_lookup(data, model):
    try:
        fields_in = data['fields'] or {}
        fields = {f: fields_in[f] for f in FIELD_NAMES if f in fields_in and fields_in[f] not in (None, '')}
        sources = [{'url': s['url'], 'title': str(s.get('title') or ''), 'why': str(s.get('why') or '')}
                   for s in (data.get('sources') or []) if isinstance(s, dict) and _URL.match(str(s.get('url') or ''))]
        return Lookup(summary=str(data['summary'] or ''), confirmed=bool(data['confirmed']),
                      fields=fields, sources=sources[:5], model=model)
    except (KeyError, TypeError) as e:
        raise LookupFailed(f'model output missing {e}') from e


def merge_fields(core, ext, lk):
    """The fill rule on plain dicts (a Decoded's core/ext, or a stored knife
    row + its ext). Returns (core_updates, ext_updates, filled). Only blanks
    are filled; a filled born_on gets born_on_source 'lookup' and, if the
    precision is blank, 'year'."""
    core_up, ext_up, filled = {}, {}, []
    for f, v in lk.fields.items():
        if f in CORE_FIELDS:
            if core.get(f) in (None, ''):
                core_up[f] = v
                filled.append(f)
        elif f in EXT_FIELDS and f in ext and ext.get(f) in (None, ''):
            ext_up[f] = v
            filled.append(f)
    if 'born_on' in filled:
        core_up['born_on_source'] = 'lookup'
        if core.get('born_on_precision') in (None, '') and 'born_on_precision' not in core_up:
            core_up['born_on_precision'] = 'year'
            filled.append('born_on_precision')
    return core_up, ext_up, filled


def note_line(lk):
    line = 'Looked up: ' + (lk.summary.strip() or ('confirmed' if lk.confirmed else 'no match'))
    if lk.sources:
        line += ' Sources: ' + ', '.join(s['url'] for s in lk.sources)
    return line


def merge(d, lk):
    """Fill a decode.Decoded's blanks in place (the synchronous shape, kept for
    tests and scripts). Returns the field names filled."""
    core_up, ext_up, filled = merge_fields(d.core, d.ext, lk)
    d.core.update(core_up)
    d.ext.update(ext_up)
    for f in filled:
        d.confidence[f] = 'medium'
    d.reasoning = (d.reasoning.rstrip() + '\n' + note_line(lk)).strip()
    return filled


# ---- background run + state file ------------------------------------------

def state_path(knife_id):
    return os.path.join(paths.DATA_DIR, 'lookups', f'{int(knife_id)}.json')


def write_state(knife_id, **state):
    state.setdefault('ts', dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'))
    os.makedirs(os.path.dirname(state_path(knife_id)), exist_ok=True)
    tmp = state_path(knife_id) + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, state_path(knife_id))
    return state


def read_state(knife_id):
    """The state for the page: None (never run), or {'status': pending|done|error, ...}.
    A pending state older than STALE_S means the worker died mid-lookup."""
    try:
        with open(state_path(knife_id)) as f:
            st = json.load(f)
    except (OSError, ValueError):
        return None
    if st.get('status') == 'pending':
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(st['ts'])).total_seconds()
        except (KeyError, ValueError):
            age = STALE_S + 1
        if age > STALE_S:
            return {'status': 'error', 'error': 'lookup timed out', 'ts': st.get('ts')}
    return st


def clear_state(knife_id):
    try:
        os.remove(state_path(knife_id))
    except OSError:
        pass


def spawn(fn):
    """Run fn in a daemon thread. Tests replace this with an inline call."""
    threading.Thread(target=fn, daemon=True).start()


def run(impl, owner_id, knife_id, guess, note, tag='', handle=''):
    """The background job: lookup → fill the stored knife's blanks → state
    file → ledger line. Never raises; every outcome lands in the state file.
    Imports db/decode/publish lazily so this module stays importable alone."""
    from bb import db, decode, publish
    t0 = time.monotonic()
    try:
        lk = impl.lookup(guess, note)
    except LookupFailed as e:
        ms = int((time.monotonic() - t0) * 1000)
        log.warning('lookup failed for %s/%s: %s', handle, tag, e)
        decode.log_call(owner_id, knife_id, getattr(impl, 'model', None), False, error=str(e)[:300],
                        latency_ms=ms, kind='lookup')
        return write_state(knife_id, status='error', error='lookup failed')
    except Exception as e:  # a bug must not leave the page polling forever
        log.exception('lookup crashed for %s/%s', handle, tag)
        decode.log_call(owner_id, knife_id, getattr(impl, 'model', None), False, error=f'crash: {e}'[:300],
                        kind='lookup')
        return write_state(knife_id, status='error', error='lookup failed')
    decode.log_call(owner_id, knife_id, lk.model, True, decoded=lk, kind='lookup')
    con = db.connect()
    try:
        k = db.get_knife(con, owner_id, knife_id)
        if k is None:                       # discarded while we were searching
            return write_state(knife_id, status='error', error='knife is gone')
        core_up, ext_up, filled = merge_fields(k, k.get('ext') or {}, lk)
        conf = dict(k.get('confidence') or {})
        for f in filled:
            conf[f] = 'medium'
        db.apply_lookup(con, owner_id, knife_id, core_up, ext_up, conf, note_line(lk),
                        detail=f'{lk.model} filled={",".join(filled) or "-"} ms={lk.latency_ms}')
        status = k['status']
    finally:
        con.close()
    log.info('looked up %s for @%s via %s: filled %s (%dms)', tag, handle, lk.model,
             ','.join(filled) or 'nothing', lk.latency_ms)
    if status == 'live':
        publish.schedule(owner_id)
    return write_state(knife_id, status='done', summary=lk.summary, confirmed=lk.confirmed,
                       filled=filled, sources=lk.sources)


class LookupBase:
    model = None

    def lookup(self, guess, note):
        raise NotImplementedError


class ClaudeLookup(LookupBase):
    def __init__(self, model=DEFAULT_MODEL, api_key=None):
        self.model, self._api_key, self._client = model, api_key, None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key, timeout=SDK_TIMEOUT_S,
                                               max_retries=SDK_MAX_RETRIES)
        return self._client

    def lookup(self, guess, note):
        t0 = time.monotonic()
        tin = tout = 0
        try:
            # stage 1: search. Free text; the server tool loops inside this one call.
            with self.client.messages.stream(
                    model=self.model, max_tokens=MAX_TOKENS, output_config={'effort': EFFORT},
                    tools=[{'type': 'web_search_20260209', 'name': 'web_search', 'max_uses': MAX_SEARCHES}],
                    messages=build_messages(guess, note)) as st:
                r1 = st.get_final_message()
            tin += int(getattr(r1.usage, 'input_tokens', 0) or 0)
            tout += int(getattr(r1.usage, 'output_tokens', 0) or 0)
            if getattr(r1, 'stop_reason', None) == 'refusal':
                raise LookupFailed('model refused (search)')
            findings = '\n'.join(b.text for b in r1.content if getattr(b, 'type', '') == 'text').strip()
            if not findings:
                raise LookupFailed('search returned no findings')
            # stage 2: extract. Schema-forced, no tools.
            r2 = self.client.messages.create(
                model=self.model, max_tokens=MAX_TOKENS,
                output_config={'format': {'type': 'json_schema', 'schema': SCHEMA}},
                messages=build_extract_messages(guess, findings))
            tin += int(getattr(r2.usage, 'input_tokens', 0) or 0)
            tout += int(getattr(r2.usage, 'output_tokens', 0) or 0)
        except LookupFailed:
            raise
        except Exception as e:  # SDK errors are many classes; the route only needs "failed"
            raise LookupFailed(f'{type(e).__name__}: {e}') from e
        ms = int((time.monotonic() - t0) * 1000)
        if getattr(r2, 'stop_reason', None) == 'refusal':
            raise LookupFailed('model refused (extract)')
        text = ''.join(b.text for b in r2.content if getattr(b, 'type', '') == 'text')
        lk = _to_lookup(_parse_json(text), self.model)
        lk.input_tokens, lk.output_tokens, lk.latency_ms = tin, tout, ms
        return lk


def _parse_json(text):
    try:
        return json.loads(text)
    except ValueError:
        m = re.search(r'\{.*\}', text, re.S)
        if not m:
            raise LookupFailed(f'model output was not JSON: {text[:80]!r}')
        try:
            return json.loads(m.group(0))
        except ValueError as e:
            raise LookupFailed(f'model output was not JSON: {text[:80]!r}') from e


class FakeLookup(LookupBase):
    """Tests: a canned result dict (or an exception to raise); records
    (n_images, guess, note) per call."""
    def __init__(self, result, model='fake'):
        self.result, self.model, self.calls = result, model, []

    def lookup(self, guess, note):
        self.calls.append((guess, note))
        if isinstance(self.result, Exception):
            raise self.result
        lk = _to_lookup(self.result, self.model)
        lk.input_tokens, lk.output_tokens, lk.latency_ms = 2000, 300, 7
        return lk


class NoLookup(LookupBase):
    def lookup(self, guess, note):
        raise LookupFailed('lookup not configured')


def enabled():
    return (config.get('BLADEBOOK_LOOKUP') or '1').strip().lower() not in ('0', 'off', 'false', 'no')


def from_env():
    key = config.get('ANTHROPIC_API_KEY')
    if not key or not enabled():
        return NoLookup()
    return ClaudeLookup(config.get('LOOKUP_MODEL', config.get('DECODER_MODEL', DEFAULT_MODEL)), api_key=key)
