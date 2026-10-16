# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/born.py — the date board's date logic (spec §1, §2). Pure: no DB, no Flask.

parse()  : free text → (year | None, month, day). Year optional. Two-digit years
           go to the 2000s unless that lands after this year (99 → 1999, 21 → 2021).
window() : the ±7 day neighbourhood of a month-day, computed in the leap year
           2000 so 02-29 is a real day and the year end wraps (12-30 is 3 days
           before 01-02).
rank()   : see Task 3 — added there.
"""
import datetime as dt
import re

MAX_LEN = 32
WINDOW_DAYS = 7
YEAR_MIN, YEAR_MAX = 1900, 2100
MESSAGE = 'I need at least a month and a day'

_MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july',
           'august', 'september', 'october', 'november', 'december']
MONTH_NAMES = [m.capitalize() for m in _MONTHS]

_NUMERIC_MDY = re.compile(r'^(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2}|\d{4}))?$')
_NUMERIC_YMD = re.compile(r'^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$')
_NAME_FIRST = re.compile(r'^([a-z]+)\.?\s+(\d{1,2})(?:,?\s+(\d{4}))?$')
_DAY_FIRST = re.compile(r'^(\d{1,2})\s+([a-z]+)\.?(?:,?\s+(\d{4}))?$')


class BornError(ValueError):
    pass


def _month_from_name(name):
    if len(name) < 3:
        return None
    for i, m in enumerate(_MONTHS):
        if m.startswith(name):
            return i + 1
    return None


def _year(raw):
    if raw is None:
        return None
    n = int(raw)
    if len(raw) == 2:
        this_year = dt.date.today().year
        n = 2000 + n if 2000 + n <= this_year else 1900 + n
    if not YEAR_MIN <= n <= YEAR_MAX:
        raise BornError(MESSAGE)
    return n


def _check(month, day):
    try:
        dt.date(2000, month, day)          # leap year: 2/29 is legal with no year
    except ValueError:
        raise BornError(MESSAGE) from None


def parse(text):
    """(year | None, month, day) or BornError. See module doc for the formats."""
    s = re.sub(r'\s+', ' ', (text or '').strip().lower())
    s = re.sub(r'\s*([/-])\s*', r'\1', s)       # '7 / 28' → '7/28'
    if not s or len(s) > MAX_LEN:
        raise BornError(MESSAGE)
    m = _NUMERIC_MDY.match(s)
    if m:
        month, day, year = int(m.group(1)), int(m.group(2)), _year(m.group(3))
    else:
        m = _NUMERIC_YMD.match(s)
        if m:
            year, month, day = _year(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = _NAME_FIRST.match(s)
            if m:
                month, day, year = _month_from_name(m.group(1)), int(m.group(2)), _year(m.group(3))
            else:
                m = _DAY_FIRST.match(s)
                if not m:
                    raise BornError(MESSAGE)
                day, month, year = int(m.group(1)), _month_from_name(m.group(2)), _year(m.group(3))
    if month is None:
        raise BornError(MESSAGE)
    _check(month, day)
    if year is not None and month == 2 and day == 29:
        try:
            dt.date(year, 2, 29)
        except ValueError:
            raise BornError(MESSAGE) from None
    return year, month, day


def window(month, day, days=WINDOW_DAYS):
    """[(days_off, 'MM-DD'), ...] for days_off in -days..days, leap-year calendar."""
    centre = dt.date(2000, month, day)
    out = []
    for off in range(-days, days + 1):
        d = centre + dt.timedelta(days=off)
        out.append((off, f'{d.month:02d}-{d.day:02d}'))
    return out


def label(year, month, day):
    base = f'{MONTH_NAMES[month - 1]} {day}'
    return f'{base}, {year}' if year else base


def url_form(year, month, day):
    md = f'{month:02d}-{day:02d}'
    return f'{year}-{md}' if year else md


def short(month, day):
    return f'{month}/{day}'
