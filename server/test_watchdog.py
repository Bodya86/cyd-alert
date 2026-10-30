"""Сторож deploy/cyd_watchdog.py: оцінка health і машина станів сповіщень (без мережі)."""
import importlib.util, os, unittest

_p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy", "cyd_watchdog.py")
_s = importlib.util.spec_from_file_location("cyd_watchdog", _p)
wd = importlib.util.module_from_spec(_s)
_s.loader.exec_module(wd)
CFG = dict(wd.DEF)
H_OK = {"ok": True, "uptime_s": 4000, "iter_age_s": 1.2, "tg_ok_age_s": 8.0, "new_post_age_s": 300}


class TestWatchdog(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(wd.evaluate("active", H_OK, CFG), ([], False))

    def test_down(self):
        p, hung = wd.evaluate("failed", None, CFG)
        self.assertEqual(p[0][0], "down")
        self.assertFalse(hung)

    def test_no_http_is_hung(self):
        p, hung = wd.evaluate("active", "URLError", CFG)
        self.assertEqual(p[0][0], "health")
        self.assertTrue(hung)

    def test_ingest_over_2min(self):
        p, _ = wd.evaluate("active", dict(H_OK, tg_ok_age_s=150), CFG)
        self.assertEqual([c for c, _ in p], ["ingest"])
        p, _ = wd.evaluate("active", dict(H_OK, tg_ok_age_s=100), CFG)
        self.assertEqual(p, [])
        p, _ = wd.evaluate("active", dict(H_OK, tg_ok_age_s=None), CFG)
        self.assertEqual([c for c, _ in p], ["ingest"])

    def test_grace_after_restart(self):
        self.assertEqual(wd.evaluate("active", dict(H_OK, uptime_s=30, tg_ok_age_s=None), CFG), ([], False))

    def test_quiet_optional(self):
        h = dict(H_OK, new_post_age_s=7200)
        self.assertEqual(wd.evaluate("active", h, CFG)[0], [])
        self.assertEqual(wd.evaluate("active", h, dict(CFG, WD_NEWPOST_MAX_S="3600"))[0][0][0], "quiet")

    def test_state_machine(self):
        probs = [("ingest", "пости не надходять")]
        a, st = wd.step({}, probs, False, 1000, CFG)
        self.assertEqual(a, [])                                    # перша — лише підтвердження
        a, st = wd.step(st, probs, False, 1060, CFG)
        self.assertEqual(a[0][0], "send")
        a, st = wd.step(st, probs, False, 1120, CFG)
        self.assertEqual(a, [])                                    # без спаму
        a, st = wd.step(st, probs, False, 1060 + 1800, CFG)
        self.assertTrue(a and "досі" in a[0][1])                   # нагадування через 30 хв
        a, st = wd.step(st, [], False, 4000, CFG)
        self.assertTrue(a and a[0][1].startswith("✅"))
        a, st = wd.step(st, [], False, 4060, CFG)
        self.assertEqual(a, [])

    def test_restart_when_hung(self):
        probs = [("health", "не відповідає")]
        st, acts = {}, []
        for i in range(3):
            acts, st = wd.step(st, probs, True, 1000 + 60 * i, CFG)
        self.assertIn("restart", [k for k, _ in acts])
        acts, st = wd.step(st, probs, True, 1300, CFG)
        acts, st = wd.step(st, probs, True, 1360, CFG)
        acts, st = wd.step(st, probs, True, 1420, CFG)
        self.assertNotIn("restart", [k for k, _ in acts])           # не частіше раз на 15 хв

    def test_down_no_restart(self):
        st = {}
        for i in range(5):
            acts, st = wd.step(st, [("down", "failed")], False, 1000 + 60 * i, CFG)
            self.assertNotIn("restart", [k for k, _ in acts])      # systemd Restart=always сам підніме


if __name__ == "__main__":
    unittest.main()
