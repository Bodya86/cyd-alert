#!/usr/bin/env python3
"""Разовий («one-shot») режим сервера для GitHub Actions + GitHub Pages.

    python server/build_static.py --out public/ [--state public/tracker_state.json] [--jaam-wait 15]

Що робить за один запуск:
  1. тривоги: JAAM WebSocket (підключитись, прочитати знімок ~10–20 с, відключитись) → резерв ubilling
     (або api.ukrainealarm.com, якщо задано секрет UKRAINEALARM_KEY);
  2. Telegram: публічна сторінка t.me/s/<канал> (kremen_sv та інші канали з data/locations.json);
  3. графіки відключень (усі області з джерелами, усі черги);
  4. трекер цілей (фільтри частинок) — стан читається з попереднього запуску і зберігається знову;
  5. пише статичні файли:
       api/state.json                      — Кременчук (за замовчуванням)
       api/state/<city>.json, api/state/<city>/<district>.json
       api/targets.json, api/targets/<city>.json
       api/posts.json                      — пости kremen_sv з оцінкою
       api/outage.json                     — Кременчук (Полтавська обл.): усі черги
       api/outage/<oblast>/<group>.json    — для вибору черги на ESP
       index.html, tracker_state.json, .nojekyll
"""
import argparse, json, os, sys, threading, time, traceback, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JAAM", "1")
import server as srv          # noqa: E402  — без запуску HTTP і фонових потоків
import analyzer as an         # noqa: E402
import tracker as tk          # noqa: E402
import outage                 # noqa: E402
import jaam_ws                # noqa: E402

DEFAULT_CITY = "kremenchuk"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))


def load_prev_state(path, url):
    """Попередній стан трекера: локальний файл (actions/cache) → опублікований на Pages."""
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f), "file"
        except Exception as e:  # noqa: BLE001
            log("state file:", e)
    if url:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "CYD-Alert build"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.loads(r.read().decode("utf-8")), "pages"
        except Exception as e:  # noqa: BLE001
            log("state url:", e)
    return None, None


def jaam_snapshot(wait_s):
    """Підключитись до JAAM, дочекатися знімка тривог (+ кілька секунд на події) і відключитись."""
    stop = threading.Event()
    err = []

    def run():
        try:
            jaam_ws.run_session(srv.jaam, stop)
        except Exception as e:  # noqa: BLE001
            err.append(str(e))
    th = threading.Thread(target=run, daemon=True)
    th.start()
    t0 = time.time()
    got_at = None
    while time.time() - t0 < wait_s and th.is_alive():
        _, _, upd = srv.jaam.snapshot()
        if upd and got_at is None:
            got_at = time.time()
        if got_at and time.time() - got_at > 3:      # знімок є, ще 3 с на разові події
            break
        time.sleep(0.25)
    stop.set()
    _, evs, upd = srv.jaam.snapshot()
    log(f"jaam: {'ok' if upd else 'немає даних'} за {time.time() - t0:.1f} с, подій {len(evs)}" + (f", помилка {err[0]}" if err else ""))
    return bool(upd)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--state", default=None, help="файл стану трекера (типово <out>/tracker_state.json)")
    ap.add_argument("--state-url", default=os.environ.get("STATE_URL", ""), help="резерв: стан з опублікованого сайту")
    ap.add_argument("--jaam-wait", type=float, default=float(os.environ.get("JAAM_WAIT", "18")))
    ap.add_argument("--no-jaam", action="store_true")
    ap.add_argument("--no-outage", action="store_true")
    a = ap.parse_args()
    out = a.out.rstrip("/\\")
    state_path = a.state or os.path.join(out, "tracker_state.json")
    os.makedirs(out, exist_ok=True)
    t_start = time.time()
    now = time.time()

    # ---- стан трекера з попереднього запуску
    prev, prev_src = load_prev_state(state_path, a.state_url)
    extra = tk.tracker_load(srv.trk, prev) if prev else {}
    srv._prev_lvl.update({int(k): v for k, v in (extra.get("prev_lvl") or {}).items()})
    srv._jaam_ev_seen = float(extra.get("jaam_ev_seen", 0))
    log(f"tracker: {len(srv.trk.tracks)} цілей з попереднього запуску ({prev_src or 'новий'})")

    # ---- тривоги
    if a.no_jaam:
        srv.USE_JAAM = False
    elif not srv.UKRAINEALARM_KEY:
        srv.USE_JAAM = jaam_snapshot(a.jaam_wait)
    try:
        ob, rs, src = srv.fetch_alerts()
        srv.alerts.update(oblasts=ob, regions=rs, at=time.time(), src=src, err=None)
        srv.feed_tracker_alerts(rs)
        log(f"alerts: {src}, областей з тривогою {sum(c != 'N' for c in ob)}")
    except Exception as e:  # noqa: BLE001
        srv.alerts["err"] = str(e)
        log("alerts error:", e)

    # ---- Telegram
    chans = sorted({c["channel"] for c in an.LOC["cities"] if c.get("channel")})
    for ch in chans:
        c = srv.channels.setdefault(ch, {"posts": [], "at": 0, "err": None, "last_req": time.time()})
        try:
            posts = srv.fetch_channel(ch)
            c.update(posts=posts, at=time.time(), err=None)
            srv.feed_tracker_posts(ch, posts)
            log(f"tg {ch}: {len(posts)} постів")
        except Exception as e:  # noqa: BLE001
            c.update(at=time.time(), err=str(e))      # at=now — щоб state() не тягнув знову
            log(f"tg {ch}:", e)

    gen = int(time.time())
    meta = {"generated": gen, "static": True, "next_in": 300}

    # ---- стани по містах і районах
    n_state = 0
    for cdef in an.LOC["cities"]:
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
            wjson(os.path.join(out, "api", "state", cid, did + ".json") if did else os.path.join(out, "api", "state", cid + ".json"), st)
            n_state += 1
            if cid == DEFAULT_CITY and not did:
                wjson(os.path.join(out, "api", "state.json"), st)
        try:
            tg = {**srv.targets_api({"city": cid}), **meta}
            wjson(os.path.join(out, "api", "targets", cid + ".json"), tg)
            if cid == DEFAULT_CITY:
                wjson(os.path.join(out, "api", "targets.json"), tg)
        except Exception:  # noqa: BLE001
            traceback.print_exc()

    # ---- пости
    for ch in chans:
        city = next((c["id"] for c in an.LOC["cities"] if c.get("channel") == ch), None)
        p = {**srv.posts_api({"channel": ch, "city": city}), **meta}
        wjson(os.path.join(out, "api", "posts", ch + ".json"), p)
        if city == DEFAULT_CITY:
            wjson(os.path.join(out, "api", "posts.json"), p)

    # ---- відключення
    n_out = 0
    if not a.no_outage:
        def_ob = an.CITIES[DEFAULT_CITY]["oblast"]
        oblasts = sorted({o for p in outage.CFG["providers"] for o in p["oblasts"]})
        for obi in oblasts:
            groups = {}
            for g in outage.CFG["groups"]:
                try:
                    r = outage.get_outage(obi, g)
                except Exception as e:  # noqa: BLE001
                    r = {"days": [], "group": g, "err": str(e)}
                r = {**r, **meta}
                groups[g] = r
                wjson(os.path.join(out, "api", "outage", str(obi), g + ".json"), r)
                n_out += 1
            if obi == def_ob:
                wjson(os.path.join(out, "api", "outage.json"), {"oblast": obi, "city": DEFAULT_CITY, "groups": groups, **meta})
        log(f"outage: {n_out} файлів, {len(oblasts)} областей")

    # ---- дашборд і стан
    loc = json.dumps({"cities": [{"id": c["id"], "name": c["name"], "oblast": c["oblast"], "channel": c.get("channel", ""),
                                  "districts": [{"id": d["id"], "name": d["name"]} for d in c["districts"]]}
                                 for c in an.LOC["cities"]]}, ensure_ascii=False)
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "static_index.html"), encoding="utf-8") as f:
        html = f.read().replace("%LOC%", loc).replace("%GROUPS%", json.dumps(outage.CFG["groups"]))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(out, ".nojekyll"), "w").close()
    st = tk.tracker_dump(srv.trk, {"prev_lvl": srv._prev_lvl, "jaam_ev_seen": srv._jaam_ev_seen, "saved": gen})
    wjson(state_path, st)
    if os.path.abspath(state_path) != os.path.abspath(os.path.join(out, "tracker_state.json")):
        wjson(os.path.join(out, "tracker_state.json"), st)
    log(f"done: {n_state} state-файлів, цілей {len(srv.trk.tracks)}, {time.time() - t_start:.1f} с → {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
