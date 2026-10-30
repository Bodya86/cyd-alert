"""Веб-прошивальник (ESP Web Tools) на /flash/: сторінка + manifest-<env>.json + повні образи стабільної версії.

Бінарники беремо з GitHub releases/latest (стабільний канал, куди їх просуває fw-promote.yml після 24 год бети)
і кешуємо локально: браузер (Web Serial) читає образ через fetch(), а асети GitHub без CORS — тому віддаємо самі.
Цикл: раз на FLASH_POLL_S перевіряємо latest; новий тег → завантажуємо cyd-full-<env>.bin, перевіряємо розмір і
заголовки образів (0x1000 bootloader, 0x10000 app = 0xE9), атомарно перемикаємо «current». Збій → лишається попередня.
"""
import json, os, shutil, threading, time, urllib.request

REPO = os.environ.get("FLASH_REPO", "Bodya86/cyd-alert")
POLL_S = float(os.environ.get("FLASH_POLL_S", "900"))
ENVS = {"cyd": "CYD 2.8\" (один micro-USB, ILI9341)", "cyd2usb": "CYD 2.8\" (USB-C + micro-USB, ST7789)"}
PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "flash", "index.html")
STATUS = {"version": None, "checked": 0, "err": None, "published": None}
_lock = threading.Lock()


def _get(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "cyd-alert-flasher", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def valid_image(data):
    """Повний образ з 0x0: bootloader на 0x1000, розмітка на 0x8000, застосунок на 0x10000."""
    return (len(data) > 0x10000 + 0x1000 and data[0x1000] == 0xE9 and data[0x10000] == 0xE9
            and data[0x8000:0x8002] == b"\xaa\x50")


def manifest(env, ver):
    return {"name": "CYD Alert — мапа повітряних тривог", "version": ver,
            "new_install_prompt_erase": False,          # нове встановлення — завжди повне стирання (NVS, otadata)
            "new_install_improv_wait_time": 0,          # прошивка не підтримує Improv — не чекати
            "builds": [{"chipFamily": "ESP32", "parts": [{"path": f"fw/{ver}/cyd-full-{env}.bin", "offset": 0}]}]}


def sync_once(root):
    rel = json.loads(_get(f"https://api.github.com/repos/{REPO}/releases/latest"))
    ver = rel["tag_name"].lstrip("v")
    with _lock:
        if STATUS["version"] == ver and os.path.isdir(os.path.join(root, "fw", ver)):
            return False
    assets = {a["name"]: a for a in rel.get("assets", [])}
    tmp = os.path.join(root, "fw", f".{ver}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for env in ENVS:
        a = assets.get(f"cyd-full-{env}.bin")
        if not a:
            raise RuntimeError(f"v{ver}: немає cyd-full-{env}.bin")
        data = _get(a["browser_download_url"], timeout=180)
        if len(data) != a["size"] or not valid_image(data):
            raise RuntimeError(f"v{ver}: cyd-full-{env}.bin пошкоджений ({len(data)}/{a['size']})")
        with open(os.path.join(tmp, f"cyd-full-{env}.bin"), "wb") as f:
            f.write(data)
    dst = os.path.join(root, "fw", ver)
    shutil.rmtree(dst, ignore_errors=True)
    os.replace(tmp, dst)
    for env in ENVS:
        p = os.path.join(root, f"manifest-{env}.json")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(manifest(env, ver), f, ensure_ascii=False, indent=1)
        os.replace(p + ".tmp", p)
    with open(os.path.join(root, "current.json"), "w", encoding="utf-8") as f:
        json.dump({"version": ver, "published": rel.get("published_at")}, f)
    for d in os.listdir(os.path.join(root, "fw")):        # старі версії (крім поточної) прибираємо
        if d != ver and not d.startswith("."):
            shutil.rmtree(os.path.join(root, "fw", d), ignore_errors=True)
    with _lock:
        STATUS.update(version=ver, published=rel.get("published_at"))
    return True


def loop(root, log=print):
    os.makedirs(os.path.join(root, "fw"), exist_ok=True)
    try:
        cur = json.load(open(os.path.join(root, "current.json")))
        if os.path.isdir(os.path.join(root, "fw", cur["version"])):
            STATUS.update(version=cur["version"], published=cur.get("published"))
    except (OSError, ValueError, KeyError):
        pass
    while True:
        try:
            if sync_once(root):
                log(f"flasher: v{STATUS['version']} готовий для веб-прошивки")
            STATUS["err"] = None
        except Exception as e:  # noqa: BLE001
            STATUS["err"] = str(e)[:200]
            log(f"flasher: {e}")
        STATUS["checked"] = int(time.time())
        time.sleep(POLL_S)


def start(root, log=print):
    threading.Thread(target=loop, args=(root, log), daemon=True, name="flasher").start()


def routes(app, root, web):
    """/flash → /flash/, /flash/ → сторінка, /flash/manifest-<env>.json, /flash/fw/<ver>/<file>.bin, /flash/status.json"""
    hdr = {"Cache-Control": "no-cache", "Access-Control-Allow-Origin": "*"}

    async def redirect(req):
        raise web.HTTPMovedPermanently("/flash/")

    async def page(req):
        return web.FileResponse(PAGE, headers={**hdr, "Content-Type": "text/html; charset=utf-8"})

    async def man(req):
        p = os.path.join(root, f"manifest-{req.match_info['env']}.json")
        if req.match_info["env"] not in ENVS or not os.path.isfile(p):
            raise web.HTTPServiceUnavailable(text='{"error":"firmware not ready"}', content_type="application/json")
        return web.FileResponse(p, headers={**hdr, "Content-Type": "application/json"})

    async def binf(req):
        ver, name = req.match_info["ver"], req.match_info["name"]
        if "/" in ver or ".." in ver or name not in {f"cyd-full-{e}.bin" for e in ENVS}:
            raise web.HTTPNotFound()
        p = os.path.join(root, "fw", ver, name)
        if not os.path.isfile(p):
            raise web.HTTPNotFound()
        return web.FileResponse(p, headers={"Cache-Control": "public, max-age=86400, immutable",
                                            "Access-Control-Allow-Origin": "*", "Content-Type": "application/octet-stream"})

    async def status(req):
        return web.json_response({**STATUS, "envs": ENVS}, headers=hdr)

    app.router.add_get("/flash", redirect)
    app.router.add_get("/flash/", page)
    app.router.add_get("/flash/index.html", page)
    app.router.add_get("/flash/status.json", status)
    app.router.add_get("/flash/manifest-{env}.json", man)
    app.router.add_get("/flash/fw/{ver}/{name}", binf)
