#!/usr/bin/env python3
"""Перевірка після рестарту: сервіс active, /api/health ok, /api/state.json віддається, /ws шле JSON.

    healthcheck.py [--base http://127.0.0.1:8080] [--wait 150]   → exit 0 = здоровий
Чекає до --wait с (старт рушія: seed + JAAM до 20 с + перша ітерація)."""
import argparse, asyncio, json, subprocess, sys, time, urllib.request


def http_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


async def ws_ok(base):
    import aiohttp
    url = base.replace("http", "ws", 1) + "/ws?city=kremenchuk"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url, timeout=10) as ws:
            msg = await ws.receive(timeout=10)
            d = json.loads(msg.data)
            await ws.send_str("ping")
            pong = await ws.receive(timeout=10)
            return isinstance(d, dict) and "generated" in d and pong.data == "pong"


def check(base, unit):
    errs = []
    if unit:
        st = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
        if st != "active":
            errs.append(f"{unit}: {st}")
    try:
        h = http_json(base + "/api/health")
        if not h.get("ok") or h.get("iter", 0) < 2:
            errs.append(f"health: ok={h.get('ok')} iter={h.get('iter')}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"health: {e}")
    try:
        s = http_json(base + "/api/state.json")
        if "generated" not in s:
            errs.append("state.json: no 'generated'")
    except Exception as e:  # noqa: BLE001
        errs.append(f"state.json: {e}")
    try:
        if not asyncio.run(ws_ok(base)):
            errs.append("ws: bad payload")
    except Exception as e:  # noqa: BLE001
        errs.append(f"ws: {e!r}")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    ap.add_argument("--unit", default="cyd-alert")
    ap.add_argument("--wait", type=float, default=150)
    a = ap.parse_args()
    t0, errs = time.time(), ["not run"]
    time.sleep(5)
    while time.time() - t0 < a.wait:
        errs = check(a.base, a.unit)
        if not errs:
            print(f"HEALTHY after {time.time() - t0:.0f} s")
            return 0
        time.sleep(5)
    print("UNHEALTHY: " + "; ".join(errs))
    return 1


if __name__ == "__main__":
    sys.exit(main())
