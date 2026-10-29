#!/usr/bin/env python3
"""Щотижнева перекалібровка ваг моделі маршрутів (history.yml) — пошук по сітці на останніх N хвилях.

    python tools/recalibrate.py [--waves 60] [--dry-run]

Параметри (data/model_params.json):
  trk_w     — вага переходів із ланцюжків положень проти явних «курсом на X» у полі напрямків (tools/history_model.py);
  prior_mix — частка частинок, курс яких береться з історичного поля, коли курс цілі невідомий (server/tracker.py).
Схема: модель напрямків навчається на хвилях ДО останніх N; останні N діляться навпіл за часом:
перша половина — валідація (вибір параметрів за log-likelihood наступного кроку), друга — відкладена (holdout).
Нові параметри записуються, лише якщо на holdout вони кращі за поточні (≥ 0.002 нат/крок).
Кожен запуск (і прийнятий, і відхилений) дописується в data/calibration_log.json — для сторінки stats.html.
"""
import argparse, json, math, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import history_model as hm  # noqa: E402

PARAMS = os.path.join(ROOT, "data", "model_params.json")
LOG = os.path.join(ROOT, "data", "calibration_log.json")
DEFAULT = dict(trk_w=0.3, prior_mix=0.6)
GRID_TRK_W = (0.0, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0)
GRID_MIX = (0.2, 0.4, 0.6, 0.8, 0.95)
MIN_GAIN = 0.002


def load_params():
    try:
        with open(PARAMS, encoding="utf-8") as f:
            return {**DEFAULT, **json.load(f)}
    except (OSError, ValueError):
        return dict(DEFAULT)


def build_dir_w(trans, trk_w):
    old = hm.TRK_W
    hm.TRK_W = trk_w
    try:
        return hm.build_dir(trans)
    finally:
        hm.TRK_W = old


def score(model, trans, mix):
    """→ (log-likelihood на крок, top-1, n): суміш mix·поле + (1−mix)·рівномірний, як ініціалізація частинок"""
    ll = acc = n = 0
    for c, d, *_ in trans:
        pf = hm.dir_prob(model, c)
        pv = [mix * p + (1 - mix) / hm.DIRS for p in pf] if pf else [1 / hm.DIRS] * hm.DIRS
        ll += math.log(max(pv[d], 1e-6)); acc += int(max(range(hm.DIRS), key=lambda i: pv[i]) == d); n += 1
    return (ll / n if n else 0.0), (acc / n if n else 0.0), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--waves", type=int, default=60, help="скільки останніх хвиль для валідації+holdout")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    posts = hm.load_posts()
    if not posts:
        sys.exit("архів порожній — спершу tools/history_collect.py")
    ev = hm.extract(posts, hm.Geo())
    W = hm.waves(ev)
    N = min(a.waves, max(4, len(W) // 3))
    if len(W) < N + 10:
        sys.exit(f"замало хвиль: {len(W)}")
    train_w, recent = W[:-N], W[-N:]
    val_w, hold_w = recent[:N // 2], recent[N // 2:]
    tr_train, tr_val, tr_hold = hm.transitions(train_w), hm.transitions(val_w), hm.transitions(hold_w)
    cur = load_params()

    best = None
    models = {}
    for tw in GRID_TRK_W:
        models[tw] = build_dir_w(tr_train, tw)
        for mix in GRID_MIX:
            ll, t1, n = score(models[tw], tr_val, mix)
            if best is None or ll > best[0]:
                best = (ll, dict(trk_w=tw, prior_mix=mix))
    new = best[1]
    m_cur = models.get(cur["trk_w"]) or build_dir_w(tr_train, cur["trk_w"])
    h_cur = score(m_cur, tr_hold, cur["prior_mix"])
    h_new = score(models[new["trk_w"]], tr_hold, new["prior_mix"])
    accept = h_new[0] - h_cur[0] >= MIN_GAIN and new != {k: cur[k] for k in new}
    entry = dict(ts=int(time.time()), waves=len(W), val_waves=len(val_w), holdout_waves=len(hold_w),
                 holdout_steps=h_cur[2], current={k: cur[k] for k in DEFAULT}, proposed=new,
                 holdout=dict(current=dict(ll=round(h_cur[0], 4), top1=round(h_cur[1], 3)),
                              proposed=dict(ll=round(h_new[0], 4), top1=round(h_new[1], 3)),
                              uniform=dict(ll=round(math.log(1 / hm.DIRS), 4), top1=round(1 / hm.DIRS, 3))),
                 accepted=bool(accept))
    print(json.dumps(entry, ensure_ascii=False, indent=1))
    if a.dry_run:
        return 0
    if accept:
        out = {**cur, **new, "updated": entry["ts"], "holdout_ll": entry["holdout"]["proposed"]["ll"],
               "holdout_top1": entry["holdout"]["proposed"]["top1"]}
        with open(PARAMS, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("-> прийнято:", PARAMS)
    else:
        print("-> параметри без змін (на holdout не краще)")
    try:
        with open(LOG, encoding="utf-8") as f:
            log = json.load(f)
    except (OSError, ValueError):
        log = []
    log = (log + [entry])[-52:]
    with open(LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
