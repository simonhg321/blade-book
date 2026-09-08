# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import io
import json

import pytest
from PIL import Image

from bb import db, decode
from tests.conftest import ok_result, signed_in

K = '/blade-book/api/knives'


def _jpeg(w=800, h=600):
    img = Image.new('RGB', (w, h), (10, 120, 200))
    buf = io.BytesIO(); img.save(buf, 'JPEG'); return buf.getvalue()


def _draft_with_photo(client, mailer, email='a@example.com'):
    signed_in(client, mailer, email)
    kid = client.post(K + '/').get_json()['id']
    r = client.post(f'{K}/{kid}/photos/1', data={'photo': (io.BytesIO(_jpeg()), 'a.jpg')},
                    content_type='multipart/form-data')
    assert r.status_code == 201
    return kid


def test_decode_happy_path(client, mailer, decoder):
    kid = _draft_with_photo(client, mailer)
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 200, r.get_json()
    j = r.get_json()
    assert j['model'] == 'Sebenza' and j['ext']['generation'] == '31'
    assert j['card_text'] == 'LARGE SEBENZA 31' and j['status'] == 'draft'
    assert j['decoded']['flags'] == [] and j['decoded']['model'] == 'fake'
    assert j['decoded']['age_months'] is not None and j['decoded']['no_card'] is False
    assert decoder.calls == [(1, '', 'crk', False)]
    # ledger line written
    from bb import paths
    line = json.loads(open(paths.ai_log()).read().splitlines()[-1])
    assert line['ok'] is True and line['knife'] == kid


def test_decode_passes_note_and_no_card(client, mailer, decoder):
    kid = _draft_with_photo(client, mailer)
    client.put(f'{K}/{kid}/note', json={'note': 'Large 31 from a collector'})
    r = client.post(f'{K}/{kid}/decode', json={'no_card': True})
    assert r.status_code == 200
    assert decoder.calls[-1] == (1, 'Large 31 from a collector', 'crk', True)


def test_decode_requires_photo_and_ownership(client, mailer, decoder):
    signed_in(client, mailer, 'a@example.com')
    kid = client.post(K + '/').get_json()['id']
    assert client.post(f'{K}/{kid}/decode').status_code == 400        # no photos
    client.post(f'{K}/{kid}/photos/1', data={'photo': (io.BytesIO(_jpeg()), 'a.jpg')}, content_type='multipart/form-data')
    con = db.connect(); con.execute("UPDATE knives SET status = 'live' WHERE id = ?", (kid,)); con.commit(); con.close()
    assert client.post(f'{K}/{kid}/decode').status_code == 200        # live: re-decode allowed
    client.post('/blade-book/api/auth/signout')
    signed_in(client, mailer, 'b@example.com')
    assert client.post(f'{K}/{kid}/decode').status_code == 404        # not yours
    assert len(decoder.calls) == 1


def test_decode_unauth_is_401(client):
    assert client.post(f'{K}/1/decode').status_code == 401


def test_decode_daily_cap_free_vs_paid(client, mailer, decoder, monkeypatch):
    from bb.routes import knives as kr
    monkeypatch.setattr(db, 'DECODES_PER_MINUTE', 10 ** 6)             # daily cap under test, not the burst limiter
    kid = _draft_with_photo(client, mailer)
    con = db.connect()
    for _ in range(kr.FREE_DECODES_PER_DAY):
        db.record_decode_call(con, 1, kid)
    con.close()
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 429 and 'today' in r.get_json()['error']
    con = db.connect(); con.execute("UPDATE users SET sub_status = 'active' WHERE id = 1"); con.commit(); con.close()
    assert client.post(f'{K}/{kid}/decode').status_code == 200


def test_decode_failure_is_502_and_logged(client, mailer, app):
    app.config['DECODER'] = decode.FakeDecoder(decode.DecodeError('model refused'))
    kid = _draft_with_photo(client, mailer)
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 502 and 'try again' in r.get_json()['error']
    from bb import paths
    line = json.loads(open(paths.ai_log()).read().splitlines()[-1])
    assert line['ok'] is False and 'refused' in line['error']
    assert isinstance(line['ms'], int) and line['ms'] >= 0             # failure ledger carries real timing
    assert db.decodes_today(db.connect(), 1) == 1                      # review H1: a failed call was still billed


def test_decode_unknown_maker_is_400(client, mailer, decoder):
    kid = _draft_with_photo(client, mailer)
    con = db.connect(); con.execute("UPDATE knives SET maker = 'unknown' WHERE id = ?", (kid,))
    con.commit(); con.close()
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 400 and r.get_json()['error'] == 'unknown maker'
    assert decoder.calls == []


def test_decode_not_configured_is_503(client, mailer, app):
    app.config['DECODER'] = decode.NoDecoder()
    kid = _draft_with_photo(client, mailer)
    assert client.post(f'{K}/{kid}/decode').status_code == 503


def test_decode_refuses_undecodable_photo_at_upload(client, mailer, decoder):
    # M7 (security review): an undecodable file is now refused at ingest, not
    # stored with no thumb — it never reaches decode's own skip-and-400 path.
    signed_in(client, mailer, 'a@example.com')
    kid = client.post(K + '/').get_json()['id']
    r = client.post(f'{K}/{kid}/photos/1', data={'photo': (io.BytesIO(b'\x00' * 2000), 'raw.dng')}, content_type='multipart/form-data')
    assert r.status_code == 415
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 400 and 'add the box' in r.get_json()['error']   # no photos at all
    assert decoder.calls == []


def test_decode_400s_when_stored_photos_are_undecodable(client, mailer, decoder, monkeypatch):
    # The knife HAS photos (unlike the case above) but none of them decode —
    # still reachable in production for photos stored before this deploy, or
    # written by scripts/add_photo.py / import_crkinv.py, which bypass the
    # upload route's ingest() gate entirely. decode.images_for's own
    # skip-undecodable logic (bb/decode.py) is what has to catch this.
    kid = _draft_with_photo(client, mailer)
    monkeypatch.setattr(decode, 'images_for', lambda store, k: [])
    r = client.post(f'{K}/{kid}/decode')
    assert r.status_code == 400
    assert r.get_json()['error'] == 'none of the photos are decodable — re-shoot as JPEG/HEIC'
    assert decoder.calls == []


def test_decode_result_appears_in_get_and_private_fields_stay_owner_only(client, mailer, decoder):
    kid = _draft_with_photo(client, mailer)
    client.post(f'{K}/{kid}/decode')
    j = client.get(f'{K}/{kid}').get_json()
    assert j['card_text'] == 'LARGE SEBENZA 31' and j['confidence']['model'] == 'high'
    assert 'card_text' in db.PRIVATE_COLUMNS  # the /@handle bundle (plan 06) strips it


# --- plan 14: a Hinderer through the whole API ---

def test_decode_refiles_other_brands_and_edit_refiles_back(client, mailer, decoder):
    kid = _draft_with_photo(client, mailer)
    decoder.result = ok_result(maker_name='Hinderer Knives', model='XM-18', card_text='XM-18 3.5" SPANTO')
    j = client.post(f'{K}/{kid}/decode').get_json()
    assert j['maker'] == 'other' and j['maker_name'] == 'Hinderer Knives' and j['model'] == 'XM-18'
    assert j['ext'] == {} and j['decoded']['flags'] == []
    # an 'other' knife rejects CRK-only fields on edit ...
    r = client.patch(f'{K}/{kid}', json={'ext': {'generation': '31'}})
    assert r.status_code == 400 and 'ext.generation' in r.get_json()['error']
    # ... and re-files as CRK when the owner corrects the brand
    j = client.patch(f'{K}/{kid}', json={'maker_name': 'Chris Reeve Knives'}).get_json()
    assert j['maker'] == 'crk' and j['maker_name'] == 'Chris Reeve Knives'
    j = client.patch(f'{K}/{kid}', json={'ext': {'generation': '31', 'size': 'Large'}}).get_json()
    assert j['ext']['generation'] == '31'
    # back to Hinderer: the CRK ext is dropped, not merged
    j = client.patch(f'{K}/{kid}', json={'maker_name': 'Hinderer'}).get_json()
    assert j['maker'] == 'other' and j['ext'] == {}
    # a second decode on an 'other' draft keeps the hint when the model reads no brand
    decoder.result = ok_result(maker_name='', card_text='', model='XM-18')
    assert client.post(f'{K}/{kid}/decode').get_json()['maker'] == 'other'
