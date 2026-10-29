"""Журнал прогнозів трекера і їх перевірка тим, що сталося насправді (live.yml, кожна ітерація).

Прогноз (раз на 5 хв на кожну дронову ціль): положення, розподіл курсу по 8 секторах (з частинок),
топ-5 «куди летить» (history.p_target). Перевірка:
  • напрям — перше наступне спостереження тієї самої цілі (≥ 12 км від точки прогнозу, ≤ 60 хв) → фактичний сектор;
    влучання top-1 / top-3 серед секторів;
  • ціль — коли трек зник (збили / долетів / відбій), остання позиція ≤ 25 км від вузла моделі → фактичне місто;
    влучання top-1 / top-3. Остання позиція не біля жодного міста — «без цілі» (не рахується у точність).
Файли (у гілці data, переживають перезапуски): acc/pending.json, acc/resolved.jsonl (30 днів), acc/summary.json.
"""
import json, math, os, time

DIRS = 8
PRED_EVERY = 300
DIR_MIN_KM, DIR_MAX_S = 12.0, 3600
NODE_KM = 25.0
KEEP_DAYS = 30


def _km(a, b, c, d):
    return math.hypot((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * 111.2)


def _bearing(a, b, c, d):
    return math.degrees(math.atan2((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * 111.2)) % 360


def dir_bin(brg):
    return int(((brg + 22.5) % 360) // 45)


def particle_dirs(tr):
    """розподіл курсу цілі по 8 секторах (0 = пн) із зважених частинок"""
    v = [0.0] * DIRS
    for q, w in zip(tr.p, tr.w):
        v[dir_bin(math.degrees(q[2]))] += w
    s = sum(v) or 1.0
    return [x / s for x in v]


class AccuracyLog:
    def __init__(self, out, now=None):
        self.dir = os.path.join(out, "acc")
        os.makedirs(self.dir, exist_ok=True)
        self.pending_path = os.path.join(self.dir, "pending.json")
        self.resolved_path = os.path.join(self.dir, "resolved.jsonl")
        self.summary_path = os.path.join(self.dir, "summary.json")
        try:
            with open(self.pending_path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            d = {}
        self.preds = d.get("preds", [])          # відкриті прогнози
        self.obs = {int(k): v for k, v in d.get("obs", {}).items()}   # id → [[ts, lat, lon], ...] (останні 12)
        self.last_pred = {int(k): v for k, v in d.get("last_pred", {}).items()}
        self.last_summary = 0

    # ---------------------------------------------------------------- журнал
    def tick(self, trk, now=None):
        """→ кількість нових перевірених записів"""
        import tracker as tk
        now = now or time.time()
        hist = trk.history
        alive = {}
        for tr in trk.tracks:
            if tr.type not in tk.DRONE_TYPES:
                continue
            alive[tr.id] = tr
            x, y, hd, Rh, v, sd = tr.mean()
            lat, lon = tr.proj.ll(x, y)
            o = self.obs.setdefault(tr.id, [])
            if not o or o[-1][0] != tr.last_obs:
                o.append([tr.last_obs, round(lat, 4), round(lon, 4)])
                del o[:-12]
            if now - tr.last_obs > 900 or now - self.last_pred.get(tr.id, 0) < PRED_EVERY:
                continue
            self.last_pred[tr.id] = now
            dp = particle_dirs(tr)
            top_d = sorted(range(DIRS), key=lambda i: -dp[i])[:3]
            tg = []
            if hist is not None:
                P = hist.p_target(lat, lon, math.degrees(hd) if Rh >= 0.3 else None)
                tg = [[k, round(p, 3)] for k, p in sorted(P.items(), key=lambda kv: -kv[1])[:5]]
            self.preds.append(dict(id=tr.id, t=int(now), ty=tr.type, la=round(lat, 4), lo=round(lon, 4),
                                   hc=round(Rh, 2), d=top_d, dp=round(dp[top_d[0]], 3), tg=tg,
                                   prior=bool(getattr(tr, "prior_used", False)), obs_t=tr.last_obs))
        return self._resolve(alive, hist, now)

    def _resolve(self, alive, hist, now):
        done, keep = [], []
        for p in self.preds:
            o = self.obs.get(p["id"], [])
            if "ad" not in p:
                for ts, la, lo in o:
                    if ts > p["obs_t"] and ts - p["t"] <= DIR_MAX_S and _km(p["la"], p["lo"], la, lo) >= DIR_MIN_KM:
                        p["ad"] = dir_bin(_bearing(p["la"], p["lo"], la, lo))
                        break
                if "ad" not in p and now - p["t"] > DIR_MAX_S:
                    p["ad"] = -1
            if "at" not in p and p["id"] not in alive:
                if o and hist is not None:
                    _, la, lo = o[-1]
                    k = hist.node_near(la, lo, NODE_KM)
                    p["at"] = -1 if k is None else k
                else:
                    p["at"] = -1
            if "ad" in p and "at" in p:
                done.append(p)
            elif now - p["t"] < 4 * 3600:
                keep.append(p)
        self.preds = keep
        for i in list(self.obs):
            if i not in alive and not any(p["id"] == i for p in keep):
                del self.obs[i]
        self.last_pred = {i: t for i, t in self.last_pred.items() if i in alive}
        if done:
            with open(self.resolved_path, "a", encoding="utf-8") as f:
                for p in done:
                    tgk = [k for k, _ in p["tg"]]
                    rec = dict(t=p["t"], ty=p["ty"], hc=p["hc"], pr=int(p["prior"]))
                    if p["ad"] >= 0:
                        rec.update(d1=int(p["d"][0] == p["ad"]), d3=int(p["ad"] in p["d"]))
                    if p["at"] >= 0 and tgk:
                        rec.update(t1=int(tgk[0] == p["at"]), t3=int(p["at"] in tgk[:3]))
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        return len(done)

    def save(self):
        with open(self.pending_path, "w", encoding="utf-8") as f:
            json.dump(dict(preds=self.preds, obs=self.obs, last_pred=self.last_pred), f, separators=(",", ":"))

    # ---------------------------------------------------------------- підсумок
    def summarize(self, now=None, force=False):
        now = now or time.time()
        if not force and now - self.last_summary < 600:
            return None
        self.last_summary = now
        recs = []
        try:
            with open(self.resolved_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if now - r["t"] <= KEEP_DAYS * 86400:
                        recs.append(r)
        except OSError:
            pass
        with open(self.resolved_path, "w", encoding="utf-8") as f:     # обрізка до 30 днів
            for r in recs:
                f.write(json.dumps(r, separators=(",", ":")) + "\n")
        summ = summarize_records(recs, now)
        summ["pending"] = len(self.preds)
        with open(self.summary_path, "w", encoding="utf-8") as f:
            json.dump(summ, f, ensure_ascii=False, separators=(",", ":"))
        return summ


def _rates(rs):
    nd = [r for r in rs if "d1" in r]
    nt = [r for r in rs if "t1" in r]
    f = lambda xs, k: round(sum(r[k] for r in xs) / len(xs), 3) if xs else None   # noqa: E731
    return dict(n=len(rs), n_dir=len(nd), dir1=f(nd, "d1"), dir3=f(nd, "d3"),
                n_tgt=len(nt), tgt1=f(nt, "t1"), tgt3=f(nt, "t3"))


def summarize_records(recs, now):
    out = dict(generated=int(now), window_days=KEEP_DAYS, baseline=dict(dir1=round(1 / DIRS, 3), dir3=round(3 / DIRS, 3)))
    out["rolling"] = {k: _rates([r for r in recs if now - r["t"] <= s]) for k, s in
                      (("24h", 86400), ("7d", 7 * 86400), ("30d", 30 * 86400))}
    days = {}
    for r in recs:
        days.setdefault(time.strftime("%Y-%m-%d", time.localtime(r["t"])), []).append(r)
    out["daily"] = [dict(day=d, **_rates(v)) for d, v in sorted(days.items())]
    out["by_conf"] = {k: _rates([r for r in recs if lo <= r["hc"] < hi]) for k, lo, hi in
                      (("course_known", 0.3, 9), ("course_unknown", -1, 0.3))}
    return out
