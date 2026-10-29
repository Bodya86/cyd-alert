"""Тест сповіщень Telegram (workflow tg-test): одне повідомлення з мапою. --dry-run — без надсилання."""
import json, os, sys, urllib.error, urllib.parse, urllib.request, uuid, io
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_notify as tn

TEXT = "🧪 CYD Alert: тест сповіщень — секрети працюють"


def call(token, method, fields, files=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    if files:
        bnd = uuid.uuid4().hex
        b = io.BytesIO()
        for k, v in fields.items():
            b.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        for k, (fn, data, ct) in files.items():
            b.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: {ct}\r\n\r\n".encode())
            b.write(data); b.write(b"\r\n")
        b.write(f"--{bnd}--\r\n".encode())
        req = urllib.request.Request(url, data=b.getvalue(), headers={"Content-Type": f"multipart/form-data; boundary={bnd}"})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(fields).encode())
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            j = {"ok": False, "error_code": e.code, "description": "HTTP " + str(e.code)}
    except Exception as e:  # noqa: BLE001
        j = {"ok": False, "description": type(e).__name__}
    return j


def main():
    dry = "--dry-run" in sys.argv
    cfg = tn.load_cfg()
    st = {"regions": "." * 139, "region": 100, "tgt": [{"c": "S", "la": 49.3, "lo": 33.9, "hd": 250, "n": 1,
                                                        "pt": [[49.25, 33.7, 5], [49.15, 33.5, 9]]}]}
    png = tn.render_map(st, 49.068, 33.42, 2, cfg)
    print("Мапа:", f"{len(png)} байт PNG" if png else "НЕ згенерована (немає Pillow) — буде лише текст")
    if dry:
        print("DRY-RUN: нічого не надсилаю. OK")
        return 0
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    chat = os.environ.get("TG_CHAT_ID", "").strip()
    missing = [n for n, v in (("TG_BOT_TOKEN", token), ("TG_CHAT_ID", chat)) if not v]
    if missing:
        print("ПОМИЛКА: відсутні секрети:", ", ".join(missing), "(Settings → Secrets and variables → Actions)")
        return 1
    if png:
        r = call(token, "sendPhoto", {"chat_id": chat, "caption": TEXT}, {"photo": ("map.png", png, "image/png")})
    else:
        r = call(token, "sendMessage", {"chat_id": chat, "text": TEXT})
    if not r.get("ok"):
        desc = str(r.get("description", "невідома помилка")).replace(token, "***")
        print(f"ПОМИЛКА Telegram API ({r.get('error_code', '?')}): {desc}")
        if r.get("error_code") == 401:
            print("→ невірний TG_BOT_TOKEN")
        elif r.get("error_code") in (400, 403):
            print("→ перевірте TG_CHAT_ID і що боту надіслано /start")
        return 1
    print("✅ Надіслано! Секрети TG_BOT_TOKEN і TG_CHAT_ID працюють.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
