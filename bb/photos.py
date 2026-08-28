# Copyright (c) 2026 Simon SGH — blade-book.com — All rights reserved
"""
bb/photos.py — turn uploaded bytes into what the register stores. Ported
from billboard crkinv/photos.py: the original is kept EXACTLY as received
(full resolution, EXIF intact — stripping happens at publish, plan 06); the
thumb is a best-effort ≤400 px JPEG for the intake screen. A file Pillow
can't decode (HEIC without pillow-heif) is still stored; it just has no
thumb and no dimensions.
"""
import hashlib
import io
import os
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_EXT = frozenset({'jpg', 'jpeg', 'png', 'heic', 'heif', 'webp', 'tif', 'tiff', 'dng', 'gif'})
MAX_PHOTO_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 80_000_000  # above any phone (48 MP) or DSLR (61 MP); a bigger "photo" is a bomb
THUMB_EDGE = 400
MIME = {'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
        'heic': 'image/heic', 'heif': 'image/heif', 'webp': 'image/webp',
        'tif': 'image/tiff', 'tiff': 'image/tiff', 'dng': 'image/x-adobe-dng',
        'gif': 'image/gif'}


class TooBig(ValueError):
    pass


class BadType(ValueError):
    pass


@dataclass
class Ingested:
    ext: str
    sha256: str
    width: int | None
    height: int | None
    thumb: bytes | None


def ext_for(filename):
    ext = os.path.splitext(filename or '')[1].lstrip('.').lower()
    return ext if ext in ALLOWED_EXT else None


def _open(data):
    """Header-only open; None if Pillow can't identify it; TooBig if the declared
    dimensions exceed MAX_PIXELS (checked BEFORE any pixel data is decoded)."""
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data))
    except Image.DecompressionBombError as e:
        raise TooBig(f'declared {e}') from e
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    w, h = img.size
    if w * h > MAX_PIXELS:
        raise TooBig(f'{w}x{h} = {w * h} pixels > {MAX_PIXELS}')
    return img


def _decode(data):
    img = _open(data)
    if img is None:
        return None
    try:
        img.load()
        return img
    except (OSError, ValueError):
        return None


def ingest(data, filename):
    if len(data) > MAX_PHOTO_BYTES:
        raise TooBig(f'{len(data)} bytes > {MAX_PHOTO_BYTES}')
    ext = ext_for(filename)
    if ext is None:
        raise BadType(f'{filename!r}: allowed {sorted(ALLOWED_EXT)}')
    sha = hashlib.sha256(data).hexdigest()
    img = _decode(data)
    if img is None:
        return Ingested(ext, sha, None, None, None)
    width, height = img.size
    thumb = ImageOps.exif_transpose(img).convert('RGB')
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
    buf = io.BytesIO()
    thumb.save(buf, 'JPEG', quality=80, optimize=True)
    return Ingested(ext, sha, width, height, buf.getvalue())
