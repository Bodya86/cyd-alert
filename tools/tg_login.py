#!/usr/bin/env python3
"""Одноразовий вхід у Telegram (MTProto) для сервера CYD Alert — запускати на сервері від root:

    /opt/cyd-alert/.venv/bin/python /opt/cyd-alert/tools/tg_login.py --qr [--png /root/tg_qr.png]
    /opt/cyd-alert/.venv/bin/python /opt/cyd-alert/tools/tg_login.py --phone +380XXXXXXXXX
    ... --status            лише перевірити сесію (нічого не запитує)
    ... --join              після входу: показати канали без підписки й (після підтвердження y) підписатись

Бере TG_API_ID / TG_API_HASH з /etc/cyd-alert/env (або середовища). Сесію пише в TG_SESSION
(/var/lib/cyd-alert/tg_user.session), права 600, власник cyd (користувач сервісу). Після входу:
    systemctl restart cyd-alert     → у /api/health з'явиться "mtproto": {"state": "ok", ...}

QR: Telegram на телефоні → Налаштування → Пристрої → «Підключити пристрій» → скануйте код з терміналу
або PNG (--png). Якщо ввімкнено хмарний пароль (2FA) — скрипт попросить його (getpass, не відображається).
Файл сесії = повний доступ до акаунта: не копіюйте його нікуди; вийти — Telegram → Пристрої → завершити сеанс."""
import argparse, asyncio, datetime as dt, getpass, json, os, pwd, sys, time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
ENV_FILE = "/etc/cyd-alert/env"
STATUS_FILE = None   # --status-file: JSON {state, expires, ...} для зовнішнього спостерігача (без секретів)


def status(state, **kw):
    if not STATUS_FILE:
        return
    tmp = STATUS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"state": state, "at": int(time.time()), **kw}, f)
    os.replace(tmp, STATUS_FILE)


def load_env(path=ENV_FILE):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def fix_perms(base):
    for suf in (".session", ".session-journal"):
        p = base + suf
        if os.path.exists(p):
            os.chmod(p, 0o600)
            try:
                u = pwd.getpwnam(os.environ.get("CYD_USER", "cyd"))
                os.chown(p, u.pw_uid, u.pw_gid)
            except (KeyError, PermissionError):
                pass


async def main(a):
    load_env()
    import tg_mtproto as mt
    cfg = mt.config()
    if not cfg:
        sys.exit("Немає TG_API_ID / TG_API_HASH у /etc/cyd-alert/env (my.telegram.org → API development tools)")
    from telethon import TelegramClient, errors
    base = mt.session_base(os.environ.get("TG_SESSION", mt.SESSION))
    os.makedirs(os.path.dirname(base), exist_ok=True)
    client = TelegramClient(base, *cfg, device_model="CYD Alert server", app_version="cyd-alert")
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Сесія вже активна: {me.first_name or ''} (id {me.id})")
        elif a.status:
            print("Сесії немає / не авторизована")
            return 1
        elif a.qr:
            qr = await client.qr_login()
            deadline = time.time() + a.timeout
            while True:
                ttl = max(5.0, (qr.expires - dt.datetime.now(dt.timezone.utc)).total_seconds())
                try:
                    import segno
                    q = segno.make(qr.url, error="l")
                    if not a.quiet:
                        q.terminal(compact=True)
                    if a.png:
                        q.save(a.png + ".tmp.png", scale=8, border=3)
                        os.chmod(a.png + ".tmp.png", 0o600)
                        os.replace(a.png + ".tmp.png", a.png)
                        print("PNG:", a.png, flush=True)
                except ImportError:
                    print("(pip install segno — щоб намалювати QR) URL:", qr.url)
                status("waiting", expires=int(time.time() + ttl), deadline=int(deadline))
                print(f"Скануйте: Telegram → Налаштування → Пристрої → Підключити пристрій. Код дійсний ~{ttl:.0f} с.", flush=True)
                try:
                    await qr.wait(timeout=ttl)
                    break
                except asyncio.TimeoutError:
                    if time.time() > deadline:
                        status("timeout")
                        print("Час очікування вичерпано — запустіть ще раз", flush=True)
                        return 2
                    await qr.recreate()
                except errors.SessionPasswordNeededError:
                    status("2fa_needed")
                    print("Потрібен хмарний пароль (2FA)", flush=True)
                    await client.sign_in(password=getpass.getpass("Хмарний пароль Telegram (2FA): "))
                    break
        else:
            phone = a.phone or input("Телефон (+380…): ").strip()
            await client.send_code_request(phone)
            code = input("Код з Telegram: ").strip()
            try:
                await client.sign_in(phone, code)
            except errors.SessionPasswordNeededError:
                await client.sign_in(password=getpass.getpass("Хмарний пароль Telegram (2FA): "))
        me = await client.get_me()
        print(f"OK: увійшли як {me.first_name or ''} (id {me.id}); сесія {base}.session")
        status("ok", user_id=me.id)
        if a.png and os.path.exists(a.png):
            os.remove(a.png)
        if a.join:
            import build_static as bs
            want = {c.lower() for c in bs.all_channels()}
            have = set()
            async for d in client.iter_dialogs():
                un = (getattr(d.entity, "username", None) or "").lower()
                if un:
                    have.add(un)
            miss = sorted(want - have)
            print(f"Підписано {len(want & have)}/{len(want)}. Без підписки: {', '.join(miss) or '—'}")
            if miss and input("Підписатись на всі ці канали цим акаунтом? [y/N] ").strip().lower() == "y":
                from telethon.tl.functions.channels import JoinChannelRequest
                for ch in miss:
                    try:
                        await client(JoinChannelRequest(ch))
                        print("  +", ch)
                        await asyncio.sleep(3)
                    except errors.FloodWaitError as e:
                        print(f"  FloodWait {e.seconds} с — решту пізніше (повторіть --join)")
                        break
                    except Exception as e:  # noqa: BLE001
                        print("  !", ch, e)
        print("Далі: systemctl restart cyd-alert && curl -s localhost:8080/api/health | python3 -m json.tool | grep -A6 mtproto")
        return 0
    finally:
        await client.disconnect()
        fix_perms(base)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--qr", action="store_true", help="вхід QR-кодом (без SMS-коду)")
    g.add_argument("--phone", help="вхід за номером: код прийде в Telegram")
    g.add_argument("--status", action="store_true", help="лише перевірити, чи сесія активна")
    ap.add_argument("--png", help="зберегти QR також у PNG (для пересилання на телефон з іншого пристрою)")
    ap.add_argument("--join", action="store_true", help="запропонувати підписатись на канали без підписки")
    ap.add_argument("--timeout", type=float, default=600, help="QR: скільки чекати сканування, с (за замовч. 600)")
    ap.add_argument("--status-file", help="QR: писати стан у JSON (waiting/2fa_needed/ok/timeout)")
    ap.add_argument("--quiet", action="store_true", help="QR: не малювати в терміналі")
    _a = ap.parse_known_args()[0]
    STATUS_FILE = _a.status_file
    sys.exit(asyncio.run(main(ap.parse_args())) or 0)
