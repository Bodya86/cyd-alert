"""Аналіз постів Telegram — Python-версія алгоритму з src/analyzer.cpp (тримайте синхронними).
Правила беруться з ../data/rules.json та ../data/locations.json."""
import json, math, os, re, time
from dataclasses import dataclass, field
from textnorm import fold, fold_kw, fuzzy_any

# Увесь сервер (GitHub Actions працює в UTC) показує час за Києвом: підписи, журнали, добова статистика.
# Порівняння «свіжості» постів скрізь ідуть в epoch-секундах і від поясу не залежать.
os.environ["TZ"] = os.environ.get("CYD_TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4")
if hasattr(time, "tzset"):
    time.tzset()

DATA = os.environ.get("CYD_DATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
RED_TTL_MIN, YELLOW_TTL_MIN = 15, 40   # ракети/КАБ, дрони
OBLAST_TTL_MIN = 10                    # «балістика хз звідки» — червоний на всю область, коротко
FAR_KM = 150                           # місце далі — «далека ціль» (лише інформація)
NEAR_KM = 60                           # для загальнонаціональних каналів: місце ближче — пост «наш»
EXPLICIT_TTL_MIN = 20                  # пост «курс на ваше місто» дає помаранчевий/червоний без тривоги не довше
DEDUP_MIN = 10                         # одна подія з різних каналів: той самий тип+місце в межах 10 хв

# Рівні за БЛИЗЬКІСТЮ до вас (екран фарбується відповідно, без модальних вікон):
#   GREEN  — чисто;
#   YELLOW — дрони в області / загальна загроза;
#   ORANGE — ціль курсом на ваше місто («курс на Кременчук»), ракети/КАБ в області;
#   RED    — ціль над / курсом на ваш район, вибухи в місті, ракети поруч, балістика на область.
GREEN, YELLOW, ORANGE, RED = 0, 1, 2, 3
LEVEL_NAMES = {GREEN: "green", YELLOW: "yellow", ORANGE: "orange", RED: "red"}
CLS_DRONE, CLS_MISSILE = 1, 2     # клас загрози (rules.json level yellow/red) — окремо від рівня близькості
# ЖОВТИЙ = дрони (шахеди/БпЛА) або невідома ціль, ЧЕРВОНИЙ = ракети/балістика/КАБ/авіація


def _load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


RULES = _load("rules.json")
# підсумки/звіти постфактум («За ніч ворог застосував…», «ЗБИТО/ПОДАВЛЕНО…», «Попередньо, вранці ворог застосував 6 ракет по Кременчуку»)
# — не загроза зараз: без цього вони давали червоний рівень і сповіщення вранці (30.10 07:26). Текст — у fold()-формі.
RETRO_RE = re.compile(r"(ворог\w*|противник\w*|окупант\w*|рф|росия|россия|враг)\s+(\w+\s+)?(застосував|применил|атакував|атаковал|"
                      r"використав|использовал)")
RETRO_HINT_RE = re.compile(r"\d|попередн|предварит|вранци|утром|вночи|ночью|вчора|вчера|минулої|прошлой")
RETRO_STRONG_RE = re.compile(r"[зс]бито\s*/\s*подавлено|\b(у|в) (нич|ночь) на \d|за (нич|ночь|добу|сутки) (ворог|противник|враг)")
LIVE_RE = re.compile(r"курс|пидлит|подлет|пидльот|летит|летить|летять|летят|укрит|укрыт|напрям|направлен|загроз|угроз|тривог|тревог|"
                     r"вибух|взрыв|ппо|зараз|сейчас|работа|працю")
MKR_RE = re.compile(r"(?<![\w-])(мКР|МКР)(?![\w.-])")
# «Іскандер-К» / «крилаті ракети» — крилата ракета (траєкторія, хвилини), не балістика
CRUISE_RE = re.compile(r"крилат|крылат|[иі]скандер[\s-]*к\b")
BALLISTIC_RE = re.compile(r"балист|баллист|кинджал|кинжал|кн-23|kn-23|[иі]скандер[\s-]*м\b|циркон")


def is_report(t):
    """Пост-підсумок постфактум (fold-текст)."""
    return bool(RETRO_STRONG_RE.search(t)) or (bool(RETRO_RE.search(t)) and bool(RETRO_HINT_RE.search(t)) and not LIVE_RE.search(t))
LOC = _load("locations.json")
CHANNELS_CFG = _load("channels.json")["channels"]
CHANNELS = {c["id"].lower(): c for c in CHANNELS_CFG}
OBLAST_KW = {int(k): [fold_kw(w) for w in v] for k, v in RULES.get("oblast_kw", {}).items()}
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
# «по нам», «над містом», «на город»… — займенникові ключі міста: означають НАШЕ місто лише в міському каналі цього міста
GENERIC_PREP = ("по ", "на ", "над ", "в ", "у ", "до ")
# прикметник / «-щина»: «Кременчуцький район», «Полтавська область», «Кременчуччина» — це район/область, а не місто
_ADJ_RE = re.compile(r"(?:[цсз]к(?:ий|ого|ому|им|ими|ом|ая|а|ои|ой|ую|у|ие|их|е|и)|(?:щин|ччин)[а-я]*)$")


# загальні слова районів міста («центр», «НПЗ», «набережна»): з чужого каналу без назви міста — це про інше місто
AMBIG_DISTRICT_KW = {"центр", "соборн", "набережн", "площ", "правий берег", "правобереж", "нпз", "нефтезавод",
                     "нафтозавод", "промзон", "автозавод"}


def is_generic_kw(kw):
    return kw.startswith(GENERIC_PREP)


def city_match(t, kws, home=True):
    """→ (місто згадано, згадано лише район/прикметник). Займенникові ключі — лише для home-каналу."""
    hit = raion = False
    for kw in kws:
        if is_generic_kw(kw):
            if home and kw in t:
                hit = True
            continue
        i = t.find(kw)
        while i >= 0:
            s = t.rfind(" ", 0, i) + 1
            e = t.find(" ", i)
            tok = t[s:e if e >= 0 else len(t)].strip(".-/")
            if _ADJ_RE.search(tok):
                raion = True
            else:
                hit = True
            i = t.find(kw, i + 1)
    return hit, raion and not hit


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
    local: bool = False        # згадано ваше місто/район/село/область або місце ближче NEAR_KM
    place: str = ""            # ключ місця для дедуплікації між каналами
    threat: str = ""           # тип загрози (ballistic/missile/kab/drone/...)
    sub: str = ""              # підтип (rules.json "sub"): "banderol" — мКР «Бандероль»
    cls: int = 0               # CLS_DRONE / CLS_MISSILE (0 — немає)
    prox: int = 0              # 0 — область/загально, 1 — місто/курс на місто/поруч, 2 — ваш район/над вами
    explicit: bool = False     # пост прямо про ваше місто/район/≤15 км (або балістика на область) — може дати
                               # помаранчевий/червоний і БЕЗ офіційної тривоги; інакше без тривоги — не вище жовтого
    scope: str = ""            # куди саме: "oblast" / "raion" / "city" / "district" (ваш район міста) / "" — невідомо
    reasons: list = field(default_factory=list)

    @property
    def reason(self):
        return ", ".join(self.reasons)


def channel_cfg(ch):
    return CHANNELS.get((ch or "").lower().lstrip("@")) or {"id": ch, "scope": "national", "oblasts": [], "home": None,
                                                            "weight": 0.5, "default": False}


def channel_is_home(ch, city_id):
    """Міський канал саме цього міста (kremen_sv для Кременчука): лише в ньому «по нам / над містом» = наше місто."""
    if not ch or not city_id:
        return True
    c = channel_cfg(ch)
    return c.get("home") == city_id or (CITIES.get(city_id) or {}).get("channel", "").lower() == ch.lower().lstrip("@")


def channel_is_local(ch, city_id):
    """Канал «ваш»: міський канал вашого міста або обласний канал вашої області. Інакше — remote (правило відстані)."""
    c = channel_cfg(ch)
    city = CITIES.get(city_id) if city_id else None
    if not city:
        return False
    if c.get("home") == city_id or (CITIES.get(city_id) or {}).get("channel", "").lower() == (ch or "").lower():
        return True
    return c.get("scope") == "oblast" and city["oblast"] in c.get("oblasts", [])


def default_channels(city_id=None, oblast=None):
    """Канали за замовчуванням: усі national з default + канали вашої області (default)."""
    city = CITIES.get(city_id) if city_id else None
    ob = city["oblast"] if city else oblast
    out = []
    for c in CHANNELS_CFG:
        if not c.get("default"):
            continue
        if c["scope"] == "national" or (ob is not None and ob in c.get("oblasts", [])):
            out.append(c["id"])
    if city and city.get("channel") and city["channel"] not in out:
        out.insert(0, city["channel"])
    return out


def analyze_post(text: str, city_id: str = None, district_id: str = None, remote: bool = False, home: bool = None) -> PostEval:
    """home — пост із міського каналу цього міста (None → not remote, для старих викликів і тестів)."""
    if home is None:
        home = not remote
    ev = PostEval()
    t = fold(text)
    if MKR_RE.search(text or ""):
        t += "бандерол "         # «мКР» (малогабаритна КР «Бандероль»), регістр важливий: «мкр.» — мікрорайон
    ign = len(_any(t, RULES["ignore"]))
    if ign >= 2 or (ign >= 1 and len(t.encode()) > 350):
        ev.ignored = True
        return ev

    if is_report(t):
        ev.ignored = True
        ev.reasons = ["звіт постфактум"]
        return ev

    best, threat_w = threat_of(t)
    if best and best[1]["type"] == "ballistic" and CRUISE_RE.search(t) and not BALLISTIC_RE.search(t):
        mis = next((th for th in RULES["threats"] if th["type"] == "missile" and not th.get("sub")), None)
        if mis:
            best = (2 if mis.get("level") == "red" else 1, mis)
    threat_label = best[1]["label"] if best else None
    approach = bool(_any(t, RULES["approach"]))
    escalate = bool(_any(t, RULES["escalate"]))
    ev.clear = bool(_any(t, RULES["clear"]))

    city = CITIES.get(city_id) if city_id else None
    in_city, dist_bonus, settl_bonus, place, settl_hit, raion_hit = False, 0, 0, None, False, False
    if city:
        in_city, raion_hit = city_match(t, city["kw_f"], home)
        dists = city["districts"]
        ud = next((d for d in dists if d["id"] == district_id), None)
        user_sector = ud["sector"] if ud else -2
        named = in_city
        for d in dists:
            kws = d["kw_f"] if (home or named) else [k for k in d["kw_f"] if k not in AMBIG_DISTRICT_KW]
            if not _any(t, kws):
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
    ev.threat = best[1]["type"] if best else ""
    ev.sub = best[1].get("sub", "") if best else ""
    obl_hit = bool(city and _any(t, OBLAST_KW.get(city["oblast"], [])))
    near_place = None
    if city:
        for pl, role in places:
            if role == "at" and km_between(city["lat"], city["lon"], pl["lat"], pl["lon"]) <= NEAR_KM:
                near_place = pl
                break
    ev.local = bool(in_city or settl_hit or dist_bonus or obl_hit or near_place or raion_hit)
    if dist_bonus:
        ev.place = place or ""
    elif settl_hit:
        ev.place = "settl:" + next((s["kw"][0] for s in RULES.get("settlements", {}).get(city["id"], []) if _any(t, s["kw"])), "")
    elif in_city:
        ev.place = "city"
    elif near_place:
        ev.place = near_place["n"]
    elif raion_hit:
        ev.place = "raion"
    elif obl_hit:
        ev.place = "oblast"
    elif places:
        ev.place = places[0][0]["n"]
    if dist_bonus == 4:
        ev.scope = "district"
    elif in_city or dist_bonus or settl_bonus >= 2:
        ev.scope = "city"
    elif raion_hit or settl_bonus >= 1 or near_place:     # населений пункт ≤ 45 км — район; далі (Полтава, Миргород) — ні
        ev.scope = "raion"
    elif obl_hit:
        ev.scope = "oblast"
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

    if ev.clear and not escalate and dist_bonus < 4 and not (remote and not ev.local):
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

    # загальнонаціональний / чужий обласний канал: без згадки вашої території — лише інформація
    if remote and city and not ev.local:
        ev.clear = False          # «відбій» з чужого каналу не скасовує ваш рівень
        ev.score = min(score, 2)
        ev.level, ev.kind, ev.near = GREEN, "", False
        ev.reasons = ([threat_label] if threat_label else []) + ["не про вашу область"]
        return ev
    # балістика без місця або «хз звідки» → червоний на всю область (коротко, OBLAST_TTL_MIN)
    if best and best[1]["type"] == "ballistic" and (unknown_dir or not (at or in_city or settl_hit or dist_bonus)) \
            and (not remote or obl_hit or in_city):
        score = max(score, 6)
        ev.oblast_wide = True
        ev.scope = ev.scope or "oblast"
        ev.reasons.append("напрямок невідомий — вся область")
    ev.score = score
    ev.near = score >= 7
    if score >= 3:
        ev.cls = best[0] if best else CLS_DRONE
        ev.kind = best[1].get("kind", best[1]["label"]) if best else "Загроза"
        # близькість: 2 — ваш район (або сусідній сектор із курсом/вибухами), вибухи в місті; 1 — місто/≤15 км/курс
        if dist_bonus == 4 or (dist_bonus >= 2 and (approach or escalate)) or (in_city and escalate):
            ev.prox = 2
        elif in_city or settl_bonus >= 2 or ev.near:
            ev.prox = 1
        ballistic_obl = ev.threat == "ballistic" and obl_hit      # «балістика на Полтавщину» — хвилини до удару
        ev.level = level_of(ev.cls, ev.prox, ev.oblast_wide or ballistic_obl, bool(settl_bonus))
        ev.explicit = bool(in_city or dist_bonus or settl_bonus >= 2 or ev.oblast_wide or ballistic_obl)
    return ev


AGE_STEP_MIN = 10   # дрон рухається: кожні 10 хв без нових постів близькість падає на щабель (червоний → помаранчевий → жовтий)


def aged_level(e, age_min):
    if e.level <= YELLOW or e.cls != CLS_DRONE or e.oblast_wide:
        return e.level
    return max(YELLOW, e.level - int(max(0, age_min) // AGE_STEP_MIN))


def level_of(cls, prox, oblast_wide=False, nearby=False):
    """Клас загрози + близькість → рівень. Дрон: область → жовтий, місто → помаранчевий, район → червоний.
    Ракета/КАБ/балістика: область → помаранчевий, місто/поруч/«вся область» (балістика) → червоний."""
    if cls == CLS_MISSILE:
        return RED if (prox >= 1 or oblast_wide or nearby) else ORANGE
    return (YELLOW, ORANGE, RED)[min(prox, 2)]


def analyze_feed(posts, city_id=None, district_id=None, now=None, remote=False):
    """posts: список dict {id, ts, text} у хронологічному порядку. Повертає (level, post|None, PostEval)."""
    now = now or time.time()
    cur, best, best_ev = GREEN, None, PostEval()
    for p in posts:
        if not p.get("text"):
            continue
        age_min = (now - p["ts"]) / 60 if p.get("ts") else 0
        if age_min > YELLOW_TTL_MIN:
            continue
        e = analyze_post(p["text"], city_id, district_id, remote)
        if e.ignored:
            continue
        e.level = aged_level(e, age_min)
        if e.explicit and age_min > EXPLICIT_TTL_MIN:
            e.explicit = False
        if e.clear and e.level == GREEN:
            cur, best, best_ev = GREEN, p, e
            continue
        lvl = e.level
        if e.oblast_wide and age_min > OBLAST_TTL_MIN:
            continue
        if e.cls == CLS_MISSILE and age_min > RED_TTL_MIN:
            continue   # ракети швидкі — старий пост неактуальний
        if lvl >= cur and lvl > GREEN:
            cur, best, best_ev = lvl, p, e
        elif cur == GREEN and e.relevant:
            best, best_ev = p, e
    return cur, best, best_ev


def _live(e, age_min):
    if e.oblast_wide and age_min > OBLAST_TTL_MIN:
        return False
    if e.cls == CLS_MISSILE and age_min > RED_TTL_MIN:
        return False
    return age_min <= YELLOW_TTL_MIN


def analyze_multi(feeds, city_id=None, district_id=None, now=None):
    """Кілька каналів: feeds = {channel: [posts]}.
    1) кожен пост оцінюється з урахуванням області каналу (national/чужа область → правило відстані);
    2) «відбій» з вашого каналу скасовує все, що було раніше;
    3) однакові події (тип + місце, ±DEDUP_MIN хв) з різних каналів зливаються в кластер,
       довіра = 1 − Π(1 − weight); ≥2 каналів → +1 до балу; кластер лише з малодовірених каналів (<0.5) — не вище жовтого.
    → (level, best_post|None (з полем channel), best_ev, clusters)"""
    now = now or time.time()
    items = []
    last_clear = 0
    for ch, posts in (feeds or {}).items():
        cfg = channel_cfg(ch)
        remote = not channel_is_local(ch, city_id)
        home = channel_is_home(ch, city_id)
        for p in posts or []:
            if not p.get("text"):
                continue
            ts = p.get("ts") or now
            age = (now - ts) / 60
            if age > YELLOW_TTL_MIN:
                continue
            e = analyze_post(p["text"], city_id, district_id, remote, home)
            if e.ignored:
                continue
            e.level = aged_level(e, age)
            if e.explicit and age > EXPLICIT_TTL_MIN:
                e.explicit = False    # «курс на місто» старший за 20 хв — уже не підстава для помаранчевого
            if e.clear and e.level == GREEN:
                if not remote or e.local:
                    last_clear = max(last_clear, ts)
                continue
            items.append((ts, ch, cfg.get("weight", 0.5), p, e, age))
    items.sort(key=lambda x: x[0])
    clusters = []
    for ts, ch, w, p, e, age in items:
        if ts < last_clear or not (e.level > GREEN or e.relevant):
            continue
        key = (e.threat or e.kind, e.place)
        cl = next((c for c in clusters if c["key"] == key and abs(ts - c["ts"]) <= DEDUP_MIN * 60), None)
        if cl is None:
            cl = {"key": key, "ts": ts, "first": ts, "channels": {}, "best": None}
            clusters.append(cl)
        cl["ts"] = max(cl["ts"], ts)
        cl["channels"][ch] = max(cl["channels"].get(ch, 0), w)
        if _live(e, age) and (cl["best"] is None or (e.level, e.score, w) >= (cl["best"][1].level, cl["best"][1].score, cl["best"][3])):
            cl["best"] = (p, e, ch, w)
    cur, best, best_ev, best_key = GREEN, None, PostEval(), (-1, -1.0, 0)
    for cl in clusters:
        conf = 1.0
        for w in cl["channels"].values():
            conf *= (1 - w)
        cl["conf"] = round(1 - conf, 2)
        cl["n"] = len(cl["channels"])
        if not cl["best"]:
            continue
        p, e, ch, _w = cl["best"]
        lvl = e.level
        if cl["n"] >= 2 and lvl > GREEN:
            e.score += 1
            e.near = e.near or e.score >= 7
            e.reasons.append(f"{cl['n']} канали")
        if cl["conf"] < 0.5 and lvl == RED:
            lvl = ORANGE          # лише малодовірені канали — на щабель нижче
        e.level = lvl
        k = (lvl if lvl > GREEN else -1, cl["conf"], cl["ts"])
        if k > best_key:
            best_key, cur, best, best_ev = k, lvl, {**p, "channel": ch}, e
    return cur, best, best_ev, clusters


def cap_level(level, official_oblast, explicit):
    """Головне правило: помаранчевий/червоний для вашого міста — лише якщо є офіційна тривога у вашій області/районі
    або свіжий (≤ EXPLICIT_TTL_MIN) пост прямо про ціль на ваше місто/район. Інакше — не вище жовтого."""
    if official_oblast or explicit:
        return level
    return min(level, YELLOW)
