"""Історичне апріорі (data/history_model.json, будує tools/history_model.py).

• dir_prob / sample_heading — поле переходів «куди зазвичай летять дрони з цієї комірки 0.25°»
  (8 напрямків), використовується для початкових курсів частинок, коли курс невідомий;
• p_target — P(ціль атаки = місто | ціль зараз тут, курс), та сама формула, що в tools/history_model.py;
• heat_window — компактне вікно «типових маршрутів» навколо області для прошивки (base36).
Якщо файлу немає — модуль повертає None і трекер працює як раніше."""
import json
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, "data", "history_model.json")
KM_LAT = 111.2
B36 = "0123456789abcdefghijklmnopqrstuvwxyz"


def _km(a, b, c, d):
    return math.hypot((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * KM_LAT)


def _bearing(a, b, c, d):
    return math.degrees(math.atan2((d - b) * 111.32 * math.cos(math.radians((a + c) / 2)), (c - a) * KM_LAT)) % 360


class History:
    def __init__(self, d):
        g = d["grid"]
        self.lat0, self.lon0, self.step, self.rows, self.cols = g["lat0"], g["lon0"], g["step"], g["rows"], g["cols"]
        self.dir = {int(k): v for k, v in d.get("dir", {}).items()}
        self.heat = {int(k): v for k, v in d.get("heat", {}).items()}
        self.nodes = d.get("targets", [])
        self.pairs = {int(k): {i: n for i, n in v} for k, v in d.get("pairs", {}).items()}
        p = d.get("params") or {}
        self.base, self.gamma, self.lam = p.get("base", 0.15), p.get("gamma", 0.7), p.get("lam", 180.0)
        self.stats = d.get("stats", {})
        self.hmax = max(self.heat.values(), default=1.0)

    @classmethod
    def load(cls, path=DEFAULT_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f))
        except (OSError, ValueError, KeyError):
            return None

    def cell(self, lat, lon):
        r, c = int((self.lat0 - lat) / self.step), int((lon - self.lon0) / self.step)
        return r * self.cols + c if 0 <= r < self.rows and 0 <= c < self.cols else None

    def dir_prob(self, lat, lon, alpha=0.5, min_n=1.5):
        v = self.dir.get(self.cell(lat, lon))
        if not v or sum(v) < min_n:
            return None
        s = sum(v) + alpha * 8
        return [(x + alpha) / s for x in v]

    def sample_heading(self, lat, lon, rnd, pf=None):
        """курс (рад, 0 = північ) з поля; None — якщо комірка без даних"""
        pf = pf or self.dir_prob(lat, lon)
        if not pf:
            return None
        u, acc = rnd.random(), 0.0
        for k, p in enumerate(pf):
            acc += p
            if u <= acc:
                break
        return math.radians(k * 45 + rnd.gauss(0, 17)) % (2 * math.pi)

    def p_target(self, lat, lon, hd=None, maxkm=350):
        c = self.cell(lat, lon)
        w = {}
        if c is not None:
            r, q = divmod(c, self.cols)
            for dr in (-1, 0, 1):
                for dq in (-1, 0, 1):
                    for k, n in self.pairs.get((r + dr) * self.cols + q + dq, {}).items():
                        w[k] = w.get(k, 0) + n * (1.0 if dr == dq == 0 else 0.4)
        out = {}
        for k, n in enumerate(self.nodes):
            d = _km(lat, lon, n[1], n[2])
            if d > maxkm or d < 3:
                continue
            v = (1 + n[3] + n[4]) ** self.gamma * math.exp(-d / self.lam) * self.base + w.get(k, 0)
            if hd is not None:
                v *= math.exp(2.0 * (math.cos(math.radians(_bearing(lat, lon, n[1], n[2]) - hd)) - 1))
            out[k] = v
        s = sum(out.values()) or 1
        return {k: v / s for k, v in out.items()}

    def node_near(self, lat, lon, km=15):
        best = None
        for k, n in enumerate(self.nodes):
            d = _km(lat, lon, n[1], n[2])
            if d <= km and (best is None or d < best[0]):
                best = (d, k)
        return best[1] if best else None

    def likely_target(self, lat, lon, hd, ulat, ulon):
        """→ (назва, p) для міста користувача, якщо воно у списку вузлів, інакше найімовірніша ціль"""
        P = self.p_target(lat, lon, hd)
        if not P:
            return None, 0.0
        home = self.node_near(ulat, ulon)
        if home is not None and home in P:
            return self.nodes[home][0], P[home]
        k = max(P, key=P.get)
        return self.nodes[k][0], P[k]

    def heat_window(self, lat, lon, w=28, h=18):
        """вікно комірок із центром (lat, lon): la/lo — верхній-лівий кут, v — base36 (0..z, log-шкала)"""
        r0 = max(0, min(self.rows - h, int((self.lat0 - lat) / self.step) - h // 2))
        c0 = max(0, min(self.cols - w, int((lon - self.lon0) / self.step) - w // 2))
        lm = math.log1p(self.hmax)
        v = []
        for r in range(r0, r0 + h):
            for c in range(c0, c0 + w):
                x = self.heat.get(r * self.cols + c, 0)
                v.append(B36[min(35, int(round(35 * math.log1p(x) / lm)))] if x > 0 else "0")
        return dict(la=round(self.lat0 - r0 * self.step, 3), lo=round(self.lon0 + c0 * self.step, 3), w=w, h=h, v="".join(v))
