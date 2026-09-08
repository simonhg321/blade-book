# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Any maker without a module of its own (plan 14). No extension fields, no
consistency rules: the core record + the owner's corrections ARE the record.
A maker that earns deep reading gets its own module; until then it files here."""
import re

EXT_PROPS = {}

PROMPT = (
    'Any other maker: production, mid-tech or custom. Read the maker name from whatever '
    'is in frame (card, certificate, box, tang stamp, the owner\'s note). Use general '
    'knowledge of that maker only for standard specs when the model is unambiguous; '
    'otherwise leave the field empty and rate it low.'
)


def _s(v):
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip().lower()


def norm(field, value):
    """Comparison form: case/space-insensitive. No aliases — nothing to collapse."""
    return _s(value)


def flags(core_, ext, today=None):
    """No maker rules → never a flag."""
    return []
