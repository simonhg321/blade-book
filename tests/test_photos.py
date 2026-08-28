# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
import hashlib
import io

import pytest
from PIL import Image

from bb import photos


def _jpeg(w=1200, h=900, exif_orientation=None):
    img = Image.new('RGB', (w, h), (200, 30, 30))
    buf = io.BytesIO()
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        img.save(buf, 'JPEG', exif=exif.tobytes())
    else:
        img.save(buf, 'JPEG')
    return buf.getvalue()


def test_ext_for_allows_known_and_rejects_others():
    assert photos.ext_for('IMG_0001.JPG') == 'jpg'
    assert photos.ext_for('shot.heic') == 'heic'
    assert photos.ext_for('x.png') == 'png'
    assert photos.ext_for('doc.pdf') is None
    assert photos.ext_for('noext') is None
    assert photos.ext_for('') is None


def test_ingest_jpeg_gives_sha_dims_and_thumb():
    data = _jpeg()
    r = photos.ingest(data, 'IMG_1.jpg')
    assert r.ext == 'jpg'
    assert r.sha256 == hashlib.sha256(data).hexdigest()
    assert (r.width, r.height) == (1200, 900)
    t = Image.open(io.BytesIO(r.thumb))
    assert t.format == 'JPEG' and max(t.size) == 400


def test_ingest_thumb_respects_exif_orientation():
    r = photos.ingest(_jpeg(1200, 900, exif_orientation=6), 'a.jpg')  # 6 = rotate 90
    t = Image.open(io.BytesIO(r.thumb))
    assert t.size[1] > t.size[0]  # portrait after transpose
    assert (r.width, r.height) == (1200, 900)  # dims are of the stored original, untouched


def test_ingest_undecodable_but_allowed_ext_is_stored_without_thumb():
    r = photos.ingest(b'not really an image', 'shot.heic')
    assert r.ext == 'heic' and r.thumb is None and r.width is None and r.height is None
    assert r.sha256 == hashlib.sha256(b'not really an image').hexdigest()


def test_ingest_rejects_too_big_and_bad_type():
    with pytest.raises(photos.TooBig):
        photos.ingest(b'x' * (photos.MAX_PHOTO_BYTES + 1), 'a.jpg')
    with pytest.raises(photos.BadType):
        photos.ingest(_jpeg(), 'a.pdf')


def test_mime_covers_every_allowed_ext():
    for ext in photos.ALLOWED_EXT:
        assert photos.MIME[ext].startswith('image/'), ext
