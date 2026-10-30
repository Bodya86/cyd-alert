"""Векторизовані (numpy) методи tracker.Track — та сама модель руху/спостережень, у десятки разів швидше:
forecast, predict_to, ваги спостережень (lik_pos/obs_*/match), ресемплінг, mean.
Підключається в rt_server.py і tools/backtest_tracker.py (install()); без numpy лишається чиста версія.
Частинки зберігаються як масиви N×5 / N (tracker_dump/accuracy/p_zones ітерують їх так само, як списки)."""
import math
import tracker as tk

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

_orig = tk.Track.forecast


def forecast(self, ulat, ulon, R=15.0, horizons=(5, 15, 30), step_min=1.0):
    ux, uy = self.proj.xy(ulat, ulon)
    H = max(horizons)
    rng = self.cfg["rng"]
    nst = int(H / step_min)
    keys = (0, 5, 10, 15, 20, 30)
    P = np.asarray(self.p, dtype=float)
    w = np.asarray(self.w, dtype=float)
    x, y, hd, v, dist = P[:, 0].copy(), P[:, 1].copy(), P[:, 2], P[:, 3], P[:, 4].copy()
    sn, cs = np.sin(hd), np.cos(hd)
    h = step_min / 60.0
    t_in = np.where(np.hypot(x - ux, y - uy) <= R, 0.0, np.nan)
    snap = {0: (x.copy(), y.copy())}
    for k in range(1, nst + 1):
        mv = ~(dist >= rng) if rng else np.ones_like(x, dtype=bool)
        d = np.where(mv, v * h, 0.0)
        nx, ny = x + d * sn, y + d * cs
        dist = dist + d
        miss = np.isnan(t_in)
        if miss.any():
            dx, dy = nx - x, ny - y
            L2 = dx * dx + dy * dy
            with np.errstate(invalid="ignore", divide="ignore"):
                tt = np.where(L2 == 0, 0.0, np.clip(((ux - x) * dx + (uy - y) * dy) / np.where(L2 == 0, 1, L2), 0.0, 1.0))
            dd = np.hypot(x + tt * dx - ux, y + tt * dy - uy)
            hit = miss & (dd <= R)
            t_in = np.where(hit, (k - 1 + tt) * step_min, t_in)
        x, y = nx, ny
        if k * step_min in keys:
            snap[int(k * step_min)] = (x.copy(), y.copy())
    got = ~np.isnan(t_in)
    p = {T: round(float(w[got & (t_in <= T)].sum()), 3) for T in horizons}
    eta = None
    tot = float(w[got].sum())
    if tot > 0.05:
        order = np.argsort(t_in[got], kind="stable")
        ti, ws = t_in[got][order], w[got][order]
        acc = np.cumsum(ws)
        i = int(np.searchsorted(acc, tot / 2 - 1e-12))
        eta = round(float(ti[min(i, len(ti) - 1)]), 1)
    path = []
    mx0, my0, hd0, _, _, _ = self.mean()
    nxh, nyh = math.cos(hd0), -math.sin(hd0)
    sw = float(w.sum()) or 1.0
    for T in sorted(snap):
        sx, sy = snap[T]
        mx = float((sx * w).sum()) / sw; my = float((sy * w).sum()) / sw
        sd = math.sqrt(float((w * ((sx - mx) * nxh + (sy - my) * nyh) ** 2).sum()) / sw)
        lat, lon = self.proj.ll(mx, my)
        path.append([round(lat, 4), round(lon, 4), round(sd, 1)])
    return dict(p=p, eta_min=eta, path=path, dist_km=round(math.hypot(mx0 - ux, my0 - uy), 1))


def _arr(self):
    if not isinstance(self.p, np.ndarray):
        self.p = np.asarray(self.p, dtype=float).reshape(-1, 5)
    if not isinstance(self.w, np.ndarray):
        self.w = np.asarray(self.w, dtype=float)
    g = getattr(self, "_g", None)
    if g is None:
        g = self._g = np.random.default_rng(int(self.rnd.random() * 2 ** 32))
    return self.p, self.w, g


def predict_to(self, t):
    dt = t - self.t
    if dt <= 0:
        return
    P, _, g = _arr(self)
    steps = max(1, int(math.ceil(dt / 60.0)))
    h = dt / steps / 3600.0
    turn = math.radians(self.cfg["turn"]) * math.sqrt(dt / steps / 60.0)
    vm, vs = self.cfg["v"]
    rng = self.cfg["rng"]
    n = len(P)
    for _ in range(steps):
        mv = ~(P[:, 4] >= rng) if rng else np.ones(n, dtype=bool)
        P[:, 2] = np.where(mv, (P[:, 2] + g.normal(0, turn, n)) % tk.TWO_PI, P[:, 2])
        P[:, 3] = np.where(mv, np.clip(P[:, 3] + g.normal(0, vs * 0.05, n), vm - 3 * vs, vm + 3 * vs), P[:, 3])
        d = np.where(mv, P[:, 3] * h, 0.0)
        P[:, 0] += d * np.sin(P[:, 2]); P[:, 1] += d * np.cos(P[:, 2]); P[:, 4] += d
    self.t = t


def _reweight(self, lik):
    _, w, _ = _arr(self)
    ws = w * np.asarray(lik, dtype=float)
    s = float(ws.sum())
    if s <= 1e-300:
        return False
    self.w = ws / s
    if 1.0 / float((self.w * self.w).sum()) < len(self.w) / 2:
        self._resample()
    return True


def _resample(self):
    P, w, g = _arr(self)
    n = len(w)
    pos = (self.rnd.random() + np.arange(n)) / n
    idx = np.minimum(np.searchsorted(np.cumsum(w), pos), n - 1)
    Q = P[idx].copy()
    Q[:, 0] += g.normal(0, 1.5, n); Q[:, 1] += g.normal(0, 1.5, n)
    Q[:, 2] = (Q[:, 2] + g.normal(0, 0.05, n)) % tk.TWO_PI
    self.p, self.w = Q, np.full(n, 1.0 / n)


def lik_pos(self, lat, lon, sigma):
    P, _, _ = _arr(self)
    px, py = self.proj.xy(lat, lon)
    return np.exp(-((P[:, 0] - px) ** 2 + (P[:, 1] - py) ** 2) / (2 * sigma * sigma))


def obs_pos(self, lat, lon, sigma):
    return self._reweight(0.02 + self.lik_pos(lat, lon, sigma))


def _brg(x1, y1, x2, y2):
    return np.arctan2(x2 - x1, y2 - y1) % tk.TWO_PI


def obs_heading_to(self, lat, lon, kappa=3.0):
    P, _, _ = _arr(self)
    px, py = self.proj.xy(lat, lon)
    return self._reweight(np.exp(kappa * (np.cos(P[:, 2] - _brg(P[:, 0], P[:, 1], px, py)) - 1)) + 0.01)


def obs_from(self, lat, lon, kappa=2.0):
    P, _, _ = _arr(self)
    px, py = self.proj.xy(lat, lon)
    return self._reweight(np.exp(kappa * (np.cos(P[:, 2] - _brg(px, py, P[:, 0], P[:, 1])) - 1)) + 0.01)


def obs_region(self, lat, lon, radius, sigma=25.0, eps=0.25, inside=True):
    P, _, _ = _arr(self)
    px, py = self.proj.xy(lat, lon)
    d = np.maximum(0.0, np.hypot(P[:, 0] - px, P[:, 1] - py) - radius)
    lik = eps + (1 - eps) * np.exp(-d * d / (2 * sigma * sigma)) if inside else 1.0 - 0.5 * (d == 0.0)
    return self._reweight(lik)


def match(self, lat, lon, sigma):
    _, w, _ = _arr(self)
    return float((w * self.lik_pos(lat, lon, sigma + 5)).sum())


def mean(self):
    P, w, _ = _arr(self)
    x = float((w * P[:, 0]).sum()); y = float((w * P[:, 1]).sum())
    s = float((w * np.sin(P[:, 2])).sum()); c = float((w * np.cos(P[:, 2])).sum())
    v = float((w * P[:, 3]).sum())
    sd = math.sqrt(float((w * ((P[:, 0] - x) ** 2 + (P[:, 1] - y) ** 2)).sum()) / 2)
    return x, y, math.atan2(s, c) % tk.TWO_PI, math.hypot(s, c), v, sd


def install():
    if np is not None:
        T = tk.Track
        T.forecast, T.predict_to, T._reweight, T._resample = forecast, predict_to, _reweight, _resample
        T.lik_pos, T.obs_pos, T.obs_heading_to, T.obs_from, T.obs_region = lik_pos, obs_pos, obs_heading_to, obs_from, obs_region
        T.match, T.mean = match, mean
    return np is not None
