"""The Plateau-Mont-Royal from OpenStreetMap, in the generator's terms.

    python osm_layout.py [osm/plateau_osm.json]      prints what it found

plateau_layout.py holds a street table written by hand. This module builds the
same kind of table from real data instead: the street centre lines, one-way
directions and lane counts, the building footprints and their storeys, the
parks, ponds, back lanes (ruelles) and park paths, all from an OpenStreetMap
extract. osm/query.overpassql is the query that made osm/plateau_osm.json; run
it again at https://overpass-api.de to refresh the data.

Map data (c) OpenStreetMap contributors, available under the Open Database
License (ODbL): https://www.openstreetmap.org/copyright

Coordinates. Everything is turned into metres on Montreal's street grid, the
same frame plateau_layout.py uses: x runs "Montreal east" and y "Montreal
north", from the corner of avenue du Parc and rue Sherbrooke. Montreal north is
the direction of boulevard Saint-Laurent, measured from the data
(GRID_BEARING), about 57 degrees west of true north.

Streets. GTA2 roads run along the grid, one block per lane. A street that is
straight on the grid becomes one straight piece. A street that is not (rue
Sherbrooke runs some 17 degrees off the grid here) is cut where it crosses
other streets, and each piece between two crossings is laid at its own
position, so the street follows its real line as a staircase. The pieces of one
street meet on the street that crosses between them.
"""
import collections
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OSM = os.path.join(HERE, 'osm', 'plateau_osm.json')

# Highways that carry cars, and the kind of street each makes.
DRIVE = {
    'primary': 'boulevard', 'secondary': 'commercial', 'tertiary': 'commercial',
    'residential': 'residential', 'living_street': 'residential',
    'unclassified': 'residential',
}
# These are drawn on the grid as boulevards whatever OSM calls them.
BOULEVARDS = {'Avenue du Parc', 'Rue Sherbrooke', 'Avenue Papineau', 'Boulevard Saint-Joseph'}
# Commercial streets: shops on the ground floor, taller buildings.
COMMERCIAL = {'Boulevard Saint-Laurent', 'Rue Saint-Denis', 'Avenue du Mont-Royal', 'Rue Rachel',
              'Avenue Laurier', 'Rue Duluth', 'Avenue du Parc', 'Rue Sherbrooke',
              'Avenue Papineau', 'Rue Saint-Viateur', 'Avenue Fairmount'}

# A piece of street may wander this far off its own line before it is cut.
STRAIGHT_TOLERANCE = 9.0
# Pieces shorter than this, after cutting, are dropped.
MIN_PIECE = 35.0
# Streets closer than this to a parallel one (centre to centre) give way to it.
MIN_GAP = 26.0


class Frame:
    """Latitude and longitude to metres on Montreal's grid."""

    def __init__(self, lat0, lon0, bearing):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111320.0 * math.cos(math.radians(lat0))
        self.ky = 110574.0
        b = math.radians(bearing)               # bearing of Montreal north
        self.north = (math.sin(b), math.cos(b))
        self.east = (math.cos(b), -math.sin(b))  # 90 degrees clockwise from it

    def __call__(self, lat, lon):
        e = (lon - self.lon0) * self.kx
        n = (lat - self.lat0) * self.ky
        return (e * self.east[0] + n * self.east[1], e * self.north[0] + n * self.north[1])


def base_name(name):
    """'Rue Rachel Est' and 'Rue Rachel Ouest' are one street on the map."""
    for suffix in (' Est', ' Ouest'):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def ring_area(pts):
    a = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        a += x0 * y1 - x1 * y0
    return a / 2.0


class OsmPlateau:
    def __init__(self, path=DEFAULT_OSM):
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        self.copyright = data.get('osm3s', {}).get('copyright', '')
        els = data['elements']
        self.nodes_ll = {e['id']: (e['lat'], e['lon']) for e in els if e['type'] == 'node'}
        self.ways = [e for e in els if e['type'] == 'way']
        self.way_by_id = {w['id']: w for w in self.ways}
        self.relations = [e for e in els if e['type'] == 'relation']
        self.entrances = [e for e in els if e['type'] == 'node'
                          and e.get('tags', {}).get('railway') == 'subway_entrance']

        bearing = self._grid_bearing()
        lat0, lon0 = self._origin()
        self.bearing = bearing
        self.frame = Frame(lat0, lon0, bearing)
        self.xy = {i: self.frame(*ll) for i, ll in self.nodes_ll.items()}

        self.streets = self._streets()
        self.west = self._pos('Avenue du Parc') - 130.0
        self.east = self._pos('Rue Cartier', default=self._pos('Avenue Papineau') + 110.0) + 90.0
        self.south = self._pos('Rue Sherbrooke', at=0.0) - 95.0
        self.north = self._pos('Boulevard Saint-Joseph') + 175.0
        self.streets = self._fit_to_bounds(self.streets)

    # --- frame ---------------------------------------------------------------

    def _named_ways(self, name, kinds=DRIVE):
        return [w for w in self.ways if w.get('tags', {}).get('highway') in kinds
                and base_name(w['tags'].get('name', '')) == name]

    def _grid_bearing(self):
        """Length-weighted bearing of boulevard Saint-Laurent, pointing north."""
        lat0 = 45.52
        kx, ky = 111320.0 * math.cos(math.radians(lat0)), 110574.0
        sx = sy = 0.0
        for w in self._named_ways('Boulevard Saint-Laurent'):
            pts = [self.nodes_ll[i] for i in w['nodes'] if i in self.nodes_ll]
            for (a_lat, a_lon), (b_lat, b_lon) in zip(pts, pts[1:]):
                dx, dy = (b_lon - a_lon) * kx, (b_lat - a_lat) * ky
                if dy < 0:
                    dx, dy = -dx, -dy
                sx += dx
                sy += dy
        return math.degrees(math.atan2(sx, sy)) % 360.0

    def _origin(self):
        """The node avenue du Parc and rue Sherbrooke share."""
        parc = {n for w in self._named_ways('Avenue du Parc') for n in w['nodes']}
        sher = {n for w in self._named_ways('Rue Sherbrooke') for n in w['nodes']}
        common = [n for n in parc & sher if n in self.nodes_ll]
        if not common:
            raise ValueError('avenue du Parc and rue Sherbrooke do not meet in the data')
        lats = [self.nodes_ll[n][0] for n in common]
        lons = [self.nodes_ll[n][1] for n in common]
        return sum(lats) / len(lats), sum(lons) / len(lons)

    # --- streets -------------------------------------------------------------

    def _segments(self, ways):
        """(x0, y0, x1, y1, oneway sign, lanes) for every segment of the ways."""
        out = []
        for w in ways:
            tags = w['tags']
            ow = tags.get('oneway', 'no')
            sign = 1 if ow in ('yes', 'true', '1') else (-1 if ow == '-1' else 0)
            if tags.get('junction') == 'roundabout':
                sign = 1
            try:
                lanes = int(tags.get('lanes', '0').split(';')[0])
            except ValueError:
                lanes = 0
            pts = [self.xy[i] for i in w['nodes'] if i in self.xy]
            for a, b in zip(pts, pts[1:]):
                out.append((a[0], a[1], b[0], b[1], sign, lanes))
        return out

    def _streets(self):
        by_name = collections.defaultdict(list)
        for w in self.ways:
            tags = w.get('tags', {})
            if tags.get('highway') in DRIVE and tags.get('name') and tags.get('area') != 'yes':
                by_name[base_name(tags['name'])].append(w)
        streets = []
        for name, ways in by_name.items():
            segs = self._segments(ways)
            ns = sum(abs(s[3] - s[1]) for s in segs)
            ew = sum(abs(s[2] - s[0]) for s in segs)
            axis = 'ns' if ns >= ew else 'ew'
            kinds = collections.Counter(DRIVE[w['tags']['highway']] for w in ways)
            kind = kinds.most_common(1)[0][0]
            if name in BOULEVARDS:
                kind = 'boulevard'
            elif name in COMMERCIAL:
                kind = 'commercial'
            streets.append(dict(name=name, axis=axis, kind=kind, segs=segs))
        return streets

    def _pos(self, name, at=None, default=None):
        """A street's cross position: its length-weighted median, or near `at`
        along its own axis."""
        for s in self.streets:
            if s['name'] == name:
                samples = []
                for x0, y0, x1, y1, _, _ in s['segs']:
                    along = (y0 + y1) / 2 if s['axis'] == 'ns' else (x0 + x1) / 2
                    cross = (x0 + x1) / 2 if s['axis'] == 'ns' else (y0 + y1) / 2
                    length = math.hypot(x1 - x0, y1 - y0)
                    if at is None or abs(along - at) < 300:
                        samples.append((cross, length))
                if samples:
                    return weighted_median(samples)
        if default is not None:
            return default
        raise KeyError(name)

    def _fit_to_bounds(self, streets):
        """Cuts each street to the map, then into straight pieces."""
        pieces = []
        for s in streets:
            for piece in self._pieces(s):
                pieces.append(piece)
        # A street whose line is too close to a bigger parallel one gives way.
        rank = {'boulevard': 3, 'commercial': 2, 'residential': 1}
        pieces.sort(key=lambda p: (-rank[p['kind']], -(p['span'][1] - p['span'][0])))
        # Where the two only overlap at one end (a street meeting a bigger one
        # that runs off the grid), the smaller one is cut back to meet it.
        kept = []
        for p in pieces:
            p = dict(p)
            clash = False
            for q in kept:
                if q['axis'] != p['axis'] or q['name'] == p['name']:
                    continue
                lo, hi = max(p['span'][0], q['span'][0]), min(p['span'][1], q['span'][1])
                if hi <= lo or abs(p['pos'] - q['pos']) >= MIN_GAP + 4 * (p['lanes'] + q['lanes'] - 2):
                    continue
                length = p['span'][1] - p['span'][0]
                if hi - lo < 0.5 * length:
                    if lo <= p['span'][0] + 1e-6:
                        p['span'] = (hi, p['span'][1])
                    else:
                        p['span'] = (p['span'][0], lo)
                    if p['span'][1] - p['span'][0] >= MIN_PIECE:
                        continue
                clash = True
                break
            if not clash:
                kept.append(p)
        return kept

    def _pieces(self, s):
        lo_b, hi_b = (self.south, self.north) if s['axis'] == 'ns' else (self.west, self.east)
        cross_lo, cross_hi = (self.west, self.east) if s['axis'] == 'ns' else (self.south, self.north)
        pts = []          # (along, cross, length, sign along the axis, lanes)
        for x0, y0, x1, y1, sign, lanes in s['segs']:
            a0, a1 = (y0, y1) if s['axis'] == 'ns' else (x0, x1)
            c0, c1 = (x0, x1) if s['axis'] == 'ns' else (y0, y1)
            length = math.hypot(x1 - x0, y1 - y0)
            if length < 1e-6:
                continue
            # Skip the cross pieces of a street that zig-zags off its axis.
            if abs(c1 - c0) > abs(a1 - a0) * 1.5:
                continue
            direction = 0
            if sign:
                direction = sign if a1 > a0 else -sign
            steps = max(1, int(length // 10))
            for k in range(steps):
                t = (k + 0.5) / steps
                a = a0 + (a1 - a0) * t
                c = c0 + (c1 - c0) * t
                if lo_b + 20 < a < hi_b - 20 and cross_lo + 20 < c < cross_hi - 20:
                    pts.append((a, c, length / steps, direction, lanes))
        if not pts:
            return []
        pts.sort()
        # Runs: stretches of the street with no gap longer than 40 m.
        runs, current = [], [pts[0]]
        for p in pts[1:]:
            if p[0] - current[-1][0] > 40:
                runs.append(current)
                current = []
            current.append(p)
        runs.append(current)
        out = []
        for run in runs:
            # A short piece joins its neighbour rather than leave a gap in the
            # street; a run that is short altogether is dropped.
            chunks = []
            for chunk in split_straight(run):
                if chunks and chunk[-1][0] - chunk[0][0] < MIN_PIECE:
                    chunks[-1] = chunks[-1] + chunk
                elif chunks and chunks[-1][-1][0] - chunks[-1][0][0] < MIN_PIECE:
                    chunks[-1] = chunks[-1] + chunk
                else:
                    chunks.append(chunk)
            for chunk in chunks:
                lo, hi = chunk[0][0], chunk[-1][0]
                if hi - lo < MIN_PIECE:
                    continue
                pos = weighted_median([(c, l) for _, c, l, _, _ in chunk])
                fwd = sum(l for _, _, l, d, _ in chunk if d > 0)
                back = sum(l for _, _, l, d, _ in chunk if d < 0)
                total = sum(l for _, _, l, _, _ in chunk)
                if fwd > 0.6 * total:
                    direction = 'n' if s['axis'] == 'ns' else 'e'
                elif back > 0.6 * total:
                    direction = 's' if s['axis'] == 'ns' else 'w'
                else:
                    direction = 'two'
                # A divided street is two one-way carriageways side by side.
                spread = weighted_spread([(c, l) for _, c, l, _, _ in chunk])
                divided = direction == 'two' and fwd > 0.3 * total and back > 0.3 * total
                lanes_tag = max((n for *_, n in chunk), default=0)
                out.append(dict(name=s['name'], axis=s['axis'], kind=s['kind'], pos=pos,
                                span=(lo - 12.0, hi + 12.0), dir=direction,
                                lanes=lane_cells(s['kind'], direction, lanes_tag, divided),
                                median=divided and s['kind'] == 'boulevard' and spread > 6.0))
        return out

    # --- areas ---------------------------------------------------------------

    def _ring(self, way):
        pts = [self.xy[i] for i in way['nodes'] if i in self.xy]
        if len(pts) >= 4 and way['nodes'][0] == way['nodes'][-1]:
            return pts[:-1]
        return None

    def polygons(self, test):
        """Outer rings of closed ways and multipolygon relations whose tags pass
        `test`, as lists of (x, y) metres."""
        out = []
        for w in self.ways:
            if test(w.get('tags', {})):
                ring = self._ring(w)
                if ring:
                    out.append((w['id'], w.get('tags', {}), ring))
        for r in self.relations:
            tags = r.get('tags', {})
            if not test(tags):
                continue
            parts = [self.way_by_id[m['ref']]['nodes'] for m in r.get('members', [])
                     if m['type'] == 'way' and m.get('role') in ('outer', '') and m['ref'] in self.way_by_id]
            for nodes in stitch(parts):
                pts = [self.xy[i] for i in nodes if i in self.xy]
                if len(pts) >= 4:
                    out.append((r['id'], tags, pts[:-1]))
        return out

    def buildings(self):
        """(id, tags, ring, storeys) for every building."""
        out = []
        for oid, tags, ring in self.polygons(lambda t: 'building' in t and t.get('building') != 'no'
                                             and t.get('location') != 'underground'):
            out.append((oid, tags, ring, storeys(tags)))
        return out

    def parks(self):
        return self.polygons(lambda t: t.get('leisure') in ('park', 'garden', 'playground', 'pitch')
                             or t.get('landuse') in ('grass', 'recreation_ground')
                             or t.get('natural') in ('wood', 'scrub', 'grassland'))

    def water(self):
        return self.polygons(lambda t: t.get('natural') == 'water')

    def parking(self):
        return self.polygons(lambda t: t.get('amenity') == 'parking' and t.get('parking') != 'underground')

    def lines(self, test):
        out = []
        for w in self.ways:
            tags = w.get('tags', {})
            if test(tags):
                pts = [self.xy[i] for i in w['nodes'] if i in self.xy]
                if len(pts) >= 2:
                    out.append((w['id'], tags, pts))
        return out

    def alleys(self):
        """Back lanes (ruelles) and other service lanes behind the houses."""
        return self.lines(lambda t: t.get('highway') == 'service'
                          and t.get('service') in ('alley', None, 'driveway') and t.get('access') != 'private'
                          and t.get('area') != 'yes')

    def paths(self):
        """Footpaths that are not sidewalks: park paths, pedestrian streets."""
        return self.lines(lambda t: t.get('highway') in ('footway', 'path', 'pedestrian', 'cycleway')
                          and t.get('footway') not in ('sidewalk', 'crossing')
                          and t.get('area') != 'yes')

    def pedestrian_areas(self):
        return self.polygons(lambda t: t.get('highway') == 'pedestrian' or t.get('place') == 'square')

    def metro_entrances(self):
        return [(e.get('tags', {}).get('name', 'Metro'), self.frame(e['lat'], e['lon'])) for e in self.entrances]


def stitch(parts):
    """Joins the ways of a multipolygon's outline end to end into closed rings
    (lists of node ids, first == last)."""
    parts = [list(p) for p in parts if len(p) >= 2]
    rings = []
    while parts:
        ring = parts.pop(0)
        grew = True
        while ring[0] != ring[-1] and grew:
            grew = False
            for i, p in enumerate(parts):
                if p[0] == ring[-1]:
                    ring += p[1:]
                elif p[-1] == ring[-1]:
                    ring += p[-2::-1]
                elif p[-1] == ring[0]:
                    ring = p[:-1] + ring
                elif p[0] == ring[0]:
                    ring = p[:0:-1] + ring
                else:
                    continue
                parts.pop(i)
                grew = True
                break
        if ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def storeys(tags):
    """Storeys above the street. Plateau plexes are mostly two or three."""
    for key in ('building:levels', 'levels'):
        v = tags.get(key)
        if v:
            try:
                return max(1, int(round(float(v.split(';')[0]))))
            except ValueError:
                pass
    if tags.get('height'):
        try:
            return max(1, int(round(float(tags['height'].split()[0]) / 3.2)))
        except ValueError:
            pass
    b = tags.get('building')
    if b in ('church', 'cathedral', 'chapel') or tags.get('amenity') == 'place_of_worship':
        return 5
    if b in ('garage', 'garages', 'shed', 'roof', 'kiosk', 'carport'):
        return 1
    if b in ('commercial', 'retail', 'office', 'school', 'university', 'hospital', 'public'):
        return 4
    return 3


def lane_cells(kind, direction, lanes_tag, divided):
    """GTA2 lanes (one block each) for a street. A block is 7-9 m here, so one
    block holds a lane and its parking lane."""
    if kind == 'boulevard':
        return 4
    if direction == 'two':
        return 2
    return 2 if lanes_tag >= 3 or kind == 'commercial' and lanes_tag >= 2 else 1


def weighted_median(samples):
    samples = sorted(samples)
    total = sum(w for _, w in samples)
    acc = 0.0
    for v, w in samples:
        acc += w
        if acc >= total / 2:
            return v
    return samples[-1][0]


def weighted_spread(samples):
    m = weighted_median(samples)
    total = sum(w for _, w in samples) or 1.0
    return sum(abs(v - m) * w for v, w in samples) / total


def split_straight(run):
    """Cuts a run of (along, cross, ...) samples into pieces that each stay
    within STRAIGHT_TOLERANCE of their own median line."""
    def ok(chunk):
        m = weighted_median([(c, l) for _, c, l, _, _ in chunk])
        # A divided street's carriageways sit ~10 m either side of the line.
        devs = sorted(abs(c - m) for _, c, _, _, _ in chunk)
        return devs[int(len(devs) * 0.9)] <= STRAIGHT_TOLERANCE + 8
    if len(run) < 4 or ok(run):
        return [run]
    # Cut at the sample that best splits the drift.
    best, best_i = None, len(run) // 2
    for i in range(2, len(run) - 2, max(1, len(run) // 40)):
        a, b = run[:i], run[i:]
        ma = weighted_median([(c, l) for _, c, l, _, _ in a])
        mb = weighted_median([(c, l) for _, c, l, _, _ in b])
        err = sum(abs(c - ma) for _, c, *_ in a) + sum(abs(c - mb) for _, c, *_ in b)
        if best is None or err < best:
            best, best_i = err, i
    return split_straight(run[:best_i]) + split_straight(run[best_i:])


OVERPASS = 'https://overpass-api.de/api/interpreter'
QUERY = os.path.join(HERE, 'osm', 'query.overpassql')


def fetch(path=DEFAULT_OSM):
    """Downloads the extract from the Overpass API with osm/query.overpassql.
    It is some 20 MB, over what the repository keeps, so it is fetched once
    and kept beside the query."""
    import urllib.parse
    import urllib.request
    with open(QUERY, encoding='utf-8') as f:
        body = urllib.parse.urlencode({'data': f.read()}).encode()
    req = urllib.request.Request(OVERPASS, data=body, headers={
        'User-Agent': 'gta2-rtx-remix-upgraded plateau map builder', 'Accept': 'application/json'})
    print('fetching the OpenStreetMap extract from %s ...' % OVERPASS)
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read()
    json.loads(data)                 # an error page is not JSON
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(data)
    print('wrote %s (%.1f MB)' % (path, len(data) / 1e6))


def main(argv):
    path = argv[1] if len(argv) > 1 else DEFAULT_OSM
    if not os.path.exists(path):
        fetch(path)
    p = OsmPlateau(path)
    print('grid bearing %.1f deg; bounds x %.0f..%.0f, y %.0f..%.0f'
          % (p.bearing, p.west, p.east, p.south, p.north))
    for s in sorted(p.streets, key=lambda s: (s['axis'], s['pos'])):
        print('  %-2s %7.0f  %6.0f..%-6.0f %-4s lanes %d %-11s %s%s'
              % (s['axis'], s['pos'], s['span'][0], s['span'][1], s['dir'], s['lanes'],
                 s['kind'], s['name'], ' (median)' if s['median'] else ''))
    print('%d street pieces, %d buildings, %d parks, %d ponds, %d lanes, %d paths'
          % (len(p.streets), len(p.buildings()), len(p.parks()), len(p.water()),
             len(p.alleys()), len(p.paths())))


if __name__ == '__main__':
    main(sys.argv)
