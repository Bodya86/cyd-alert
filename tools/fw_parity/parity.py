"""Firmware (src/analyzer.cpp) vs server (server/analyzer.py) parity over the whole post archive."""
import glob, json, os, struct, subprocess, sys, collections
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT + "/server")
import analyzer as an
RAW = sys.argv[1] if len(sys.argv) > 1 else ROOT + "/data/history/raw"
TRUNC = 359
city = an.CITIES["kremenchuk"]
dists = [d["id"] for d in city["districts"]]
texts = []
for p in sorted(glob.glob(RAW + "/*.jsonl")):
    for l in open(p, encoding="utf-8"):
        try:
            d = json.loads(l)
        except ValueError:
            continue
        if d.get("text"):
            texts.append(d["text"])
texts = list(dict.fromkeys(texts))
def trunc(s, n=TRUNC):
    b = s.encode()[:n]
    return b.decode("utf-8", "ignore")
# configurations: (mode bits: 1 remote, 2 home), district index
cfgs = [(2, -1, "home/city"), (0, -1, "local-oblast"), (1, -1, "remote"), (2, 0, "home/district0"), (0, 3, "local/district3")]
recs, meta = [], []
for t0 in texts:
    for tr in (True, False):
        t = trunc(t0) if tr else t0
        for mode, di, name in cfgs:
            recs.append((mode, di, t)); meta.append((name, tr))
with open("in.bin", "wb") as f:
    for mode, di, t in recs:
        b = t.encode()
        f.write(struct.pack("<Bbi", mode, di, len(b))); f.write(b)
out = subprocess.run(["./parity", "in.bin"], capture_output=True, text=True, check=True).stdout.splitlines()
assert len(out) == len(recs), (len(out), len(recs))
F = ["score", "level", "near", "cls", "prox", "clear", "ignored", "relevant", "far", "local", "explicit", "oblast_wide", "threat"]
diff = collections.Counter(); ex = collections.defaultdict(list); tot = collections.Counter(); lvl_diff = collections.Counter()
for (mode, di, t), (name, tr), line in zip(recs, meta, out):
    c = line.split("\t")[1:]
    if len(c) < 13: c += [""] * (13 - len(c))
    e = an.analyze_post(t, "kremenchuk", dists[di] if di >= 0 else None, remote=bool(mode & 1), home=bool(mode & 2))
    py = [e.score, e.level, int(e.near), e.cls, e.prox, int(e.clear), int(e.ignored), int(e.relevant), int(e.far), int(e.local),
          int(e.explicit), int(e.oblast_wide), e.threat]
    cc = [int(x) if i < 12 else x for i, x in enumerate(c)]
    key = ("trunc" if tr else "full")
    tot[key] += 1
    bad = [F[i] for i in range(13) if py[i] != cc[i]]
    if py[1] != cc[1]:
        lvl_diff[(key, an.LEVEL_NAMES[py[1]], an.LEVEL_NAMES[cc[1]])] += 1
    if bad:
        diff[(key, tuple(bad))] += 1
        if len(ex[(key, tuple(bad))]) < 4:
            ex[(key, tuple(bad))].append(dict(cfg=name, py=py, fw=cc, text=t[:300]))
res = dict(n_texts=len(texts), totals=tot, level_mismatch={f"{k[0]} py={k[1]} fw={k[2]}": v for k, v in lvl_diff.items()},
           field_mismatch={f"{k[0]}:{'+'.join(k[1])}": v for k, v in diff.most_common()},
           examples={f"{k[0]}:{'+'.join(k[1])}": v for k, v in ex.items()})
json.dump(res, open("parity_result.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: res[k] for k in ("n_texts", "totals", "level_mismatch")}, ensure_ascii=False, indent=1))
print(json.dumps(res["field_mismatch"], ensure_ascii=False, indent=1)[:3000])
