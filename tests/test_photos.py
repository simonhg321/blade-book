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


def _png_1bit(w, h):
    buf = io.BytesIO(); Image.new('1', (w, h), 1).save(buf, 'PNG'); return buf.getvalue()


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
    assert t.format == 'JPEG' and max(t.size) == photos.THUMB_EDGE


def test_ingest_thumb_respects_exif_orientation():
    r = photos.ingest(_jpeg(1200, 900, exif_orientation=6), 'a.jpg')  # 6 = rotate 90
    t = Image.open(io.BytesIO(r.thumb))
    assert t.size[1] > t.size[0]  # portrait after transpose
    assert (r.width, r.height) == (1200, 900)  # dims are of the stored original, untouched


def test_ingest_undecodable_but_allowed_ext_is_refused():
    with pytest.raises(photos.Undecodable):
        photos.ingest(b'not really an image', 'shot.heic')


def test_ingest_rejects_too_big_and_bad_type():
    with pytest.raises(photos.TooBig):
        photos.ingest(b'x' * (photos.MAX_PHOTO_BYTES + 1), 'a.jpg')
    with pytest.raises(photos.BadType):
        photos.ingest(_jpeg(), 'a.pdf')


def test_mime_covers_every_allowed_ext():
    for ext in photos.ALLOWED_EXT:
        assert photos.MIME[ext].startswith('image/'), ext


def test_ingest_rejects_decompression_bombs_before_decoding():
    small = _png_1bit(20000, 9000)          # 180 MP declared, tiny file → Pillow's own bomb error
    assert len(small) < photos.MAX_PHOTO_BYTES
    with pytest.raises(photos.TooBig):
        photos.ingest(small, 'bomb.png')
    with pytest.raises(photos.TooBig):      # 81 MP: under Pillow's threshold, over ours
        photos.ingest(_png_1bit(9000, 9000), 'big.png')
    r = photos.ingest(_png_1bit(5000, 5000), 'ok.png')   # 25 MP — under the non-JPEG cap
    assert (r.width, r.height) == (5000, 5000) and r.thumb is not None


def test_non_jpeg_pixel_cap_is_lower_than_jpeg():
    with pytest.raises(photos.TooBig):      # 36 MP PNG > MAX_PIXELS_NON_JPEG (30 MP)
        photos.ingest(_png_1bit(6000, 6000), 'big.png')
    r = photos.ingest(_png_1bit(5000, 5000), 'ok.png')  # 25 MP — fine
    assert (r.width, r.height) == (5000, 5000)


def test_large_jpeg_decodes_via_draft_mode():
    buf = io.BytesIO()
    Image.new('RGB', (8000, 6000), (10, 120, 200)).save(buf, 'JPEG', quality=30)
    data = buf.getvalue()
    assert len(data) < photos.MAX_PHOTO_BYTES  # well under the 48 MP JPEG/MPO cap
    r = photos.ingest(data, 'big.jpg')
    assert (r.width, r.height) == (8000, 6000)  # original dims, captured before draft()
    t = Image.open(io.BytesIO(r.thumb))
    assert t.format == 'JPEG' and max(t.size) == photos.THUMB_EDGE
