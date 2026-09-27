"""Napkin images for the 'knife leads' mockup (docs/mockups/knife.html).
Read-only on the live store; writes only into the current directory.

A record, not a tool: it ran against 7a8af01, when publish._watermark(img,
handle) was still the white diagonal shown as 'A · today'. The etch and the
plate below are the sketches that became bb/publish.py's mark."""
import sys
sys.path.insert(0, '/home/shg/blade-book')
from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageChops, ImageFilter
from bb import publish

FONTS = '/home/shg/blade-book/html/fonts/'
BEBAS = FONTS + 'BebasNeue-Regular.woff2'
DM = FONTS + 'DMSans.woff2'
CREAM, INK, ACCENT, MUTED = (250, 246, 238), (26, 24, 20), (184, 69, 46), (110, 104, 94)
HANDLE, TAG = 'simon-collector', 'K80'

src = ImageOps.exif_transpose(Image.open('/var/lib/blade-book/photos/1/83/1.jpg')).convert('RGB')
s = src.size[0] / 1200
crop = src.crop(tuple(int(v * s) for v in (480, 660, 1200, 1236)))
crop.thumbnail((1600, 1600))
crop.save('crop.jpg', quality=90)
card = src.copy(); card.thumbnail((1600, 1600))


def spaced(draw, xy, text, font, fill, gap):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + gap
    return x


def spaced_w(text, font, gap):
    d = ImageDraw.Draw(Image.new('L', (4, 4)))
    return int(sum(d.textlength(ch, font=font) + gap for ch in text))


def etch_mask(size, lines, angle):
    """lines: [(text, font_px, gap, (cx, cy))] → L mask, rotated about each centre."""
    mask = Image.new('L', size, 0)
    for text, px, gap, (cx, cy) in lines:
        font = ImageFont.truetype(BEBAS, px)
        tw = spaced_w(text, font, gap)
        t = Image.new('L', (tw + 20, int(px * 1.3)), 0)
        spaced(ImageDraw.Draw(t), (10, 0), text, font, 255, gap)
        t = t.rotate(angle, expand=True, resample=Image.BICUBIC)
        mask.paste(t, (int(cx - t.width / 2), int(cy - t.height / 2)), t)
    return mask


def etch(img, mask, strength=1.0):
    """Engraved look: the letters take the photo's own tones — a lit face, a
    shadowed lower-right edge — instead of white paint on top."""
    body = mask.filter(ImageFilter.GaussianBlur(0.6))
    shadow = ImageChops.subtract(ImageChops.offset(body, 2, 2), body)
    light = ImageChops.subtract(ImageChops.offset(body, -1, -1), body)
    grey = Image.new('L', img.size, 128)
    layer = ImageChops.add(grey, body.point(lambda v: int(v * 0.17 * strength)))
    layer = ImageChops.add(layer, light.point(lambda v: int(v * 0.30 * strength)))
    layer = ImageChops.subtract(layer, shadow.point(lambda v: int(v * 0.38 * strength)))
    return ImageChops.overlay(img, Image.merge('RGB', (layer,) * 3))


def plate(img, title, sub, url):
    w, h = img.size
    ph = int(w * 0.082)
    out = Image.new('RGB', (w, h + ph), CREAM)
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    d.line([(0, h), (w, h)], fill=INK, width=max(2, w // 500))
    pad = int(w * 0.022)
    big = ImageFont.truetype(BEBAS, int(ph * 0.50))
    small = ImageFont.truetype(DM, int(ph * 0.21))
    x = spaced(d, (pad, h + ph * 0.13), TAG, big, ACCENT, 2) + pad * 0.6
    spaced(d, (x, h + ph * 0.13), title, big, INK, 2)
    d.text((pad, h + ph * 0.66), sub, font=small, fill=MUTED)
    uw = d.textlength(url, font=small)
    d.text((w - pad - uw, h + ph * 0.66), url, font=small, fill=ACCENT)
    bb = ImageFont.truetype(BEBAS, int(ph * 0.34))
    bw = spaced_w('BLADE-BOOK', bb, 3)
    spaced(d, (w - pad - bw, h + ph * 0.17), 'BLADE-BOOK', bb, INK, 3)
    return out


W, H = crop.size
line = f'@{HANDLE.upper()}  ·  BLADE-BOOK  ·  {TAG}'
title, sub = 'LARGE SEBENZA 21 — GLORIOUS', 'born February 6, 2014  ·  Chris Reeve Knives  ·  @simon-collector'
url = 'blade-book.com/@simon-collector/K80'

# A — today's mark, the real function, on the same crop
publish._watermark(crop.copy(), HANDLE).save('a-today.jpg', quality=88)
# B — etched: one line along the knife's lower edge
m = etch_mask((W, H), [(line, int(W * 0.040), int(W * 0.006), (W * 0.47, H * 0.63))], -38)
etched = etch(crop, m)
etched.save('b-etched.jpg', quality=88)
# C — plate only, photo untouched
plate(crop, title, sub, url).save('c-plate.jpg', quality=88)
# D — etched + plate
plate(etched, title, sub, url).save('d-etched-plate.jpg', quality=88)

# the record — the full card shot carries the heavier mark: a quiet repeat over everything
cw, ch = card.size
rows = []
px = int(cw * 0.034)
step = int(ch * 0.155)
for i, cy in enumerate(range(step // 2, ch + step, step)):
    rows.append((line + '      ' + line, px, int(cw * 0.005), (cw * (0.5 + (0.12 if i % 2 else -0.12)), cy)))
rec = etch(card, etch_mask((cw, ch), rows, 24), strength=1.25)
plate(rec, title, sub, url).save('record.jpg', quality=86)
publish._watermark(card.copy(), HANDLE).save('record-today.jpg', quality=86)
for n in ('a-today', 'b-etched', 'c-plate', 'd-etched-plate', 'record', 'record-today', 'crop'):
    print(n, Image.open(n + '.jpg').size)
