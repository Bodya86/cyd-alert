"""Векторизований (numpy) Track.forecast — та сама модель руху, що tracker.Track.forecast, у ~50× швидше.
Підключається лише в rt_server.py (install()); без numpy лишається чиста версія."""
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


def install():
    if np is not None:
        tk.Track.forecast = forecast
    return np is not None
