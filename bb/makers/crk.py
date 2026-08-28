# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Chris Reeve Knives: extension fields, what their cards and boxes say, SKU
prefixes, era rules. Rules FLAG, they never block — a flagged record is still
written and the flag is shown to the owner."""
import datetime as dt
import re

from bb.makers import core

EXT_PROPS = {
    'generation': {'type': 'string', 'enum': ['', 'Classic', '21', '25', '31'], 'description': 'Sebenza generation from card/box/SKU; "" for non-Sebenza'},
    'size': {'type': 'string', 'enum': ['', 'Small', 'Large']},
    'crk_sku': {'type': 'string', 'description': 'Box label SKU exactly as printed, e.g. "L31-1633", "LIN-1000"; empty if not visible'},
    'hand': {'type': 'string', 'enum': ['', 'right', 'left']},
    'hand_on_box': {'type': 'string', 'enum': ['', 'right', 'left'], 'description': 'Hand as printed on the BOX label, if it differs in origin from the card'},
    'hardness_note': {'type': 'string', 'description': 'e.g. "63-64 RC" as printed'},
    'handle_treatment': {'type': 'string', 'enum': ['', 'plain-ti', 'inlay', 'unique graphic', 'CGG', 'front face', 'CAD Custom', 'other']},
    'inlay_material': {'type': 'string', 'description': 'e.g. "box elder burl", "canvas micarta"; empty if none'},
    'damascus_smith': {'type': 'string', 'description': 'e.g. "Chad Nichols", "Devin Thomas"; empty unless the steel is damascus'},
    'damascus_pattern': {'type': 'string', 'description': 'e.g. "Stainless Ladder", "Raindrop", "Basketweave"'},
    'graphic_name': {'type': 'string', 'description': 'Unique Graphic / CGG name as printed'},
    'special_edition': {'type': 'string'},
    'surface_finish': {'type': 'string', 'description': '"polished", "stonewashed", "blasted"…'},
    'hardware_note': {'type': 'string', 'description': 'Thumb stud / lugs / pivot notes, e.g. "gold thumb stud, double lugs"'},
    'box_type': {'type': 'string', 'description': '"modern 6x6x2 SKU label", "old award-sticker box, no SKU"…'},
}

PROMPT = (
    'Maker: Chris Reeve Knives (Boise, Idaho). Models: Sebenza (generations Classic, 21, 25, 31; '
    'sizes Small, Large), Inkosi (Small/Large; blade Drop Point or Insingo), Mnandi, Impinda, '
    'Umnumzaan, TiLock (Ti-Lock), Zaan, and fixed blades (Green Beret, Pacific, Nyala, '
    'Professional Soldier, Sikayo). The BIRTH CARD lists the model, blade steel with hardness '
    '(e.g. "CPM MagnaCut 63-64 RC"), the "Born on" date (day precision), the inlay or graphic, '
    'and the hand. The BOX LABEL (modern 6x6x2 box) carries a SKU whose prefix encodes model '
    'and size: S21/L21 = Small/Large Sebenza 21, S25/L25 = Sebenza 25, S31/L31 = Sebenza 31, '
    'SIN/LIN = Small/Large Inkosi, MNA = Mnandi, IMP = Impinda, UMN = Umnumzaan, TIL = TiLock. '
    'Older boxes have an award sticker and no SKU. Damascus blades name the smith on the card '
    '(Chad Nichols, Devin Thomas). "CGG" = Computer Generated Graphic; "unique graphic" is a '
    'one-off anodised pattern named on the card. Left-handed knives say so on the card.'
)

SKU_PREFIXES = {
    'S21': ('Sebenza', 'Small', '21'), 'L21': ('Sebenza', 'Large', '21'),
    'S25': ('Sebenza', 'Small', '25'), 'L25': ('Sebenza', 'Large', '25'),
    'S31': ('Sebenza', 'Small', '31'), 'L31': ('Sebenza', 'Large', '31'),
    'SIN': ('Inkosi', 'Small', ''), 'LIN': ('Inkosi', 'Large', ''),
    'MNA': ('Mnandi', '', ''), 'IMP': ('Impinda', '', ''),
    'UMN': ('Umnumzaan', '', ''), 'TIL': ('TiLock', '', ''),
}

# steel ↔ era (spec §7): outside the window → flag
_ERA = {  # steel → (first year, last year) inclusive; None = open
    's30v': (None, 2016), 'magnacut': (2022, None), 's45vn': (2021, 2023),
}

_STEEL_ALIASES = [
    (re.compile(r'magna\s*cut', re.I), 'magnacut'),
    (re.compile(r'damascus', re.I), 'damascus'),
    (re.compile(r'\bs35\s*vn?\b', re.I), 's35vn'),
    (re.compile(r'\bs45\s*vn?\b', re.I), 's45vn'),
    (re.compile(r'\bs30\s*v\b', re.I), 's30v'),
    (re.compile(r'bg[-\s]?42', re.I), 'bg-42'),
    (re.compile(r'\b(cpm[-\s]?)?4v\b', re.I), 'cpm 4v'),
]
_MODELS = ('sebenza', 'inkosi', 'mnandi', 'impinda', 'umnumzaan', 'tilock', 'ti-lock',
           'zaan', 'green beret', 'pacific', 'nyala', 'professional soldier', 'sikayo')


def _s(v):
    return re.sub(r'\s+', ' ', str(v if v is not None else '')).strip().lower()


def norm(field, value):
    """Comparison form of a field (eval + matching): case/space-insensitive,
    aliases collapsed. Returns '' for None."""
    if field == 'hand':
        # CRK cards only say "left"; an unstated hand IS right-handed
        s = _s(value)
        return 'left' if 'left' in s else 'right'
    if value is None or value == '':
        return ''
    if field.startswith('has_'):
        return '1' if value in (True, 1, '1', 'true', 'True') else '0'
    s = _s(value)
    if field == 'blade_steel':
        for rx, out in _STEEL_ALIASES:
            if rx.search(s):
                return out
        return s.replace('cpm ', '')
    if field == 'model':
        for m in _MODELS:
            if m in s:
                return 'tilock' if m == 'ti-lock' else m
        return s
    if field == 'generation':
        # crkinv calls the pre-21 Sebenza "Regular"; the schema enum says "Classic"
        m = re.search(r'\b(classic|regular|21|25|31)\b', s)
        return 'classic' if m and m.group(1) == 'regular' else (m.group(1) if m else s)
    if field == 'crk_sku':
        return str(value).strip().upper()
    if field == 'born_on':
        # month-only cards ("DECEMBER, 2011") are stored day-padded as YYYY-MM-01 in
        # crkinv; the decoder returns YYYY-MM — compare at month precision for those
        s = str(value).strip()
        if re.fullmatch(r'\d{4}-01-01', s):
            return s[:4]  # year-only card stored day-padded
        return s[:-3] if re.fullmatch(r'\d{4}-\d{2}-01', s) else s
    if field == 'inlay_material':
        return re.sub(r'\s*\(.*?\)', '', s).strip()  # "elforyn (super tusk)" → "elforyn"
    return s


def flags(core_, ext, today=None):
    """Consistency rules. Input = the decoded core + ext dicts. Output = list of
    human sentences; empty means consistent."""
    today = today or dt.date.today()
    out = []
    sku = (ext.get('crk_sku') or '').strip().upper()
    pre = sku.split('-', 1)[0] if sku else ''
    if pre in SKU_PREFIXES:
        model, size, gen = SKU_PREFIXES[pre]
        if norm('model', core_.get('model')) and norm('model', core_.get('model')) != norm('model', model):
            out.append(f'SKU {pre}- is a {model} but the model reads {core_.get("model")}')
        if size and ext.get('size') and _s(ext['size']) != _s(size):
            out.append(f'SKU {pre}- is a {size} but the size reads {ext["size"]}')
        if gen and ext.get('generation') and _s(ext['generation']) != _s(gen):
            out.append(f'SKU {pre}- is a Sebenza {gen} but the generation reads {ext["generation"]}')
    born = core_.get('born_on') or ''
    year = int(born[:4]) if re.match(r'^\d{4}', born) else None
    steel = norm('blade_steel', core_.get('blade_steel'))
    if year and steel in _ERA:
        lo, hi = _ERA[steel]
        if (lo and year < lo) or (hi and year > hi):
            label = {'s30v': 'S30V', 'magnacut': 'MagnaCut', 's45vn': 'S45VN'}[steel]
            out.append(f'{label} on a {year} card — outside its production window')
    if year and year > today.year:
        out.append(f'card date {born} is in the future')
    h, hb = _s(ext.get('hand')), _s(ext.get('hand_on_box'))
    if h and hb and h != hb:
        out.append(f'hand on card ({h}) differs from hand on box ({hb})')
    return out
