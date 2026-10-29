#!/usr/bin/env python3
"""HD-растри мапи для симулятора (×K): ті самі проєкції/види, що в include/map_*.h, перерастровані з
tools/ukraine_adm2.geojson і tools/ne/ua_water.geojson у K разів детальніше. Межі — пропорційної товщини.
Вихід: build/hd/map_hd.h (лише для потрібних видів: Україна, obl16, c_kremenchuk або ті, що в argv)."""
import json, math, os, re, sys
import numpy as np
from PIL import ImageOps, Image, ImageDraw

K = int(sys.argv[1]) if len(sys.argv) > 1 else 12
WANT = sys.argv[2].split(",") if len(sys.argv) > 2 else ["obl16", "c_kremenchuk"]
HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.abspath(os.path.join(HERE, "../../.."))
TOOLS = os.path.join(ROOT, "tools")
W, H = 240, 162
BG, BORDER, RBORDER, WATER, RIVER = 255, 254, 253, 252, 251
COSLAT = math.cos(math.radians(48.5))
KYIV, SEVAS = 9, 18
gj = json.load(open(os.path.join(TOOLS, "ukraine_adm2.geojson"), encoding="utf-8"))
feats = gj["features"]; N = len(feats)
obl = np.array([f["properties"]["oblast"] for f in feats] + [-1] * (256 - N))
cityIdx = [i for i in range(N) if obl[i] in (KYIV, SEVAS)]
water = json.load(open(os.path.join(TOOLS, "ne", "ua_water.geojson"), encoding="utf-8"))["features"]
meta = open(os.path.join(ROOT, "include", "map_meta.h")).read()
mv = lambda n: float(re.search(r"#define MAP_PROJ_%s ([-\d.]+)f" % n, meta).group(1))
views = [(-1, "ua", (mv("MINX"), mv("MINY"), mv("SCALE"), mv("OFFX"), mv("OFFY"), 0, 0, W, H), 0)]
# растр виду може бути більшим за кадр (ex, ey, rw, rh — див. tools/gen_map_views.py)
for m in re.finditer(r"\{([-\d.]+)f, ([-\d.]+)f, ([-\d.]+)f, ([-\d.]+)f, ([-\d.]+)f, VO(\d+), VR\d+, VL\d+, \d+, (-?\d+), (-?\d+), (\d+), (\d+)\},\s*//\s*(\S+)",
                     open(os.path.join(ROOT, "include", "map_views.h"), encoding="utf-8").read()):
    if m.group(11) in WANT:
        views.append((int(m.group(6)), m.group(11), tuple(float(m.group(i)) for i in range(1, 6)) + tuple(int(m.group(i)) for i in range(7, 11)),
                      2 if m.group(11).startswith("c_") else 1))

def disk(r):
    return [(dy, dx) for dy in range(-r, r + 1) for dx in range(-r, r + 1) if dy * dy + dx * dx <= r * r and (dy or dx)]

def shift(a, dy, dx, fill):
    out = np.full_like(a, fill)
    ys = slice(max(dy, 0), a.shape[0] + min(dy, 0)); yd = slice(max(-dy, 0), a.shape[0] + min(-dy, 0))
    xs = slice(max(dx, 0), a.shape[1] + min(dx, 0)); xd = slice(max(-dx, 0), a.shape[1] + min(-dx, 0))
    out[yd, xd] = a[ys, xs]
    return out

def render(p, level):
    minx, miny, sc, ox, oy, ex, ey, rw, rh = p
    ox += ex; oy += ey
    Wk, Hk = rw * K, rh * K
    P = lambda lon, lat: (((lon * COSLAT - minx) * sc + ox) * K, ((-lat - miny) * sc + oy) * K)
    img = Image.new("L", (Wk, Hk), BG); d = ImageDraw.Draw(img)
    for i in sorted(range(N), key=lambda i: i in cityIdx):
        for poly in feats[i]["geometry"]["coordinates"]:
            pts = [P(lo, la) for lo, la in poly[0]]
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            if max(xs) < -50 or min(xs) > Wk + 50 or max(ys) < -50 or min(ys) > Hk + 50: continue
            d.polygon(pts, fill=i)
    # дірки всередині полігонів (внутрішні кільця анклавів, яких немає в даних) на 1x не растеризуються,
    # а на ×K дають «кружечки» з береговою межею — заливаємо їх сусідньою громадою
    m = Image.fromarray(np.where(np.array(img) == BG, 255, 0).astype(np.uint8))
    m = ImageOps.expand(m, 1, fill=255); ImageDraw.floodfill(m, (0, 0), 128)
    hole = np.array(m)[1:-1, 1:-1] == 255
    a = np.array(img)
    for _ in range(64):
        if not hole.any(): break
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb = shift(a, dy, dx, BG)
            f = hole & (nb != BG); a[f] = nb[f]; hole &= ~f
    land = a != BG
    # дрібні громади-анклави (міста обласного значення тощо) на 1x займають ~1 піксель і меж не мають —
    # тут теж без контуру, інакше на ×K вони стають помітними «кружечками»
    cnt = np.bincount(a.ravel(), minlength=256)
    tiny = [i for i in range(N) if 0 < cnt[i] < (4 * K) ** 2]
    noB = list(cityIdx) + tiny
    isc = np.isin(a, noB)
    px = a.copy()
    t = max(2, round(0.7 * K)); r = max(1, t // 2)
    rb = np.zeros_like(land); ob = np.zeros_like(land); coast = np.zeros_like(land)
    for dy, dx in disk(t):
        nb = shift(a, dy, dx, BG)
        coast |= land & (nb == BG)
    for dy, dx in disk(r):
        nb = shift(a, dy, dx, BG)
        inner = land & (nb != BG) & (nb != a) & ~isc & ~np.isin(nb, noB)
        dob = obl[a] != obl[nb]
        ob |= inner & dob; rb |= inner & ~dob
    px[rb] = RBORDER; px[ob | coast] = BORDER
    if level >= 1:
        wimg = Image.new("L", (Wk, Hk), 0); wd = ImageDraw.Draw(wimg)
        for f in water:
            if f["properties"]["kind"] != "lake": continue
            for poly in f["geometry"]["coordinates"]:
                wd.polygon([P(lo, la) for lo, la in poly[0]], fill=1)
        lake = (np.array(wimg) > 0) & land
        rimg = Image.new("L", (Wk, Hk), 0); rd = ImageDraw.Draw(rimg)
        for f in water:
            pr = f["properties"]
            if pr["kind"] != "river": continue
            major = pr.get("src") == "rivers_lake_centerlines"
            if not major and level < 2: continue
            wdt = 2 if (major and level == 2 and pr["name"] == "Dnipro") else 1
            for l in f["geometry"]["coordinates"]:
                rd.line([P(lo, la) for lo, la in l], fill=1, width=max(1, round(wdt * 0.75 * K)), joint="curve")
        river = (np.array(rimg) > 0) & land & ~lake
        px[lake] = WATER
        px[river & (px != BORDER)] = RIVER
    return px

def rle(px):
    runs, ofs = [], []
    for row in px:
        ofs.append(len(runs) // 2)
        ch = np.flatnonzero(np.diff(row.astype(np.int16))) + 1
        starts = np.concatenate(([0], ch)); ends = np.concatenate((ch, [len(row)]))
        for s, e in zip(starts, ends):
            v = int(row[s]); n = int(e - s)
            while n > 0:
                m = min(n, 255); runs += (m, v); n -= m
    ofs.append(len(runs) // 2)
    return runs, ofs

os.makedirs(os.path.join(HERE, "../build/hd"), exist_ok=True)
with open(os.path.join(HERE, "../build/hd/map_hd.h"), "w") as f:
    f.write("// AUTO-GENERATED by tools/sim/hd/gen_map_hd.py (simulator only)\n#pragma once\n#include <stdint.h>\n")
    f.write(f"#define HD_MAP_K {K}\nstruct HdMap {{ int view; const uint32_t* rows; const uint8_t* runs; }};\n")
    for k, (vi, name, p, level) in enumerate(views):
        px = render(p, level); runs, ofs = rle(px)
        Image.fromarray(px).save(os.path.join(HERE, f"../build/hd/map_{name}.png"))
        f.write(f"static const uint32_t HDR{k}[{len(ofs)}] = {{" + ",".join(map(str, ofs)) + "};\n")
        f.write(f"static const uint8_t HDU{k}[{len(runs)}] = {{" + ",".join(map(str, runs)) + "};\n")
        print(name, vi, len(runs) // 2, "runs")
    f.write(f"static const HdMap HD_MAPS[{len(views)}] = {{" + ",".join(f"{{{vi},HDR{k},HDU{k}}}" for k, (vi, *_ ) in enumerate(views)) + "};\n")
    f.write(f"#define HD_MAP_COUNT {len(views)}\n")
