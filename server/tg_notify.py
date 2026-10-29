"""Сповіщення в Telegram-бот із live-циклу (.github/workflows/live.yml).

Рівень для «вашої» адреси (data/notify.json: city/district) береться з api/state/<city>[/<district>].json,
який щойно записав build_static.write_outputs (поле final — та сама логіка, що на ESP).

  • підвищення до помаранчевого («КУРС НА МІСТО») або червоного («НЕБЕЗПЕКА») → sendPhoto (мапа PNG) / sendMessage;
  • без повторів: той самий або нижчий рівень протягом debounce_min (10 хв) не надсилається вдруге;
  • повернення до зеленого (стабільно clear_after_min хв) після тривожного повідомлення → «🟢 Відбій»;
  • секрети TG_BOT_TOKEN / TG_CHAT_ID відсутні → модуль мовчки нічого не робить.
Стан (останній надісланий рівень/час) — <out>/notify_state.json, переживає перезапуски live.yml через гілку data.
"""
import io, json, math, os, time, urllib.parse, urllib.request, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(ROOT, "data", "notify.json")
LV = {"green": 0, "yellow": 1, "orange": 2, "red": 3}
HEAD = {2: "🟠 КУРС НА МІСТО", 3: "🔴 НЕБЕЗПЕКА"}
TYPE_NAMES = {"S": "шахед", "J": "реактивний шахед", "D": "розвідник", "U": "ціль", "C": "крилата ракета", "K": "КАБ", "B": "балістика"}
TYPE_COLORS = {"S": (242, 201, 76), "J": (255, 140, 26), "D": (180, 190, 200), "U": (242, 201, 76),
               "C": (229, 72, 77), "K": (229, 72, 77), "B": (229, 72, 77)}


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
def decide(mem, level, now, cfg):
    """mem — стан (змінюється на місці). → None | ("alert", level) | ("clear", тривалість_с)."""
    minl = LV.get(cfg.get("min_level", "orange"), 2)
    deb = float(cfg.get("debounce_min", 10)) * 60
    clr = float(cfg.get("clear_after_min", 3)) * 60
    sent_lvl = mem.get("sent_level", 0)
    if level >= sent_lvl > 0:
        mem["high_at"] = now            # загроза не нижча за вже надіслану — подія триває
    # надісланий рівень «старіє», лише коли загроза нижча за нього вже debounce хв поспіль —
    # тоді наступне підвищення знову сповіщаємо (короткі «мерехтіння» рівня не дають повторів)
    if level < sent_lvl and now - mem.get("high_at", 0) >= deb:
        mem["sent_level"] = sent_lvl = level
    if level >= minl:
        mem["green_since"] = 0
        if level > sent_lvl:
            mem.update(sent_level=level, sent_at=now, high_at=now, active=True, since=mem.get("since") or now)
            return ("alert", level)
        return None
    if level == 0 and mem.get("active"):
        if not mem.get("green_since"):
            mem["green_since"] = now
        if now - mem["green_since"] >= clr:
            dur = now - (mem.get("since") or now)
            mem.update(active=False, sent_level=0, green_since=0, since=0, sent_at=now)
            return ("clear", dur) if cfg.get("send_clear", True) else None
        return None
    if level > 0:
        mem["green_since"] = 0
    return None


def alert_text(st, level, cfg):
    place = cfg.get("label") or cfg.get("city")
    lines = [f"{HEAD.get(level, '⚠️ ЗАГРОЗА')} · {place}"]
    kind, reason = (st.get("kind") or "").strip(), (st.get("reason") or "").strip()
    if kind or reason:
        lines.append(" — ".join(x for x in (kind, reason) if x))
    if st.get("p_near"):
        eta = st.get("eta", -1)
        lines.append(f"Ймовірність прольоту поруч за 30 хв: {st['p_near']}%" + (f", ~{eta} хв" if eta is not None and eta >= 0 else ""))
    if st.get("lt") and st.get("lp"):
        lines.append(f"Ймовірна ціль (історія маршрутів): {st['lt']} — {st['lp']}%")
    tg = st.get("tgt") or []
    if tg:
        cnt = {}
        for t in tg:
            nm = TYPE_NAMES.get(t.get("c"), "ціль")
            cnt[nm] = cnt.get(nm, 0) + int(t.get("n") or 1)
        lines.append("На мапі: " + ", ".join(f"{k} ×{v}" for k, v in cnt.items()))
    if st.get("official"):
        lines.append("Офіційна тривога в районі: так" + (" (частина громад)" if st.get("official_partial") else ""))
    txt = (st.get("text") or "").strip().replace("\n", " ")
    if txt:
        if len(txt) > 260:
            txt = txt[:257] + "…"
        ch = st.get("channel") or ""
        agree = st.get("agree") or 0
        lines.append(f"«{txt}»" + (f" — @{ch}" if ch else "") + (f" (+{agree - 1} кан.)" if agree and agree > 1 else ""))
    lines.append(time.strftime("%H:%M") + " · Дані: JAAM (jaam.net.ua), Telegram-канали · CYD Alert")
    return "\n".join(lines)


def clear_text(cfg, dur_s):
    place = cfg.get("label") or cfg.get("city")
    d = int(dur_s // 60)
    return f"🟢 Відбій · {place}\nЗагрозу для вашої адреси знято" + (f" (тривала ~{d} хв)" if d > 0 else "") + \
        "\n" + time.strftime("%H:%M") + " · CYD Alert"


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

    # цілі: курс штрихом через точки прогнозу (5/15/30 хв), іконка, кількість
    for t in st.get("tgt") or []:
        col = TYPE_COLORS.get(t.get("c"), (242, 201, 76))
        x, y = xy(t["la"], t["lo"])
        pts = [(x, y)] + [xy(p[0], p[1]) for p in (t.get("pt") or [])]
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(L / (10 * S)))
            for k in range(0, n, 2):
                p1 = (a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n)
                p2 = (a[0] + (b[0] - a[0]) * min(n, k + 1) / n, a[1] + (b[1] - a[1]) * min(n, k + 1) / n)
                dr.line([p1, p2], fill=col, width=2 * S)
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
    bar = {2: (255, 140, 26), 3: (229, 72, 77)}.get(level, (46, 160, 67))
    dr.rectangle([0, 0, w, 44 * S], fill=bar)
    title = {2: "КУРС НА МІСТО", 3: "НЕБЕЗПЕКА"}.get(level, "ЧИСТО") + " · " + (cfg.get("label") or "")
    dr.text((14 * S, 8 * S), title, font=font(22, True), fill=(11, 15, 20))
    dr.text((14 * S, h - 24 * S), time.strftime("%d.%m %H:%M") + " · Дані: JAAM (jaam.net.ua), Telegram-канали",
            font=font(12), fill=(139, 152, 165))
    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


# ------------------------------------------------------------------ Bot API
def _post(token, method, fields, files=None, timeout=25):
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
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("ok", False)


class Notifier:
    def __init__(self, out, cfg=None, token=None, chat=None, sender=None):
        self.out = out
        self.cfg = cfg or load_cfg()
        self.token = token if token is not None else os.environ.get("TG_BOT_TOKEN", "").strip()
        self.chat = chat if chat is not None else os.environ.get("TG_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat)
        self.sender = sender or self._send
        self.mem_path = os.path.join(out, "notify_state.json")
        try:
            with open(self.mem_path, encoding="utf-8") as f:
                self.mem = json.load(f)
        except (OSError, ValueError):
            self.mem = {}

    def _save(self):
        try:
            with open(self.mem_path, "w", encoding="utf-8") as f:
                json.dump(self.mem, f)
        except OSError:
            pass

    def _send(self, text, png=None):
        try:
            if png:
                ok = _post(self.token, "sendPhoto", {"chat_id": self.chat, "caption": text[:1024]},
                           {"photo": ("map.png", png, "image/png")})
                if ok:
                    return True
            return _post(self.token, "sendMessage", {"chat_id": self.chat, "text": text[:4000], "disable_web_page_preview": "true"})
        except Exception as e:  # noqa: BLE001 — токен у повідомленні не друкуємо
            _log("помилка надсилання:", type(e).__name__, str(getattr(e, "code", "")))
            return False

    def tick(self, now=None, st=None):
        """Викликати після write_outputs. → "alert"/"clear"/None."""
        if not self.enabled:
            return None
        now = now or time.time()
        if st is None:
            try:
                with open(state_path(self.out, self.cfg), encoding="utf-8") as f:
                    st = json.load(f)
            except (OSError, ValueError):
                return None
        level = LV.get(st.get("final"), 0)
        act = decide(self.mem, level, now, self.cfg)
        res = None
        if act and act[0] == "alert":
            png = None
            if self.cfg.get("photo", True):
                try:
                    import server as srv
                    ulat, ulon, _ = srv.user_point({"city": self.cfg["city"], "district": self.cfg.get("district") or ""})
                    if ulat is not None:
                        png = render_map(st, ulat, ulon, level, self.cfg)
                except Exception as e:  # noqa: BLE001
                    _log("мапа:", e)
            ok = self.sender(alert_text(st, level, self.cfg), png)
            _log(f"alert {level}: {'надіслано' if ok else 'НЕ надіслано'}")
            res = "alert"
        elif act and act[0] == "clear":
            ok = self.sender(clear_text(self.cfg, act[1]), None)
            _log(f"відбій: {'надіслано' if ok else 'НЕ надіслано'}")
            res = "clear"
        self.mem["level"] = level
        self._save()
        return res
