"""Writes the RTX Remix mod that gives the Plateau tiles real materials.

    python make_plateau_mod.py <textures folder> <plateau_tiles.json> <mod folder>

The mod folder is normally <GTA2 folder>/rtx-remix/mods/plateau. Remix loads
it beside the release's own gta2rtx mod.

For every surface plateau_textures.py drew, Remix is told: where you see this
tile (by its texture hash, from make_plateau_style.py), use this material
instead - the full-size colour, normal and roughness maps, and for the floors
with lit windows an emissive map, so the houses glow at night.

The maps are written as Remix reads them, the way make_water_mod.py does it:
DDS with a full mip chain; colour and emissive as BC1, the normal map as BC5
in Remix's unsigned octahedral encoding, roughness as BC4.
"""
import json
import os
import struct
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import make_water_mod as water   # noqa: E402  (tools/make_water_mod.py)

# How bright the lit windows are, in Remix's emissive units.
WINDOW_GLOW = 4.0

MATERIAL = '''        def Material "mat_{h}"
        {{
            token outputs:mdl:displacement.connect = </RootNode/Looks/mat_{h}/Shader.outputs:out>
            token outputs:mdl:surface.connect = </RootNode/Looks/mat_{h}/Shader.outputs:out>
            token outputs:mdl:volume.connect = </RootNode/Looks/mat_{h}/Shader.outputs:out>

            def Shader "Shader" (
                kind = "Material"
            )
            {{
                uniform token info:implementationSource = "sourceAsset"
                uniform asset info:mdl:sourceAsset = @AperturePBR_Opacity.mdl@
                uniform token info:mdl:sourceAsset:subIdentifier = "AperturePBR_Opacity"
                custom asset inputs:diffuse_texture = @./SubUSDs/{key}.a.rtex.dds@ (
                    colorSpace = "auto"
                )
                custom asset inputs:normalmap_texture = @./SubUSDs/{key}.n.rtex.dds@ (
                    colorSpace = "raw"
                )
                custom asset inputs:reflectionroughness_texture = @./SubUSDs/{key}.r.rtex.dds@ (
                    colorSpace = "raw"
                )
{emission}                token outputs:out
            }}
        }}
'''

EMISSION = '''                custom asset inputs:emissive_mask_texture = @./SubUSDs/{key}.e.rtex.dds@ (
                    colorSpace = "auto"
                )
                custom bool inputs:enable_emission = 1
                custom float inputs:emissive_intensity = {glow}
'''


def mips(a):
    levels = [a]
    while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
        levels.append(water.halve(levels[-1]))
    return levels


def bc1_blocks(rgb):
    """BC1 (DXT1) of a 0..255 RGB image, 4-colour mode. Endpoints are the
    block's extremes along its brightest-to-darkest axis."""
    h, w = rgb.shape[:2]
    bh, bw = (h + 3) // 4, (w + 3) // 4
    p = np.pad(rgb, ((0, bh * 4 - h), (0, bw * 4 - w), (0, 0)), mode='edge').astype(np.float32)
    blocks = p.reshape(bh, 4, bw, 4, 3).transpose(0, 2, 1, 3, 4).reshape(bh, bw, 16, 3)
    mean = blocks.mean(2, keepdims=True)
    centred = blocks - mean
    # Principal axis by a few power iterations.
    axis = np.ones((bh, bw, 3), np.float32)
    cov = np.einsum('abki,abkj->abij', centred, centred)
    for _ in range(4):
        axis = np.einsum('abij,abj->abi', cov, axis)
        axis /= np.maximum(np.linalg.norm(axis, axis=-1, keepdims=True), 1e-6)
    t = np.einsum('abki,abi->abk', centred, axis)
    c0 = mean[:, :, 0] + axis * t.max(-1, keepdims=True)
    c1 = mean[:, :, 0] + axis * t.min(-1, keepdims=True)

    def to565(c):
        c = np.clip(np.rint(c), 0, 255).astype(np.uint32)
        return ((c[..., 0] >> 3) << 11) | ((c[..., 1] >> 2) << 5) | (c[..., 2] >> 3)

    def from565(v):
        r = ((v >> 11) & 31) * 255 // 31
        g = ((v >> 5) & 63) * 255 // 63
        b = (v & 31) * 255 // 31
        return np.stack([r, g, b], -1).astype(np.float32)

    e0, e1 = to565(c0), to565(c1)
    swap = e0 < e1
    e0, e1 = np.where(swap, e1, e0), np.where(swap, e0, e1)
    same = e0 == e1
    e0 = np.where(same & (e0 < 0xFFFF), e0 + 1, e0)       # keep 4-colour mode
    p0, p1 = from565(e0), from565(e1)
    palette = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 2)   # bh,bw,4,3
    d = ((blocks[:, :, :, None, :] - palette[:, :, None, :, :]) ** 2).sum(-1)
    idx = d.argmin(-1).astype(np.uint32)                                   # bh,bw,16
    bits = np.zeros((bh, bw), np.uint32)
    for i in range(16):
        bits |= idx[..., i] << np.uint32(2 * i)
    out = np.zeros((bh, bw, 8), np.uint8)
    out[..., 0] = e0 & 0xFF
    out[..., 1] = e0 >> 8
    out[..., 2] = e1 & 0xFF
    out[..., 3] = e1 >> 8
    for b in range(4):
        out[..., 4 + b] = (bits >> np.uint32(8 * b)) & 0xFF
    return out.tobytes()


def write_dds(path, levels, fourcc, block_bytes):
    height, width = levels[0][1]
    header = struct.pack('<7I44x', 124, 0xA1007, height, width, len(levels[0][0]), 0, len(levels))
    pixfmt = struct.pack('<2I4s5I', 32, 0x4, fourcc, 0, 0, 0, 0, 0)
    caps = struct.pack('<5I', 0x401008, 0, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'DDS ' + header + pixfmt + caps + b''.join(data for data, _ in levels))


def srgb_halve(a):
    """Mip a colour image in linear light."""
    lin = (a / 255.0) ** 2.2
    return np.clip(water.halve(lin) ** (1 / 2.2) * 255.0, 0, 255)


def colour_dds(png, path):
    a = np.asarray(Image.open(png).convert('RGB')).astype(np.float32)
    levels = [a]
    while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
        levels.append(srgb_halve(levels[-1]))
    write_dds(path, [(bc1_blocks(l), l.shape[:2]) for l in levels], b'DXT1', 8)


def roughness_dds(png, path):
    a = np.asarray(Image.open(png).convert('L')).astype(np.float32)[..., None]
    levels = mips(a)
    data = [(water.bc4_blocks(np.clip(np.rint(l[..., 0]), 0, 255).astype(np.uint8)).tobytes(), l.shape[:2])
            for l in levels]
    write_dds(path, data, b'BC4U', 8)


def normal_dds(png, path):
    water.write_normal_dds(png, path)


def build(textures, tiles_json, mod):
    with open(os.path.join(textures, 'manifest.json')) as f:
        manifest = json.load(f)
    with open(tiles_json) as f:
        hashes = json.load(f)['hashes']
    sub = os.path.join(mod, 'SubUSDs')
    os.makedirs(sub, exist_ok=True)
    body = []
    for item in manifest:
        key = item['key']
        folder = os.path.join(textures, key)
        colour_dds(os.path.join(folder, 'albedo.png'), os.path.join(sub, key + '.a.rtex.dds'))
        normal_dds(os.path.join(folder, 'normal.png'), os.path.join(sub, key + '.n.rtex.dds'))
        roughness_dds(os.path.join(folder, 'roughness.png'), os.path.join(sub, key + '.r.rtex.dds'))
        emission = ''
        if item.get('has_emissive') and os.path.exists(os.path.join(folder, 'emissive.png')):
            colour_dds(os.path.join(folder, 'emissive.png'), os.path.join(sub, key + '.e.rtex.dds'))
            emission = EMISSION.format(key=key, glow=WINDOW_GLOW)
        body.append(MATERIAL.format(h=hashes[key], key=key, emission=emission))
        print('  %-20s %s' % (key, hashes[key]))
    text = ('#usda 1.0\n(\n    upAxis = "Y"\n)\n\nover "RootNode"\n{\n    over "Looks"\n    {\n'
            + '\n'.join(body) + '    }\n}\n')
    with open(os.path.join(mod, 'mod.usda'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print('wrote %s: %d materials' % (mod, len(body)))


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__.split('\n\n')[1])
    build(*sys.argv[1:])
