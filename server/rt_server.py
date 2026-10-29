#!/usr/bin/env python3
"""Реалтайм-сервер CYD Alert (Lightsail / будь-який VPS): той самий конвеєр, що й live.py, але безперервно.

    python server/rt_server.py --out /var/lib/cyd-alert/public --port 8080

  * JAAM WebSocket — постійно; тривоги перечитуються кожні ALERT_S (2 с);
  * Telegram t.me/s: «швидкі» канали (міські + TG_FAST) кожні TG_FAST_S (5 с), усі — кожні TG_ALL_S (15 с);
  * після кожної ітерації — api/*.json (лише змінені файли) + push по WebSocket підписникам;
  * графіки відключень — окремий потік раз на 15 хв; новини — окремий потік (як у live.py);
  * HTTP: ті самі шляхи, що на GitHub Pages (/api/state/<city>/<district>.json …) і динамічні
    /api/state?city=&district=, /api/targets?city=, /api/outage?oblast=&group=, /api/posts?channel=;
  * /ws?city=..&district=..  (або ?path=api/…json) — сервер шле повний JSON стану при підключенні й при кожній зміні;
  * /api/health — живість, вік даних, затримки.
Секрети: /etc/cyd-alert/env (TG_BOT_TOKEN, TG_CHAT_ID, ALERTS_TOKEN, UKRAINEALARM_KEY) — через systemd EnvironmentFile.
"""
import argparse, asyncio, json, os, sys, threading, time, traceback, urllib.request
os.environ.setdefault("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4")
time.tzset()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JAAM", "1")
import build_static as bs     # noqa: E402
import server as srv          # noqa: E402
import tracker as tk          # noqa: E402
import analyzer as an         # noqa: E402
import outage                 # noqa: E402
import jaam_ws                # noqa: E402
import tg_notify              # noqa: E402
import accuracy               # noqa: E402
from live import NewsLoop     # noqa: E402
import fastforecast           # noqa: E402
FAST_NP = fastforecast.install()
from aiohttp import web, WSMsgType   # noqa: E402

log = bs.log
ALERT_S = float(os.environ.get("ALERT_S", "2"))
TG_FAST_S = float(os.environ.get("TG_FAST_S", "5"))
TG_ALL_S = float(os.environ.get("TG_ALL_S", "15"))
OUTAGE_S = float(os.environ.get("OUTAGE_S", "900"))
SEED_URL = os.environ.get("SEED_URL", "https://raw.githubusercontent.com/Bodya86/cyd-alert/data/")

# ---- запис файлів: атомарно (HTTP не бачить напівзаписаний JSON) + облік змінених шляхів (для WebSocket push)
_changed, _changed_lock = set(), threading.Lock()
_wlock = threading.Lock()


def _wjson(path, obj, force=False):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _wlock:
        if not force and isinstance(obj, dict) and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    old = json.load(f)
                if bs._stable(old) == bs._stable(obj) and time.time() - float(old.get("generated", 0)) < bs.HEARTBEAT_S:
                    bs._written["skipped"] += 1
                    return False
            except Exception:  # noqa: BLE001
                pass
        tmp = f"{path}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
        bs._written["n"] += 1
    with _changed_lock:
        _changed.add(os.path.abspath(path))
    return True


bs.wjson = _wjson

# ---- кеш аналізу постів: той самий текст аналізується десятки разів (для кожного міста/району) кожні 2 с
import copy  # noqa: E402
_an_orig, _an_cache = an.analyze_post, {}


def _analyze_cached(text, city_id=None, district_id=None, remote=False, home=None):
    k = (text, city_id, district_id, remote, home)
    r = _an_cache.get(k)
    if r is None:
        if len(_an_cache) > 50000:
            _an_cache.clear()
        r = _an_cache[k] = _an_orig(text, city_id, district_id, remote, home)
    return copy.deepcopy(r)


an.analyze_post = _analyze_cached

STATS = {"started": time.time(), "it": 0, "it_at": 0.0, "it_ms": 0, "tg_fast_at": 0.0, "tg_all_at": 0.0,
         "outage_at": 0.0, "alerts_src": None, "push": 0, "ws_clients": 0, "last_change": 0.0, "errors": 0}


def seed(out):
    """Перший старт на новому сервері: стан трекера/новин/сповіщень з гілки data (GitHub) — щоб не починати з нуля."""
    for rel in ("tracker_state.json", "api/news/hits.json", "notify_state.json", "acc/log.json", "acc/summary.json"):
        p = os.path.join(out, rel)
        if os.path.exists(p):
            continue
        try:
            req = urllib.request.Request(SEED_URL + rel, headers={"User-Agent": "cyd-rt-seed"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            log(f"seed: {rel} ({len(data)} B)")
        except Exception as e:  # noqa: BLE001
            log(f"seed {rel}: {e}")


def write_outage(out):
    meta = {"generated": int(time.time()), "static": True, "next_in": 5}
    def_ob = an.CITIES[bs.DEFAULT_CITY]["oblast"]
    for obi in sorted({o for p in outage.CFG["providers"] for o in p["oblasts"]}):
        groups = {}
        for g in outage.CFG["groups"]:
            try:
                r = outage.get_outage(obi, g)
            except Exception as e:  # noqa: BLE001
                r = {"days": [], "group": g, "err": str(e)}
            r = {**r, **meta}
            groups[g] = r
            bs.wjson(os.path.join(out, "api", "outage", str(obi), g + ".json"), r)
        if obi == def_ob:
            bs.wjson(os.path.join(out, "api", "outage.json"), {"oblast": obi, "city": bs.DEFAULT_CITY, "groups": groups, **meta})


def write_cities(out, cities, meta):
    for cdef in cities:
        cid = cdef["id"]
        for did in [None] + [d["id"] for d in cdef.get("districts", [])]:
            q = {"city": cid}
            if did:
                q["district"] = did
            try:
                st = {**srv.state(q), **meta, "city": cid, "district": did}
            except Exception:  # noqa: BLE001
                traceback.print_exc()
                continue
            bs.wjson(os.path.join(out, "api", "state", cid, did + ".json") if did else os.path.join(out, "api", "state", cid + ".json"), st)
            if cid == bs.DEFAULT_CITY and not did:
                bs.wjson(os.path.join(out, "api", "state.json"), st)
        try:
            tg = {**srv.targets_api({"city": cid}), **meta}
            bs.wjson(os.path.join(out, "api", "targets", cid + ".json"), tg)
            if cid == bs.DEFAULT_CITY:
                bs.wjson(os.path.join(out, "api", "targets.json"), tg)
        except Exception:  # noqa: BLE001
            traceback.print_exc()


def write_misc(out, chans, meta, state_path):
    for ch in chans:
        home = srv.home_city(ch)
        city = home["id"] if home else bs.DEFAULT_CITY
        bs.wjson(os.path.join(out, "api", "posts", ch + ".json"), {**srv.posts_api({"channel": ch, "city": city}), **meta})
    bs.wjson(os.path.join(out, "api", "posts.json"), {**srv.posts_api({"city": bs.DEFAULT_CITY}), **meta})
    bs.wjson(os.path.join(out, "api", "channels.json"), {"channels": [
        {**c, "ok": not (srv.channels.get(c["id"]) or {}).get("err"), "at": int((srv.channels.get(c["id"]) or {}).get("at", 0))}
        for c in an.CHANNELS_CFG], **meta})
    loc = json.dumps({"cities": [{"id": c["id"], "name": c["name"], "oblast": c["oblast"], "channel": c.get("channel", ""),
                                  "districts": [{"id": d["id"], "name": d["name"]} for d in c["districts"]]}
                                 for c in an.LOC["cities"]]}, ensure_ascii=False)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_index.html"), encoding="utf-8") as f:
        html = f.read().replace("%LOC%", loc).replace("%GROUPS%", json.dumps(outage.CFG["groups"]))
    bs._write_if_changed(os.path.join(out, "index.html"), html)
    bs._write_if_changed(os.path.join(out, "stats.html"), bs.stats_html())
    st = tk.tracker_dump(srv.trk, {"prev_lvl": srv._prev_lvl, "jaam_ev_seen": srv._jaam_ev_seen, "saved": int(time.time())})
    bs.wjson(state_path, st, force=True)


FULL_S = float(os.environ.get("FULL_S", "60"))
HOT_TICK_S = float(os.environ.get("HOT_TICK_S", "6"))   # без нових даних — перерахунок (рух цілей, ETA) раз на N с


def input_sig():
    """Підпис вхідних даних: зміна тривог / нових постів / подій JAAM → негайний перерахунок."""
    _, evs, _ = srv.jaam.snapshot()
    posts = tuple(sorted((ch, (v.get("posts") or [{}])[-1].get("id")) for ch, v in srv.channels.items()))
    return (srv.alerts.get("oblasts"), srv.alerts.get("regions"), len(evs), posts)


class Engine(threading.Thread):
    def __init__(self, out, on_change):
        super().__init__(daemon=True, name="engine")
        self.out, self.on_change = out, on_change
        self.state_path = os.path.join(out, "tracker_state.json")

    def _outage_loop(self):
        while True:
            try:
                t0 = time.time()
                write_outage(self.out)
                STATS["outage_at"] = time.time()
                log(f"outage: {time.time() - t0:.1f} с")
                self._flush()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            time.sleep(OUTAGE_S)

    def _full_loop(self):
        time.sleep(3)
        while True:
            t0 = time.time()
            try:
                meta = {"generated": int(time.time()), "static": True, "next_in": int(FULL_S)}
                write_cities(self.out, self.cold, meta)
                write_misc(self.out, self.chans, meta, self.state_path)
                self._flush()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            STATS["full_ms"] = int((time.time() - t0) * 1000)
            time.sleep(max(1.0, FULL_S - (time.time() - t0)))

    def _flush(self):
        with _changed_lock:
            ch = set(_changed)
            _changed.clear()
        if ch:
            STATS["last_change"] = time.time()
            self.on_change(ch)

    def run(self):
        out = self.out
        os.makedirs(out, exist_ok=True)
        seed(out)
        prev = None
        if os.path.exists(self.state_path):
            prev, _ = bs.load_prev_state(self.state_path, "")
        extra = tk.tracker_load(srv.trk, prev) if prev else {}
        srv._prev_lvl.update({int(k): v for k, v in (extra.get("prev_lvl") or {}).items()})
        srv._jaam_ev_seen = float(extra.get("jaam_ev_seen", 0))
        log(f"tracker: {len(srv.trk.tracks)} цілей з попереднього стану; numpy-прогноз: {'так' if FAST_NP else 'ні'}")
        if not srv.UKRAINEALARM_KEY:
            srv.USE_JAAM = True
            jaam_ws.start(srv.jaam, log)
            t0 = time.time()
            while time.time() - t0 < 20 and not srv.jaam.snapshot()[2]:
                time.sleep(0.5)
        notifier = tg_notify.Notifier(out)
        STATS["notify"] = notifier.enabled
        log("telegram-сповіщення: " + ("увімкнено" if notifier.enabled else "вимкнено (немає TG_BOT_TOKEN/TG_CHAT_ID у /etc/cyd-alert/env)"))
        acc = accuracy.AccuracyLog(out)
        chans = bs.all_channels()
        fast = {c["channel"].lower() for c in an.LOC["cities"] if c.get("channel")}
        fast |= {x.strip().lower() for x in os.environ.get("TG_FAST", "").split(",") if x.strip()}
        fast_list = [c for c in chans if c.lower() in fast]
        log(f"канали: {len(chans)} усього, швидкі ({TG_FAST_S:g} с): {', '.join(fast_list)}")
        try:
            nl = NewsLoop(out)
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            nl = None
        threading.Thread(target=self._outage_loop, daemon=True, name="outage").start()
        hot_ids = {tg_notify.load_cfg().get("city") or bs.DEFAULT_CITY, bs.DEFAULT_CITY}
        hot_ids |= {x.strip() for x in os.environ.get("HOT_CITIES", "").split(",") if x.strip()}
        self.hot = [c for c in an.LOC["cities"] if c["id"] in hot_ids]
        self.cold = [c for c in an.LOC["cities"] if c["id"] not in hot_ids]
        self.chans = chans
        log(f"гарячі міста (кожні {ALERT_S:g} с): {', '.join(sorted(hot_ids))}; решта — раз на {FULL_S:g} с")
        threading.Thread(target=self._full_loop, daemon=True, name="full").start()
        last_fast = last_all = last_hot = 0.0
        sig = None
        while True:
            t0 = time.time()
            STATS["it"] += 1
            try:
                try:
                    ob, rs, src = srv.fetch_alerts()
                    srv.alerts.update(oblasts=ob, regions=rs, at=time.time(), src=src, err=None)
                    srv.feed_tracker_alerts(rs)
                    STATS["alerts_src"] = src
                except Exception as e:  # noqa: BLE001
                    srv.alerts["err"] = str(e)
                    log("alerts error:", e)
                if t0 - last_all >= TG_ALL_S:
                    bs.fetch_all_channels(chans)
                    last_all = last_fast = time.time()
                    STATS["tg_all_at"] = STATS["tg_fast_at"] = last_all
                    if nl:
                        try:
                            nl.tick()
                        except Exception:  # noqa: BLE001
                            traceback.print_exc()
                elif fast_list and t0 - last_fast >= TG_FAST_S:
                    bs.fetch_all_channels(fast_list, workers=4)
                    last_fast = STATS["tg_fast_at"] = time.time()
                bs._written.update(n=0, skipped=0)
                ns = input_sig()
                if ns != sig or time.time() - last_hot >= HOT_TICK_S:
                    STATS["hot_why"] = "дані" if ns != sig else "таймер"
                    sig, last_hot = ns, time.time()
                    th = time.time()
                    write_cities(out, self.hot, {"generated": int(time.time()), "static": True, "next_in": int(ALERT_S)})
                    STATS["hot_ms"] = int((time.time() - th) * 1000)
                    self._flush()
                try:
                    notifier.tick()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                try:
                    if acc.tick(srv.trk):
                        acc.summarize(force=True)
                    acc.save()
                    acc.summarize()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            except Exception:  # noqa: BLE001
                STATS["errors"] += 1
                traceback.print_exc()
            STATS["it_at"] = time.time()
            STATS["it_ms"] = int((time.time() - t0) * 1000)
            if STATS["it"] % 300 == 1:
                log(f"#{STATS['it']}: {STATS['alerts_src']}, {STATS['it_ms']} мс, цілей {len(srv.trk.tracks)}, ws {STATS['ws_clients']}")
            time.sleep(max(0.3, ALERT_S - (time.time() - t0)))


# ------------------------------------------------------------------ HTTP / WebSocket
def make_app(out):
    out = os.path.abspath(out)
    subs = {}                     # abs path -> set(ws)
    app = web.Application()

    def safe(rel):
        p = os.path.abspath(os.path.join(out, rel.lstrip("/")))
        return p if p.startswith(out + os.sep) or p == out else None

    def state_path_q(q):
        city = (q.get("city") or "").strip()
        dist = (q.get("district") or "").strip()
        if not city:
            return "api/state.json"
        return f"api/state/{city}/{dist}.json" if dist else f"api/state/{city}.json"

    def dyn_rel(name, q):
        if name == "state":
            return state_path_q(q)
        if name == "targets":
            c = q.get("city")
            return f"api/targets/{c}.json" if c else "api/targets.json"
        if name == "outage":
            ob, g = q.get("oblast"), q.get("group")
            return f"api/outage/{ob}/{g}.json" if ob and g else "api/outage.json"
        if name == "posts":
            ch = q.get("channel")
            return f"api/posts/{ch}.json" if ch else "api/posts.json"
        if name == "channels":
            return "api/channels.json"
        return None

    def file_resp(p):
        if not p or not os.path.isfile(p):
            raise web.HTTPNotFound(text='{"error":"not found"}', content_type="application/json")
        r = web.FileResponse(p)
        r.headers["Cache-Control"] = "no-cache"
        r.headers["Access-Control-Allow-Origin"] = "*"
        return r

    async def health(req):
        now = time.time()
        _, _, jupd = srv.jaam.snapshot()
        h = {"ok": now - STATS["it_at"] < 30, "now": int(now), "uptime_s": int(now - STATS["started"]),
             "iter": STATS["it"], "iter_ms": STATS["it_ms"], "iter_age_s": round(now - STATS["it_at"], 1),
             "alerts_src": STATS["alerts_src"], "jaam_connected": bool(srv.jaam.connected),
             "jaam_age_s": round(now - jupd, 1) if jupd else None,
             "tg_fast_age_s": round(now - STATS["tg_fast_at"], 1), "tg_all_age_s": round(now - STATS["tg_all_at"], 1),
             "full_ms": STATS.get("full_ms"), "hot_ms": STATS.get("hot_ms"), "hot_why": STATS.get("hot_why"), "outage_age_s": round(now - STATS["outage_at"], 1), "ws_clients": STATS["ws_clients"],
             "pushes": STATS["push"], "last_change_age_s": round(now - STATS["last_change"], 1) if STATS["last_change"] else None,
             "notify": bool(STATS.get("notify")), "errors": STATS["errors"],
             "channels_err": sorted(k for k, v in srv.channels.items() if v.get("err"))}
        return web.json_response(h, headers={"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"})

    async def api(req):
        name = req.match_info["name"]
        if name.endswith(".json") or "/" in name:
            return file_resp(safe("api/" + name))
        rel = dyn_rel(name, req.query)
        return file_resp(safe(rel) if rel else None)

    async def ws_handler(req):
        rel = req.query.get("path") or state_path_q(req.query)
        p = safe(rel)
        if not p:
            raise web.HTTPBadRequest()
        ws = web.WebSocketResponse(heartbeat=25, compress=False)
        await ws.prepare(req)
        subs.setdefault(p, set()).add(ws)
        STATS["ws_clients"] += 1
        try:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    await ws.send_str(f.read())
            async for msg in ws:
                if msg.type == WSMsgType.TEXT and msg.data == "ping":
                    await ws.send_str("pong")
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break
        finally:
            subs.get(p, set()).discard(ws)
            STATS["ws_clients"] -= 1
        return ws

    async def broadcast(paths):
        for p in paths:
            s = subs.get(p)
            if not s:
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    data = f.read()
            except OSError:
                continue
            for ws in list(s):
                try:
                    await ws.send_str(data)
                    STATS["push"] += 1
                except Exception:  # noqa: BLE001
                    s.discard(ws)

    async def index(req):
        return file_resp(safe("index.html"))

    async def static_any(req):
        rel = req.match_info["tail"]
        p = safe(rel)
        if p and os.path.isdir(p):
            p = os.path.join(p, "index.html")
        return file_resp(p)

    app.router.add_get("/api/health", health)
    app.router.add_get("/ws", ws_handler)
    app.router.add_get("/api/{name:.+}", api)
    app.router.add_get("/static/api/{name:.+}", api)     # «статичний» режим прошивки (адреса …/static)
    app.router.add_get("/", index)
    app.router.add_get("/{tail:.+}", static_any)
    app["broadcast"] = broadcast
    return app


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.environ.get("OUT_DIR", "/var/lib/cyd-alert/public"))
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    a = ap.parse_args()
    app = make_app(a.out)

    async def start(app_):
        loop = asyncio.get_running_loop()

        def on_change(paths):
            loop.call_soon_threadsafe(lambda: asyncio.ensure_future(app_["broadcast"](paths)))
        Engine(a.out, on_change).start()
    app.on_startup.append(start)
    web.run_app(app, host=a.host, port=a.port, access_log=None, print=lambda *x: log(*x))


if __name__ == "__main__":
    main()
