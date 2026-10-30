"""Регресія 30.10.2026: «Відбій» у Telegram прийшов на ~9 хв пізніше за офіційний.
Офіційний відбій у Кременчуцькому р-ні ~15:43, але пост «балістика на область» (explicit, TTL 20 хв) і хвіст
трекера (ракета, nm=1) тримали червоний до 15:48:41, а tg_notify ще 3 хв чекав «стабільно зеленого» → 15:52:11.
Тепер: офіційний відбій → рівень не вище жовтого (якщо немає НОВОГО прямого поста) і «Відбій» за ~15 с."""
import time
import unittest

import analyzer as an
import server as srv
import tg_notify as tn

NOW = 1_793_367_000            # 30.10.2026 ~15:50 за Києвом
KREM = 95                      # Кременчуцький район (data/regions.json)
CFG = dict(tn.load_cfg(), min_level="orange", debounce_min=10, clear_after_min=3, official_clear_s=15)


class DecideOfficialEnd(unittest.TestCase):
    def test_clear_15s_after_official_end(self):
        mem = {}
        self.assertEqual(tn.decide(mem, 3, 0, CFG, official=True), ("alert", 3))
        self.assertIsNone(tn.decide(mem, 3, 600, CFG, official=True))
        end = 800
        # відбій: рівень уже ≤ жовтого (сервер обмежив), але ще не 15 с — чекаємо (захист від мерехтіння)
        self.assertIsNone(tn.decide(mem, 1, end + 5, CFG, off_end=end))
        self.assertEqual(tn.decide(mem, 1, end + 16, CFG, off_end=end)[0], "clear")
        self.assertFalse(mem["active"])
        self.assertIsNone(tn.decide(mem, 1, end + 30, CFG, off_end=end))     # без повторного «Відбою»/тривоги

    def test_no_clear_if_still_high_or_alert_back(self):
        mem = {}
        tn.decide(mem, 3, 0, CFG, official=True)
        # новий прямий пост після відбою тримає червоний — не відбій
        self.assertIsNone(tn.decide(mem, 3, 120, CFG, off_end=100))
        # тривога відновилась (жовта, дрони) — старий відбій забуто, жовтий не дає «Відбою»
        self.assertIsNone(tn.decide(mem, 1, 200, CFG, official=True))
        self.assertIsNone(tn.decide(mem, 1, 260, CFG))
        self.assertTrue(mem["active"])

    def test_old_end_before_episode_ignored(self):
        mem = {}
        tn.decide(mem, 2, 1000, CFG)                       # епізод з поста, без офіційної тривоги
        self.assertIsNone(tn.decide(mem, 1, 1100, CFG, off_end=500))   # відбій був ДО епізоду
        self.assertTrue(mem["active"])

    def test_without_official_end_old_rule(self):
        mem = {}
        tn.decide(mem, 2, 0, CFG)
        self.assertIsNone(tn.decide(mem, 0, 100, CFG))
        self.assertEqual(tn.decide(mem, 0, 100 + 180, CFG)[0], "clear")


class NotifierOfficialEnd(unittest.TestCase):
    def test_live_message_cleared_fast(self):
        import tempfile
        calls = []

        def api(m, f, files=None):
            calls.append(m)
            return {"message_id": len(calls)}
        with tempfile.TemporaryDirectory() as d:
            n = tn.Notifier(d, cfg=dict(CFG, photo=False), token="x", chat="1", api=api)
            st = {"final": "red", "official": True, "kind": "ballistic", "scope": "oblast", "thr": "Балістика"}
            self.assertEqual(n.tick(now=1000, st=st), "alert")
            st = dict(st, final="yellow", official=False, official_end=1500)
            self.assertNotEqual(n.tick(now=1505, st=st), "clear")
            self.assertEqual(n.tick(now=1516, st=st), "clear")
            self.assertIn("deleteMessage", calls)


class StateAfterOfficialEnd(unittest.TestCase):
    POSTS = {"kpszsu": [{"id": 1, "ts": NOW - 8 * 60, "text": "🚀 Балістика на Полтавщину!"}]}

    def _state(self, posts, off_end):
        old_ch, old_al, old_time, old_end = dict(srv.channels), dict(srv.alerts), time.time, dict(srv._off_end)
        try:
            time.time = lambda: NOW
            rs = ["."] * len(srv.al.REGIONS)
            for i, r in enumerate(srv.al.REGIONS):     # інші райони Полтавщини ще під тривогою
                if r["oblast"] == srv.al.REGIONS[KREM]["oblast"] and i != KREM:
                    rs[i] = "R"
            srv.alerts.update(oblasts="N" * 27, regions="".join(rs), src="test")
            srv._off_end.clear()
            if off_end:
                srv._off_end[KREM] = off_end
            q = {"city": "kremenchuk"}
            for ch in srv.channels_for(q):
                srv.channels[ch] = {"posts": posts.get(ch, []), "at": NOW, "err": None, "last_req": NOW}
            return srv.state(q)
        finally:
            time.time = old_time
            srv.channels.clear(); srv.channels.update(old_ch)
            srv.alerts.clear(); srv.alerts.update(old_al)
            srv._off_end.clear(); srv._off_end.update(old_end)

    def test_old_post_capped_after_end(self):
        before = self._state(self.POSTS, 0)
        self.assertIn(before["final"], ("orange", "red"))           # так було 30.10 о 15:43–15:48
        st = self._state(self.POSTS, NOW - 60)
        self.assertIn(st["final"], ("green", "yellow"))
        self.assertIn(st["level"], ("green", "yellow"))             # плата рахує сама з level/tlevel
        self.assertIn(st["tlevel"], ("green", "yellow"))
        self.assertEqual(st["official_end"], NOW - 60)

    def test_new_explicit_post_after_end_still_counts(self):
        posts = {"kremen_sv": [{"id": 9, "ts": NOW - 20, "text": "Шахед курсом на Кременчук"}]}
        st = self._state(posts, NOW - 60)
        self.assertIn(st["final"], ("orange", "red"))

    def test_end_expires(self):
        st = self._state(self.POSTS, NOW - (srv.AFTER_END_MIN + 1) * 60)
        self.assertEqual(st["official_end"], 0)

    def test_feed_records_end(self):
        old = dict(srv._prev_lvl), dict(srv._off_end)
        try:
            srv._prev_lvl.clear(); srv._off_end.clear()
            rs = ["."] * len(srv.al.REGIONS); rs[KREM] = "R"
            srv.feed_tracker_alerts("".join(rs))
            self.assertNotIn(KREM, srv._off_end)
            rs[KREM] = "."
            srv.feed_tracker_alerts("".join(rs))
            self.assertIn(KREM, srv._off_end)
            rs[KREM] = "Y"
            srv.feed_tracker_alerts("".join(rs))
            self.assertNotIn(KREM, srv._off_end)
        finally:
            srv._prev_lvl.clear(); srv._prev_lvl.update(old[0])
            srv._off_end.clear(); srv._off_end.update(old[1])


if __name__ == "__main__":
    unittest.main()
