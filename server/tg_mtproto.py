"""Пряме читання Telegram через MTProto (Telethon, акаунт користувача): пост з'являється за ~1 с
замість 5–15 с опитування веб-превʼю t.me/s/<канал>. t.me лишається запасним джерелом (fallback) завжди.

Вмикається сам, лише якщо водночас:
  * у /etc/cyd-alert/env є TG_API_ID і TG_API_HASH (https://my.telegram.org → API development tools);
  * встановлено telethon (server/requirements.txt);
  * файл сесії TG_SESSION (типово /var/lib/cyd-alert/tg_user.session) уже авторизований — один раз
    через tools/tg_login.py (QR-код або телефон+код). Сервер НІКОЛИ не запитує логін сам:
    лише connect() + is_user_authorized(); без сесії — стан "needs_login" і робота лише через t.me.
  * TG_MTPROTO=0 вимикає модуль повністю.

Push-оновлення Telegram приходять лише з каналів, на які акаунт підписаний (join). Канали без підписки
читаються t.me як і раніше (і, якщо TG_MT_POLL>0 — ще й get_messages раз на TG_MT_POLL с, обережно з FloodWait)."""
import asyncio, os, threading, time

SESSION = os.environ.get("TG_SESSION", "/var/lib/cyd-alert/tg_user.session")
STATUS = {"state": "off", "why": "", "joined": [], "rx": 0, "last_rx": 0.0, "since": 0.0, "errors": 0}


def config(env=None):
    """(api_id, api_hash) або None, якщо не налаштовано / вимкнено."""
    env = os.environ if env is None else env
    if env.get("TG_MTPROTO", "1") == "0":
        return None
    api_id, api_hash = (env.get("TG_API_ID") or "").strip(), (env.get("TG_API_HASH") or "").strip()
    if not api_id.isdigit() or len(api_hash) < 16:
        return None
    return int(api_id), api_hash


def session_base(path=SESSION):
    """Telethon сам додає «.session» — передаємо шлях без розширення."""
    return path[:-8] if path.endswith(".session") else path


def to_post(msg):
    """telethon Message → {id, ts, text} як у tg_source.fetch_web (текст в один рядок, без порожніх)."""
    text = getattr(msg, "message", None) or ""
    text = " ".join(text.split())
    if not text or getattr(msg, "date", None) is None:
        return None
    return {"id": int(msg.id), "ts": msg.date.timestamp(), "text": text}


def healthy(max_quiet_s=None):
    """MTProto підключено й авторизовано (канали з підпискою можна опитувати t.me рідше)."""
    return STATUS["state"] == "ok"


def covers(ch):
    return healthy() and ch.lower() in STATUS["joined"]


class MtprotoSource(threading.Thread):
    """Фоновий потік з власним asyncio-циклом. on_posts(channel, [post]) викликається з цього потоку."""

    def __init__(self, channels, on_posts, log=print, poll_s=None):
        super().__init__(daemon=True, name="mtproto")
        self.channels = [c.lower() for c in channels]
        self.on_posts, self.log = on_posts, log
        self.poll_s = float(os.environ.get("TG_MT_POLL", "0")) if poll_s is None else poll_s
        self.cfg = config()

    @staticmethod
    def available():
        try:
            import telethon  # noqa: F401
            return True
        except Exception:  # noqa: BLE001
            return False

    def run(self):
        if not self.cfg:
            STATUS.update(state="off", why="немає TG_API_ID/TG_API_HASH (або TG_MTPROTO=0)")
            return
        if not self.available():
            STATUS.update(state="off", why="не встановлено telethon")
            return
        delay = 15
        while True:
            try:
                r = asyncio.run(self._main())
            except Exception as e:  # noqa: BLE001
                STATUS.update(state="error", why=f"{type(e).__name__}: {e}"[:200])
                STATUS["errors"] += 1
                self.log("mtproto: помилка:", STATUS["why"])
                r = "error"
            if r == "needs_login":       # без сесії — перевіряємо раз на 10 хв (раптом залогінились)
                time.sleep(600)
                continue
            time.sleep(delay)
            delay = min(300, delay * 2)

    async def _main(self):
        from telethon import TelegramClient, events, utils
        api_id, api_hash = self.cfg
        if not os.path.exists(session_base() + ".session"):
            STATUS.update(state="needs_login", why="немає файлу сесії — запустіть tools/tg_login.py")
            return "needs_login"
        STATUS.update(state="connecting", why="")
        client = TelegramClient(session_base(), api_id, api_hash, device_model="CYD Alert server",
                                app_version="cyd-alert", receive_updates=True, auto_reconnect=True)
        await client.connect()
        try:
            if not await client.is_user_authorized():
                STATUS.update(state="needs_login", why="сесія не авторизована — запустіть tools/tg_login.py")
                return "needs_login"
            want = set(self.channels)
            ids = {}
            async for d in client.iter_dialogs():
                ent = d.entity
                un = (getattr(ent, "username", None) or "").lower()
                if un and un in want:
                    ids[utils.get_peer_id(ent)] = un
            STATUS.update(joined=sorted(ids.values()))
            self.log(f"mtproto: авторизовано; push з {len(ids)}/{len(want)} каналів (решта — t.me)")

            async def on_msg(ev):
                ch = ids.get(ev.chat_id)
                p = to_post(ev.message) if ch else None
                if not p:
                    return
                STATUS["rx"] += 1
                STATUS["last_rx"] = time.time()
                try:
                    self.on_posts(ch, [p])
                except Exception as e:  # noqa: BLE001
                    STATUS["errors"] += 1
                    self.log("mtproto on_posts:", e)

            if ids:
                client.add_event_handler(on_msg, events.NewMessage(chats=list(ids)))
                client.add_event_handler(on_msg, events.MessageEdited(chats=list(ids)))
                for pid, ch in ids.items():          # наздогнати пропущене за час перепідключення
                    try:
                        msgs = await client.get_messages(pid, limit=10)
                        posts = [p for p in (to_post(m) for m in reversed(msgs)) if p]
                        if posts:
                            self.on_posts(ch, posts)
                    except Exception as e:  # noqa: BLE001
                        self.log(f"mtproto catch-up {ch}:", e)
                    await asyncio.sleep(0.3)
            STATUS.update(state="ok", since=time.time(), why="")
            tasks = [asyncio.ensure_future(client.run_until_disconnected())]
            rest = [c for c in self.channels if c not in set(ids.values())]
            if self.poll_s > 0 and rest:
                tasks.append(asyncio.ensure_future(self._poll(client, rest)))
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            STATUS.update(state="error", why="відключено")
            return "disconnected"
        finally:
            await client.disconnect()

    async def _poll(self, client, rest):
        from telethon.errors import FloodWaitError
        while True:
            for ch in rest:
                try:
                    msgs = await client.get_messages(ch, limit=10)
                    posts = [p for p in (to_post(m) for m in reversed(msgs)) if p]
                    if posts:
                        self.on_posts(ch, posts)
                except FloodWaitError as e:
                    self.log(f"mtproto poll: FloodWait {e.seconds} с")
                    await asyncio.sleep(e.seconds + 5)
                except Exception as e:  # noqa: BLE001
                    self.log(f"mtproto poll {ch}:", e)
                await asyncio.sleep(1.5)
            await asyncio.sleep(self.poll_s)
