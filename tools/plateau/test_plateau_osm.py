"""Checks for the OpenStreetMap Plateau, its style and its Remix mod.

    python test_plateau_osm.py

No GTA2 files needed; needs osm/plateau_osm.json (in the repository) and
numpy, Pillow and xxhash.

Builds the map from the OSM extract with made-up tile numbers and runs every
check test_plateau.py runs on the hand-drawn map: ground in every cell, arrows
that stay on the road, one road network every car can get around, walls on
every open storey, no building on the kerb, and the renderer's own parser and
mesh builder when a C++ compiler is there. Then:

- the zones copied from the district's map leave out the train stations, and
  every small zone ends up on a street
- make_plateau_style.py puts each tile in its own slot with its own palette,
  touches nothing else in the style, and its hash is the renderer's
- make_plateau_mod.py writes DDS files a reader can walk, with every mip, and
  one material per tile hash
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import gmp  # noqa: E402
import make_plateau_map  # noqa: E402
import make_plateau_mod  # noqa: E402
import make_plateau_osm  # noqa: E402
import make_plateau_style  # noqa: E402
import osm_layout  # noqa: E402
import pick_tiles  # noqa: E402
import test_plateau  # noqa: E402


def fake_tiles(path, water=22):
    """Tile numbers 1-21 for the surfaces, inside test_plateau's made-up style."""
    tiles = {k: i + 1 for i, k in enumerate(make_plateau_osm.TILE_KEYS)}
    with open(path, 'w') as f:
        json.dump(dict(style='plateau.sty', tiles=tiles, water=water), f)


def fake_zone_map(path):
    """A map with only a ZONE chunk: a train station, a restart point in the
    middle of nowhere and the main zone."""
    zones = [(gmp.ZONE_NAVIGATION, 0, 0, 255, 255, 'main'),
             (5, 120, 120, 2, 2, 'trak00st'),
             (2, 0, 0, 1, 1, 'restart1')]
    gmp.write_gmp(path, [('ZONE', gmp.zone_chunk(zones))])


@unittest.skipUnless(os.path.exists(osm_layout.DEFAULT_OSM), 'no OSM extract')
class OsmPlateauTest(test_plateau.PlateauTest):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix='plateau_osm_')
        tiles = os.path.join(cls.dir, 'tiles.json')
        fake_tiles(tiles)
        zones = os.path.join(cls.dir, 'zones.gmp')
        fake_zone_map(zones)
        cls.plateau, cls.writer = make_plateau_osm.main(
            [cls.dir, '--tiles', tiles, '--zones-from', zones])
        cls.gmp_path = os.path.join(cls.dir, 'plateau.gmp')
        cls.columns, cls.blocks = gmp.read_dmap(cls.gmp_path)

    def test_chunks(self):
        chunks = gmp.read_chunks(self.gmp_path)
        self.assertEqual(set(chunks), {'DMAP', 'ZONE', 'LGHT'})
        self.assertGreater(len(chunks['LGHT']) // 16, 1000)

    def test_zones(self):
        zone = gmp.read_chunks(self.gmp_path)['ZONE']
        names, at = [], 0
        while at < len(zone):
            kind, x, y, w, h, n = zone[at:at + 6]
            name = zone[at + 6:at + 6 + n].decode()
            names.append(name)
            if name == 'restart1':
                cell = self.plateau.mat[self.plateau.at(x, y)]
                self.assertIn(cell, (make_plateau_map.ROAD, make_plateau_map.SIDEWALK))
            at += 6 + n
        self.assertEqual(names, ['main', 'restart1'])

    def test_real_streets(self):
        """A few streets, where the city has them and the right way round."""
        streets = {s['name']: s for s in self.plateau.streets}
        for name in ('Boulevard Saint-Laurent', 'Rue Saint-Denis', 'Avenue du Mont-Royal',
                     'Rue Rachel', 'Rue Sherbrooke', 'Avenue Papineau', 'Rue Cherrier'):
            self.assertIn(name, streets)
        laurent = streets['Boulevard Saint-Laurent']
        self.assertEqual((laurent['axis'], laurent['dir']), ('ns', 'n'))
        royal = streets['Avenue du Mont-Royal']
        rachel = [s for s in self.plateau.streets if s['name'] == 'Rue Rachel']
        self.assertTrue(all(royal['pos'] > r['pos'] for r in rachel), 'Mont-Royal is north of Rachel')
        # Parc La Fontaine's ponds.
        self.assertGreater(self.plateau.mat.count(make_plateau_map.WATER), 150)

    def test_storeys_and_families(self):
        p = self.plateau
        heights = [p.height[k] for k, m in enumerate(p.mat) if m == make_plateau_map.BUILDING]
        self.assertGreater(len(heights), 8000)
        self.assertLessEqual(max(heights), make_plateau_osm.MAX_STOREYS)
        families = {p.wall[k] for k, m in enumerate(p.mat) if m == make_plateau_map.BUILDING}
        self.assertTrue(set(make_plateau_osm.FAMILIES) <= families)
        self.assertIn('church', families)

    def test_pick_tiles_finds_the_placeholders(self):
        self.skipTest('the OSM map takes its tiles from make_plateau_style.py')


def write_style(path, pages=4):
    """test_plateau's made-up style, four pages long, with palettes 32-63 unused."""
    test_plateau.write_style(path)
    with open(path, 'rb') as f:
        data = bytearray(f.read())
    chunks = make_plateau_style.read_chunks(bytes(data))
    off, size = chunks['TILE']
    tiles = bytes(data[off:off + size]) * (pages // 2)
    palx = struct.pack('<%dH' % (pages * 16), *([i % 32 for i in range(pages * 16)]))
    out = bytearray(b'GBST' + struct.pack('<H', 700))
    for tag, body in (('TILE', tiles), ('PPAL', data[chunks['PPAL'][0]:chunks['PPAL'][0] + chunks['PPAL'][1]]),
                      ('PALX', palx), ('PALB', struct.pack('<H', pages * 16)),
                      ('SPEC', struct.pack('<4H', 0, 0, 6, 0))):
        out += tag.encode() + struct.pack('<I', len(body)) + bytes(body)
    with open(path, 'wb') as f:
        f.write(out)


def write_textures(folder, keys, size=16):
    rng = np.random.default_rng(7)
    manifest = []
    y, x = np.mgrid[0:size, 0:size]
    for i, key in enumerate(keys):
        d = os.path.join(folder, key)
        os.makedirs(d)
        # A texture-like picture: a gradient in a colour of its own, a little grain.
        base = rng.integers(60, 200, 3)
        ramp = (x + y)[..., None] * (60.0 / (2 * size)) - 30
        colour = np.clip(base + ramp + rng.normal(0, 4, (size, size, 3)), 0, 255).astype(np.uint8)
        Image.fromarray(colour).save(os.path.join(d, 'albedo.png'))
        Image.fromarray(np.full((size, size, 3), (128, 128, 255), np.uint8)).save(os.path.join(d, 'normal.png'))
        Image.fromarray(np.full((size, size), 200, np.uint8)).save(os.path.join(d, 'roughness.png'))
        Image.fromarray(colour).resize((64, 64)).save(os.path.join(d, 'tile64.png'))
        has_emissive = i == 0
        if has_emissive:
            Image.fromarray(colour // 2).save(os.path.join(d, 'emissive.png'))
        manifest.append(dict(key=key, kind='lid', description=key, has_emissive=has_emissive))
    with open(os.path.join(folder, 'manifest.json'), 'w') as f:
        json.dump(manifest, f)


def walk_dds(path):
    """Header fields and the byte count every mip of a block-compressed DDS
    should take; checks the file is exactly that long."""
    with open(path, 'rb') as f:
        data = f.read()
    assert data[:4] == b'DDS '
    height, width, linear, _, mipcount = struct.unpack_from('<5I', data, 12)
    fourcc = data[84:88]
    block = 16 if fourcc in (b'BC5U', b'ATI2', b'DXT5') else 8
    total, w, h = 0, width, height
    for _ in range(mipcount):
        total += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * block
        w, h = max(1, w // 2), max(1, h // 2)
    return dict(width=width, height=height, mips=mipcount, fourcc=fourcc,
                ok=len(data) == 128 + total)


class StyleAndModTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix='plateau_sty_')
        cls.src = os.path.join(cls.dir, 'ste.sty')
        write_style(cls.src)
        cls.keys = ['asphalt', 'brick_red_ground', 'roof_white']
        cls.textures = os.path.join(cls.dir, 'textures')
        write_textures(cls.textures, cls.keys)
        cls.result = make_plateau_style.build(None, cls.textures, cls.dir, src=cls.src,
                                              slots=list(range(40, 64)), palettes=list(range(40, 64)),
                                              water=5)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_tiles_land_in_their_slots(self):
        style = pick_tiles.Style(os.path.join(self.dir, 'plateau.sty'))
        for key, tile in self.result['tiles'].items():
            want = np.asarray(Image.open(os.path.join(self.textures, key, 'tile64.png')).convert('RGB'))
            got = style.tile(tile)[..., :3]
            self.assertLess(np.abs(got.astype(int) - want).mean(), 6, key)
            self.assertTrue((style.tile(tile)[..., 3] == 255).all(), '%s has see-through pixels' % key)

    def test_nothing_else_changes(self):
        old = pick_tiles.Style(self.src)
        new = pick_tiles.Style(os.path.join(self.dir, 'plateau.sty'))
        used = set(self.result['tiles'].values())
        for tile in range(old.count):
            if tile not in used:
                self.assertTrue((old.tile(tile) == new.tile(tile)).all(), 'tile %d changed' % tile)

    def test_hash_is_the_renderers(self):
        """Same decode as the renderer's: A8R8G8B8 of the style's pixels."""
        import xxhash
        style = pick_tiles.Style(os.path.join(self.dir, 'plateau.sty'))
        for key, tile in self.result['tiles'].items():
            rgba = style.tile(tile).astype(np.uint32)
            argb = (rgba[..., 3] << 24) | (rgba[..., 0] << 16) | (rgba[..., 1] << 8) | rgba[..., 2]
            self.assertEqual(self.result['hashes'][key],
                             '%016X' % xxhash.xxh3_64_intdigest(argb.astype('<u4').tobytes()))

    def test_mod(self):
        mod = os.path.join(self.dir, 'mod')
        make_plateau_mod.build(self.textures, os.path.join(self.dir, 'plateau_tiles.json'), mod)
        with open(os.path.join(mod, 'mod.usda')) as f:
            usda = f.read()
        for key in self.keys:
            self.assertIn('mat_%s' % self.result['hashes'][key], usda)
            for kind, fourcc in (('a', b'DXT1'), ('n', b'BC5U'), ('r', b'BC4U')):
                info = walk_dds(os.path.join(mod, 'SubUSDs', '%s.%s.rtex.dds' % (key, kind)))
                self.assertEqual(info['fourcc'], fourcc)
                self.assertEqual(info['mips'], 5)       # 16 px down to 1
                self.assertTrue(info['ok'], '%s.%s is the wrong length' % (key, kind))
        self.assertEqual(usda.count('enable_emission'), 1)

    def test_bc1_round_trip(self):
        y, x = np.mgrid[0:64, 0:64]
        img = np.stack([x * 4, y * 4, (x + y) * 2], -1).astype(np.float32)
        data = np.frombuffer(make_plateau_mod.bc1_blocks(img), np.uint8).reshape(16, 16, 8)

        def c565(v):
            return np.array([((v >> 11) & 31) * 255 // 31, ((v >> 5) & 63) * 255 // 63,
                             (v & 31) * 255 // 31], np.float32)
        out = np.zeros((64, 64, 3))
        for by in range(16):
            for bx in range(16):
                b = data[by, bx]
                p0, p1 = c565(int(b[0]) | int(b[1]) << 8), c565(int(b[2]) | int(b[3]) << 8)
                pal = [p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3]
                bits = int(b[4]) | int(b[5]) << 8 | int(b[6]) << 16 | int(b[7]) << 24
                for i in range(16):
                    out[by * 4 + i // 4, bx * 4 + i % 4] = pal[(bits >> (2 * i)) & 3]
        psnr = 10 * np.log10(255 ** 2 / ((out - img) ** 2).mean())
        self.assertGreater(psnr, 35)


if __name__ == '__main__':
    unittest.main(verbosity=2)
