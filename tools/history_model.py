#!/usr/bin/env python3
"""Історична модель маршрутів і цілей атак з архіву публічних Telegram-постів.

  python3 tools/history_collect.py          # 1) зібрати/дописати архів (data/history/raw/*.jsonl)
  python3 tools/history_model.py            # 2) події → хвилі → треки → модель + бектест → data/history_model.json

Модель:
  dir   — поле переходів Маркова на сітці 0.25°: для кожної комірки ймовірності руху в 8 сусідніх напрямках
          (з послідовних положень однієї цілі в межах хвилі та з явних «курсом на X»), згладжене по сусідах.
  pairs — «звідки → куди»: з комірки c у який населений пункт (вузол-ціль) прямували цілі (курс / влучання).
  targets — вузли-цілі (міста) з кількістю повідомлень про влучання/вибухи (апріорна «привабливість»).
Бектест: останні 20% хвиль за часом відкладаються; модель будується на перших 80%.
Геокодер: data/gazetteer.json + GeoNames (CC BY 4.0) населені пункти, data/geonames_ua.json (tools/build_geonames.py).
"""
import argparse, collections, json, math, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))
import tracker as tk  # noqa: E402

RAW = os.path.join(ROOT, "data", "history", "raw")
GN_FILE = os.path.join(ROOT, "data", "geonames_ua.json")   # tools/build_geonames.py
OUT = os.path.join(ROOT, "data", "history_model.json")

LAT_TOP, LON_LEFT, STEP, ROWS, COLS = 52.5, 22.0, 0.25, 34, 74
DIRS = 8                       # 0 = пн, далі за годинниковою, крок 45°
KM_LAT = 110.57

OBL_WORDS = [("крим", 0), ("волин", 1), ("вінниччин", 2), ("винничин", 2), ("дніпропетровщин", 3), ("днепропетровщин", 3),
             ("дніпровщин", 3), ("донеччин", 4), ("донетчин", 4), ("житомирщин", 5), ("закарпатт", 6), ("закарпат", 6),
             ("запоріжж", 7), ("запорожь", 7), ("запорізьк", 7), ("прикарпатт", 8), ("франківщин", 8), ("київщин", 10), ("киевщин", 10),
             ("кіровоградщин", 11), ("кировоградщин", 11), ("луганщин", 12), ("львівщин", 13), ("львовщин", 13),
             ("миколаївщин", 14), ("николаевщин", 14), ("одещин", 15), ("одесчин", 15), ("полтавщин", 16), ("рівненщин", 17),
             ("ровенщин", 17), ("сумщин", 19), ("тернопільщин", 20), ("тернопольщин", 20), ("харківщин", 21), ("харьковщин", 21),
             ("херсонщин", 22), ("хмельниччин", 23), ("хмельнитчин", 23), ("черкащин", 24), ("буковин", 25),
             ("чернігівщин", 26), ("черниговщин", 26)]
HIT_WORDS = ("вибух", "взрыв", "прильот", "прилет", "влучан", "попадан", "уражен", "пожеж", "пожар", "детонац")
TYPE_EXTRA = [("jet", ("швидкісн", "скоростн")), ("shahed", ("🅿", "🛵", "мопед"))]
DRONES = ("shahed", "jet", "unknown", None)
TRK_W = 0.3     # вага переходів із ланцюжків положень (асоціація «та сама ціль» ненадійна) проти явних «курсом на»
PRIOR_GAMMA, PRIOR_LAMBDA = 0.7, 180.0   # «привабливість» (1+згадок)^γ · exp(−d/λ)
PAIR_BASE = 0.15  # вага апріорі «відстань × влучання» проти історичних пар «звідки → куди» (підбирається на валідації)


def cell_of(lat, lon):
    r, c = int((LAT_TOP - lat) / STEP), int((lon - LON_LEFT) / STEP)
    return r * COLS + c if 0 <= r < ROWS and 0 <= c < COLS else None


def cell_center(i):
    r, c = divmod(i, COLS)
    return LAT_TOP - (r + 0.5) * STEP, LON_LEFT + (c + 0.5) * STEP


def km(a, b, c, d):
    dx = (d - b) * 111.32 * math.cos(math.radians((a + c) / 2)); dy = (c - a) * KM_LAT
    return math.hypot(dx, dy)


def bearing(a, b, c, d):
    dx = (d - b) * 111.32 * math.cos(math.radians((a + c) / 2)); dy = (c - a) * KM_LAT
    return math.degrees(math.atan2(dx, dy)) % 360


def dir_bin(brg):
    return int(((brg + 22.5) % 360) // 45)


# ---------------- геокодер ----------------
APOS = ("\u2019", "\u02bc")


def _apos(s):
    for a in APOS:
        s = s.replace(a, "'")
    return s


_GN = None


def geonames():
    global _GN
    if _GN is None:
        _GN = json.load(open(GN_FILE, encoding="utf-8")) if os.path.exists(GN_FILE) else []
    return _GN


class Geo:
    """Пошук населеного пункту за словом з урахуванням відмінка та контексту області.
    Малі села — лише з великої літери і майже точним збігом (щоб «ворожої» не ставало Ворожбою)."""
    STOP = {"курс", "курсом", "напрямку", "напрямок", "район", "районі", "області", "область", "місто", "місті", "увага",
            "загроза", "ціль", "цілі", "ракета", "ракети", "дрони", "дрон", "шахеди", "шахед", "група", "групи", "нова",
            "мирне", "мирний", "зоря", "весела", "веселе", "перемога", "травня", "жовтня", "листопада", "вересня",
            "серпня", "грудня", "січня", "лютого", "березня", "квітня", "червня", "липня", "повз", "через", "тепер",
            "вище", "нижче", "чисто", "тиша", "відбій", "ворожі", "ворожої", "ворога", "також", "більше", "кілька",
            "схід", "захід", "північ", "південь", "сходу", "заходу", "півночі", "півдня", "центр", "центру", "моря",
            "море", "річки", "річка", "дніпра", "десни", "межі", "кордон", "кордону", "лінія", "лінії", "фронт", "фронту",
            "мінус", "плюс", "дорозвідка", "розвідка", "швидкісна", "реактивний", "балістика", "загрози"}

    def __init__(self):
        self.idx = collections.defaultdict(list)
        base = json.load(open(os.path.join(ROOT, "data", "gazetteer.json"), encoding="utf-8"))
        for name, lat, lon, ob, ri in base:
            if name.endswith("*р") or name.endswith("щ") or (ri < 0 and ob < 0 and len(name) < 5):
                continue
            self.add(name, lat, lon, ob, 50000)
        for names, lat, lon, ob, pop in geonames():
            for n in names:
                self.add(n, lat, lon, ob, pop)

    def add(self, name, lat, lon, ob, pop):
        if name not in self.STOP:
            self.idx[name[:4]].append((name, lat, lon, ob, pop))

    def find(self, word, cap, oblast=None, near=None):
        w = _apos(word.lower().strip("'-.,:;!?()\""))
        if len(w) < 4 or w in self.STOP:
            return None
        best = None
        for name, lat, lon, ob, pop in self.idx.get(w[:4], ()):
            if w == name:
                q = 3
            elif len(name) >= 5 and w[:len(name) - 1] == name[:-1] and len(w) - len(name) <= 3:
                q = 2
            elif len(name) >= 7 and w[:len(name) - 2] == name[:-2] and len(w) - len(name) <= 3:
                q = 1
            else:
                continue
            if pop < 3000 and (not cap or q < 2):
                continue
            if pop < 300 and oblast is None and near is None:
                continue
            s = q + math.log10(pop + 10)
            if oblast is not None and ob == oblast:
                s += 4
            elif oblast is not None and ob >= 0:
                s -= 3
            if near is not None:
                d = km(near[0], near[1], lat, lon)
                s += 3 if d < 120 else (-2 if d > 400 else 0)
            if not best or s > best[0]:
                best = (s, dict(name=name, lat=lat, lon=lon, ob=ob, pop=pop))
        return best[1] if best and best[0] > 3.5 else None


# ---------------- події ----------------
HEAD = re.compile(r"(курсом на|курс на|у бік|в бік|в сторону|в напрямку|у напрямку|напрямок на|на|до|к|летить на|летять на|рухається на|повз|мимо)$")
FROM = re.compile(r"(з|із|зі|від|со|с|из|з боку|со стороны|зі сторони)$")
ARROW = "\u2192"


def segments(text):
    """Пост → сегменти за областями («Полтавщина: … Житомирщина: …») і рядками."""
    parts = re.split(r"(?=(?:[А-ЯІЇЄҐ][а-яіїєґ']+(?:щина|щини|ччина|ччини|ття|жжя)|Київ|Крим)\s*[:—-])|\n|\|", text)
    return [p.strip() for p in parts if p and p.strip()]


def seg_oblast(seg):
    low = seg.lower()
    best = None
    for w, ob in OBL_WORDS:
        i = low.find(w)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, ob)
    return best[1] if best else None


def seg_type(seg):
    typ = tk.detect_type(" " + tk._norm(seg) + " ", seg)
    for t, kws in TYPE_EXTRA:
        if typ in (None, "unknown") and any(k in seg.lower() or k in seg for k in kws):
            typ = t
    return typ


def extract(posts, geo):
    """→ події dict(ts, ch, typ, kind: pos|head|hit, lat, lon, name, ob[, slat, slon — звідки курс])"""
    ev = []
    for p in posts:
        txt = p["text"]
        if not txt or len(txt) > 700:            # довгі огляди/аналітика — шум
            continue
        ctx_ob = None
        for seg in segments(txt):
            ob = seg_oblast(seg)
            if ob is not None:
                ctx_ob = ob
            low = seg.lower()
            typ = seg_type(seg)
            hit = any(h in low for h in HIT_WORDS)
            if typ == "ballistic" and not hit:
                continue
            toks = re.findall(r"[A-Za-zА-ЯІЇЄҐа-яіїєґ'\u2019\u02bc\-]+|" + ARROW, seg)
            pos, head, near = [], [], None
            # «Київ: 🅿️ Оболонь» / «Полтавщина: …» — префікс до двокрапки лише контекст, не положення
            colon = seg.find(":")
            ntok_prefix = len(re.findall(r"[A-Za-zА-ЯІЇЄҐа-яіїєґ'\u2019\u02bc\-]+|" + ARROW, seg[:colon])) if 0 < colon < 40 else 0
            for i, t in enumerate(toks):
                if t == ARROW or len(t) < 4:
                    continue
                g = geo.find(t, t[:1].isupper(), ctx_ob, near)
                if not g:
                    continue
                if i < ntok_prefix and len(toks) > ntok_prefix:
                    near = (g["lat"], g["lon"])
                    continue
                before = " ".join(x.lower() for x in toks[max(0, i - 2):i])
                if (i > 0 and toks[i - 1] == ARROW) or HEAD.search(before):
                    head.append(g)
                elif FROM.search(before):
                    continue
                else:
                    pos.append(g)
                near = (g["lat"], g["lon"])
            if hit and (pos or head):
                g = (pos or head)[0]
                ev.append(dict(ts=p["ts"], ch=p["ch"], typ=typ, kind="hit", lat=g["lat"], lon=g["lon"], name=g["name"], ob=g["ob"]))
            if typ not in DRONES and typ not in ("cruise", "kab", "recon"):
                continue
            src = pos[-1] if pos else None
            for g in pos:
                ev.append(dict(ts=p["ts"], ch=p["ch"], typ=typ, kind="pos", lat=g["lat"], lon=g["lon"], name=g["name"], ob=g["ob"]))
            for g in head:
                e = dict(ts=p["ts"], ch=p["ch"], typ=typ, kind="head", lat=g["lat"], lon=g["lon"], name=g["name"], ob=g["ob"])
                if src and 12 < km(src["lat"], src["lon"], g["lat"], g["lon"]) < 260:
                    e["slat"], e["slon"] = src["lat"], src["lon"]
                ev.append(e)
    ev.sort(key=lambda e: e["ts"])
    return ev


def waves(ev, gap_min=90):
    """Хвиля — події дронів без перерви > gap_min хв."""
    out, cur, last = [], [], None
    for e in ev:
        if e["typ"] not in DRONES:
            continue
        if last is not None and e["ts"] - last > gap_min * 60:
            if len(cur) >= 3:
                out.append(cur)
            cur = []
        cur.append(e); last = e["ts"]
    if len(cur) >= 3:
        out.append(cur)
    return out


def chain(wave, vmax=230.0):
    """Жадібна асоціація положень у треки: найближчий трек, досяжний за швидкістю; неоднозначне — новий трек."""
    tracks = []
    for e in wave:
        if e["kind"] != "pos":
            continue
        cand = []
        for tr in tracks:
            dt = (e["ts"] - tr[-1]["ts"]) / 3600
            if tr[-1]["ch"] != e["ch"] or dt < 0 or dt > 0.75:   # один канал веде одну ціль послідовно
                continue
            d = km(tr[-1]["lat"], tr[-1]["lon"], e["lat"], e["lon"])
            if d <= vmax * dt + 25:
                cand.append((d, tr))
        cand.sort(key=lambda x: x[0])
        if cand and (len(cand) == 1 or cand[0][0] < 0.6 * cand[1][0] or cand[0][0] < 5):
            tr = cand[0][1]
            # повторна згадка вже пройденого місця («Одеса … Фонтанка … Одеса») — не рух назад
            if all(km(q["lat"], q["lon"], e["lat"], e["lon"]) >= 15 for q in tr[-4:]):
                tr.append(e)
        else:
            tracks.append([e])
    return [t for t in tracks if len(t) >= 2]


def transitions(wave_list):
    """→ [(cell, dir, lat, lon, brg, prev_brg|None, src)] з ланцюжків положень і явних «курсом на»."""
    out = []
    for w in wave_list:
        for tr in chain(w):
            prev = None
            for a, b in zip(tr, tr[1:]):
                d = km(a["lat"], a["lon"], b["lat"], b["lon"])
                c = cell_of(a["lat"], a["lon"])
                if d < 12 or d > 260 or c is None:
                    continue
                brg = bearing(a["lat"], a["lon"], b["lat"], b["lon"])
                out.append((c, dir_bin(brg), a["lat"], a["lon"], brg, prev, "trk"))
                prev = brg
        for e in w:
            if e["kind"] == "head" and "slat" in e:
                c = cell_of(e["slat"], e["slon"])
                if c is not None:
                    brg = bearing(e["slat"], e["slon"], e["lat"], e["lon"])
                    out.append((c, dir_bin(brg), e["slat"], e["slon"], brg, None, "head"))
    return out


# ---------------- модель ----------------
def build_dir(trans):
    """Лічильники напрямків на комірку, згладжені 3×3 (центр 1, сусіди 0.35)."""
    cnt = collections.defaultdict(lambda: [0.0] * DIRS)
    for c, d, la, lo, brg, prev, src in trans:
        wt = 1.0 if src == "head" else TRK_W
        r, q = divmod(c, COLS)
        for dr in (-1, 0, 1):
            for dq in (-1, 0, 1):
                rr, qq = r + dr, q + dq
                if 0 <= rr < ROWS and 0 <= qq < COLS:
                    cnt[rr * COLS + qq][d] += wt * (1.0 if dr == dq == 0 else 0.35)
    return {c: v for c, v in cnt.items() if sum(v) >= 0.7}


def dir_prob(model, c, alpha=0.5):
    v = model.get(c)
    if not v:
        return None
    s = sum(v) + alpha * DIRS
    return [(x + alpha) / s for x in v]


def target_nodes():
    """Вузли-цілі: міста з locations.json + населені пункти від 40 тис. (не ближче 20 км один до одного)."""
    locs = json.load(open(os.path.join(ROOT, "data", "locations.json"), encoding="utf-8"))["cities"]
    nodes = [[c["name"], c["lat"], c["lon"], 0, 0] for c in locs]
    for names, lat, lon, ob, pop in geonames():
        if pop < 40000:
            break
        if all(km(lat, lon, n[1], n[2]) > 20 for n in nodes):
            nodes.append([max(names, key=len).capitalize(), lat, lon, 0, 0])
    return nodes


def node_of(nodes, lat, lon, maxkm=25):
    best = min(range(len(nodes)), key=lambda i: km(lat, lon, nodes[i][1], nodes[i][2]))
    return best if km(lat, lon, nodes[best][1], nodes[best][2]) <= maxkm else None


def build_targets(ev, nodes):
    """nodes[k][3] — повідомлень про вибухи/влучання, [4] — «курсом на» вузол; pairs[cell][k] — з комірки на вузол."""
    pairs = collections.defaultdict(collections.Counter)
    for n in nodes:
        n[3] = n[4] = 0
    for e in ev:
        k = node_of(nodes, e["lat"], e["lon"])
        if k is None:
            continue
        if e["kind"] == "hit":
            nodes[k][3] += 1
        elif e["kind"] == "head" and e["typ"] in DRONES:
            nodes[k][4] += 1
            if "slat" in e:
                c = cell_of(e["slat"], e["slon"])
                if c is not None:
                    pairs[c][k] += 1
    return pairs


def p_target(nodes, pairs, lat, lon, hd=None, maxkm=350, use_pairs=True, use_hits=True, base=None, gamma=None, lam=None):
    """P(ціль атаки = вузол | ціль зараз у (lat, lon), курс hd°) → {вузол: p}. Та сама формула — у server/history.py."""
    base = PAIR_BASE if base is None else base
    gamma = PRIOR_GAMMA if gamma is None else gamma
    lam = PRIOR_LAMBDA if lam is None else lam
    c = cell_of(lat, lon)
    r, q = divmod(c, COLS) if c is not None else (-99, -99)
    w = collections.Counter()
    if use_pairs and c is not None:
        for dr in (-1, 0, 1):
            for dq in (-1, 0, 1):
                for k, n in pairs.get((r + dr) * COLS + (q + dq), {}).items():
                    w[k] += n * (1.0 if dr == dq == 0 else 0.4)
    out = {}
    for k, n in enumerate(nodes):
        d = km(lat, lon, n[1], n[2])
        if d > maxkm or d < 3:
            continue
        v = (1 + (n[3] + n[4] if use_hits else 0)) ** gamma * math.exp(-d / lam) * base + (w.get(k, 0) if use_pairs else 0)
        if hd is not None:
            v *= math.exp(2.0 * (math.cos(math.radians(bearing(lat, lon, n[1], n[2]) - hd)) - 1))
        out[k] = v
    s = sum(out.values()) or 1
    return {k: v / s for k, v in out.items()}


# ---------------- бектест ----------------
def _i0(x):
    s, t, k = 1.0, 1.0, 1
    while t > 1e-9 * s:
        t *= (x / 2) ** 2 / (k * k); s += t; k += 1
    return s


def backtest(test_tr, dmodel, nodes, pairs, test_ev, K=2.5, prm=None):
    """Порівняння на відкладених хвилях (модель навчена лише на ранніх)."""
    res = {}
    # 1) напрям наступного кроку цілі: рівномірний / лише поле / інерція (фон Мізес κ) / інерція × поле
    acc = collections.Counter(); ll = collections.Counter(); cnt = collections.Counter()
    for c, d, la, lo, brg, prev, src in test_tr:
        grp = "explicit_heading" if src == "head" else "chained_positions"
        pf = dir_prob(dmodel, c)
        cnt[grp] += 1
        if pf:
            cnt[grp + "_covered"] += 1
        cand = {"uniform": [1 / DIRS] * DIRS, "field": pf or [1 / DIRS] * DIRS}
        if prev is not None:
            per = [math.exp(K * math.cos(math.radians(k * 45 - prev))) for k in range(DIRS)]
            s = sum(per); cand["persist"] = [x / s for x in per]
        for name, pv in cand.items():
            acc[grp, name] += int(max(range(DIRS), key=lambda i: pv[i]) == d)
            ll[grp, name] += math.log(max(pv[d], 1e-6))
            cnt[grp, name] += 1
    res["next_dir"] = {grp: dict(n=cnt[grp], covered=cnt[grp + "_covered"],
                                 **{name: dict(n=cnt[g, name], top1=round(acc[g, name] / max(1, cnt[g, name]), 3),
                                               ll_per_step=round(ll[g, name] / max(1, cnt[g, name]), 3))
                                    for (g, name) in list(acc) if g == grp})
                       for grp in ("explicit_heading", "chained_positions")}
    # 2) куди летить: явні «курсом на X» у відкладених хвилях, без курсу (лише положення)
    hits = collections.Counter(); lls = collections.Counter(); m = 0
    for e in test_ev:
        if e["kind"] != "head" or "slat" not in e or e["typ"] not in DRONES:
            continue
        k = node_of(nodes, e["lat"], e["lon"])
        if k is None:
            continue
        m += 1
        prm = prm or {}
        for name, kw in (("distance_only", dict(use_pairs=False, use_hits=False, lam=prm.get("lam"))),
                         ("hits_prior", dict(use_pairs=False, gamma=prm.get("gamma"), lam=prm.get("lam"))),
                         ("history_prior", prm)):
            P = p_target(nodes, pairs, e["slat"], e["slon"], None, **kw)
            top = [t[0] for t in sorted(P.items(), key=lambda x: -x[1])[:3]]
            hits[name, 1] += int(bool(top) and top[0] == k)
            hits[name, 3] += int(k in top)
            lls[name] += math.log(max(P.get(k, 0), 1e-4))
    res["target"] = dict(n=m, **{name: dict(top1=round(hits[name, 1] / max(1, m), 3), top3=round(hits[name, 3] / max(1, m), 3),
                                            ll_per_case=round(lls[name] / max(1, m), 3))
                                 for name in ("distance_only", "hits_prior", "history_prior")})
    return res


def load_posts():
    posts = []
    if not os.path.isdir(RAW):
        return posts
    for f in sorted(os.listdir(RAW)):
        if f.endswith(".jsonl"):
            for line in open(os.path.join(RAW, f), encoding="utf-8"):
                try:
                    p = json.loads(line)
                except ValueError:
                    continue
                p["ch"] = f[:-6]
                posts.append(p)
    return posts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    t0 = time.time()
    posts = load_posts()
    if not posts:
        sys.exit("архів порожній — спершу tools/history_collect.py")
    if not geonames():
        print("увага: немає data/geonames_ua.json — геокодер лише з data/gazetteer.json", file=sys.stderr)
    geo = Geo()
    ev = extract(posts, geo)
    W = waves(ev)
    split = int(len(W) * 0.8)
    train_w, test_w = W[:split], W[split:]
    t_split = test_w[0][0]["ts"] if test_w else ev[-1]["ts"] + 1
    ev_train = [e for e in ev if e["ts"] < t_split]; ev_test = [e for e in ev if e["ts"] >= t_split]
    tr_train, tr_test = transitions(train_w), transitions(test_w)
    nodes = target_nodes()
    # вага апріорі для P(ціль): підбір на валідації всередині навчальної частини (останні 20% навчальних хвиль)
    vs = int(len(train_w) * 0.8)
    t_val = train_w[vs][0]["ts"] if vs < len(train_w) else t_split
    fit_ev = [e for e in ev_train if e["ts"] < t_val]; val_ev = [e for e in ev_train if e["ts"] >= t_val]
    fit_pairs = build_targets(fit_ev, nodes)
    best = None
    for b in (0.02, 0.05, 0.15, 0.5, 2.0):
        for g in (0.0, 0.3, 0.7):
            for lam in (60.0, 120.0, 250.0):
                prm = dict(base=b, gamma=g, lam=lam)
                r = backtest([], {}, nodes, fit_pairs, val_ev, prm=prm)["target"]
                if r["n"] and (best is None or r["history_prior"]["ll_per_case"] > best[1]):
                    best = (prm, r["history_prior"]["ll_per_case"])
    prm = best[0] if best else dict(base=PAIR_BASE, gamma=PRIOR_GAMMA, lam=PRIOR_LAMBDA)
    # базові лінії з тим самим λ (чесно: «лише відстань» теж отримує підібраний масштаб)
    bt = backtest(tr_test, build_dir(tr_train), nodes, build_targets(ev_train, nodes), ev_test, prm=prm)
    bt["params"] = prm
    # фінальна модель — на всіх даних
    tr_all = tr_train + tr_test
    dm = build_dir(tr_all)
    pairs = build_targets(ev, nodes)
    heat = collections.Counter()
    for c, *_ in tr_all:
        heat[c] += 1
    for e in ev:
        if e["kind"] == "pos" and e["typ"] in DRONES:
            c = cell_of(e["lat"], e["lon"])
            if c is not None:
                heat[c] += 0.5
    stats = dict(posts=len(posts), channels=dict(collections.Counter(p["ch"] for p in posts)),
                 first=min(p["ts"] for p in posts), last=max(p["ts"] for p in posts),
                 events=len(ev), events_by_kind=dict(collections.Counter(e["kind"] for e in ev)),
                 waves=len(W), train_waves=len(train_w), test_waves=len(test_w), split_ts=t_split,
                 transitions=len(tr_all), transitions_by_src=dict(collections.Counter(t[6] for t in tr_all)),
                 cells=len(dm), build_s=round(time.time() - t0, 1))
    model = dict(v=1, built=int(time.time()), params=prm,
                 grid=dict(lat0=LAT_TOP, lon0=LON_LEFT, step=STEP, rows=ROWS, cols=COLS, dirs="N,NE,E,SE,S,SW,W,NW"),
                 dir={str(c): [round(x, 2) for x in v] for c, v in sorted(dm.items())},
                 heat={str(c): round(v, 1) for c, v in sorted(heat.items())},
                 targets=[[n[0], n[1], n[2], n[3], n[4]] for n in nodes],
                 pairs={str(c): [[k, n] for k, n in cnt.most_common(8)] for c, cnt in sorted(pairs.items())},
                 stats=stats, backtest=bt)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(model, f, ensure_ascii=False, separators=(",", ":"))
    print(json.dumps(dict(stats=stats, backtest=bt), ensure_ascii=False, indent=1))
    print("->", a.out, os.path.getsize(a.out), "B")


if __name__ == "__main__":
    main()
