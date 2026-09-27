# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/search.py — the /search index (spec §9).

search_cards + search_fts are DERIVED data, rebuilt per owner from the SAME
public_row() dicts the published bundle is built from — publish.PUBLIC_FIELDS
stays the single security boundary, and the index can never disagree with the
public page. Key-gated and profile-private owners are excluded entirely
(a gated register is link+key only). Repair tool: scripts/publish_sweep.py
--all (every build_user() reindexes its owner).
"""
import json
import re

from bb import publish

# What a card carries to the browser. Built ONLY from public_row() output
# (already whitelisted + hide_born_day-truncated) plus handle/name/img_t.
CARD_FIELDS = ('tag', 'maker_name', 'model', 'variant', 'generation', 'size', 'born',
               'damascus_smith', 'damascus_pattern', 'special_edition',
               'for_sale', 'asking_price', 'img_t')

FTS_FIELDS = ('tag', 'maker_name', 'model', 'variant', 'blade_steel', 'blade_shape',
              'generation', 'size', 'handle_treatment', 'graphic_name',
              'inlay_material', 'damascus_smith', 'damascus_pattern',
              'special_edition', 'notes_public', 'born')

MAX_Q = 100


def _card(row, handle):
    c = {f: row.get(f) for f in CARD_FIELDS if row.get(f) not in (None, '')}
    c['tag'] = row['tag']
    c['handle'] = handle
    c['name'] = publish.full_name(row)
    return c


def deindex_user(con, owner_id):
    ids = [r[0] for r in con.execute(
        'SELECT knife_id FROM search_cards WHERE owner_id = ?', (owner_id,))]
    if ids:
        ph = ','.join('?' * len(ids))
        con.execute(f'DELETE FROM search_fts WHERE rowid IN ({ph})', ids)
        con.execute('DELETE FROM search_cards WHERE owner_id = ?', (owner_id,))
    con.commit()


def reindex_user(con, user, rows):
    """rows = the public_row() dicts build_user computed (img_t included).
    NOTE: rows carry no knife id, so knife_id here is a per-owner synthetic
    id derived from the owner id and the row's position — stable enough for
    replace-all-rows semantics, and never exposed."""
    deindex_user(con, user['id'])
    if user.get('profile_private') or (user.get('public_key') or '').strip():
        return 0
    handle = user['handle']
    for i, row in enumerate(rows):
        kid = user['id'] * 1_000_000 + i + 1
        m = re.match(r'^(\d{4})', row.get('born_on') or '')
        y = int(m.group(1)) if m else None
        con.execute(
            'INSERT INTO search_cards (knife_id, owner_id, handle, maker_name, model, generation,'
            ' size, born_year, damascus_smith, damascus_pattern, special_edition,'
            ' for_sale, card) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (kid, user['id'], handle, row.get('maker_name') or '', row.get('model') or '', row.get('generation') or '',
             row.get('size') or '', y, row.get('damascus_smith') or '',
             row.get('damascus_pattern') or '', row.get('special_edition') or '',
             row.get('for_sale') or 0, json.dumps(_card(row, handle))))
        text = ' '.join(str(row.get(f) or '') for f in FTS_FIELDS)
        text = f"{text} {handle} {publish.full_name(row)}"
        con.execute('INSERT INTO search_fts (rowid, text) VALUES (?, ?)', (kid, text))
    con.commit()
    return len(rows)


def _fts_query(q):
    """User text → safe FTS5 MATCH string: alnum-only terms, each quoted,
    last term prefix-starred. Returns None when nothing searchable remains."""
    terms = re.findall(r'[A-Za-z0-9]+', q or '')[:8]
    if not terms:
        return None
    quoted = [f'"{t}"' for t in terms]
    quoted[-1] = quoted[-1] + '*'          # "term"* — FTS5 prefix match (verified syntax)
    return ' '.join(quoted)


def run_query(con, q, filters=None, who_min=None, limit=50):
    filters = filters or {}
    where, args = [], []
    match = _fts_query((q or '')[:MAX_Q])
    if match:
        ids = [r[0] for r in con.execute(
            'SELECT rowid FROM search_fts WHERE search_fts MATCH ?', (match,))]
        if not ids:
            return {'count': 0, 'knives': [],
                    'aggregates': {'makers': [], 'models': [], 'years': [], 'sizes': []},
                    **({'owners': []} if who_min else {})}
        ph = ','.join('?' * len(ids))
        where.append(f'knife_id IN ({ph})')
        args += ids
    if filters.get('year_from') is not None:
        where.append('born_year >= ?'); args.append(int(filters['year_from']))
    if filters.get('year_to') is not None:
        where.append('born_year <= ?'); args.append(int(filters['year_to']))
    for key, col in (('smith', 'damascus_smith'), ('pattern', 'damascus_pattern'),
                     ('edition', 'special_edition')):
        if filters.get(key):
            where.append(f'{col} LIKE ?'); args.append(f"%{filters[key]}%")
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    count = con.execute(f'SELECT count(*) FROM search_cards {w}', args).fetchone()[0]
    knives = [json.loads(r[0]) for r in con.execute(
        f'SELECT card FROM search_cards {w} ORDER BY born_year DESC NULLS LAST, knife_id DESC LIMIT ?',
        args + [limit])]
    # Ordered [name, count] pairs, NOT dicts — Flask 3.1's default JSON
    # provider sorts dict keys (sort_keys=True), which silently alphabetized
    # these and threw away the ORDER BY count(*) DESC below. Lists preserve
    # the SQL ordering all the way to the browser.
    aggs = {}
    for name, col in (('makers', 'maker_name'), ('models', 'model'), ('years', 'born_year'), ('sizes', 'size')):
        aggs[name] = [[str(r[0]), r[1]] for r in con.execute(
            f"SELECT {col}, count(*) FROM search_cards {w} "
            f"GROUP BY {col} ORDER BY count(*) DESC", args) if r[0] not in (None, '')]
    out = {'count': count, 'knives': knives, 'aggregates': aggs}
    if who_min:
        out['owners'] = [{'handle': r[0], 'n': r[1]} for r in con.execute(
            f'SELECT handle, count(*) AS n FROM search_cards {w} '
            f'GROUP BY handle HAVING n >= ? ORDER BY n DESC', args + [int(who_min)])]
    return out
