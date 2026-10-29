#!/usr/bin/env python3
"""PPM (320x240) з cyd_sim -> PNG 960x720 (nearest) + версії в рамці CYD + zip."""
import glob, os, sys, zipfile
from PIL import Image, ImageDraw, ImageFilter

raw, out = sys.argv[1], sys.argv[2]
os.makedirs(os.path.join(out, "framed"), exist_ok=True)
S, B = 3, 70                         # масштаб, ширина бортика
for f in sorted(glob.glob(os.path.join(raw, "*.ppm"))):
    name = os.path.splitext(os.path.basename(f))[0]
    scr = Image.open(f).convert("RGB").resize((320 * S, 240 * S), Image.NEAREST)
    scr.save(os.path.join(out, name + ".png"))
    W, H = scr.width + 2 * B, scr.height + 2 * B
    pad = 40
    canvas = Image.new("RGBA", (W + 2 * pad, H + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((pad, pad + 12, pad + W, pad + H + 12), 48, fill=(0, 0, 0, 150))
    canvas = Image.alpha_composite(canvas, sh.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(canvas)
    d.rounded_rectangle((pad, pad, pad + W, pad + H), 48, fill=(24, 26, 30, 255), outline=(58, 62, 70, 255), width=3)
    d.rounded_rectangle((pad + B - 10, pad + B - 10, pad + B + scr.width + 10, pad + B + scr.height + 10), 10, fill=(6, 7, 9, 255))
    canvas.paste(scr, (pad + B, pad + B))
    d.ellipse((pad + W - 34, pad + H // 2 - 6, pad + W - 22, pad + H // 2 + 6), fill=(40, 200, 90, 255))  # LED
    canvas.save(os.path.join(out, "framed", name + "_framed.png"))
    print(name)
z = out.rstrip("/") + ".zip"
with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(glob.glob(os.path.join(out, "*.png")) + glob.glob(os.path.join(out, "framed", "*.png"))):
        zf.write(p, os.path.relpath(p, os.path.dirname(out.rstrip("/"))))
print("zip", z)
