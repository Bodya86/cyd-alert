#!/usr/bin/env python3
"""Збір архіву публічних постів каналів (t.me/s/<ch>?before=<id>) для історичної моделі маршрутів.

  python3 tools/history_collect.py [--days 120] [--pages 250] [--delay 0.8] [ch ...]
Результат: data/history/raw/<ch>.jsonl ({id, ts, text}), дописується інкрементально (вже зібрані id пропускаються).
Лише публічний веб-перегляд без акаунта; запити повільні (delay) — не навантажуємо t.me.
"""
import argparse, json, os, re, sys, time, urllib.request, html
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
RAW = os.path.join(ROOT, "data", "history", "raw")
UA = "Mozilla/5.0 (X11; Linux x86_64) CYD-Alert-History/1.0"
DEFAULT = ["war_monitor", "kudy_letyt", "kpszsu", "eRadarrua", "mon1tor_ua", "raketa_trevoga", "monitor1654",
           "vanek_nikolaev", "kremen_sv", "ppo_poltava", "poltava_monitor"]


def clean(fr):
    s = re.sub(r"<br\s*/?>", " ", fr); s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def page(ch, before=None):
    url = f"https://t.me/s/{ch}" + (f"?before={before}" if before else "")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        p = r.read().decode("utf-8", "replace")
    out = []
    for chunk in p.split("tgme_widget_message_wrap")[1:]:
        pid = re.search(r'data-post="[^"/]+/(\d+)"', chunk)
        tm = re.search(r'<time datetime="([^"]+)"', chunk)
        tx = re.search(r'js-message_text[^>]*>(.*?)</div>', chunk, re.S)
        if pid and tm:
            out.append({"id": int(pid.group(1)), "ts": int(datetime.fromisoformat(tm.group(1)).timestamp()),
                        "text": clean(tx.group(1)) if tx else ""})
    return out


def collect(ch, days, pages, delay, raw=RAW):
    os.makedirs(raw, exist_ok=True)
    fn = os.path.join(raw, ch + ".jsonl")
    seen, oldest = set(), None
    if os.path.exists(fn):
        for l in open(fn, encoding="utf-8"):
            try:
                d = json.loads(l); seen.add(d["id"]); oldest = d["id"] if oldest is None else min(oldest, d["id"])
            except Exception:
                pass
    stop = time.time() - days * 86400
    f = open(fn, "a", encoding="utf-8")
    # спочатку свіже (до вже зібраного), потім — глибше від найстарішого
    starts = [None] + ([oldest] if oldest else [])
    n_new = 0
    for start in starts:
        before = start
        for k in range(pages):
            try:
                ps = page(ch, before)
            except Exception as e:  # noqa: BLE001
                print(ch, "error", e, file=sys.stderr); time.sleep(5); continue
            if not ps:
                break
            fresh = [p for p in ps if p["id"] not in seen]
            for p in fresh:
                if p["text"]:
                    f.write(json.dumps(p, ensure_ascii=False) + "\n"); n_new += 1
                seen.add(p["id"])
            f.flush()
            mn = min(p["id"] for p in ps)
            if min(p["ts"] for p in ps) < stop or mn <= 1:
                break
            if start is None and not fresh and oldest:
                break   # дійшли до вже зібраного — далі продовжимо від найстарішого
            before = mn
            time.sleep(delay)
    f.close()
    print(ch, "new", n_new, "total", len(seen), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120); ap.add_argument("--pages", type=int, default=250)
    ap.add_argument("--delay", type=float, default=0.8); ap.add_argument("ch", nargs="*")
    ap.add_argument("--dir", default=RAW, help="куди писати (новини — data/history/news_raw)")
    ap.add_argument("--news", action="store_true", help="канали новин з data/news_sources.json → data/history/news_raw")
    a = ap.parse_args()
    chs, raw = a.ch or DEFAULT, a.dir
    if a.news:
        raw = os.path.join(ROOT, "data", "history", "news_raw")
        chs = a.ch or [c["id"] for c in json.load(open(os.path.join(ROOT, "data", "news_sources.json"), encoding="utf-8"))["telegram"]]
    for ch in chs:
        collect(ch, a.days, a.pages, a.delay, raw)
