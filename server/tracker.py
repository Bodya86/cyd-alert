"""Злиття джерел і ймовірнісний прогноз траєкторій повітряних цілей (частинковий фільтр).

Кожна ціль — хмара з N частинок (x, y [км], курс [рад], швидкість [км/год], пройдено [км]).
Рух: постійна швидкість із випадковим блуканням курсу (σ залежить від типу) і швидкості.
Спостереження (усі — ймовірнісні, ваги частинок множаться на правдоподібність):
  * положення «над X / в районі X / біля X» (газетир)        — гаусс, σ 6–15 км (+ похибка затримки);
  * «курс на X / в бік X»                                     — фон Мізес: курс ↔ азимут частинка→X;
  * «з боку X / зі сторони X»                                 — фон Мізес: курс ↔ азимут X→частинка;
  * тривога/сповіщення по району (JAAM, alerts.in.ua, api.ukrainealarm.com) — слабке: ε + (1-ε)·гаусс
    від межі району (σ 25 км); відбій у районі — м'яке зниження ваги частинок усередині;
  * «збито / мінус» поруч з ціллю — ціль знімається.
Прогноз: кожна частинка просувається на 30 хв кроком 1 хв; P(R, T) = Σ w·[мінімальна відстань до
користувача на відрізках шляху ≤ R до моменту T]; ETA — зважена медіана моменту входу в коло R.
Конус: середнє положення й поперечне σ (км) на 0/5/10/15/20/30 хв.
"""
import math, random, re, time, json, os

DATA = os.environ.get("CYD_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
N = 300
KM_LAT = 110.57
TWO_PI = 2 * math.pi

# тип: швидкість (μ, σ) км/год, σ курсу °/√хв, час життя без спостережень, клас, дальність (км), підпис, символ
TYPES = {
    "shahed":    dict(v=(170, 15),  turn=3, ttl=35 * 60, cls="drone",   rng=None, label="Шахед",            ch="S"),
    "jet":       dict(v=(450, 60),  turn=2,  ttl=15 * 60, cls="drone",   rng=None, label="Реактивний шахед", ch="J"),
    "recon":     dict(v=(120, 25),  turn=6, ttl=35 * 60, cls="drone",   rng=None, label="Розвідник",        ch="D"),
    "unknown":   dict(v=(200, 80),  turn=4, ttl=25 * 60, cls="drone",   rng=None, label="Ціль",             ch="U"),
    "cruise":    dict(v=(800, 80),  turn=1.5, ttl=12 * 60, cls="missile", rng=None, label="Крилата ракета",   ch="C"),
    "kab":       dict(v=(450, 100), turn=1,  ttl=6 * 60,  cls="missile", rng=70,   label="КАБ",              ch="K"),
    "ballistic": dict(v=(3000, 500), turn=1, ttl=10 * 60,  cls="missile", rng=None, label="Балістика",        ch="B"),
}
DRONE_TYPES = {k for k, v in TYPES.items() if v["cls"] == "drone"}


# ---------------- геометрія ----------------
class Proj:
    """Локальна рівнопроміжна проєкція навколо (lat0, lon0) — км."""
    def __init__(self, lat0, lon0):
        self.lat0, self.lon0 = lat0, lon0
        self.kx = 111.32 * math.cos(math.radians(lat0))

    def xy(self, lat, lon):
        return (lon - self.lon0) * self.kx, (lat - self.lat0) * KM_LAT

    def ll(self, x, y):
        return self.lat0 + y / KM_LAT, self.lon0 + x / self.kx


def bearing_xy(x1, y1, x2, y2):
    """азимут (рад, 0 = північ, за годинниковою) з точки 1 на точку 2"""
    return math.atan2(x2 - x1, y2 - y1) % TWO_PI


def ang_diff(a, b):
    d = (a - b) % TWO_PI
    return d - TWO_PI if d > math.pi else d


def seg_point_dist(ax, ay, bx, by, px, py):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
    qx, qy = ax + t * dx - px, ay + t * dy - py
    return math.hypot(qx, qy), t


# ---------------- газетир ----------------
STOP = {"город", "города", "городу", "городом", "городе", "місто", "міста", "місту", "містом", "місті", "район", "района",
        "районі", "районе", "область", "області", "области", "центр", "центру", "сторону", "сторони", "стороны", "північ",
        "південь", "захід", "схід", "север", "запад", "восток", "курсом", "напрямку", "направлении", "увага", "внимание",
        "тривога", "тревога", "відбій", "отбой", "шахед", "шахеди", "шахеды", "ракета", "ракети", "ракеты", "дрони", "дроны"}
STOP5 = {"городс", "міськ", "облас", "район", "північ", "півде", "напря", "напра", "сторо", "шахед", "ракет", "мопед"}
def _norm(s):
    s = (s or "").lower().replace("’", "'").replace("ʼ", "'").replace("`", "'").replace("ё", "е")
    return re.sub(r"[^\w' -]+", " ", s)


class Gazetteer:
    def __init__(self, entries=None, region_geo=None):
        if entries is None:
            entries = json.load(open(os.path.join(DATA, "gazetteer.json"), encoding="utf-8"))
        if region_geo is None:
            region_geo = json.load(open(os.path.join(DATA, "region_geo.json"), encoding="utf-8"))
        self.region_geo = region_geo
        self.idx = {}
        for name, lat, lon, ob, ri in entries:
            raion = name.endswith("*р")
            stem = name[:-2] if raion else name
            # основа для відмінків: «глобине» → «глобин», «кременчук» → «кременчу»
            base = stem if len(stem) <= 4 else stem[:max(4, len(stem) - (2 if len(stem) > 6 else 1))]
            self.idx.setdefault(base[:4], []).append((base, stem, raion, lat, lon, ob, ri))

    def find(self, word, next_word=""):
        w = word.strip("'- ")
        if len(w) < 4 or w in STOP or w[:5] in STOP5:
            return None
        best = None
        for base, stem, raion, lat, lon, ob, ri in self.idx.get(w[:4], ()):
            if not w.startswith(base):
                continue
            if raion and not next_word.startswith(("район", "р-н", "р-")):
                continue
            if abs(len(w) - len(stem)) > 4 or (len(base) < 5 and w != stem and not w.startswith(stem)):
                continue
            score = len(base) + (3 if raion else 0)
            if not best or score > best[0]:
                best = (score, dict(name=stem, lat=lat, lon=lon, oblast=ob, region=ri,
                                    sigma=(self.region_geo[ri][2] * 0.7 if raion and ri >= 0 else 50.0 if stem.endswith("щ") else 7.0)))
        return best[1] if best else None


# ---------------- розбір підказок з тексту ----------------
HEAD_RE = r"(курсом на|курс на|в бік|у бік|в сторону|в направлении|у напрямку|в напрямку|напрямок на|летить на|летит на|летять на|летят на|рухається на|движется на|заходить на|заходит на|на)"
FROM_RE = r"(з боку|зі сторони|со стороны|з напрямку|с направления|від|из|з)"
POS_RE = r"(над|в районі|в районе|біля|возле|поблизу|около|в|у|повз|мимо)"
COUNT_RE = re.compile(r"(\d{1,2})\s*(х|x|шт|шахед|мопед|бпла|дрон|ціл|цел|ракет|каб)")

TYPE_WORDS = [
    ("ballistic", ["баліст", "баллист", "балист", "іскандер", "искандер", "кинджал", "кинжал", "кн-23", "kn-23"]),
    ("kab", ["каб ", "каби", "кабы", "каб.", "авіабомб", "авиабомб", "керован", "управляем"]),
    ("cruise", ["крилат", "крылат", "калібр", "калибр", "х-101", "х-555", "х-59", "х-69", "ракет"]),
    ("jet", ["реактивн", "джет", "jet", "герань-3", "герань 3"]),
    ("recon", ["розвід", "развед", "орлан", "zala", "зала", "суперкам", "supercam"]),
    ("shahed", ["шахед", "шахід", "мопед", "герань", "гербер", "бпла", "дрон", "shahed", "моторол", "газонокос", "ланцет"]),
    ("unknown", ["ціль", "цель", "цілі", "цели", "обʼєкт", "об'єкт", "объект"]),
]
KILL_WORDS = ["збит", "сбит", "мінус", "минус", "знищ", "уничтож", "приземл"]


def detect_type(t, raw=None):
    for typ, kws in TYPE_WORDS:
        if any(k in t for k in kws):
            return typ
    # нечіткий збіг (суржик/помилки: «бублистика», «рокета», «шахид») — той самий словник, що й analyzer
    try:
        import analyzer as an
        best, _ = an.threat_of(an.fold(raw if raw is not None else t))
        if best:
            return {"ballistic": "ballistic", "missile": "cruise", "kab": "kab", "drone": "shahed",
                    "generic": "unknown"}.get(best[1]["type"])
    except Exception:  # noqa: BLE001
        pass
    return None


def parse_cues(text, gaz, city=None):
    """→ dict(type, count, pos:[place], heading:[place], origin:[place], kill:bool)"""
    t = " " + _norm(text) + " "
    typ = detect_type(t, text)
    m = COUNT_RE.search(t)
    count = int(m.group(1)) if m and 0 < int(m.group(1)) < 60 else (3 if re.search(r" груп| зграя| стая| кілька| несколько| декілька", t) else 1)
    words = t.split()
    cues = dict(type=typ, count=count, pos=[], heading=[], origin=[], kill=any(k in t for k in KILL_WORDS))
    seen = set()
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        p = gaz.find(w, nxt)
        if not p or p["name"] in seen:
            continue
        seen.add(p["name"])
        before = " ".join(words[max(0, i - 3):i])
        if re.search(r"(^| )" + HEAD_RE + r"$", before):
            cues["heading"].append(p)
        elif re.search(r"(^| )" + FROM_RE + r"$", before):
            cues["origin"].append(p)
        else:
            cues["pos"].append(p)
    # великі міста з російськими/укр. назвами (rules.json → places): «харьков», «днепр», «кривой рог»…
    try:
        import analyzer as an
        ft = an.fold(text)
        have = cues["pos"] + cues["heading"] + cues["origin"]
        for pl, role in an.find_places(ft):
            if any(abs(h["lat"] - pl["lat"]) < 0.15 and abs(h["lon"] - pl["lon"]) < 0.2 for h in have):
                continue
            kwpos = min((ft.find(" " + k) for k in pl["kw"] if ft.find(" " + k) >= 0), default=-1)
            before = " ".join(ft[:kwpos].split()[-3:]) if kwpos >= 0 else ""
            cue = dict(name=pl["n"], lat=pl["lat"], lon=pl["lon"], oblast=-1, region=-1, sigma=12.0)
            if role == "from":
                cues["origin"].append(cue)
            elif re.search(r"(^| )" + HEAD_RE + r"$", before) or before.endswith((" к", " до")):
                cues["heading"].append(cue)
            else:
                cues["pos"].append(cue)
            have.append(cue)
    except Exception:  # noqa: BLE001
        pass
    if city:
        # мікрорайони міста каналу: точка на 3 км від центру в бік сектора
        for d in city.get("districts", []):
            if d.get("sector", -1) < 0:
                continue
            kws = d.get("keywords", [])
            for i, w in enumerate(words):
                if any(w.startswith(k) for k in kws) and d["name"] not in seen:
                    seen.add(d["name"])
                    b = math.radians(d["sector"])
                    lat = city["lat"] + 3 * math.cos(b) / KM_LAT
                    lon = city["lon"] + 3 * math.sin(b) / (111.32 * math.cos(math.radians(city["lat"])))
                    pl = dict(name=d["name"], lat=lat, lon=lon, oblast=city["oblast"], region=-1, sigma=3.0)
                    before = " ".join(words[max(0, i - 3):i])
                    (cues["heading"] if re.search(r"(^| )" + HEAD_RE + r"$", before) else cues["pos"]).append(pl)
                    break
    if city and city.get("keywords"):
        kws = city["keywords"]
        cp = dict(name=city["name"], lat=city["lat"], lon=city["lon"], oblast=city["oblast"], region=-1, sigma=6.0)
        if any(k in t for k in ("на город", "на місто", "в бік міста", "к городу", "до міста", "на нас", "курс на нас")):
            if cp["name"] not in seen:
                cues["heading"].append(cp)
        elif any(k in t for k in ("над городом", "над містом", "над нами", "в городе", "в місті", "по городу", "по місту")):
            if cp["name"] not in seen:
                cues["pos"].append(cp)
    return cues


# ---------------- ціль ----------------
class Track:
    _next = 1

    def __init__(self, typ, lat, lon, sigma, t, rnd, heading=None, kappa=0.0, count=1, src=""):
        self.id = Track._next; Track._next += 1
        self.type = typ
        self.cfg = TYPES[typ]
        self.proj = Proj(lat, lon)
        self.t = t
        self.born = t
        self.last_obs = t
        self.count = count
        self.src = {src} if src else set()
        self.rnd = rnd
        self.nobs = 1
        vm, vs = self.cfg["v"]
        self.p = []
        for _ in range(N):
            x, y = rnd.gauss(0, sigma), rnd.gauss(0, sigma)
            h = rnd.vonmisesvariate(heading, kappa) if heading is not None and kappa > 0 else rnd.uniform(0, TWO_PI)
            v = max(vm * 0.4, rnd.gauss(vm, vs))
            self.p.append([x, y, h % TWO_PI, v, 0.0])
        self.w = [1.0 / N] * N

    # --- рух ---
    def predict_to(self, t):
        dt = t - self.t
        if dt <= 0:
            return
        steps = max(1, int(math.ceil(dt / 60.0)))
        h = dt / steps / 3600.0
        turn = math.radians(self.cfg["turn"]) * math.sqrt(dt / steps / 60.0)
        vm, vs = self.cfg["v"]
        rng = self.cfg["rng"]
        g = self.rnd.gauss
        for _ in range(steps):
            for q in self.p:
                if rng and q[4] >= rng:
                    continue          # КАБ уже впав
                q[2] = (q[2] + g(0, turn)) % TWO_PI
                q[3] = min(vm + 3 * vs, max(vm - 3 * vs, q[3] + g(0, vs * 0.05)))
                d = q[3] * h
                q[0] += d * math.sin(q[2]); q[1] += d * math.cos(q[2]); q[4] += d
        self.t = t

    # --- оновлення вагами ---
    def _reweight(self, lik):
        ws = [w * l for w, l in zip(self.w, lik)]
        s = sum(ws)
        if s <= 1e-300:
            return False
        self.w = [w / s for w in ws]
        if 1.0 / sum(w * w for w in self.w) < N / 2:
            self._resample()
        return True

    def _resample(self):
        r = self.rnd
        u0, cum, i, out = r.random() / N, self.w[0], 0, []
        for k in range(N):
            u = u0 + k / N
            while u > cum and i < N - 1:
                i += 1; cum += self.w[i]
            q = self.p[i]
            out.append([q[0] + r.gauss(0, 1.5), q[1] + r.gauss(0, 1.5), (q[2] + r.gauss(0, 0.05)) % TWO_PI, q[3], q[4]])
        self.p, self.w = out, [1.0 / N] * N

    def lik_pos(self, lat, lon, sigma):
        px, py = self.proj.xy(lat, lon)
        s2 = 2 * sigma * sigma
        return [math.exp(-((q[0] - px) ** 2 + (q[1] - py) ** 2) / s2) for q in self.p]

    def obs_pos(self, lat, lon, sigma):
        return self._reweight([0.02 + l for l in self.lik_pos(lat, lon, sigma)])

    def obs_heading_to(self, lat, lon, kappa=3.0):
        px, py = self.proj.xy(lat, lon)
        return self._reweight([math.exp(kappa * (math.cos(q[2] - bearing_xy(q[0], q[1], px, py)) - 1)) + 0.01 for q in self.p])

    def obs_from(self, lat, lon, kappa=2.0):
        px, py = self.proj.xy(lat, lon)
        return self._reweight([math.exp(kappa * (math.cos(q[2] - bearing_xy(px, py, q[0], q[1])) - 1)) + 0.01 for q in self.p])

    def obs_region(self, lat, lon, radius, sigma=25.0, eps=0.25, inside=True):
        px, py = self.proj.xy(lat, lon)
        lik = []
        for q in self.p:
            d = max(0.0, math.hypot(q[0] - px, q[1] - py) - radius)
            g = math.exp(-d * d / (2 * sigma * sigma))
            lik.append(eps + (1 - eps) * g if inside else 1.0 - 0.5 * (d == 0.0))
        return self._reweight(lik)

    def match(self, lat, lon, sigma):
        """середня правдоподібність положення — для асоціації спостереження з ціллю"""
        return sum(w * l for w, l in zip(self.w, self.lik_pos(lat, lon, sigma + 5)))

    # --- оцінки ---
    def mean(self):
        x = sum(w * q[0] for w, q in zip(self.w, self.p)); y = sum(w * q[1] for w, q in zip(self.w, self.p))
        s = sum(w * math.sin(q[2]) for w, q in zip(self.w, self.p)); c = sum(w * math.cos(q[2]) for w, q in zip(self.w, self.p))
        v = sum(w * q[3] for w, q in zip(self.w, self.p))
        R = math.hypot(s, c)
        sd = math.sqrt(sum(w * ((q[0] - x) ** 2 + (q[1] - y) ** 2) for w, q in zip(self.w, self.p)) / 2)
        return x, y, math.atan2(s, c) % TWO_PI, R, v, sd

    def forecast(self, ulat, ulon, R=15.0, horizons=(5, 15, 30), step_min=1.0):
        """→ dict(p={5:..,15:..,30:..}, eta_min, path=[[lat,lon,σ_km],...], dist_km)"""
        ux, uy = self.proj.xy(ulat, ulon)
        H = max(horizons)
        rng = self.cfg["rng"]
        nst = int(H / step_min)
        snap = {0: [], 5: [], 10: [], 15: [], 20: [], 30: []}
        hit_t = []
        h = step_min / 60.0
        for q, w in zip(self.p, self.w):
            x, y, hd, v, dist = q
            t_in = None
            d0 = math.hypot(x - ux, y - uy)
            if d0 <= R:
                t_in = 0.0
            snap[0].append((x, y, w))
            for k in range(1, nst + 1):
                if rng and dist >= rng:
                    nx, ny = x, y
                else:
                    d = v * h
                    nx, ny = x + d * math.sin(hd), y + d * math.cos(hd)
                    dist += d
                if t_in is None:
                    dd, tt = seg_point_dist(x, y, nx, ny, ux, uy)
                    if dd <= R:
                        t_in = (k - 1 + tt) * step_min
                x, y = nx, ny
                if k * step_min in snap:
                    snap[int(k * step_min)].append((x, y, w))
            hit_t.append((t_in, w))
        p = {T: round(sum(w for ti, w in hit_t if ti is not None and ti <= T), 3) for T in horizons}
        hits = sorted((ti, w) for ti, w in hit_t if ti is not None)
        eta = None
        tot = sum(w for _, w in hits)
        if tot > 0.05:
            acc = 0.0
            for ti, w in hits:
                acc += w
                if acc >= tot / 2:
                    eta = round(ti, 1); break
        path = []
        mx0, my0, hd0, _, _, _ = self.mean()
        for T in sorted(snap):
            pts = snap[T]
            sw = sum(w for _, _, w in pts) or 1
            mx = sum(x * w for x, _, w in pts) / sw; my = sum(y * w for _, y, w in pts) / sw
            # поперечне σ відносно середнього курсу
            nx, ny = math.cos(hd0), -math.sin(hd0)
            sd = math.sqrt(sum(w * ((x - mx) * nx + (y - my) * ny) ** 2 for x, y, w in pts) / sw)
            lat, lon = self.proj.ll(mx, my)
            path.append([round(lat, 4), round(lon, 4), round(sd, 1)])
        return dict(p=p, eta_min=eta, path=path, dist_km=round(math.hypot(mx0 - ux, my0 - uy), 1))


# ---------------- трекер ----------------
class Tracker:
    def __init__(self, gaz=None, seed=None):
        self.gaz = gaz or Gazetteer()
        self.tracks = []
        self.rnd = random.Random(seed)
        self.seen_posts = set()
        self.events = []       # балістика по регіонах без траєкторії: (t, type, region, oblast, place)
        self.home = None       # місто Telegram-каналу (dict з lat/lon) — апріорна зона для постів без місця

    def _alive(self, now):
        self.tracks = [tr for tr in self.tracks if now - tr.last_obs < tr.cfg["ttl"]]
        self.events = [e for e in self.events if now - e[0] < TYPES[e[1]]["ttl"]]

    def _compatible(self, tr, typ):
        if typ is None or typ == "unknown" or tr.type == "unknown":
            return TYPES[tr.type]["cls"] == TYPES[typ or "unknown"]["cls"] or typ in (None, "unknown")
        return tr.type == typ or (tr.type in DRONE_TYPES and typ in DRONE_TYPES and "jet" not in (tr.type, typ))

    def add_post(self, text, ts, city=None, src="tg", default_type=None, post_id=None):
        if post_id is not None:
            if post_id in self.seen_posts:
                return []
            self.seen_posts.add(post_id)
        if city:
            self.home = city
        try:
            import analyzer as an
            ev = an.analyze_post(text, city.get("id") if city else None)
            if ev.ignored:
                return []
            clear = ev.clear
        except Exception:  # noqa: BLE001
            clear = False
        cues = parse_cues(text, self.gaz, city)
        if clear and not cues["kill"]:
            # «відбій / пролетіли / на виліт»: знімаємо дрони в зоні каналу
            hx, hy = (self.home["lat"], self.home["lon"]) if self.home else (None, None)
            kill = [tr for tr in self.tracks if tr.cfg["cls"] == "drone" and hx is not None
                    and math.hypot(*Proj(hx, hy).xy(*tr.proj.ll(*tr.mean()[:2]))) < 90]
            for tr in kill:
                self.tracks.remove(tr)
            return [("kill", tr.id) for tr in kill]
        return self.apply_cues(cues, ts, src, default_type)

    def apply_cues(self, cues, ts, src="tg", default_type=None):
        typ = cues["type"] or default_type
        touched = []
        for tr in self.tracks:
            tr.predict_to(max(tr.t, ts))
        anchors = cues["pos"]
        if cues["kill"] and cues["pos"]:
            for p in cues["pos"]:
                for tr in list(self.tracks):
                    if self._compatible(tr, typ) and tr.match(p["lat"], p["lon"], p["sigma"] + 15) > 0.05:
                        self.tracks.remove(tr); touched.append(("kill", tr.id))
                        break
            return touched
        if typ is None:
            return touched
        if typ == "ballistic":
            reg = (cues["pos"] or cues["heading"] or [None])[0]
            # «балістика хз звідки» — без місця: подія для всієї області каналу
            ob = reg["oblast"] if reg else (self.home or {}).get("oblast", -1)
            self.events.append((ts, typ, reg["region"] if reg else -1, ob, reg))
            return [("event", typ)]
        if not anchors and cues["origin"]:
            # «з боку X» без положення: ціль між X і метою, ближче до X
            o = dict(cues["origin"][0]); o["sigma"] = o["sigma"] + 12
            anchors = [o]
            cues = dict(cues, origin=[])
            if cues["heading"]:
                h = cues["heading"][0]
                pr = Proj(o["lat"], o["lon"]); hx, hy = pr.xy(h["lat"], h["lon"])
                k = min(0.35, 20.0 / max(1.0, math.hypot(hx, hy)))
                o["lat"], o["lon"] = pr.ll(hx * k, hy * k)
        if not anchors:
            # лише курс без положення: оновлюємо найсвіжішу сумісну ціль поблизу району каналу
            if cues["heading"]:
                cand = [tr for tr in self.tracks if self._compatible(tr, typ) and ts - tr.last_obs < 600]
                if cand:
                    tr = max(cand, key=lambda x: x.last_obs)
                    for h in cues["heading"]:
                        tr.obs_heading_to(h["lat"], h["lon"])
                    tr.last_obs = ts; tr.nobs += 1; tr.src.add(src)
                    touched.append(("update", tr.id))
                elif self.home:
                    # нова ціль «десь у зоні каналу» (σ 35 км навколо міста), курс на X
                    h = cues["heading"][0]
                    tr = Track(typ, self.home["lat"], self.home["lon"], 35.0, ts, random.Random(self.rnd.random()),
                               count=cues["count"], src=src)
                    tr.obs_heading_to(h["lat"], h["lon"], kappa=4.0)
                    self.tracks.append(tr)
                    touched.append(("new", tr.id))
            return touched
        p = anchors[0]
        sigma = p["sigma"] + 3.0
        best, bm = None, 0.0
        for tr in self.tracks:
            if not self._compatible(tr, typ):
                continue
            m = tr.match(p["lat"], p["lon"], sigma)
            if m > bm:
                best, bm = tr, m
        if best is not None and bm > 0.08:
            tr = best
            if typ != tr.type and tr.type == "unknown":
                tr.type, tr.cfg = typ, TYPES[typ]
            ok = tr.obs_pos(p["lat"], p["lon"], sigma)
            if not ok:
                best = None
            else:
                tr.last_obs = ts; tr.nobs += 1; tr.src.add(src); tr.count = max(tr.count, cues["count"])
                touched.append(("update", tr.id))
        if best is None or bm <= 0.08:
            head, kappa = None, 0.0
            if cues["origin"]:
                o = cues["origin"][0]
                pr = Proj(p["lat"], p["lon"]); ox, oy = pr.xy(o["lat"], o["lon"])
                head, kappa = bearing_xy(ox, oy, 0, 0), 2.0
            tr = Track(typ, p["lat"], p["lon"], sigma, ts, random.Random(self.rnd.random()), head, kappa, cues["count"], src)
            self.tracks.append(tr)
            touched.append(("new", tr.id))
        for h in cues["heading"]:
            if h["name"] != p["name"]:
                tr.obs_heading_to(h["lat"], h["lon"])
        for o in cues["origin"]:
            if o["name"] != p["name"] and touched[-1][0] != "new":
                tr.obs_from(o["lat"], o["lon"])
        return touched

    def add_region_event(self, region, types, ts, src="jaam"):
        """Разове сповіщення по району (дрон/ракета/КАБ/балістика): слабке спостереження або нова ціль."""
        if region is None or region < 0:
            return
        lat, lon, rad = self.gaz.region_geo[region]
        for typ in types:
            t = {"drone": "shahed", "rocket": "cruise", "kab": "kab", "ballistic": "ballistic", "recon_drone": "recon"}.get(typ)
            if not t:
                continue
            if t in ("ballistic",):
                self.events.append((ts, t, region, -1, dict(name="", lat=lat, lon=lon, region=region, oblast=-1)))
                continue
            cand = [tr for tr in self.tracks if self._compatible(tr, t)]
            for tr in cand:
                tr.predict_to(max(tr.t, ts))
            near = [tr for tr in cand if tr.match(lat, lon, rad + 25) > 0.03]
            if near:
                tr = max(near, key=lambda x: x.match(lat, lon, rad + 25))
                tr.obs_region(lat, lon, rad)
                tr.last_obs = ts; tr.src.add(src)
            else:
                self.tracks.append(Track(t, lat, lon, max(8.0, rad * 0.6), ts, random.Random(self.rnd.random()), src=src))

    def alert_change(self, region, started, ts, drones=False):
        """Зміна статусу тривоги в районі — дуже слабке спостереження для наявних цілей."""
        if region is None or region < 0:
            return
        lat, lon, rad = self.gaz.region_geo[region]
        for tr in self.tracks:
            if not started or tr.type in DRONE_TYPES or not drones:
                tr.predict_to(max(tr.t, ts))
                if tr.match(lat, lon, rad + 60) > 0.02:
                    tr.obs_region(lat, lon, rad, sigma=35.0, eps=0.5, inside=started)

    # --- результат ---
    def assess(self, ulat, ulon, now=None, R=15.0, user_region=None, user_oblast=None, neighbors=()):
        now = now or time.time()
        self._alive(now)
        out = []
        for tr in self.tracks:
            tr.predict_to(max(tr.t, now))
            f = tr.forecast(ulat, ulon, R)
            x, y, hd, Rh, v, sd = tr.mean()
            lat, lon = tr.proj.ll(x, y)
            out.append(dict(id=tr.id, type=tr.type, label=tr.cfg["label"], ch=tr.cfg["ch"], cls=tr.cfg["cls"],
                            count=tr.count, lat=round(lat, 4), lon=round(lon, 4), heading=round(math.degrees(hd)),
                            heading_conf=round(Rh, 2), speed=round(v), sd_km=round(sd, 1),
                            age_s=int(now - tr.last_obs), obs=tr.nobs, src=sorted(tr.src), **f))
        # балістика/КАБ «подією»: без траєкторії — апріорна ймовірність за збігом району/області
        for (t0, typ, reg, ob, place) in self.events:
            if reg is not None and reg >= 0 and reg == user_region:
                p = 0.6
            elif ob is not None and ob >= 0 and ob == user_oblast:
                p = 0.3
            elif place and math.hypot(*Proj(ulat, ulon).xy(place["lat"], place["lon"])) < 150:
                p = 0.25
            else:
                p = 0.05
            age = (now - t0) / 60
            out.append(dict(id=-int(t0) % 100000, type=typ, label=TYPES[typ]["label"], ch=TYPES[typ]["ch"], cls="missile",
                            count=1, lat=place["lat"] if place else None, lon=place["lon"] if place else None,
                            heading=None, heading_conf=0, speed=TYPES[typ]["v"][0], sd_km=None, age_s=int(now - t0),
                            obs=1, src=["event"], p={5: p, 15: p, 30: p}, eta_min=max(0.0, round(3 - age, 1)), path=[], dist_km=None))
        out.sort(key=lambda d: -d["p"][30])
        return out


def threat_summary(targets):
    """Загальна ймовірність, що хоча б одна ціль пройде в радіусі R: 1 − Π(1 − p_i).
    Група з n цілей летить корельовано, тому замість 1−(1−p)^n беремо 1−(1−p)^√n."""
    def comb(cls, T):
        q = 1.0
        for d in targets:
            if cls is None or d["cls"] == cls:
                p = min(0.99, d["p"][T])
                q *= (1 - p) ** math.sqrt(max(1, min(d["count"], 9)))
        return round(1 - q, 3)
    s = {"p_any": {T: comb(None, T) for T in (5, 15, 30)},
         "p_drone": {T: comb("drone", T) for T in (5, 15, 30)},
         "p_missile": {T: comb("missile", T) for T in (5, 15, 30)}}
    etas = [d["eta_min"] for d in targets if d["eta_min"] is not None and d["p"][30] >= 0.1]
    s["eta_min"] = min(etas) if etas else None
    lvl = 0
    if s["p_drone"][30] >= 0.15:
        lvl = 1
    if s["p_missile"][30] >= 0.10:     # ракети — вищі наслідки, нижчий поріг
        lvl = 2
    s["level"] = lvl
    return s


# ---------------- збереження стану між запусками (GitHub Actions: build_static.py) ----------------
def _r(v, nd=4):
    return round(v, nd) if isinstance(v, float) else v


def tracker_dump(trk, extra=None, max_seen=600):
    """Стан трекера → JSON-сумісний dict (частинки округлені, rnd не зберігається)."""
    tracks = []
    for tr in trk.tracks:
        tracks.append({"id": tr.id, "type": tr.type, "lat0": tr.proj.lat0, "lon0": tr.proj.lon0, "t": tr.t,
                       "born": tr.born, "last_obs": tr.last_obs, "count": tr.count, "src": sorted(tr.src),
                       "nobs": tr.nobs, "p": [[_r(x, 3) for x in pp] for pp in tr.p], "w": [_r(x, 8) for x in tr.w]})
    seen = sorted(trk.seen_posts)[-max_seen:]
    return {"v": 1, "next_id": Track._next, "tracks": tracks, "seen_posts": seen,
            "events": [list(e) for e in trk.events], "home": trk.home, "extra": extra or {}}


def tracker_load(trk, d):
    """Відновити стан, збережений tracker_dump(). Повертає d["extra"]."""
    if not d or d.get("v") != 1:
        return {}
    Track._next = max(Track._next, int(d.get("next_id", 1)))
    trk.tracks = []
    for s in d.get("tracks", []):
        if s["type"] not in TYPES or len(s.get("p", [])) != len(s.get("w", [])) or not s["p"]:
            continue
        tr = Track.__new__(Track)
        tr.id, tr.type, tr.cfg = s["id"], s["type"], TYPES[s["type"]]
        tr.proj = Proj(s["lat0"], s["lon0"])
        tr.t, tr.born, tr.last_obs = s["t"], s["born"], s["last_obs"]
        tr.count, tr.src, tr.nobs, tr.rnd = s["count"], set(s["src"]), s["nobs"], trk.rnd
        tr.p, tr.w = [list(map(float, pp)) for pp in s["p"]], [float(x) for x in s["w"]]
        sw = sum(tr.w) or 1.0
        tr.w = [x / sw for x in tr.w]
        trk.tracks.append(tr)
    trk.seen_posts = set(d.get("seen_posts", []))
    trk.events = [tuple(e) for e in d.get("events", []) if len(e) == 5 and e[1] in TYPES]
    trk.home = d.get("home") or trk.home
    return d.get("extra") or {}
