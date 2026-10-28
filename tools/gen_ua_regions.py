#!/usr/bin/env python3
"""ID регіонів ukrainealarm.com (їх використовують api.ukrainealarm.com і JAAM) → індекси районів карти
+ газетир (координати громад/міст) для розбору Telegram-постів і моделі траєкторій.

Вхід:  tools/src/uaapi.json        — дерево регіонів api.ukrainealarm.com (/api/v3/regions, копія з
                                     github.com/J-A-A-M/ukraine_alarm_map, GPL-3.0; самі ID — факти)
       tools/src/jaam_regions.json — центри міст/районів того ж репозиторію
       tools/ukraine_adm2.geojson  — спрощені межі районів (OCHA COD-AB)
       data/locations.json, data/rules.json
Вихід: data/ua_regions.json  {"<id>": [region_idx|-1, oblast_idx, kind(0 state,1 district,2 community)]}
       data/gazetteer.json   [[назва_нормалізована, lat, lon, oblast_idx, region_idx], ...]
"""
import json, math, os, re

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
D = lambda *p: os.path.join(ROOT, *p)


def norm(s):
    s = (s or "").lower().replace("’", "'").replace("ʼ", "'").replace("`", "'").replace("ё", "е")
    return re.sub(r"\s+", " ", s).strip()


RENAMED = {"володимир-волинський": "володимирський", "сєвєродонецький": "сіверськодонецький",
           "червоноградський": "шептицький", "новомосковський": "самарівський", "красноградський": "берестинський"}


def stem_raion(s):
    s = norm(s).replace(" район", "").replace("район", "").strip()
    return RENAMED.get(s, s)


def main():
    loc = json.load(open(D("data", "locations.json"), encoding="utf-8"))
    regions = json.load(open(D("data", "regions.json"), encoding="utf-8"))
    geo = json.load(open(D("tools", "ukraine_adm2.geojson"), encoding="utf-8"))
    ua = json.load(open(D("tools", "src", "uaapi.json"), encoding="utf-8"))
    jr = json.load(open(D("tools", "src", "jaam_regions.json"), encoding="utf-8"))

    # назва області → індекс (порядок alerts.in.ua IoT)
    def obl_key(n):
        n = norm(n).replace("область", "").replace("обл.", "").replace("м. ", "").strip()
        return n.split()[0] if n else n
    obl_idx = {}
    for i, o in enumerate(loc["oblasts"]):
        obl_idx[obl_key(o["name"])] = i
        if o.get("ubilling"):
            obl_idx[obl_key(o["ubilling"])] = i
    obl_idx.setdefault("автономна", 0)
    obl_idx.setdefault("київ", next(i for i, o in enumerate(loc["oblasts"]) if norm(o["name"]).startswith("м. київ") or norm(o["name"]) == "київ"))

    def find_obl(name):
        k = obl_key(name)
        if norm(name).startswith("м. київ"):
            return obl_idx["київ"]
        if k in obl_idx:
            return obl_idx[k]
        for kk, v in obl_idx.items():
            if kk[:6] == k[:6]:
                return v
        return -1

    reg_idx = {}
    for i, r in enumerate(regions):
        reg_idx[(r["oblast"], stem_raion(r["name"]))] = i

    out, miss = {}, []
    def walk(node, ob, parent_reg):
        t = node["regionType"]
        rid = node["regionId"]
        if t == "State":
            ob = find_obl(node["regionName"])
            ri = -1
            if norm(node["regionName"]).startswith("м. київ"):
                ri = next((i for i, r in enumerate(regions) if r["oblast"] == ob), -1)
            out[rid] = [ri, ob, 0]
            pr = ri
        elif t == "District":
            ri = reg_idx.get((ob, stem_raion(node["regionName"])), -1)
            if ri < 0:
                miss.append(node["regionName"])
            out[rid] = [ri, ob, 1]
            pr = ri
        else:
            out[rid] = [parent_reg, ob, 2]
            pr = parent_reg
        for c in node.get("regionChildIds", []) or []:
            walk(c, ob, pr)
    for s in ua["states"]:
        walk(s, -1, -1)

    # --- газетир ---
    polys = []
    for f in geo["features"]:
        g = f["geometry"]
        rings = [p[0] for p in g["coordinates"]] if g["type"] == "MultiPolygon" else [g["coordinates"][0]]
        polys.append(rings)

    def pip(lon, lat, ring):
        ins = False
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]; xj, yj = ring[j]
            if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi + 1e-12) + xi:
                ins = not ins
            j = i
        return ins

    def region_at(lat, lon):
        for i, rings in enumerate(polys):
            if any(pip(lon, lat, r) for r in rings):
                return i
        return -1

    gaz = {}
    def add(name, lat, lon, ob=-1):
        n = norm(name)
        n = re.sub(r"^(м\.|смт|с\.|селище|село|місто)\s*", "", n)
        n = re.sub(r"\s*\(.*\)$", "", n).strip()
        if len(n) < 3 or n in gaz:
            return
        ri = region_at(lat, lon)
        if ob < 0 and ri >= 0:
            ob = regions[ri]["oblast"]
        gaz[n] = [n, round(lat, 4), round(lon, 4), ob, ri]

    for c in loc["cities"]:
        add(c["name"], c["lat"], c["lon"], c["oblast"])
    for k, v in jr.items():
        if not k.endswith("-CITY") or not v.get("location"):
            continue
        add(v["name"], v["location"]["lat"], v["location"]["lon"])
    # населені пункти навколо міст з rules.json (азимут/відстань → координати)
    rules = json.load(open(D("data", "rules.json"), encoding="utf-8"))
    cities = {c["id"]: c for c in loc["cities"]}
    for cid, lst in rules.get("settlements", {}).items():
        c = cities.get(cid)
        if not c:
            continue
        for s in lst:
            b = math.radians(s["bearing"])
            lat = c["lat"] + s["km"] * math.cos(b) / 110.57
            lon = c["lon"] + s["km"] * math.sin(b) / (111.32 * math.cos(math.radians(c["lat"])))
            for kw in s["kw"]:
                n = norm(kw)
                if n not in gaz and len(n) >= 4:
                    gaz[n] = [n, round(lat, 4), round(lon, 4), -1, region_at(lat, lon)]
    # центри районів (за назвою «кременчуцьк» — для «в Кременчуцькому районі»)
    for i, rings in enumerate(polys):
        pts = [p for r in rings for p in r]
        lon = sum(p[0] for p in pts) / len(pts); lat = sum(p[1] for p in pts) / len(pts)
        st = stem_raion(regions[i]["name"])
        if len(st) > 5 and "зона" not in st:
            key = st[:-2] + "*р"   # маркер «район»: зіставляється з «<основа>… район…»
            gaz.setdefault(key, [key, round(lat, 4), round(lon, 4), regions[i]["oblast"], i])

    # центр і «радіус» кожного району (для слабких спостережень «тривога в районі»)
    rgeo = []
    for i, rings in enumerate(polys):
        pts = [p for r in rings for p in r]
        lon = sum(p[0] for p in pts) / len(pts); lat = sum(p[1] for p in pts) / len(pts)
        area = 0.0
        for r in rings:
            for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]):
                area += x1 * y2 - x2 * y1
        km2 = abs(area) / 2 * 111.32 * 110.57 * math.cos(math.radians(lat))
        rgeo.append([round(lat, 4), round(lon, 4), round(math.sqrt(km2 / math.pi), 1)])
    json.dump(rgeo, open(D("data", "region_geo.json"), "w"), separators=(",", ":"))
    json.dump(out, open(D("data", "ua_regions.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    json.dump(sorted(gaz.values()), open(D("data", "gazetteer.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    print(f"ua_regions: {len(out)} ids, districts unmatched {len(miss)}: {miss[:12]}")
    print(f"gazetteer: {len(gaz)} names")


if __name__ == "__main__":
    main()
