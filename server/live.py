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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("JAAM", "1")
import build_static as bs     # noqa: E402
import server as srv          # noqa: E402
import tracker as tk          # noqa: E402
import jaam_ws                # noqa: E402

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
    pub = Publisher(out, a.branch) if a.push else None
    chans = bs.all_channels()
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
            do_out = time.time() - last_out > a.outage_every
            bs._written.update(n=0, skipped=0)
            bs.write_outputs(out, state_path, chans, do_outage=do_out, next_in=int(a.interval))
            if do_out:
                last_out = time.time()
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
