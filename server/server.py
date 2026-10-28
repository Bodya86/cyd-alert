#!/usr/bin/env python3
"""CYD Alert — сервер-компаньйон.

Читає Telegram-канали (t.me/s/<channel> або Telethon), аналізує пости тим самим алгоритмом, що й прошивка,
проксіює статус тривог (alerts.in.ua з токеном → ubilling.net.ua) і віддає JSON для ESP32.

  GET /api/state?oblast=16&city=kremenchuk&district=molod[&channel=kremen_sv]
  GET /api/posts?channel=kremen_sv[&city=..&district=..]
  GET /api/outage?oblast=16&group=3.1[&prov=poe]   — графік відключень (48 півгодинних клітинок на добу)
  GET /api/targets?city=kremenchuk[&district=..][&lat=..&lon=..][&R=15]
                             — цілі (частинковий фільтр), прогноз шляху, P(пролетить ≤ R км) за 5/15/30 хв, ETA
  GET /                      — дашборд у браузері

Змінні середовища: PORT (8080), ALERTS_TOKEN (alerts.in.ua), UKRAINEALARM_KEY (api.ukrainealarm.com),
                   JAAM=1|0 (WebSocket JAAM data_fusion, за замовчуванням увімкнено),
                   TARGETS_URL (необов'язково: ваш JSON-список точок цілей), TG_POLL (20 c), ALERTS_POLL (15 c),
                   TG_MODE=web|telethon (+ TG_API_ID, TG_API_HASH), RADIUS_KM (15)
Дані тривог: JAAM (jaam.net.ua) — потрібна атрибуція «Дані: JAAM (jaam.net.ua)».
"""
import json, math, os, sys, threading, time, traceback, urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import analyzer as an
import alerts as al
import outage
import tg_source
import tracker as tk
import jaam_ws

PORT = int(os.environ.get("PORT", "8080"))
ALERTS_TOKEN = os.environ.get("ALERTS_TOKEN", "")
TG_POLL = int(os.environ.get("TG_POLL", "20"))
ALERTS_POLL = int(os.environ.get("ALERTS_POLL", "15"))
TG_MODE = os.environ.get("TG_MODE", "web")
UKRAINEALARM_KEY = os.environ.get("UKRAINEALARM_KEY", "")
USE_JAAM = os.environ.get("JAAM", "1") != "0"
TARGETS_URL = os.environ.get("TARGETS_URL", "")
RADIUS_KM = float(os.environ.get("RADIUS_KM", "15"))

lock = threading.Lock()
channels = {}            # channel -> {"posts": [...], "at": ts, "err": str|None, "last_req": ts}
alerts = {"oblasts": "N" * 27, "regions": "." * len(al.REGIONS), "at": 0, "src": None, "err": None}
CITY_REGIONS = json.load(open(os.path.join(al.DATA, "city_regions.json"), encoding="utf-8"))
_telethon = None
UA_REGIONS = {int(k): v for k, v in json.load(open(os.path.join(al.DATA, "ua_regions.json"), encoding="utf-8")).items()}  # id → [region, oblast, kind]
REG_BY_OBLAST = {}
for _i, _r in enumerate(al.REGIONS):
    REG_BY_OBLAST.setdefault(_r["oblast"], []).append(_i)
jaam = jaam_ws.JaamState()
trk = tk.Tracker()
trk_lock = threading.Lock()
_prev_lvl = {}           # region idx → рівень (для alert_change)
_jaam_ev_seen = 0.0
CITY_BY_CHANNEL = {c["channel"].lower(): c for c in an.LOC["cities"] if c.get("channel")}
for _c in an.CHANNELS_CFG:          # data/channels.json: «домашнє» місто каналу (для трекера); national → None
    _home = an.CITIES.get(_c.get("home")) if _c.get("home") else None
    if _home:
        CITY_BY_CHANNEL[_c["id"].lower()] = _home
    elif _c["id"].lower() not in CITY_BY_CHANNEL:
        CITY_BY_CHANNEL[_c["id"].lower()] = None


def home_city(ch):
    """Місто-«дім» каналу для трекера. Для обласних каналів без home — обласний центр не вгадуємо (None)."""
    return CITY_BY_CHANNEL.get((ch or "").lower())


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ---------- тривоги ----------
def _get(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": tg_source.UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def regions_from_ids(items):
    """items: [(ukrainealarm regionId, level 1|2)] → (regions_str, oblasts_str).
    State → вся область; District → район; Community → район частково."""
    lvl = [0] * len(al.REGIONS); part = [True] * len(al.REGIONS); obl = ["N"] * 27
    for rid, L in items:
        ri, oi, kind = UA_REGIONS.get(rid, (-1, -1, -1))
        if kind == 0 and 0 <= oi < 27:
            for i in REG_BY_OBLAST.get(oi, []):
                lvl[i] = max(lvl[i], L); part[i] = False
            obl[oi] = "A"
        elif ri >= 0:
            if kind == 1:
                lvl[ri] = max(lvl[ri], L); part[ri] = False
            elif lvl[ri] == 0 or part[ri]:
                lvl[ri] = max(lvl[ri], L)
            if obl[oi] == "N":
                obl[oi] = "P"
    for o, idx in REG_BY_OBLAST.items():
        if 0 <= o < 27 and idx and all(lvl[i] and not part[i] for i in idx):
            obl[o] = "A"
    rs = "".join("." if not lvl[i] else (("R" if lvl[i] == 2 else "Y").lower() if part[i] else ("R" if lvl[i] == 2 else "Y"))
                 for i in range(len(al.REGIONS)))
    return rs, "".join(obl)


def jaam_level(flags):
    """Офіційний рівень JAAM: level_red, ракети, артобстріл/бої/хім/ядерна → червоний; інакше жовтий."""
    if flags & (1 << 12 | 1 << 6 | 1 << 1 | 1 << 2 | 1 << 3 | 1 << 4):
        return 2
    return 1 if flags else 0


def fetch_ukrainealarm():
    """api.ukrainealarm.com v3 з ключем розробника (Authorization: KEY)."""
    j = json.loads(_get("https://api.ukrainealarm.com/api/v3/alerts", {"Authorization": UKRAINEALARM_KEY,
                                                                      "Accept": "application/json"}))
    items = []
    for reg in j if isinstance(j, list) else j.get("alerts", []):
        rid = int(reg.get("regionId") or 0)
        acts = reg.get("activeAlerts") or []
        if not acts:
            continue
        L = 1
        for a in acts:
            t = str(a.get("type", "")).upper()
            if t in ("ARTILLERY", "URBAN_FIGHTS", "CHEMICAL", "NUCLEAR"):
                L = 2
        for lv in reg.get("activeAlertLevels") or reg.get("ActiveAlertLevels") or []:
            txt = json.dumps(lv, ensure_ascii=False).lower()
            if "red" in txt or "missile" in txt or "ballistic" in txt or "ракет" in txt:
                L = 2
        items.append((rid, L))
    return items


def fetch_alerts():
    """→ (oblasts, regions, src). Пріоритет: ukrainealarm (ключ) → JAAM WS → alerts.in.ua (токен) → ubilling."""
    if UKRAINEALARM_KEY:
        try:
            rs, ob = regions_from_ids(fetch_ukrainealarm())
            return ob, rs, "ukrainealarm"
        except Exception as e:
            log("ukrainealarm:", e)
    if USE_JAAM:
        a, _, upd = jaam.snapshot()
        if jaam.connected or time.time() - upd < 180:
            if upd:
                rs, ob = regions_from_ids([(rid, jaam_level(f)) for rid, f in a.items()])
                return ob, rs, "jaam"
    if ALERTS_TOKEN:
        try:
            j = json.loads(_get("https://api.alerts.in.ua/v1/alerts/active.json",
                                {"Authorization": f"Bearer {ALERTS_TOKEN}"}))
            rs, ob = al.regions_from_active(j.get("alerts", j if isinstance(j, list) else []))
            return ob, rs, "alerts.in.ua"
        except Exception as e:
            log("alerts.in.ua:", e)
    data = json.loads(_get("https://ubilling.net.ua/aerialalerts/"))
    states = data.get("states", {})
    out = []
    for ob in an.LOC["oblasts"]:
        st = states.get(ob.get("ubilling") or "", None)
        out.append("A" if st and st.get("alertnow") else ("N" if st is not None else "A" if ob["short"] == "Крим" else "N"))
    ob = "".join(out)
    return ob, al.regions_from_oblasts(ob), "ubilling"


def alerts_loop():
    while True:
        try:
            ob, rs, src = fetch_alerts()
            with lock:
                alerts.update(oblasts=ob, regions=rs, at=time.time(), src=src, err=None)
            feed_tracker_alerts(rs)
        except Exception as e:
            with lock:
                alerts["err"] = str(e)
            log("alerts error:", e)
        time.sleep(ALERTS_POLL)


# ---------- злиття даних у трекер ----------
def feed_tracker_alerts(rs):
    """Зміни статусу районів і разові події JAAM → слабкі спостереження трекера."""
    global _jaam_ev_seen
    now = time.time()
    with trk_lock:
        for i, c in enumerate(rs):
            L = 0 if c == "." else 1
            if _prev_lvl and _prev_lvl.get(i, 0) != L:
                trk.alert_change(i, L > 0, now)
            _prev_lvl[i] = L
        if USE_JAAM:
            _, evs, _ = jaam.snapshot()
            for (t0, rid, types) in evs:
                if t0 <= _jaam_ev_seen:
                    continue
                ri, oi, kind = UA_REGIONS.get(rid, (-1, -1, -1))
                if ri >= 0:
                    trk.add_region_event(ri, types, t0)
                elif "ballistic" in types and oi >= 0:
                    trk.events.append((t0, "ballistic", -1, oi, None))
            if evs:
                _jaam_ev_seen = max(_jaam_ev_seen, max(e[0] for e in evs))


def feed_tracker_posts(ch, posts):
    city = home_city(ch)
    now = time.time()
    with trk_lock:
        for p in sorted(posts, key=lambda x: x.get("ts", 0)):
            ts = p.get("ts") or now
            if now - ts > 40 * 60:           # старі пости не створюють цілей
                trk.seen_posts.add(f"{ch}/{p.get('id')}")
                continue
            try:
                trk.add_post(p.get("text", ""), ts, city, src=f"tg:{ch}", post_id=f"{ch}/{p.get('id')}")
            except Exception:  # noqa: BLE001
                traceback.print_exc()


def targets_loop():
    """TARGETS_URL: ваш власний JSON [{type, lat, lon, heading?, ts?, sigma?}] — сильні спостереження положення."""
    while True:
        try:
            j = json.loads(_get(TARGETS_URL))
            pts = j if isinstance(j, list) else j.get("targets", [])
            with trk_lock:
                for t in pts:
                    typ = t.get("type") if t.get("type") in tk.TYPES else "unknown"
                    cue = dict(type=typ, count=int(t.get("count", 1)), kill=False, origin=[], heading=[],
                               pos=[dict(name="ext", lat=float(t["lat"]), lon=float(t["lon"]), sigma=float(t.get("sigma", 3)),
                                         region=-1, oblast=-1)])
                    if t.get("heading") is not None:
                        b = float(t["heading"])
                        la, lo = tk.Proj(float(t["lat"]), float(t["lon"])).ll(80 * math.sin(math.radians(b)), 80 * math.cos(math.radians(b)))
                        cue["heading"] = [dict(name="hd", lat=la, lon=lo, sigma=5, region=-1, oblast=-1)]
                    trk.apply_cues(cue, float(t.get("ts") or time.time()), src="ext")
        except Exception as e:
            log("targets:", e)
        time.sleep(max(15, ALERTS_POLL))


def user_point(q):
    city = q.get("city") or None
    cdef = an.CITIES.get(city) if city else None
    if q.get("lat") and q.get("lon"):
        return float(q["lat"]), float(q["lon"]), cdef
    if not cdef:
        return None, None, None
    lat, lon = cdef["lat"], cdef["lon"]
    d = next((d for d in cdef.get("districts", []) if d["id"] == q.get("district")), None)
    if d and d.get("sector", -1) >= 0:
        lat, lon = tk.Proj(lat, lon).ll(3 * math.sin(math.radians(d["sector"])), 3 * math.cos(math.radians(d["sector"])))
    return lat, lon, cdef


def assess(q):
    lat, lon, cdef = user_point(q)
    if lat is None:
        return [], tk.threat_summary([]), None
    R = float(q.get("R") or RADIUS_KM)
    region = CITY_REGIONS.get(cdef["id"]) if cdef else None
    with trk_lock:
        tg = trk.assess(lat, lon, time.time(), R, user_region=region, user_oblast=(cdef or {}).get("oblast"))
    return tg, tk.threat_summary(tg), (lat, lon, R)


def targets_api(q):
    tg, summ, u = assess(q)
    _, _, upd = jaam.snapshot()
    return {"targets": tg, "summary": summ, "user": dict(zip(("lat", "lon", "R"), u)) if u else None,
            "sources": {"jaam": jaam.connected, "jaam_at": int(upd), "ukrainealarm": bool(UKRAINEALARM_KEY),
                        "targets_url": bool(TARGETS_URL), "tracks": len(trk.tracks)},
            "attribution": "Дані: JAAM (jaam.net.ua)" if USE_JAAM else ""}


# ---------- Telegram ----------
def fetch_channel(ch):
    global _telethon
    if TG_MODE == "telethon":
        if _telethon is None:
            _telethon = tg_source.TelethonSource()
        return _telethon.fetch(ch)
    return tg_source.fetch_web(ch)


def tg_loop():
    while True:
        with lock:
            # опитуємо канали, які запитували за останню годину
            active = [c for c, v in channels.items() if time.time() - v["last_req"] < 3600]
        for ch in active:
            try:
                posts = fetch_channel(ch)
                with lock:
                    channels[ch].update(posts=posts, at=time.time(), err=None)
                feed_tracker_posts(ch, posts)
            except Exception as e:
                with lock:
                    channels[ch]["err"] = str(e)
                log(f"tg {ch}:", e)
        time.sleep(TG_POLL)


def get_posts(ch):
    with lock:
        c = channels.setdefault(ch, {"posts": [], "at": 0, "err": None, "last_req": 0})
        c["last_req"] = time.time()
        fresh = time.time() - c["at"] < TG_POLL * 3
    if not fresh:  # перший запит — тягнемо одразу
        try:
            posts = fetch_channel(ch)
            with lock:
                c.update(posts=posts, at=time.time(), err=None)
            feed_tracker_posts(ch, posts)
        except Exception as e:
            with lock:
                c["err"] = str(e)
    with lock:
        return list(c["posts"]), c["at"], c["err"]


def channels_for(q):
    """?channels=a,b,c — явний список; інакше типові для міста/області + ?channel= (власний)."""
    city = q.get("city") or None
    oblast = int(q["oblast"]) if q.get("oblast", "").lstrip("-").isdigit() else None
    if q.get("channels"):
        chs = [c.strip().lstrip("@") for c in q["channels"].split(",") if c.strip()]
    else:
        chs = an.default_channels(city, oblast)
    if q.get("channel"):
        extra = q["channel"].strip().lstrip("@")
        if extra.lower() not in [c.lower() for c in chs]:
            chs.insert(0, extra)
    return chs[:24]


def get_many(chs):
    """Пости кількох каналів паралельно → ({ch: posts}, найстаріший at, {ch: err})."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        res = list(ex.map(get_posts, chs))
    feeds = {ch: r[0] for ch, r in zip(chs, res)}
    errs = {ch: r[2] for ch, r in zip(chs, res) if r[2]}
    ats = [r[1] for r in res if r[1]]
    return feeds, (min(ats) if ats else 0), errs


def tg_summary(feeds, city, district, now=None):
    lvl, best, ev, clusters = an.analyze_multi(feeds, city, district, now)
    bc = None
    if best:
        bc = next((c for c in clusters if c.get("best") and c["best"][0].get("id") == best.get("id")
                   and c["best"][2] == best.get("channel")), None)
    return lvl, best, ev, {"agree": (bc or {}).get("n", 0), "conf": (bc or {}).get("conf", 0),
                           "events": [{"type": c["key"][0], "place": c["key"][1], "channels": sorted(c["channels"]),
                                       "conf": c["conf"], "ts": int(c["ts"])}
                                      for c in sorted(clusters, key=lambda c: -c["ts"]) if c.get("best")][:8]}


# ---------- API ----------
def state(q):
    city = q.get("city") or None
    district = q.get("district") or None
    cdef = an.CITIES.get(city) if city else None
    oblast = int(q["oblast"]) if q.get("oblast", "").lstrip("-").isdigit() else (cdef or {}).get("oblast", -1)

    chs = channels_for(q)
    feeds, at, errs = get_many(chs) if chs else ({}, 0, {})
    lvl, best, ev, multi = tg_summary(feeds, city, district)
    ch = (best or {}).get("channel") or (chs[0] if chs else "")
    err = "; ".join(f"{k}: {v}" for k, v in errs.items()) or None
    with lock:
        obl = alerts["oblasts"]
        rs = alerts["regions"]
        alerts_src = alerts["src"]
    region = CITY_REGIONS.get(city) if city else None
    if region is not None and region >= 0:
        off_lvl, off_part = al.region_status(rs, region)
    else:
        c = obl[oblast] if 0 <= oblast < len(obl) else "N"
        off_lvl, off_part = (1 if c in "ARYP" else 0), c == "P"
    official = off_lvl > 0
    # Та сама логіка, що computeFinalLevel() у прошивці:
    #  офіційна тривога у вашому районі → max(тип тривоги, Telegram); без неї — Telegram лише при балі >= 5
    final = max(off_lvl, lvl) if official else (lvl if ev.score >= 5 else an.GREEN)
    tg, summ, _ = assess(q)
    final = max(final, summ["level"])
    compact = []
    for d in tg[:6]:
        if d["lat"] is None:
            continue
        pts = [p for p in d["path"] if p][1:]
        pts = [pts[i] for i in (0, 2, 4) if i < len(pts)] if len(pts) >= 5 else pts[:3]   # 5, 15, 30 хв
        compact.append({"c": d["ch"], "la": d["lat"], "lo": d["lon"], "hd": d["heading"] if d["heading_conf"] >= 0.3 else -1,
                        "p": round(d["p"][30] * 100), "e": round(d["eta_min"]) if d["eta_min"] is not None else -1,
                        "n": d["count"], "pt": [[p[0], p[1], round(min(p[2], 99))] for p in pts]})
    balli = [d for d in tg if d["lat"] is None]
    return {
        "level": an.LEVEL_NAMES[lvl],          # рівень за Telegram (прошивка сама комбінує з офіційним)
        "final": an.LEVEL_NAMES[final],
        "score": ev.score,
        "text": (best or {}).get("text", "")[:350],
        "ts": int((best or {}).get("ts", 0)),
        "post": (best or {}).get("id", 0),
        "reason": ev.reason,
        "channel": ch,
        "channels": chs,
        "agree": multi["agree"],
        "conf": multi["conf"],
        "events": multi["events"],
        "tg_at": int(at),
        "tg_err": err,
        "oblasts": obl,
        "oblasts_src": alerts_src,
        "official": official,
        "official_level": an.LEVEL_NAMES[off_lvl],
        "official_partial": off_part,
        "kind": ev.kind,
        "near": ev.near,
        "region": region,
        "region_name": al.REGIONS[region]["name"] if region is not None and region >= 0 else None,
        "regions": rs,
        "p_near": round(summ["p_any"][30] * 100),
        "p_drone": round(summ["p_drone"][30] * 100),
        "p_missile": round(summ["p_missile"][30] * 100),
        "p_near15": round(summ["p_any"][15] * 100),
        "eta": round(summ["eta_min"]) if summ["eta_min"] is not None else -1,
        "tlevel": an.LEVEL_NAMES[summ["level"]],
        "tgt": compact,
        "ballistic": round(max([d["p"][5] for d in balli], default=0) * 100),
    }


def eval_posts(ch, posts, city, district):
    remote = not an.channel_is_local(ch, city)
    out = []
    for p in posts:
        e = an.analyze_post(p["text"], city, district, remote)
        out.append({**p, "channel": ch, "level": an.LEVEL_NAMES[e.level], "score": e.score, "kind": e.kind, "near": e.near,
                    "reason": e.reason, "ignored": e.ignored, "clear": e.clear, "local": e.local})
    return out


def posts_api(q):
    """?channel=x — один канал; без нього — стрічка всіх типових каналів (новіші зверху після сортування клієнтом)."""
    city, district = q.get("city") or None, q.get("district") or None
    if q.get("channel") and not q.get("channels"):
        ch = q["channel"]
        posts, at, err = get_posts(ch)
        return {"channel": ch, "at": int(at), "err": err, "posts": eval_posts(ch, posts, city, district)}
    chs = channels_for(q)
    feeds, at, errs = get_many(chs)
    out = []
    for ch, posts in feeds.items():
        out += eval_posts(ch, posts[-8:], city, district)
    out.sort(key=lambda p: p.get("ts") or 0)
    return {"channel": ",".join(chs), "channels": chs, "at": int(at), "err": errs or None, "posts": out[-60:]}


DASH = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>CYD Alert</title><style>
body{background:#0d1117;color:#e6edf3;font:15px system-ui;margin:0;padding:20px;max-width:760px;margin:auto}
.card{background:#161b22;border-radius:14px;padding:16px;margin:12px 0;border-left:6px solid #3fb950}
.yellow{border-color:#f2c94c}.red{border-color:#ff4d4f}.mut{color:#8b949e;font-size:13px}
h1{font-size:22px}select,input{background:#161b22;color:#e6edf3;border:1px solid #30363d;border-radius:8px;padding:6px}
</style><h1>🛡 CYD Alert — сервер</h1>
<div>Місто <select id=c></select> Район <select id=d></select></div><div id=st></div><div id=tg></div><div id=out></div><div id=ps></div>
<script>
const L=%LOC%;const c=document.getElementById('c'),d=document.getElementById('d');
L.cities.forEach(x=>c.add(new Option(x.name,x.id)));c.value='kremenchuk';
function fd(){d.innerHTML='';d.add(new Option('—',''));(L.cities.find(x=>x.id==c.value).districts||[]).forEach(x=>d.add(new Option(x.name,x.id)))}
c.onchange=()=>{fd();go()};d.onchange=go;fd();
const t=s=>s?new Date(s*1000).toLocaleTimeString('uk'):'—';
async function go(){const q=`city=${c.value}&district=${d.value}`;
 const s=await (await fetch('/api/state?'+q)).json();
 document.getElementById('st').innerHTML=`<div class="card ${s.final}"><b>${{green:'🟢 Чисто',yellow:'🟡 Дрони',red:'🔴 Ракети'}[s.final]}</b> (Telegram: ${s.level}${s.kind?' / '+s.kind:''}, бал ${s.score}${s.near?', поруч':''}${s.official?', офіційна тривога: '+s.official_level+(s.official_partial?' частково':'')+' · '+(s.region_name||''):''})<br>${s.reason||''}<p>${s.text||'—'}</p><span class=mut>${t(s.ts)} · @${s.channel}${s.agree>1?' · підтверджують '+s.agree+' канали':''} · ${(s.channels||[]).length} каналів · тривоги: ${s.oblasts_src||'—'}</span></div>`;
 const g=await (await fetch('/api/targets?'+q)).json();const S=g.summary;
 document.getElementById('tg').innerHTML=`<div class=card style="border-color:#58a6ff"><b>🎯 Цілі (${g.targets.length})</b> · P(≤${g.user?g.user.R:15} км, 30 хв): дрони ${Math.round(S.p_drone[30]*100)}%, ракети ${Math.round(S.p_missile[30]*100)}%${S.eta_min!=null?' · ETA '+Math.round(S.eta_min)+' хв':''}<br>`+
  g.targets.map(x=>`<span class=mut>${x.label}${x.count>1?' ×'+x.count:''} · ${x.dist_km!=null?x.dist_km+' км':'—'} · курс ${x.heading!=null?x.heading+'°':'—'} (${Math.round(x.heading_conf*100)}%) · ${x.speed} км/год · P5/15/30 ${[5,15,30].map(T=>Math.round(x.p[T]*100)).join('/')}% · ETA ${x.eta_min??'—'} · ${x.src.join(',')}</span>`).join('<br>')+`<div class=mut>${g.attribution}</div></div>`;
 const p=await (await fetch('/api/posts?'+q)).json();
 document.getElementById('ps').innerHTML=p.posts.slice().reverse().map(x=>`<div class="card ${x.level}"><span class=mut>@${x.channel} #${x.id} · ${t(x.ts)} · бал ${x.score} ${x.ignored?'· реклама':''}${x.clear?'· відбій':''} ${x.reason}</span><br>${x.text}</div>`).join('')}
const OC=['#2ea043','#5b6573','#f59e0b'];
async function outg(){const ci=L.cities.find(x=>x.id==c.value);const o=await (await fetch(`/api/outage?oblast=${ci.oblast}&group=${document.getElementById('g').value}`)).json();
 document.getElementById('ob').innerHTML=(o.days||[]).map(dd=>`<div class=mut>${dd.date}</div><div style="display:flex;gap:1px">${[...dd.cells].map(v=>`<span style="flex:1;height:14px;background:${OC[v]}"></span>`).join('')}</div>`).join('')||('— '+(o.err||''));
 document.getElementById('os').textContent=(o.src||'')+' '+(o.updated||'')}
document.getElementById('out').innerHTML=`<div class=card style="border-color:#5b6573"><b>⚡ Відключення</b> черга <select id=g>${%GROUPS%.map(x=>`<option>${x}</option>`).join('')}</select> <span id=os class=mut></span><div id=ob></div></div>`;
document.getElementById('g').onchange=outg;
go();outg();setInterval(go,15000);
</script>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/state":
                return self._send(200, json.dumps(state(q), ensure_ascii=False))
            if u.path == "/api/outage":
                ob = int(q.get("oblast", "16"))
                return self._send(200, json.dumps(outage.get_outage(ob, q.get("group", "1.1"), q.get("prov")),
                                                  ensure_ascii=False))
            if u.path == "/api/targets":
                return self._send(200, json.dumps(targets_api(q), ensure_ascii=False))
            if u.path == "/api/posts":
                return self._send(200, json.dumps(posts_api(q), ensure_ascii=False))
            if u.path in ("/", "/index.html"):
                loc = json.dumps({"cities": [{"id": c["id"], "name": c["name"], "oblast": c["oblast"], "districts": [
                    {"id": d["id"], "name": d["name"]} for d in c["districts"]]} for c in an.LOC["cities"]]}, ensure_ascii=False)
                return self._send(200, DASH.replace("%LOC%", loc).replace("%GROUPS%", json.dumps(outage.CFG["groups"])), "text/html; charset=utf-8")
            self._send(404, '{"error":"not found"}')
        except Exception as e:
            traceback.print_exc()
            self._send(500, json.dumps({"error": str(e)}))

    def log_message(self, fmt, *a):
        log(self.address_string(), fmt % a)


def main():
    if USE_JAAM:
        jaam_ws.start(jaam, log)
    if TARGETS_URL:
        threading.Thread(target=targets_loop, daemon=True).start()
    threading.Thread(target=alerts_loop, daemon=True).start()
    threading.Thread(target=tg_loop, daemon=True).start()
    log(f"CYD Alert server on :{PORT}  (tg={TG_MODE}, alerts={'ukrainealarm' if UKRAINEALARM_KEY else 'jaam' if USE_JAAM else 'alerts.in.ua' if ALERTS_TOKEN else 'ubilling'})")
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()


if __name__ == "__main__":
    main()
