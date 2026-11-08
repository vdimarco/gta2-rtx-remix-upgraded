# GTA2 RTX Remix — HD Cars Edition

GTA2 path-traced with [NVIDIA RTX Remix](https://github.com/NVIDIAGameWorks/rtx-remix), with hand-made HD cars,
real glass, doors and dents, working lights and new voice packs.

<img width="1751" height="173" alt="gta2 rtx upgraded" src="https://github.com/user-attachments/assets/38adc172-302d-4d7f-b37e-8026d78dd557" />

https://github.com/user-attachments/assets/38f0c67a-ba31-46e7-a305-c5b6cfbde161

<img width="3840" height="2160" alt="Grand Theft Auto 2 2026 11 01 - 12 16 56 07 00_00_15_26 Still002" src="https://github.com/user-attachments/assets/48fe2029-cf81-48f3-a180-14d13b71fd2a" />
<img width="3840" height="2160" alt="Grand Theft Auto 2 2026 11 01 - 12 16 56 07 00_01_02_47 Still003" src="https://github.com/user-attachments/assets/bbcffc85-eaff-49e3-98c0-7bd6405a21e8" />

This project is an upgrade of **[gebdag/gta2-rtx-remix](https://github.com/gebdag/gta2-rtx-remix)** (now updated to his
newest version, v0.8.5, including its WidescreenFix and Plugin SDK support), the Direct3D 9 renderer that makes GTA2 run on RTX Remix.
All the renderer work is theirs; thank you.
Everything on top of it (HD cars, car damage, lights, blood, voices and more) is added here.
Both projects are MIT licensed.

## Install (3 steps)

1. **Install RTX Remix.** Download `Remix_Plus_v1.5.1_x86_games_release.zip` from the **Assets** list at the bottom of the
   [Remix Plus 1.5.1 release page](https://github.com/RemixProjGroup/dxvk-remix/releases/tag/remix-plus-1.5.1) and unzip it into
   your GTA2 folder, next to `gta2.exe`. (Without Remix the game starts, but with no ray tracing.)
2. **Download** the zip from [Releases](../../releases) and **unzip it into your GTA2 folder**. Say yes to replacing files.
3. **Start `gta2.exe`.**

That's it. Press **F4** in game for the settings panel (the game's own **Options** button opens it too).
Installing never touches your player profiles or saves.

**You need:** your own copy of GTA2 (v9.6 is what it was tested on; this project ships no game files), an
RTX-capable GPU with a current NVIDIA driver, and Windows 10 or 11, 64-bit.

### PC specs

The game itself is from 1999, but the lighting is full ray tracing (path tracing), so the graphics card is what matters.

| | Minimum | Recommended |
|---|---|---|
| Graphics card | NVIDIA GeForce RTX 2060 or newer (any RTX card; not tested on cards this small, so expect to lower the resolution) | NVIDIA GeForce RTX 3070 or better |
| Video memory | 8 GB | 12 GB or more |
| Processor | 6 cores, about 3.5 GHz | 8 cores or more, e.g. Ryzen 7 / Core i7 and up |
| Memory (RAM) | 16 GB | 32 GB |
| Disk | 5 GB free, on an SSD | 10 GB free on an SSD |
| System | Windows 10 or 11, 64-bit, current NVIDIA driver | the same |

Tested on two PCs:

- Ryzen 7 9800X3D, 32 GB DDR5 RAM, GeForce RTX 5080 (frame generation on).
- Ryzen 9 5950X, 64 GB RAM, GeForce RTX 3090 Ti at 2560x1440 (frame generation off).

Both run smoothly with no stutter. The game runs at 30 frames per second (that is what GTA2 was made for), so a faster card
gives you higher resolution and cleaner light, not more frames.

- **Frame generation** (DLSS 3 and later) works only on GeForce RTX 40 series and newer. On an RTX 30 series card, leave it
  off (`rtx.dlfg.enable = False` in `rtx.conf`): it only adds ghosting.
- **Start-up bar:** when the game starts, a progress bar prepares the HD cars and loads their saved pictures from the
  `gta2dx9_cache` folder (up to about 2 GB) into memory. The game begins when the bar is full, so nothing has to be prepared
  while you play. That is why an SSD matters. To turn the bar off, put `enabled=0` under `[preload]` in `gta2dx9_effects.ini`.
- Honest note: only the two high-end PCs above have actually been tested. The minimum and recommended columns are estimates
  and will be corrected as people report how it runs on their hardware.

**Where to get GTA2:** see [Where to get GTA2](#where-to-get-gta2) below.

**Something wrong?** Check `gta2dx9.log` next to `gta2.exe`, and the troubleshooting list in `gta2dx9_README.txt`
inside the zip.

**Only the F4 menu works and the game looks the same as before?** Press F4. If the top of the panel says *"Remix API
unavailable ... (not running under Remix)"*, RTX Remix is not installed in your GTA2 folder. Go back to step 1 above: download
`Remix_Plus_v1.5.1_x86_games_release.zip` (the x86, 32-bit one), unzip all of it into the GTA2 folder next to `gta2.exe`, and
say yes to replacing files. You should then see a `d3d9.dll` and a `.trex` folder there. You also need an NVIDIA RTX graphics
card and a current driver.

## What you get

- Hand-made HD cars with full PBR maps and real glass
- Doors and dents drawn on the HD cars, wheels, roof domes and gang roof logos
- Taxi, police and emergency lights; blood puddles on the road
- Radio stays in stereo at any distance (the original distance effect is a menu option)
- Voice packs
- Everything from the original project: lights from the game's own events, dynamic time of day
  (Remix Plus only), emissive maps, animated water, full 16:9, and 60 fps with frame generation

## Where to get GTA2

GTA2 was officially free for a while: Rockstar gave it away as part of its **Rockstar Classics** series, together with the first
GTA and Wild Metal Country. That free offer has ended. Rockstar later sold it on its own store (until 2013, according to
[Delisted Games](https://delistedgames.com/?p=10001)), and Steam removed its GTA2 page in July 2025 without explanation.

So today there is no official place to get it. The game is still easy to find online if you look for it (searching for
"GTA 2 Rockstar Classics" is a good start), but those copies come from third parties, so use one you trust, or a copy you
already own (the original CD, or an old Rockstar or Steam purchase). This project ships no game files and does not host or
link to downloads of the game.

## Plateau-Mont-Royal map

`tools/plateau` builds a GTA2 map of Montreal's Plateau-Mont-Royal: the street
grid from avenue du Parc to rue Cartier and from Sherbrooke to Saint-Joseph,
Parc La Fontaine, rows of plexes with their lanes, and Montreal's warm street
lamps. It uses the tiles of your own copy of GTA2. See
[tools/plateau/README.md](tools/plateau/README.md).

## Known issues

The tank turret still uses the original low-resolution art, the 5-star FBI cars are blue instead of black,
and a few cars still have odd windows. More in the [release notes](../../releases).

## Autosave

The mod saves your game by itself a moment after a big payout (a mission reward, for example), so a crash costs you less.
This is on by default. To turn it off, press **F4** in game, untick **Save after a big payout** and click **Save**, or
add these two lines to `gta2dx9_settings.ini` in your GTA2 folder:

```
[save]
auto_save=0
```

## Uninstall

Delete the `gta2dx9*` files, `gta2dx9_hd`, `gta2dx9_voice` and `rtx-remix\mods\gta2rtx` and `gta2water`, and run
`uninstall.reg`. The install replaced three of GTA2's own files (`d3ddll.dll`, `Dmavideo.dll`, `DMAGlide.dll`), so to get
the original game back, copy those three from a backup if you made one (optional, before step 2), or reinstall GTA2.

## Building from source

Needs Visual Studio with the C++ x86 toolset.

```bat
package.bat
```

Builds `dll\build\gta2dx9.dll` and `vid\build\gta2dx9_vid.dll` and assembles the release files in `release\`.
The two DLLs can also be built alone with `dll\build.bat` and `vid\build.bat`. The RTX Remix API headers in
`dll\deps\bridge_api` must match the Remix runtime the game uses (see `dll\deps\README.md`).

```
dll/        gta2dx9.dll, the renderer
vid/        gta2dx9_vid.dll, the video device shim
src/        map, style, mesh, camera and D3D9 renderer code used by the DLL
package/    release files: settings, registry files, player README, tuned ini files
tools/      development tools; tools/plateau builds the Plateau-Mont-Royal map
docs/       research notes
```

Technical notes on the renderer are in `dll/README.md`.

## Legal

MIT licence, see [LICENSE](LICENSE); third-party components are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
This is an unofficial fan project. GTA2 is a trademark and copyright of Rockstar Games / Take-Two Interactive,
and this repository and its releases contain no game files.
