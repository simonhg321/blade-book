# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Validation for owner edits: which columns may change and what they accept.
Enums come from the maker schema so the edit form, the decoder and the DB agree.
Status, tag, owner and the sale columns are NOT editable here — they have their
own helpers/routes."""
import re

from bb import makers
from bb.makers import core

SHORT = 200
LONG = 2000
PRIVATE_TEXT = ('acquired_from', 'location', 'notes_private', 'condition_note')
PUBLIC_TEXT = ('notes_public',)
_BORN = re.compile(r'^\d{4}(-\d{2}(-\d{2})?)?$')
_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_PRECISION = {4: 'year', 7: 'month', 10: 'day'}


class EditError(ValueError):
    """User-facing message; becomes the 400 body."""


def _enum(prop):
    if 'enum' in prop:
        return prop['enum']
    for alt in prop.get('anyOf', ()):
        if 'enum' in alt:
            return alt['enum']
    return None


def _text(v, limit, what):
    if v is None:
        return ''
    if not isinstance(v, str):
        raise EditError(f'{what} must be text')
    v = v.strip()
    if len(v) > limit:
        raise EditError(f'{what} is longer than {limit} characters')
    return v


def _number(v, what, lo, hi):
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        raise EditError(f'{what} must be a number')
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise EditError(f'{what} must be a number') from None
    if not lo <= f <= hi:
        raise EditError(f'{what} out of range')
    return f


def _core(key, prop, v):
    if key.startswith('has_'):
        if isinstance(v, bool) or v in (0, 1):
            return 1 if v else 0
        raise EditError(f'{key} must be true or false')
    if key == 'condition':
        if v is None or v == '':
            return None
        if not isinstance(v, bool) and v in (1, 2, 3, 4):
            return int(v)
        raise EditError('condition must be 1–4')
    if key == 'blade_length_in':
        f = _number(v, key, 0.1, 30)
        return None if f is None else round(f, 3)
    s = _text(v, SHORT, key)
    if key == 'born_on':
        if s and not _BORN.match(s):
            raise EditError('born_on must be YYYY, YYYY-MM or YYYY-MM-DD')
        return s or None
    enum = _enum(prop)
    if enum is not None and s not in enum:
        raise EditError(f'{key} must be one of {[e for e in enum if e != ""]}')
    return s


def validate(maker, body, photo_seqs=()):
    """Return column → cleaned value for db.update_knife. Unknown keys are an
    error. `ext` holds only the keys sent (the route merges over the stored ext).
    Editing born_on sets precision from its shape and source='owner' unless given."""
    if not isinstance(body, dict):
        raise EditError('body must be an object')
    mod = makers.get(maker)  # KeyError for an unknown maker — the route never lets one in
    out = {}
    for key, v in body.items():
        if key in core.CORE_PROPS:
            out[key] = _core(key, core.CORE_PROPS[key], v)
        elif key == 'ext':
            if not isinstance(v, dict):
                raise EditError('ext must be an object')
            ext = {}
            for ek, ev in v.items():
                if ek not in mod.EXT_PROPS:
                    raise EditError(f'unknown field ext.{ek}')
                s = _text(ev, SHORT, f'ext.{ek}')
                enum = _enum(mod.EXT_PROPS[ek])
                if enum is not None and s not in enum:
                    raise EditError(f'ext.{ek} must be one of {[e for e in enum if e != ""]}')
                ext[ek] = s
            out['ext'] = ext
        elif key == 'price_paid':
            out[key] = _number(v, key, 0, 10_000_000)
        elif key == 'acquired_date':
            s = _text(v, 10, key)
            if s and not _DATE.match(s):
                raise EditError('acquired_date must be YYYY-MM-DD')
            out[key] = s or None
        elif key in PRIVATE_TEXT or key in PUBLIC_TEXT:
            out[key] = _text(v, LONG, key) or None
        elif key == 'hero_photo':
            if v is None or v == '':
                out[key] = None
            elif not isinstance(v, bool) and v in tuple(photo_seqs):
                out[key] = int(v)
            else:
                raise EditError("hero_photo must be the seq of one of this knife's photos")
        else:
            raise EditError(f'unknown field {key}')
    if 'born_on' in out:
        b = out['born_on']
        out.setdefault('born_on_precision', _PRECISION[len(b)] if b else '')
        out.setdefault('born_on_source', 'owner' if b else '')
    return out


def flags_for(knife):
    """Maker consistency flags for a stored record (flag, never block)."""
    mod = makers.get(knife.get('maker') or 'crk')
    core_ = {f: knife.get(f) for f in core.CORE_FIELDS}
    return mod.flags(core_, knife.get('ext') or {})
