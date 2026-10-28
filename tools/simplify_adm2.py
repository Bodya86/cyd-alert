#!/usr/bin/env python3
"""Одноразова підготовка tools/ukraine_adm2.geojson (райони України, спрощені) з OCHA COD-AB.

Джерело: HDX «Ukraine – Subnational Administrative Boundaries» (cod-ab-ukr, v05, valid_on 2025-09-01),
ліцензія CC BY 3.0 IGO: https://data.humdata.org/dataset/cod-ab-ukr  (файл ukr_admin2.geojson з geojson.zip)
UID районів alerts.in.ua: бібліотека alerts_in_ua (MIT), LocationUidResolver.

  python3 tools/simplify_adm2.py /path/to/ukr_admin2.geojson [/path/to/location_uid_resolver.py]
"""
import json, math, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOL = 0.006   # градусів (~0.5 км) — на екрані 1 px ≈ 5 км
# порядок областей alerts.in.ua IoT (= data/locations.json)
ORDER = ["Автономна Республіка Крим", "Волинська", "Вінницька", "Дніпропетровська", "Донецька", "Житомирська",
         "Закарпатська", "Запорізька", "Івано-Франківська", "Київ", "Київська", "Кіровоградська", "Луганська",
         "Львівська", "Миколаївська", "Одеська", "Полтавська", "Рівненська", "Севастополь", "Сумська",
         "Тернопільська", "Харківська", "Херсонська", "Хмельницька", "Черкаська", "Чернівецька", "Чернігівська"]


def rdp(pts, eps):
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts); keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]; bx, by = pts[b]
        dx, dy = bx - ax, by - ay; L = math.hypot(dx, dy) or 1e-12
        best, bi = -1, -1
        for i in range(a + 1, b):
            px, py = pts[i]
            d = abs(dy * px - dx * py + bx * ay - by * ax) / L
            if d > best: best, bi = d, i
        if best > eps:
            keep[bi] = True; stack += [(a, bi), (bi, b)]
    return [p for p, k in zip(pts, keep) if k]


def norm(s):
    return re.sub(r"[ʼ’'`]", "'", s).replace(" район", "").strip()


def load_uids(path):
    if not path or not os.path.exists(path):
        return {}
    src = open(path, encoding="utf-8").read().split("self.location_to_uid")[0]
    cur, out = None, {}
    for uid, name in re.findall(r'(\d+):\s*"([^"]+)"', src):
        if "область" in name or name.startswith("м. ") or "Крим" in name:
            cur = name.replace(" область", ""); continue
        out[(cur, norm(name))] = int(uid)
    return out


def main():
    src = sys.argv[1]
    uids = load_uids(sys.argv[2] if len(sys.argv) > 2 else "")
    gj = json.load(open(src, encoding="utf-8"))
    feats = []
    for f in gj["features"]:
        p = f["properties"]
        ob = p["adm1_name1"]; idx = ORDER.index(ob)
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out = []
        for poly in polys:
            r0 = [(x, y) for x, y in poly[0]]
            # замкнене кільце: ділимо на дві половини по найдальшій від першої точці
            far = max(range(len(r0)), key=lambda i: (r0[i][0] - r0[0][0]) ** 2 + (r0[i][1] - r0[0][1]) ** 2)
            ring = rdp(r0[:far + 1], TOL)[:-1] + rdp(r0[far:], TOL)
            if len(ring) >= 4:
                out.append([[[round(x, 3), round(y, 3)] for x, y in ring]])
        name = p["adm2_name1"]
        feats.append({"type": "Feature", "properties": {
            "name": name, "name_en": p["adm2_name"], "pcode": p["adm2_pcode"], "oblast": idx,
            "uid": uids.get((ob, norm(name)), 0)},
            "geometry": {"type": "MultiPolygon", "coordinates": out}})
    feats.sort(key=lambda f: (f["properties"]["oblast"], f["properties"]["name"]))
    res = {"type": "FeatureCollection",
           "_source": "OCHA COD-AB Ukraine (HDX cod-ab-ukr v05), CC BY 3.0 IGO; simplified for CYD Alert",
           "features": feats}
    out = os.path.join(HERE, "ukraine_adm2.geojson")
    json.dump(res, open(out, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(len(feats), "features,", os.path.getsize(out) // 1024, "KB; with uid:", sum(1 for f in feats if f["properties"]["uid"]))


if __name__ == "__main__":
    main()
