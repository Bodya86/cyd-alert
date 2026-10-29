#!/usr/bin/env python3
"""Бектест апріорі «новини про влучання» для P(ціль) (server/news.py) поверх історичної моделі.

  python3 tools/news_backtest.py [--write]
Той самий поділ хвиль, що tools/history_model.py: навчання — перші 80% хвиль, тест — останні 20%;
β (сила впливу новин) і half-life підбираються на валідації (останні 20% навчальних хвиль, лише там, де вже є
архів новин), оцінка — на відкладених хвилях. Влучання для моменту t беруться лише ДО t − 30 хв (без підглядання).
--write: записати news_beta/news_hl_days у data/model_params.json, ЛИШЕ якщо на тесті краще (top-1 і log-likelihood)."""
import argparse, bisect, json, math, os, sys

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "server"))
import history_model as hm   # noqa: E402
import news                  # noqa: E402

NEWS_RAW = os.path.join(ROOT, "data", "history", "news_raw")
LAG = 30 * 60


def load_news_events():
    cfg = news.load_cfg()
    w_by = {s["id"]: (s.get("weight", 0.7), s.get("oblast")) for s in cfg.get("telegram", [])}
    chans = {c["id"]: c for c in json.load(open(os.path.join(ROOT, "data", "channels.json"), encoding="utf-8"))["channels"]}
    H = news.NewsHits(keep_days=10 ** 5)
    first = {}
    for d, is_news in ((NEWS_RAW, True), (hm.RAW, False)):
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            if not f.endswith(".jsonl"):
                continue
            ch = f[:-6]
            if is_news:
                w, ob = w_by.get(ch, (0.7, None))
            else:
                c = chans.get(ch, {})
                w = c.get("weight", 0.5) * (1.0 if c.get("kind") == "official" else 0.7)
                ob = (c.get("oblasts") or [None])[0] if c.get("scope") == "oblast" else None
            for line in open(os.path.join(d, f), encoding="utf-8"):
                try:
                    p = json.loads(line)
                except ValueError:
                    continue
                if is_news:
                    first[ch] = min(first.get(ch, p["ts"]), p["ts"])
                H.add_post(ch, p["id"], p["ts"], p["text"], w, ob)
    ev = H.events()
    ev.sort(key=lambda e: e["t"])
    cover = max(first.values()) if first else None     # з цього моменту архів новин повний для всіх каналів
    return ev, cover, len(H.items)


def evaluate(cases, nodes, pairs, prm, nev, beta, hl):
    """cases: [(ts, slat, slon, k_true)] → (top1, top3, ll)."""
    ts_list = [e["t"] for e in nev]
    t1 = t3 = 0; ll = 0.0
    for ts, la, lo, k in cases:
        P = hm.p_target(nodes, pairs, la, lo, None, **prm)
        if beta:
            j = bisect.bisect_left(ts_list, ts - LAG)
            S = news.node_scores(nev[max(0, j - 4000):j], nodes, ts - LAG, hl)
            P = {q: v * (1 + beta * S.get(q, 0.0)) for q, v in P.items()}
            s = sum(P.values()) or 1
            P = {q: v / s for q, v in P.items()}
        top = [q for q, _ in sorted(P.items(), key=lambda x: -x[1])[:3]]
        t1 += int(bool(top) and top[0] == k); t3 += int(k in top)
        ll += math.log(max(P.get(k, 0), 1e-4))
    n = max(1, len(cases))
    return round(t1 / n, 3), round(t3 / n, 3), round(ll / n, 3)


def cases_of(ev, nodes, t0, t1):
    out = []
    for e in ev:
        if e["kind"] != "head" or "slat" not in e or e["typ"] not in hm.DRONES or not (t0 <= e["ts"] < t1):
            continue
        k = hm.node_of(nodes, e["lat"], e["lon"])
        if k is not None:
            out.append((e["ts"], e["slat"], e["slon"], k))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    posts = hm.load_posts()
    geo = hm.Geo()
    ev = hm.extract(posts, geo)
    W = hm.waves(ev)
    split = int(len(W) * 0.8)
    train_w, test_w = W[:split], W[split:]
    t_split = test_w[0][0]["ts"]
    vs = int(len(train_w) * 0.8)
    t_val = train_w[vs][0]["ts"]
    nev, cover, nitems = load_news_events()
    nodes = hm.target_nodes()
    model = json.load(open(hm.OUT, encoding="utf-8"))
    prm = model.get("backtest", {}).get("params") or {}
    ev_train = [e for e in ev if e["ts"] < t_split]
    # валідація: там, де вже ≥14 діб архіву новин (інакше апріорі «порожнє» і β не підібрати)
    v0 = max(t_val, (cover or 0) + 14 * 86400)
    fit_pairs = hm.build_targets([e for e in ev_train if e["ts"] < v0], nodes)
    val = cases_of(ev, nodes, v0, t_split)
    if len(val) < 50:      # замало — валідація на першій половині тесту, оцінка на другій (теж без підглядання)
        mid = cases_of(ev, nodes, t_split, 9e12)
        mid.sort()
        h = len(mid) // 2
        val, test = mid[:h], mid[h:]
        val_pairs = test_pairs = hm.build_targets(ev_train, nodes)
        scheme = "val = перша половина тестових хвиль, test = друга"
    else:
        test = cases_of(ev, nodes, t_split, 9e12)
        val_pairs, test_pairs = fit_pairs, hm.build_targets(ev_train, nodes)
        scheme = "val = кінець навчальних хвиль, test = відкладені хвилі"
    grid = []
    for hl in (3.0, 7.0, 10.0, 14.0):
        for beta in (0.0, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0):
            r = evaluate(val, nodes, val_pairs, prm, nev, beta, hl)
            grid.append(((hl, beta), r))
    best = max(grid, key=lambda x: x[1][2])
    (hl, beta), rv = best
    base_t = evaluate(test, nodes, test_pairs, prm, nev, 0.0, hl)
    news_t = evaluate(test, nodes, test_pairs, prm, nev, beta, hl)
    better = beta > 0 and news_t[2] > base_t[2] and news_t[0] >= base_t[0]
    res = dict(scheme=scheme, news_items=nitems, news_events=len(nev),
               news_cover_from=cover, val_n=len(val), test_n=len(test), chosen=dict(beta=beta, hl_days=hl), val=rv,
               test_history_only=dict(zip(("top1", "top3", "ll"), base_t)),
               test_history_plus_news=dict(zip(("top1", "top3", "ll"), news_t)), enabled=better)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.write:
        pp = news.PARAMS_PATH
        try:
            P = json.load(open(pp, encoding="utf-8"))
        except (OSError, ValueError):
            P = {}
        P["news_beta"] = beta if better else 0.0
        P["news_hl_days"] = hl
        P["news_backtest"] = res
        json.dump(P, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("->", pp, "news_beta =", P["news_beta"])


if __name__ == "__main__":
    main()
