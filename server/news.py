"""Новини про влучання/вибухи → «куди б'ють останнім часом» (апріорі для «ймовірної цілі»).

Лише ІНФОРМАЦІЯ: рівень тривоги з цього модуля не підвищується ніколи. Він дає:
  • extract_hits(text) — місце (населений пункт / область), тип зброї і довіру повідомлення про влучання;
  • NewsHits — ковзний набір влучань за keep_days (у гілці data: api/news/hits.json), злиття дублікатів
    з різних джерел (та сама подія ±6 год → довіра 1 − Π(1 − w)), бал вузла S_k = Σ conf · 0.5^(вік / half-life);
  • NewsFeed — фоновий потік: Telegram-канали новин + RSS сайтів (data/news_sources.json) раз на refresh_s,
    паралельно з тайм-аутами; також пости каналів основного циклу (ОВА, монітори).
Вплив на P(ціль): p_k ∝ p_hist,k · (1 + β·S_k); β і half-life підбираються бектестом (tools/news_backtest.py),
β = 0 → вимкнено (data/model_params.json: news_beta, news_hl_days)."""
import email.utils, html, json, math, os, re, sys, threading, time, traceback, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
CFG_PATH = os.path.join(ROOT, "data", "news_sources.json")
PARAMS_PATH = os.path.join(ROOT, "data", "model_params.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) CYD-Alert-News/1.0"


def load_cfg(path=CFG_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"telegram": [], "rss": []}


def load_params():
    try:
        with open(PARAMS_PATH, encoding="utf-8") as f:
            p = json.load(f)
        return float(p.get("news_beta", 0.0)), float(p.get("news_hl_days", 10.0))
    except (OSError, ValueError):
        return 0.0, 10.0


# ------------------------------------------------------------------ розбір тексту
STRONG = ("влучан", "влучил", "влучив", "прильот", "прилет", "уражен", "пошкодж", "зруйнов", "руйнуван", "знищен",
          "загинул", "загинув", "загибл", "поранен", "травмован", "постражд", "попадан", "повреж", "разруш", "погиб",
          "ранен", "атакував", "атакувал", "атакуют", "атакує", "атакован", "удар по", "удару по", "ударом по", "ударив", "вдарив", "вдарил", "вдарили",
          "обстріл", "обстрел", "наслідки атаки", "наслідки удару", "уламк", "обломк", "детонац")
WEAK = ("вибух", "взрыв")
ATTACK = ("атак", "удар", "обстріл", "обстрел", "шахед", "дрон", "бпла", "бплa", "безпілот", "беспилот", "ракет",
          "балістик", "баллист", "каб", "авіабомб", "авиабомб", "вибух", "взрыв", "прильот", "прилет", "влучан",
          "уражен", "герань", "іскандер", "кинджал", "окупант", "росіян", "ворог", "рф")
# не про подію удару: прогнози/загрози/навчання/спростування; «можливі вибухи» — попередження
SKIP = ("відбій", "отбой", "загроза", "угроза", "курсом", "у напрямку", "в напрямку", "в сторону", "можливі вибух",
        "можливо вибух", "навчан", "учени", "тренуван", "фейк", "не підтверджу", "не відповідає дійсн", "не зафіксован",
        "без влучан", "збито всі", "роботи ппо", "робота ппо", "працює ппо", "працюють ппо", "сили ппо",
        "головні події", "головне за", "відновлен", "відбудов", "річниц", "роковин", "попрощ", "вшанув", "суд ", "вирок")
# не місця: прикметники районів/областей/районів міст («Олександрійському районі», «Шевченківський»), імена, загальні слова
NOT_PLACE = ("україн", "головн", "середн", "серг", "володимир", "олександр ", "зеленськ", "кличк", "сирськ", "табурец",
             "малецьк", "когут", "лисак", "федоров", "росі", "москв", "курськ", "брянськ", "бєлгород", "білгород ")
STOP_WORDS = {"олександр", "олександра", "серед", "середа", "середу", "середи", "середні", "нове", "новий", "нова", "нові",
              "нового", "новое", "новая", "основа", "основі", "основу", "північне", "північний", "південне", "південний",
              "зарічний", "зарічному", "заріччя", "самара", "самарі", "вишня", "болгарка", "романа", "романів"}
ADJ = re.compile(r"(ськ|цьк|зьк)(ий|ому|ого|ій|а|ої|ім|ою|е|их|і)$")
WEAPON = [("ballistic", ("балістик", "баллист", "іскандер", "искандер", "кинджал", "кинжал", "кн-23", "kn-23")),
          ("kab", ("каб", "авіабомб", "авиабомб", "керован", "фаб")),
          ("missile", ("ракет", "крилат", "х-101", "х-22", "калібр", "калибр", "х-59", "х-69")),
          ("drone", ("шахед", "бпла", "дрон", "безпілот", "беспилот", "герань", "гербер", "мопед", "ударн"))]
CYR = re.compile(r"[A-Za-zА-ЯІЇЄҐа-яіїєґ'\u2019\u02bc\-]+")
# поширені форми, які геокодер плутає/не знає (місцевий відмінок «у Києві», «у Сумах», «Горішніх Плавнях»)
ALIASES = [("києв", "kyiv"), ("київ", "kyiv"), ("киев", "kyiv"), ("сумах", "sumy"), ("сумам", "sumy"), ("суми", "sumy"),
           ("сумщ", None), ("горішн", "hplavni"), ("горишн", "hplavni"), ("кременчу", "kremenchuk"), ("кременчуг", "kremenchuk"),
           ("харків", "kharkiv"), ("харков", "kharkiv"), ("харьков", "kharkiv"), ("дніпр", "dnipro"), ("днепр", "dnipro"), ("одес", "odesa"),
           ("запоріж", "zaporizhzhia"), ("запорож", "zaporizhzhia"), ("кривому", "kryvyirih"), ("кривий", "kryvyirih"),
           ("кривой", "kryvyirih"), ("кривого", "kryvyirih"), ("полтав", "poltava"), ("черкас", "cherkasy"),
           ("кропивницьк", "kropyvnytskyi"), ("миколаїв", "mykolaiv"), ("николаев", "mykolaiv"), ("херсон", "kherson"),
           ("львів", "lviv"), ("львов", "lviv"), ("чернігів", "chernihiv"), ("чернигов", "chernihiv"),
           ("житомир", "zhytomyr"), ("хмельницьк", "khmelnytskyi"), ("кропивниц", "kropyvnytskyi"), ("вінниц", "vinnytsia"), ("винниц", "vinnytsia")]
# слова-області в місцевому відмінку не мають ставати містом («на Черкащині» ≠ Черкаси)
OBL_SUFFIX = re.compile(r"(щин|ччин|ччин|тт[яі]|жж[яі]|чин[аіиу])", re.I)

_GEO = None
_CITY = None


def _geo():
    global _GEO, _CITY
    if _GEO is None:
        import history_model as hm
        _GEO = hm.Geo()
        locs = json.load(open(os.path.join(ROOT, "data", "locations.json"), encoding="utf-8"))["cities"]
        _CITY = {c["id"]: c for c in locs}
    return _GEO


def _oblast_of(low):
    import history_model as hm
    best = None
    for w, ob in hm.OBL_WORDS:
        i = low.find(w)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, ob)
    return best[1] if best else None


def weapon_of(low):
    for k, kws in WEAPON:
        for w in kws:
            if (" " + w) in (" " + low) if len(w) <= 3 else w in low:
                return k
    return "unknown"


def extract_hits(text, max_places=2, default_ob=None):
    """→ [dict(name, lat, lon, ob, k (тип зброї), q (0..1 — сила ознак))]; [] — якщо це не повідомлення про удар.
    Оголошення/загрози/«курсом на» — не влучання; область без міста → запис з name=None (лише для статистики області)."""
    if not text:
        return []
    if sum(text.count(b) for b in ("🔹", "📌", "▪️", "•")) >= 3:
        return []          # дайджест кількох подій — місце/подію не розділити надійно
    t = re.sub(r"\s+", " ", text)[:700]
    low = t.lower()
    if any(s in low for s in SKIP):
        return []
    strong = any(s in low for s in STRONG)
    weak = any(s in low for s in WEAK)
    if not (strong or weak) or not any(a in low for a in ATTACK):
        return []
    q = 1.0 if strong else 0.5
    k = weapon_of(low)
    ob_ctx = _oblast_of(low)
    geo = _geo()
    out, seen = [], set()
    for m in CYR.finditer(t):
        w = m.group(0).strip("-'")
        if len(w) < 4 or not w[:1].isupper():
            continue
        wl = w.lower().replace("\u2019", "'").replace("\u02bc", "'")
        if OBL_SUFFIX.search(wl) and not wl.startswith(("черкас", "кременч")):
            continue
        if wl.startswith(NOT_PLACE) or wl in STOP_WORDS:
            continue
        if ADJ.search(wl) and not wl.startswith(("кропивницьк", "хмельницьк")):
            continue
        g = None
        for stem, cid in ALIASES:
            if wl.startswith(stem):
                if cid is None:
                    g = False
                else:
                    c = _CITY[cid]
                    g = dict(name=c["name"], lat=c["lat"], lon=c["lon"], ob=c["oblast"], pop=10 ** 6)
                break
        if g is False:
            continue
        if g is None:
            r = geo.find(w, True, ob_ctx if ob_ctx is not None else default_ob)
            if r and r["pop"] >= 5000:
                g = dict(name=r["name"].capitalize(), lat=r["lat"], lon=r["lon"], ob=r["ob"], pop=r["pop"])
        if g and out and g["pop"] < 40000:
            continue            # друге місце в тексті — лише велике місто (інакше це часто прізвище/назва об'єкта)
        if not g or g["name"] in seen or any(_km(g["lat"], g["lon"], o["lat"], o["lon"]) < 12 for o in out):
            continue
        seen.add(g["name"])
        out.append(dict(name=g["name"], lat=round(g["lat"], 4), lon=round(g["lon"], 4), ob=g["ob"], k=k, q=q))
        if len(out) >= max_places:
            break
    if not out and ob_ctx is not None:
        out.append(dict(name=None, lat=None, lon=None, ob=ob_ctx, k=k, q=q * 0.7))
    return out


# ------------------------------------------------------------------ набір влучань
def _km(a, b, c, d):
    return math.hypot((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * 111.2)


class NewsHits:
    MERGE_S = 6 * 3600

    def __init__(self, keep_days=30):
        self.items = []         # dict(t, s, w, n, la, lo, ob, k, id)
        self.ids = set()
        self.keep = keep_days * 86400
        self.lock = threading.Lock()
        self.changed = False

    def add_post(self, src, pid, ts, text, w, default_ob=None):
        key = f"{src}/{pid}"
        if key in self.ids:
            return 0
        self.ids.add(key)
        if time.time() - ts > self.keep:
            return 0
        n = 0
        for h in extract_hits(text, default_ob=default_ob):
            with self.lock:
                self.items.append(dict(t=int(ts), s=src, w=round(w * h["q"], 2), n=h["name"], la=h["lat"], lo=h["lon"],
                                       ob=h["ob"], k=h["k"], id=key))
            n += 1
        self.changed |= n > 0
        return n

    def prune(self, now=None):
        now = now or time.time()
        with self.lock:
            self.items = [x for x in self.items if now - x["t"] <= self.keep]

    def events(self):
        """Злиті події: (місто, клас зброї) у межах MERGE_S → dict(t, n, la, lo, ob, k, conf, src[])."""
        with self.lock:
            items = sorted(self.items, key=lambda x: x["t"])
        ev = []
        for x in items:
            kc = "missile" if x["k"] in ("missile", "ballistic") else x["k"]
            e = next((e for e in reversed(ev[-60:]) if e["n"] == x["n"] and e["ob"] == x["ob"] and e["kc"] == kc
                      and x["t"] - e["t1"] <= self.MERGE_S), None)
            if e is None:
                e = dict(t=x["t"], t1=x["t"], n=x["n"], la=x["la"], lo=x["lo"], ob=x["ob"], k=x["k"], kc=kc, ws={})
                ev.append(e)
            e["t1"] = x["t"]
            e["ws"][x["s"]] = max(e["ws"].get(x["s"], 0), x["w"])
        for e in ev:
            q = 1.0
            for w in e["ws"].values():
                q *= 1 - w
            e["conf"] = round(1 - q, 3)
            e["src"] = sorted(e["ws"]); del e["ws"]
        return ev

    def node_scores(self, nodes, now=None, hl_days=10.0, before=None):
        """S_k = Σ conf · 0.5^(вік/hl) для вузлів history-моделі (місто ≤ 25 км від вузла)."""
        now = now or time.time()
        return node_scores(self.events(), nodes, now, hl_days)

    def to_json(self):
        with self.lock:
            return {"v": 1, "items": list(self.items)}

    def load_json(self, d):
        if not d or d.get("v") != 1:
            return
        with self.lock:
            self.items = [x for x in d.get("items", []) if "t" in x and "id" in x]
            self.ids = {x["id"] for x in self.items}


_NODE_CACHE = {}


def node_of(nodes, lat, lon, maxkm=25.0):
    key = (id(nodes), round(lat, 3), round(lon, 3))
    if key in _NODE_CACHE:
        return _NODE_CACHE[key]
    best, bd = None, maxkm
    for k, n in enumerate(nodes):
        d = _km(lat, lon, n[1], n[2])
        if d <= bd:
            best, bd = k, d
    _NODE_CACHE[key] = best
    return best


def node_scores(events, nodes, now, hl_days=10.0):
    out = {}
    hl = hl_days * 86400
    for e in events:
        if e["la"] is None or e["t"] > now:
            continue
        k = node_of(nodes, e["la"], e["lo"])
        if k is None:
            continue
        out[k] = out.get(k, 0.0) + e["conf"] * 0.5 ** ((now - e["t"]) / hl)
    return out


def summary(events, lat, lon, ob, now=None, hl_days=10.0, top=3):
    """Для стану користувача: скільки влучань поруч (≤50 км) за 24 год / 7 діб, в області, де б'ють найчастіше."""
    now = now or time.time()
    n24 = n7 = o7 = 0
    by = {}
    last = None
    for e in events:
        age = now - e["t"]
        if age < 0 or age > 7 * 86400:
            continue
        near = e["la"] is not None and _km(lat, lon, e["la"], e["lo"]) <= 50
        if near:
            n7 += 1
            n24 += age <= 86400
            if last is None or e["t"] > last["t"]:
                last = e
        if e["ob"] == ob:
            o7 += 1
            if e["n"]:
                by[e["n"]] = by.get(e["n"], 0.0) + e["conf"] * 0.5 ** (age / (hl_days * 86400))
    s = sum(by.values()) or 1
    tops = [[n, round(100 * v / s)] for n, v in sorted(by.items(), key=lambda x: -x[1])[:top]]
    return {"n24": n24, "n7": n7, "obl7": o7, "top": tops,
            "last": {"n": last["n"], "t": last["t"], "k": last["k"]} if last else None}


# ------------------------------------------------------------------ джерела
def _get(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _clean(s):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def parse_rss(raw):
    """→ [{id, ts, text}] (RSS 2.0 / Atom)."""
    out = []
    root = ET.fromstring(raw)
    for it in root.iter():
        tag = it.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        f = {c.tag.rsplit("}", 1)[-1]: (c.text or c.get("href") or "") for c in it}
        title, desc = _clean(f.get("title")), _clean(f.get("description") or f.get("summary"))
        link = f.get("link") or f.get("guid") or f.get("id") or title
        d = f.get("pubDate") or f.get("published") or f.get("updated") or ""
        try:
            ts = email.utils.parsedate_to_datetime(d).timestamp()
        except (TypeError, ValueError):
            try:
                from datetime import datetime
                ts = datetime.fromisoformat(d.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
        out.append({"id": link.strip()[-120:], "ts": ts, "text": (title + ". " + desc[:300]).strip()})
    return out


class NewsFeed:
    """Фоновий збір (окремий потік — основний 60-секундний цикл не чекає на повільні сайти)."""

    def __init__(self, hits=None, cfg=None, log=print):
        self.cfg = cfg or load_cfg()
        self.hits = hits or NewsHits(self.cfg.get("keep_days", 30))
        self.log = log
        self.status = {}
        self.at = 0
        self._stop = threading.Event()

    def fetch_once(self, timeout=10):
        jobs = [("tg", s) for s in self.cfg.get("telegram", [])] + [("rss", s) for s in self.cfg.get("rss", [])]

        def one(job):
            kind, s = job
            t0 = time.time()
            try:
                if kind == "tg":
                    import tg_source
                    posts = tg_source.fetch_web(s["id"], timeout=timeout)
                else:
                    posts = parse_rss(_get(s["url"], timeout))
                return s, posts, None, time.time() - t0
            except Exception as e:  # noqa: BLE001
                return s, None, f"{type(e).__name__}: {str(e)[:60]}", time.time() - t0
        n_new = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            res = list(ex.map(one, jobs))
        for s, posts, err, dt in res:
            self.status[s["id"]] = {"ok": err is None, "err": err, "n": len(posts or []), "s": round(dt, 1), "at": int(time.time())}
            for p in posts or []:
                n_new += self.hits.add_post(s["id"], p["id"], p["ts"], p["text"], s.get("weight", 0.7), s.get("oblast"))
        self.hits.prune()
        self.at = time.time()
        bad = [k for k, v in self.status.items() if not v["ok"]]
        self.log(f"news: {len(res) - len(bad)}/{len(res)} джерел, +{n_new} влучань, всього {len(self.hits.items)}"
                 + (f"; помилки: {', '.join(bad)}" if bad else ""))
        return n_new

    def ingest_channels(self, channels, cfg_by_id):
        """Пости каналів основного циклу (srv.channels): ОВА, монітори — теж повідомляють про влучання."""
        kinds = set(self.cfg.get("reuse_kinds", ["official", "monitor"]))
        n = 0
        for ch, c in list(channels.items()):
            cc = cfg_by_id.get(ch.lower())
            if not cc or cc.get("kind") not in kinds:
                continue
            w = cc.get("weight", 0.5) * (1.0 if cc.get("kind") == "official" else 0.7)
            ob = (cc.get("oblasts") or [None])[0] if cc.get("scope") != "national" else None
            for p in c.get("posts") or []:
                n += self.hits.add_post(ch, p.get("id"), p.get("ts") or 0, p.get("text", ""), w, ob)
        return n

    def run(self):
        while not self._stop.is_set():
            try:
                self.fetch_once()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            self._stop.wait(float(self.cfg.get("refresh_s", 300)))

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="news").start()

    def stop(self):
        self._stop.set()
