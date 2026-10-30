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


def ld(d):
    """ln км до найближчого свіжого повідомленого положення (tracker.nearest_km); немає — 300 км."""
    d = np.array([300.0 if v is None else v for v in d], float)
    return np.log(np.clip(d, 2.0, 300.0))


def fit(x, y, l2=1e-2):
    """логістична регресія Ньютоном із пошуком кроку; x — (n,) або (n,k) → [a, (c,) b]"""
    x = np.asarray(x, float)
    X = np.c_[x.reshape(len(x), -1), np.ones(len(x))]; w = np.zeros(X.shape[1])
    my = min(max(float(np.mean(y)), 1e-4), 1 - 1e-4); w[-1] = math.log(my / (1 - my))

    def nll(w):
        z = X @ w
        return float(np.sum(np.logaddexp(0, z) - y * z) + 0.5 * l2 * w @ w)
    for _ in range(300):
        p = 1 / (1 + np.exp(-np.clip(X @ w, -30, 30)))
        g = X.T @ (p - y) + l2 * w
        H = (X.T * (p * (1 - p))) @ X + l2 * np.eye(len(w))
        step = np.linalg.solve(H, g); t = 1.0; f0 = nll(w)
        while nll(w - t * step) > f0 and t > 1e-6:
            t /= 2
        w -= t * step
        if np.abs(t * step).max() < 1e-9:
            break
    return [round(float(v), 4) for v in w]


def platt(p, ab, d=None):
    z = ab[0] * lg(p) + ab[-1]
    if len(ab) == 3:
        z = z + ab[1] * ld(d)
    return 1 / (1 + np.exp(-z))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--split", default="2026-10-12")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--dist", action="store_true", help="ознака ln(відстань до свіжого положення цілі класу): σ(a·logit p + c·ln d + b)")
    ap.add_argument("--levels", action="store_true", help="поріг помаранчевого на каліброваному p_drone30 з тією ж повнотою на train, що й старе правило")
    a = ap.parse_args()
    split = bt.ts_of(a.split)
    sums = [d for f in sorted(glob.glob(os.path.join(a.log, "fc-*.jsonl"))) for d in map(json.loads, open(f)) if d["k"] == "sum"]
    sums = [s for s in sums if s.get("raw")]
    cd = bt.an.CITIES[bt.CITY]
    near = sorted((t, c) for t, c, la, lo in json.load(open(a.truth)) if bt.km(cd["lat"], cd["lon"], la, lo) <= bt.R)
    nts = [t for t, _ in near]
    t = np.array([s["t"] for s in sums]); tr = t < split; ho = ~tr
    params, rep, ok, cal = {}, {}, True, {}
    if a.dist and not any(s.get("d_any") is not None for s in sums):
        sys.exit("--dist: у зведеннях немає d_any/d_drone (потрібен свіжий бектест)")
    for k, cls in CLS.items():
        has = np.array([(s.get("n", 0) if cls is None else s.get("nd" if cls == "drone" else "nm", 0)) > 0 for s in sums])
        d = [s.get("d_" + k) for s in sums]
        for i, T in enumerate((5, 15, 30)):
            pr = np.array([s["raw"][k][i] for s in sums], float)
            y = np.array([int(any(cls is None or c == cls for _, c in near[bisect.bisect_right(nts, s["t"]):bisect.bisect_right(nts, s["t"] + T * 60)]))
                          for s in sums])
            m = tr & has
            if y[m].sum() < 10:
                continue
            x = np.c_[lg(pr), ld(d)] if a.dist else lg(pr)
            ab = fit(x[m], y[m])
            pc = np.where(has, platt(pr, ab, d), 0.0)
            cal[(k, T)] = pc
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
    levels = None
    if a.levels:
        # помаранчевий: найвищий поріг на каліброваному p_drone30, що на train дає не меншу повноту, ніж старе правило
        # (сирий p_drone30 ≥ 0.15 або червоний); червоний лишається на сирих (на holdout калібровані не кращі — аудит 30.10)
        y = np.array([int(bisect.bisect_right(nts, s["t"] + 1800) > bisect.bisect_right(nts, s["t"])) for s in sums])
        rd = lambda j: np.array([s["raw"]["drone"][j] or 0 for s in sums])   # noqa: E731
        red = (rd(1) >= 0.5) | (np.array([s["raw"]["missile"][2] or 0 for s in sums]) >= 0.10)
        old_or = (rd(2) >= 0.15) | red
        rec = lambda m, mm: (m & mm & (y == 1)).sum() / max(1, (mm & (y == 1)).sum())   # noqa: E731
        target = rec(old_or, tr)
        th = max(g for g in np.round(np.arange(0.005, 0.6, 0.001), 4) if rec((cal[("drone", 30)] >= g) | red, tr) >= target)
        new_or = (cal[("drone", 30)] >= th) | red
        for part, mm in (("train", tr), ("holdout", ho)):
            print(f"orange {part}: old prec {y[old_or & mm].mean():.3f} rec {rec(old_or, mm):.3f} → new (θ={th}) "
                  f"prec {y[new_or & mm].mean():.3f} rec {rec(new_or, mm):.3f}")
        levels = {"orange": float(th), "red": None}
    if a.write:
        if not ok:
            print("NOT written: holdout 30-min calibrated BSS not > 0 / not better than raw", file=sys.stderr)
            sys.exit(1)
        p = os.path.join(ROOT, "data", "model_params.json")
        mp = json.load(open(p, encoding="utf-8"))
        mp["city_cal"] = {"enabled": True, "method": ("σ(a·logit(p) + c·ln(d_km) + b), d — до свіжого повідомленого положення цілі класу"
                                                     if a.dist else "platt σ(a·logit(p)+b)") + ", fit on train < " + a.split,
                          "truth": f"сире положення класу ≤ {bt.R:g} км від {bt.CITY} за T хв", "params": params,
                          "holdout_bss": {k: {"raw": v["holdout"]["raw"].get("bss"), "cal": v["holdout"]["cal"].get("bss")} for k, v in rep.items()}}
        if levels:
            mp["city_cal"]["levels"] = levels
        json.dump(mp, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("written", p)


if __name__ == "__main__":
    main()
