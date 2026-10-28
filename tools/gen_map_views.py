#!/usr/bin/env python3
"""Генерує include/map_views.h: збільшені растри мапи для прошивки (тап по мапі — зум).

Рівні: 0 — вся Україна (include/map_data.h, tools/gen_map.py); 1 — область (для кожної з 27, Київ → Київщина,
Севастополь → Крим); 2 — околиці міста (радіус ~75 км) для кожного міста з data/locations.json.
Значення пікселя як у map_data.h + MAP_WATER (водосховища/озера) і MAP_RIVER (річки).
Підписи (міста, райцентри, села) розставляються тут же з уникненням накладань.

  pip install pillow numpy && python3 tools/gen_map_views.py
Геометрія: tools/ukraine_adm2.geojson (OCHA COD-AB, CC BY 3.0 IGO); вода: tools/ne/ua_water.geojson (Natural Earth, PD)
"""
import json, math, os, re
import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
W, H, SS = 240, 162, 3
BG, BORDER, RBORDER, WATER, RIVER = 255, 254, 253, 252, 251
COSLAT = math.cos(math.radians(48.5))
KYIV, SEVAS = 9, 18
AREA_KM = 75
FONT = ImageFont.truetype(os.path.join(HERE, "fonts", "Golos-500.ttf"), 12)   # = font_ua_12 у прошивці

gj = json.load(open(os.path.join(HERE, "ukraine_adm2.geojson"), encoding="utf-8"))
feats = gj["features"]; N = len(feats)
obl = [f["properties"]["oblast"] for f in feats]
cityIdx = {i for i in range(N) if obl[i] in (KYIV, SEVAS)}
water = json.load(open(os.path.join(HERE, "ne", "ua_water.geojson"), encoding="utf-8"))["features"]
loc = json.load(open(os.path.join(ROOT, "data", "locations.json"), encoding="utf-8"))
gaz = json.load(open(os.path.join(ROOT, "data", "gazetteer.json"), encoding="utf-8"))


def rings(f):
    return [poly[0] for poly in f["geometry"]["coordinates"]]


def proj(lon, lat):
    return lon * COSLAT, -lat


class View:
    def __init__(self, minlon, minlat, maxlon, maxlat, pad=2):
        x0, y0 = proj(minlon, maxlat); x1, y1 = proj(maxlon, minlat)
        self.minx, self.miny = x0, y0
        self.scale = min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0))
        self.offx = (W - (x1 - x0) * self.scale) / 2
        self.offy = (H - (y1 - y0) * self.scale) / 2

    def px(self, lon, lat, ss=1):
        x, y = proj(lon, lat)
        return ((x - self.minx) * self.scale + self.offx) * ss, ((y - self.miny) * self.scale + self.offy) * ss

    def km_px(self):
        return self.scale / 111.0


def render(v, level):
    img = Image.new("L", (W * SS, H * SS), BG); d = ImageDraw.Draw(img)
    for i in sorted(range(N), key=lambda i: i in cityIdx):
        for r in rings(feats[i]):
            pts = [v.px(lon, lat, SS) for lon, lat in r]
            xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
            if max(xs) < -50 or min(xs) > W * SS + 50 or max(ys) < -50 or min(ys) > H * SS + 50:
                continue
            d.polygon(pts, fill=i)
    a = np.array(img, dtype=np.uint8).reshape(H, SS, W, SS).transpose(0, 2, 1, 3).reshape(H, W, SS * SS)
    cnt = np.stack([(a == a[:, :, k:k + 1]).sum(axis=2) for k in range(SS * SS)], axis=2)
    small = np.take_along_axis(a, cnt.argmax(axis=2)[:, :, None], axis=2)[:, :, 0]
    # крихітні міста-області — поверх, якщо займають ≥ 3 субпікселі
    for c in cityIdx:
        m = (a == c).sum(axis=2) >= 3
        small[m] = c
    px = small.copy()
    o = np.array(obl + [-1] * (256 - N))
    for dy, dx in ((0, 1), (1, 0), (0, -1), (-1, 0)):
        nb = np.full_like(small, BG)
        if dx == 1: nb[:, :-1] = small[:, 1:]
        if dx == -1: nb[:, 1:] = small[:, :-1]
        if dy == 1: nb[:-1, :] = small[1:, :]
        if dy == -1: nb[1:, :] = small[:-1, :]
        land = small != BG
        diff = land & (nb != small)
        px[diff & (nb == BG)] = BORDER
        if (dy, dx) in ((0, 1), (1, 0)):
            inner = diff & (nb != BG)
            city = np.isin(small, list(cityIdx)) | np.isin(nb, list(cityIdx))
            ob = inner & ~city & (o[small] != o[np.minimum(nb, 255)])
            rb = inner & ~city & ~ob & (px != BORDER)
            px[ob] = BORDER
            px[rb & (px != BORDER)] = RBORDER
    # вода: озера/водосховища (≥ 4/9 субпікселів), річки — лінії
    wimg = Image.new("L", (W * SS, H * SS), 0); wd = ImageDraw.Draw(wimg)
    for f in water:
        if f["properties"]["kind"] != "lake": continue
        for poly in f["geometry"]["coordinates"]:
            wd.polygon([v.px(lon, lat, SS) for lon, lat in poly[0]], fill=1)
    wa = np.array(wimg).reshape(H, SS, W, SS).sum(axis=(1, 3))
    lake = (wa >= 4) & (small != BG)
    rimg = Image.new("L", (W, H), 0); rd = ImageDraw.Draw(rimg)
    for f in water:
        p = f["properties"]
        if p["kind"] != "river": continue
        major = p.get("src") == "rivers_lake_centerlines"
        if not major and level < 2: continue
        wdt = 2 if (major and level == 2 and p["name"] == "Dnipro") else 1
        for l in f["geometry"]["coordinates"]:
            rd.line([v.px(lon, lat) for lon, lat in l], fill=1, width=wdt)
    river = (np.array(rimg) > 0) & (small != BG) & ~lake
    px[lake] = WATER
    px[river & (px != BORDER)] = RIVER
    return px


def disp(n):
    n = n.replace("*р", "")
    return "-".join(" ".join(w[:1].upper() + w[1:] for w in part.split(" ")) for part in n.split("-"))


FIX = {"козельщин": "козельщина", "манжел": "манжелія", "нова праг": "нова прага", "оболон": "оболонь", "піщан": "піщане",
       "семенівк": "семенівка", "кам'янські поток": "кам'янські потоки", "поток": "потоки", "глобин": "глобине"}
SKIP = {"водосховищ", "водохранилищ", "море", "плавн", "комсомольск", "горишни", "поток"}
UKR = re.compile("[іїєґ']")
RUS = re.compile("[ыэёъ]")


def gaz_places():
    """Населені пункти газетира з «людськими» назвами (з груп варіантів однієї точки — найповніша українська)."""
    groups = {}
    for n, la, lo, ob, rg in gaz:
        if "*" in n or n in SKIP or RUS.search(n): continue
        groups.setdefault((round(la, 2), round(lo, 2)), []).append(n)
    out = []
    for (la, lo), ns in groups.items():
        ns = sorted(ns, key=lambda s: (bool(UKR.search(s)), len(s)), reverse=True)
        n = FIX.get(ns[0], ns[0])
        if len(n) < 4: continue
        out.append((disp(n), la, lo))
    return out


PLACES = gaz_places()


class Labeler:
    def __init__(self):
        self.boxes = []; self.out = []

    def add(self, x, y, name, kind, tw=None):
        tw = tw or int(FONT.getlength(name)) + 2
        if any(abs(x - q[0]) < 8 and abs(y - q[1]) < 8 for q in self.out): return False   # та сама точка (Полтава/«Полтав»)
        for (lx, ly) in ((x + 4, y - 7), (x - 4 - tw, y - 7), (x - tw // 2, y + 3), (x - tw // 2, y - 17)):
            b = (lx, ly, lx + tw, ly + 13)
            if b[0] < 0 or b[1] < 0 or b[2] > W or b[3] > H: continue
            if any(not (b[2] < c[0] or b[0] > c[2] or b[3] < c[1] or b[1] > c[3]) for c in self.boxes): continue
            self.boxes.append(b); self.out.append((int(x), int(y), int(lx), int(ly), kind, name)); return True
        return False


def oblast_view(o):
    idx = [i for i in range(N) if obl[i] == o]
    pts = [c for i in idx for r in rings(feats[i]) for c in r]
    lo = [p[0] for p in pts]; la = [p[1] for p in pts]
    mx = (max(lo) - min(lo)) * 0.06; my = (max(la) - min(la)) * 0.06
    return View(min(lo) - mx, min(la) - my, max(lo) + mx, max(la) + my)


def area_view(lat, lon):
    dla = AREA_KM / 111.0; dlo = AREA_KM / (111.0 * math.cos(math.radians(lat)))
    return View(lon - dlo, lat - dla, lon + dlo, lat + dla)


def labels_for(v, level, o, city=None):
    L = Labeler()
    if city:   # місце користувача резервуємо заздалегідь
        x, y = v.px(city["lon"], city["lat"]); L.add(x, y, city["name"], 0)
    cs = sorted(loc["cities"], key=lambda c: (c["oblast"] != o, c["id"] != (city or {}).get("id")))
    for c in cs:
        x, y = v.px(c["lon"], c["lat"])
        if 0 <= x < W and 0 <= y < H and not (city and c["id"] == city["id"]):
            L.add(x, y, c["name"], 0)
    if level == 1:   # райцентри: населений пункт, з назви якого «виростає» назва району
        for i in range(N):
            if obl[i] != o: continue
            rn = feats[i]["properties"]["name"].lower()
            stem = re.sub(r"(ський|цький|зький|ький|ський район)$", "", rn)
            best = None
            for n, la, lo in PLACES:
                nl = n.lower()
                if len(stem) >= 4 and (nl.startswith(stem[:max(4, len(stem) - 1)]) or rn.startswith(nl[:max(4, len(nl) - 2)])):
                    best = (n, la, lo); break
            if best:
                x, y = v.px(best[2], best[1])
                if 0 <= x < W and 0 <= y < H: L.add(x, y, best[0], 1)
    else:
        for n, la, lo in sorted(PLACES, key=lambda p: (p[1] - city["lat"]) ** 2 + ((p[2] - city["lon"]) * 0.66) ** 2):
            x, y = v.px(lo, la)
            if 2 <= x < W - 2 and 2 <= y < H - 2 and not any(abs(x - q[0]) < 3 and abs(y - q[1]) < 3 for q in L.out):
                L.add(x, y, n, 2)
    return L.out


def rle(px):
    runs, ofs = [], []
    for y in range(H):
        ofs.append(len(runs)); x = 0
        row = px[y]
        while x < W:
            v = int(row[x]); n = 1
            while x + n < W and row[x + n] == v and n < 255: n += 1
            runs.append((n, v)); x += n
    ofs.append(len(runs))
    return runs, ofs


def esc(s): return s.replace("\\", "\\\\").replace('"', '\\"')


views = []
obl_view = [-1] * 27
for o in range(27):
    if o in (KYIV, SEVAS): continue
    v = oblast_view(o)
    views.append(("obl%d" % o, v, render(v, 1), labels_for(v, 1, o)))
    obl_view[o] = len(views) - 1
obl_view[KYIV] = obl_view[10]; obl_view[SEVAS] = obl_view[0]
city_view = []
for c in loc["cities"]:
    v = area_view(c["lat"], c["lon"])
    views.append(("c_" + c["id"], v, render(v, 2), labels_for(v, 2, c["oblast"], c)))
    city_view.append((c["id"], len(views) - 1))

total = 0
with open(os.path.join(ROOT, "include", "map_views.h"), "w", encoding="utf-8") as f:
    f.write("// AUTO-GENERATED by tools/gen_map_views.py - do not edit. Include ONLY from src/mapdata.cpp\n#pragma once\n#include \"map_meta.h\"\n")
    f.write("// Геометрія: OCHA COD-AB (CC BY 3.0 IGO); вода: Natural Earth (public domain)\n")
    for k, (name, v, px, labs) in enumerate(views):
        runs, ofs = rle(px)
        total += len(runs) * 2 + len(ofs) * 2 + len(labs) * 10
        f.write(f"static const uint16_t VO{k}[{len(ofs)}] = {{" + ",".join(map(str, ofs)) + "};\n")
        f.write(f"static const uint8_t VR{k}[{len(runs) * 2}] = {{" + ",".join(f"{n},{q}" for n, q in runs) + "};\n")
        f.write(f"static const MapLabel VL{k}[{max(1, len(labs))}] = {{" +
                (",".join(f'{{{x},{y},{lx},{ly},{kd},"{esc(n)}"}}' for x, y, lx, ly, kd, n in labs) or "{0,0,0,0,0,\"\"}") + "};\n")
    f.write(f"const MapView MAP_VIEWS[{len(views)}] = {{\n")
    for k, (name, v, px, labs) in enumerate(views):
        f.write(f"  {{{v.minx:.6f}f, {v.miny:.6f}f, {v.scale:.4f}f, {v.offx:.3f}f, {v.offy:.3f}f, VO{k}, VR{k}, VL{k}, {len(labs)}}},  // {name}\n")
    f.write("};\n")
    f.write("const int8_t MAP_OBL_VIEW[27] = {" + ",".join(map(str, obl_view)) + "};\n")
    f.write(f"const MapCityView MAP_CITY_VIEWS[{len(city_view)}] = {{" + ",".join(f'{{"{c}",{k}}}' for c, k in city_view) + "};\n")
with open(os.path.join(ROOT, "include", "map_views_meta.h"), "w", encoding="utf-8") as f:
    f.write("// AUTO-GENERATED by tools/gen_map_views.py - do not edit\n#pragma once\n#include <stdint.h>\n")
    f.write(f"#define MAP_WATER {WATER}\n#define MAP_RIVER {RIVER}\n#define MAP_VIEW_COUNT {len(views)}\n#define MAP_CITY_VIEW_COUNT {len(city_view)}\n")
    f.write("// kind: 0 — місто (з locations.json), 1 — райцентр, 2 — село/смт; (x,y) — точка, (lx,ly) — лівий верх підпису\n")
    f.write("struct MapLabel { int16_t x, y, lx, ly; uint8_t kind; const char* name; };\n")
    f.write("struct MapView { float minx, miny, scale, offx, offy; const uint16_t* rowOfs; const uint8_t* runs;\n"
            "                 const MapLabel* labels; uint8_t nlabels; };\n")
    f.write("struct MapCityView { const char* city; uint8_t view; };\n")
    f.write("extern const MapView MAP_VIEWS[MAP_VIEW_COUNT];\nextern const int8_t MAP_OBL_VIEW[27];\n"
            "extern const MapCityView MAP_CITY_VIEWS[MAP_CITY_VIEW_COUNT];\n")

# прев'ю
def hexc(h): return ((h >> 16) & 255, (h >> 8) & 255, h & 255)
os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
for name in ("obl16", "c_kremenchuk"):
    k = [i for i, vv in enumerate(views) if vv[0] == name][0]
    _, v, px, labs = views[k]
    o16 = 16
    im = Image.new("RGB", (W, H)); pp = im.load()
    for y in range(H):
        for x in range(W):
            q = int(px[y, x])
            pp[x, y] = hexc(0x0B0F14 if q == BG else 0x5E6C7E if q == BORDER else 0x18202A if q == RBORDER else
                            0x1F4E79 if q == WATER else 0x2D6A9F if q == RIVER else
                            (0xE5484D if feats[q]["properties"]["name"] == "Кременчуцький" else 0x2C3B4E if obl[q] == o16 else 0x1E2733))
    im = im.resize((W * 3, H * 3), Image.NEAREST); d = ImageDraw.Draw(im)
    F3 = ImageFont.truetype(os.path.join(HERE, "fonts", "Golos-500.ttf"), 36)
    for x, y, lx, ly, kd, n in labs:
        d.rectangle([x * 3 - 2, y * 3 - 2, x * 3 + 2, y * 3 + 2], fill=(230, 230, 230))
        d.text((lx * 3, ly * 3 - 3), n, fill=(230, 230, 230) if kd < 2 else (160, 170, 180), font=F3)
    im.save(os.path.join(ROOT, "docs", f"map_{name}.png"))
print("views:", len(views), "bytes:", total)
