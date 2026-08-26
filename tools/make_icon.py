# Tank Monitor Console Simulator -- a training simulator for TLS-350
# compatible tank monitor consoles.
# Copyright (C) 2026 Verbose Software
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. It is distributed WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License (LICENSE) for more details.
"""Draw the application icon, with no image library on the machine.

The product has no icon otherwise, and an unbranded `.exe` shows the Windows
default, which reads as unfinished. So the icon is drawn here from the app's
own hero image: the buried tank seen end-on, amber product in it, the probe
down the middle, and the surface as a bright green line with the float riding
it. Rendered pixel by pixel and written out as a PNG and a multi-size ICO,
both by hand, because PyMuPDF is the only imaging code on this machine and it
is a dev-only tool that does not ship.

Run it to regenerate `assets/icon.ico` and `assets/icon.png` after changing
the look. It is not run at build time; the committed icons are the artefacts.
"""
import math
import os
import struct
import zlib

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "assets")

# The badge the mark sits on: a dark modern app tile.
BADGE_TOP = (0x24, 0x28, 0x2E)     # top of the badge gradient
BADGE_BOT = (0x12, 0x15, 0x19)     # bottom of it
BADGE_EDGE = (0x3A, 0x40, 0x48)    # a thin lit rim
GLASS = (0x07, 0x12, 0x0A)         # the display glass, near black
GLASS_EDGE = (0x0F, 0x2A, 0x18)
VFD_INK = (0x74, 0xFF, 0x95)       # the bright green
VFD_DIM = (0x2A, 0x6E, 0x42)       # a dimmer green for the second line
VFD_GLOW = (0x74, 0xFF, 0x95, 60)  # the halo behind the green
NAVY = (0x3D, 0x6B, 0xD6)          # one cool accent, the level marker
SHADOW = (0x00, 0x00, 0x00, 90)


def blend(dst, src):
    """src over dst, both RGBA tuples, straight alpha."""
    sa = src[3] / 255.0
    return tuple(int(src[i] * sa + dst[i] * (1 - sa)) for i in range(3)) \
        + (255,)


class Canvas:
    def __init__(self, size, bg=(0, 0, 0, 0)):
        self.n = size
        self.px = [list(bg) for _ in range(size * size)]

    def put(self, x, y, rgba):
        if 0 <= x < self.n and 0 <= y < self.n:
            i = y * self.n + x
            if len(rgba) == 3:
                rgba = rgba + (255,)
            self.px[i] = list(blend(self.px[i], rgba) if rgba[3] < 255
                              else rgba + () if len(rgba) == 4 else rgba)

    def rect(self, x0, y0, x1, y1, rgba):
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                self.put(x, y, rgba)

    def rrect(self, x0, y0, x1, y1, r, rgba):
        """A rounded rectangle, corners of radius r, lightly antialiased."""
        for y in range(int(y0), int(y1)):
            for x in range(int(x0), int(x1)):
                dx = min(x - x0, x1 - 1 - x)
                dy = min(y - y0, y1 - 1 - y)
                if dx < r and dy < r:
                    dist = math.hypot(r - dx, r - dy)
                    if dist > r:
                        continue
                    if dist > r - 1.2:
                        a = max(0.0, r - dist) / 1.2
                        col = rgba[:3] + (int((rgba[3] if len(rgba) > 3
                                               else 255) * a),)
                        self.put(x, y, col)
                        continue
                self.put(x, y, rgba)


def _lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _disc(c, cx, cy, r, col, aa=1.5):
    """A filled circle with a soft edge."""
    for y in range(int(cy - r - 2), int(cy + r + 2)):
        for x in range(int(cx - r - 2), int(cx + r + 2)):
            d = math.hypot(x - cx, y - cy)
            if d <= r - aa:
                c.put(x, y, col + (255,))
            elif d <= r:
                c.put(x, y, col + (int(255 * (r - d) / aa),))


def _ring(c, cx, cy, r, w, col):
    """A circle outline of width w, soft on both edges."""
    for y in range(int(cy - r - 2), int(cy + r + 2)):
        for x in range(int(cx - r - 2), int(cx + r + 2)):
            d = math.hypot(x - cx, y - cy)
            if r - w <= d <= r:
                edge = min(d - (r - w), r - d)
                a = 255 if edge > 1.2 else max(int(255 * edge / 1.2), 0)
                c.put(x, y, col + (a,))


def _segment(c, cx, cy, r, level, col):
    """Liquid in a circle: fill below `level` of its height."""
    ys = cy + r - level * 2 * r
    for y in range(int(cy - r), int(cy + r)):
        if y < ys:
            continue
        half = math.sqrt(max(0.0, r * r - (y - cy) ** 2))
        for x in range(int(cx - half) + 1, int(cx + half)):
            c.put(x, y, col + (255,))


# the mark's own palette
STEEL = (0x8A, 0x93, 0x9E)     # the tank shell
ULLAGE = (0x0A, 0x0D, 0x11)    # the space over the fuel
FUEL = (0xF2, 0xB0, 0x3C)      # the product
FUEL_DEEP = (0xC4, 0x85, 0x1E)
WATER = (0x3D, 0x6B, 0xD6)     # the sliver under it, as on the bench
SURFACE = (0x74, 0xFF, 0x95)   # the reading: the green line the probe takes
PROBE = (0xEC, 0xEE, 0xF0)


def draw(size):
    """The tank, end on: the app's own hero image as its mark.

    A buried cylinder seen from the end, amber product in it, a thin
    blue water layer under that, the probe down the middle, and the
    surface drawn as a bright green line with the float riding it. The
    green line IS the product: the one number the whole machine exists
    to read.
    """
    c = Canvas(size)
    s = size / 256.0

    # the badge: dark rounded tile with a gradient and a lit rim
    x0, y0, x1, y1, r = 12 * s, 12 * s, 244 * s, 244 * s, 52 * s
    c.rrect(x0 - 2 * s, y0 - 2 * s, x1 + 2 * s, y1 + 2 * s, r + 2 * s,
            BADGE_EDGE + (255,))
    span = y1 - y0
    for y in range(int(y0), int(y1)):
        col = _lerp(BADGE_TOP, BADGE_BOT, (y - y0) / span) + (255,)
        for x in range(int(x0), int(x1)):
            dx = min(x - x0, x1 - 1 - x)
            dy = min(y - y0, y1 - 1 - y)
            if dx < r and dy < r and math.hypot(r - dx, r - dy) > r:
                continue
            c.put(x, y, col)

    cx, cy, rr = 128 * s, 134 * s, 72 * s
    level = 0.58
    ys = cy + rr - level * 2 * rr

    # a soft amber glow behind the tank, so the mark sits in light
    for y in range(int(cy - rr * 1.3), int(cy + rr * 1.3)):
        for x in range(int(cx - rr * 1.3), int(cx + rr * 1.3)):
            d = math.hypot(x - cx, y - cy)
            if d < rr * 1.3:
                a = int(14 * (1 - d / (rr * 1.3)))
                if a > 0:
                    c.put(x, y, FUEL + (a,))

    # the shell, the ullage, the fuel, the water
    _ring(c, cx, cy, rr + 7 * s, 7 * s, STEEL)
    _disc(c, cx, cy, rr, ULLAGE)
    _segment(c, cx, cy, rr, level, FUEL)
    _segment(c, cx, cy, rr, level * 0.45, FUEL_DEEP)
    _segment(c, cx, cy, rr, 0.075, WATER)

    # the surface: a bright green line with a soft glow above and below
    half = math.sqrt(max(0.0, rr * rr - (ys - cy) ** 2))
    for g, alpha in ((6 * s, 40), (3.5 * s, 90)):
        c.rect(cx - half, ys - g, cx + half, ys + g, SURFACE + (alpha,))
    c.rect(cx - half, ys - 2.2 * s, cx + half, ys + 2.2 * s,
           SURFACE + (255,))

    # the probe: riser from the badge top, shaft to near the bottom
    c.rect(cx - 2.6 * s, y0 + 8 * s, cx + 2.6 * s, cy + rr - 8 * s,
           PROBE + (255,))
    c.rect(cx - 6 * s, y0 + 8 * s, cx + 6 * s, y0 + 16 * s,
           STEEL + (255,))

    # the float, riding the surface
    _disc(c, cx, ys, 9.5 * s, PROBE)
    _disc(c, cx, ys, 4.6 * s, SURFACE)
    return c


def write_png(canvas, path):
    n = canvas.n
    raw = bytearray()
    for y in range(n):
        raw.append(0)                    # filter type 0
        for x in range(n):
            raw.extend(canvas.px[y * n + x])
    comp = zlib.compress(bytes(raw), 9)

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", comp)
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)
    return png


def write_ico(png_by_size, path):
    """An ICO with each size stored as an embedded PNG (Vista+ format)."""
    entries, blobs, offset = [], [], 6 + 16 * len(png_by_size)
    for size, png in sorted(png_by_size.items()):
        dim = 0 if size >= 256 else size
        entries.append(struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                                   len(png), offset))
        blobs.append(png)
        offset += len(png)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(png_by_size)))
        for e in entries:
            f.write(e)
        for b in blobs:
            f.write(b)


def main():
    os.makedirs(OUT, exist_ok=True)
    pngs = {}
    for size in (16, 32, 48, 64, 128, 256):
        canvas = draw(size)
        png = write_png(canvas, os.path.join(OUT, f"icon-{size}.png"))
        pngs[size] = png
    # the canonical single PNG and the multi-size ICO
    write_png(draw(256), os.path.join(OUT, "icon.png"))
    write_ico(pngs, os.path.join(OUT, "icon.ico"))
    # clean up the per-size PNGs; the ICO carries them now
    for size in (16, 32, 48, 64, 128, 256):
        p = os.path.join(OUT, f"icon-{size}.png")
        if os.path.exists(p):
            os.remove(p)
    print(f"wrote {OUT}\\icon.ico and icon.png")


if __name__ == "__main__":
    main()
