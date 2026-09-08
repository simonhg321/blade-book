# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""Maker-agnostic half of the decoder: the core knife fields every maker shares,
the JSON schema the model is forced into, and the base prompt. A maker module
supplies `EXT_PROPS` (its own fields) and `PROMPT` (what its cards/boxes say)."""
import datetime as dt
import re

CONF = ['high', 'medium', 'low']

CORE_PROPS = {
    'maker_name': {'type': 'string', 'description': 'Maker / brand as printed on the card, box or blade, e.g. "Chris Reeve Knives", "Hinderer Knives", "Strider"; empty if nothing in frame names the maker'},
    'model': {'type': 'string', 'description': 'Model line only, e.g. "Sebenza", "Inkosi", "Mnandi", "XM-18"; empty if unknown'},
    'variant': {'type': 'string', 'description': 'Edition/qualifier not covered elsewhere, e.g. "25th Anniversary"; empty if none'},
    'blade_steel': {'type': 'string', 'description': 'As printed, e.g. "CPM MagnaCut", "S35VN", "Damascus"; empty if unknown'},
    'blade_shape': {'type': 'string', 'description': '"Drop Point", "Insingo", "Tanto", "Wharncliffe"…; empty if unknown'},
    'blade_length_in': {'anyOf': [{'type': 'number'}, {'type': 'null'}], 'description': 'Inches, only when the model+size makes it certain; else null'},
    'handle_material': {'type': 'string', 'description': 'Frame/scale material incl. inlay wood or micarta, e.g. "titanium, box elder burl inlay"'},
    'lock_type': {'type': 'string', 'description': '"framelock", "slipjoint", "fixed"…'},
    'born_on': {'type': 'string', 'description': 'Birth/manufacture date from the card as YYYY-MM-DD, YYYY-MM or YYYY; empty if no date visible'},
    'born_on_precision': {'type': 'string', 'enum': ['day', 'month', 'year', '']},
    'born_on_source': {'type': 'string', 'enum': ['card', 'box', 'owner', 'inferred', '']},
    'condition': {'anyOf': [{'type': 'integer', 'enum': [1, 2, 3, 4]}, {'type': 'null'}], 'description': '1 new/unused, 2 excellent, 3 very good, 4 used; null if the knife is not visible'},
    'has_box': {'type': 'boolean'},
    'has_card': {'type': 'boolean', 'description': 'A birth card / certificate is IN FRAME'},
    'has_papers': {'type': 'boolean', 'description': 'Any other paperwork: warranty, care sheet, receipt'},
    'has_pouch': {'type': 'boolean'},
    'has_lanyard': {'type': 'boolean'},
    'has_spare_hardware': {'type': 'boolean', 'description': 'Spare screws/washers/tools bag in frame'},
}
CORE_FIELDS = tuple(CORE_PROPS)

NO_CARD_LINE = ('The owner states there is NO birth card for this knife — no birth card is in frame. '
                'Do not invent one; leave born_on empty with born_on_source "" unless a date is printed on the box.')

BASE_PROMPT = (
    'These photos are ALL of the SAME knife, shot by its owner for their private register. '
    'Photo 1 should show the box, the kit and the birth card together; photos 2 and 3 are '
    'the knife open and closed. Fill EVERY field of the schema.\n\n'
    'Rules:\n'
    '- maker_name: the maker is NOT known in advance. Read the brand from the card, certificate, '
    'box label or blade tang stamp, as printed. Maker-specific rules below apply ONLY when the '
    'maker matches; otherwise leave that maker\'s ext fields empty.\n'
    '- If a birth card, certificate or box label is visible, READ IT and use it verbatim for '
    'steel, hardness, date, inlay, hand. Transcribe the whole card into card_text, one printed '
    'line per line. Set no_card=true only when no card is in frame.\n'
    '- Dates: card date wins over box date wins over the owner\'s note. Give born_on_precision '
    'honestly (a card usually gives a day; a box label usually only a year).\n'
    '- Use knowledge of the maker only for standard specs when the model is unambiguous; '
    'otherwise leave the field empty/null and mark it low confidence.\n'
    '- Never invent accessories that are not in frame. has_* fields describe what is VISIBLE.\n'
    '- confidence: "high" = read from card/box/stamp, "medium" = inferred from what is visible, '
    '"low" = guess. Every field gets a rating.\n'
    '- reasoning: two sentences — what identified it, what is a guess.\n'
    '{no_card}\n'
    '{maker_prompt}\n'
    'Owner\'s note (may be partial, may contain the answer): {note!r}\n'
)


def build_schema(ext_props):
    fields = list(CORE_FIELDS) + list(ext_props)
    return {
        'type': 'object',
        'properties': {
            **CORE_PROPS,
            'ext': {'type': 'object', 'properties': dict(ext_props),
                    'required': list(ext_props), 'additionalProperties': False},
            'card_text': {'type': 'string', 'description': 'Verbatim transcription of the birth card / certificate; empty if none'},
            'no_card': {'type': 'boolean'},
            'confidence': {'type': 'object',
                           'properties': {f: {'type': 'string', 'enum': list(CONF)} for f in fields},
                           'required': fields, 'additionalProperties': False},
            'reasoning': {'type': 'string'},
        },
        'required': list(CORE_FIELDS) + ['ext', 'card_text', 'no_card', 'confidence', 'reasoning'],
        'additionalProperties': False,
    }


_DATE = re.compile(r'^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$')


def born_date(born_on):
    """YYYY[-MM[-DD]] → date (missing month/day → 1); None if absent or unparsable."""
    m = _DATE.match(born_on or '')
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2) or 1), int(m.group(3) or 1)
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def age_months(born_on, today=None):
    """Whole months between born_on (YYYY[-MM[-DD]]) and today; None if unparsable."""
    born = born_date(born_on)
    if born is None:
        return None
    today = today or dt.date.today()
    months = (today.year - born.year) * 12 + (today.month - born.month)
    if today.day < born.day:
        months -= 1
    return max(months, 0)
