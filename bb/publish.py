# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/publish.py — the public projection (spec §8): a static bundle per collector
at WWW_DIR/@{handle}/, regenerated on save (debounced 30 s). Built ONLY from
PUBLIC_FIELDS below — the whitelist is the security boundary, and
tests/test_publish_leak.py proves nothing in db.PRIVATE_COLUMNS survives it.
The DB never sees public traffic; Apache serves the files.
"""
import re

SAFE_HANDLE = re.compile(r'[a-z0-9-]{3,24}')

# spec §8 whitelist, verbatim — core columns and (for crk) ext keys.
PUBLIC_FIELDS = ('tag', 'maker', 'model', 'variant', 'blade_steel', 'blade_shape',
                 'born_on', 'born_on_precision', 'notes_public')
PUBLIC_EXT = ('generation', 'size', 'handle_treatment', 'graphic_name',
              'inlay_material', 'damascus_smith', 'damascus_pattern',
              'special_edition')

_MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
           'August', 'September', 'October', 'November', 'December']


def _fmt_born(d, precision):
    if not d:
        return ''
    parts = d.split('-')
    y = parts[0]
    if precision == 'year' or len(parts) == 1:
        return f'circa {y}'
    m = _MONTHS[int(parts[1]) - 1]
    if precision == 'month' or len(parts) == 2:
        return f'{m} {y}'
    return f'{m} {int(parts[2])}, {y}'


def public_row(k, user):
    """One knife → its public dict. Everything not named here stays private."""
    row = {f: k.get(f) or '' for f in PUBLIC_FIELDS}
    ext = k.get('ext') or {}
    for f in PUBLIC_EXT:
        row[f] = ext.get(f) or ''
    precision = k.get('born_on_precision') or 'day'
    if user.get('hide_born_day') and precision == 'day' and row['born_on']:
        row['born_on'] = row['born_on'][:7]
        precision = 'month'
    row['born_on_precision'] = precision
    row['born'] = _fmt_born(row['born_on'], precision)
    row['for_sale'] = 1 if k.get('sale_status') == 'for_sale' else 0
    row['for_trade'] = 1 if k.get('sale_status') == 'for_trade' else 0
    if row['for_sale']:
        if k.get('asking_price'):
            row['asking_price'] = k['asking_price']
        if k.get('seller_note'):
            row['seller_note'] = k['seller_note']
    return row


def display_name(row):
    """'Large Sebenza 31', 'Mnandi', 'Small Inkosi' — from public fields only."""
    if row.get('model') == 'Sebenza':
        return ' '.join(x for x in (row.get('size'), 'Sebenza', row.get('generation')) if x)
    name = row.get('model') or row['tag']
    if row.get('size') and row.get('size') not in name:
        return f"{row['size']} {name}"
    return name
