"""Apply the gate's fix advice to a generated "pixel art" sprite that has real transparency.

    python fix_sprite.py SRC.png DST.png

Thresholds alpha at 128, crops to the sprite, area-downscales it to fit 32x32 (premultiplied,
so the transparent background doesn't bleed into the edge colors), and quantizes the visible
pixels to 16 colors without dithering. The models that painted a checkerboard instead of
returning alpha need the background removed by hand first; this script refuses them.
"""

import sys

import numpy as np
from PIL import Image

SIZE, COLORS = 32, 16


def fix(src, dst):
    im = Image.open(src).convert("RGBA")
    rgba = np.asarray(im).copy()
    if (rgba[..., 3] == 0).mean() < 0.2:
        sys.exit(f"{src}: no transparent background to keep; remove the background first")
    bg = rgba[..., 3] < 128
    rgba[..., 3] = np.where(bg, 0, 255)
    ys, xs = np.nonzero(~bg)
    rgba = rgba[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    h, w = rgba.shape[:2]
    s = SIZE / max(h, w)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    # premultiplied area downscale so transparent pixels don't bleed color
    pm = rgba.astype(np.float32)
    pm[..., :3] *= pm[..., 3:4] / 255
    small = np.asarray(Image.fromarray(pm.clip(0, 255).astype(np.uint8)).resize((nw, nh), Image.BOX)).astype(
        np.float32
    )
    alpha = small[..., 3]
    rgb = np.where(alpha[..., None] > 0, small[..., :3] * 255 / np.maximum(alpha[..., None], 1), 0)
    keep = alpha >= 128
    canvas = np.zeros((SIZE, SIZE, 4), np.uint8)
    ox, oy = (SIZE - nw) // 2, SIZE - nh
    canvas[oy : oy + nh, ox : ox + nw, :3] = rgb.clip(0, 255).astype(np.uint8)
    canvas[oy : oy + nh, ox : ox + nw, 3] = np.where(keep, 255, 0)
    vis = canvas[..., 3] == 255
    pal_src = Image.fromarray(canvas[vis][None, :, :3])
    q = pal_src.quantize(COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = canvas.copy()
    out[vis, :3] = np.asarray(q.convert("RGB"))[0]
    out[~vis] = 0
    Image.fromarray(out, "RGBA").save(dst)


if __name__ == "__main__":
    fix(sys.argv[1], sys.argv[2])
