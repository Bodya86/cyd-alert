#!/usr/bin/env python3
"""Platt-калібрування зведення по місту (tracker.threat_summary → p_any/p_drone/p_missile) на train-частині бектесту.

    python tools/backtest_tracker.py --set '{"CITY_CAL": {}}' --truth /tmp/truth.json --out /tmp/bt_raw.json   # сирі ймовірності
    python tools/fit_city_cal.py --log <log_dir з bt_raw.json> --truth /tmp/truth.json [--split 2026-10-12] [--write]

Правда: сире положення класу ≤ R (15 км) від Кременчука в (t, t+T]. Модель: p' = σ(a·logit(p_raw) + b) по класу й горизонту,
лише для зведень, де є цілі цього класу (інакше 0). --write пише data/model_params.json "city_cal" лише якщо на holdout
калібровані BSS > 0 і кращі за сирі для всіх 30-хв зведень."""
import argparse, bisect, glob, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import backtest_tracker as bt   # noqa: E402  (TZ, km, brier_block, ts_of)
import numpy as np              # noqa: E402

CLS = {"any": None, "drone": "drone"}   # missile: на holdout немає подій — не перевірити, лишається сирим


def lg(p):
    p = np.clip(np.asarray(p, float), 1e-3, 0.999)
    return np.log(p / (1 - p))


def fit(x, y, l2=1e-2):
    X = np.c_[x, np.ones(len(x))]; w = np.array([1.0, 0.0])
    for _ in range(100):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + l2 * w
        H = (X.T * (p * (1 - p))) @ X + l2 * np.eye(2)
        step = np.linalg.solve(H, g); w -= step
        if np.abs(step).max() < 1e-8:
            break
    return [round(float(w[0]), 4), round(float(w[1]), 4)]


def platt(p, ab):
    return 1 / (1 + np.exp(-(ab[0] * lg(p) + ab[1])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--split", default="2026-10-12")
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    split = bt.ts_of(a.split)
    sums = [d for f in sorted(glob.glob(os.path.join(a.log, "fc-*.jsonl"))) for d in map(json.loads, open(f)) if d["k"] == "sum"]
    sums = [s for s in sums if s.get("raw")]
    cd = bt.an.CITIES[bt.CITY]
    near = sorted((t, c) for t, c, la, lo in json.load(open(a.truth)) if bt.km(cd["lat"], cd["lon"], la, lo) <= bt.R)
    nts = [t for t, _ in near]
    t = np.array([s["t"] for s in sums]); tr = t < split; ho = ~tr
    params, rep, ok = {}, {}, True
    for k, cls in CLS.items():
        has = np.array([(s.get("n", 0) if cls is None else s.get("nd" if cls == "drone" else "nm", 0)) > 0 for s in sums])
        for i, T in enumerate((5, 15, 30)):
            pr = np.array([s["raw"][k][i] for s in sums], float)
            y = np.array([int(any(cls is None or c == cls for _, c in near[bisect.bisect_right(nts, s["t"]):bisect.bisect_right(nts, s["t"] + T * 60)]))
                          for s in sums])
            m = tr & has
            if y[m].sum() < 10:
                continue
            ab = fit(lg(pr[m]), y[m])
            pc = np.where(has, platt(pr, ab), 0.0)
            params.setdefault(k, {})[str(T)] = ab
            r = {}
            for part, mm in (("train", tr), ("holdout", ho)):
                r[part] = {"raw": bt.brier_block(list(zip(pr[mm].tolist(), y[mm].tolist()))),
                           "cal": bt.brier_block(list(zip(pc[mm].tolist(), y[mm].tolist())))}
                for v in r[part].values():
                    v.pop("rel", None)
            rep[f"{k}{T}"] = dict(ab=ab, **r)
            print(f"{k:7s} T={T:2d} a,b={ab}  holdout BSS raw {r['holdout']['raw'].get('bss')} → cal {r['holdout']['cal'].get('bss')}"
                  f"  (train {r['train']['raw'].get('bss')} → {r['train']['cal'].get('bss')})")
            if T == 30:
                hb = r["holdout"]["cal"].get("bss")
                ok = ok and hb is not None and hb > 0 and hb > (r["holdout"]["raw"].get("bss") or -9)
    if a.write:
        if not ok:
            print("NOT written: holdout 30-min calibrated BSS not > 0 / not better than raw", file=sys.stderr)
            sys.exit(1)
        p = os.path.join(ROOT, "data", "model_params.json")
        mp = json.load(open(p, encoding="utf-8"))
        mp["city_cal"] = {"enabled": True, "method": "platt σ(a·logit(p)+b), fit on train < " + a.split,
                          "truth": f"сире положення класу ≤ {bt.R:g} км від {bt.CITY} за T хв", "params": params,
                          "holdout_bss": {k: {"raw": v["holdout"]["raw"].get("bss"), "cal": v["holdout"]["cal"].get("bss")} for k, v in rep.items()}}
        json.dump(mp, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("written", p)


if __name__ == "__main__":
    main()
