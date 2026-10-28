"""Аналіз постів Telegram — Python-версія алгоритму з src/analyzer.cpp (тримайте синхронними).
Правила беруться з ../data/rules.json та ../data/locations.json."""
import json, math, os, re, time
from dataclasses import dataclass, field
from textnorm import fold, fold_kw, fuzzy_any

DATA = os.environ.get("CYD_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
RED_TTL_MIN, YELLOW_TTL_MIN = 15, 40   # ракети/КАБ, дрони
OBLAST_TTL_MIN = 10                    # «балістика хз звідки» — червоний на всю область, коротко
FAR_KM = 150                           # місце далі — «далека ціль» (лише інформація)

GREEN, YELLOW, RED = 0, 1, 2
LEVEL_NAMES = {GREEN: "green", YELLOW: "yellow", RED: "red"}
# ЖОВТИЙ = дрони (шахеди/БпЛА) або невідома ціль, ЧЕРВОНИЙ = ракети/балістика/КАБ/авіація


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


RULES = _load("rules.json")
LOC = _load("locations.json")
CITIES = {c["id"]: c for c in LOC["cities"]}
# усі ключові слова — у нормалізованій формі (як і текст поста)
for _k in ("approach", "escalate", "clear", "ignore", "unknown_dir"):
    RULES[_k] = [fold_kw(w) for w in RULES.get(_k, [])]
for _t in RULES["threats"]:
    _t["kw"] = [fold_kw(w) for w in _t["kw"]]
    _t["fuzzy"] = [("=" + fold_kw(w[1:])) if w.startswith("=") else fold_kw(w) for w in _t.get("fuzzy", [])]
for _lst in RULES.get("settlements", {}).values():
    for _s in _lst:
        _s["kw"] = [fold_kw(w) for w in _s["kw"]]
for _pl in RULES.get("places", []):
    _pl["kw"] = [fold_kw(w) for w in _pl["kw"]]
for _c in LOC["cities"]:
    _c["kw_f"] = [fold_kw(w) for w in _c["keywords"]]
    for _d in _c["districts"]:
        _d["kw_f"] = [fold_kw(w) for w in _d["keywords"] + [_d["name"].lower()]]
FROM_WORDS = {"с", "з", "из", "зи", "от", "вид", "со", "из-за", "через"}


def norm(text: str) -> str:
    return fold(text)


def _any(t, words):
    return [w for w in words if w in t]


def km_between(la1, lo1, la2, lo2):
    x = (lo2 - lo1) * 111.32 * math.cos(math.radians((la1 + la2) / 2))
    y = (la2 - la1) * 110.57
    return math.hypot(x, y)


def find_places(t):
    """Згадані великі міста: [(place, role)] — role 'from' якщо перед назвою «з/с/из/від/зі сторони…»."""
    out = []
    toks = t.split()
    for pl in RULES.get("places", []):
        for kw in pl["kw"]:
            i = t.find(" " + kw)
            if i < 0:
                continue
            before = t[:i].split()[-2:]
            role = "from" if (before and before[-1] in FROM_WORDS) or (len(before) == 2 and before[0] in ("со", "зи", "з", "с") and before[1].startswith(("сторон", "боку", "напрям"))) else "at"
            out.append((pl, role))
            break
    return out


def threat_of(t):
    """→ (threat dict | None, вага) — тип за підрядком або нечітким збігом слова."""
    best, w = None, 0
    for th in RULES["threats"]:
        if not (_any(t, th["kw"]) or fuzzy_any(t, th.get("fuzzy", []))):
            continue
        w = max(w, th["weight"])
        lv = 2 if th.get("level") == "red" else 1
        if best is None or lv > best[0] or (lv == best[0] and th["weight"] > best[1]["weight"]):
            best = (lv, th)
    return best, w


def angle_diff(a, b):
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


@dataclass
class PostEval:
    score: int = 0
    level: int = GREEN
    near: bool = False      # бал >= 7 — ціль поруч / курс на вас
    kind: str = ""          # "Дрони", "Ракети", ...
    clear: bool = False
    ignored: bool = False
    relevant: bool = False
    far: bool = False          # ціль далеко (>150 км) і не до нас — лише інформація
    far_place: str = ""
    far_km: int = 0
    oblast_wide: bool = False  # балістика з невідомим напрямком — червоний для всієї області на 10 хв
    reasons: list = field(default_factory=list)

    @property
    def reason(self):
        return ", ".join(self.reasons)


def analyze_post(text: str, city_id: str = None, district_id: str = None) -> PostEval:
    ev = PostEval()
    t = fold(text)
    ign = len(_any(t, RULES["ignore"]))
    if ign >= 2 or (ign >= 1 and len(t.encode()) > 350):
        ev.ignored = True
        return ev

    best, threat_w = threat_of(t)
    threat_label = best[1]["label"] if best else None
    approach = bool(_any(t, RULES["approach"]))
    escalate = bool(_any(t, RULES["escalate"]))
    ev.clear = bool(_any(t, RULES["clear"]))

    city = CITIES.get(city_id) if city_id else None
    in_city, dist_bonus, settl_bonus, place, settl_hit = False, 0, 0, None, False
    if city:
        in_city = bool(_any(t, city["kw_f"]))
        dists = city["districts"]
        ud = next((d for d in dists if d["id"] == district_id), None)
        user_sector = ud["sector"] if ud else -2
        for d in dists:
            if not _any(t, d["kw_f"]):
                continue
            in_city = True
            if ud and d["id"] == ud["id"]:
                b = 4
            elif user_sector in (-2, -1) or d["sector"] == -1 or angle_diff(user_sector, d["sector"]) <= 60:
                b = 2
            else:
                b = 1
            if b > dist_bonus:
                dist_bonus, place = b, d["name"]
        user_sec = ud["sector"] if ud else -1
        for s in RULES.get("settlements", {}).get(city["id"], []):
            if not _any(t, s["kw"]):
                continue
            settl_hit = True
            b = 2 if s["km"] <= 15 else (1 if s["km"] <= 45 else 0)
            if b > 0 and user_sec >= 0 and angle_diff(user_sec, s["bearing"]) <= 60:
                b += 1
            settl_bonus = max(settl_bonus, b)

    places = find_places(t)
    unknown_dir = bool(_any(t, RULES.get("unknown_dir", [])))
    ev.relevant = threat_w > 0 or in_city or settl_bonus > 0 or approach or unknown_dir
    if not ev.relevant and not ev.clear:
        return ev

    score = threat_w
    if in_city:
        score += 2
    if in_city and threat_w >= 4:
        score += 1
    if approach and in_city:
        score += 2
    elif approach and settl_bonus:
        score += 1
    score += dist_bonus + settl_bonus
    if escalate:
        score += 3

    if threat_label: ev.reasons.append(threat_label)
    if dist_bonus == 4: ev.reasons.append("ваш район")
    elif place: ev.reasons.append(place)
    elif in_city: ev.reasons.append("місто")
    if settl_bonus: ev.reasons.append("поруч")
    if approach: ev.reasons.append("курс")
    if escalate: ev.reasons.append("вибухи/ППО")

    if ev.clear and not escalate and dist_bonus < 4:
        ev.score, ev.level, ev.reasons = 0, GREEN, ["відбій / ціль пролетіла"]
        return ev

    # «Харків ракета»: усі згадані місця далеко, про нас — нічого → лише інформація, рівень не підвищуємо
    at = [pl for pl, role in places if role == "at"]
    if city and at and not (in_city or settl_hit or dist_bonus):
        dists = [(km_between(city["lat"], city["lon"], pl["lat"], pl["lon"]), pl) for pl in at]
        if all(d > FAR_KM for d, _ in dists):
            d, pl = min(dists, key=lambda x: x[0])
            ev.far = True
            ev.far_place, ev.far_km = pl["n"], round(d)
            ev.score = min(score, 2)
            ev.level, ev.kind, ev.near = GREEN, "", False
            ev.reasons = ([threat_label] if threat_label else []) + [f"далеко: {pl['n']} ~{round(d)} км"]
            return ev

    # балістика без місця або «хз звідки» → червоний на всю область (коротко, OBLAST_TTL_MIN)
    if best and best[1]["type"] == "ballistic" and (unknown_dir or not (at or in_city or settl_hit or dist_bonus)):
        score = max(score, 6)
        ev.oblast_wide = True
        ev.reasons.append("напрямок невідомий — вся область")
    ev.score = score
    ev.near = score >= 7
    if score >= 3:
        ev.level = best[0] if best else YELLOW
        ev.kind = best[1].get("kind", best[1]["label"]) if best else "Загроза"
    return ev


def analyze_feed(posts, city_id=None, district_id=None, now=None):
    """posts: список dict {id, ts, text} у хронологічному порядку. Повертає (level, post|None, PostEval)."""
    now = now or time.time()
    cur, best, best_ev = GREEN, None, PostEval()
    for p in posts:
        if not p.get("text"):
            continue
        age_min = (now - p["ts"]) / 60 if p.get("ts") else 0
        if age_min > YELLOW_TTL_MIN:
            continue
        e = analyze_post(p["text"], city_id, district_id)
        if e.ignored:
            continue
        if e.clear and e.level == GREEN:
            cur, best, best_ev = GREEN, p, e
            continue
        lvl = e.level
        if e.oblast_wide and age_min > OBLAST_TTL_MIN:
            continue
        if lvl == RED and age_min > RED_TTL_MIN:
            continue   # ракети швидкі — старий пост неактуальний
        if lvl >= cur and lvl > GREEN:
            cur, best, best_ev = lvl, p, e
        elif cur == GREEN and e.relevant:
            best, best_ev = p, e
    return cur, best, best_ev
