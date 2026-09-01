# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import hashlib
import json

from bb import db, publish, search


def _mk_user(con, email='idx@example.com', handle='idx-guy', **over):
    uid = db.create_user(con, email, handle)
    if over:
        cols = ', '.join(f'{c} = ?' for c in over)
        con.execute(f'UPDATE users SET {cols} WHERE id = ?', (*over.values(), uid))
        con.commit()
    return db.get_user(con, uid)


def _mk_knife(con, uid, model='Sebenza', born='2008-03-14', ext=None, **cols):
    k = db.create_draft_knife(con, uid)
    ext = {'generation': '21', 'size': 'Large', **(ext or {})}
    con.execute("UPDATE knives SET confidence = '{}', model = ?, born_on = ?, "
                "born_on_precision = 'day', ext = ? WHERE id = ?",
                (model, born, json.dumps(ext), k['id']))
    for c, v in cols.items():
        con.execute(f'UPDATE knives SET {c} = ? WHERE id = ?', (v, k['id']))
    con.commit()
    # Add a fake photo so publish succeeds
    db.add_photo(con, uid, k['id'], 1, f'{uid}/{k["id"]}/1.jpg',
                 hashlib.sha256(b'x').hexdigest(), 800, 600)
    db.publish_knife(con, uid, k['id'])
    return db.get_knife(con, uid, k['id'])   # (con, owner_id, knife_id) — verified


def _rows(con, user):
    out = []
    for k in db.public_knives(con, user['id']):
        r = publish.public_row(k, user)
        r['img_t'] = f"{k['tag']}_t.jpg"
        out.append(r)
    return out


def test_reindex_inserts_public_knives(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    n = search.reindex_user(con, u, _rows(con, u))
    assert n == 1
    row = con.execute('SELECT * FROM search_cards').fetchone()
    assert row['handle'] == 'idx-guy' and row['model'] == 'Sebenza'
    assert row['born_year'] == 2008
    card = json.loads(row['card'])
    assert card['name'] == 'Large Sebenza 21' and card['tag'] == 'K01'
    assert card['img_t'] == 'K01_t.jpg'
    hit = con.execute("SELECT rowid FROM search_fts WHERE search_fts MATCH '\"sebenza\"'").fetchall()
    assert len(hit) == 1


def test_reindex_is_idempotent_and_drops_stale(con):
    u = _mk_user(con)
    k = _mk_knife(con, u['id'])
    search.reindex_user(con, u, _rows(con, u))
    search.reindex_user(con, u, _rows(con, u))
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 1
    db.set_public(con, u['id'], [k['id']], False)
    search.reindex_user(con, u, _rows(con, u))
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM search_fts').fetchone()[0] == 0


def test_gated_and_private_owners_are_excluded(con):
    for over in ({'public_key': 'shh'}, {'profile_private': 1}):
        u = _mk_user(con, email=f"{list(over)[0]}@example.com", handle=f"h-{list(over)[0][:6]}", **over)
        _mk_knife(con, u['id'])
        assert search.reindex_user(con, u, _rows(con, u)) == 0
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 0


def test_deindex_user(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    search.reindex_user(con, u, _rows(con, u))
    search.deindex_user(con, u['id'])
    assert con.execute('SELECT count(*) FROM search_cards').fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM search_fts').fetchone()[0] == 0


def test_hide_born_day_truncates_in_card(con):
    u = _mk_user(con, hide_born_day=1)
    _mk_knife(con, u['id'])
    search.reindex_user(con, u, _rows(con, u))
    card = json.loads(con.execute('SELECT card FROM search_cards').fetchone()[0])
    assert card['born'] == 'March 2008'
    assert '14' not in card['born']


def test_query_free_text_and_aggregates(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    _mk_knife(con, u['id'], model='Inkosi', born='2019-06-05',
              ext={'generation': '', 'size': 'Small'})
    search.reindex_user(con, u, _rows(con, u))
    r = search.run_query(con, 'sebenza')
    assert r['count'] == 1 and r['knives'][0]['model'] == 'Sebenza'
    assert r['aggregates']['models'] == [['Sebenza', 1]]
    r = search.run_query(con, '')
    assert r['count'] == 2
    # counts tie at 1 for both models — SQL doesn't guarantee a tiebreak
    # order, so compare as a set of pairs rather than an exact list.
    assert {tuple(p) for p in r['aggregates']['models']} == {('Inkosi', 1), ('Sebenza', 1)}
    assert {tuple(p) for p in r['aggregates']['years']} == {('2008', 1), ('2019', 1)}
    assert 'owners' not in r


def test_query_prefix_matches_last_term(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    search.reindex_user(con, u, _rows(con, u))
    assert search.run_query(con, 'seb')['count'] == 1
    assert search.run_query(con, 'large seb')['count'] == 1


def test_query_hostile_input_is_safe(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'])
    search.reindex_user(con, u, _rows(con, u))
    for q in ('sebenza" OR "x', 'NEAR(', 'a AND b)', '"', '*', '- -', 'col:x'):
        r = search.run_query(con, q)          # must not raise sqlite3.OperationalError
        assert isinstance(r['count'], int)


def test_query_paid_filters(con):
    u = _mk_user(con)
    _mk_knife(con, u['id'], ext={'generation': '21', 'size': 'Large',
                                 'damascus_smith': 'Devin Thomas',
                                 'damascus_pattern': 'Raindrop',
                                 'special_edition': 'Annual 2008'})
    _mk_knife(con, u['id'], model='Inkosi', born='2019-06-05')
    search.reindex_user(con, u, _rows(con, u))
    assert search.run_query(con, '', {'year_from': 2005, 'year_to': 2010})['count'] == 1
    assert search.run_query(con, '', {'smith': 'devin thomas'})['count'] == 1
    assert search.run_query(con, '', {'pattern': 'raindrop'})['count'] == 1
    assert search.run_query(con, '', {'edition': 'annual'})['count'] == 1
    assert search.run_query(con, 'sebenza', {'year_from': 2019})['count'] == 0


def test_query_who_min(con):
    a = _mk_user(con)
    b = _mk_user(con, email='b@example.com', handle='b-guy')
    for _ in range(2):
        _mk_knife(con, a['id'])
    _mk_knife(con, b['id'])
    search.reindex_user(con, a, _rows(con, a))
    search.reindex_user(con, b, _rows(con, b))
    r = search.run_query(con, 'sebenza', who_min=2)
    assert r['owners'] == [{'handle': 'idx-guy', 'n': 2}]


def test_born_on_non_numeric_format_does_not_crash(con):
    u = _mk_user(con)
    k = _mk_knife(con, u['id'])
    # Simulate decoder-emitted 'c. 2008' format
    con.execute("UPDATE knives SET born_on = ? WHERE id = ?", ('c. 2008', k['id']))
    con.commit()
    rows = _rows(con, u)
    # Should not raise ValueError
    n = search.reindex_user(con, u, rows)
    assert n == 1
    # Knife still indexed but born_year is None (can't extract leading digits)
    row = con.execute('SELECT born_year, card FROM search_cards').fetchone()
    assert row['born_year'] is None
    card = json.loads(row['card'])
    assert card['tag'] == 'K01'


def test_query_results_ordered_by_born_year_descending(con):
    a = _mk_user(con)
    b = _mk_user(con, email='b@example.com', handle='b-guy')
    # Create knives with different birth years across both owners
    _mk_knife(con, a['id'], born='2008-03-14')  # K01 owner a, born 2008
    _mk_knife(con, b['id'], born='2021-01-01')  # K01 owner b, born 2021
    _mk_knife(con, a['id'], born='2019-06-05')  # K02 owner a, born 2019
    search.reindex_user(con, a, _rows(con, a))
    search.reindex_user(con, b, _rows(con, b))
    r = search.run_query(con, '')
    # Results should be ordered by born_year DESC, so 2021, 2019, 2008
    assert len(r['knives']) == 3
    assert r['knives'][0]['born'] == 'January 1, 2021'     # First (most recent)
    assert r['knives'][1]['born'] == 'June 5, 2019'        # Second
    assert r['knives'][2]['born'] == 'March 14, 2008'      # Third (oldest)
