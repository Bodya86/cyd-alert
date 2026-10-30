#!/usr/bin/env python3
"""Надійність каналів з архіву постів (data/history/raw) → data/channel_reliability.json.

Для кожного повідомлення з точним положенням (σ ≤ 20 км, дрон/ракета) шукаємо незалежне підтвердження:
інший канал повідомив сумісне положення в межах ±20 хв і d ≤ σ1 + σ2 + v·|Δt| (v: дрон 3, ракета 12 км/хв).
Рахуємо лише «покриті» повідомлення — коли інші канали писали саме про це місце (≤ 40 км, ±30 хв),
інакше відсутність підтвердження нічого не означає.
  • prec     — частка підтверджених серед покритих (точність місця);
  • type     — збіг класу (дрон/ракета) з підтвердженням за положенням, будь-якого класу;
  • first    — частка підтверджених, де канал був першим (нікого раніше за 2 хв);
  • late     — частка «запізнілих»: інший канал писав про цю ціль ≥ 5 хв раніше (після факту);
  • lag_med  — медіана запізнення відносно першого повідомлення про ціль, хв.
Оцінка s = 0.55·prec + 0.20·type + 0.25·(1 − late), нормована до середньої апріорної ваги цих каналів;
вага w = (n·w_data + K·w_prior)/(n + K) (стягування до ручної ваги з data/channels.json для малих вибірок),
множник σ у трекері (лише з --tracker-sigma): clip(1 + 0.6·(0.8 − w), 1.0, 1.15).
Бектест 30.10 (holdout track_p30): база BSS 0.423; √(0.8/w)∈[0.85,1.6] → 0.407 (гірше);
м'який 1.0–1.15 → 0.420 (hit/prec трохи краще, BSS −0.003) — тому за замовчуванням σ не чіпаємо (1.0),
а кандидат пишемо в sigma_mult_test.
"""
import argparse
import bisect
import glob
import json
import math
import os
import statistics
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import analyzer as an  # noqa: E402
import tracker as tk  # noqa: E402

V = {"drone": 3.0, "missile": 12.0}
WIN, COV_WIN, COV_KM, LATE_MIN = 20 * 60, 30 * 60, 40.0, 5
K = 50.0


def km(a, b, c, d):
    return math.hypot((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * 110.57)


def reports(t0, t1):
    gaz = tk.Gazetteer()
    by_ch = {c["channel"].lower(): c for c in an.LOC["cities"] if c.get("channel")}
    out = []
    for p in sorted(glob.glob(os.path.join(ROOT, "data", "history", "raw", "*.jsonl"))):
        ch = os.path.basename(p)[:-6]
        for line in open(p, encoding="utf-8"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not (t0 <= d.get("ts", 0) < t1) or not d.get("text"):
                continue
            try:
                if an.analyze_post(d["text"], None).ignored:
                    continue
                parts = tk.split_post(d["text"])
                if len(parts) <= 1:
                    parts = [(d["text"], None)]
                for frag, ob in parts:
                    c = tk.parse_cues(frag, gaz, by_ch.get(ch.lower()), ob)
                    cls = tk.TYPES[c["type"]]["cls"] if c["type"] in tk.TYPES else None
                    if c["kill"] or cls not in V:
                        continue
                    for q in c["pos"][:1]:
                        if q.get("sigma", 99) <= 20:
                            out.append((d["ts"], ch, cls, q["lat"], q["lon"], q["sigma"]))
            except Exception:  # noqa: BLE001
                continue
    out.sort()
    return out


def estimate(rep):
    ts = [r[0] for r in rep]
    st = {}
    for i, (t, ch, cls, la, lo, sg) in enumerate(rep):
        s = st.setdefault(ch, dict(n=0, cov=0, conf=0, tmatch=0, tagree=0, first=0, late=0, lags=[]))
        s["n"] += 1
        a, b = bisect.bisect_left(ts, t - COV_WIN), bisect.bisect_right(ts, t + COV_WIN)
        covered, matches, tmat = False, [], []
        for j in range(a, b):
            t2, ch2, cls2, la2, lo2, sg2 = rep[j]
            if ch2 == ch:
                continue
            d = km(la, lo, la2, lo2)
            if d <= COV_KM:
                covered = True
            if abs(t2 - t) > WIN:
                continue
            lim = sg + sg2 + V["drone" if "drone" in (cls, cls2) else "missile"] * abs(t2 - t) / 60
            if d <= lim:
                tmat.append(cls2)
                if cls2 == cls and d <= sg + sg2 + V[cls] * abs(t2 - t) / 60:
                    matches.append(t2)
        if not covered:
            continue
        s["cov"] += 1
        if tmat:
            s["tmatch"] += 1
            s["tagree"] += cls in tmat
        if matches:
            s["conf"] += 1
            t_first = min(min(matches), t)
            lag = (t - t_first) / 60
            s["lags"].append(lag)
            s["first"] += lag < 2
            s["late"] += lag >= LATE_MIN
    return st


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", default="2026-08-01")
    ap.add_argument("--to", default="2026-11-01")
    ap.add_argument("--k", type=float, default=K)
    ap.add_argument("--tracker-sigma", action="store_true", help="застосувати sigma_mult у трекері (бектест не кращий)")
    ap.add_argument("--out", default=os.path.join(ROOT, "data", "channel_reliability.json"))
    a = ap.parse_args()
    t0 = int(datetime.strptime(a.frm, "%Y-%m-%d").timestamp())
    t1 = int(datetime.strptime(a.to, "%Y-%m-%d").timestamp())
    rep = reports(t0, t1)
    st = estimate(rep)
    prior = {c["id"].lower(): c.get("weight", 0.5) for c in an.CHANNELS_CFG_PRIOR}
    rows = {}
    for ch, s in st.items():
        if s["cov"] < 5:
            continue
        prec = s["conf"] / s["cov"]
        typ = s["tagree"] / s["tmatch"] if s["tmatch"] else 1.0
        late = s["late"] / s["conf"] if s["conf"] else 0.0
        rows[ch] = dict(n=s["n"], covered=s["cov"], prec=round(prec, 3), type=round(typ, 3),
                        first=round(s["first"] / s["conf"], 3) if s["conf"] else None, late=round(late, 3),
                        lag_med=round(statistics.median(s["lags"]), 1) if s["lags"] else None,
                        score=0.55 * prec + 0.20 * typ + 0.25 * (1 - late))
    if not rows:
        sys.exit("немає даних")
    mp = statistics.mean(prior.get(ch.lower(), 0.6) for ch in rows)
    ms = statistics.mean(r["score"] for r in rows.values())
    chans = {}
    for ch, r in sorted(rows.items()):
        wp = prior.get(ch.lower(), 0.6)
        wd = min(1.0, max(0.3, r.pop("score") / ms * mp))
        n = r["covered"]
        w = (n * wd + a.k * wp) / (n + a.k)
        sm = round(min(1.15, max(1.0, 1 + 0.6 * (0.8 - w))), 3)
        r.update(prior=wp, w_data=round(wd, 3), weight=round(w, 3), sigma_mult=sm if a.tracker_sigma else 1.0,
                 sigma_mult_test=sm)
        chans[ch] = r
    doc = dict(generated=int(datetime.now().timestamp()), period=[a.frm, a.to], reports=len(rep), k=a.k,
               method="confirm ±20 min, d ≤ σ1+σ2+v·Δt; covered ≤40 km ±30 min; w=(n·w_data+k·w_prior)/(n+k)",
               channels=chans)
    json.dump(doc, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{'channel':16} {'n':>6} {'cov':>6} {'prec':>5} {'type':>5} {'first':>5} {'late':>5} {'lag':>5} {'prior':>5} {'data':>5} {'w':>5} {'σ×':>5}")
    for ch, r in sorted(chans.items(), key=lambda x: -x[1]["weight"]):
        print(f"{ch:16} {r['n']:6} {r['covered']:6} {r['prec']:5} {r['type']:5} {r['first']!s:>5} {r['late']:5} "
              f"{r['lag_med']!s:>5} {r['prior']:5} {r['w_data']:5} {r['weight']:5} {r['sigma_mult_test']:5}")


if __name__ == "__main__":
    main()
