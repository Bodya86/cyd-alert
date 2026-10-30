#!/usr/bin/env python3
"""Підписка акаунта MTProto на всі моніторені канали + без звуку + в архів (щоб не заважали на телефоні).

    systemctl stop cyd-alert      # сесію одночасно використовувати не можна (AUTH_KEY_DUPLICATED)
    /opt/cyd-alert/.venv/bin/python /opt/cyd-alert/tools/tg_channels.py --yes [--pace 5] [--max-wait 300]
    systemctl start cyd-alert

Нічого не публікує. FloodWait ≤ --max-wait — чекає й повторює; довший — зупиняється (повторіть пізніше).
Вивід — лише назви публічних каналів і лічильники."""
import argparse, asyncio, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import tg_login  # noqa: E402


async def main(a):
    tg_login.load_env()
    import tg_mtproto as mt
    import build_static as bs
    from telethon import TelegramClient, errors, functions, types
    cfg = mt.config()
    if not cfg:
        sys.exit("немає TG_API_ID/TG_API_HASH")
    base = mt.session_base(os.environ.get("TG_SESSION", mt.SESSION))
    client = TelegramClient(base, *cfg, device_model="CYD Alert server", app_version="cyd-alert")
    await client.connect()
    try:
        if not await client.is_user_authorized():
            sys.exit("сесія не авторизована — спершу tg_login.py --qr")
        want = sorted({c.lower() for c in bs.all_channels()})
        have = {}
        async for d in client.iter_dialogs(archived=None):
            un = (getattr(d.entity, "username", None) or "").lower()
            if un in want:
                have[un] = d.entity
        miss = [c for c in want if c not in have]
        print(f"моніториться {len(want)}, вже підписано {len(have)}, без підписки {len(miss)}", flush=True)
        if miss and not a.yes:
            sys.exit("додайте --yes для підписки")

        async def call(fn, what):
            for _ in range(3):
                try:
                    return await fn()
                except errors.FloodWaitError as e:
                    if e.seconds > a.max_wait:
                        print(f"  FloodWait {e.seconds} с на {what} > {a.max_wait} — зупиняюсь", flush=True)
                        raise
                    print(f"  FloodWait {e.seconds} с на {what} — чекаю", flush=True)
                    await asyncio.sleep(e.seconds + 2)
            return None

        failed = []
        for i, ch in enumerate(miss):
            try:
                r = await call(lambda: client(functions.channels.JoinChannelRequest(ch)), ch)
                ent = r.chats[0] if r and getattr(r, "chats", None) else await client.get_entity(ch)
                have[ch] = ent
                print(f"  + {ch}", flush=True)
            except errors.FloodWaitError:
                failed += miss[i:]
                break
            except Exception as e:  # noqa: BLE001
                failed.append(ch)
                print(f"  ! {ch}: {type(e).__name__}", flush=True)
            await asyncio.sleep(a.pace)

        muted = 0
        for ch, ent in have.items():   # без звуку назавжди
            try:
                ip = await client.get_input_entity(ent)
                await call(lambda: client(functions.account.UpdateNotifySettingsRequest(
                    peer=types.InputNotifyPeer(ip),
                    settings=types.InputPeerNotifySettings(mute_until=2 ** 31 - 1, show_previews=False))), "mute " + ch)
                muted += 1
            except Exception as e:  # noqa: BLE001
                print(f"  ! mute {ch}: {type(e).__name__}", flush=True)
            await asyncio.sleep(0.5)
        try:   # в архів (folder 1)
            await call(lambda: client.edit_folder(list(have.values()), 1), "archive")
            archived = len(have)
        except Exception as e:  # noqa: BLE001
            archived = 0
            print(f"  ! archive: {type(e).__name__}", flush=True)
        print(f"ГОТОВО: підписано {len(have)}/{len(want)}, без звуку {muted}, в архіві {archived}"
              + (f"; не вдалось: {', '.join(failed)}" if failed else ""), flush=True)
        return 0 if not failed else 3
    finally:
        await client.disconnect()
        tg_login.fix_perms(base)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="підписатись без запитання")
    ap.add_argument("--pace", type=float, default=5, help="пауза між підписками, с")
    ap.add_argument("--max-wait", type=int, default=300, help="макс. FloodWait, який чекаємо, с")
    sys.exit(asyncio.run(main(ap.parse_args())) or 0)
