"""Статус тривог по районах + тип загрози (дрони / ракети). Та сама логіка, що src/netsvc.cpp.

Формат рядка районів (довжина = кількість районів у data/regions.json, порядок як у прошивці):
  '.' — немає тривоги, 'Y'/'y' — дрони або невідомий тип (y — частково, лише громади району),
  'R'/'r' — ракети / балістика / КАБ / авіація / інші небезпеки (r — частково).
Рядок областей (27 символів): 'A' — тривога по всій області, 'P' — частково, 'N' — немає.
"""
import json, os

DATA = os.environ.get("CYD_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
REGIONS = json.load(open(os.path.join(DATA, "regions.json"), encoding="utf-8"))
LOC = json.load(open(os.path.join(DATA, "locations.json"), encoding="utf-8"))
OBLAST_UID = {ob["uid"]: i for i, ob in enumerate(LOC["oblasts"])}
REGION_BY_UID = {r["uid"]: i for i, r in enumerate(REGIONS) if r["uid"]}

RED_THREATS = {"ballistic_missiles", "cruise_missiles", "unspecified_missiles", "mig31k_departure",
               "guided_aerial_bombs", "tactic_aircraft_activity", "strategic_aircraft_activity"}
YELLOW_THREATS = {"drones", "unknown", "air_defense"}
KIND_NAMES = {0: "", 1: "Дрони / невідомо", 2: "Ракети / КАБ"}


def _norm(s):
    return (s or "").replace(" район", "").replace("’", "'").replace("ʼ", "'").strip()


def alert_level(a):
    """1 = жовтий (дрони/невідомо), 2 = червоний (ракети, КАБ, авіація, обстріли)."""
    t = a.get("alert_type") or "air_raid"
    if t != "air_raid":
        return 2   # artillery_shelling, urban_fights, chemical, nuclear
    threats = a.get("threats") or []
    if isinstance(threats, dict):
        threats = [threats]
    types = {(x.get("threat_type") if isinstance(x, dict) else str(x)) for x in threats}
    if a.get("threat_type"):
        types.add(a["threat_type"])
    return 2 if types & RED_THREATS else 1


def regions_from_active(alerts):
    """alerts: список з /v1/alerts/active.json → (regions_str, oblasts_str, threat_types_by_region)"""
    lvl = [0] * len(REGIONS)
    part = [False] * len(REGIONS)
    obl = ["N"] * 27

    def mark(i, L, partial):
        if partial:
            if lvl[i] == 0 or part[i]:
                lvl[i] = max(lvl[i], L); part[i] = True
        else:
            if part[i] or lvl[i] < L:
                lvl[i] = max(L, lvl[i] if not part[i] else 0); part[i] = False

    for a in alerts:
        if a.get("finished_at"):
            continue
        L = alert_level(a)
        lt = a.get("location_type")
        uid = int(a.get("location_uid") or 0)
        ob = OBLAST_UID.get(int(a.get("location_oblast_uid") or 0), OBLAST_UID.get(uid, -1))
        if lt == "oblast" or (uid in OBLAST_UID and lt != "raion"):
            o = OBLAST_UID.get(uid, ob)
            for i, r in enumerate(REGIONS):
                if r["oblast"] == o:
                    mark(i, L, False)
            if o >= 0:
                obl[o] = "A"
            continue
        if lt == "raion" and uid in REGION_BY_UID:
            mark(REGION_BY_UID[uid], L, False)
        else:   # громада / місто — частково по району
            rn = _norm(a.get("location_raion"))
            hit = [i for i, r in enumerate(REGIONS) if r["oblast"] == ob and _norm(r["name"]) == rn]
            if not hit:   # невідомий район — частково по всій області
                hit = [i for i, r in enumerate(REGIONS) if r["oblast"] == ob]
            for i in hit:
                mark(i, L, True)
        if 0 <= ob < 27 and obl[ob] == "N":
            obl[ob] = "P"
    # області, де всі райони під тривогою, вважаємо повними
    for o in range(27):
        idx = [i for i, r in enumerate(REGIONS) if r["oblast"] == o]
        if idx and all(lvl[i] and not part[i] for i in idx):
            obl[o] = "A"
    rs = "".join("." if not lvl[i] else ("R" if lvl[i] == 2 else "Y").lower() if part[i] else ("R" if lvl[i] == 2 else "Y")
                 for i in range(len(REGIONS)))
    return rs, "".join(obl)


def regions_from_oblasts(obl):
    """Тільки рівень областей (ubilling / IoT): усі райони області жовті (тип невідомий)."""
    out = []
    for r in REGIONS:
        c = obl[r["oblast"]] if r["oblast"] < len(obl) else "N"
        out.append("Y" if c in "AR" else "y" if c == "P" else ".")
    return "".join(out)


def region_status(rs, idx):
    """→ (level 0/1/2, partial)"""
    if not rs or idx is None or not (0 <= idx < len(rs)):
        return 0, False
    c = rs[idx]
    return (2 if c in "Rr" else 1 if c in "Yy" else 0), c in "ry"
