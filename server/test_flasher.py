"""Веб-прошивальник: перевірка образу, маніфест (повне стирання, без Improv), маршрути /flash/."""
import asyncio, json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import flasher  # noqa: E402
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402


def img():
    b = bytearray(b"\xff" * 0x20000)
    b[0x1000] = 0xE9; b[0x8000:0x8002] = b"\xaa\x50"; b[0x10000] = 0xE9
    return bytes(b)


def test_valid_image():
    assert flasher.valid_image(img())
    bad = bytearray(img()); bad[0x10000] = 0
    assert not flasher.valid_image(bytes(bad))
    assert not flasher.valid_image(b"\xe9" * 100)


def test_manifest():
    m = flasher.manifest("cyd", "1.2.3")
    assert m["new_install_prompt_erase"] is False and m["new_install_improv_wait_time"] == 0
    assert m["builds"][0]["chipFamily"] == "ESP32"
    assert m["builds"][0]["parts"] == [{"path": "fw/1.2.3/cyd-full-cyd.bin", "offset": 0}]


def test_routes():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "fw", "1.2.3"))
    open(os.path.join(root, "fw", "1.2.3", "cyd-full-cyd.bin"), "wb").write(img())
    json.dump(flasher.manifest("cyd", "1.2.3"), open(os.path.join(root, "manifest-cyd.json"), "w"))

    async def go():
        app = web.Application()
        flasher.routes(app, root, web)
        async with TestClient(TestServer(app)) as c:
            r = await c.get("/flash", allow_redirects=False); assert r.status == 301
            r = await c.get("/flash/"); assert r.status == 200 and "esp-web-install-button" in await r.text()
            r = await c.get("/flash/manifest-cyd.json"); assert r.status == 200
            assert (await r.json())["version"] == "1.2.3" and r.headers["Access-Control-Allow-Origin"] == "*"
            r = await c.get("/flash/manifest-cyd2usb.json"); assert r.status == 503
            r = await c.get("/flash/fw/1.2.3/cyd-full-cyd.bin"); assert r.status == 200 and len(await r.read()) == 0x20000
            r = await c.get("/flash/fw/1.2.3/secret.txt"); assert r.status == 404
            r = await c.get("/flash/fw/../cyd-full-cyd.bin"); assert r.status == 404
            r = await c.get("/flash/status.json"); assert r.status == 200
    asyncio.run(go())


if __name__ == "__main__":
    test_valid_image(); test_manifest(); test_routes(); print("ok")
