"""Writes plateau.sty: the Residential district's style with Plateau tiles in it.

    python make_plateau_style.py <GTA2 folder> <textures folder> <out folder>

The textures folder is what plateau_textures.py writes: one folder per surface
(asphalt, sidewalk, brick_red_ground, ...) with a 64x64 tile64.png. Each of
those becomes a tile of its own in a copy of ste.sty, which keeps everything
else the district has (cars, people, objects, fonts) exactly as it was.

Writes, into the out folder:

    plateau.sty           the style
    plateau_tiles.json    surface name -> tile number, for make_plateau_osm.py,
                          and surface name -> RTX Remix texture hash, for
                          make_plateau_mod.py

Where the tiles go. A GTA2 style keeps 992 tiles, each drawn with one of its
physical palettes through the PALX table, and those palettes are shared: no
tile has one to itself. ste.sty has 46 physical palettes that nothing points
at (SPARE_PALETTES), so every new tile gets one of those for its own 255
colours, and only its own PALX entry changes. The tiles replaced are ones
whose old palette only tiles use (no sprite) and that are not special (SPEC
lists water, electrified ground and so on), taken from SLOTS. The Plateau map
uses no other tile but the style's own water, which is left alone.

Colour 0 of a tile palette is see-through, so the new tiles use 1-255 only.

The Remix hash is the one the renderer gives the tile: xxh3-64 of its 64x64
A8R8G8B8 pixels, decoded here from the written file by pick_tiles.Style, the
same decode that reproduces the water mod's known hashes.

Needs numpy, Pillow and xxhash.
"""
import json
import os
import struct
import sys

import numpy as np
from PIL import Image

import pick_tiles

# Physical palettes no virtual palette in ste.sty refers to.
SPARE_PALETTES = list(range(274, 320))
# Tile numbers drawn only with tile-only palettes, away from the water tiles
# (608-619, 670) and every SPEC list. Checked by check_slots() on each run.
SLOTS = list(range(32, 64)) + list(range(96, 128))

PAGE = 256                 # a TILE page is 256x256 bytes, 4x4 tiles
TILES_PER_PAGE = 16
PAL_PAGE = 64 * 1024       # PPAL keeps 64 palettes to a 64K page, interleaved


def read_chunks(data):
    if data[:4] != b'GBST':
        raise ValueError('not a GTA2 style')
    chunks, pos = {}, 6
    while pos + 8 <= len(data):
        tag = data[pos:pos + 4].decode('ascii', 'replace')
        size = struct.unpack_from('<I', data, pos + 4)[0]
        chunks[tag] = (pos + 8, size)
        pos += 8 + size
    return chunks


def check_slots(data, chunks, slots, palettes):
    """Every slot must be a tile whose old palette no sprite shares, outside the
    SPEC lists, and every spare palette unreferenced."""
    off, size = chunks['PALB']
    palb = struct.unpack_from('<%dH' % (min(size, 16) // 2), data, off)
    off, size = chunks['PALX']
    palx = struct.unpack_from('<%dH' % (size // 2), data, off)
    used = set(palx[:sum(palb)])
    for p in palettes:
        if p in used:
            raise ValueError('physical palette %d is in use' % p)
    if max(palettes) >= chunks['PPAL'][1] // 1024:
        raise ValueError('physical palette %d is past the end of PPAL' % max(palettes))
    sprite_palettes = set(palx[palb[0]:sum(palb)])
    off, size = chunks['SPEC']
    spec = set(struct.unpack_from('<%dH' % (size // 2), data, off)) - {0}
    for t in slots:
        if t >= palb[0]:
            raise ValueError('tile %d is past the tiles' % t)
        if palx[t] in sprite_palettes:
            raise ValueError('tile %d shares its palette with a sprite' % t)
        if t in spec:
            raise ValueError('tile %d is a special tile' % t)


def quantize(img):
    """64x64 RGB -> (indices 1..255, 255 colours)."""
    q = img.convert('RGB').quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    idx = np.asarray(q, dtype=np.uint8)
    pal = np.asarray(q.getpalette()[:255 * 3], dtype=np.uint8).reshape(-1, 3)
    if len(pal) < 255:
        pal = np.vstack([pal, np.zeros((255 - len(pal), 3), np.uint8)])
    return idx + 1, pal


def build(game, textures, out, src=None, slots=SLOTS, palettes=SPARE_PALETTES, water=None):
    """src, slots, palettes and water are for the tests' made-up style."""
    src = src or os.path.join(game, 'data', 'ste.sty')
    data = bytearray(open(src, 'rb').read())
    chunks = read_chunks(bytes(data))
    with open(os.path.join(textures, 'manifest.json')) as f:
        manifest = json.load(f)
    keys = [m['key'] for m in manifest]
    if len(keys) > min(len(slots), len(palettes)):
        sys.exit('%d surfaces, room for %d' % (len(keys), min(len(slots), len(palettes))))
    slots, palettes = slots[:len(keys)], palettes[:len(keys)]
    check_slots(bytes(data), chunks, slots, palettes)

    tile_off = chunks['TILE'][0]
    ppal_off = chunks['PPAL'][0]
    palx_off = chunks['PALX'][0]
    for key, tile, pal_no in zip(keys, slots, palettes):
        img = Image.open(os.path.join(textures, key, 'tile64.png'))
        if img.size != (64, 64):
            sys.exit('%s/tile64.png is %dx%d, not 64x64' % (key, *img.size))
        idx, pal = quantize(img)
        page, slot = divmod(tile, TILES_PER_PAGE)
        base = tile_off + page * PAGE * PAGE + (slot // 4) * 64 * PAGE + (slot % 4) * 64
        for y in range(64):
            data[base + y * PAGE:base + y * PAGE + 64] = idx[y].tobytes()
        pbase = ppal_off + (pal_no // 64) * PAL_PAGE + (pal_no % 64) * 4
        for i in range(256):
            r, g, b = (0, 0, 0) if i == 0 else pal[i - 1]
            data[pbase + i * 256:pbase + i * 256 + 4] = bytes((int(b), int(g), int(r), 0))
        struct.pack_into('<H', data, palx_off + tile * 2, pal_no)

    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, 'plateau.sty')
    with open(path, 'wb') as f:
        f.write(data)

    import xxhash
    style = pick_tiles.Style(path)
    hashes = {}
    for key, tile in zip(keys, slots):
        rgba = style.tile(tile).astype(np.uint32)
        argb = (rgba[..., 3] << 24) | (rgba[..., 0] << 16) | (rgba[..., 1] << 8) | rgba[..., 2]
        hashes[key] = '%016X' % xxhash.xxh3_64_intdigest(argb.astype('<u4').tobytes())
    if len(set(hashes.values())) != len(hashes):
        sys.exit('two surfaces came out with the same pixels')
    result = dict(style='plateau.sty', tiles=dict(zip(keys, slots)), hashes=hashes,
                  water=water if water is not None else pick_water(style))
    with open(os.path.join(out, 'plateau_tiles.json'), 'w') as f:
        json.dump(result, f, indent=2)
    print('wrote %s: %d Plateau tiles in slots %d-%d, palettes %d-%d'
          % (path, len(keys), slots[0], slots[-1], palettes[0], palettes[-1]))
    return result


def pick_water(style):
    """The style's own water tile: the renderer's water material keys on it."""
    return 670 if 670 in style.water else min(style.water)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__.split('\n\n')[1])
    build(*sys.argv[1:])
