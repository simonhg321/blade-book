import io

import pytest
from PIL import Image

from bb import decode, photos

heif = pytest.importorskip('pillow_heif')  # absent locally → skipped, not failed


def _heic(w=1200, h=900):
    heif.register_heif_opener()
    img = Image.new('RGB', (w, h), (30, 60, 90))
    buf = io.BytesIO(); img.save(buf, format='HEIF', quality=60)
    return buf.getvalue()


def test_heif_flag_and_format_registered():
    assert photos.HEIF_OK is True and 'HEIF' in photos._OPEN_FORMATS


def test_ingest_heic_gets_dims_and_thumb():
    ing = photos.ingest(_heic(), 'IMG_0001.HEIC')
    assert ing.ext == 'heic' and (ing.width, ing.height) == (1200, 900)
    assert ing.thumb and Image.open(io.BytesIO(ing.thumb)).format == 'JPEG'


def test_prep_image_heic_to_jpeg():
    out = decode.prep_image(_heic(4000, 3000))
    im = Image.open(io.BytesIO(out))
    assert im.format == 'JPEG' and max(im.size) <= decode.MAX_EDGE


def test_heif_cap_is_its_own():
    assert photos.MAX_PIXELS_HEIF == 50_000_000
