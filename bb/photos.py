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
MAX_PIXELS = 80_000_000  # JPEG/MPO only — decoded cheaply via draft mode, so a bigger
                          # cap is safe; above any phone (48 MP) or DSLR (61 MP)
MAX_PIXELS_NON_JPEG = 30_000_000  # everything else is fully decoded — keep the cap tight
THUMB_EDGE = 800
_CHEAP_FORMATS = ('JPEG', 'MPO')  # Pillow can decode these at reduced scale via draft()
# NOTE: 'MPO' is deliberately absent here — Pillow never registers a separate 'MPO'
# opener (Image.OPEN has no 'MPO' key; passing it to formats= raises KeyError). MPO
# files are JPEGs with extra APP2 frames: the 'JPEG' factory detects them and swaps
# in an MpoImageFile (img.format == 'MPO') internally, so 'JPEG' alone covers both.
_OPEN_FORMATS = ['JPEG', 'PNG', 'GIF', 'WEBP', 'TIFF']  # Pillow only dispatches to
                                                          # these decoders regardless
                                                          # of the sniffed content
MAX_PIXELS_HEIF = 50_000_000  # pillow-heif decodes fully; a 48 MP iPhone HEIC must pass
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
    _OPEN_FORMATS.append('HEIF')
    HEIF_OK = True
except ImportError:  # optional dependency — HEIC then stores without a thumb, as before
    HEIF_OK = False

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
    dimensions exceed the format's cap (checked BEFORE any pixel data is decoded).
    `formats=` restricts Pillow to decoders we've vetted — it only ever dispatches
    to these regardless of what the file's bytes claim to be."""
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(data), formats=_OPEN_FORMATS)
    except Image.DecompressionBombError as e:
        raise TooBig(f'declared {e}') from e
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    w, h = img.size
    if img.format in _CHEAP_FORMATS:
        cap = MAX_PIXELS
    elif img.format == 'HEIF':
        cap = MAX_PIXELS_HEIF
    else:
        cap = MAX_PIXELS_NON_JPEG
    if w * h > cap:
        raise TooBig(f'{w}x{h} = {w * h} pixels > {cap}')
    return img


def _decode(data):
    """Returns (img, width, height) of the ORIGINAL (pre-draft) dimensions, or
    (None, None, None) if undecodable. For JPEG/MPO, img.draft() runs before
    img.load() so the decoder works at DCT-reduced scale — width/height are
    captured first because draft() changes img.size."""
    img = _open(data)
    if img is None:
        return None, None, None
    width, height = img.size
    try:
        if img.format in _CHEAP_FORMATS:
            img.draft('RGB', (THUMB_EDGE * 2, THUMB_EDGE * 2))
        img.load()
        return img, width, height
    except (OSError, ValueError):
        return None, None, None


def ingest(data, filename):
    if len(data) > MAX_PHOTO_BYTES:
        raise TooBig(f'{len(data)} bytes > {MAX_PHOTO_BYTES}')
    ext = ext_for(filename)
    if ext is None:
        raise BadType(f'{filename!r}: allowed {sorted(ALLOWED_EXT)}')
    sha = hashlib.sha256(data).hexdigest()
    img, width, height = _decode(data)
    if img is None:
        return Ingested(ext, sha, None, None, None)
    thumb = ImageOps.exif_transpose(img).convert('RGB')
    thumb.thumbnail((THUMB_EDGE, THUMB_EDGE))
    buf = io.BytesIO()
    thumb.save(buf, 'JPEG', quality=80, optimize=True)
    return Ingested(ext, sha, width, height, buf.getvalue())
