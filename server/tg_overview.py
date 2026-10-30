"""Постійне повідомлення-мапа всієї України в Telegram-чаті бота (30.10).

  • одне повідомлення (фото без підпису): тривоги по районах (поле regions з api/state.json) + усі цілі
    з api/targets.json (пройдений шлях — пунктир кольором цілі, прогноз — білий пунктир), без тексту на мапі;
  • editMessageMedia не частіше edit_s (5 с) і лише коли змінилась картинка (підпис вхідних даних);
  • «message is not modified» — норма; 429 → пауза retry_after; повідомлення зникло → нове (і закріплюється);
  • id — в <out>/overview_state.json (переживає перезапуск). Логіка тривог/видалень (tg_notify.Notifier) його
    не знає і не чіпає: окремий стан, окремий файл.
"""
import io, json, math, os, threading, time, uuid

import tg_notify as tn

UA_BOX = (44.25, 52.45, 22.0, 40.35)          # lat_min, lat_max, lon_min, lon_max
FILL = {"R": (229, 72, 77), "r": (138, 46, 51), "Y": (212, 167, 44), "y": (124, 101, 38)}
BG, LAND, EDGE = (11, 15, 20), (38, 52, 69), (70, 84, 100)


def _log(*a):
    print(time.strftime("%H:%M:%S"), "[tg-map]", *a, flush=True)


def _tlist(targets):
    """api/targets.json → [(тип, lat, lon, heading, trail[(lat,lon)], path[(lat,lon)])]"""
    out = []
    for t in (targets or {}).get("targets") or []:
        try:
            out.append((t.get("lc") or t.get("ch") or "U", float(t["lat"]), float(t["lon"]), t.get("heading"),
                        [(p[0], p[1]) for p in (t.get("trail") or [])], [(p[0], p[1]) for p in (t.get("path") or [])]))
        except (KeyError, TypeError, ValueError, IndexError):
            continue
    return out


def overview_sig(state, targets):
    """Картинка залежить лише від regions і цілей (округлено ~1 км / 5°) → однаковий підпис = та сама картинка."""
    r2 = lambda v: round(v, 2)   # noqa: E731
    tg = sorted((k, r2(la), r2(lo), (round(hd / 5) * 5) if isinstance(hd, (int, float)) and hd >= 0 else -1,
                 [(r2(a), r2(b)) for a, b in tr], [(r2(a), r2(b)) for a, b in pt]) for k, la, lo, hd, tr, pt in _tlist(targets))
    m = json.dumps([(state or {}).get("regions") or "", tg], ensure_ascii=False)
    return uuid.uuid5(uuid.NAMESPACE_OID, m).hex


def render_overview(state, targets, W=1000, H=680):
    """PNG (bytes) усієї України без жодного тексту. None — немає Pillow."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None
    S = 2
    w, h = W * S, H * S
    img = Image.new("RGB", (w, h), BG)
    dr = ImageDraw.Draw(img)
    la0, la1, lo0, lo1 = UA_BOX
    k = math.cos(math.radians((la0 + la1) / 2))
    # вписати бокс зі збереженням пропорцій (екваріальна проєкція з cos φ)
    sx = w / ((lo1 - lo0) * k)
    sy = h / (la1 - la0)
    sc = min(sx, sy)
    ox = (w - (lo1 - lo0) * k * sc) / 2
    oy = (h - (la1 - la0) * sc) / 2

    def xy(lat, lon):
        return (ox + (lon - lo0) * k * sc, oy + (la1 - lat) * sc)

    rs = (state or {}).get("regions") or ""
    for i, f in enumerate(tn._geo()):
        g = f.get("geometry") or {}
        polys = g.get("coordinates", []) if g.get("type") == "MultiPolygon" else [g.get("coordinates", [])]
        fill = FILL.get(rs[i] if i < len(rs) else ".", LAND)
        for poly in polys:
            if poly:
                pts = [xy(p[1], p[0]) for p in poly[0]]
                dr.polygon(pts, fill=fill, outline=BG)
                dr.line(pts + [pts[0]], fill=EDGE, width=1)

    def dashed(pts, col, width, on, per):
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
                    dr.line([(a[0] + ux * s0, a[1] + uy * s0), (a[0] + ux * (s0 + seg), a[1] + uy * (s0 + seg))],
                            fill=col, width=width)
                s0 += max(seg, 0.5)
            ph = (ph + L) % per

    tl = _tlist(targets)
    for kind, la, lo, hd, tr, pt in tl:           # спершу всі лінії, потім маркери (маркери зверху)
        col = tn.TYPE_COLORS.get(kind, (255, 140, 26))
        x, y = xy(la, lo)
        trail = [xy(a, b) for a, b in tr] + [(x, y)]
        if len(trail) > 1:
            dashed(trail, BG, 4 * S, 4 * S, 9 * S)
            dashed(trail, col, 2 * S, 4 * S, 9 * S)
        path = [(x, y)] + [xy(a, b) for a, b in pt]
        if len(path) > 1:
            dashed(path, BG, 4 * S, 7 * S, 12 * S)
            dashed(path, (255, 255, 255), 2 * S, 7 * S, 12 * S)
    for kind, la, lo, hd, tr, pt in tl:
        col = tn.TYPE_COLORS.get(kind, (255, 140, 26))
        x, y = xy(la, lo)
        r = 6 * S
        dr.ellipse([x - r, y - r, x + r, y + r], fill=col, outline=BG, width=2 * S)
        if isinstance(hd, (int, float)) and hd >= 0:
            a = math.radians(hd)
            ux, uy = math.sin(a), -math.cos(a)
            tip = (x + ux * r * 2.3, y + uy * r * 2.3)
            bx, by = x + ux * r * 1.2, y + uy * r * 1.2
            dr.polygon([tip, (bx - uy * r * 0.7, by + ux * r * 0.7), (bx + uy * r * 0.7, by - ux * r * 0.7)],
                       fill=col, outline=BG)
    img = img.resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


class OverviewMap:
    """Одне постійне фото-повідомлення з мапою України; step() — одна ітерація (тестується з фейковим api)."""

    def __init__(self, out, token=None, chat=None, api=None, edit_s=5.0, render=render_overview):
        self.out = out
        self.token = token if token is not None else os.environ.get("TG_BOT_TOKEN", "").strip()
        self.chat = chat if chat is not None else os.environ.get("TG_CHAT_ID", "").strip()
        self.enabled = bool(self.token and self.chat)
        self.api = api or (lambda m, f, files=None: tn._call(self.token, m, f, files))
        self.edit_s = float(edit_s)
        self.render = render
        self.path = os.path.join(out, "overview_state.json")
        self.hold_until = 0.0
        self.last_try = 0.0
        try:
            with open(self.path, encoding="utf-8") as f:
                self.mem = json.load(f)
        except (OSError, ValueError):
            self.mem = {}
        self._th = None

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.mem, f)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _read(self):
        def j(p):
            try:
                with open(os.path.join(self.out, "api", p), encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, ValueError):
                return {}
        return j("state.json"), j("targets.json")

    def _call(self, method, fields, files=None):
        try:
            return self.api(method, dict(fields, chat_id=self.chat), files)
        except tn.TgError as e:
            if e.code == 429:
                self.hold_until = time.time() + max(1, e.retry_after or 0) + 1
                _log(f"{method}: 429, пауза {e.retry_after} с")
            raise

    def step(self, now=None, state=None, targets=None):
        """→ 'sent' | 'edited' | 'same' | 'hold' | 'wait' | 'error' | 'off'"""
        if not self.enabled:
            return "off"
        now = time.time() if now is None else now
        if now < self.hold_until:
            return "hold"
        if now - self.last_try < self.edit_s:
            return "wait"
        if state is None and targets is None:
            state, targets = self._read()
        sig = overview_sig(state, targets)
        mid = self.mem.get("id")
        if mid and sig == self.mem.get("sig"):
            return "same"
        self.last_try = now
        png = self.render(state, targets)
        if not png:
            return "error"
        try:
            if mid:
                try:
                    self._call("editMessageMedia", {"message_id": mid,
                                                    "media": json.dumps({"type": "photo", "media": "attach://map"})},
                               {"map": ("map.png", png, "image/png")})
                    self.mem["sig"] = sig
                    self._save()
                    return "edited"
                except tn.TgError as e:
                    d = e.desc.lower()
                    if "not modified" in d:
                        self.mem["sig"] = sig
                        self._save()
                        return "same"
                    if e.code == 400 and ("not found" in d or "message_id_invalid" in d or "can't be edited" in d):
                        _log("повідомлення-мапа зникло — надсилаю нове")
                        mid = None
                    else:
                        raise
            r = self._call("sendPhoto", {"disable_notification": "true"}, {"photo": ("map.png", png, "image/png")})
            mid = (r or {}).get("message_id")
            if not mid:
                return "error"
            self.mem.update(id=mid, sig=sig, pinned=False)
            self._save()
            _log(f"надіслано мапу України: id {mid}")
            try:
                self._call("pinChatMessage", {"message_id": mid, "disable_notification": "true"})
                self.mem["pinned"] = True
                self._save()
            except tn.TgError as e:
                _log("закріплення:", e.code, e.desc[:80])
            return "sent"
        except tn.TgError as e:
            if e.code != 429:
                _log("мапа:", e.code, e.desc[:80])
            return "error"

    def start(self):
        """Фоновий потік: step() кожну секунду (сам обмежує частоту edit_s)."""
        if not self.enabled or self._th:
            return

        def loop():
            while True:
                try:
                    self.step()
                except Exception as e:  # noqa: BLE001
                    _log("помилка:", type(e).__name__, str(e)[:120])
                time.sleep(1.0)
        self._th = threading.Thread(target=loop, name="tg-map", daemon=True)
        self._th.start()

    def own_ids(self):
        return {self.mem["id"]} if self.mem.get("id") else set()
