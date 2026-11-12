"""Builds the OpenStreetMap Plateau and puts it in a GTA2 folder as City 2.

    python install_plateau.py <GTA2 folder>              build and install
    python install_plateau.py <GTA2 folder> --uninstall  put City 2 back

In order:

1. plateau_textures.py draws the Plateau surfaces (into textures/, once).
2. make_plateau_style.py writes plateau.sty from the game's own ste.sty.
3. make_plateau_osm.py builds plateau.gmp from osm/plateau_osm.json, with the
   Residential district's zones so its script runs.
4. make_plateau_mod.py writes the RTX Remix materials into
   rtx-remix/mods/plateau (only used when Remix is installed).
5. plateau.gmp and plateau.sty go into data/, and City 2's entry in
   data/test1.seq is pointed at them. test1.seq is kept as test1.seq.bak the
   first time.

The district's own ste.gmp and ste.sty are never changed. Choose City 2 in the
game and you play the Plateau with the Residential district's script.
--uninstall puts test1.seq back and removes the three things step 5 and 4 added.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEXTURES = os.path.join(HERE, 'textures')
BUILD = os.path.join(HERE, 'out_osm')


def run(*args):
    print('>', ' '.join(args))
    subprocess.run([sys.executable] + list(args), check=True, cwd=HERE)


def patch_seq(path, gmp_name, sty_name):
    """Points the City 2 main entry (GMPFile = ste.gmp) at the Plateau."""
    with open(path, 'rb') as f:
        raw = f.read()
    text = raw.decode('latin-1')
    lines = text.splitlines(keepends=True)
    out, in_city2, changed = [], False, 0
    for line in lines:
        key = line.split('=')[0].strip().lower()
        value = line.split('=', 1)[1].strip().lower() if '=' in line else ''
        if key == 'mainorbonus':
            in_city2 = False
        if key == 'gmpfile' and value in ('ste.gmp', gmp_name.lower()):
            in_city2 = True
            line = line.split('=')[0] + '= ' + gmp_name + line[len(line.rstrip('\r\n')):]
            changed += 1
        elif key == 'styfile' and in_city2:
            line = line.split('=')[0] + '= ' + sty_name + line[len(line.rstrip('\r\n')):]
            changed += 1
        out.append(line)
    if changed != 2:
        sys.exit('%s: could not find the City 2 entry (GMPFile = ste.gmp)' % path)
    with open(path, 'wb') as f:
        f.write(''.join(out).encode('latin-1'))


def audio_file(data, name):
    """data\\audio\\<name>, whatever case the folder and file have on disk."""
    for folder in os.listdir(data):
        if folder.lower() == 'audio':
            for f in os.listdir(os.path.join(data, folder)):
                if f.lower() == name.lower():
                    return os.path.join(data, folder, f)
    sys.exit('no data/audio/%s in %s' % (name, data))


def mod_textures(mod):
    """The ./SubUSDs/ files a mod's mod.usda refers to."""
    path = os.path.join(mod, 'mod.usda')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as f:
        return sorted(set(re.findall(r'@\./SubUSDs/([^@/]+)@', f.read())))


def share_textures(mods):
    """Puts every mod's textures in every other mod's SubUSDs folder.

    Remix resolves the ./SubUSDs/ paths of all the mods it loads against one
    asset search path, the last mod's ("Overriding asset search path" in its
    log), so with the Plateau loaded after the RTX release the release's own
    textures were looked for in mods/plateau and not found."""
    folders = [os.path.join(mods, m, 'SubUSDs') for m in sorted(os.listdir(mods))
               if os.path.isdir(os.path.join(mods, m, 'SubUSDs'))]
    for src in folders:
        for dst in folders:
            if src == dst:
                continue
            for name in os.listdir(src):
                if not os.path.exists(os.path.join(dst, name)):
                    shutil.copyfile(os.path.join(src, name), os.path.join(dst, name))


def install(game):
    data = os.path.join(game, 'data')
    seq = os.path.join(data, 'test1.seq')
    if not os.path.exists(os.path.join(data, 'ste.sty')) or not os.path.exists(seq):
        sys.exit('%s does not look like a GTA2 folder (no data/ste.sty or data/test1.seq)' % game)
    sys.path.insert(0, HERE)
    import osm_layout
    if not os.path.exists(osm_layout.DEFAULT_OSM):
        osm_layout.fetch()
    if not os.path.exists(os.path.join(TEXTURES, 'manifest.json')):
        run('plateau_textures.py', TEXTURES)
    run('make_plateau_style.py', game, TEXTURES, BUILD)
    tiles = os.path.join(BUILD, 'plateau_tiles.json')
    # The district's own map for its zones: the backup if an earlier quick
    # look copied a Plateau over ste.gmp.
    zones = os.path.join(data, 'ste.gmp.bak')
    if not os.path.exists(zones):
        zones = os.path.join(data, 'ste.gmp')
    run('make_plateau_osm.py', BUILD, '--tiles', tiles, '--zones-from', zones)
    mod = os.path.join(game, 'rtx-remix', 'mods', 'plateau')
    run('make_plateau_mod.py', TEXTURES, tiles, mod)
    share_textures(os.path.dirname(mod))
    for name in ('plateau.gmp', 'plateau.sty'):
        shutil.copyfile(os.path.join(BUILD, name), os.path.join(data, name))
    # GTA2 loads a district's sounds by its style's name (data\audio\STE.SDT for
    # ste.sty), so plateau.sty needs the Residential sounds under its own name.
    for ext in ('RAW', 'SDT'):
        src = audio_file(data, 'STE.' + ext)
        shutil.copyfile(src, os.path.join(os.path.dirname(src), 'PLATEAU.' + ext))
    if not os.path.exists(seq + '.bak'):
        shutil.copyfile(seq, seq + '.bak')
    patch_seq(seq, 'plateau.gmp', 'plateau.sty')
    print('installed: City 2 is now the Plateau (data/plateau.gmp, data/plateau.sty, %s)' % mod)


def uninstall(game):
    data = os.path.join(game, 'data')
    seq = os.path.join(data, 'test1.seq')
    if os.path.exists(seq + '.bak'):
        shutil.copyfile(seq + '.bak', seq)
    audio = os.path.dirname(audio_file(data, 'STE.SDT'))
    for path in (os.path.join(data, 'plateau.gmp'), os.path.join(data, 'plateau.sty'),
                 os.path.join(audio, 'PLATEAU.RAW'), os.path.join(audio, 'PLATEAU.SDT')):
        if os.path.exists(path):
            os.remove(path)
    mod = os.path.join(game, 'rtx-remix', 'mods', 'plateau')
    if os.path.isdir(mod):
        # The copies share_textures left in the other mods' folders.
        names = mod_textures(mod)
        for other in os.listdir(os.path.dirname(mod)):
            for name in names:
                path = os.path.join(os.path.dirname(mod), other, 'SubUSDs', name)
                if other != 'plateau' and os.path.exists(path):
                    os.remove(path)
        shutil.rmtree(mod)
    print('City 2 is the Residential district again')


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('game')
    ap.add_argument('--uninstall', action='store_true')
    args = ap.parse_args()
    (uninstall if args.uninstall else install)(args.game)
