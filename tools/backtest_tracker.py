#!/usr/bin/env python3
"""Бектест трекера на архіві постів (data/history/raw) — та сама логіка, що rt_server + server/outcomes.py.

    python tools/backtest_tracker.py [--from 2026-08-01] [--to 2026-10-29] [--step 120] [--motion '{"shahed":{"turn":2}}']
                                     [--split 2026-10-12] [--out /tmp/bt.json]
Пости всіх каналів архіву подаються в tracker.Tracker у хронологічному порядку (місто-«дім» каналу як у server.py);
кожні --step с (коли є цілі) — прогноз для Кременчука (R = 15 км) у outcomes.OutcomeLog з модельним часом.
Метрики (окремо train = до --split і holdout = після):
  • track: Brier / hit-rate / precision для p30 «ціль пройде ≤ R за 30 хв» проти сирих положень з постів
    (лише прогнози, де після прогнозу були нові положення тієї ж цілі);
  • city: p_drone30 / p_any30 зведення проти «будь-яке сире положення дрона/ракети ≤ R від міста за 30 хв»
    (не залежить від асоціації треків);
  • pos_err: похибка прогнозованого положення (шлях 0/5/10/15/20/30 хв, лінійна інтерполяція) проти наступних
    сирих положень тієї ж цілі — медіана км за горизонтами (курс + швидкість);
  • target: «ймовірна ціль» проти вузла останнього положення зниклої цілі; + підтвердження новинами (data/history/news_raw:
    місто + слова удару протягом 6 год);
  • speeds: спостережувані швидкості між сирими положеннями однієї цілі (5–40 хв, ≥ 10 км) за типами.
"""
import argparse, bisect, glob, json, math, os, re, statistics, sys, tempfile, time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
os.environ.setdefault("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4"); time.tzset()
import tracker as tk     # noqa: E402
import analyzer as an    # noqa: E402
import history           # noqa: E402
import outcomes as oc    # noqa: E402
import fastforecast      # noqa: E402

CITY = "kremenchuk"
R = 15.0
NEWS_HIT = re.compile(r"вибух|удар|атак|уражен|влучан|пошкоджен|обстріл|прил[іе]т|пожеж")


def ts_of(s):
    return int(datetime.strptime(s, "%Y-%m-%d").timestamp())


def load_posts(t0, t1):
    out = []
    for p in glob.glob(os.path.join(ROOT, "data", "history", "raw", "*.jsonl")):
        ch = os.path.basename(p)[:-6]
        for line in open(p, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if t0 <= d["ts"] < t1 and d.get("text"):
                out.append((d["ts"], ch, d["id"], d["text"]))
    out.sort()
    return out


def load_news(nodes, t0, t1):
    """→ {node_name: [ts, ...]} — новини з назвою міста-вузла і словами удару"""
    stems = [(n[0], n[0].lower()[:max(4, len(n[0]) - 2)]) for n in nodes]
    hits = {}
    for p in glob.glob(os.path.join(ROOT, "data", "history", "news_raw", "*.jsonl")):
        for line in open(p, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not (t0 <= d.get("ts", 0) < t1 + 86400):
                continue
            t = (d.get("text") or "").lower()
            if not NEWS_HIT.search(t):
                continue
            for name, st in stems:
                if st in t:
                    hits.setdefault(name, []).append(d["ts"])
    for v in hits.values():
        v.sort()
    return hits


def interp(path, lead_min):
    keys = (0, 5, 10, 15, 20, 30)
    for (k0, a), (k1, b) in zip(zip(keys, path), zip(keys[1:], path[1:])):
        if k0 <= lead_min <= k1:
            f = (lead_min - k0) / (k1 - k0)
            return a[0] + f * (b[0] - a[0]), a[1] + f * (b[1] - a[1])
    return None


def km(a, b, c, d):
    return math.hypot((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * 110.57)


def brier_block(pairs):
    if not pairs:
        return dict(n=0)
    n = len(pairs)
    base = sum(y for _, y in pairs) / n
    m = dict(n=n, base=round(base, 4), brier=round(sum((p - y) ** 2 for p, y in pairs) / n, 5),
             brier_clim=round(base * (1 - base), 5))
    m["bss"] = round(1 - m["brier"] / m["brier_clim"], 3) if m["brier_clim"] > 0 else None
    for th in (0.15, 0.5):
        tp = sum(1 for p, y in pairs if p >= th and y); fp = sum(1 for p, y in pairs if p >= th and not y)
        fn = sum(1 for p, y in pairs if p < th and y)
        m[f"hit@{th}"] = round(tp / (tp + fn), 3) if tp + fn else None
        m[f"prec@{th}"] = round(tp / (tp + fp), 3) if tp + fp else None
    rel = []                                  # надійність: [межа кошика, n, середнє p, частота]
    for lo, hi in ((0, .05), (.05, .15), (.15, .3), (.3, .5), (.5, 1.01)):
        b = [(p, y) for p, y in pairs if lo <= p < hi]
        if b:
            rel.append([lo, len(b), round(sum(p for p, _ in b) / len(b), 3), round(sum(y for _, y in b) / len(b), 3)])
    m["rel"] = rel
    return m


def run(a):
    fastforecast.install()
    for k, v in (json.loads(a.set).items() if a.set else ()):
        setattr(tk, k, v)
    if a.motion:
        for typ, kv in json.loads(a.motion).items():
            for k, v in kv.items():
                tk.TYPES[typ][k] = tuple(v) if isinstance(v, list) else v
    t0, t1, split = ts_of(a.frm), ts_of(a.to), ts_of(a.split)
    posts = load_posts(t0, t1)
    cdef = an.CITIES[CITY]
    ulat, ulon = cdef["lat"], cdef["lon"]
    by_ch = {c["channel"].lower(): c for c in an.LOC["cities"] if c.get("channel")}
    regions = json.load(open(os.path.join(ROOT, "data", "city_regions.json"), encoding="utf-8"))
    ureg = regions.get(CITY)
    trk = tk.Tracker(seed=1)
    trk.history = None if a.no_history else history.History.load()
    tk.Track._next = 1
    d = tempfile.mkdtemp(prefix="bt_")
    log = oc.OutcomeLog(d, R=R, every=a.step)
    # сирі положення з постів (для city-метрики): перехоплюємо parse_cues
    raw = []
    cur = {"ts": 0}
    orig = tk.parse_cues

    def pc(text, gaz, city=None, *rest):
        c = orig(text, gaz, city, *rest)
        cls = tk.TYPES[c["type"]]["cls"] if c["type"] in tk.TYPES else None
        # «правда» — лише з розбору цілого посту (однакова для всіх варіантів трекера)
        if cls and not c["kill"] and text == cur.get("text"):
            for p in c["pos"]:
                if p.get("sigma", 99) <= 20:
                    raw.append((cur["ts"], cls, p["lat"], p["lon"]))
        return c
    tk.parse_cues = pc
    paths = {}
    tick = lambda now: log.tick(trk, ulat, ulon, now=now, summary=tk.threat_summary,   # noqa: E731
                                assess=lambda: trk.assess(ulat, ulon, now, R, user_region=ureg, user_oblast=cdef["oblast"]))
    wall = time.time()
    nxt = None
    errs = {}
    for i, (ts, ch, pid, text) in enumerate(posts):
        while nxt is not None and nxt < ts:
            if trk.tracks:
                n0 = len(log.pending)
                tick(nxt)
                for p in log.pending[n0:]:
                    paths[p["fid"]] = None
            else:
                log.tick(trk, ulat, ulon, now=nxt)
            nxt += a.step
        cur["ts"], cur["text"] = ts, text
        try:
            trk.add_post(text, ts, by_ch.get(ch.lower()), src=f"tg:{ch}", post_id=f"{ch}/{pid}")
        except Exception as e:  # noqa: BLE001
            errs[type(e).__name__] = errs.get(type(e).__name__, 0) + 1
        if nxt is None:
            nxt = ts
        if i % 5000 == 0:
            print(f"  {i}/{len(posts)} {time.strftime('%m-%d', time.localtime(ts))} tracks={len(trk.tracks)} "
                  f"{time.time() - wall:.0f}s", file=sys.stderr, flush=True)
    trk.tracks = []
    log.tick(trk, ulat, ulon, now=(nxt or t1) + 4000)
    tk.parse_cues = orig
    if errs:
        print("add_post errors:", errs, file=sys.stderr)
    fc, out, sums = oc.load(d)
    # шляхи прогнозів не пишуться в журнал — перерахунок похибки положення: беремо la/lo/hd/v із fc (пряма)
    return fc, out, sums, raw, trk, d


def metrics(fc, out, sums, raw, split, hist_nodes, news):
    res = {}
    raw.sort()
    rts = [r[0] for r in raw]
    ulat, ulon = an.CITIES[CITY]["lat"], an.CITIES[CITY]["lon"]
    near_raw = [(t, cls) for t, cls, la, lo in raw if km(ulat, ulon, la, lo) <= R]
    nts = [t for t, _ in near_raw]
    for part, cond in (("train", lambda t: t < split), ("holdout", lambda t: t >= split), ("all", lambda t: True)):
        P = {}
        tr_pairs = [(fc[f]["p30"], out[f]["near"]) for f in out if f in fc and out[f].get("near") is not None and cond(fc[f]["t"])]
        P["track_p30"] = brier_block(tr_pairs)
        for ty in ("shahed", "gerbera", "jet", "lancet", "recon", "unknown", "cruise", "banderol", "kh22", "kab"):
            pp = [(fc[f]["p30"], out[f]["near"]) for f in out if f in fc and out[f].get("near") is not None
                  and cond(fc[f]["t"]) and fc[f]["ty"] == ty]
            if pp:
                P[f"track_p30_{ty}"] = brier_block(pp)
        # city: будь-яке сире положення ≤ R за (t, t+30 хв]
        cb = {k: [] for k in ("city_drone30", "city_any30", "city_missile30", "city_drone15", "city_any15")}
        for s in sums:
            if not cond(s["t"]):
                continue
            for T in (30, 15):
                i, j = bisect.bisect_right(nts, s["t"]), bisect.bisect_right(nts, s["t"] + T * 60)
                evs = near_raw[i:j]
                pd, pa = (s["p_drone"], s["p_any"]) if T == 30 else (s.get("p_drone15"), s.get("p_any15"))
                if pd is not None:
                    cb[f"city_drone{T}"].append((pd, int(any(c == "drone" for _, c in evs))))
                    cb[f"city_any{T}"].append((pa, int(bool(evs))))
                if T == 30 and s.get("p_missile") is not None:
                    cb["city_missile30"].append((s["p_missile"], int(any(c == "missile" for _, c in evs))))
        for k, v in cb.items():
            P[k] = brier_block(v)
        # похибка положення: пряма з курсом/швидкістю прогнозу проти наступних сирих положень тієї ж цілі
        errs = {"5": [], "15": [], "30": []}
        for f in out:
            r = fc.get(f)
            if not r or not cond(r["t"]):
                continue
            for ts, la, lo in out[f].get("trail") or []:
                L = (ts - r["t"]) / 60
                if L <= 1 or L > 30:
                    continue
                dd = r["v"] * L / 60
                b = math.radians(r["hd"])
                pla = r["la"] + dd * math.cos(b) / 110.57
                plo = r["lo"] + dd * math.sin(b) / (111.32 * math.cos(math.radians(r["la"])))
                k = "5" if L <= 7.5 else "15" if L <= 20 else "30"
                errs[k].append(km(pla, plo, la, lo))
        P["pos_err_km_median"] = {k: (round(statistics.median(v), 1) if v else None) for k, v in errs.items()}
        P["pos_err_n"] = {k: len(v) for k, v in errs.items()}
        # ціль
        tg = [(fc[f]["tgt"], out[f]["node"]) for f in out if f in fc and cond(fc[f]["t"]) and out[f].get("node") and fc[f].get("tgt")]
        P["target_vs_endnode"] = dict(n=len(tg), acc=round(sum(x == y for x, y in tg) / len(tg), 3) if tg else None)
        nw = []
        for f in out:
            r = fc.get(f)
            if not r or not cond(r["t"]) or not r.get("tgt") or not out[f].get("gone"):
                continue
            hs = news.get(r["tgt"], [])
            k = bisect.bisect_right(hs, r["t"])
            any_news = any(r["t"] < x <= r["t"] + 6 * 3600 for lst in news.values() for x in lst[bisect.bisect_right(lst, r["t"]):][:1])
            if any_news:
                nw.append(int(k < len(hs) and hs[k] <= r["t"] + 6 * 3600))
        P["target_news_confirmed"] = dict(n=len(nw), rate=round(sum(nw) / len(nw), 3) if nw else None)
        res[part] = P
    return res


def speeds(out, fc):
    sp = {}
    for f, o in out.items():
        r = fc.get(f)
        tl = o.get("trail") or []
        if not r:
            continue
        for (t0, a0, b0), (t1, a1, b1) in zip(tl, tl[1:]):
            dt = (t1 - t0) / 3600
            dd = km(a0, b0, a1, b1)
            if 5 / 60 <= dt <= 40 / 60 and dd >= 10:
                sp.setdefault(r["ty"], set()).add((t0, round(dd / dt)))
    return {k: dict(n=len(v), median=statistics.median([x for _, x in v]),
                    p25=statistics.quantiles([x for _, x in v], n=4)[0] if len(v) > 3 else None,
                    p75=statistics.quantiles([x for _, x in v], n=4)[2] if len(v) > 3 else None) for k, v in sp.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-08-01")
    ap.add_argument("--to", default="2026-10-29")
    ap.add_argument("--split", default="2026-10-12")
    ap.add_argument("--step", type=int, default=120)
    ap.add_argument("--motion", default="")
    ap.add_argument("--set", default="", help='константи tracker, напр. {"HEAD_ASSOC_KM": 0}')
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--label", default="")
    ap.add_argument("--truth", default="", help="файл сирих положень-«правди»: є — взяти його, нема — записати (однакова правда для всіх варіантів)")
    ap.add_argument("--dump", default="", help="pickle (fc, out, sums, raw) для аналізу")
    a = ap.parse_args()
    wall = time.time()
    fc, out, sums, raw, trk, d = run(a)
    if a.truth:
        if os.path.exists(a.truth):
            raw = [tuple(r) for r in json.load(open(a.truth))]
        else:
            json.dump(raw, open(a.truth, "w"))
    if a.dump:
        import pickle
        pickle.dump((fc, out, sums, raw), open(a.dump, "wb"))
    nodes = trk.history.nodes if trk.history else history.History.load().nodes
    news = load_news(nodes, ts_of(a.frm), ts_of(a.to))
    res = dict(label=a.label, args=vars(a), n_fc=len(fc), n_out=len(out), n_sum=len(sums), n_raw=len(raw),
               runtime_s=round(time.time() - wall), metrics=metrics(fc, out, sums, raw, ts_of(a.split), nodes, news),
               speeds=speeds(out, fc), log_dir=d)
    s = json.dumps(res, ensure_ascii=False, indent=1)
    print(s)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(s)


if __name__ == "__main__":
    main()
