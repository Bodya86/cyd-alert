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

# тип: швидкість (μ, σ) км/год, σ курсу °/√хв, час життя без спостережень, клас, дальність (км), підпис,
# ch — символ кольору на мапі (пристрій ≥1.3.12 / TG; без підписів, лише колір; дрони — фіолетова гама),
# lc — старий символ для прошивок ≤1.3.11 (S/J/D/U/C/K/B). cls: drone / missile / recon (розвідник — відстежується
# і показується, але НЕ входить у p_drone/p_any і сам не дає помаранчевий/червоний).
# Швидкості: відкриті дані + бектест (медіана з логів: шахед ~210, реактивний ~300, Бандероль ~380 км/год — оцінки
# з постів занижені через затримки/непрямий шлях).
TYPES = {
    "shahed":    dict(v=(170, 15),  turn=3, ttl=35 * 60, cls="drone",   rng=None, label="Шахед/Герань-2",   ch="S", lc="S"),
    # «Гербера»/«Пародія» — дешеві дрони-імітатори (пінопласт/фанера), ~150–180 км/год, летять разом із шахедами
    "gerbera":   dict(v=(160, 20),  turn=3, ttl=30 * 60, cls="drone",   rng=None, label="Гербера (імітатор)", ch="G", lc="S"),
    # Герань-3 / реактивний Shahed-238: 350–550 км/год (бектест: медіана ~300, калібровано 350±80 у model_params)
    "jet":       dict(v=(450, 60),  turn=2,  ttl=15 * 60, cls="drone",   rng=None, label="Реактивний шахед", ch="J", lc="J"),
    # Ланцет / FPV / «Молнія» — ударні дрони прифронтової зони (до ~70 км), 80–130 км/год, пікірування до 300
    "lancet":    dict(v=(110, 30),  turn=6, ttl=15 * 60, cls="drone",   rng=70,   label="Ланцет/FPV",       ch="L", lc="S"),
    # Орлан-10/30, Zala, Supercam, Мерлін-ВР — розвідники, 70–150 км/год, кружляють (часто перед ударом)
    "recon":     dict(v=(100, 30),  turn=6, ttl=40 * 60, cls="recon",   rng=None, label="Розвідник",        ch="D", lc="D"),
    "unknown":   dict(v=(200, 80),  turn=4, ttl=25 * 60, cls="drone",   rng=None, label="Ціль",             ch="U", lc="U"),
    # Х-101/555, Калібр, Іскандер-К, Х-59/69 — дозвукові крилаті, 700–1000 км/год
    "cruise":    dict(v=(800, 80),  turn=1.5, ttl=12 * 60, cls="missile", rng=None, label="Крилата ракета",   ch="C", lc="C"),
    # мКР/баражуючий боєприпас «Бандероль» (S8000, ГУР): крейсерська 520–560 км/год, макс. ~650; маневрує крутіше за КР
    "banderol":  dict(v=(530, 60),  turn=3, ttl=15 * 60, cls="missile", rng=None, label="Бандероль (мКР)",  ch="N", lc="C"),
    # Х-22/Х-32 (Ту-22М3), П-800 «Онікс» — надзвукові, ~2500–4000 км/год, хвилини до удару
    "kh22":      dict(v=(3500, 700), turn=1, ttl=8 * 60,  cls="missile", rng=None, label="Х-22/Онікс",       ch="X", lc="C"),
    # КАБ/УМПК/УПАБ — планують 40–90 км від носія, 400–700 км/год
    "kab":       dict(v=(450, 100), turn=1,  ttl=6 * 60,  cls="missile", rng=70,   label="КАБ",              ch="K", lc="K"),
    # Іскандер-М, KN-23, С-300/С-400 по землі — балістика (подія без траєкторії)
    "ballistic": dict(v=(3000, 500), turn=1, ttl=10 * 60,  cls="missile", rng=None, label="Балістика",        ch="B", lc="B"),
    # Х-47М2 «Кинджал» (МіГ-31К), 3М22 «Циркон» — аеробалістика/гіперзвук (подія без траєкторії)
    "kinzhal":   dict(v=(4500, 1000), turn=1, ttl=8 * 60, cls="missile", rng=None, label="Кинджал/Циркон",   ch="Z", lc="B"),
}
EVENT_TYPES = ("ballistic", "kinzhal")    # без траєкторії — апріорна ймовірність за районом/областю
DRONE_TYPES = {k for k, v in TYPES.items() if v["cls"] == "drone"}


def load_motion(path=None):
    """data/model_params.json → "motion": {тип: {v: [м, σ], turn, ttl}} — калібрування tools/backtest_tracker.py (лише якщо holdout кращий)."""
    try:
        mp = json.load(open(path or os.path.join(DATA, "model_params.json"), encoding="utf-8")).get("motion") or {}
    except (OSError, ValueError):
        return {}
    for typ, kv in mp.items():
        for k, v in (kv.items() if typ in TYPES and isinstance(kv, dict) else ()):
            if k in ("v", "turn", "ttl"):
                TYPES[typ][k] = tuple(v) if isinstance(v, list) else v
    return mp


load_motion()


def load_city_cal(path=None):
    """data/model_params.json → "city_cal": {"drone"|"any"|"missile": {"5"|"15"|"30": [a, b]}} — Platt-калібрування
    зведення по місту (tools/fit_city_cal.py): p' = σ(a·logit(p) + b). Немає — сирі ймовірності."""
    try:
        cc = json.load(open(path or os.path.join(DATA, "model_params.json"), encoding="utf-8")).get("city_cal") or {}
    except (OSError, ValueError):
        return {}
    return {c: {int(T): tuple(ab) for T, ab in v.items()} for c, v in cc.get("params", {}).items()} if cc.get("enabled", True) else {}


CITY_CAL = load_city_cal()
SPLIT_DIGESTS = True     # дайджести з кількома цілями → окремі фрагменти (split_post)
HEAD_ASSOC_KM = 150.0    # «курсом на X» без положення оновлює лише ціль, від якої X ≤ 150 км (0 — стара поведінка)
HEAD_NEW_SIGMA = 30.0    # нова ціль біля X для «Область: … курсом на X» (0 — стара поведінка: зона каналу)
TRAIL_MAX = 8            # точок пройденого шляху цілі
TRAIL_SIGMA = 30.0       # початкове положення з σ ≤ 30 км — перша точка шляху (зона каналу 35 км — ні)
FC_TURN = 0.0            # дифузія курсу в прогнозі (× cfg turn, °/√хв): цілі виляють; 0 — прямі. model_params "fc_turn"
try:
    with open(os.path.join(DATA, "model_params.json"), encoding="utf-8") as _f:
        FC_TURN = float(json.load(_f).get("fc_turn", FC_TURN))
except (OSError, ValueError, TypeError):
    pass


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

    def find(self, word, next_word="", oblast=None):
        """oblast — контекст («Полтавщина: …»): однойменні села в цій області мають перевагу."""
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
            score = len(base) + (3 if raion else 0) + (6 if oblast is not None and ob == oblast else 0)
            if not best or score > best[0]:
                best = (score, dict(name=stem, lat=lat, lon=lon, oblast=ob, region=ri,
                                    sigma=(self.region_geo[ri][2] * 0.7 if raion and ri >= 0 else 50.0 if stem.endswith("щ") else 7.0)))
        return best[1] if best else None


# ---------------- розбір підказок з тексту ----------------
FAR_PREFIX_KM = 120   # обласний/міський канал: неточний збіг назви в іншій області далі за це — не та місцевість
HEAD_RE = r"(курсом на|курс на|в бік|у бік|в сторону|в направлении|у напрямку|в напрямку|напрямок на|летить на|летит на|летять на|летят на|рухається на|движется на|заходить на|заходит на|на)"
FROM_RE = r"(з боку|зі сторони|со стороны|з напрямку|с направления|від|из|з)"
POS_RE = r"(над|в районі|в районе|біля|возле|поблизу|около|в|у|повз|мимо)"
COUNT_RE = re.compile(r"(\d{1,2})\s*(х|x|шт|шахед|мопед|бпла|дрон|ціл|цел|ракет|каб|бандер[оo]л|гербер|ланцет|розвідник|реактивн)")

TYPE_WORDS = [
    # порядок = пріоритет: конкретні слова раніше за загальні («розвідувальний БпЛА» → розвідник, а не шахед;
    # «шахеди і розвідник» → шахед). Підрядки в нижньому регістрі (не фолдинг).
    ("kinzhal", ["кинджал", "кинжал", "х-47", "циркон", "гіперзвук", "гиперзвук"]),
    ("ballistic", ["баліст", "баллист", "балист", "іскандер", "искандер", "кн-23", "kn-23", "с-300", "с-400", "c-300", "c-400"]),
    ("kab", ["каб ", "каби", "кабы", "каб.", "авіабомб", "авиабомб", "керован", "управляем", "умпк", "упаб"]),
    ("kh22", ["х-22", "х22", "х-32", "онікс", "оникс"]),
    ("cruise", ["крилат", "крылат", "калібр", "калибр", "х-101", "х-555", "х-55", "х-59", "х-69", "ракет"]),
    ("jet", ["реактивн", "герань-3", "герань 3", "геран-3"]),
    ("shahed", ["шахед", "шахід", "мопед", "shahed", "ударн", "герань", "моторол", "газонокос"]),
    ("gerbera", ["гербер", "імітатор", "имитатор", "пародія", "пародия"]),
    ("lancet", ["ланцет", "lancet", "фпв", "fpv"]),
    ("recon", ["розвідувальн", "розвідник", "разведчик", "разведыв", "орлан", "zala", "суперкам", "supercam", "мерлін", "merlin"]),
    ("shahed", ["бпла", "дрон", "безпілот", "беспилот"]),
    ("unknown", ["ціль", "цель", "цілі", "цели", "обʼєкт", "об'єкт", "объект"]),
]
JET_RE = re.compile(r"(?<![а-яіїєґa-z])(джет|jet)")   # не «бюджет», «гаджет»
ZALA_RE = re.compile(r"(?<![а-яіїєґ])зала(?![а-яіїєґ])")   # «Зала» (розвідник), не «вокзала»/«зала» у слові
KILL_WORDS = ["збит", "сбит", "мінус", "минус", "знищ", "уничтож", "приземл"]


# «Бандероль»/«бандеролька»/«бандероди» (опечатка), «мКР», S8000 — НЕ «Бандери» (вулиця): лише «бандерол»/«бандерод»
BANDEROL_HINT = re.compile(r"бандер[оo]л|бандерод|[sс][\s-]?8000")
MKR_RE = re.compile(r"(?<![\w-])(мКР|МКР)(?![\w.-])")   # «мКР» (регістр!) — не «мкр.» (мікрорайон)
CRUISE_HINT = re.compile(r"крилат|крылат|[иі]скандер[\s-]*к\b")
BALLISTIC_HINT = re.compile(r"баліст|баллист|балист|кинджал|кинжал|кн-23|kn-23|[иі]скандер[\s-]*м\b|циркон|[сc]-[34]00")
KH22_HINT = re.compile(r"х-?22\b|х-?32\b|он[іи]кс")


def detect_type(t, raw=None):
    if (BANDEROL_HINT.search(t) or (raw and MKR_RE.search(raw))) and not BALLISTIC_HINT.search(t):
        return "banderol"        # повільніша за КР (~530 км/год) — інакше прогноз прольоту на ~20 хв раніше (30.10 10:24 vs 10:48)
    if KH22_HINT.search(t) and not BALLISTIC_HINT.search(t):
        return "kh22"            # надзвукова: ~3500 км/год — інакше «крилата» відстає на хвилини
    if CRUISE_HINT.search(t) and not BALLISTIC_HINT.search(t):
        return "cruise"          # «Іскандер-К», «крилаті ракети» — крилата ракета з траєкторією, не балістика
    for typ, kws in TYPE_WORDS:
        if typ == "shahed" and JET_RE.search(t):
            return "jet"
        if typ == "recon" and ZALA_RE.search(t):
            return "recon"
        if any(k in t for k in kws):
            return typ
    # нечіткий збіг (суржик/помилки: «бублистика», «рокета», «шахид») — той самий словник, що й analyzer
    try:
        import analyzer as an
        best, _ = an.threat_of(an.fold(raw if raw is not None else t))
        if best:
            sub = best[1].get("sub")
            if sub in TYPES:
                return sub
            if sub == "s300":
                return "ballistic"
            return {"ballistic": "ballistic", "missile": "cruise", "kab": "kab", "drone": "shahed", "recon": "recon",
                    "generic": "unknown"}.get(best[1]["type"])
    except Exception:  # noqa: BLE001
        pass
    return None


# підпис каналу («📣 ППО Полтавщина», «ППО Полтавщина» в кінці) — не місце цілі
SIG_RE = re.compile(r"(📣[^\n]*$|\bППО\s+\w+щина\s*$)", re.I | re.M)
# оголошення тривоги по району/громаді («Кременчуцький район, тривога: загроза БпЛА») — це не положення цілі
ALERT_ONLY_RE = re.compile(r"(тривог|відбій|загроза застосування|загроза бпла|загроза балістики|угроза)", re.I)
MOVE_RE = re.compile(r"(курс|напрям|бік|сторон|рух|лет|пролет|над |в районі|повз|біля|поблизу|вибух|збит|на |→)", re.I)


def alert_only(text):
    """Пост лише оголошує тривогу/загрозу для території, без руху цілі."""
    s = SIG_RE.sub("", text or "")
    if not ALERT_ONLY_RE.search(s):
        return False
    rest = ALERT_ONLY_RE.sub(" ", s)
    rest = re.sub(r"загроз\w*|бпла|шахед\w*|ударн\w*|безпілот\w*|балістик\w*|ракет\w*|застосуван\w*", " ", rest, flags=re.I)
    return not MOVE_RE.search(rest)


def parse_cues(text, gaz, city=None, oblast=None):
    """→ dict(type, count, pos:[place], heading:[place], origin:[place], kill:bool).
    oblast — контекст області (заголовок «Полтавщина: …»): однойменні села в ній мають перевагу."""
    text = SIG_RE.sub("", text or "")
    if alert_only(text):
        return dict(type=detect_type(" " + _norm(text) + " ", text), count=1, pos=[], heading=[], origin=[], kill=False)
    t = " " + _norm(text) + " "
    typ = detect_type(t, text)
    m = COUNT_RE.search(t)
    count = int(m.group(1)) if m and 0 < int(m.group(1)) < 60 else (3 if re.search(r" груп| зграя| стая| кілька| несколько| декілька", t) else 1)
    words = t.split()
    cues = dict(type=typ, count=count, pos=[], heading=[], origin=[], kill=any(k in t for k in KILL_WORDS))
    seen = set()
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if len(w) > 6 and any(w.startswith(o) for o, _ in OBL_WORDS):
            continue                     # «Полтавщина» — область, не місто Полтава
        p = gaz.find(w, nxt, oblast)
        if not p or p["name"] in seen:
            continue
        if city and p["name"] != w.strip("'- ") and p.get("oblast", -1) not in (-1, city.get("oblast")) and \
                math.hypot((p["lon"] - city["lon"]) * 72.5, (p["lat"] - city["lat"]) * KM_LAT) > FAR_PREFIX_KM:
            continue                     # неточний збіг основи далеко в чужій області: «Рокита» (Полтавщина) ≠ «Рокитне» (Київщина)
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
        ft = re.sub(r"\w+щин\w*", " ", an.fold(text))      # назви областей ≠ обласні центри
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


# ---------------- дайджести: «Чернігівщина: БпЛА курсом на A, 3х Реактивні БпЛА курсом на B. Сумщина: …» ----------------
OBL_WORDS = [("крим", 0), ("волин", 1), ("вінниччин", 2), ("винничин", 2), ("дніпропетровщин", 3), ("днепропетровщин", 3),
             ("дніпровщин", 3), ("донеччин", 4), ("донетчин", 4), ("житомирщин", 5), ("закарпатт", 6), ("закарпат", 6),
             ("запоріжж", 7), ("запорожь", 7), ("запорізьк", 7), ("прикарпатт", 8), ("франківщин", 8), ("київщин", 10), ("киевщин", 10),
             ("кіровоградщин", 11), ("кировоградщин", 11), ("луганщин", 12), ("львівщин", 13), ("львовщин", 13),
             ("миколаївщин", 14), ("николаевщин", 14), ("одещин", 15), ("одесчин", 15), ("полтавщин", 16), ("рівненщин", 17),
             ("ровенщин", 17), ("сумщин", 19), ("тернопільщин", 20), ("тернопольщин", 20), ("харківщин", 21), ("харьковщин", 21),
             ("херсонщин", 22), ("хмельниччин", 23), ("хмельнитчин", 23), ("черкащин", 24), ("буковин", 25),
             ("чернігівщин", 26), ("черниговщин", 26)]
OBL_HDR_RE = re.compile(r"(?=(?:[А-ЯІЇЄҐ][а-яіїєґ'’]+(?:щина|щини|ччина|ччини|ття|жжя|ччя))\s*[:—–-])")
CLAUSE_RE = re.compile(r"(?=(?<![\w-])(?:\d{1,2}\s*[хx]\s*)?(?:(?:реактивн|швидкісн|ударн)\w*\s+)?(?:бпла|шахед|дрон|ракет|каб\b|мопед|бандер[оo]л|гербер|ланцет|розвідник|розвідувальн)\w*)", re.I)
HEAD_CNT_RE = re.compile(r"(курсом|курс на|в напрямку|у напрямку|в бік|у бік|в сторону|в направлении)", re.I)


def split_post(text):
    """Пост → [(текст, область | None)]: заголовки областей стають контекстом (не положенням), а сегмент
    з кількома «курсом на» ділиться на окремі цілі перед словом типу/кількості («3х Реактивні БпЛА …»).
    Звичайний пост про одну ціль лишається одним фрагментом."""
    out = []
    for seg in OBL_HDR_RE.split(text):
        seg = seg.strip()
        if not seg:
            continue
        ob = None
        m = re.match(r"([А-ЯІЇЄҐ][а-яіїєґ'’]+)\s*[:—–-]\s*", seg)
        if m:
            low = m.group(1).lower()
            ob = next((o for w, o in OBL_WORDS if low.startswith(w)), None)
            if ob is not None:
                seg = seg[m.end():]
        parts = [seg]
        if len(HEAD_CNT_RE.findall(seg)) >= 2:
            parts, cur = [], ""
            for piece in CLAUSE_RE.split(seg):
                if cur and HEAD_CNT_RE.search(cur) and piece.strip():
                    parts.append(cur)
                    cur = piece
                else:
                    cur += piece
            if cur.strip():
                parts.append(cur)
        out += [(p.strip(" ,.;"), ob) for p in parts if p.strip(" ,.;")]
    return out or [(text, None)]


# ---------------- ціль ----------------
_CH_SIGMA = None


def ch_sigma(src):
    """Множник σ положення за надійністю каналу (data/channel_reliability.json через analyzer); 1.0 — невідомий."""
    global _CH_SIGMA
    if _CH_SIGMA is None:
        try:
            import analyzer as an
            _CH_SIGMA = dict(an.CH_SIGMA)
        except Exception:  # noqa: BLE001
            _CH_SIGMA = {}
    ch = src[3:] if isinstance(src, str) and src.startswith("tg:") else src
    return _CH_SIGMA.get(str(ch).lower(), 1.0) if ch else 1.0


class Track:
    _next = 1

    def __init__(self, typ, lat, lon, sigma, t, rnd, heading=None, kappa=0.0, count=1, src="", prior=None):
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
        # пройдений шлях (пунктир на мапі): оцінки положення після спостережень, ≤ TRAIL_MAX точок
        self.trail = [[int(t), round(lat, 4), round(lon, 4)]] if sigma <= TRAIL_SIGMA else []
        vm, vs = self.cfg["v"]
        self.p = []
        # курс невідомий: prior_mix (типово 60%, data/model_params.json) частинок — з історичного поля маршрутів (якщо модель є й комірка має дані), решта рівномірно
        pf = prior.dir_prob(lat, lon) if prior is not None and typ in DRONE_TYPES and not (heading is not None and kappa > 0) else None
        self.prior_used = pf is not None
        n_prior = int(round(20 * getattr(prior, "prior_mix", 0.6))) if pf else 0
        for k in range(N):
            x, y = rnd.gauss(0, sigma), rnd.gauss(0, sigma)
            if heading is not None and kappa > 0:
                h = rnd.vonmisesvariate(heading, kappa)
            elif pf and k % 20 < n_prior:
                h = prior.sample_heading(lat, lon, rnd, pf)
            else:
                h = rnd.uniform(0, TWO_PI)
            v = max(vm * 0.4, rnd.gauss(vm, vs))
            self.p.append([x, y, h % TWO_PI, v, 0.0])
        self.w = [1.0 / N] * N

    def retype(self, typ):
        """Зміна типу з перемасштабуванням швидкостей частинок під новий тип (КР 800 → «Бандероль» 530 км/год)."""
        old = self.cfg["v"][0]
        self.type, self.cfg = typ, TYPES[typ]
        vm, vs = self.cfg["v"]
        for q in self.p:
            q[3] = min(vm + 3 * vs, max(vm - 3 * vs, q[3] * vm / old))

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

    def add_trail(self, ts):
        """Додати поточну оцінку положення (середнє частинок) до пройденого шляху; поруч (< 3 км) — оновити останню."""
        x, y, *_ = self.mean()
        lat, lon = self.proj.ll(x, y)
        tl = self.trail = getattr(self, "trail", None) or []
        pt = [int(ts), round(lat, 4), round(lon, 4)]
        if tl and math.hypot(*Proj(tl[-1][1], tl[-1][2]).xy(lat, lon)) < 3.0:
            tl[-1] = pt
        else:
            tl.append(pt)
        del tl[:-TRAIL_MAX]

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
        ft = math.radians(self.cfg["turn"]) * FC_TURN * math.sqrt(step_min)   # виляння цілей: курс дифундує і в прогнозі
        frnd = random.Random(self.id * 1000003 + int(self.t))   # окремий ГВЧ: прогноз не зсуває потік фільтра
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
                    if ft > 0:
                        hd += frnd.gauss(0, ft)
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


    def p_zones(self, zones, H=30.0, step_min=3.0, sub=3):
        """zones: [[(lat, lon, R_км), ...], ...] — кожна зона як об'єднання кіл (область = кола її районів).
        → [P(ціль увійде в зону за H хв)] — та сама модель руху, що forecast(), але грубіше (кожна 3-я частинка,
        крок 3 хв; траєкторії кешуються на ітерацію). Далекі кола відкидаються одразу."""
        key = (self.t, H, step_min, sub)
        if getattr(self, "_traj_key", None) != key:
            rng = self.cfg["rng"]
            nst, h = int(H / step_min), step_min / 60.0
            tr = []
            for q, w in list(zip(self.p, self.w))[::sub]:
                x, y, hd, v, dist = q
                pts = [(x, y)]
                for _ in range(nst):
                    if not (rng and dist >= rng):
                        d = v * h
                        x, y = x + d * math.sin(hd), y + d * math.cos(hd)
                        dist += d
                    pts.append((x, y))
                tr.append((w * sub, pts))
            self._traj, self._traj_key = tr, key
            mx, my, *_ = self.mean()
            self._reach = (mx, my, max(math.hypot(p[-1][0] - p[0][0], p[-1][1] - p[0][1]) + math.hypot(p[0][0] - mx, p[0][1] - my)
                                      for _, p in tr) if tr else 0.0)
        mx, my, reach = self._reach
        out = []
        for z in zones:
            circ = [(cx, cy, R) for cx, cy, R in ((*self.proj.xy(la, lo), R) for la, lo, R in z)
                    if math.hypot(cx - mx, cy - my) <= reach + R]
            if not circ:
                out.append(0.0)
                continue
            acc = 0.0
            for w, pts in self._traj:
                hit = False
                for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                    for cx, cy, R in circ:
                        if seg_point_dist(x0, y0, x1, y1, cx, cy)[0] <= R:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    acc += w
            out.append(round(min(1.0, acc), 3))
        return out


# ---------------- трекер ----------------
class Tracker:
    def __init__(self, gaz=None, seed=None):
        self.gaz = gaz or Gazetteer()
        self.tracks = []
        self.rnd = random.Random(seed)
        self.seed = seed
        self.seen_posts = set()
        self.events = []       # балістика по регіонах без траєкторії: (t, type, region, oblast, place)
        self.home = None       # місто Telegram-каналу (dict з lat/lon) — апріорна зона для постів без місця
        self.history = None    # history.History — історичне апріорі (поле курсів, P(ціль)); None — без нього

    def _trng(self, ts, lat, lon, typ):
        """ГВЧ нової цілі. Із seed (бектест) — від (час, місце, тип), а не зі спільного потоку: нова/зайва ціль
        не змінює випадковості всіх наступних (інакше метрики «плавали» від будь-якої зміни класифікації)."""
        if self.seed is None:
            return random.Random(self.rnd.random())
        return random.Random(f"{self.seed}|{int(ts)}|{lat:.3f}|{lon:.3f}|{typ}")

    def _alive(self, now):
        self.tracks = [tr for tr in self.tracks if now - tr.last_obs < tr.cfg["ttl"]]
        self.events = [e for e in self.events if now - e[0] < TYPES[e[1]]["ttl"]]

    def _compatible(self, tr, typ):
        if typ is None or typ == "unknown" or tr.type == "unknown":
            return TYPES[tr.type]["cls"] == TYPES[typ or "unknown"]["cls"] or typ in (None, "unknown")
        return tr.type == typ or (tr.type in DRONE_TYPES and typ in DRONE_TYPES and "jet" not in (tr.type, typ)) or \
            {tr.type, typ} == {"cruise", "banderol"}     # «ракета» про вже відому «Бандероль» — та сама ціль

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
        parts = split_post(text) if SPLIT_DIGESTS else [(text, None)]
        if len(parts) <= 1 and (not parts or parts[0][1] is None):
            return self.apply_cues(cues, ts, src, default_type)
        out, prev_t = [], None
        for frag, ob in parts:
            c = parse_cues(frag, self.gaz, city, ob)
            if c["type"] is None:
                c["type"] = prev_t or cues["type"]
            prev_t = c["type"] or prev_t
            c["oblast"] = ob
            c["skip"] = {i for op, i in out if op in ("new", "update")}   # різні фрагменти одного посту — різні цілі
            out += self.apply_cues(c, ts, src, default_type)
        return out

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
        if typ in EVENT_TYPES:
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
                h = cues["heading"][0]
                if HEAD_ASSOC_KM:
                    # ціль «курсом на X» — лише серед сумісних цілей, від яких X досяжний (≤ HEAD_ASSOC_KM), і
                    # чий курс узгоджується з напрямком на X; раніше бралась найсвіжіша ціль будь-де в країні
                    cand = []
                    for tr in self.tracks:
                        if not self._compatible(tr, typ) or ts - tr.last_obs >= 900 or tr.id in cues.get("skip", ()):
                            continue
                        x, y, hd, Rh, _, _ = tr.mean()
                        hx, hy = tr.proj.xy(h["lat"], h["lon"])
                        dk = math.hypot(hx - x, hy - y)
                        if dk > HEAD_ASSOC_KM:
                            continue
                        agree = math.cos(ang_diff(bearing_xy(x, y, hx, hy), hd)) * Rh if dk > 10 else 1.0
                        cand.append((agree - dk / HEAD_ASSOC_KM, tr))
                    cand = [c for c in cand if c[0] > -0.5]
                else:
                    cand = [(tr.last_obs, tr) for tr in self.tracks if self._compatible(tr, typ) and ts - tr.last_obs < 600]
                if cand:
                    tr = max(cand, key=lambda c: c[0])[1]
                    for hh in cues["heading"]:
                        tr.obs_heading_to(hh["lat"], hh["lon"])
                    tr.last_obs = ts; tr.nobs += 1; tr.src.add(src)
                    touched.append(("update", tr.id))
                elif HEAD_ASSOC_KM and HEAD_NEW_SIGMA < 0:
                    pass         # курс без положення і без відповідної цілі — ігноруємо (нема де поставити ціль)
                elif HEAD_ASSOC_KM and HEAD_NEW_SIGMA and (cues.get("oblast") is not None or not self.home):
                    # дайджест «Область: БпЛА курсом на X» без положення: ціль на підльоті до X (σ HEAD_NEW_SIGMA км)
                    tr = Track(typ, h["lat"], h["lon"], HEAD_NEW_SIGMA, ts, self._trng(ts, h["lat"], h["lon"], typ),
                               count=cues["count"], src=src)
                    tr.obs_heading_to(h["lat"], h["lon"], kappa=4.0)
                    self.tracks.append(tr)
                    touched.append(("new", tr.id))
                elif self.home:
                    # нова ціль «десь у зоні каналу» (σ 35 км навколо міста), курс на X
                    tr = Track(typ, self.home["lat"], self.home["lon"], 35.0, ts, self._trng(ts, self.home["lat"], self.home["lon"], typ),
                               count=cues["count"], src=src)
                    tr.obs_heading_to(h["lat"], h["lon"], kappa=4.0)
                    self.tracks.append(tr)
                    touched.append(("new", tr.id))
            return touched
        p = anchors[0]
        sigma = (p["sigma"] + 3.0) * ch_sigma(src)
        best, bm = None, 0.0
        for tr in self.tracks:
            if not self._compatible(tr, typ) or tr.id in cues.get("skip", ()):
                continue
            m = tr.match(p["lat"], p["lon"], sigma)
            if m > bm:
                best, bm = tr, m
        if best is not None and bm > 0.08:
            tr = best
            if typ != tr.type and tr.type == "unknown":
                tr.type, tr.cfg = typ, TYPES[typ]
            elif typ == "banderol" and tr.type == "cruise":
                tr.retype("banderol")      # уточнення: «ракета» → «Бандероль» (повільніша)
            ok = tr.obs_pos(p["lat"], p["lon"], sigma)
            if not ok:
                best = None
            else:
                tr.last_obs = ts; tr.nobs += 1; tr.src.add(src); tr.count = max(tr.count, cues["count"])
                tr.last_pos = [int(ts), round(p["lat"], 4), round(p["lon"], 4), round(sigma, 1)]
                tr.add_trail(ts)
                touched.append(("update", tr.id))
        if best is None or bm <= 0.08:
            head, kappa = None, 0.0
            if cues["origin"]:
                o = cues["origin"][0]
                pr = Proj(p["lat"], p["lon"]); ox, oy = pr.xy(o["lat"], o["lon"])
                head, kappa = bearing_xy(ox, oy, 0, 0), 2.0
            tr = Track(typ, p["lat"], p["lon"], sigma, ts, self._trng(ts, p["lat"], p["lon"], typ), head, kappa, cues["count"], src,
                       prior=None if cues["heading"] else self.history)
            tr.last_pos = [int(ts), round(p["lat"], 4), round(p["lon"], 4), round(sigma, 1)]
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
            if t in EVENT_TYPES:
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
                self.tracks.append(Track(t, lat, lon, max(8.0, rad * 0.6), ts, self._trng(ts, lat, lon, t), src=src))

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
            lt = None
            if self.history is not None and tr.type in DRONE_TYPES:
                name, pt = self.history.likely_target(lat, lon, math.degrees(hd) if Rh >= 0.3 else None, ulat, ulon)
                if name:
                    lt = dict(name=name, p=round(pt, 3))
            out.append(dict(id=tr.id, type=tr.type, label=tr.cfg["label"], ch=tr.cfg["ch"], lc=tr.cfg["lc"], cls=tr.cfg["cls"], likely=lt,
                            count=tr.count, lat=round(lat, 4), lon=round(lon, 4), heading=round(math.degrees(hd)),
                            heading_conf=round(Rh, 2), speed=round(v), sd_km=round(sd, 1),
                            age_s=int(now - tr.last_obs), obs=tr.nobs, src=sorted(tr.src),
                            trail=[[p[1], p[2]] for p in getattr(tr, "trail", [])], **f))
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
            out.append(dict(id=-int(t0) % 100000, type=typ, label=TYPES[typ]["label"], ch=TYPES[typ]["ch"], lc=TYPES[typ]["lc"], cls="missile",
                            count=1, lat=place["lat"] if place else None, lon=place["lon"] if place else None,
                            heading=None, heading_conf=0, speed=TYPES[typ]["v"][0], sd_km=None, age_s=int(now - t0),
                            obs=1, src=["event"], likely=None, p={5: p, 15: p, 30: p}, eta_min=max(0.0, round(3 - age, 1)), path=[], dist_km=None, trail=[]))
        # спершу небезпечні, далі ближчі (щоб при p=0 на мапі були різні цілі, а не перші за часом)
        out.sort(key=lambda d: (-d["p"][30], d["dist_km"] if d["dist_km"] is not None else 9999))
        return out


SCOPES = ("oblast", "raion", "city", "district")


def scope_probs(trk, zones, now=None, maxkm=260.0):
    """zones: dict scope → [(lat, lon, R)] (порожній список — зони немає). → dict scope → P(хоч одна ціль увійде за 30 хв).
    Група з n цілей корельована: 1 − Π(1 − p)^√n (як threat_summary). Лише цілі з траєкторією ≤ maxkm від міста."""
    now = now or time.time()
    names = [k for k in SCOPES if zones.get(k)]
    q = {k: 1.0 for k in names}
    if not names:
        return {}
    c0 = zones.get("city") or zones[names[0]]
    for tr in trk.tracks:
        if now - tr.last_obs >= tr.cfg["ttl"] or tr.cfg["cls"] == "recon":   # розвідник не підвищує зону/рівень
            continue
        tr.predict_to(max(tr.t, now))
        x, y, *_ = tr.mean()
        lat, lon = tr.proj.ll(x, y)
        if math.hypot(*Proj(c0[0][0], c0[0][1]).xy(lat, lon)) > maxkm:
            continue
        ps = tr.p_zones([zones[k] for k in names])
        for k, p in zip(names, ps):
            q[k] *= (1 - min(0.99, p)) ** math.sqrt(max(1, min(tr.count, 9)))
    return {k: round(1 - q[k], 3) for k in names}


def _platt(p, ab):
    p = min(0.999, max(0.001, p))
    return 1 / (1 + math.exp(-(ab[0] * math.log(p / (1 - p)) + ab[1])))


def threat_summary(targets, cal=None):
    """Загальна ймовірність, що хоча б одна ціль пройде в радіусі R: 1 − Π(1 − p_i).
    Група з n цілей летить корельовано, тому замість 1−(1−p)^n беремо 1−(1−p)^√n.
    Сирі ймовірності переоцінюють (бектест: при 0.68 збувається ~19 %) → p_any/p_drone/p_missile калібруються
    (CITY_CAL, Platt по горизонтах; немає цілей класу — 0). Рівень (level) — з сирих, як і раніше; сирі — у s["raw"]."""
    cal = CITY_CAL if cal is None else cal

    def comb(cls, T):
        q = 1.0
        for d in targets:
            if (d["cls"] != "recon") if cls is None else d["cls"] == cls:
                p = min(0.99, d["p"][T])
                q *= (1 - p) ** math.sqrt(max(1, min(d["count"], 9)))
        return round(1 - q, 3)
    raw = {"p_any": {T: comb(None, T) for T in (5, 15, 30)},
           "p_drone": {T: comb("drone", T) for T in (5, 15, 30)},
           "p_missile": {T: comb("missile", T) for T in (5, 15, 30)}}
    s = {k: dict(v) for k, v in raw.items()}
    for k, cls in (("p_any", None), ("p_drone", "drone"), ("p_missile", "missile")):
        c = cal.get(k[2:])
        if not c or not any((d["cls"] != "recon") if cls is None else d["cls"] == cls for d in targets):
            continue
        prev = 1.0
        for T in (30, 15, 5):                       # монотонно: p5 ≤ p15 ≤ p30
            if T in c:
                prev = min(prev, round(_platt(raw[k][T], c[T]), 3))
                s[k][T] = prev
    s["raw"] = raw
    raw_s = raw
    etas = [d["eta_min"] for d in targets if d["eta_min"] is not None and d["p"][30] >= 0.1]
    s["eta_min"] = min(etas) if etas else None
    # рівні як в analyzer: 2 — помаранчевий (ціль, ймовірно, пройде в радіусі R від вас), 3 — червоний
    lvl = 0
    if raw_s["p_drone"][30] >= 0.15:
        lvl = 2
    if raw_s["p_drone"][15] >= 0.5 or raw_s["p_missile"][30] >= 0.10:     # дрон майже напевно поруч / ракети
        lvl = 3
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
                       "nobs": tr.nobs, "trail": getattr(tr, "trail", []), "p": [[_r(x, 3) for x in pp] for pp in tr.p], "w": [_r(x, 8) for x in tr.w]})
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
        tr.trail = [list(x) for x in s.get("trail", [])][-TRAIL_MAX:]
        sw = sum(tr.w) or 1.0
        tr.w = [x / sw for x in tr.w]
        trk.tracks.append(tr)
    trk.seen_posts = set(d.get("seen_posts", []))
    trk.events = [tuple(e) for e in d.get("events", []) if len(e) == 5 and e[1] in TYPES]
    trk.home = d.get("home") or trk.home
    return d.get("extra") or {}
