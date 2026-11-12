"""Builds the Plateau-Mont-Royal map from OpenStreetMap, with Plateau tiles.

    python make_plateau_osm.py <output folder> --tiles plateau_tiles.json
                               [--osm osm/plateau_osm.json]
                               [--zones-from <GTA2 folder>/data/ste.gmp]

make_plateau_map.py draws the street table written by hand in
plateau_layout.py and fills every block with the same rows of plexes. This
draws the real neighbourhood instead (osm_layout.py): every street where it
is, its one-way direction, every building on its own footprint at its own
number of storeys, the parks, the ponds of Parc La Fontaine, the back lanes
and the park paths. The street lamps, traffic arrows and map writing are
make_plateau_map.py's.

Tiles. --tiles is what make_plateau_style.py writes: the tile number of each
Plateau surface in plateau.sty. A building's walls change with the storey: the
ground floor has the door and the foot of the outside staircase, the floors
above have the balconies, and the top one the cornice; a building on a
commercial street has a shop front on its ground floor. Each building keeps one
family of brick or stone, chosen from its OSM id, so the same house always
looks the same.

Zones. GTA2's Residential district script looks up zones by name (where you
come back after dying, bus stops, traffic areas). Without them the game
crashes as the level starts, so --zones-from copies them from the district's
own map. The train stations (trak*) are left out: they need railway under them,
and the game crashes looking for it. A zone that lands on no street is moved to
the nearest one, so nobody is sent back to life inside a wall.

Writes plateau.gmp, plateau.mmp, plateau_preview.png and plateau_report.txt.

Traffic. Every road cell can reach every other one (test_plateau_osm.py checks
it). Where the real one-way streets leave a corner of the map that cars cannot
leave, that street is drawn two-way; anything still cut off becomes a
pedestrian street.
"""
import argparse
import collections
import json
import math
import os
import struct
import sys

from PIL import Image, ImageDraw

import gmp
import make_plateau_map as base
import osm_layout
from make_plateau_map import (LOT, ROAD, SIDEWALK, PARK, WATER, PLAZA, ALLEY, YARD, BUILDING,
                              GROUND_LEVEL, MAX_LEVEL, EDGE_LO, EDGE_HI, MIN_BLOCKS, stable_hash)

PATH, PARKING = 9, 10
MATERIAL_NAMES = base.MATERIAL_NAMES + ['path', 'parking']

FAMILIES = ['brick_red', 'brick_brown', 'brick_buff', 'greystone']
# How often each family shows up on a residential street: the Plateau is mostly
# red brick, with greystone on the grander streets.
FAMILY_WEIGHTS = [45, 22, 13, 20]

LIDS = {ROAD: ('asphalt', gmp.ROAD), SIDEWALK: ('sidewalk', gmp.PAVEMENT),
        PARK: ('grass', gmp.FIELD), YARD: ('yard', gmp.FIELD), PLAZA: ('plaza', gmp.PAVEMENT),
        PATH: ('path', gmp.PAVEMENT), ALLEY: ('asphalt_alley', gmp.PAVEMENT),
        PARKING: ('asphalt_alley', gmp.PAVEMENT)}

TILE_KEYS = (['asphalt', 'asphalt_alley', 'sidewalk', 'plaza', 'path', 'grass', 'yard',
              'roof_white', 'roof_tar', 'commercial_ground', 'church_wall', 'church_top']
             + ['%s_%s' % (f, s) for f in FAMILIES for s in ('ground', 'upper', 'top')])

MAX_STOREYS = MAX_LEVEL - GROUND_LEVEL
# A street end this many cells or fewer from a crossing street runs into it.
SNAP = 6


class Grid(base.Grid):
    def __init__(self, west, east, south, north):
        self.west, self.east, self.south, self.north = west, east, south, north
        self.sx = (east - west) / (EDGE_HI - EDGE_LO)
        self.sy = (north - south) / (EDGE_HI - EDGE_LO)

    def x(self, metres):
        return EDGE_LO + (metres - self.west) / self.sx

    def y(self, metres):
        return EDGE_LO + (self.north - metres) / self.sy


def lane_arrows(street, i):
    """make_plateau_map.lane_arrows, plus a two-way street one lane wide,
    which carries both directions in its one lane."""
    if street['dir'] == 'two' and street['lanes'] == 1:
        return (gmp.UP | gmp.DOWN) if street['axis'] == 'ns' else (gmp.LEFT | gmp.RIGHT)
    return base.lane_arrows(street, i)


class OsmPlateau(base.Plateau):
    def __init__(self, osm, two_way=frozenset()):
        self.osm = osm
        self.grid = Grid(osm.west, osm.east, osm.south, osm.north)
        n = gmp.MAP_W * gmp.MAP_H
        self.mat = [LOT] * n
        self.arrows = [0] * n
        self.street_of = [-1] * n
        self.height = [0] * n
        self.wall = [None] * n          # family, or 'church'
        self.shop = [False] * n         # ground floor is a shop front
        self.roof = [None] * n
        self.labels = {}
        self.lights = []
        self.medians = []
        self.streets = self._streets(osm, two_way)
        self.by_name = {}
        for i, s in enumerate(self.streets):
            self.by_name.setdefault(s['name'], i)
        self._span_cache = {}

    @staticmethod
    def _streets(osm, two_way):
        ring = [
            dict(name='Ring road (west)', axis='ns', pos=osm.west, lanes=2, dir='two', kind='ring',
                 span=(osm.south, osm.north)),
            dict(name='Ring road (east)', axis='ns', pos=osm.east, lanes=2, dir='two', kind='ring',
                 span=(osm.south, osm.north)),
            dict(name='Ring road (south)', axis='ew', pos=osm.south, lanes=2, dir='two', kind='ring',
                 span=(osm.west, osm.east)),
            dict(name='Ring road (north)', axis='ew', pos=osm.north, lanes=2, dir='two', kind='ring',
                 span=(osm.west, osm.east)),
        ]
        out = []
        for i, s in enumerate(ring + [dict(p) for p in osm.streets]):
            s = dict(s, median=s.get('median', False), id=i)
            if i in two_way and s['dir'] != 'two':
                s['dir'] = 'two'
            out.append(s)
        return out

    # --- streets in cells ---------------------------------------------------

    def along_cells(self, street, lo_m, hi_m):
        """Metres along a street's axis to an inclusive cell range."""
        if street['axis'] == 'ns':
            a, b = self.grid.y(hi_m), self.grid.y(lo_m)
        else:
            a, b = self.grid.x(lo_m), self.grid.x(hi_m)
        return int(math.floor(a)), int(math.ceil(b)) - 1

    def lane_range(self, street):
        lanes, _, _ = self.cross_cells(street)
        real = [c for c in lanes if c is not None]
        return min(real), max(real)

    def spans(self, street):
        """The street's one span in cells, its ends run into the nearest
        crossing street within SNAP cells."""
        key = street['id']
        if key in self._span_cache:
            return self._span_cache[key]
        lo, hi = self.along_cells(street, *street['span'])
        if street['kind'] == 'ring':
            # Each side runs to the far lane of the two sides it meets, so the
            # corners are joined.
            across = [self.lane_range(s) for s in self.streets
                      if s['kind'] == 'ring' and s['axis'] != street['axis']]
            lo = min(r[0] for r in across)
            hi = max(r[1] for r in across)
        else:
            c0, c1 = self.lane_range(street)
            best_lo = best_hi = None
            for other in self.streets:
                if other['axis'] == street['axis']:
                    continue
                o_lo, o_hi = self.along_cells(other, *other['span'])
                if not (o_lo - 1 <= c0 and c1 <= o_hi + 1):
                    continue        # the other street does not reach this one
                r0, r1 = self.lane_range(other)
                if abs(r0 - lo) <= SNAP or abs(r1 - lo) <= SNAP or r0 <= lo <= r1:
                    d = min(abs(r0 - lo), abs(r1 - lo))
                    if best_lo is None or d < best_lo[0]:
                        best_lo = (d, r0)
                if abs(r0 - hi) <= SNAP or abs(r1 - hi) <= SNAP or r0 <= hi <= r1:
                    d = min(abs(r0 - hi), abs(r1 - hi))
                    if best_hi is None or d < best_hi[0]:
                        best_hi = (d, r1)
            if best_lo:
                lo = best_lo[1]
            if best_hi:
                hi = best_hi[1]
        lo, hi = max(0, lo), min(gmp.MAP_W - 1, hi)
        self._span_cache[key] = [(lo, hi)] if hi > lo else []
        return self._span_cache[key]

    # --- painting -----------------------------------------------------------

    def raster(self, polygons, supersample=2, min_cover=2):
        """Labels each cell with the index of the polygon covering its centre
        area (min_cover of supersample**2 samples); -1 where none does."""
        size = gmp.MAP_W * supersample
        img = Image.new('I', (size, size), 0)
        draw = ImageDraw.Draw(img)
        for i, ring in enumerate(polygons):
            pts = [(self.grid.x(x) * supersample, self.grid.y(y) * supersample) for x, y in ring]
            draw.polygon(pts, fill=i + 1)
        raw = img.tobytes()
        labels = [-1] * (gmp.MAP_W * gmp.MAP_H)
        import array
        px = array.array('i')
        px.frombytes(raw)
        for cy in range(gmp.MAP_H):
            for cx in range(gmp.MAP_W):
                votes = collections.Counter()
                for sy in range(supersample):
                    row = (cy * supersample + sy) * size + cx * supersample
                    for sx in range(supersample):
                        v = px[row + sx]
                        if v:
                            votes[v] += 1
                if votes:
                    v, n = votes.most_common(1)[0]
                    if n >= min_cover:
                        labels[cy * gmp.MAP_W + cx] = v - 1
        return labels

    def paint_lines(self, lines, material, over, width=1):
        for _, _, pts in lines:
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                a = (self.grid.x(x0), self.grid.y(y0))
                b = (self.grid.x(x1), self.grid.y(y1))
                steps = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) * 2) + 1
                for k in range(steps + 1):
                    t = k / steps
                    cx = int(a[0] + (b[0] - a[0]) * t)
                    cy = int(a[1] + (b[1] - a[1]) * t)
                    for dx in range(width):
                        if self.inside(cx + dx, cy) and self.mat[self.at(cx + dx, cy)] in over:
                            self.mat[self.at(cx + dx, cy)] = material

    def paint_areas(self):
        for material, items in ((PARK, self.osm.parks()), (PARKING, self.osm.parking()),
                                (PLAZA, self.osm.pedestrian_areas()), (WATER, self.osm.water())):
            labels = self.raster([ring for _, _, ring in items], min_cover=2 if material != WATER else 3)
            for k, v in enumerate(labels):
                if v >= 0 and self.mat[k] in (LOT, PARK, PARKING):
                    self.mat[k] = material
                    if material == PARK and items[v][1].get('name') and items[v][1].get('leisure') == 'park':
                        self.labels.setdefault(items[v][1]['name'], (k % gmp.MAP_W, k // gmp.MAP_W))

    def paint_roads(self):
        # make_plateau_map's, with this module's lane_arrows.
        for street in self.streets:
            lanes, _, _ = self.cross_cells(street)
            median = lanes[lanes.index(None) - 1] + 1 if street['median'] else None
            for lo, hi in self.spans(street):
                for along in range(lo, hi + 1):
                    for i, across in enumerate(c for c in lanes if c is not None):
                        x, y = self.cell(street, across, along)
                        if not self.inside(x, y):
                            continue
                        k = self.at(x, y)
                        if self.mat[k] != ROAD:
                            self.street_of[k] = street['id']
                        self.mat[k] = ROAD
                        self.arrows[k] |= lane_arrows(street, i)
                    if median is not None:
                        self.medians.append(self.cell(street, median, along))
        for x, y in self.medians:
            if self.inside(x, y) and self.mat[self.at(x, y)] != ROAD:
                self.mat[self.at(x, y)] = PARK
        # The last row at each end of a two-way street lets a car cross to the
        # other direction's lane, so a dead end is a turning place. At a
        # crossing these cells are inside the junction anyway.
        for street in self.streets:
            if street['dir'] != 'two':
                continue
            lanes = [c for c in self.cross_cells(street)[0] if c is not None]
            cross_bits = (gmp.LEFT | gmp.RIGHT) if street['axis'] == 'ns' else (gmp.UP | gmp.DOWN)
            for lo, hi in self.spans(street):
                for along in (lo, hi):
                    for across in lanes:
                        x, y = self.cell(street, across, along)
                        if self.inside(x, y):
                            self.arrows[self.at(x, y)] |= cross_bits
        # Where a street's pieces step sideways (a street off the grid), join
        # them: the cells between the two pieces, over the width of both.
        pieces = collections.defaultdict(list)
        for s in self.streets:
            if self.spans(s):
                pieces[(s['name'], s['axis'])].append(s)
        for (_, axis), group in pieces.items():
            group.sort(key=lambda s: self.spans(s)[0][0])
            for a, b in zip(group, group[1:]):
                (alo, ahi), (blo, bhi) = self.spans(a)[0], self.spans(b)[0]
                lo, hi = max(alo, blo), min(ahi, bhi)
                if hi < lo:
                    lo = hi = (ahi + blo) // 2
                    if blo - ahi > 3:
                        continue
                ca, cb = self.lane_range(a), self.lane_range(b)
                c0, c1 = min(ca[0], cb[0]), max(ca[1], cb[1])
                along_bits = lane_arrows(a, 0) | lane_arrows(a, max(0, a['lanes'] - 1))
                cross_bits = (gmp.LEFT | gmp.RIGHT) if axis == 'ns' else (gmp.UP | gmp.DOWN)
                for along in range(lo - 1, hi + 2):
                    for across in range(c0, c1 + 1):
                        x, y = self.cell(a, across, along)
                        if self.inside(x, y):
                            k = self.at(x, y)
                            if self.mat[k] != ROAD:
                                self.street_of[k] = a['id']
                            self.mat[k] = ROAD
                            self.arrows[k] |= along_bits | cross_bits

    def paint_kerbs(self):
        """A sidewalk along every road, wherever a street happens to end."""
        for y in range(gmp.MAP_H):
            for x in range(gmp.MAP_W):
                k = self.at(x, y)
                if self.mat[k] != ROAD:
                    continue
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = x + dx, y + dy
                        if self.inside(nx, ny):
                            n = self.at(nx, ny)
                            if self.mat[n] in (LOT, PARK, PARKING, YARD):
                                self.mat[n] = SIDEWALK
                                if self.street_of[n] < 0:
                                    self.street_of[n] = self.street_of[k]

    def stranded(self):
        """Road cells outside the biggest set that can all reach each other."""
        roads = [k for k, m in enumerate(self.mat) if m == ROAD]
        succ = {k: [] for k in roads}
        pred = {k: [] for k in roads}
        for k in roads:
            x, y = k % gmp.MAP_W, k // gmp.MAP_W
            for bit, (dx, dy) in gmp.ARROW_STEP.items():
                if self.arrows[k] & bit and self.inside(x + dx, y + dy):
                    n = self.at(x + dx, y + dy)
                    if n in succ:
                        succ[k].append(n)
                        pred[n].append(k)
        # Kosaraju, without recursion.
        order, seen = [], set()
        for start in roads:
            if start in seen:
                continue
            stack = [(start, 0)]
            seen.add(start)
            while stack:
                node, i = stack.pop()
                if i < len(succ[node]):
                    stack.append((node, i + 1))
                    n = succ[node][i]
                    if n not in seen:
                        seen.add(n)
                        stack.append((n, 0))
                else:
                    order.append(node)
        comp, best = {}, None
        for start in reversed(order):
            if start in comp:
                continue
            members = [start]
            comp[start] = start
            stack = [start]
            while stack:
                node = stack.pop()
                for n in pred[node]:
                    if n not in comp:
                        comp[n] = start
                        members.append(n)
                        stack.append(n)
            if best is None or len(members) > len(best):
                best = members
        keep = set(best or [])
        return [k for k in roads if k not in keep]

    def paint_buildings(self):
        items = self.osm.buildings()
        labels = self.raster([ring for _, _, ring, _ in items])
        commercial = set()
        for k, m in enumerate(self.mat):
            if m == ROAD and self.street_of[k] >= 0 and \
                    self.streets[self.street_of[k]]['kind'] in ('commercial', 'boulevard'):
                commercial.add(k)
        for k, v in enumerate(labels):
            if v < 0 or self.mat[k] not in (LOT, PARK, PARKING, YARD):
                continue
            oid, tags, _, storeys = items[v]
            self.mat[k] = BUILDING
            self.height[k] = max(1, min(MAX_STOREYS, storeys))
            h = stable_hash('osm', oid)
            church = tags.get('building') in ('church', 'cathedral', 'chapel') or \
                tags.get('amenity') == 'place_of_worship'
            if church:
                self.wall[k] = 'church'
            else:
                pick = h % sum(FAMILY_WEIGHTS)
                for fam, w in zip(FAMILIES, FAMILY_WEIGHTS):
                    if pick < w:
                        self.wall[k] = fam
                        break
                    pick -= w
            self.roof[k] = 'roof_tar' if (h >> 8) % 5 < 2 else 'roof_white'
            x, y = k % gmp.MAP_W, k // gmp.MAP_W
            near = any(self.at(x + dx, y + dy) in commercial
                       for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2)
                       if self.inside(x + dx, y + dy))
            self.shop[k] = not church and (near or 'shop' in tags)
            if tags.get('name') and (church or tags.get('building') in ('school', 'public', 'civic')):
                self.labels.setdefault(tags['name'], (x, y))

    def place_lights(self):
        """Lamps along the sidewalks, as make_plateau_map does, and along the
        park paths."""
        def lamp(x, y, intensity=170, radius=3.5):
            self.lights.append(dict(rgb=base.LAMP_RGB, x=x + 0.5, y=y + 0.5,
                                    z=GROUND_LEVEL + 1 + 1.3, radius=radius, intensity=intensity))
        for street in self.streets:
            if street['kind'] == 'ring':
                continue
            _, west, east = self.cross_cells(street)
            step = 4 if street['kind'] in ('commercial', 'boulevard') else 6
            for lo, hi in self.spans(street):
                for along in range(lo, hi + 1, step):
                    for j, across in enumerate((west, east)):
                        if street['kind'] == 'residential' and (along // step + j) % 2:
                            continue
                        x, y = self.cell(street, across, along)
                        if self.inside(x, y) and self.mat[self.at(x, y)] == SIDEWALK:
                            lamp(x, y)
        for y in range(0, gmp.MAP_H, 5):
            for x in range(0, gmp.MAP_W, 5):
                if self.mat[self.at(x, y)] == PATH:
                    lamp(x, y, intensity=140, radius=3.0)

    def build(self):
        self.paint_border()
        self.paint_areas()
        self.paint_roads()
        self.paint_kerbs()
        self.paint_lines(self.osm.alleys(), ALLEY, (LOT, PARKING, YARD))
        self.paint_lines(self.osm.paths(), PATH, (PARK,))
        self.prune_arrows()
        self.paint_buildings()
        for k, m in enumerate(self.mat):
            if m == LOT:
                self.mat[k] = YARD
        self.place_lights()

    # --- output -------------------------------------------------------------

    def wall_tile(self, palette, k, level, top):
        fam = self.wall[k]
        storey = level - GROUND_LEVEL          # 1 = ground floor
        if fam == 'church':
            return palette['church_top' if level == top and storey > 1 else 'church_wall']
        if storey == 1:
            return palette['commercial_ground' if self.shop[k] else fam + '_ground']
        if level == top:
            return palette[fam + '_top']
        return palette[fam + '_upper']

    @staticmethod
    def lid_face(tile, x, y):
        """A lid turned and mirrored by the cell, so the patches in the asphalt
        and the vents on the roofs do not line up block after block. The
        textures carry no direction, so any turn is right."""
        h = stable_hash('lid', x, y)
        return gmp.face(tile, rotation=h & 3, flip=bool(h & 4))

    def to_map(self, palette):
        writer = gmp.MapWriter()
        sides = ((-1, 0), (1, 0), (0, -1), (0, 1))
        for y in range(gmp.MAP_H):
            for x in range(gmp.MAP_W):
                k = self.at(x, y)
                m = self.mat[k]
                if m == WATER:
                    lid = gmp.face(palette['water'])
                    writer.set_column(x, y, 0, [gmp.block(lid=lid, ground=gmp.AIR)])
                    continue
                faces = []
                for dx, dy in sides:
                    low = self.top_level(x + dx, y + dy) == 0
                    faces.append(gmp.face(palette['plaza']) if low else 0)
                if m == BUILDING:
                    ground = gmp.block(*faces, ground=gmp.PAVEMENT)
                else:
                    key, ground_type = LIDS[m]
                    ground = gmp.block(*faces, lid=self.lid_face(palette[key], x, y),
                                       arrows=self.arrows[k], ground=ground_type)
                records = [ground]
                if m == BUILDING:
                    top = self.top_level(x, y)
                    for level in range(GROUND_LEVEL + 1, top + 1):
                        tile = self.wall_tile(palette, k, level, top)
                        walls = []
                        for dx, dy in sides:
                            exposed = self.top_level(x + dx, y + dy) < level
                            walls.append(gmp.face(tile, wall=True) if exposed else 0)
                        lid = self.lid_face(palette[self.roof[k]], x, y) if level == top else 0
                        records.append(gmp.block(*walls, lid=lid, ground=gmp.PAVEMENT))
                writer.set_column(x, y, GROUND_LEVEL, records)
        return writer

    def spawn(self):
        """Saint-Denis at Mont-Royal, the north-east corner."""
        def piece(name, near_m):
            options = [s for s in self.streets if s['name'] == name and self.spans(s)]
            return min(options, key=lambda s: 0 if s['span'][0] <= near_m <= s['span'][1]
                       else min(abs(s['span'][0] - near_m), abs(s['span'][1] - near_m)))
        royal = piece('Avenue du Mont-Royal', 950)
        denis = piece('Rue Saint-Denis', royal['pos'])
        _, _, x = self.cross_cells(denis)
        _, y, _ = self.cross_cells(royal)
        return x, y


def build_plateau(osm):
    """Builds, then makes every street that strands cars two-way and builds
    again, until traffic can get everywhere; leftovers become pedestrian."""
    two_way = set()
    for _ in range(6):
        plateau = OsmPlateau(osm, frozenset(two_way))
        plateau.build()
        stranded = plateau.stranded()
        if not stranded:
            return plateau
        more = {plateau.street_of[k] for k in stranded if plateau.street_of[k] >= 0} - two_way
        more = {i for i in more if plateau.streets[i]['dir'] != 'two'}
        if not more:
            break
        two_way |= more
    for _ in range(20):
        stranded = plateau.stranded()
        if not stranded:
            break
        for k in stranded:
            plateau.mat[k] = PLAZA
            plateau.arrows[k] = 0
        plateau.prune_arrows()
    return plateau


def copy_zones(path, plateau):
    """The district's own zones, minus the train stations, moved onto a street
    where they cover none."""
    raw = gmp.read_chunks(path).get('ZONE')
    if raw is None:
        sys.exit('%s has no ZONE chunk' % path)
    streets = [k for k, m in enumerate(plateau.mat) if m in (ROAD, SIDEWALK)]
    out, kept, moved, dropped = bytearray(), 0, 0, 0
    p = 0
    while p < len(raw):
        kind, x, y, w, h, n = struct.unpack_from('<6B', raw, p)
        name = raw[p + 6:p + 6 + n]
        p += 6 + n
        if name.rstrip(b'\0').startswith(b'trak'):
            dropped += 1
            continue
        if w * h < 2000 and not any(plateau.mat[plateau.at(cx, cy)] in (ROAD, SIDEWALK)
                                    for cx in range(x, min(256, x + w)) for cy in range(y, min(256, y + h))):
            cx, cy = x + w // 2, y + h // 2
            k = min(streets, key=lambda k: (k % 256 - cx) ** 2 + (k // 256 - cy) ** 2)
            x = max(0, min(255 - w, k % 256 - w // 2))
            y = max(0, min(255 - h, k // 256 - h // 2))
            moved += 1
        out += struct.pack('<6B', kind, x, y, w, h, n) + name
        kept += 1
    return bytes(out), kept, moved, dropped


def ascii_name(name):
    """The preview's built-in font has no accents."""
    import unicodedata
    return unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii')


def write_preview(path, plateau, scale=4):
    colours = {
        ROAD: (52, 54, 58), SIDEWALK: (170, 166, 158), PARK: (88, 140, 70), WATER: (52, 104, 160),
        PLAZA: (196, 182, 150), ALLEY: (120, 112, 104), YARD: (112, 140, 86), PATH: (210, 196, 160),
        PARKING: (96, 96, 100), LOT: (90, 90, 90),
    }
    walls = {'brick_red': (164, 76, 54), 'brick_brown': (120, 70, 52), 'brick_buff': (196, 170, 120),
             'greystone': (156, 152, 142), 'church': (130, 128, 150)}
    img = Image.new('RGB', (gmp.MAP_W * scale, gmp.MAP_H * scale))
    draw = ImageDraw.Draw(img)
    for y in range(gmp.MAP_H):
        for x in range(gmp.MAP_W):
            k = plateau.at(x, y)
            m = plateau.mat[k]
            if m == BUILDING:
                b = walls[plateau.wall[k]]
                shade = 0.6 + 0.08 * plateau.height[k]
                c = tuple(min(255, int(v * shade)) for v in b)
            else:
                c = colours[m]
            draw.rectangle([x * scale, y * scale, x * scale + scale - 1, y * scale + scale - 1], c)
    for light in plateau.lights:
        cx, cy = light['x'] * scale, light['y'] * scale
        draw.point((cx, cy), (255, 200, 120))
    named = {}
    for s in plateau.streets:
        if s['kind'] == 'ring' or not plateau.spans(s):
            continue
        lo, hi = plateau.spans(s)[0]
        if s['name'] not in named or hi - lo > named[s['name']][1]:
            named[s['name']] = (s, hi - lo)
    for full, (s, _) in named.items():
        name = ascii_name(full)
        lo, hi = plateau.spans(s)[0]
        c0, _ = plateau.lane_range(s)
        mid = lo + (hi - lo) * (1 + stable_hash(name) % 6) // 8
        if s['axis'] == 'ns':
            text = Image.new('RGBA', (len(name) * 6 + 4, 11), (0, 0, 0, 170))
            ImageDraw.Draw(text).text((2, 0), name, (255, 255, 255))
            text = text.rotate(90, expand=True)
            img.paste(text, (int(c0 * scale), int(mid * scale)), text)
        else:
            draw.rectangle([mid * scale, c0 * scale - 1, mid * scale + 4 + len(name) * 6, c0 * scale + 10],
                           (0, 0, 0))
            draw.text((mid * scale + 2, c0 * scale - 1), name, (255, 255, 255))
    for name, (cx, cy) in plateau.labels.items():
        draw.text((cx * scale + 4, cy * scale - 4), ascii_name(name), (255, 240, 200))
    sx, sy = plateau.spawn()
    draw.rectangle([sx * scale - 3, sy * scale - 3, sx * scale + scale + 2, sy * scale + scale + 2],
                   outline=(255, 0, 255), width=2)
    draw.text((6, gmp.MAP_H * scale - 14), 'Map data (c) OpenStreetMap contributors, ODbL', (255, 255, 255))
    img.save(path)


def load_tiles(path):
    with open(path) as f:
        data = json.load(f)
    tiles = dict(data['tiles'])
    tiles['water'] = data.get('water', 670)
    missing = [k for k in TILE_KEYS if k not in tiles]
    if missing:
        sys.exit('%s has no tile for: %s' % (path, ', '.join(missing)))
    return tiles, data.get('style', 'plateau.sty')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('out')
    ap.add_argument('--tiles', required=True, help='plateau_tiles.json from make_plateau_style.py')
    ap.add_argument('--osm', default=osm_layout.DEFAULT_OSM)
    ap.add_argument('--zones-from', help="the Residential district's ste.gmp, for its zones")
    ap.add_argument('--no-lights', action='store_true')
    args = ap.parse_args(argv)

    palette, style = load_tiles(args.tiles)
    osm = osm_layout.OsmPlateau(args.osm)
    plateau = build_plateau(osm)
    writer = plateau.to_map(palette)
    os.makedirs(args.out, exist_ok=True)

    chunks = [('DMAP', writer.dmap(MIN_BLOCKS))]
    zone_note = 'none'
    if args.zones_from:
        raw, kept, moved, dropped = copy_zones(args.zones_from, plateau)
        chunks.append(('ZONE', raw))
        zone_note = '%d from %s (%d moved onto a street, %d train zones left out)' % (
            kept, os.path.basename(args.zones_from), moved, dropped)
    if not args.no_lights:
        chunks.append(('LGHT', gmp.light_chunk(plateau.lights)))
    gmp.write_gmp(os.path.join(args.out, 'plateau.gmp'), chunks)
    base.write_mmp(os.path.join(args.out, 'plateau.mmp'), style)
    write_preview(os.path.join(args.out, 'plateau_preview.png'), plateau)

    counts = collections.Counter(MATERIAL_NAMES[m] for m in plateau.mat)
    two_way = [s['name'] for s in plateau.streets
               if s['dir'] == 'two' and s['kind'] != 'ring'
               and any(p['name'] == s['name'] and p['dir'] != 'two' for p in osm.streets)]
    with open(os.path.join(args.out, 'plateau_report.txt'), 'w', encoding='utf-8') as f:
        f.write('Plateau-Mont-Royal for GTA2, from OpenStreetMap\n')
        f.write('Map data (c) OpenStreetMap contributors, ODbL\n\n')
        f.write('style: %s\n' % style)
        f.write('grid bearing: %.1f deg\n' % osm.bearing)
        f.write('scale: %.1f m per block east-west, %.1f m north-south\n' % (plateau.grid.sx, plateau.grid.sy))
        f.write('blocks: %d distinct\n' % len(writer.blocks))
        f.write('lights: %d\n' % (0 if args.no_lights else len(plateau.lights)))
        f.write('zones: %s\n' % zone_note)
        f.write('streets drawn two-way so traffic can leave: %s\n' % (', '.join(sorted(set(two_way))) or 'none'))
        f.write('player start: cell %d, %d (Saint-Denis at Mont-Royal)\n' % plateau.spawn())
        f.write('\ncells by surface:\n')
        for name in MATERIAL_NAMES:
            f.write('  %-9s %6d\n' % (name, counts.get(name, 0)))
    print('wrote %s: %d blocks, %d lights; zones: %s'
          % (os.path.join(args.out, 'plateau.gmp'), len(writer.blocks), len(plateau.lights), zone_note))
    return plateau, writer


if __name__ == '__main__':
    main()
