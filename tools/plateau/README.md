# Plateau-Mont-Royal map

Tools that build a GTA2 map of the Plateau-Mont-Royal neighbourhood in Montreal,
to play with this renderer.

There are two builds. The **OpenStreetMap build** (next section) is the real
neighbourhood with Plateau tiles and RTX materials. The **hand-drawn build**
after it is the first version: a street table written by hand, played with the
Residential district's own tiles.

## The OpenStreetMap build

![Preview of the OpenStreetMap map](plateau_osm_preview.png)

```bat
python install_plateau.py "C:\Games\GTA2"
```

That is all: choose City 2 in the game and you play the Plateau.
`python install_plateau.py "C:\Games\GTA2" --uninstall` gives City 2 back to
the Residential district. The game's own files are never changed; see what it
does below.

What is real. Every street comes from OpenStreetMap
(fetched once into `osm/plateau_osm.json` with [osm/query.overpassql](osm/query.overpassql); install_plateau.py does it):
where it runs, which way its traffic goes, how wide it is. So does every
building, on its own footprint and at its own number of storeys (taken from
`building:levels`; a Plateau plex without one is three storeys), the parks,
the two ponds of Parc La Fontaine, the back lanes (ruelles), the park paths,
the car parks and the pedestrian squares. The map covers avenue du Parc to
past rue Cartier and the foot of the mountain, from rue Sherbrooke to past
boulevard Saint-Joseph, at about 9.8 m to a block, turned so that Montreal
north (boulevard Saint-Laurent) is up the screen.

What GTA2 changes. GTA2 roads run along the grid, so a street that does not
(rue Sherbrooke runs some 17 degrees off it here) is drawn as a staircase of
straight pieces that meet on the streets crossing between them. Every road
cell has to be reachable by traffic: where a real one-way street leaves a car
nowhere to go (a dead end at the Carre Saint-Louis, say), that street is drawn
two-way; the report lists them. Buildings go up to six storeys. The map keeps
a two-lane ring road round the edge, so no street ends in a wall.

How it looks. `plateau_textures.py` draws 24 Plateau surfaces: red, brown and
buff brick and greystone plex fronts, each in a ground floor (door, the foot of
the outside staircase), a middle floor (balcony, railing) and a top floor
(cornice); shop fronts for the ground floor of the commercial streets; the
church; white membrane and gravel roofs; asphalt, ruelles, concrete sidewalks,
pavers, stone-dust paths, lawn and yards. Each building keeps one family of
brick or stone, picked from its OSM id. `make_plateau_style.py` puts the 64 px
versions into `plateau.sty`, a copy of the district's `ste.sty`, and
`make_plateau_mod.py` gives each one an RTX Remix material: full-size colour,
normal and roughness maps, and lit windows that glow at night.

Zones. The Residential script looks up its zones by name (where you come back
after dying, bus stops, traffic areas); without them the game crashes as the
level starts. The build copies them from the district's map, leaves out the
train stations (they need railway under them, and the game crashes looking
for it), and moves any that land on no street onto the nearest one.

What install_plateau.py does, step by step:

| Step | Writes |
| --- | --- |
| `plateau_textures.py textures` | the surfaces, once |
| `make_plateau_style.py <GTA2> textures out_osm` | `plateau.sty`, and `plateau_tiles.json`: each surface's tile number and RTX Remix hash |
| `make_plateau_osm.py out_osm --tiles out_osm/plateau_tiles.json --zones-from <GTA2>/data/ste.gmp` | `plateau.gmp`, a preview and a report |
| `make_plateau_mod.py textures out_osm/plateau_tiles.json <GTA2>/rtx-remix/mods/plateau` | the Remix materials |
| copy, and edit `data/test1.seq` | City 2's map and style become `plateau.gmp` and `plateau.sty`; `test1.seq.bak` keeps the old one |

### On Linux

Tested with Remix Plus under GE-Proton 11-12 on Hyprland:

- Run the game with GE-Proton (`umu-run`), not plain Wine: Wine 11 never
  copies Remix's picture from its 64-bit bridge into the 32-bit game's window,
  so the screen stays white or black.
- In `rtx.conf`: `rtx.useNewGuiInputMethod = False` (the new method's overlay
  is an opaque white window under X11), `rtx.dlfg.enable = False` and
  `rtx.dlfg.enablePresentMetering = False`,
  `rtx.automation.disableBlockingDialogBoxes = True` (Remix's welcome panel
  cannot be clicked with the old input method), and, with no DLSS under
  Proton, `rtx.upscalerType = 3` with `rtx.taauPreset = 4`.
- In `.trex/bridge.conf`: `presentSemaphoreEnabled = False`.
- In `gta2dx9.ini`: `own_window=1`. The picture then goes into a child of the
  game's window, so the window manager sees one window and the game keeps
  the focus (GTA2 pauses while it does not have it). The child is the render
  size, so make the window that size, and run the prefix at 96 DPI.

`python test_plateau_osm.py` runs every check below on the OpenStreetMap map
too, plus checks of the style, the hashes and the Remix files.

Map data (c) OpenStreetMap contributors, under the Open Database License:
https://www.openstreetmap.org/copyright

## The hand-drawn build

![Preview of the map](plateau_preview.png)

### What is in the map

- The street grid from avenue du Parc to rue Cartier, and from rue Sherbrooke to
  boulevard Saint-Joseph. Montreal's own grid is used, as on the city's street
  signs: "north" runs along Saint-Laurent, away from the river. That puts
  Mount Royal on the west edge of the map.
- Traffic arrows that follow the real one-way streets on the main roads:
  Saint-Laurent goes north, Saint-Urbain and Saint-Dominique go south, and
  Saint-Denis, Mont-Royal, Rachel, Laurier, Sherbrooke, Parc and Papineau are
  two-way. Saint-Joseph has its grass median.
- Rows of two- and three-storey plexes on every block, with a lane (ruelle)
  down the middle of each long block and yards behind the houses. The
  commercial streets have taller buildings.
- Parc La Fontaine with its ponds and chalet, Parc Jeanne-Mance, the foot of
  Mount Royal with the George-Etienne Cartier monument, the Carre Saint-Louis
  with its fountain, and the pedestrian part of rue Prince-Arthur.
- The Eglise Saint-Jean-Baptiste on Rachel, and the Mont-Royal and Sherbrooke
  metro entrances. Landmarks stand behind the sidewalk, never on it.
- About 1,800 street lamps in the warm white of Montreal's LED lamps (3000 K).
  The renderer turns them into RTX Remix lights like the game's own.
- A two-lane ring road around the edge, so that no street ends in a wall.

The street positions are estimates written by hand in `plateau_layout.py`. They
keep the order of the streets and the size of the parks close to the real
neighbourhood, but they are not survey data. To move a street, change its
number there and build again. The map is about 9 m to a block east-west and
7 m north-south, so the Plateau fits GTA2's 256 x 256 blocks.

### Build it

You need Python 3.8 or newer, with numpy and Pillow.

1. Choose the tiles from your own copy of GTA2. The map uses the Residential
   district's style (`ste.sty`):

   ```bat
   python pick_tiles.py "C:\Games\GTA2" --out palette.json --sheet tiles.png
   ```

   This prints the tile it chose for each surface and why. Open `tiles.png` to
   see every tile with its number. To change a choice, edit its number in
   `palette.json`.

2. Build the map:

   ```bat
   python make_plateau_map.py out --palette palette.json
   ```

   `out` then holds `plateau.gmp`, `plateau.mmp`, `plateau.mis`, a preview
   picture and a report.

   The map uses fewer than 200 kinds of block, but the file holds 4,000: the
   rest are unused padding. Renderer builds made before this map came out
   compare 4,000 block records when they work out which map is loaded, and a
   shorter list would make them read past its end.

3. Copy `plateau.gmp` and `plateau.mmp` into the GTA2 `data` folder.

The renderer needs no change: it reads whatever map the game has loaded, and it
finds the style from `plateau.mmp`.

### Play it

GTA2 also needs a compiled mission script (`plateau.scr`) to start a map. Two
ways to get into the Plateau:

- **Quick look, no script.** Back up `data\ste.gmp`, copy `plateau.gmp` over it,
  and start the Residential district. You play the Plateau with that district's
  script, so its missions do not fit the streets, and you may start on a
  rooftop. Copy the backup back afterwards.
- **Its own level.** Compile `plateau.mis` with GTA2's mission compiler into
  `data\plateau.scr`. The script places the player on the corner of
  Saint-Denis and Mont-Royal. It has not been tested with the compiler yet, so
  check it against the compiler's own sample scripts first.

## Montreal sun

`montreal_timeofday.ini` sets the renderer's sun to Montreal's latitude and the
season. Open it for the steps. In short: change the five values in the
`[timeofday]` section of `gta2dx9.ini` to the ones in the file. Change them,
do not add a second copy. If you have saved settings from the F4 menu before,
`gta2dx9_settings.ini` wins over `gta2dx9.ini`. Then set the values in the F4
menu instead: tick Advanced first, or the Latitude and Season sliders do not
show. The file lists the menu's own names and values.

The renderer's clock is solar time, with no daylight-saving time. A Montreal
summer sunset at 20:45 on a clock is about 19:45 on the renderer's clock.

## Options

| Option | What it does |
| --- | --- |
| `--palette palette.json` | Tiles from `pick_tiles.py`. Without it the map uses placeholder tiles and the textures are wrong. |
| `--style ste.sty` | The style named in `plateau.mmp`. Default: the palette's. |
| `--nav-zones` | Adds named areas: one main zone (plateau) over the whole map, and local areas inside it (mntroyal, stlaur, stdenis, parcfont). GTA2 shows an area's name from its text file, which does not have these names, so they may show blank. Off by default. |
| `--no-lights` | Leaves out the street lamps. |

## Tests

```bat
python test_plateau.py
```

The tests need no GTA2 files. They build the map with a made-up style and
check that:

- every cell has ground and no building goes above level 7
- every traffic arrow leads to another road cell
- a car can drive from any road cell to any other one
- every storey of every building has a solid wall on each open side
- no building stands right at the kerb
- the block list is padded to 4,000
- the navigation zones are one main zone, holding every road and sidewalk,
  with local areas inside it
- `pick_tiles.py` finds all twelve tiles in a map where the answer is known

With a C++ compiler on the PATH they also load the map with the renderer's own
parser and mesh builder (`src/gta2_map.cpp`, `src/world_mesh.cpp`, through
`tools/dump_block.cpp audit`) and check that no triangle is wider than one
block. The one exception is the seal: a single quad the mesh builder lays under
every map.

## Files

| File | What it is |
| --- | --- |
| `plateau_layout.py` | Streets, parks, water, plazas, landmarks: the part to edit |
| `make_plateau_map.py` | Turns the layout into a map |
| `pick_tiles.py` | Chooses tiles from your GTA2 style |
| `gmp.py` | Reads and writes `.gmp` files |
| `montreal_timeofday.ini` | The renderer's sun for Montreal |
| `test_plateau.py` | The tests |
| `osm_layout.py` | OpenStreetMap: streets, buildings, parks, water, lanes, on Montreal's grid |
| `osm/plateau_osm.json`, `osm/query.overpassql` | The OpenStreetMap extract and the query that made it |
| `make_plateau_osm.py` | Turns the OpenStreetMap layout into a map with Plateau tiles |
| `plateau_textures.py` | Draws the Plateau surfaces, full size and 64 px |
| `make_plateau_style.py` | Puts the surfaces into `plateau.sty` and works out their Remix hashes |
| `make_plateau_mod.py` | Writes the RTX Remix materials for them |
| `install_plateau.py` | All of the above, into a GTA2 folder as City 2 |
| `test_plateau_osm.py` | The tests for the OpenStreetMap build |
