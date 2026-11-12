"""Draws the Plateau's surfaces as high-resolution PBR textures for RTX Remix.

    python plateau_textures.py <out_dir> [--size 512] [--only key,key]

GTA2 draws every map cell as a block: the top face (the lid) and the side faces
(the walls) each show one square 64 x 64 tile. An RTX Remix mod can swap each
tile for a sharper material, mapped over the same 0..1 square, so a texture made
here has the same layout as the 64 px tile: it is that tile, only sharper.

This draws 24 of them for the Plateau-Mont-Royal map (see README.md), all from
code, with nothing taken from the game:

- Lids, seen from straight above, tile in both directions: street and lane
  asphalt, sidewalk, pavers, park path, grass, backyard, and two flat roofs.
- Walls, one storey of one block face (top of the picture = top of the storey),
  tile left to right: for four facade families (red, brown and buff brick, and
  greystone) a ground storey with the bottom of the outside staircase, a middle
  storey with its balcony, and a top storey with its cornice; then a shopfront
  and two church walls.

For each key, <out_dir>/<key>/ gets:

    albedo.png     RGB, sRGB colour with no lighting baked in, apart from soft
                   ambient occlusion in recesses and the shadow of the painted
                   staircase and railings, which are flat pictures here
    normal.png     RGB tangent-space normal: x = +U (right), y = +V (down the
                   picture), z out of the surface, stored as n * 0.5 + 0.5, the
                   same as make_water_mod.py
    roughness.png  L, 0 = mirror, 255 = fully rough
    emissive.png   RGB, only for tiles with a lit window: warm 2700 K room light
    tile64.png     the albedo averaged down to 64 x 64, what the stock game shows

and <out_dir> gets manifest.json (key, kind, description, has_emissive) and
contact_sheet.png (every tile64 at 4x beside its albedo at 128 px).

How it is drawn. Each tile is a stack of layers painted onto four maps at once:
colour (kept linear until it is saved), height in millimetres, roughness and
emission. Shapes are measured in tile units (0..1) and anti-aliased from their
distance to the edge, so --size changes the sharpness, not the picture. Shapes
that cross an edge wrap round to the other side, which is what makes the tiles
seamless. The normal map comes from the height: a wall tile is taken as 3 m
across, a lid as 7.5 m, and tall steps are softened so a window recess does not
turn into a black line. Which windows are lit is fixed by a hash of the tile and
the window's number (about one in three), and every random choice comes from a
generator seeded with the tile's name, so two runs give the same bytes.

Needs numpy and Pillow.
"""
import argparse
import json
import os
import sys
import time
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

LID_MM = 7500    # a lid is about 7.5 m across (the map's blocks are 9 m by 7 m)
WALL_MM = 3000   # a wall tile is one storey, about 3 m, drawn square in the game


def C(r, g, b):
    """A colour written as 8-bit sRGB, returned as linear RGB."""
    c = np.array([r, g, b], float) / 255
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def encode(lin):
    """Linear RGB (0..1) to 8-bit sRGB."""
    c = np.clip(lin, 0, 1)
    s = np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)
    return np.rint(s * 255).astype(np.uint8)


# 2700 K, the warm white of a lamp indoors: (255, 169, 87) in sRGB.
WARM = C(255, 169, 87)

STONE = C(184, 180, 168)        # limestone sills and lintels
GLASS = C(34, 38, 43)           # a window with a dark room behind it
ROOM_LIT = C(104, 84, 60)       # a window with a lit room behind it
CURTAIN = C(120, 108, 92)
BRASS = C(170, 130, 60)
SLAB = C(122, 118, 110)         # painted wooden balcony floor


# ---------------------------------------------------------------- noise

def _bspline_axis(size, n):
    """Lattice indices and cubic B-spline weights for one axis of value noise."""
    x = (np.arange(size) + 0.5) / size * n - 0.5
    i = np.floor(x).astype(int)
    t = x - i
    w = np.stack([(1 - t) ** 3, 3 * t ** 3 - 6 * t ** 2 + 4,
                  -3 * t ** 3 + 3 * t ** 2 + 3 * t + 1, t ** 3]) / 6
    idx = (i[None, :] + np.arange(-1, 3)[:, None]) % n
    return idx, w


def vnoise(rng, size, nx, ny=None):
    """Smooth noise from an nx by ny lattice that wraps round, mean 0, spread 1.

    The lattice, not the picture size, sets the pattern, so the same noise comes
    out at any --size."""
    ny = nx if ny is None else ny
    nx, ny = max(1, min(int(nx), size)), max(1, min(int(ny), size))
    g = rng.standard_normal((ny, nx))
    ix, wx = _bspline_axis(size, nx)
    iy, wy = _bspline_axis(size, ny)
    a = sum(g[:, ix[k]] * wx[k] for k in range(4))
    out = sum(a[iy[k], :] * wy[k][:, None] for k in range(4))
    out = out - out.mean()
    return out / (out.std() + 1e-12)


def fbm(rng, size, n, octaves=4, gain=0.5, aspect=1.0):
    """Several octaves of vnoise added up, coarse to fine."""
    out = np.zeros((size, size))
    for o in range(octaves):
        f = n * 2 ** o
        out += gain ** o * vnoise(rng, size, f, f * aspect)
    out -= out.mean()
    return out / (out.std() + 1e-12)


def worley(rng, size, n, jitter=0.85):
    """Cellular noise on an n x n grid that wraps round: the distance to the
    nearest and second nearest point (in cells) and the nearest point's number."""
    pts = rng.random((n, n, 2)) * jitter + (1 - jitter) / 2
    x = (np.arange(size) + 0.5) / size * n
    X = np.broadcast_to(x[None, :], (size, size))
    Y = np.broadcast_to(x[:, None], (size, size))
    cx, cy = np.floor(X).astype(int), np.floor(Y).astype(int)
    f1 = np.full((size, size), 9.0)
    f2 = np.full((size, size), 9.0)
    ident = np.zeros((size, size), int)
    for dj in (-1, 0, 1):
        for di in (-1, 0, 1):
            gx, gy = cx + di, cy + dj
            p = pts[gy % n, gx % n]
            d = np.hypot(X - gx - p[..., 0], Y - gy - p[..., 1])
            closer = d < f1
            f2 = np.where(closer, f1, np.minimum(f2, d))
            ident = np.where(closer, (gy % n) * n + gx % n, ident)
            f1 = np.where(closer, d, f1)
    return f1, f2, ident


def gblur(a, sigma, wrap_y=True):
    """Gaussian blur of sigma pixels. Always wraps left-right; top-bottom only
    when wrap_y (lids), otherwise the edge rows are repeated (walls)."""
    if sigma < 0.25:
        return a
    r = int(np.ceil(3 * sigma))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    out = np.zeros_like(a)
    for i, w in enumerate(k):
        out += w * np.roll(a, i - r, axis=1)
    if wrap_y:
        tmp, out = out, np.zeros_like(a)
        for i, w in enumerate(k):
            out += w * np.roll(tmp, i - r, axis=0)
    else:
        n = a.shape[0]
        p = np.pad(out, [(r, r)] + [(0, 0)] * (a.ndim - 1), mode='edge')
        out = sum(w * p[i:i + n] for i, w in enumerate(k))
    return out


# ---------------------------------------------------------------- the tile

class Tile:
    """One texture being painted: colour, height, roughness and emission.

    u runs left to right and v top to bottom, both 0..1 across the tile.
    """

    def __init__(self, key, kind, size):
        self.key, self.kind, self.s = key, kind, size
        self.px = 1.0 / size
        self.lid = kind == 'lid'
        self.mm = LID_MM if self.lid else WALL_MM
        self.x = (np.arange(size) + 0.5) / size
        self.u = np.broadcast_to(self.x[None, :], (size, size))
        self.v = np.broadcast_to(self.x[:, None], (size, size))
        self.rng = np.random.default_rng(zlib.crc32(key.encode()))
        self.alb = np.zeros((size, size, 3))
        self.h = np.zeros((size, size))
        self.rough = np.full((size, size), 220.0)
        self.emis = np.zeros((size, size, 3))
        self.windows = 0
        self.bump = 1.0     # normal map strength
        self.ao = 30.0      # mm of recess that counts as fully occluded

    # noise in this tile's own random sequence
    def noise(self, nx, ny=None):
        return vnoise(self.rng, self.s, nx, ny)

    def fbm(self, n, octaves=4, gain=0.5, aspect=1.0):
        return fbm(self.rng, self.s, n, octaves, gain, aspect)

    # shapes: each returns coverage 0..1, anti-aliased over one pixel
    def du(self, c):
        return (self.u - c + 0.5) % 1.0 - 0.5

    def dv(self, c):
        return (self.v - c + 0.5) % 1.0 - 0.5 if self.lid else self.v - c

    def edge(self, d):
        """Coverage from a signed distance in tile units, positive inside."""
        return np.clip(d / self.px + 0.5, 0, 1)

    def rect(self, x0, y0, x1, y1):
        dx, dy = self.du((x0 + x1) / 2), self.dv((y0 + y1) / 2)
        return self.edge(np.minimum((x1 - x0) / 2 - np.abs(dx), (y1 - y0) / 2 - np.abs(dy)))

    def band(self, y0, y1):
        return self.rect(0, y0, 1, y1)

    def disc(self, cx, cy, r):
        return self.edge(r - np.hypot(self.du(cx), self.dv(cy)))

    def ring(self, cx, cy, r, w):
        return self.edge(w / 2 - np.abs(np.hypot(self.du(cx), self.dv(cy)) - r))

    def periodic(self, n, phase=0.5):
        """Signed distance along u to the nearest of n evenly spaced centres
        at (k + phase) / n."""
        return ((self.u * n - phase) % 1.0 - 0.5) / n

    def put(self, m, col=None, h=None, rough=None, emis=0.0, hadd=None):
        """Paints coverage m: colour, height (mm), roughness, emission. Anything
        painted is opaque, so it puts out what glows under it unless told."""
        m = np.asarray(m, float)
        if col is not None:
            self.alb += (col - self.alb) * m[..., None]
        if h is not None:
            self.h += (h - self.h) * m
        if hadd is not None:
            self.h += hadd * m
        if rough is not None:
            self.rough += (rough - self.rough) * m
        if emis is not None:
            self.emis += (emis - self.emis) * m[..., None]

    def shade(self, m, amount):
        """Darkens the colour under m by a fraction."""
        self.alb *= (1 - amount * m)[..., None]

    def shadow(self, m, dx=0.010, dy=0.010, blur=0.004, strength=0.45):
        """The soft shadow of a flat painted object (stair, railing) on the
        wall behind it, falling right and down."""
        sy, sx = int(round(dy * self.s)), int(round(dx * self.s))
        sh = np.roll(m, (sy, sx), axis=(0, 1))
        if not self.lid and sy > 0:
            sh[:sy] = 0
        self.shade(gblur(sh, blur * self.s, self.lid), strength)

    def next_lit(self):
        """Whether the next window drawn has its light on: about one in three,
        fixed by the tile's name and the window's number."""
        i = self.windows
        self.windows += 1
        return zlib.crc32(f'{self.key}/window{i}'.encode()) % 3 == 0

    def finish(self):
        """Turns the layers into the five images."""
        s = self.s
        scale = s / 512
        cav = np.clip((gblur(self.h, 4 * scale, self.lid) - self.h) / self.ao, 0, 1)
        alb = np.clip(self.alb * (1 - 0.35 * cav)[..., None], 0.012, 0.85)
        # Tall steps are squashed (log) so a 9 cm window recess reads as an edge,
        # not as a wall turned sideways; fine relief keeps its true slope.
        hc = 20 * np.sign(self.h) * np.log1p(np.abs(self.h) / 20)
        hc = gblur(hc, 0.7 * scale, self.lid)
        mmpx = self.mm / s
        gx = (np.roll(hc, -1, 1) - np.roll(hc, 1, 1)) / (2 * mmpx)
        if self.lid:
            gy = (np.roll(hc, -1, 0) - np.roll(hc, 1, 0)) / (2 * mmpx)
        else:
            gy = np.gradient(hc, axis=0) / mmpx
        n = np.stack([-gx * self.bump, -gy * self.bump, np.ones_like(gx)], -1)
        n /= np.linalg.norm(n, axis=-1, keepdims=True)
        out = {
            'albedo': encode(alb),
            'normal': np.rint((n * 0.5 + 0.5) * 255).astype(np.uint8),
            'roughness': np.clip(np.rint(self.rough), 0, 255).astype(np.uint8),
            'tile64': encode(alb.reshape(64, s // 64, 64, s // 64, 3).mean(axis=(1, 3))),
        }
        if self.emis.max() > 1e-3:
            out['emissive'] = encode(self.emis)
        return out


def wavy_line(t, along, c, amp, n, width):
    """A crack or seal running right across the tile, wandering by amp; it
    wraps, so it continues into the next tile."""
    w = vnoise(t.rng, t.s, n, 1)[0] * amp
    slope = (np.roll(w, -1) - np.roll(w, 1)) * t.s / 2
    if along == 'u':
        d = ((t.v - c - w[None, :] + 0.5) % 1 - 0.5) / np.sqrt(1 + slope[None, :] ** 2)
    else:
        d = ((t.u - c - w[:, None] + 0.5) % 1 - 0.5) / np.sqrt(1 + slope[:, None] ** 2)
    return t.edge(width / 2 - np.abs(d))


def level_lines(t, field, width):
    """Lines where a noise field crosses zero: a network of meandering cracks."""
    gx = (np.roll(field, -1, 1) - np.roll(field, 1, 1)) * t.s / 2
    gy = (np.roll(field, -1, 0) - np.roll(field, 1, 0)) * t.s / 2
    return t.edge(width / 2 - np.abs(field) / (np.hypot(gx, gy) + 1e-9))


def table(rng, shape, spread):
    """A random factor near 1 for each brick, block or board."""
    return 1 + rng.normal(0, spread, shape)


# ---------------------------------------------------------------- lids

def asphalt(t):
    t.ao, t.bump = 6.0, 1.4
    base = C(80, 80, 82)
    low = t.fbm(3, 3)
    grain = t.noise(t.s // 2)
    f1, f2, cid = worley(t.rng, t.s, 120)
    tone = t.rng.random(120 * 120)[cid]
    stone = np.clip((0.42 - f1) / 0.12, 0, 1)
    col = base * (1 + 0.09 * low + 0.07 * grain)[..., None]
    col = col * (1 + 0.35 * stone * (tone - 0.5))[..., None]
    light = (tone > 0.93) * stone
    t.put(1, col=col, h=1.4 * stone + 0.3 * grain, rough=226 + 8 * grain)
    t.put(light, col=C(150, 146, 138))
    # Utility cuts filled with newer, darker asphalt, edges sealed with tar.
    for x, y, w, h in ((0.18, 0.62, 0.30, 0.22), (0.74, 0.20, 0.16, 0.34)):
        outer = t.rect(x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        inner = t.rect(x - w / 2 + 0.008, y - h / 2 + 0.008, x + w / 2 - 0.008, y + h / 2 - 0.008)
        t.put(outer, col=C(62, 62, 64) * (1 + 0.06 * grain)[..., None], hadd=0.8, rough=218)
        t.put(np.clip(outer - inner, 0, 1), col=C(40, 40, 42), rough=150, hadd=0.6)
    # Tar snakes: sealed cracks, a long one each way plus a few short branches.
    tar = np.maximum(wavy_line(t, 'v', 0.42, 0.035, 5, 0.013),
                     wavy_line(t, 'u', 0.88, 0.025, 4, 0.012))
    branches = level_lines(t, t.fbm(2, 3), 0.010) * np.clip(t.fbm(2, 2) * 2 - 1.2, 0, 1)
    tar = np.maximum(tar, branches)
    t.put(tar, col=C(38, 38, 40) * (1 + 0.05 * grain)[..., None], h=1.6, rough=150)


def asphalt_alley(t):
    t.ao, t.bump = 6.0, 1.4
    base = C(112, 110, 105)
    low = t.fbm(3, 3)
    grain = t.noise(t.s // 2)
    f1, f2, cid = worley(t.rng, t.s, 100)
    tone = t.rng.random(100 * 100)[cid]
    stone = np.clip((0.42 - f1) / 0.12, 0, 1)
    col = base * (1 + 0.10 * low + 0.07 * grain)[..., None] * (1 + 0.4 * stone * (tone - 0.5))[..., None]
    t.put(1, col=col, h=1.6 * stone + 0.4 * grain, rough=232 + 8 * grain)
    # Old patches, darker and smoother, with ragged edges.
    rag = 0.012 * t.fbm(8, 2)
    for x, y, w, h, c in ((0.30, 0.25, 0.36, 0.26, C(78, 77, 76)),
                          (0.78, 0.70, 0.22, 0.40, C(88, 87, 85)),
                          (0.12, 0.80, 0.18, 0.14, C(70, 70, 70))):
        d = np.minimum(w / 2 - np.abs(t.du(x)), h / 2 - np.abs(t.dv(y))) + rag
        t.put(t.edge(d), col=c * (1 + 0.06 * grain)[..., None], hadd=1.0, rough=222)
    # Alligator cracking over most of the lane, and two long cracks.
    a1, a2, _ = worley(t.rng, t.s, 9)
    region = np.clip(t.fbm(2, 2) * 1.5 + 0.6, 0, 1)
    cracks = t.edge(0.004 - (a2 - a1) / 9 / 2) * region
    cracks = np.maximum(cracks, wavy_line(t, 'v', 0.55, 0.05, 6, 0.006))
    cracks = np.maximum(cracks, wavy_line(t, 'u', 0.05, 0.04, 5, 0.005))
    t.put(cracks, col=C(36, 36, 34), h=-6, rough=245)
    # Moss in some of the cracks, and a tuft where they are widest.
    moss = gblur(cracks, 1.5 * t.s / 512) * np.clip(t.fbm(3, 2) + 0.2, 0, 1)
    t.put(np.clip(moss * 1.6, 0, 1), col=C(70, 92, 40) * (1 + 0.15 * grain)[..., None], hadd=1.0, rough=240)


def sidewalk(t):
    t.ao, t.bump = 5.0, 1.0
    n = 5                                   # 1.5 m panels, five across
    base = C(162, 160, 152)
    pu, pv = np.floor(t.u * n).astype(int), np.floor(t.v * n).astype(int)
    fu, fv = t.u * n - pu, t.v * n - pv
    tint = table(t.rng, (n, n), 0.045)[pv, pu]
    tilt = t.rng.normal(0, 4.0, (n, n, 2))  # mm across a panel: slabs heave a little
    dj = np.minimum(np.minimum(fu, 1 - fu), np.minimum(fv, 1 - fv)) / n
    low = t.fbm(3, 4)
    grain = t.noise(t.s // 2)
    broom = t.noise(6, t.s // 3)            # broom finish, lines across the slab
    col = base * (tint * (1 + 0.05 * low + 0.04 * grain + 0.02 * broom))[..., None]
    height = tilt[pv, pu, 0] * (fu - 0.5) + tilt[pv, pu, 1] * (fv - 0.5) + 0.3 * grain + 0.15 * broom
    t.put(1, col=col, h=height, rough=232 + 6 * grain)
    # Tooled border round each slab: smoother, a touch lighter.
    border = t.edge(0.014 - dj) * t.edge(dj - 0.004)
    t.put(border * 0.6, col=base * 1.05 * tint[..., None], rough=210)
    # Joints, with dirt along them.
    joint = t.edge(0.004 - dj)
    dirt = t.edge(0.010 - dj) * 0.5
    t.shade(dirt, 0.18)
    t.put(joint, col=C(70, 68, 62), h=-8, rough=245)
    # Stains and gum.
    stains = np.clip(t.fbm(4, 3) - 0.9, 0, 1)
    t.shade(stains, 0.35)
    for _ in range(14):
        x, y, r = t.rng.random(), t.rng.random(), t.rng.uniform(0.003, 0.006)
        t.put(t.disc(x, y, r), col=C(70, 68, 66), hadd=0.5, rough=170)
    # One cracked slab.
    crack = wavy_line(t, 'u', 0.5, 0.02, 7, 0.003) * t.rect(0.42, 0.40, 0.58, 0.60)
    t.put(crack, col=C(60, 58, 54), h=-5, rough=245)


def plaza(t):
    t.ao, t.bump = 5.0, 1.0
    n = 12          # herringbone: 2 x 1 pavers on a 12 x 12 grid, period 4
    gx, gy = t.u * n, t.v * n
    cx, cy = np.floor(gx).astype(int), np.floor(gy).astype(int)
    k = (cx - cy) % 4
    horiz = k < 2
    x0 = np.where(horiz, cx - (k == 1), cx)
    y0 = np.where(horiz, cy, cy - (k == 2))
    w = np.where(horiz, 2, 1)
    h = np.where(horiz, 1, 2)
    fx, fy = gx - x0, gy - y0
    d = np.minimum(np.minimum(fx, w - fx), np.minimum(fy, h - fy)) / n
    pid = (y0 % n) * n + (x0 % n) + horiz * n * n
    palette = np.array([C(150, 146, 138), C(170, 156, 134), C(126, 122, 116), C(160, 142, 118)])
    pick = t.rng.choice(4, size=2 * n * n, p=[0.40, 0.30, 0.15, 0.15])
    tint = table(t.rng, 2 * n * n, 0.04)
    grain = t.noise(t.s // 2)
    low = t.fbm(3, 3)
    col = palette[pick[pid]] * (tint[pid] * (1 + 0.04 * grain + 0.05 * low))[..., None]
    bevel = -3.0 * (1 - np.clip(d / 0.008, 0, 1)) ** 2
    face = t.edge(d - 0.0025)
    t.put(1, col=col, h=bevel + 0.3 * grain, rough=225 + 8 * grain)
    t.put(1 - face, col=C(140, 126, 104) * (1 + 0.1 * grain)[..., None], h=-6, rough=245)
    t.shade(np.clip(t.fbm(3, 3) - 1.0, 0, 1), 0.25)


def path(t):
    t.ao, t.bump = 6.0, 1.2
    base = C(176, 160, 134)
    low = t.fbm(2, 4)
    grain = t.noise(t.s // 2)
    f1, f2, cid = worley(t.rng, t.s, 150)
    tone = t.rng.random(150 * 150)[cid]
    fine = np.clip((0.45 - f1) / 0.15, 0, 1)
    col = base * (1 + 0.07 * low + 0.05 * grain + 0.18 * fine * (tone - 0.5))[..., None]
    t.put(1, col=col, h=0.8 * fine + 0.4 * low, rough=236 + 6 * grain)
    # Compacted, slightly damp tracks, and loose stone dust swept to the sides.
    damp = np.clip(t.fbm(2, 3) - 0.3, 0, 1)
    t.put(damp * 0.5, col=C(150, 134, 108), rough=220)
    # Bigger pebbles here and there.
    p1, _, pid = worley(t.rng, t.s, 26)
    keep = t.rng.random(26 * 26) < 0.30
    size = t.rng.uniform(0.18, 0.32, 26 * 26)
    shade = t.rng.choice(np.array([C(150, 146, 138), C(196, 186, 166), C(120, 112, 100), C(170, 150, 120)]), 26 * 26)
    r = size[pid]
    peb = t.edge((r - p1) / 26) * keep[pid]
    dome = np.sqrt(np.clip(1 - (p1 / r) ** 2, 0, 1))
    t.put(peb, col=shade[pid] * (1 + 0.06 * grain)[..., None], h=5 * dome, rough=215)
    # A few fallen leaves.
    for _ in range(7):
        x, y = t.rng.random(), t.rng.random()
        a = t.rng.uniform(0, np.pi)
        dx, dy = t.du(x), t.dv(y)
        lx, ly = dx * np.cos(a) + dy * np.sin(a), -dx * np.sin(a) + dy * np.cos(a)
        leaf = t.edge(0.009 - np.hypot(lx / 1.0, ly * 1.8))
        t.put(leaf, col=C(150, 92, 40) * t.rng.uniform(0.8, 1.1), hadd=0.8, rough=200)


def grass_field(t, base, stripes=True):
    """Mown lawn: blade speckle, clumps, clover, mowing stripes."""
    low = t.fbm(3, 3)
    grain = t.noise(t.s // 2)
    f1, f2, cid = worley(t.rng, t.s, 180)
    blade = t.rng.random(180 * 180)[cid]
    col = base * (1 + 0.10 * low + 0.06 * grain)[..., None]
    col = col * (1 + 0.30 * (blade - 0.5))[..., None]
    if stripes:
        st = np.clip(np.sin(2 * np.pi * (t.u * 2)) * 4, -1, 1)
        col = col * (1 + 0.10 * st)[..., None]
    hgt = 3 * (1 - f1) + 1.5 * grain
    return col, hgt, grain


def grass(t):
    t.ao, t.bump = 8.0, 1.0
    col, hgt, grain = grass_field(t, C(88, 114, 50))
    t.put(1, col=col, h=hgt, rough=238 + 6 * grain)
    clover = np.clip(t.fbm(5, 2) - 0.9, 0, 1)
    t.put(np.clip(clover * 1.5, 0, 1), col=C(74, 104, 48) * (1 + 0.08 * grain)[..., None], hadd=1.0)
    dry = np.clip(t.fbm(2, 3) - 1.3, 0, 1)
    t.put(np.clip(dry * 2, 0, 1), col=C(140, 136, 78) * (1 + 0.08 * grain)[..., None])
    for _ in range(10):
        x, y = t.rng.random(), t.rng.random()
        t.put(t.disc(x, y, 0.0035), col=C(214, 182, 40), hadd=2, rough=200)


def yard(t):
    t.ao, t.bump = 20.0, 1.0
    col, hgt, grain = grass_field(t, C(96, 118, 56), stripes=False)
    t.put(1, col=col, h=hgt, rough=238)
    # Worn lawn: yellow patches and bare earth.
    dry = np.clip(t.fbm(3, 3) + 0.2, 0, 1)
    t.put(np.clip(dry * 0.8, 0, 1), col=C(138, 132, 76) * (1 + 0.1 * grain)[..., None])
    bare = np.clip((t.fbm(3, 3) - 0.6) * 2.5, 0, 1)
    t.put(bare, col=C(112, 92, 68) * (1 + 0.12 * grain)[..., None], h=0, rough=245)
    # Wooden deck, boards along u, raised 15 cm.
    dx0, dy0, dx1, dy1 = 0.05, 0.06, 0.47, 0.44
    deck = t.rect(dx0, dy0, dx1, dy1)
    nb = 17
    bw = (dy1 - dy0) / nb
    bi = np.clip(np.floor((t.v - dy0) / bw).astype(int), 0, nb - 1)
    fb = (t.v - dy0) / bw - bi
    ends = t.rng.uniform(dx0 + 0.08, dx1 - 0.08, nb)
    seg = (t.u > ends[bi]).astype(int)
    btint = table(t.rng, (nb, 2), 0.08)[bi, seg]
    wood = t.noise(5, t.s // 4) * 0.6 + t.noise(10, t.s // 2) * 0.4
    dcol = C(150, 122, 96) * (btint * (1 + 0.10 * wood))[..., None]
    t.shadow(deck, 0.0, 0.0, 0.010, 0.30)
    t.put(deck, col=dcol, h=150 + 0.4 * wood, rough=190)
    gap = t.edge(0.0018 - np.minimum(fb, 1 - fb) * bw) * deck
    endgap = t.edge(0.0015 - np.abs(t.u - ends[bi])) * deck
    t.put(np.maximum(gap, endgap), col=C(40, 34, 28), h=120, rough=240)
    for jx in np.arange(dx0 + 0.04, dx1, 0.08):
        nails = t.edge(0.0016 - np.hypot(t.u - jx, (np.minimum(fb, 1 - fb) - 0.25) * bw)) * deck
        t.put(nails, col=C(60, 58, 56), rough=150)
    # Concrete patio slabs, 4 x 4, grass in the joints.
    px0, py0, step = 0.56, 0.54, 0.095
    for i in range(4):
        for j in range(4):
            if (i, j) in ((3, 0), (0, 3)):
                continue
            x, y = px0 + i * step, py0 + j * step
            m = t.rect(x, y, x + step - 0.008, y + step - 0.008)
            c = C(170, 166, 156) * t.rng.uniform(0.92, 1.05) * (1 + 0.05 * grain)[..., None]
            t.put(m, col=c, h=20 + 0.3 * grain, rough=228)
    t.shade(np.clip(t.fbm(4, 3) - 0.9, 0, 1) * t.rect(px0, py0, px0 + 4 * step, py0 + 4 * step), 0.25)


def roof_white(t):
    t.ao, t.bump = 30.0, 1.0
    base = C(216, 216, 210)
    low = t.fbm(2, 4)
    grain = t.noise(t.s // 2)
    wr = t.fbm(6, 3)
    col = base * (1 + 0.025 * grain)[..., None]
    t.put(1, col=col, h=0.8 * wr + 0.1 * grain, rough=150 + 6 * grain)
    # Three sheets across the tile; welded laps where they meet, and one end
    # lap in each sheet.
    for k in range(3):
        y = k / 3
        t.put(t.band(y, y + 0.022), col=base * 0.98, hadd=1.6, rough=140)
        t.put(t.band(y + 0.020, y + 0.025), col=base * 0.76, hadd=0.4)
        x = t.rng.uniform(0.05, 0.95)
        t.put(t.rect(x, y + 0.02, x + 0.02, y + 1 / 3), col=base * 0.98, hadd=1.6, rough=140)
        t.put(t.rect(x + 0.018, y + 0.02, x + 0.023, y + 1 / 3), col=base * 0.76, hadd=0.4)
    # Grime: grey blotches where water stood, darker towards their rims.
    pond = np.clip(t.fbm(2, 3) - 0.9, 0, 1)
    rim = np.clip(1 - np.abs(t.fbm(2, 3)) * 3, 0, 1) * 0.3
    t.shade(np.clip(pond * 0.9 + rim * 0.5 + 0.15 * low, 0, 1), 0.30)
    t.put(np.clip(pond, 0, 1) * 0.5, rough=120)
    # Skylight: aluminium curb, frosted acrylic dome.
    x0, y0, x1, y1 = 0.18, 0.52, 0.34, 0.68
    t.put(t.rect(x0 - 0.012, y0 - 0.012, x1 + 0.012, y1 + 0.012), col=base * 0.9, h=60, rough=150)
    t.put(t.rect(x0, y0, x1, y1), col=C(150, 152, 150), h=220, rough=110)
    dome = np.clip(1 - (np.abs(t.du((x0 + x1) / 2)) / ((x1 - x0) / 2 - 0.012)) ** 2, 0, 1) \
        * np.clip(1 - (np.abs(t.dv((y0 + y1) / 2)) / ((y1 - y0) / 2 - 0.012)) ** 2, 0, 1)
    inner = t.rect(x0 + 0.012, y0 + 0.012, x1 - 0.012, y1 - 0.012)
    t.put(inner, col=C(176, 186, 190), h=230 + 80 * np.sqrt(dome), rough=40)
    # Vent stack in a rubber boot.
    t.put(t.disc(0.74, 0.24, 0.030), col=C(46, 46, 46), h=40, rough=200)
    t.put(t.disc(0.74, 0.24, 0.017), col=C(140, 140, 138), h=300, rough=105)
    t.put(t.disc(0.74, 0.24, 0.010), col=C(60, 60, 60), h=260, rough=120)


def roof_tar(t):
    t.ao, t.bump = 6.0, 2.0
    n = 128
    f1, f2, cid = worley(t.rng, t.s, n)
    shades = np.array([C(146, 144, 138), C(128, 126, 122), C(162, 158, 150),
                       C(112, 110, 108), C(140, 130, 116), C(174, 170, 162)])
    pick = t.rng.choice(len(shades), n * n, p=[0.28, 0.25, 0.15, 0.12, 0.12, 0.08])
    tint = table(t.rng, n * n, 0.06)
    grain = t.noise(t.s // 2)
    low = t.fbm(3, 3)
    dome = np.sqrt(np.clip(1 - (f1 / 0.62) ** 2, 0, 1))
    crev = np.clip((f2 - f1) / 0.12, 0, 1)
    col = shades[pick[cid]] * (tint[cid] * (0.72 + 0.28 * crev) * (1 + 0.04 * grain + 0.08 * low))[..., None]
    t.put(1, col=col, h=5 * dome * crev, rough=240)
    # Where wind has scoured the gravel thin, the black tar shows.
    bare = np.clip((t.fbm(2, 4) - 0.55) * 2.5, 0, 1) * (1 - 0.5 * crev * dome)
    t.put(np.clip(bare, 0, 1), col=C(40, 40, 42) * (1 + 0.08 * grain)[..., None], h=-2, rough=175)


# ---------------------------------------------------------------- walls

FAMILIES = {
    'brick_red': dict(kind='brick', wall=C(158, 78, 54), mortar=C(172, 166, 154),
                      frame=C(224, 221, 210), door=C(30, 82, 116), stair=C(32, 62, 46),
                      cornice=C(50, 52, 50), name='red-orange brick'),
    'brick_brown': dict(kind='brick', wall=C(138, 80, 60), mortar=C(152, 144, 132),
                        frame=C(50, 96, 66), door=C(132, 32, 30), stair=C(44, 44, 42),
                        cornice=C(54, 88, 64), name='dark brown brick'),
    'brick_buff': dict(kind='brick', wall=C(182, 158, 116), mortar=C(190, 184, 170),
                       frame=C(116, 34, 38), door=C(36, 86, 56), stair=C(44, 44, 42),
                       cornice=C(116, 34, 38), name='pale buff brick'),
    'greystone': dict(kind='stone', wall=C(150, 148, 140), mortar=C(176, 173, 165),
                      frame=C(224, 221, 210), door=C(182, 128, 34), stair=C(32, 62, 46),
                      cornice=C(72, 78, 74), name='greystone (pierre grise) ashlar'),
}


def brick_wall(t, colour, mortar, courses=32, per_row=12, joint=0.0045):
    """Running bond. 32 courses so the coursing stays even at 64 px."""
    row = np.floor(t.v * courses).astype(int)
    fy = t.v * courses - row
    xb = t.u * per_row + 0.5 * (row % 2)
    col = np.floor(xb).astype(int)
    fx = xb - col
    col %= per_row
    row %= courses
    dx = np.minimum(fx, 1 - fx) / per_row
    dy = np.minimum(fy, 1 - fy) / courses
    face = t.edge(np.minimum(dx, dy) - joint / 2)
    tint = table(t.rng, (courses, per_row), 0.07)
    tint[t.rng.random((courses, per_row)) < 0.07] *= 0.72        # over-fired bricks
    hue = t.rng.normal(0, 0.05, (courses, per_row, 3))
    grain = t.noise(t.s // 2)
    low = t.fbm(3, 3)
    spots = np.clip((t.noise(t.s // 3) - 2.0) * 2, 0, 1)
    b = colour * (tint[row, col] * (1 + 0.05 * grain + 0.04 * low) * (1 - 0.35 * spots))[..., None]
    b = b * (1 + hue[row, col])
    m = mortar * (1 + 0.06 * grain + 0.04 * low)[..., None]
    bevel = -2.5 * (1 - np.clip((np.minimum(dx, dy) - joint / 2) / 0.006, 0, 1)) ** 2
    t.put(1, col=m, h=-7.0, rough=242)
    t.put(face, col=b, h=bevel + 0.5 * grain, rough=224 + 8 * grain)


def ashlar(t, colour, mortar, courses=8, blocks=(3, 5), joint=0.004, rock=0.0):
    """Coursed stone blocks of uneven length; rock > 0 gives a rock-faced
    (rusticated) surface that many mm deep."""
    rng = t.rng
    s = t.s
    dx = np.zeros((s, s))
    bid = np.zeros((s, s), int)
    row = np.minimum(np.floor(t.v * courses).astype(int), courses - 1)
    for r in range(courses):
        k = rng.integers(blocks[0], blocks[1] + 1)
        w = rng.uniform(0.6, 1.4, k)
        cum = np.cumsum(w / w.sum())
        cum[-1] = 1.0
        p = (t.x + rng.random()) % 1.0
        j = np.searchsorted(cum, p, side='right')
        j = np.minimum(j, k - 1)
        left = np.where(j > 0, cum[j - 1], 0.0)
        d1 = np.minimum(p - left, cum[j] - p)
        rows = row == r
        dx[rows] = np.broadcast_to(d1, (s, s))[rows]
        bid[rows] = np.broadcast_to(r * 16 + j, (s, s))[rows]
    fy = t.v * courses - row
    dy = np.minimum(fy, 1 - fy) / courses
    d = np.minimum(dx, dy)
    face = t.edge(d - joint / 2)
    tint = table(rng, courses * 16, 0.05)
    hue = rng.normal(0, 0.02, (courses * 16, 3))
    grain = t.noise(t.s // 2)
    low = t.fbm(3, 3)
    b = colour * (tint[bid] * (1 + 0.04 * grain + 0.05 * low))[..., None] * (1 + hue[bid])
    h = -2.0 * (1 - np.clip((d - joint / 2) / 0.005, 0, 1)) ** 2 + 0.6 * grain
    if rock:
        face_rock = t.fbm(10, 3) * rock * np.clip((d - 0.012) / 0.01, 0, 1)
        h = h + face_rock + rock
        b = b * (1 + 0.04 * np.clip(face_rock / rock, -1, 1))[..., None]
    t.put(1, col=mortar * (1 + 0.05 * grain)[..., None], h=-6.0, rough=240)
    t.put(face, col=b, h=h, rough=226 + 8 * grain)


def facade(t, f):
    if f['kind'] == 'brick':
        brick_wall(t, f['wall'], f['mortar'])
    else:
        ashlar(t, f['wall'], f['mortar'], courses=8)


def glass(t, m, lit, box, curtains=True):
    """Glazing, recessed: a dark room, or a lit one that glows 2700 K."""
    x0, y0, x1, y1 = box
    if lit:
        g = np.clip(1 - (t.v - y0) / (y1 - y0 + 1e-9), 0, 1)
        e = WARM * (0.70 + 0.30 * g)[..., None]
        t.put(m, col=ROOM_LIT, h=-70, rough=30, emis=e)
    else:
        e = 0.0
        t.put(m, col=GLASS * (1 + 0.15 * np.clip((t.v - y0) / (y1 - y0 + 1e-9), 0, 1))[..., None],
              h=-70, rough=30)
    if curtains and t.rng.random() < 0.7:
        w = (x1 - x0) * t.rng.uniform(0.18, 0.32)
        cm = t.rect(x0, y0, x0 + w, y1)
        if t.rng.random() < 0.6:
            cm = np.maximum(cm, t.rect(x1 - w, y0, x1, y1))
        folds = 1 + 0.12 * np.sin(t.u * 2 * np.pi * 90)
        c = CURTAIN * t.rng.uniform(0.6, 1.1)
        t.put(m * cm, col=c * folds[..., None] * (1.3 if lit else 0.55),
              emis=(e * 0.35 if lit else 0.0))


def window(t, box, frame, mullion=True, transom=0.27, sill=True, lit=None):
    """A sash window: painted frame, mullion and transom, recessed glass,
    stone sill."""
    x0, y0, x1, y1 = box
    if lit is None:
        lit = t.next_lit()
    t.put(t.rect(x0 - 0.004, y0 - 0.004, x1 + 0.004, y1 + 0.004), col=frame * 0.5, h=-50)
    t.put(t.rect(*box), col=frame, h=-35, rough=140)
    fw = 0.013
    pane = t.rect(x0 + fw, y0 + fw, x1 - fw, y1 - fw)
    bars = np.zeros_like(pane)
    ty = y0
    if transom:
        ty = y0 + (y1 - y0) * transom
        bars = np.maximum(bars, t.rect(x0, ty - 0.0055, x1, ty + 0.0055))
    if mullion:
        xc = (x0 + x1) / 2
        bars = np.maximum(bars, t.rect(xc - 0.0055, ty, xc + 0.0055, y1))
    glass(t, pane * (1 - bars), lit, box)
    if sill:
        t.put(t.rect(x0 - 0.014, y1, x1 + 0.014, y1 + 0.022), col=STONE, h=25, rough=215)


def lintel(t, x0, x1, y, height=0.045, keystone=False, col=STONE):
    """A stone lintel sitting on top of an opening whose top is at y."""
    grain = t.noise(t.s // 3)
    t.put(t.rect(x0 - 0.022, y - height, x1 + 0.022, y), col=col * (1 + 0.03 * grain)[..., None],
          h=22, rough=215)
    if keystone:
        xc = (x0 + x1) / 2
        t.put(t.rect(xc - 0.016, y - height - 0.010, xc + 0.016, y + 0.004), col=col * 1.04,
              h=34, rough=215)


def door(t, box, col, trim, glazed=0.0, transom=True):
    """A panelled wooden door in a painted casing, with a transom light, an
    optional glass light in the upper leaf, brass knob and stone step."""
    x0, y0, x1, y1 = box
    grain = t.noise(8, t.s // 3)
    t.put(t.rect(x0 - 0.012, y0 - 0.012, x1 + 0.012, y1), col=trim, h=-8, rough=140)
    yt = y0
    if transom:
        yt = y0 + 0.07
        glass(t, t.rect(x0 + 0.006, y0 + 0.006, x1 - 0.006, yt - 0.008), t.next_lit(),
              (x0, y0, x1, yt), curtains=False)
    leaf = t.rect(x0, yt, x1, y1)
    t.put(leaf, col=col * (1 + 0.03 * grain)[..., None], h=-55, rough=140)
    m = 0.014
    xc = (x0 + x1) / 2
    top = yt + m
    if glazed:
        gb = yt + (y1 - yt) * glazed
        glass(t, t.rect(x0 + m, yt + m, x1 - m, gb), False, (x0, yt, x1, gb), curtains=False)
        top = gb + m
    # Two columns of raised panels below.
    rows = 2 if (y1 - top) > 0.3 else 1
    ph = (y1 - m - top - m * (rows - 1)) / rows
    for r in range(rows):
        py = top + r * (ph + m)
        for px0, px1 in ((x0 + m, xc - m / 2), (xc + m / 2, x1 - m)):
            outer = t.rect(px0, py, px1, py + ph)
            inner = t.rect(px0 + 0.006, py + 0.006, px1 - 0.006, py + ph - 0.006)
            t.put(np.clip(outer - inner, 0, 1), col=col * 0.78, hadd=-3)
            t.put(inner, col=col * 1.06, hadd=4)
    t.put(t.disc(x1 - 0.018, yt + (y1 - yt) * 0.52, 0.0065), col=BRASS, hadd=20, rough=90)
    t.put(t.rect(x0 - 0.02, y1 - 0.014, x1 + 0.02, y1), col=STONE, h=15, rough=215)


# The outside staircase climbs from the sidewalk (bottom left of the ground
# storey) to the balcony (bottom of the middle storey). It is drawn once in a
# frame two storeys tall, v = 0..1 the middle storey and 1..2 the ground storey,
# so the two tiles meet without a seam.
STAIR_U0, STAIR_U1, STAIR_STEPS, RAIL_H = 0.03, 0.50, 15, 0.31


def _stair_r(u):
    s = np.clip((u - STAIR_U0) / (STAIR_U1 - STAIR_U0), 0, 1)
    return s ** 1.6, 1.6 * np.maximum(s, 1e-6) ** 0.6 / (STAIR_U1 - STAIR_U0)


def stair(t, col, offset):
    """offset 1 for the ground storey, 0 for the middle one."""
    vf = t.v + offset
    body = np.zeros((t.s, t.s))
    rail = np.zeros((t.s, t.s))
    for k in range(4):                        # four sub-columns for clean risers
        u = t.x + (k - 1.5) / 4 * t.px
        r, slope = _stair_r(u)
        top = np.minimum((np.floor(r * STAIR_STEPS) + 1) / STAIR_STEPS, 1.0)
        bot = r - 0.048 * np.sqrt(1 + slope ** 2)
        inside = ((u >= STAIR_U0) & (u <= STAIR_U1))[None, :]
        vt, vb = 2 - top, np.minimum(2 - bot, 2.0)
        body += t.edge(np.minimum(vf - vt[None, :], vb[None, :] - vf)) * inside / 4
        vh = 2 - r - RAIL_H
        th = 0.0090 * np.sqrt(1 + slope ** 2)
        inr = ((u >= STAIR_U0) & (u <= STAIR_U1 + 0.004))[None, :]
        rail += t.edge(th - np.abs(vf - vh[None, :])) * inr / 4
    m = np.maximum(body, rail)
    for ub in np.arange(STAIR_U0 + 0.016, STAIR_U1 - 0.008, 0.021):
        r, _ = _stair_r(ub)
        top = min((np.floor(r * STAIR_STEPS) + 1) / STAIR_STEPS, 1.0)
        b = t.edge(np.minimum(0.0034 - np.abs(t.u - ub),
                              np.minimum(vf - (2 - r - RAIL_H), (2 - top) - vf)))
        m = np.maximum(m, b)
    # Newel posts with ball caps at the foot and at the landing.
    for ux, vtop, vbot in ((STAIR_U0 + 0.006, 2 - RAIL_H - 0.02, 2.0),
                           (STAIR_U1, 1 - RAIL_H - 0.02, 1.0)):
        post = t.edge(np.minimum(0.0065 - np.abs(t.u - ux), np.minimum(vf - vtop, vbot - vf)))
        cap = t.edge(0.011 - np.hypot(t.u - ux, vf - vtop))
        m = np.maximum(m, np.maximum(post, cap))
    # The foot of the handrail curls into a scroll.
    sc = np.hypot(t.u - STAIR_U0 + 0.010, vf - (2 - RAIL_H + 0.018))
    m = np.maximum(m, t.edge(0.004 - np.abs(sc - 0.018)) * (t.u < STAIR_U0 + 0.004))
    t.shadow(m, 0.012, 0.010, 0.004, 0.45)
    t.put(m, col=col * (1 + 0.04 * t.noise(t.s // 4))[..., None], h=150, rough=115)


def balcony(t, col):
    """Wrought-iron railing across the lower third, on a painted wooden floor."""
    m = t.band(0.671, 0.689)                                  # handrail
    m = np.maximum(m, t.band(0.926, 0.938))                   # bottom rail
    m = np.maximum(m, t.band(0.744, 0.751))                   # rail under the rings
    bal = t.edge(0.0023 - np.abs(t.periodic(48))) * t.band(0.689, 0.926)
    rings = t.edge(0.0018 - np.abs(np.hypot(t.periodic(24), t.v - 0.716) - 0.0105))
    posts = t.edge(0.0055 - np.abs(t.periodic(4, 0.0))) * t.band(0.660, 0.95)
    m = np.maximum.reduce([m, bal, rings, posts])
    t.shadow(m, 0.010, 0.012, 0.004, 0.40)
    t.put(m, col=col, h=170, rough=110)
    t.put(t.band(0.948, 1.0), col=SLAB * (1 + 0.03 * t.noise(8, 64))[..., None], h=200, rough=150)


def cornice(t, col, bottom=0.15):
    """Painted sheet-metal cornice: crown, frieze with brackets and recessed
    panels, dentils, bed moulding."""
    t.shadow(t.band(0, bottom), 0.0, 0.012, 0.008, 0.40)
    grain = t.noise(16, 16)
    c = col * (1 + 0.02 * grain)[..., None]
    t.put(t.band(0, 0.034), col=c * 1.12, h=150, rough=130)
    t.put(t.band(0.034, 0.042), col=c * 0.80, h=135, rough=135)
    t.put(t.band(0.042, 0.118), col=c * 0.95, h=80, rough=140)
    d = np.abs(t.periodic(6))
    panel = t.edge(np.minimum(d - 0.034, 1 / 12 - 0.010 - d)) * t.band(0.055, 0.105)
    t.put(panel, col=c * 0.78, h=70)
    brk = np.maximum(t.edge(0.020 - d) * t.band(0.036, 0.070),
                     t.edge(0.013 - d) * t.band(0.070, 0.122))
    knob = t.edge(0.013 - np.hypot(t.periodic(6), t.v - 0.124))
    t.put(np.maximum(brk, knob), col=c * 1.08, h=170, rough=130)
    t.put(t.band(0.118, 0.134), col=c * 0.75, h=70)
    dent = t.edge(0.0065 - np.abs(t.periodic(48))) * t.band(0.118, 0.134)
    t.put(dent, col=c * 1.05, h=90)
    t.put(t.band(0.134, bottom), col=c * 1.08, h=80, rough=135)


def plex_ground(t, f):
    facade(t, f)
    t.put(t.band(0.955, 1.0), col=STONE * 0.85 * (1 + 0.04 * t.noise(t.s // 3))[..., None], h=20, rough=225)
    window(t, (0.10, 0.80, 0.28, 0.93), f['frame'], mullion=False, transom=0, sill=False, lit=False)
    lintel(t, 0.55, 0.67, 0.21)
    door(t, (0.55, 0.21, 0.67, 0.955), f['door'], f['frame'])
    lintel(t, 0.73, 0.95, 0.18)
    window(t, (0.73, 0.18, 0.95, 0.70), f['frame'])
    t.shade(np.clip(1 - t.v / 0.05, 0, 1), 0.25)            # under the balcony
    stair(t, f['stair'], 1.0)


def plex_upper(t, f):
    facade(t, f)
    lintel(t, 0.08, 0.21, 0.10)
    door(t, (0.08, 0.10, 0.21, 0.948), f['door'], f['frame'], glazed=0.38)
    for x0, x1 in ((0.33, 0.55), (0.70, 0.92)):
        lintel(t, x0, x1, 0.09)
        window(t, (x0, 0.09, x1, 0.635), f['frame'])
    balcony(t, f['stair'])
    stair(t, f['stair'], 0.0)


def plex_top(t, f):
    facade(t, f)
    for x0, x1 in ((0.13, 0.37), (0.63, 0.87)):
        lintel(t, x0, x1, 0.30, height=0.06, keystone=True)
        window(t, (x0, 0.30, x1, 0.86), f['frame'])
    cornice(t, f['cornice'])


def commercial_ground(t):
    brick_wall(t, C(150, 72, 52), C(168, 160, 148))
    t.put(t.band(0.955, 1.0), col=STONE * 0.85, h=20, rough=225)
    # Sign band, blank: a painted fascia between two mouldings.
    t.put(t.band(0.03, 0.19), col=C(30, 70, 84), h=40, rough=140)
    t.put(np.maximum(t.band(0.03, 0.045), t.band(0.177, 0.19)), col=C(214, 200, 160), h=55, rough=140)
    t.put(t.rect(0.30, 0.075, 0.82, 0.145), col=C(40, 88, 104), h=46)
    t.put(t.ring(0.18, 0.11, 0.040, 0.010), col=C(214, 200, 160), h=55)
    t.put(t.disc(0.18, 0.11, 0.030), col=C(176, 60, 46), h=50)
    # Striped awning with a scalloped edge.
    stripe = (np.floor(t.u * 12) % 2).astype(float)
    acol = C(160, 34, 36) * (1 - stripe)[..., None] + C(226, 218, 200) * stripe[..., None]
    scal = t.edge(0.0415 - np.hypot(t.periodic(12), t.v - 0.296)) * (t.v > 0.296)
    aw = np.maximum(t.band(0.19, 0.296), scal)
    t.shadow(aw, 0.0, 0.030, 0.012, 0.45)
    folds = 1 - 0.06 * (t.v - 0.19) / 0.11
    t.put(aw, col=acol * folds[..., None], h=250, rough=205)
    # Storefront: dark bronze frame, three lights under a transom, bulkhead.
    frame = C(46, 42, 38)
    x0, y0, x1, y1 = 0.04, 0.34, 0.62, 0.88
    t.put(t.rect(x0, y0, x1, 0.975), col=frame, h=-30, rough=120)
    lit = t.next_lit()
    ty = 0.42
    panes = t.rect(x0 + 0.010, y0 + 0.010, x1 - 0.010, ty - 0.005)
    w = (x1 - x0 - 0.020) / 3
    for i in range(3):
        a = x0 + 0.010 + i * w
        panes = np.maximum(panes, t.rect(a + (0.004 if i else 0), ty + 0.005,
                                         a + w - (0.004 if i < 2 else 0), y1 - 0.008))
    glass(t, panes, lit, (x0, y0, x1, y1), curtains=False)
    # Shelves and goods behind the glass.
    goods = t.rng.random(64)
    gi = np.floor(t.u * 64).astype(int) % 64
    gcol = np.array([C(180, 60, 50), C(60, 110, 160), C(210, 180, 70), C(230, 225, 210)])[
        (goods * 4).astype(int)[gi]]
    for sy in (0.60, 0.72):
        sh = t.band(sy - 0.05, sy) * panes * (goods[gi] > 0.3)
        t.put(sh * 0.8, col=gcol * (1.0 if lit else 0.35), emis=(WARM * 0.45 if lit else 0.0))
        t.put(t.band(sy, sy + 0.006) * panes, col=C(90, 80, 70) * (1.0 if lit else 0.4),
              emis=(WARM * 0.3 if lit else 0.0))
    t.put(t.rect(x0 + 0.02, 0.895, x1 - 0.02, 0.96), col=C(30, 70, 84), h=-20, rough=140)
    # Glazed door.
    dx0, dx1 = 0.69, 0.85
    t.put(t.rect(dx0 - 0.01, y0, dx1 + 0.01, 0.975), col=frame, h=-30, rough=120)
    glass(t, t.rect(dx0 + 0.012, y0 + 0.012, dx1 - 0.012, 0.86), lit, (dx0, y0, dx1, 0.86), curtains=False)
    t.put(t.rect(dx0 + 0.02, 0.62, dx1 - 0.02, 0.632), col=C(170, 170, 166), hadd=20, rough=90)
    t.put(t.rect(dx0 - 0.02, 0.962, dx1 + 0.02, 0.975), col=STONE, h=15)


def lancet(t, x0, x1, ys, y1, grow=0.0):
    """Signed distance inside a pointed (equilateral) arch window: jambs from
    x0 to x1, springing at ys, sill at y1; grow > 0 widens it all round."""
    w = x1 - x0
    d1 = np.hypot(t.u - x1, t.v - ys)
    d2 = np.hypot(t.u - x0, t.v - ys)
    arch = np.minimum(w + grow - d1, w + grow - d2)
    low = np.minimum(np.minimum(t.u - (x0 - grow), (x1 + grow) - t.u),
                     np.minimum((y1 + grow) - t.v, t.v - ys))
    return np.maximum(arch, low)


PALETTE = [C(36, 56, 150), C(160, 28, 40), C(214, 160, 46), C(40, 126, 64), C(96, 44, 140), C(180, 190, 200)]


def stained_glass(t, m, lit, sel, cell=0.034):
    """Diamond quarries in lead cames. sel: which palette entries, weights."""
    a = (t.u + t.v) / cell
    b = (t.u - t.v) / cell
    ia, ib = np.floor(a).astype(int) % 64, np.floor(b).astype(int) % 64
    idx = t.rng.choice(len(sel[0]), (64, 64), p=sel[1])
    cols = np.array([PALETTE[i] for i in sel[0]])
    c = cols[idx[ia, ib]] * table(t.rng, (64, 64), 0.12)[ia, ib][..., None]
    lead = t.edge(0.07 * cell - np.minimum(np.abs(a - np.rint(a)), np.abs(b - np.rint(b))) * cell)
    emis = 0.0
    if lit:
        emis = c / c.max(axis=-1, keepdims=True) * (0.35 + 0.65 * WARM) * 0.9
    t.put(m, col=c, h=-60, rough=35, emis=emis)
    t.put(m * lead, col=C(40, 40, 40), rough=120)


def church_stone(t):
    ashlar(t, C(140, 139, 132), C(166, 163, 156), courses=10, blocks=(3, 4), rock=4.0)
    t.ao = 40.0
    # A buttress on the seam, with a sloped set-off halfway up.
    b = t.rect(-0.05, 0, 0.05, 1)
    t.put(b, hadd=110)
    t.alb *= (1 + 0.07 * b)[..., None]
    t.shade(t.rect(0.044, 0, 0.056, 1), 0.30)          # its shadowed return
    t.put(t.rect(-0.05, 0.44, 0.05, 0.47), col=STONE * 0.95, hadd=20, rough=215)
    t.shade(t.rect(-0.05, 0.47, 0.05, 0.49), 0.30)


def church_wall(t):
    church_stone(t)
    x0, x1, top, y1 = 0.37, 0.63, 0.07, 0.92
    ys = top + (x1 - x0) * np.sqrt(3) / 2
    t.put(t.edge(lancet(t, x0, x1, ys, y1, 0.034)), col=STONE * (1 + 0.03 * t.noise(t.s // 3))[..., None],
          h=40, rough=215)
    t.put(t.edge(lancet(t, x0, x1, ys, y1, 0.010)), col=C(70, 70, 68), h=-90, rough=225)
    g = t.edge(lancet(t, x0, x1, ys, y1))
    lit = t.next_lit()
    stained_glass(t, g, lit, ([0, 1, 2, 3, 5], [0.42, 0.25, 0.15, 0.10, 0.08]))
    border = g * (1 - t.edge(lancet(t, x0, x1, ys, y1, -0.018)))
    gold = C(196, 150, 50)
    t.put(border, col=gold, emis=(gold / gold.max() * (0.35 + 0.65 * WARM) * 0.9 if lit else 0.0))
    t.put(border * t.edge(0.002 - np.abs(lancet(t, x0, x1, ys, y1, -0.018))), col=C(40, 40, 40))
    # Stone tracery: a mullion and a small quatrefoil in the head.
    mull = t.rect(0.494, ys + 0.03, 0.506, y1)
    foil = t.ring(0.5, ys - 0.025, 0.030, 0.008)
    t.put(np.maximum(mull, foil), col=STONE, h=10, rough=215)
    t.put(t.rect(x0 - 0.045, y1, x1 + 0.045, y1 + 0.028), col=STONE, h=35, rough=215)


def church_top(t):
    church_stone(t)
    # Battlements: merlons centred on the quarter points, deep crenels between.
    d = np.abs(t.periodic(4, 0.0))
    cren = t.edge(d - 0.052) * t.band(0.0, 0.105)
    t.put(t.edge(0.052 - d) * t.band(0.0, 0.022), col=STONE, h=40, rough=215)
    t.put(cren, col=C(56, 56, 54), h=-200, rough=235)
    t.put(t.edge(d - 0.052) * t.band(0.092, 0.110), col=STONE, h=30, rough=215)
    # String course and a corbel table of little round arches.
    t.put(t.band(0.165, 0.192), col=STONE * 1.02, h=60, rough=215)
    t.shade(t.band(0.192, 0.200), 0.35)
    arches = t.edge(0.022 - np.hypot(t.periodic(16), t.v - 0.208)) * (t.v > 0.208) * t.band(0.2, 0.235)
    t.put(t.band(0.192, 0.240), col=STONE * 0.92, h=30, rough=220)
    t.put(arches, col=C(84, 84, 80), h=5)
    corb = t.edge(0.006 - np.abs(t.periodic(16, 0.0))) * t.band(0.192, 0.250)
    t.put(corb, col=STONE, h=45)
    # Rose window under a hood mould.
    cx, cy, R = 0.5, 0.585, 0.165
    rr = np.hypot(t.u - cx, t.v - cy)
    th = np.arctan2(t.v - cy, t.u - cx)
    t.put(t.edge(0.008 - np.abs(rr - (R + 0.050))) * (t.v < cy + 0.02), col=STONE, h=50, rough=215)
    t.put(t.edge(R + 0.032 - rr), col=STONE * (1 + 0.03 * t.noise(t.s // 3))[..., None], h=40, rough=215)
    t.put(t.edge(R + 0.008 - rr), col=C(70, 70, 68), h=-90)
    g = t.edge(R - rr)
    sector = np.floor((th / (2 * np.pi)) * 12).astype(int) % 12
    inner = rr < 0.10
    cols = np.array([[PALETTE[1], PALETTE[0]], [PALETTE[2], PALETTE[0]]])
    c = cols[np.where(inner, 0, 1), sector % 2] * (1 + 0.15 * t.noise(t.s // 6))[..., None]
    lit = t.next_lit()
    emis = c / c.max(axis=-1, keepdims=True) * (0.35 + 0.65 * WARM) * 0.9 if lit else 0.0
    t.put(g, col=c, h=-60, rough=35, emis=emis)
    spoke = t.edge(0.003 - np.abs(np.sin(th * 6)) * rr / 6) * (rr > 0.03)
    trace = np.maximum.reduce([spoke,
                               t.edge(0.005 - np.abs(rr - 0.10)),
                               t.edge(0.032 - rr)])
    for k in range(12):
        a = (k + 0.5) / 12 * 2 * np.pi
        trace = np.maximum(trace, t.edge(0.0035 - np.abs(np.hypot(t.u - cx - 0.135 * np.cos(a),
                                                                   t.v - cy - 0.135 * np.sin(a)) - 0.024)))
    t.put(trace * g, col=STONE, h=10, rough=215)
    t.put(t.rect(cx - R - 0.03, cy + R + 0.03, cx + R + 0.03, cy + R + 0.052), col=STONE, h=35, rough=215)


# ---------------------------------------------------------------- the list

def _family(fn, fam):
    return lambda t: fn(t, FAMILIES[fam])


KEYS = [
    ('asphalt', 'lid', asphalt,
     'Montreal street asphalt: dark grey, fine aggregate, tar-sealed cracks and two utility-cut patches.'),
    ('asphalt_alley', 'lid', asphalt_alley,
     'Ruelle (back lane): older, lighter asphalt, patched, with alligator cracking and moss in the cracks.'),
    ('sidewalk', 'lid', sidewalk,
     'Poured concrete sidewalk, five 1.5 m slabs across, tooled joints, stains and gum.'),
    ('plaza', 'lid', plaza,
     'Grey and beige concrete pavers in herringbone (Prince-Arthur, Carre Saint-Louis).'),
    ('path', 'lid', path,
     'Park path of beige stone dust with fine gravel, a few pebbles and leaves (Parc La Fontaine).'),
    ('grass', 'lid', grass, 'Mown lawn with mowing stripes, clover and a few dandelions.'),
    ('yard', 'lid', yard,
     'Backyard: patchy lawn and bare earth, a raised wooden deck and concrete patio slabs.'),
    ('roof_white', 'lid', roof_white,
     'Flat roof of white TPO membrane: welded seams, grime, a skylight and a vent stack.'),
    ('roof_tar', 'lid', roof_tar,
     'Flat tar-and-gravel roof: grey ballast with tar showing where it is thin.'),
]
for fam, f in FAMILIES.items():
    KEYS += [
        (fam + '_ground', 'wall', _family(plex_ground, fam),
         f'Plex ground storey in {f["name"]}: wooden door, large window, the foot of the curving '
         'outside staircase.'),
        (fam + '_upper', 'wall', _family(plex_upper, fam),
         f'Plex middle storey in {f["name"]}: balcony door, two tall windows, wrought-iron balcony '
         'railing, the top of the staircase.'),
        (fam + '_top', 'wall', _family(plex_top, fam),
         f'Plex top storey in {f["name"]}: two windows with keystone lintels under a bracketed '
         'sheet-metal cornice.'),
    ]
KEYS += [
    ('commercial_ground', 'wall', commercial_ground,
     'Ground-floor shop: glass storefront with mullions, glazed door, blank sign band and striped awning.'),
    ('church_wall', 'wall', church_wall,
     'Grey stone church wall with a buttress and a tall lancet stained-glass window.'),
    ('church_top', 'wall', church_top,
     'Top of the church wall: rose window, corbel table and battlements.'),
]


# ---------------------------------------------------------------- output

def save(img, path):
    """uint8 arrays: 2-D saves as L, 3-D as RGB. Pillow writes no time stamps,
    so the same pixels give the same bytes."""
    Image.fromarray(img).save(path, optimize=False)


def contact_sheet(out_dir, done):
    """Every tile64 at 4x (what the game shows, enlarged) next to its albedo at
    128 px, with its name."""
    cols = 4
    cw, ch = 256 + 128 + 30, 256 + 34
    rows = (len(done) + cols - 1) // cols
    sheet = Image.new('RGB', (cols * cw + 10, rows * ch + 10), (36, 36, 38))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=15)
    except TypeError:
        font = ImageFont.load_default()
    for i, key in enumerate(done):
        x, y = 10 + (i % cols) * cw, 10 + (i // cols) * ch
        small = Image.open(os.path.join(out_dir, key, 'tile64.png')).resize((256, 256), Image.NEAREST)
        alb = Image.open(os.path.join(out_dir, key, 'albedo.png')).resize((128, 128), Image.BOX)
        sheet.paste(small, (x, y + 22))
        sheet.paste(alb, (x + 266, y + 22))
        draw.text((x, y + 2), key, fill=(230, 230, 225), font=font)
    sheet.save(os.path.join(out_dir, 'contact_sheet.png'), optimize=False)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('out_dir')
    ap.add_argument('--size', type=int, default=512, help='texture size, a multiple of 64 (default 512)')
    ap.add_argument('--only', help='comma-separated keys; skips the manifest and contact sheet')
    args = ap.parse_args()
    if args.size < 64 or args.size % 64:
        sys.exit('--size must be a multiple of 64')
    only = set(args.only.split(',')) if args.only else None
    if only and only - {k[0] for k in KEYS}:
        sys.exit('unknown keys: ' + ', '.join(sorted(only - {k[0] for k in KEYS})))
    os.makedirs(args.out_dir, exist_ok=True)
    start = time.time()
    manifest = []
    lit_windows = windows = 0
    for key, kind, fn, desc in KEYS:
        if only and key not in only:
            continue
        t = Tile(key, kind, args.size)
        fn(t)
        out = t.finish()
        d = os.path.join(args.out_dir, key)
        os.makedirs(d, exist_ok=True)
        stale = os.path.join(d, 'emissive.png')
        if 'emissive' not in out and os.path.exists(stale):
            os.remove(stale)
        for name, img in out.items():
            save(img, os.path.join(d, name + '.png'))
        lit = sum(zlib.crc32(f'{key}/window{i}'.encode()) % 3 == 0 for i in range(t.windows))
        windows += t.windows
        lit_windows += lit
        manifest.append({'key': key, 'kind': kind, 'description': desc, 'has_emissive': 'emissive' in out})
        print(f'{key:22s} {kind:4s}  lit {lit}/{t.windows}')
    if not only:
        with open(os.path.join(args.out_dir, 'manifest.json'), 'w', encoding='utf-8', newline='\n') as f:
            json.dump(manifest, f, indent=2)
            f.write('\n')
        contact_sheet(args.out_dir, [m['key'] for m in manifest])
    print(f'{len(manifest)} textures at {args.size} px, {lit_windows} of {windows} windows lit, '
          f'{time.time() - start:.1f} s')


if __name__ == '__main__':
    main()
