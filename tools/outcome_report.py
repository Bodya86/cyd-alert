#!/usr/bin/env python3
"""Точність прогнозів із живого журналу rt_server (server/outcomes.py).

    python tools/outcome_report.py [dir=/var/lib/cyd-alert/outcomes] [--days 7]
Brier і hit-rate для p30 («ціль пройде ≤ R км за 30 хв») проти фактичної траєкторії; по типах цілей;
точність «ймовірної цілі» для зниклих треків; зведення по місту (p_drone30 проти тривоги в районі)."""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "server"))
import outcomes as oc  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("dir", nargs="?", default="/var/lib/cyd-alert/outcomes")
ap.add_argument("--days", type=float, default=7)
a = ap.parse_args()
fc, out, sums = oc.load(a.dir, since=time.time() - a.days * 86400)
rep = {"forecasts": len(fc), "resolved": len(out), "all": oc.metrics(fc, out)}
for ty in sorted({r["ty"] for r in fc.values()}):
    f2 = {k: v for k, v in fc.items() if v["ty"] == ty}
    rep[ty] = oc.metrics(f2, out)
s2 = [s for s in sums if s.get("alert") is not None]
if s2:
    rep["city_sum"] = dict(n=len(s2), brier_drone_vs_alert=round(sum((s["p_drone"] - s["alert"]) ** 2 for s in s2) / len(s2), 4),
                           alert_rate=round(sum(s["alert"] for s in s2) / len(s2), 3))
print(json.dumps(rep, ensure_ascii=False, indent=1))
