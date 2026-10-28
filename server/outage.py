"""Графіки погодинних відключень (ГПВ). Та сама нормалізація, що src/outage.cpp.

Результат: {"days": [{"date": "YYYY-MM-DD", "ts": <epoch півночі за Києвом>, "cells": "<48 символів>"}],
            "src": ..., "updated": "...", "group": "3.1"}
cells: 48 півгодинних клітинок доби, '0' — світло є, '1' — немає, '2' — можливе відключення.
"""
import json, os, time, datetime, urllib.request

DATA = os.environ.get("CYD_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
CFG = json.load(open(os.path.join(DATA, "outages.json"), encoding="utf-8"))
UA = "Mozilla/5.0 (CYD-Alert companion)"
_cache = {}   # url -> (ts, json)
CACHE_S = 600

GPV = {"yes": "00", "no": "11", "first": "10", "second": "01", "mfirst": "20", "msecond": "02", "maybe": "22",
       "mno": "22"}


def providers_for(oblast):
    return [p for p in CFG["providers"] if oblast in p["oblasts"]]


def _get_json(url):
    now = time.time()
    c = _cache.get(url)
    if c and now - c[0] < CACHE_S:
        return c[1]
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        j = json.loads(r.read().decode("utf-8"))
    _cache[url] = (now, j)
    return j


def _kyiv_date(ts):
    # Київ: UTC+2 / +3 (DST) — для дати півночі достатньо +3 год зсуву від UTC-півночі
    return datetime.datetime.utcfromtimestamp(int(ts) + 3 * 3600).strftime("%Y-%m-%d")


def parse_gpv(j, group):
    fact = j.get("fact") or {}
    data = fact.get("data") or {}
    days = []
    if isinstance(data, dict):
        for k in sorted(data, key=int):
            g = data[k].get("GPV" + group)
            if not g:
                continue
            cells = "".join(GPV.get(str(g.get(str(h), "yes")), "00") for h in range(1, 25))
            days.append({"date": _kyiv_date(k), "ts": int(k), "cells": cells})
    return {"days": days, "updated": fact.get("update") or j.get("lastUpdated") or ""}


def parse_yasno(j, group):
    g = j.get(group) or {}
    days = []
    for key in ("today", "tomorrow"):
        d = g.get(key)
        if not d or not d.get("date") or d.get("status") == "WaitingForSchedule":
            continue
        cells = ["0"] * 48
        status = d.get("status", "")
        for s in d.get("slots") or []:
            t = s.get("type")
            v = "1" if t == "Definite" else "0" if t == "NotPlanned" else "2"
            for i in range(int(s.get("start", 0)) // 30, min(48, (int(s.get("end", 0)) + 29) // 30)):
                cells[i] = v
        dt = datetime.datetime.fromisoformat(d["date"])
        days.append({"date": dt.strftime("%Y-%m-%d"), "ts": int(dt.timestamp()), "cells": "".join(cells),
                     "status": status})
    return {"days": days, "updated": g.get("updatedOn", "")}


def get_outage(oblast, group, prov_id=None):
    provs = providers_for(oblast)
    if prov_id:
        provs = [p for p in provs if p["id"] == prov_id] or provs
    err = None
    for p in provs:
        try:
            j = _get_json(p["url"])
            r = parse_yasno(j, group) if p["type"] == "yasno" else parse_gpv(j, group)
            if r["days"]:
                r.update(src=p["name"], prov=p["id"], group=group)
                return r
            err = "немає графіка для черги " + group
        except Exception as e:  # noqa
            err = f"{p['id']}: {e}"
    return {"days": [], "src": provs[0]["name"] if provs else None, "group": group,
            "err": err or "немає джерела для області"}
