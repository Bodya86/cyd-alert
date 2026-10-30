#!/usr/bin/env python3
"""Довготривалий режим для GitHub Actions (.github/workflows/live.yml): цикл ~раз на хвилину протягом ~5 год 50 хв.

    python server/live.py --minutes 350 --interval 60 --branch data [--push]

  * JAAM WebSocket тримається підключеним увесь час (тривоги приходять миттєво, без перепідключень);
  * кожну ітерацію: тривоги → усі Telegram-канали з data/channels.json паралельно → трекер → api/*.json;
  * файли перезаписуються лише при зміні змісту (або раз на 5 хв як «серцебиття»), тож ETag у GitHub API
    стабільний і ESP отримує дешеві 304;
  * публікація: один коміт без історії, force-push у гілку `data` (GitHub Pages тут не задіяний —
    деплой Pages щохвилини занадто важкий; Pages оновлює окремий update.yml раз на годину);
  * стан трекера на старті береться з origin/<branch>:tracker_state.json (попередній запуск).
"""
import argparse, json, os, subprocess, sys, tempfile, time, traceback
os.environ.setdefault("TZ", "EET-2EEST,M3.5.0/3,M10.5.0/4")   # час у підписах — за Києвом (runner GitHub — UTC)
time.tzset()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JAAM", "1")
import build_static as bs     # noqa: E402
import server as srv          # noqa: E402
import tracker as tk          # noqa: E402
import jaam_ws                # noqa: E402
import tg_notify              # noqa: E402
import accuracy               # noqa: E402
import news                   # noqa: E402

log = bs.log


def git(*args, env=None, check=True, inp=None):
    r = subprocess.run(["git", *args], capture_output=True, text=True, env=env, input=inp)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()}")
    return r.stdout.strip()


def seed_from_branch(branch, out):
    """Попередній вміст гілки data → out/ (щоб порівняння «чи змінився файл» працювало з першої ітерації)
    і стан трекера. → dict стану або None."""
    if subprocess.run(["git", "fetch", "-q", "--depth=1", "origin", branch], capture_output=True).returncode:
        log(f"seed: гілки {branch} ще немає — старт з нуля")
        return None
    try:
        os.makedirs(out, exist_ok=True)
        arch = subprocess.run(["git", "archive", "FETCH_HEAD"], capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", out], input=arch, check=True)
        with open(os.path.join(out, "tracker_state.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log("seed:", e)
        return None


class Publisher:
    """Коміт без батьків із вмістом out/ → git push -f origin <commit>:refs/heads/<branch>."""

    def __init__(self, out, branch):
        self.out, self.branch, self.last_tree = os.path.abspath(out), branch, None
        self.idx = os.path.join(tempfile.gettempdir(), f"cyd-live-{os.getpid()}.idx")
        self.env = {**os.environ, "GIT_INDEX_FILE": self.idx,
                    "GIT_AUTHOR_NAME": "cyd-alert-bot", "GIT_AUTHOR_EMAIL": "actions@users.noreply.github.com",
                    "GIT_COMMITTER_NAME": "cyd-alert-bot", "GIT_COMMITTER_EMAIL": "actions@users.noreply.github.com"}

    def publish(self):
        if os.path.exists(self.idx):
            os.remove(self.idx)
        git("--work-tree=" + self.out, "add", "-A", "-f", ".", env=self.env)
        tree = git("write-tree", env=self.env)
        if tree == self.last_tree:
            return False
        c = git("commit-tree", tree, "-m", "data " + time.strftime("%Y-%m-%d %H:%M:%S"), env=self.env)
        for attempt in range(3):
            r = subprocess.run(["git", "push", "-f", "-q", "origin", f"{c}:refs/heads/{self.branch}"],
                               capture_output=True, text=True)
            if r.returncode == 0:
                self.last_tree = tree
                return True
            log("push:", r.stderr.strip())
            time.sleep(3 + attempt * 5)
        return False


class NewsLoop:
    """Новини про влучання: окремий потік (RSS + Telegram-новини раз на 5 хв) + пости моніторів/ОВА з основного циклу.
    Лише інформація («ймовірна ціль», state.news) — на рівень тривоги не впливає. Набір → out/api/news/hits.json."""

    def __init__(self, out):
        self.path = os.path.join(out, "api", "news", "hits.json")
        self.feed = news.NewsFeed(log=log)
        try:
            with open(self.path, encoding="utf-8") as f:
                self.feed.hits.load_json(json.load(f))
        except (OSError, ValueError):
            pass
        self.prm = news.load_params()
        self.cfg_by_id = {c["id"].lower(): c for c in srv.an.CHANNELS_CFG}
        self.saved_n = -1
        log(f"news: {len(self.feed.hits.items)} записів з попереднього запуску, beta={self.prm[0]}")
        self.feed.start()

    def tick(self):
        self.feed.ingest_channels(srv.channels, self.cfg_by_id)
        self.feed.hits.prune()
        ev = self.feed.hits.events()
        srv.NEWS_EVENTS = ev
        beta, hl = self.prm
        if srv.HIST is not None:
            srv.HIST.news_beta = beta
            srv.HIST.news_scores = news.node_scores(ev, srv.HIST.nodes, time.time(), hl) if beta else {}
        n = len(self.feed.hits.items)
        if n != self.saved_n:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({**self.feed.hits.to_json(), "status": self.feed.status, "at": int(self.feed.at)},
                          f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
            self.saved_n = n


_prim = {"at": 0.0, "alive": False, "down_since": 0.0}
# резерв перебирає сповіщення лише після PRIMARY_GRACE_S безперервної недоступності основного: 30.10 20:08 деплой
# (перезапуск ~25 с) → резерв зі старим кодом надіслав власне повідомлення («НЕБЕЗПЕКА… ризик для міста»), яке основний
# сервер не знав і не міг видалити — у чаті два повідомлення тривоги.
PRIMARY_GRACE_S = 300


def _health_ok(h, now):
    return bool(h.get("ok") and h.get("notify") and abs(now - float(h.get("now", 0))) < 120)


def primary_notifies(fetch=None, now=None):
    """Чи надсилає сповіщення основний сервер (PRIMARY_URL/api/health)? Тоді GitHub-резерв мовчить, щоб не було дублів.
    Перевірка раз на 60 с; основний «мертвий» лише після PRIMARY_GRACE_S поспіль невдалих перевірок (деплой/перезапуск —
    не привід). fetch(url) → dict — для тестів."""
    url = os.environ.get("PRIMARY_URL", "").rstrip("/")
    if not url:
        return False
    now = now or time.time()
    if now - _prim["at"] > 60:
        _prim["at"] = now
        try:
            if fetch:
                h = fetch(url + "/api/health")
            else:
                import urllib.request
                with urllib.request.urlopen(urllib.request.Request(url + "/api/health", headers={"User-Agent": "cyd-live"}),
                                            timeout=8) as r:
                    h = json.loads(r.read().decode("utf-8"))
            ok = _health_ok(h, now)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            _prim["down_since"] = 0.0
            _prim["alive"] = True
        else:
            if not _prim["down_since"]:
                _prim["down_since"] = now
            # ще ніколи не бачили живим (старт резерву) — теж чекаємо grace
            _prim["alive"] = now - _prim["down_since"] < PRIMARY_GRACE_S
    return _prim["alive"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    ap.add_argument("--minutes", type=float, default=350)
    ap.add_argument("--interval", type=float, default=60)
    ap.add_argument("--branch", default="data")
    ap.add_argument("--push", action="store_true", help="публікувати в гілку (у GitHub Actions)")
    ap.add_argument("--outage-every", type=float, default=900, help="графіки відключень — раз на N с")
    a = ap.parse_args()
    out = a.out.rstrip("/\\")
    state_path = os.path.join(out, "tracker_state.json")
    deadline = time.time() + a.minutes * 60

    prev = seed_from_branch(a.branch, out) if a.push else None
    if prev is None and os.path.exists(state_path):
        prev, _ = bs.load_prev_state(state_path, "")
    extra = tk.tracker_load(srv.trk, prev) if prev else {}
    srv._prev_lvl.update({int(k): v for k, v in (extra.get("prev_lvl") or {}).items()})
    srv._jaam_ev_seen = float(extra.get("jaam_ev_seen", 0))
    log(f"tracker: {len(srv.trk.tracks)} цілей з попереднього запуску")

    if not srv.UKRAINEALARM_KEY:
        srv.USE_JAAM = True
        jaam_ws.start(srv.jaam, log)        # постійне підключення з автоперепідключенням
        t0 = time.time()
        while time.time() - t0 < 20 and not srv.jaam.snapshot()[2]:
            time.sleep(0.5)
    notifier = tg_notify.Notifier(out)
    log("telegram-сповіщення: " + ("увімкнено" if notifier.enabled else "вимкнено (немає секретів TG_BOT_TOKEN/TG_CHAT_ID)"))
    acc = accuracy.AccuracyLog(out)
    pub = Publisher(out, a.branch) if a.push else None
    chans = bs.all_channels()
    try:
        nl = NewsLoop(out)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        nl = None
    last_out, it = 0.0, 0
    while time.time() < deadline:
        t0 = time.time()
        it += 1
        src = "—"
        try:
            ob, rs, src = srv.fetch_alerts()
            srv.alerts.update(oblasts=ob, regions=rs, at=time.time(), src=src, err=None)
            srv.feed_tracker_alerts(rs)
        except Exception as e:  # noqa: BLE001
            srv.alerts["err"] = str(e)
            log("alerts error:", e)
        try:
            bs.fetch_all_channels(chans)
            if nl:
                try:
                    nl.tick()
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
            do_out = time.time() - last_out > a.outage_every
            bs._written.update(n=0, skipped=0)
            bs.write_outputs(out, state_path, chans, do_outage=do_out, next_in=int(a.interval))
            if do_out:
                last_out = time.time()
            try:
                if not primary_notifies():
                    notifier.tick()
                elif notifier.enabled and (notifier.mem.get("episode") or notifier.mem.get("clears") or notifier.mem.get("active")):
                    # основний знову живий — прибираємо власні повідомлення резерву (у чаті лишається лише його)
                    notifier.retire()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            try:
                if acc.tick(srv.trk):
                    acc.summarize(force=True)
                acc.save()
                acc.summarize()
            except Exception:  # noqa: BLE001
                traceback.print_exc()
            pushed = pub.publish() if pub else False
            log(f"#{it}: {src}, записано {bs._written['n']}, без змін {bs._written['skipped']}, "
                f"цілей {len(srv.trk.tracks)}, push {'так' if pushed else 'ні'}, {time.time() - t0:.1f} с")
        except Exception:  # noqa: BLE001
            traceback.print_exc()
        time.sleep(max(1.0, a.interval - (time.time() - t0)))
    log("кінець: наступний запуск підхопить cron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
