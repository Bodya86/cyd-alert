#!/usr/bin/env python3
"""HD2: raw — справжній рендер UI на ×12 (3840x2880, hd/build_hd.sh). Екран → 2560x1920 (LANCZOS),
слайди 3840x2160 з пристроєм (екран 2400x1800, бортик зі згладжуванням)."""
import glob, os, sys
from PIL import Image, ImageDraw, ImageFilter
raw, out = sys.argv[1], sys.argv[2]
W, H = 3840, 2160
SS = 3                                   # суперсемплінг для бортика
sw, sh = 2400, 1800
B = 110; R = 90                         # бортик, радіус
dw, dh = sw + 2 * B, sh + 2 * B
ox, oy = (W - dw) // 2, (H - dh) // 2 - 25

# фон: м'який радіальний градієнт
bg = Image.new("RGB", (W, H))
g = Image.radial_gradient("L").resize((W, H), Image.BICUBIC)
bg = Image.composite(Image.new("RGB", (W, H), (14, 17, 22)), Image.new("RGB", (W, H), (44, 50, 60)), g)

def device_layer():
    L = Image.new("RGBA", (W * SS // 2, H * SS // 2), (0, 0, 0, 0))  # працюємо в 1.5x? простіше — повний SS по області пристрою
    return L

# малюємо пристрій у області з SS
pad = 200
aw, ah = (dw + 2 * pad) * SS, (dh + 2 * pad) * SS
layer = Image.new("RGBA", (aw, ah), (0, 0, 0, 0))
d = ImageDraw.Draw(layer)
x0, y0 = pad * SS, pad * SS
d.rounded_rectangle((x0, y0, x0 + dw * SS, y0 + dh * SS), R * SS, fill=(30, 32, 37, 255))
# фаска / світліший край
d.rounded_rectangle((x0, y0, x0 + dw * SS, y0 + dh * SS), R * SS, outline=(78, 83, 93, 255), width=5 * SS)
d.rounded_rectangle((x0 + 14 * SS, y0 + 14 * SS, x0 + (dw - 14) * SS, y0 + (dh - 14) * SS), (R - 14) * SS, outline=(22, 24, 28, 255), width=4 * SS)
# скло навколо екрана
m = 20
d.rounded_rectangle((x0 + (B - m) * SS, y0 + (B - m) * SS, x0 + (B + sw + m) * SS, y0 + (B + sh + m) * SS), 18 * SS, fill=(5, 6, 8, 255))
# LED + отвір датчика
cx, cy = x0 + (dw - B // 2) * SS, y0 + (dh // 2) * SS
d.ellipse((cx - 11 * SS, cy - 11 * SS, cx + 11 * SS, cy + 11 * SS), fill=(46, 210, 100, 255))
lx = x0 + (B // 2) * SS
d.ellipse((lx - 9 * SS, cy - 9 * SS, lx + 9 * SS, cy + 9 * SS), fill=(12, 13, 16, 255))
layer = layer.resize((dw + 2 * pad, dh + 2 * pad), Image.LANCZOS)

# тінь
shadow = Image.new("L", (W, H), 0)
ImageDraw.Draw(shadow).rounded_rectangle((ox + 20, oy + 60, ox + dw - 20, oy + dh + 60), R, fill=170)
shadow = shadow.filter(ImageFilter.GaussianBlur(70))
base = bg.copy()
base.paste(Image.new("RGB", (W, H), (0, 0, 0)), (0, 0), shadow)
base = base.convert("RGBA")
base.alpha_composite(layer, (ox - pad, oy - pad))
# блік зверху на бортику
gl = Image.new("L", (dw, dh), 0)
ImageDraw.Draw(gl).rounded_rectangle((0, 0, dw - 1, dh - 1), R, fill=18)
gl = Image.composite(gl, Image.new("L", (dw, dh), 0), Image.linear_gradient("L").rotate(180).resize((dw, dh)))
base.paste(Image.new("RGBA", (dw, dh), (255, 255, 255, 255)), (ox, oy), gl)

for f in sorted(glob.glob(os.path.join(raw, "*.ppm"))):
    name = os.path.splitext(os.path.basename(f))[0]
    src = Image.open(f).convert("RGB")
    src.resize((2560, 1920), Image.LANCZOS).save(os.path.join(out, "screen", name + "_2560x1920.png"), optimize=True)
    im = base.copy()
    im.paste(src.resize((sw, sh), Image.LANCZOS), (ox + B, oy + B))
    im.convert("RGB").save(os.path.join(out, "slides", name + "_slide_3840x2160.png"), optimize=True)
    print(name)
