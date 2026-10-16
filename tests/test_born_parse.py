# Copyright (c) 2026 Simon SGH — blade-book.com — Licensed under the Elastic License 2.0 (see LICENSE)
"""bb/born.py — date parsing, the ±7 day window, labels (date board spec §1)."""
import datetime as dt

import pytest

from bb import born


@pytest.mark.parametrize('text, expected', [
    ('7/28', (None, 7, 28)),
    ('7-28', (None, 7, 28)),
    ('07/28', (None, 7, 28)),
    (' 7 / 28 ', (None, 7, 28)),
    ('7/28/99', (1999, 7, 28)),
    ('7/28/1999', (1999, 7, 28)),
    ('07-28-1999', (1999, 7, 28)),
    ('11/6/21', (2021, 11, 6)),
    ('1999-07-28', (1999, 7, 28)),
    ('1999/7/28', (1999, 7, 28)),
    ('July 28', (None, 7, 28)),
    ('jul 28', (None, 7, 28)),
    ('July 28, 1999', (1999, 7, 28)),
    ('July 28 1999', (1999, 7, 28)),
    ('28 July 1999', (1999, 7, 28)),
    ('28 jul', (None, 7, 28)),
    ('2/29', (None, 2, 29)),
    ('2/29/2000', (2000, 2, 29)),
])
def test_parse_accepts(text, expected):
    assert born.parse(text) == expected


def test_two_digit_year_pivots_on_the_current_year():
    this_year = dt.date.today().year
    yy = this_year % 100
    assert born.parse(f'7/28/{yy:02d}')[0] == this_year               # this year: 2000s
    assert born.parse(f'7/28/{(yy + 1) % 100:02d}')[0] == 1900 + (yy + 1) % 100   # next year: 1900s


@pytest.mark.parametrize('text', [
    '', '   ', '7', 'abc', '13/40', '0/10', '7/0', '7/32', '2/30', '4/31',
    '7/28/1800', '7/28/2101', '1800-07-28', 'Smarch 1', 'July', '7/28/999',
    'x' * 33, '7/28/1999/1', '7//28',
])
def test_parse_rejects(text):
    with pytest.raises(born.BornError) as e:
        born.parse(text)
    assert str(e.value) == 'I need at least a month and a day'


def test_parse_is_a_value_error():
    assert issubclass(born.BornError, ValueError)


def test_window_is_fifteen_days_centred_and_wraps_the_year():
    w = born.window(1, 2)
    assert len(w) == 15
    assert w[0] == (-7, '12-26') and w[7] == (0, '01-02') and w[-1] == (7, '01-09')
    assert (-3, '12-30') in w


def test_window_includes_leap_day():
    assert (1, '02-29') in born.window(2, 28)
    assert (1, '03-01') in born.window(2, 29)
    assert (-1, '02-28') in born.window(2, 29)


def test_window_offsets_are_unique_month_days():
    mds = [md for _, md in born.window(7, 28)]
    assert len(set(mds)) == 15


def test_labels():
    assert born.label(1999, 7, 28) == 'July 28, 1999'
    assert born.label(None, 7, 28) == 'July 28'
    assert born.url_form(1999, 7, 28) == '1999-07-28'
    assert born.url_form(None, 7, 28) == '07-28'
    assert born.short(7, 28) == '7/28'
    assert born.short(11, 6) == '11/6'


def _row(i, born):
    return {'id': i, 'born_on': born}


def test_rank_three_groups_one_row_each_closest_first():
    rows = [_row(1, '1999-07-28'), _row(2, '2022-07-28'), _row(3, '2010-07-28'),
            _row(4, '2020-07-31'), _row(5, '2021-07-25'), _row(6, '2019-07-30'),
            _row(7, '2022-08-05'), _row(8, '2018-07-20')]
    r = born.rank(1999, 7, 28, rows)
    assert [x['id'] for _, x in r['exact']] == [1]
    assert [x['id'] for _, x in r['same_day']] == [2, 3]                 # most recent year first
    assert [(off, x['id']) for off, x in r['near']] == [(2, 6), (-3, 5), (3, 4)]   # |off| asc, then year desc (2021 before 2020 at distance 3)
    # 8/05 is +8 and 7/20 is -8: outside the week
    ids = [x['id'] for g in r.values() for _, x in g]
    assert 7 not in ids and 8 not in ids and len(ids) == len(set(ids))


def test_rank_without_a_year_has_no_exact_group():
    rows = [_row(1, '1999-07-28'), _row(2, '2022-07-28')]
    r = born.rank(None, 7, 28, rows)
    assert r['exact'] == [] and [x['id'] for _, x in r['same_day']] == [2, 1]


def test_rank_wraps_the_year_end_and_handles_leap_day():
    r = born.rank(None, 1, 2, [_row(1, '2021-12-30'), _row(2, '2020-01-05')])
    assert [(off, x['id']) for off, x in r['near']] == [(-3, 1), (3, 2)]     # same distance: newest year first
    r = born.rank(None, 2, 29, [_row(1, '2020-02-29'), _row(2, '2021-03-01'), _row(3, '2019-02-28')])
    assert [x['id'] for _, x in r['same_day']] == [1]
    assert [(off, x['id']) for off, x in r['near']] == [(1, 2), (-1, 3)]


def test_rank_caps_each_group():
    rows = [_row(i, f'{1950 + i}-07-28') for i in range(40)]
    r = born.rank(None, 7, 28, rows)
    assert len(r['same_day']) == born.GROUP_CAP == 24
    assert r['same_day'][0][1]['id'] == 39                                 # newest years survive the cap


def test_rank_ignores_rows_without_a_full_date():
    r = born.rank(None, 7, 28, [_row(1, '2022-07'), _row(2, ''), _row(3, None), _row(4, '2022-07-28')])
    assert [x['id'] for _, x in r['same_day']] == [4]
