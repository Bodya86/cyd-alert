"""Сповіщення в Telegram-бот із live-циклу (.github/workflows/live.yml).

Рівень для «вашої» адреси (data/notify.json: city/district) береться з api/state/<city>[/<district>].json,
який щойно записав build_static.write_outputs (поле final — та сама логіка, що на ESP).

  • підвищення до помаранчевого або червоного → sendPhoto (мапа PNG) / sendMessage, текст — 5 коротких рядків
    (місце, тип, летить/не летить, ймовірність прольоту, ймовірна ціль), без цитат постів і джерел;
  • одне «живе» повідомлення на тривогу: мапа + текст редагуються кожні live_edit_s (10 с), якщо щось змінилось;
    підвищення рівня / «· ближче» → нове повідомлення (зі звуком), попереднє видаляється;
  • без повторів: той самий або нижчий рівень протягом debounce_min (10 хв) не надсилається вдруге;
  • офіційний відбій у районі (official_end у стані, ≥ official_clear_s) → «Відбій» одразу;
    без нього — повернення до зеленого (стабільно clear_after_min хв) → усі повідомлення епізоду видаляються, лишається «🟢 Відбій»;
  • секрети TG_BOT_TOKEN / TG_CHAT_ID відсутні → модуль мовчки нічого не робить.
Стан (останній надісланий рівень/час) — <out>/notify_state.json, переживає перезапуски live.yml через гілку data.
"""
import io, json, math, os, time, urllib.error, urllib.parse, urllib.request, uuid
os.environ.setdefault("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4")   # час у підписах — за Києвом (runner GitHub — UTC)
time.tzset()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "data", "notify.json")
LV = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
# як на пристрої (src/ui.cpp targetColor): дрони — фіолетові, ракети/КАБ — червоні, балістика — блакитна, інше — помаранчеве
# колір цілі за символом типу ("k"; старе "c" — запасний). Як на пристрої (src/ui.cpp targetColor): дрони —
# фіолетова гама, ракети — червона, балістика — блакитна, КАБ — світло-сірий, невідомо — помаранчевий.
TYPE_COLORS = {"S": (180, 76, 255), "G": (214, 168, 255), "J": (255, 64, 214), "L": (110, 40, 255),
               "D": (140, 150, 255), "U": (255, 140, 26),
               "C": (255, 43, 43), "N": (255, 64, 128), "X": (200, 0, 40), "K": (224, 224, 224),
               "B": (46, 230, 255), "Z": (40, 120, 255)}


def _log(*a):
    print(time.strftime("%H:%M:%S"), "[tg-notify]", *a, flush=True)


def load_cfg(path=CFG_PATH):
    cfg = {"city": "kremenchuk", "district": None, "label": "Кременчук", "min_level": "orange", "photo": True,
           "debounce_min": 10, "clear_after_min": 3, "send_clear": True}
    try:
        with open(path, encoding="utf-8") as f:
            cfg.update({k: v for k, v in json.load(f).items() if not k.startswith("_")})
    except (OSError, ValueError):
        pass
    return cfg


def state_path(out, cfg):
    if cfg.get("district"):
        return os.path.join(out, "api", "state", cfg["city"], cfg["district"] + ".json")
    return os.path.join(out, "api", "state", cfg["city"] + ".json")


# ------------------------------------------------------------------ рішення (чиста логіка, тестується)
SCOPE_RANK = {"oblast": 1, "raion": 2, "city": 3, "district": 4}
RED_KINDS = ("ballistic", "missile", "kab", "aircraft", "Балістика", "Ракети", "КАБ", "Авіація")   # «Розвідник» — ні


def decide(mem, level, now, cfg, key=None, off_end=0, official=False):
    """mem — стан (змінюється на місці). → None | ("alert", level) | ("alert+", level) | ("clear", тривалість_с).
    key — (тип загрози, scope) поточного стану. «alert+» — уточнення без підвищення рівня: загроза наблизилась
    (scope «область» → «місто») або змінився тип червоної загрози (балістика → крилаті ракети). 30.10: о 05:32 надіслано
    «балістика на область», а о 05:56–05:59 «4–5 ракет на Кременчук» вже не надсилалось (рівень той самий)."""
    minl = LV.get(cfg.get("min_level", "orange"), 2)
    # офіційний відбій у районі після початку епізоду (і рівень уже нижче порогу) — «Відбій» одразу, без clear_after_min.
    # official_clear_s (15 с) — захист від миттєвого «мерехтіння» тривоги в джерелі (зміна типу загрози тощо)
    if official:
        mem.pop("off_end", None)            # тривога (знову) діє — старий відбій не рахується
    elif off_end:
        mem["off_end"] = off_end            # пам'ятаємо відбій і після AFTER_END_MIN (якщо свіжий пост тримав рівень)
    off_end = mem.get("off_end") or 0
    if (mem.get("active") and off_end and off_end > mem.get("since", 0) and level < minl
            and now - off_end >= float(cfg.get("official_clear_s", 15))):
        dur = now - (mem.get("since") or now)
        mem.update(active=False, sent_level=0, green_since=0, since=0, sent_at=now, sent_key=None, sent_kinds=None)
        mem.pop("off_end", None)
        return ("clear", dur) if cfg.get("send_clear", True) else None
    deb = float(cfg.get("debounce_min", 10)) * 60
    clr = float(cfg.get("clear_after_min", 3)) * 60
    sent_lvl = mem.get("sent_level", 0)
    if level >= sent_lvl > 0:
        mem["high_at"] = now            # загроза не нижча за вже надіслану — подія триває
    # надісланий рівень «старіє», лише коли загроза нижча за нього вже debounce хв поспіль —
    # тоді наступне підвищення знову сповіщаємо (короткі «мерехтіння» рівня не дають повторів)
    if level < sent_lvl and now - mem.get("high_at", 0) >= deb:
        mem["sent_level"] = sent_lvl = level
        mem["sent_key"] = mem["sent_kinds"] = None
    key = list(key) if key else None
    if level >= minl:
        mem["green_since"] = 0
        if level > sent_lvl:
            mem.update(sent_level=level, sent_at=now, high_at=now, active=True, since=mem.get("since") or now, sent_key=key,
                       sent_kinds=[key[0]] if key else None)
            return ("alert", level)
        old = mem.get("sent_key")
        if key and old and level == sent_lvl and now - mem.get("sent_at", 0) >= float(cfg.get("update_min", 2)) * 60:
            closer = SCOPE_RANK.get(key[1], 0) > SCOPE_RANK.get(old[1], 0)
            kinds = mem.get("sent_kinds") or [old[0]]
            kind = level >= 3 and key[0] not in kinds and key[0] in RED_KINDS     # кожен новий тип — один раз за подію
            if closer or kind:
                mem.update(sent_at=now, high_at=now, sent_kinds=kinds + [key[0]],
                           sent_key=[key[0], max(key[1:2] + old[1:2], key=lambda x: SCOPE_RANK.get(x, 0))])
                return ("alert+", level)
        return None
    if level == 0 and mem.get("active"):
        if not mem.get("green_since"):
            mem["green_since"] = now
        if now - mem["green_since"] >= clr:
            dur = now - (mem.get("since") or now)
            mem.update(active=False, sent_level=0, green_since=0, since=0, sent_at=now, sent_key=None, sent_kinds=None)
            return ("clear", dur) if cfg.get("send_clear", True) else None
        return None
    if level > 0:
        mem["green_since"] = 0
    return None


HEAD_ICON = {2: "🟠", 3: "🔴"}
KIND_SHORT = {"Дрони": "Дрони — Шахед/БпЛА", "drone": "Дрони — Шахед/БпЛА", "missile": "Ракети", "ballistic": "Балістика",
              "kab": "КАБ", "aircraft": "Авіація", "recon": "Розвідувальний БпЛА", "Розвідник": "Розвідувальний БпЛА"}
# «летить / не летить» — за полем scope (куди прямує ціль: область / район / місто / ваш район)
SCOPE_LINE = {"district": "🔴 Летить на ваш район", "city": "➡️ Летить у бік міста",
              "raion": "↗️ У бік району, не на місто", "oblast": "⬜ Не летить на місто (загроза в області)"}
# поле zone сервера (що реально відомо): без підтвердженого курсу — не «летить на місто» і не «ризик для міста»,
# а де саме загроза (рішення 30.10). "risk" — старий код (= в області)
ZONE_LINE = {"district": "🔴 Загроза вашому району", "raion": "⚠️ Загроза в районі", "obl": "⚠️ Загроза в області",
             "risk": "⚠️ Загроза в області", "oblast": "⬜ В області"}
ZONE_HEAD = {"course": "КУРС НА МІСТО", "district": "ЗАГРОЗА В РАЙОНІ", "raion": "ЗАГРОЗА В РАЙОНІ", "obl": "ЗАГРОЗА В ОБЛАСТІ",
             "risk": "ЗАГРОЗА В ОБЛАСТІ"}


def _acc(name):
    """Знахідний відмінок однослівної назви на -а/-я (Полтава → Полтаву), як server.city_acc."""
    if " " in name or "-" in name:
        return name
    return name[:-1] + "у" if name.endswith("а") else name[:-1] + "ю" if name.endswith("я") else name


def _scope_line(st, level, place):
    z = st.get("zone")
    if z == "course":
        return "➡️ Курс на " + _acc(place) if place else "➡️ Курс на місто"
    if z == "oblast" and st.get("scope") in ("raion", "oblast") and st.get("kind") not in ("ballistic", "kab", "aircraft"):
        return SCOPE_LINE[st["scope"]]
    if z in ZONE_LINE:
        return ZONE_LINE[z]
    # старий стан без zone
    sc = st.get("scope") or {3: "district", 2: "city"}.get(level)
    if level == 2 and sc not in ("city", "district"):
        return ZONE_LINE["raion" if sc == "raion" else "obl"]
    return SCOPE_LINE.get(sc)


def _threat(st):
    """Лише тип загрози (без цитат/джерел): «Дрони — Шахед/БпЛА ×2»."""
    thr = (st.get("thr") or "").strip()
    if thr and thr != "Загроз немає":
        name = thr.split(" · ")[0].split(",")[0].strip()
    else:
        k = (st.get("kind") or "").strip()
        name = KIND_SHORT.get(k, k or "Дрони — Шахед/БпЛА")
    # ×N — лише цілі того ж класу, що в заголовку (30.10: «Балістика ×6» рахувала 1 балістичну + 5 дронів по всій мапі)
    nl = name.lower()
    codes = next((c for k, c in THREAT_CODES if k in nl), None)
    n = sum(int(t.get("n") or 1) for t in (st.get("tgt") or []) if codes and (t.get("k") or t.get("c")) in codes)
    return name + (f" ×{n}" if n > 1 else "")


THREAT_CODES = (("балісти", "BZ"), ("дрон", "SGJLD"), ("шахед", "SGJLD"), ("бпла", "SGJLD"), ("ракет", "CNX"),
                ("кр ", "CNX"), ("каб", "K"))


def alert_text(st, level, cfg, update=False):
    """Коротко, 5 рядків: місце · тип · летить/не летить · ймовірність прольоту (не при червоній) · ймовірна ціль.
    Без цитат постів, назв каналів, джерел і підпису з часом. update — уточнення («загроза ближче»)."""
    place = cfg.get("label") or cfg.get("city")
    lines = [f"{HEAD_ICON.get(level, '⚠️')} {place}" + (" · ближче" if update else ""), _threat(st)]
    sl = _scope_line(st, level, (cfg.get("city_name") or (cfg.get("label") or "").split(",")[0].strip() or None))
    if sl:
        lines.append(sl)
    pn = st.get("p_near")
    if pn is not None and level < 3:   # червона тривога: без «Проліт поруч N%» — лише в укриття
        eta = st.get("eta", -1)
        lines.append(f"Проліт поруч за 30 хв: {pn}%" + (f" (~{eta} хв)" if pn and eta is not None and eta >= 0 else ""))
    if st.get("lt") and st.get("lp"):
        lines.append(f"Ймовірна ціль: {st['lt']} — {st['lp']}%")
    return "\n".join(lines)


def clear_text(cfg, dur_s):
    place = (cfg.get("label") or cfg.get("city") or "").split(",")[0].strip()
    d = int(dur_s // 60)
    return f"🟢 Відбій · {place}" + (f"\nтривала ~{d} хв" if d > 0 else "")


# ------------------------------------------------------------------ мапа PNG
_GEO = None


def _geo():
    global _GEO
    if _GEO is None:
        try:
            with open(os.path.join(ROOT, "tools", "ukraine_adm2.geojson"), encoding="utf-8") as f:
                _GEO = json.load(f)["features"]
        except (OSError, ValueError, KeyError):
            _GEO = []
    return _GEO


def map_title(st, level, cfg):
    """Шапка мапи — лише факти (zone). «ЧИСТО» — тільки коли загроз немає зовсім (30.10: live-повідомлення,
    відредаговане на жовтому рівні, мало шапку «ЧИСТО» при «Дрони у районі ×6» у тексті)."""
    if level <= 0:
        head = "ЧИСТО"
    elif level == 1:
        head = "ЗАГРОЗА В ОБЛАСТІ"
    else:
        z = st.get("zone") or ({3: "district", 2: "course"}.get(level) if st.get("scope") in ("city", "district") else "obl")
        head = ZONE_HEAD.get(z) or "ЗАГРОЗА В ОБЛАСТІ"
    return head + " · " + (cfg.get("label") or "")


def render_map(st, ulat, ulon, level, cfg, W=800, H=600):
    """PNG (bytes) — темна мапа районів ~±170 км навколо адреси: тривоги, цілі, курс штрихом. None — немає Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    S = 2   # суперсемплінг для згладжування
    w, h = W * S, H * S
    img = Image.new("RGB", (w, h), (11, 15, 20))
    dr = ImageDraw.Draw(img)
    coslat = math.cos(math.radians(ulat))
    span_lat = 3.1
    span_lon = span_lat * (w / h) / coslat
    lat0, lon0 = ulat + span_lat / 2, ulon - span_lon / 2

    def xy(lat, lon):
        return ((lon - lon0) / span_lon * w, (lat0 - lat) / span_lat * h)

    rs = st.get("regions") or ""
    for i, f in enumerate(_geo()):
        g = f.get("geometry") or {}
        polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])]
        c = rs[i] if i < len(rs) else "."
        fill = {"R": (229, 72, 77), "r": (138, 46, 51), "Y": (212, 167, 44), "y": (124, 101, 38)}.get(c, (38, 52, 69))
        if i == st.get("region"):
            fill = tuple(min(255, int(v * 1.25) + 18) for v in fill)
        for poly in polys:
            if not poly:
                continue
            ring = poly[0]
            if not any(lat0 - span_lat - 1 < p[1] < lat0 + 1 and lon0 - 1 < p[0] < lon0 + span_lon + 1 for p in ring[::5]):
                continue
            pts = [xy(p[1], p[0]) for p in ring]
            dr.polygon(pts, fill=fill, outline=(24, 32, 42))
            dr.line(pts + [pts[0]], fill=(70, 84, 100), width=S)

    def font(sz, bold=False):
        p = os.path.join(ROOT, "tools", "fonts", "Golos-700.ttf" if bold else "Golos-500.ttf")
        try:
            return ImageFont.truetype(p, sz * S)
        except OSError:
            return ImageFont.load_default()

    # міста з locations.json
    try:
        with open(os.path.join(ROOT, "data", "locations.json"), encoding="utf-8") as f:
            cities = json.load(f)["cities"]
    except (OSError, ValueError, KeyError):
        cities = []
    f12 = font(13)
    for c in cities:
        x, y = xy(c["lat"], c["lon"])
        if c.get("id") == cfg.get("city"):
            dr.text((x + 18 * S, y - 12 * S), c["name"], font=font(16, True), fill=(255, 255, 255))
            continue
        if math.hypot(c["lat"] - ulat, (c["lon"] - ulon) * coslat) < 0.15:   # не налазити на мітку адреси
            continue
        if 0 < x < w and 0 < y < h:
            dr.ellipse([x - 3 * S, y - 3 * S, x + 3 * S, y + 3 * S], fill=(200, 210, 220))
            dr.text((x + 6 * S, y - 9 * S), c["name"], font=f12, fill=(220, 228, 236))

    def dashed(pts, col, width, on, per):
        """пунктир по ламаній (фаза переноситься між відрізками)"""
        ph = 0.0
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L < 0.5:
                continue
            ux, uy = (b[0] - a[0]) / L, (b[1] - a[1]) / L
            s0 = 0.0
            while s0 < L:
                m = (ph + s0) % per
                seg = min((on - m) if m < on else (per - m), L - s0)
                if m < on:
                    dr.line([(a[0] + ux * s0, a[1] + uy * s0), (a[0] + ux * (s0 + seg), a[1] + uy * (s0 + seg))], fill=col, width=width)
                s0 += max(seg, 0.5)
            ph = (ph + L) % per

    # цілі: пройдений шлях — дрібний пунктир кольором цілі; курс прогнозу (5/15/30 хв) — білий пунктир; обидва на темній підкладці
    for t in st.get("tgt") or []:
        col = TYPE_COLORS.get(t.get("k") or t.get("c"), (255, 140, 26))
        x, y = xy(t["la"], t["lo"])
        tr = [xy(p[0], p[1]) for p in (t.get("tr") or [])] + [(x, y)]
        if len(tr) > 1:
            dashed(tr, (11, 15, 20), 5 * S, 5 * S, 11 * S)
            dashed(tr, col, 3 * S, 5 * S, 11 * S)
        pts = [(x, y)] + [xy(p[0], p[1]) for p in (t.get("pt") or [])]
        if len(pts) > 1:
            dashed(pts, (11, 15, 20), 5 * S, 10 * S, 17 * S)
            dashed(pts, (255, 255, 255), 2 * S, 10 * S, 17 * S)
        r = 8 * S
        dr.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=(11, 15, 20), width=2 * S)
        hd = t.get("hd", -1)
        if hd is not None and hd >= 0:
            a = math.radians(hd)
            ux, uy = math.sin(a), -math.cos(a)
            tip = (x + ux * r * 2.4, y + uy * r * 2.4)
            bx, by = x + ux * r * 1.25, y + uy * r * 1.25
            l = (bx - uy * r * 0.75, by + ux * r * 0.75)
            rr = (bx + uy * r * 0.75, by - ux * r * 0.75)
            dr.polygon([tip, l, rr], fill=col, outline=(11, 15, 20))
        if (t.get("n") or 1) > 1:
            dr.text((x + r + 2 * S, y - r), f"×{t['n']}", font=font(13, True), fill=col)

    # адреса користувача
    x, y = xy(ulat, ulon)
    for rad, c in ((14, (255, 255, 255)), (9, (59, 130, 246))):
        dr.ellipse([x - rad * S, y - rad * S, x + rad * S, y + rad * S], outline=c, width=3 * S)
    dr.ellipse([x - 4 * S, y - 4 * S, x + 4 * S, y + 4 * S], fill=(255, 255, 255))

    # шапка
    bar = {1: (250, 204, 21), 2: (255, 140, 26), 3: (229, 72, 77)}.get(level, (46, 160, 67))
    dr.rectangle([0, 0, w, 44 * S], fill=bar)
    title = map_title(st, level, cfg)
    dr.text((14 * S, 8 * S), title, font=font(22, True), fill=(11, 15, 20))
    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------ Bot API
class TgError(Exception):
    def __init__(self, code, desc, retry_after=0):
        super().__init__(f"{code} {desc}")
        self.code, self.desc, self.retry_after = code, desc or "", retry_after or 0


def _call(token, method, fields, files=None, timeout=25):
    """Bot API → result; помилка → TgError (код, опис, retry_after). Токен у винятках/логах не з'являється."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        bnd = uuid.uuid4().hex
        body = io.BytesIO()
        for k, v in fields.items():
            body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode("utf-8"))
        for k, (fn, data, ctype) in files.items():
            body.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\n"
                       f"Content-Type: {ctype}\r\n\r\n".encode("utf-8"))
            body.write(data)
            body.write(b"\r\n")
        body.write(f"--{bnd}--\r\n".encode())
        req = urllib.request.Request(url, data=body.getvalue(), headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode("utf-8"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise TgError(e.code, "http error") from None
    except Exception as e:  # noqa: BLE001 — URL із токеном не друкуємо
        raise TgError(0, type(e).__name__) from None
    if not d.get("ok"):
        raise TgError(d.get("error_code", 0), d.get("description", ""), (d.get("parameters") or {}).get("retry_after", 0))
    return d.get("result")


def _post(token, method, fields, files=None, timeout=25):   # сумісність (tg_test.py)
    try:
        return _call(token, method, fields, files, timeout) is not None
    except TgError:
        return False


def live_sig(st, level, text):
    """Підпис вмісту live-повідомлення: однаковий → нічого не редагуємо. Мапа — лише від рівня, областей і цілей."""
    tg = [(t.get("k") or t.get("c"), round(t.get("la", 0), 3), round(t.get("lo", 0), 3), t.get("hd"), t.get("n"),
           [tuple(round(x, 3) for x in p[:2]) for p in (t.get("pt") or [])],
           [tuple(round(x, 3) for x in p[:2]) for p in (t.get("tr") or [])]) for t in (st.get("tgt") or [])]
    m = json.dumps([level, st.get("regions"), st.get("region"), tg], ensure_ascii=False, default=str)
    return {"map": uuid.uuid5(uuid.NAMESPACE_OID, m).hex, "text": text}


class Notifier:
    """Одне «живе» повідомлення на тривогу: мапа + 5 рядків, редагується кожні live_edit_s (10 с), якщо щось змінилось.
    Підвищення рівня / «ближче» → нове повідомлення зі звуком, попереднє видаляється (завжди одне).
    Відбій → видаляються всі повідомлення епізоду (id у notify_state.json, переживають перезапуск), лишається «🟢 Відбій».
    threaded=True (rt_server): tick() лише передає стан фоновому потоку — мережа/рендер не гальмують цикл."""

    def __init__(self, out, cfg=None, token=None, chat=None, sender=None, api=None, threaded=False):
        self.out = out
        self.cfg = cfg or load_cfg()
        self.token = token if token is not None else os.environ.get("TG_BOT_TOKEN", "").strip()
        self.chat = chat if chat is not None else os.environ.get("TG_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat)
        self.sender = sender          # старий режим (лише надсилання) — для тестів/сумісності
        self.api = api or (lambda m, f, files=None: _call(self.token, m, f, files))
        self.mem_path = os.path.join(out, "notify_state.json")
        self.hold_until = 0.0         # 429: не чіпати API до цього часу
        try:
            with open(self.mem_path, encoding="utf-8") as f:
                self.mem = json.load(f)
        except (OSError, ValueError):
            self.mem = {}
        self._th = None
        if threaded and self.enabled:
            import threading
            self._lock = threading.Lock()
            self._ev = threading.Event()
            self._pending = None
            self._th = threading.Thread(target=self._worker, name="tg-notify", daemon=True)
            self._th.start()

    def _save(self):
        try:
            tmp = self.mem_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.mem, f)
            os.replace(tmp, self.mem_path)
        except OSError:
            pass

    # ---- низькорівневі виклики з обробкою 429 / «not modified» / «not found»
    def _api(self, method, fields, files=None):
        if time.time() < self.hold_until:
            raise TgError(429, "hold", self.hold_until - time.time())
        try:
            return self.api(method, dict(fields, chat_id=self.chat), files)
        except TgError as e:
            if e.code == 429:
                self.hold_until = time.time() + max(1, e.retry_after) + 1
                _log(f"{method}: 429, пауза {e.retry_after} с")
            raise

    def _png(self, st, level):
        if not self.cfg.get("photo", True):
            return None
        try:
            import server as srv
            ulat, ulon, _ = srv.user_point({"city": self.cfg["city"], "district": self.cfg.get("district") or ""})
            if ulat is not None:
                return render_map(st, ulat, ulon, level, self.cfg)
        except Exception as e:  # noqa: BLE001
            _log("мапа:", e)
        return None

    def _send_new(self, text, png, silent=False):
        """→ message_id | None"""
        f = {"disable_notification": "true"} if silent else {}
        try:
            if png:
                r = self._api("sendPhoto", dict(f, caption=text[:1024]), {"photo": ("map.png", png, "image/png")})
                return (r or {}).get("message_id"), "photo"
            r = self._api("sendMessage", dict(f, text=text[:4000], disable_web_page_preview="true"))
            return (r or {}).get("message_id"), "text"
        except TgError as e:
            _log("надсилання:", e.code, e.desc[:80])
            return None, None

    def _delete(self, mid):
        """→ True — повідомлення вже немає (видалено / не знайдено / старше 48 год); False — спробувати пізніше.
        30.10: мережеві збої (TgError 0 / 5xx) вважались успіхом — id випадав з епізоду, а повідомлення лишалось сиротою."""
        try:
            self._api("deleteMessage", {"message_id": mid})
            return True
        except TgError as e:
            return e.code in (400, 403) and e.code != 429

    def _edit(self, st, level, text, sig, now=None):
        """Оновити live-повідомлення. → True (оновлено/без змін) | False (спробувати пізніше) | None (зникло)."""
        lv = self.mem.get("live") or {}
        mid, kind, old = lv.get("id"), lv.get("kind"), lv.get("sig") or {}
        try:
            if kind == "photo" and sig["map"] != old.get("map"):
                png = self._png(st, level)
                if png:
                    media = json.dumps({"type": "photo", "media": "attach://map", "caption": text[:1024]}, ensure_ascii=False)
                    self._api("editMessageMedia", {"message_id": mid, "media": media}, {"map": ("map.png", png, "image/png")})
                else:
                    self._api("editMessageCaption", {"message_id": mid, "caption": text[:1024]})
            elif kind == "photo":
                self._api("editMessageCaption", {"message_id": mid, "caption": text[:1024]})
            else:
                self._api("editMessageText", {"message_id": mid, "text": text[:4000], "disable_web_page_preview": "true"})
        except TgError as e:
            d = e.desc.lower()
            if "not modified" in d:
                pass
            elif "not found" in d or "can't be edited" in d or "message_id_invalid" in d:
                return None
            else:
                if e.code != 429:
                    _log("редагування:", e.code, e.desc[:80])
                return False
        lv.update(sig=sig, at=now or time.time())
        self.mem["live"] = lv
        return True

    # ---- логіка
    def _process(self, st, now):
        level = LV.get(st.get("final"), 0)
        act = decide(self.mem, level, now, self.cfg, key=(st.get("kind") or "", st.get("scope") or ""),
                     off_end=st.get("official_end") or 0, official=bool(st.get("official")))
        res = None
        if self.sender:   # старий режим: лише надсилати
            if act and act[0] in ("alert", "alert+"):
                ok = self.sender(alert_text(st, level, self.cfg, update=act[0] == "alert+"), self._png(st, level))
                _log(f"{act[0]} {level}: {'надіслано' if ok else 'НЕ надіслано'}")
                res = "alert"
            elif act and act[0] == "clear":
                self.sender(clear_text(self.cfg, act[1]), None)
                res = "clear"
            self.mem["level"] = level
            self._save()
            return res
        ep = self.mem.setdefault("episode", [])
        if act and act[0] in ("alert", "alert+"):
            upd = act[0] == "alert+"
            text = alert_text(st, level, self.cfg, update=upd)
            mid, kind = self._send_new(text, self._png(st, level))
            _log(f"{act[0]} {level}: {'надіслано' if mid else 'НЕ надіслано'}")
            if mid:
                prev = (self.mem.get("live") or {}).get("id")
                ep.append(mid)
                self.mem["live"] = {"id": mid, "kind": kind, "upd": upd, "level": level,
                                    "sig": live_sig(st, level, text), "at": now}
                if prev and self._delete(prev) and prev in ep:
                    ep.remove(prev)
            res = "alert"
        elif act and act[0] == "clear":
            left = []
            for mid in list(ep):
                if not self._delete(mid):
                    left.append(mid)
            self.mem["live"] = None
            r, _ = self._send_new(clear_text(self.cfg, act[1]), None)
            # попередні «Відбій» теж прибираємо: у чаті лишається один — останній (30.10)
            left += [m for m in self.mem.get("clears", []) if not self._delete(m)]
            self.mem["episode"] = left
            self.mem["clears"] = [r] if r else []
            _log(f"відбій: {'надіслано' if r else 'НЕ надіслано'}; видалено {len(ep) - len([m for m in left if m in ep])}, "
                 f"лишилось {len(left)}")
            res = "clear"
        elif self.mem.get("active") and (self.mem.get("live") or {}).get("id") and level >= 1:
            lv = self.mem["live"]
            if now - lv.get("at", 0) >= float(self.cfg.get("live_edit_s", 10)):
                text = alert_text(st, level, self.cfg, update=lv.get("upd", False))
                sig = live_sig(st, level, text)
                if sig != lv.get("sig"):
                    r = self._edit(st, level, text, sig, now)
                    if r is None:   # повідомлення видалили вручну / не редагується — нове, без звуку (старе дочистить gc)
                        mid, kind = self._send_new(text, self._png(st, level), silent=True)
                        if mid:
                            ep.append(mid)
                            self.mem["live"] = dict(lv, id=mid, kind=kind, sig=sig, at=now)
                    elif r:
                        res = "edit"
                else:
                    lv["at"] = now
        live_id = (self.mem.get("live") or {}).get("id")
        stale = [m for m in ep if m != live_id]
        if stale and act is None and now - self.mem.get("gc_at", 0) >= 30:
            # хвости (429 / мережевий збій при відбої чи заміні live) — дочищаємо, і під час тривоги теж:
            # у чаті завжди одне повідомлення тривоги (30.10: лишалось старе з шапкою «ЧИСТО»)
            self.mem["gc_at"] = now
            gone = [m for m in stale if self._delete(m)]
            self.mem["episode"] = [m for m in ep if m not in gone]
        self.mem["level"] = level
        self._save()
        return res

    def _load_state(self):
        try:
            with open(state_path(self.out, self.cfg), encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def _worker(self):
        while True:
            self._ev.wait(15)
            self._ev.clear()
            with self._lock:
                st, now = self._pending or (None, None)
                self._pending = None
            if st is None:
                st, now = self._load_state(), time.time()
            if st is None:
                continue
            try:
                self._process(st, now or time.time())
            except Exception as e:  # noqa: BLE001
                _log("помилка:", type(e).__name__, str(e)[:120])
            time.sleep(1)

    def tick(self, now=None, st=None):
        """Викликати після write_outputs. → "alert"/"clear"/"edit"/None (у threaded-режимі — завжди None)."""
        if not self.enabled:
            return None
        if self._th:
            with self._lock:
                self._pending = (st, now) if st is not None else None
            self._ev.set()
            return None
        now = now or time.time()
        if st is None:
            st = self._load_state()
            if st is None:
                return None
        return self._process(st, now)
