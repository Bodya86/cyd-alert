"""Журнал прогнозів «проліт біля вас за 30 хв» і фактичних результатів (rt_server, кожна ітерація).

Раз на EVERY_S (60 с) для кожної цілі в межах MAX_KM від користувача (або з p30 > 0) пишеться прогноз:
    {"k":"fc","fid","t","id","ty","cls","la","lo","hd","hc","v","sd","dist","p5","p15","p30","eta","tgt","tp","n","obs","age"}
і зведення по місту {"k":"sum","t","p_any","p_drone","p_missile","lvl","alert"} (alert — тривога в районі користувача).
Траєкторія цілі — сирі положення з постів (Track.last_pos, σ ≤ 20 км), не відфільтроване середнє.
Результат прогнозу — через 30 хв або коли ціль зникла (збита / долетіла / відбій / ttl):
    {"k":"out","fid","id","t_fc","t","min_km","near","n_obs","gone","last","node","alert","alert_end","trail"}
  trail — сирі положення [[ts, lat, lon], …] цілі у вікні (куди вона полетіла насправді);
  min_km — мінімальна відстань від користувача до ламаної спостережень у вікні (t_fc, t_fc+30 хв]
  (з останньою точкою до прогнозу як початком); near = min_km ≤ R; None — нових спостережень не було;
  alert — тривога в районі користувача була в цьому вікні; alert_end — час відбою у вікні (якщо був);
  node — найближчий вузол-ціль history (≤ 25 км) до останньої точки, якщо ціль зникла.
Файли (поза public/, не віддаються по HTTP): <dir>/fc-YYYY-MM-DD.jsonl (append-only, ротація щодня,
>MAX_BYTES → .1, .2 …; старші KEEP_DAYS видаляються), <dir>/pending.json — відкриті прогнози (переживають рестарт).
"""
import glob, json, math, os, time

EVERY_S = 60
HORIZON_S = 1800
MAX_KM = 400.0
KEEP_DAYS = 120
MAX_BYTES = 64 * 1024 * 1024
TRAIL_N = 40
NODE_KM = 25.0
POS_SIGMA = 20.0


def _xy(ulat, ulon, lat, lon):
    return (lon - ulon) * 111.32 * math.cos(math.radians(ulat)), (lat - ulat) * 110.57


def seg_min_km(pts, ulat, ulon):
    """мінімальна відстань від (ulat, ulon) до ламаної pts=[(lat, lon), ...]"""
    if not pts:
        return None
    xy = [_xy(ulat, ulon, la, lo) for la, lo in pts]
    best = min(math.hypot(x, y) for x, y in xy)
    for (ax, ay), (bx, by) in zip(xy, xy[1:]):
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, (-ax * dx - ay * dy) / L2))
        best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


class OutcomeLog:
    def __init__(self, d, R=15.0, every=EVERY_S, horizon=HORIZON_S):
        self.dir, self.R, self.every, self.horizon = d, R, every, horizon
        os.makedirs(d, exist_ok=True)
        self.pending_path = os.path.join(d, "pending.json")
        try:
            with open(self.pending_path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            st = {}
        self.pending = st.get("pending", [])
        self.trails = {int(k): v for k, v in st.get("trails", {}).items()}   # id -> [[ts, lat, lon], ...]
        self.alerts = st.get("alerts", [])          # [[ts, 0/1], ...] — зміни тривоги в районі користувача
        self.last_fc = float(st.get("last_fc", 0))
        self.written = 0

    # ---------------------------------------------------------------- файли
    def _path(self, now):
        base = os.path.join(self.dir, "fc-" + time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl")
        p, k = base, 0
        while os.path.exists(p) and os.path.getsize(p) >= MAX_BYTES:
            k += 1
            p = f"{base}.{k}"
        return p

    def _write(self, recs, now):
        if not recs:
            return
        with open(self._path(now), "a", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.written += len(recs)

    def rotate(self, now=None):
        now = now or time.time()
        for p in glob.glob(os.path.join(self.dir, "fc-*.jsonl*")):
            try:
                if now - os.path.getmtime(p) > KEEP_DAYS * 86400:
                    os.remove(p)
            except OSError:
                pass

    def save(self):
        tmp = self.pending_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(pending=self.pending, trails=self.trails, alerts=self.alerts[-50:], last_fc=self.last_fc),
                      f, separators=(",", ":"))
        os.replace(tmp, self.pending_path)

    # ---------------------------------------------------------------- журнал
    def due(self, now=None):
        return (now or time.time()) - self.last_fc >= self.every

    def observe(self, trk, now):
        """нові спостереження цілей → траєкторії: сире положення з поста (tr.last_pos, σ ≤ POS_SIGMA км),
        а не відфільтроване середнє — щоб результат не залежав від самої моделі руху"""
        alive = set()
        for tr in trk.tracks:
            alive.add(tr.id)
            lp = getattr(tr, "last_pos", None)
            if not lp or lp[3] > POS_SIGMA:
                continue
            tl = self.trails.setdefault(tr.id, [])
            if not tl or tl[-1][0] < lp[0]:
                tl.append(lp[:3])
                del tl[:-TRAIL_N]
        return alive

    def tick(self, trk, ulat, ulon, now=None, alert=None, assess=None, summary=None):
        """assess() → список цілей (Tracker.assess для користувача), summary(targets) → threat_summary.
        Прогнози пишуться не частіше ніж раз на every с. → кількість записаних рядків."""
        now = now or time.time()
        n0 = self.written
        if alert is not None and (not self.alerts or self.alerts[-1][1] != int(alert)):
            self.alerts.append([int(now), int(alert)])
            del self.alerts[:-50]
        alive = self.observe(trk, now)
        recs = []
        if assess is not None and self.due(now):
            self.last_fc = now
            tg = assess()
            for d in tg:
                if d.get("id", 0) <= 0 or d.get("lat") is None or "event" in (d.get("src") or ()):   # балістика-«подія» без треку
                    continue
                p30 = d["p"][30] if 30 in d["p"] else d["p"].get("30", 0)
                if (d.get("dist_km") or 0) > MAX_KM and p30 <= 0:
                    continue
                fid = f"{d['id']}-{int(now)}"
                lt = d.get("likely") or {}
                recs.append(dict(k="fc", fid=fid, t=int(now), id=d["id"], ty=d["type"], cls=d["cls"], la=d["lat"], lo=d["lon"],
                                 hd=d["heading"], hc=d["heading_conf"], v=d["speed"], sd=d["sd_km"], dist=d["dist_km"],
                                 p5=d["p"][5], p15=d["p"][15], p30=p30, eta=d["eta_min"], tgt=lt.get("name"), tp=lt.get("p"),
                                 n=d["count"], obs=d["obs"], age=d["age_s"]))
                tl = self.trails.get(d["id"]) or []
                start = [x for x in tl if x[0] <= now][-1:]   # остання точка до прогнозу — початок ламаної
                self.pending.append(dict(fid=fid, id=d["id"], t=int(now), s=start[0] if start else None))
            if summary is not None:
                s = summary(tg)
                g = lambda k: s[k][30] if 30 in s[k] else s[k].get("30")   # noqa: E731
                recs.append(dict(k="sum", t=int(now), p_any=g("p_any"), p_drone=g("p_drone"), p_missile=g("p_missile"),
                                 lvl=s.get("level"), alert=None if alert is None else int(alert), n=len(tg)))
        recs += self._resolve(trk, alive, ulat, ulon, now)
        for i in [i for i in self.trails if i not in alive and not any(p["id"] == i for p in self.pending)]:
            del self.trails[i]
        self._write(recs, now)
        return self.written - n0

    def _alert_in(self, t0, t1):
        """→ (тривога в районі користувача була у вікні (t0, t1]: 0/1, час першого відбою у вікні або None)"""
        on = 0
        for ts, a in self.alerts:
            if ts <= t0:
                on = a
        win = [(ts, a) for ts, a in self.alerts if t0 < ts <= t1]
        end = next((ts for ts, a in win if not a), None)
        return int(bool(on) or any(a for _, a in win)), end

    def _resolve(self, trk, alive, ulat, ulon, now):
        out, keep = [], []
        hist = getattr(trk, "history", None)
        for p in self.pending:
            t0, t1 = p["t"], p["t"] + self.horizon
            gone = p["id"] not in alive
            if not gone and now < t1:
                keep.append(p)
                continue
            tl = [x for x in self.trails.get(p["id"], []) if t0 < x[0] <= t1]
            pts = ([p["s"]] if p.get("s") else []) + tl
            mk = seg_min_km([(la, lo) for _, la, lo in pts], ulat, ulon) if tl else None
            last = (tl or ([p["s"]] if p.get("s") else [None]))[-1]
            node = None
            if gone and last and hist is not None:
                try:
                    k = hist.node_near(last[1], last[2], NODE_KM)
                    node = hist.nodes[k][0] if k is not None else None
                except Exception:  # noqa: BLE001
                    node = None
            al, aend = self._alert_in(t0, min(t1, now))
            out.append(dict(k="out", fid=p["fid"], id=p["id"], t_fc=t0, t=int(now), min_km=None if mk is None else round(mk, 1),
                            near=None if mk is None else int(mk <= self.R), n_obs=len(tl), gone=int(gone and now < t1),
                            last=last, node=node, alert=al, alert_end=aend, trail=tl))
        self.pending = keep
        return out


def load(d, since=0):
    """→ (fc: dict fid→rec, out: dict fid→rec, sums: [rec]) з усіх fc-*.jsonl у d"""
    fc, out, sums = {}, {}, []
    for p in sorted(glob.glob(os.path.join(d, "fc-*.jsonl*"))):
        with open(p, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("t", 0) < since:
                    continue
                k = r.get("k")
                if k == "fc":
                    fc[r["fid"]] = r
                elif k == "out":
                    out[r["fid"]] = r
                elif k == "sum":
                    sums.append(r)
    return fc, out, sums


def metrics(fc, out, R=15.0):
    """Brier / hit rate для p30 проти «пройшла ≤ R км за 30 хв» (лише прогнози з відомим результатом)."""
    pairs = [(fc[f]["p30"], out[f]["near"], fc[f]) for f in out if f in fc and out[f].get("near") is not None]
    if not pairs:
        return dict(n=0)
    n = len(pairs)
    brier = sum((p - y) ** 2 for p, y, _ in pairs) / n
    base = sum(y for _, y, _ in pairs) / n
    m = dict(n=n, base_rate=round(base, 4), brier=round(brier, 4), brier_clim=round(base * (1 - base), 4))
    for th in (0.15, 0.5):
        tp = sum(1 for p, y, _ in pairs if p >= th and y)
        fp = sum(1 for p, y, _ in pairs if p >= th and not y)
        fn = sum(1 for p, y, _ in pairs if p < th and y)
        m[f"th{th}"] = dict(hit=round(tp / (tp + fn), 3) if tp + fn else None, prec=round(tp / (tp + fp), 3) if tp + fp else None,
                            far=round(fp / (tp + fp), 3) if tp + fp else None)
    tg = [(fc[f]["tgt"], out[f]["node"]) for f in out if f in fc and out[f].get("node") and fc[f].get("tgt")]
    if tg:
        m["target_acc"] = round(sum(a == b for a, b in tg) / len(tg), 3)
        m["n_target"] = len(tg)
    return m
