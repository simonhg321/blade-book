# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""Maker modules: prompt fragments, extension schema and consistency rules per
maker. `crk` is the only deep module; `other` is the catch-all for every other
brand (plan 14). Adding a maker = one module + one line in _REGISTRY + one
entry in _DETECT.

The decoder never knows the maker up front: it runs ONE call with every
module's prompt and the union of their ext schemas, then `resolve()` picks the
module from what the model read (maker_name, then the card text)."""
import re

from bb.makers import crk, other

_REGISTRY = {'crk': crk, 'other': other}
MAKERS = tuple(_REGISTRY)   # routes check `maker in MAKERS` before ever calling get()
FALLBACK = 'other'

# module key → pattern that identifies the brand in maker_name or card text.
# Model names count: a card that only says "LARGE SEBENZA 31" is still a CRK.
_DETECT = (
    ('crk', re.compile(r'chris\s*reeve|\bcrk\b|sebenza|inkosi|mnandi|impinda|umnumzaan|'
                       r'\bti-?lock\b|\bzaan\b|green beret|professional soldier|sikayo', re.I)),
)


def get(maker):
    return _REGISTRY[maker]  # KeyError on purpose — routes validate first


def resolve(maker_name, card_text='', fallback=FALLBACK):
    """Which module a decoded record belongs to. maker_name first (as read),
    then the transcribed card; nothing recognisable → `fallback`, which is
    the caller's prior (the draft's maker) — a named-but-unknown brand is
    'other', an unreadable one keeps what the owner started with."""
    name = (maker_name or '').strip()
    for key, rx in _DETECT:
        if name and rx.search(name):
            return key
    if name:
        return 'other'
    for key, rx in _DETECT:
        if card_text and rx.search(card_text):
            return key
    return fallback if fallback in _REGISTRY else FALLBACK


def union_ext_props():
    """Every module's EXT_PROPS in one schema object (keys must not collide —
    prefix them per maker, e.g. crk_sku)."""
    out = {}
    for mod in _REGISTRY.values():
        for k, v in mod.EXT_PROPS.items():
            if k in out and out[k] is not v:
                raise ValueError(f'ext field {k!r} defined by two makers')
            out[k] = v
    return out


def combined_prompt():
    """The maker section of the decode prompt: each module's own text under a
    'when the maker is …' heading, deep modules first, the catch-all last."""
    parts = ['Maker rules (pick by maker_name as read from the card/box/blade):',
             'If the maker is Chris Reeve Knives, apply: ' + crk.PROMPT,
             'Otherwise: ' + other.PROMPT]
    return '\n'.join(parts)
