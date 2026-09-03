# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import io
import json
import os
import zipfile

from bb import db, publish
from bb.routes import settings as settings_routes
from tests.conftest import signed_in
from tests.test_search import _mk_knife

S = '/blade-book/api/settings'


def _reset_export_clock():
    settings_routes._last_export.clear()


def test_settings_view_has_account_fields(client, mailer):
    me = signed_in(client, mailer)
    j = client.get(S).get_json()
    assert j['email'] == 'sam@example.com' and j['handle'] == me['handle']
    assert j['created'] and j['handle_changed_at'] is None
    assert j['can_change_handle'] is True and j['is_admin'] == 0
    assert 'session_secret' not in j and 'public_key' not in j and 'auth_subjects' not in j


def test_patch_handle_once(client, mailer, monkeypatch):
    calls = []
    monkeypatch.setattr('bb.publish.schedule', lambda owner_id: calls.append(owner_id))
    signed_in(client, mailer)
    r = client.patch(S, json={'handle': 'samuel-k'})
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j['handle'] == 'samuel-k' and j['can_change_handle'] is False and j['handle_changed_at']
    assert j['public_url'].endswith('/blade-book/@samuel-k')
    assert len(calls) == 1                                  # republish scheduled once
    r = client.patch(S, json={'handle': 'again'})
    assert r.status_code == 400 and 'already changed' in r.get_json()['error']
    assert client.get('/blade-book/api/auth/me').get_json()['handle'] == 'samuel-k'


def test_patch_handle_validation(client, mailer):
    signed_in(client, mailer)
    assert client.patch(S, json={'handle': 7}).status_code == 400
    assert 'reserved' in client.patch(S, json={'handle': 'admin'}).get_json()['error']
    assert '3–24' in client.patch(S, json={'handle': 'Sam K'}).get_json()['error']


def test_export_zip_and_rate_limit(client, mailer, app):
    _reset_export_clock()
    me = signed_in(client, mailer)
    con = db.connect()
    k = _mk_knife(con, me['id'])
    app.config['STORE'].put(k['photos'][0]['store_key'], b'JPEG')
    con.close()
    r = client.get(S + '/export')
    assert r.status_code == 200, r.data
    assert r.headers['Content-Type'] == 'application/zip'
    assert f"blade-book-{me['handle']}.zip" in r.headers['Content-Disposition']
    with zipfile.ZipFile(io.BytesIO(r.data)) as z:
        assert {'knives.json', 'knives.csv', f"photos/{k['tag']}-1.jpg"} == set(z.namelist())
        assert json.loads(z.read('knives.json'))['count'] == 1
    r.close()                                                              # release the file (Werkzeug 3.1 keeps it open until close)
    from bb import paths
    assert os.listdir(os.path.join(paths.DATA_DIR, 'exports')) == []      # unlinked after send
    r = client.get(S + '/export')
    assert r.status_code == 429 and '10 minutes' in r.get_json()['error']
    r = client.get(S + '/export', headers={'Accept': 'application/json'})
    assert r.status_code == 429 and '10 minutes' in r.get_json()['error']
    _reset_export_clock()
    assert client.get(S + '/export').status_code == 200


def test_export_rate_limit_html_for_browser_navigation(client, mailer, app):
    _reset_export_clock()
    me = signed_in(client, mailer)
    con = db.connect()
    k = _mk_knife(con, me['id'])
    app.config['STORE'].put(k['photos'][0]['store_key'], b'JPEG')
    con.close()
    r = client.get(S + '/export')
    assert r.status_code == 200, r.data
    r.close()
    r = client.get(S + '/export', headers={'Accept': 'text/html'})
    assert r.status_code == 429
    assert r.content_type.startswith('text/html')
    body = r.get_data(as_text=True)
    assert '/blade-book/me/settings/' in body


def test_export_requires_auth(client):
    assert client.get(S + '/export').status_code == 401


def test_delete_account_flow(client, mailer, app):
    me = signed_in(client, mailer)
    con = db.connect()
    k = _mk_knife(con, me['id'])
    app.config['STORE'].put(k['photos'][0]['store_key'], b'JPEG')
    # a fake public surface, the way tests/test_account.py::_public_surface builds one
    dest = publish.bundle_dir(me['handle'])
    os.makedirs(os.path.join(dest, 'K01'), exist_ok=True)
    open(os.path.join(dest, 'index.html'), 'w').write('x')
    os.makedirs(dest + '.tmp', exist_ok=True)
    lock_path = publish._lock_path(me['handle'])
    open(lock_path, 'w').close()
    con.execute('INSERT INTO search_cards (knife_id, owner_id, handle, model, generation, size, born_year,'
                ' damascus_smith, damascus_pattern, special_edition, for_sale, card)'
                " VALUES (?, ?, ?, 'Sebenza', '31', 'Large', 2025, '', '', '', 0, '{}')",
                (me['id'] * 1_000_000 + 1, me['id'], me['handle']))
    con.execute('INSERT INTO search_fts (rowid, text) VALUES (?, ?)', (me['id'] * 1_000_000 + 1, 'sebenza'))
    con.commit()
    con.close()
    assert client.post(S + '/delete', json={'confirm': 'nope'}).status_code == 400
    assert client.post(S + '/delete', json={}).status_code == 400
    r = client.post(S + '/delete', json={'confirm': me['handle']})
    assert r.status_code == 200, r.data
    assert r.get_json() == {'ok': True, 'deleted': {
        'knives': 1, 'photos': 1, 'store_keys': 1, 'store_failed': 0, 'surface_removed': True}}
    assert client.get('/blade-book/api/auth/me').status_code == 401          # cookie cleared
    con = db.connect()
    assert db.get_user(con, me['id']) is None and db.is_tombstoned(con, 'sam@example.com')
    assert con.execute('SELECT count(*) FROM search_cards WHERE owner_id = ?', (me['id'],)).fetchone()[0] == 0
    assert con.execute('SELECT count(*) FROM search_fts WHERE rowid = ?',
                       (me['id'] * 1_000_000 + 1,)).fetchone()[0] == 0
    con.close()
    assert not app.config['STORE'].exists(k['photos'][0]['store_key'])
    assert not os.path.exists(dest) and not os.path.exists(dest + '.tmp')
    assert not os.path.exists(lock_path)


def test_delete_refuses_admin(client, mailer):
    me = signed_in(client, mailer)
    con = db.connect()
    con.execute('UPDATE users SET is_admin = 1 WHERE id = ?', (me['id'],))
    con.commit()
    con.close()
    r = client.post(S + '/delete', json={'confirm': me['handle']})
    assert r.status_code == 403
    assert client.get('/blade-book/api/auth/me').status_code == 200


def test_delete_requires_auth(client):
    assert client.post(S + '/delete', json={'confirm': 'x'}).status_code == 401
