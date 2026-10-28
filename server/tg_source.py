"""Джерела постів Telegram: веб-превʼю t.me/s/<channel> (без акаунта) або Telethon (з акаунтом)."""
import html, os, re, time, urllib.request
from datetime import datetime

UA = "Mozilla/5.0 (X11; Linux x86_64) CYD-Alert-Server/1.0"


def _clean(fragment: str) -> str:
    s = re.sub(r"<br\s*/?>", " ", fragment)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_web(channel: str, timeout=15):
    """Повертає список постів [{id, ts, text}] (від старих до нових) з https://t.me/s/<channel>."""
    req = urllib.request.Request(f"https://t.me/s/{channel}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        page = r.read().decode("utf-8", "replace")
    posts = []
    for chunk in page.split("tgme_widget_message_wrap")[1:]:
        pid = re.search(r'data-post="[^"/]+/(\d+)"', chunk)
        tm = re.search(r'<time datetime="([^"]+)"', chunk)
        tx = re.search(r'js-message_text[^>]*>(.*?)</div>', chunk, re.S)
        if not (pid and tm and tx):
            continue
        text = _clean(tx.group(1))
        if not text:
            continue
        ts = datetime.fromisoformat(tm.group(1)).timestamp()
        posts.append({"id": int(pid.group(1)), "ts": ts, "text": text})
    return posts


class TelethonSource:
    """Опційно: читання каналу через Telethon (потрібні TG_API_ID, TG_API_HASH з my.telegram.org).
    Перший запуск попросить номер телефону і код у консолі; сесія зберігається у файлі cyd.session."""

    def __init__(self):
        from telethon.sync import TelegramClient  # pip install telethon
        self.client = TelegramClient("cyd", int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
        self.client.start()

    def fetch(self, channel: str, limit=20):
        msgs = self.client.get_messages(channel, limit=limit)
        out = [{"id": m.id, "ts": m.date.timestamp(), "text": m.message or ""} for m in msgs if m.message]
        return list(reversed(out))
