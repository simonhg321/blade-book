# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import io

from PIL import Image

from bb import db, photos
from tests.conftest import signed_in

K = '/blade-book/api/knives'


def _jpeg(w=800, h=600):
    img = Image.new('RGB', (w, h), (10, 120, 200))
    buf = io.BytesIO(); img.save(buf, 'JPEG'); return buf.getvalue()


def _up(client, kid, seq, data=None, name='IMG_1.jpg'):
    return client.post(f'{K}/{kid}/photos/{seq}',
                       data={'photo': (io.BytesIO(data if data is not None else _jpeg()), name)},
                       content_type='multipart/form-data')


def test_upload_slot_stores_original_and_thumb(client, mailer, app):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    data = _jpeg()
    r = _up(client, kid, 1, data)
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body['seq'] == 1 and body['width'] == 800 and body['has_thumb'] is True
    store = app.config['STORE']
    assert store.get(f"{me['id']}/{kid}/1.jpg") == data  # byte-exact original
    assert store.exists(f"{me['id']}/{kid}/1.thumb.jpg")
    k = client.get(f'{K}/{kid}').get_json()
    assert [p['seq'] for p in k['photos']] == [1] and 'store_key' not in str(k)
    assert client.get(K + '/').get_json()['knives'][0]['photo_count'] == 1


def test_thumb_and_original_endpoints(client, mailer):
    signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    data = _jpeg()
    _up(client, kid, 2, data, name='shot.JPG')
    t = client.get(f'{K}/{kid}/photos/2/thumb')
    assert t.status_code == 200 and t.mimetype == 'image/jpeg'
    assert max(Image.open(io.BytesIO(t.data)).size) == 400
    assert 'private' in t.headers.get('Cache-Control', '')
    o = client.get(f'{K}/{kid}/photos/2/original')
    assert o.status_code == 200 and o.mimetype == 'image/jpeg' and o.data == data
    assert 'K01-2.jpg' in o.headers.get('Content-Disposition', '')
    assert client.get(f'{K}/{kid}/photos/3/thumb').status_code == 404
    assert client.get(f'{K}/{kid}/photos/3/original').status_code == 404


def test_undecodable_heic_is_stored_without_thumb(client, mailer, app, env):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    r = _up(client, kid, 1, b'\x00\x00\x00\x18ftypheic-not-really', name='IMG_9.HEIC')
    assert r.status_code == 201
    assert r.get_json()['has_thumb'] is False and r.get_json()['width'] is None
    assert app.config['STORE'].exists(f"{me['id']}/{kid}/1.heic")
    assert client.get(f'{K}/{kid}/photos/1/thumb').status_code == 404
    o = client.get(f'{K}/{kid}/photos/1/original')
    assert o.status_code == 200 and o.mimetype == 'image/heic'


def test_slot_rules(client, mailer, app, env):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    assert _up(client, kid, 1).status_code == 201
    r = _up(client, kid, 1, name='again.png')
    assert r.status_code == 409 and 'taken' in r.get_json()['error']
    assert not app.config['STORE'].exists(f"{me['id']}/{kid}/1.png")  # no orphan on 409
    assert _up(client, kid, 0).status_code == 400
    assert _up(client, kid, 4).status_code == 400
    assert client.post(f'{K}/{kid}/photos/2', data={}, content_type='multipart/form-data').status_code == 400
    assert _up(client, kid, 2, name='scan.pdf').status_code == 415
    assert _up(client, kid, 2, b'x' * (photos.MAX_PHOTO_BYTES + 1)).status_code == 400
    assert _up(client, 999, 1).status_code == 404


def test_delete_slot_removes_files_and_frees_slot(client, mailer, app, env):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    _up(client, kid, 3)
    store = app.config['STORE']
    assert store.exists(f"{me['id']}/{kid}/3.jpg") and store.exists(f"{me['id']}/{kid}/3.thumb.jpg")
    assert client.delete(f'{K}/{kid}/photos/3').status_code == 200
    assert not store.exists(f"{me['id']}/{kid}/3.jpg") and not store.exists(f"{me['id']}/{kid}/3.thumb.jpg")
    assert client.delete(f'{K}/{kid}/photos/3').status_code == 404
    assert _up(client, kid, 3, name='new.png').status_code == 201


def test_photos_are_owner_scoped(app, mailer):
    a, b = app.test_client(), app.test_client()
    signed_in(a, mailer, 'a@example.com')
    kid = a.post(K + '/').get_json()['id']
    _up(a, kid, 1)
    signed_in(b, mailer, 'b@example.com')
    assert _up(b, kid, 2).status_code == 404
    assert b.get(f'{K}/{kid}/photos/1/thumb').status_code == 404
    assert b.get(f'{K}/{kid}/photos/1/original').status_code == 404
    assert b.delete(f'{K}/{kid}/photos/1').status_code == 404
    assert a.get(f'{K}/{kid}/photos/1/original').status_code == 200


def test_delete_draft_removes_its_files(client, mailer, app, env):
    me = signed_in(client, mailer)
    kid = client.post(K + '/').get_json()['id']
    _up(client, kid, 1); _up(client, kid, 2)
    store = app.config['STORE']
    assert client.delete(f'{K}/{kid}').status_code == 200
    for key in (f"{me['id']}/{kid}/1.jpg", f"{me['id']}/{kid}/1.thumb.jpg",
                f"{me['id']}/{kid}/2.jpg", f"{me['id']}/{kid}/2.thumb.jpg"):
        assert not store.exists(key), key
