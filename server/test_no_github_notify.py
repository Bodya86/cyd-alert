"""30.10: GitHub Actions-резерв (live.yml) зі старим кодом слав власні тривоги («НЕБЕЗПЕКА… ризик для міста»), яких
сервер не знав і не видаляв. Рішення: сповіщає лише основний сервер; жоден workflow не має доступу до Telegram."""
import glob, os, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class NoGithubNotifyTest(unittest.TestCase):
    def test_workflows_do_not_touch_telegram(self):
        for p in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.y*ml")):
            s = open(p, encoding="utf-8").read()
            for bad in ("TG_BOT_TOKEN", "TG_CHAT_ID", "api.telegram.org", "server/live.py", "tg_notify"):
                self.assertNotIn(bad, s, f"{os.path.basename(p)}: {bad}")

    def test_live_loop_has_no_notifier(self):
        s = open(os.path.join(ROOT, "server", "live.py"), encoding="utf-8").read()
        self.assertNotIn("Notifier(", s)


if __name__ == "__main__":
    unittest.main()
