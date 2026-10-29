"""python3 -m unittest test_analyzer  — ті самі кейси, що test/host/test_main.cpp"""
import unittest
import analyzer as an

import json
import os

_LV = {"green": an.GREEN, "yellow": an.YELLOW, "orange": an.ORANGE, "red": an.RED}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test", "analyzer_cases.json"), encoding="utf-8") as _f:
    CASES = json.load(_f)   # спільні кейси з test/host (C++)


class T(unittest.TestCase):
    def test_posts(self):
        for c in CASES:
            with self.subTest(text=c["t"]):
                ch = c.get("ch")
                remote = bool(ch) and not an.channel_is_local(ch, "kremenchuk")
                e = an.analyze_post(c["t"], "kremenchuk", c["d"], remote, an.channel_is_home(ch, "kremenchuk"))
                self.assertEqual(e.level, _LV[c["lvl"]])
                if c.get("near") is not None:
                    self.assertEqual(e.near, c["near"])
                if "far" in c:
                    self.assertEqual(e.far, c["far"])
                if "ow" in c:
                    self.assertEqual(e.oblast_wide, c["ow"])

    def test_feed_oblast_wide_ttl(self):
        now = 1_800_000_000
        posts = [{"id": 1, "ts": now - 5 * 60, "text": "Бублистика хз откуда"},
                 {"id": 2, "ts": now - 4 * 60, "text": "харьков ракета"}]
        self.assertEqual(an.analyze_feed(posts, "kremenchuk", "molod", now)[0], an.RED)
        posts[0]["ts"] = now - 12 * 60   # балістика «хз звідки» живе 10 хв
        lvl, best, ev = an.analyze_feed(posts, "kremenchuk", "molod", now)
        self.assertEqual(lvl, an.GREEN); self.assertTrue(ev.far)

    def test_multi_dedupe_confidence(self):
        now = 1_800_000_000
        feeds = {"kremen_sv": [{"id": 1, "ts": now - 120, "text": "Шахед над Глобино"}],
                 "kudy_letyt": [{"id": 5, "ts": now - 60, "text": "шахед в районі Глобиного"}],
                 "war_monitor": [{"id": 9, "ts": now - 30, "text": "Шахеды на Одессу"}]}
        lvl, best, ev, cl = an.analyze_multi(feeds, "kremenchuk", "molod", now)
        self.assertEqual(lvl, an.ORANGE)   # дрон біля Глобиного ≤15 км — помаранчевий
        glob = [c for c in cl if c["n"] == 2]
        self.assertEqual(len(glob), 1)
        self.assertGreater(glob[0]["conf"], 0.9)
        self.assertIn("2 канали", ev.reasons)

    def test_multi_local_clear_wins(self):
        now = 1_800_000_000
        feeds = {"kudy_letyt": [{"id": 1, "ts": now - 300, "text": "шахед над Глобино"}],
                 "kremen_sv": [{"id": 2, "ts": now - 60, "text": "Відбій, чисто"}],
                 "war_monitor": [{"id": 3, "ts": now - 30, "text": "відбій"}]}
        self.assertEqual(an.analyze_multi(feeds, "kremenchuk", "molod", now)[0], an.GREEN)
        # «відбій» з загальнонаціонального каналу НЕ скасовує
        feeds = {"kremen_sv": [{"id": 1, "ts": now - 300, "text": "Шахед над Молодіжним"}],
                 "war_monitor": [{"id": 3, "ts": now - 30, "text": "відбій"}]}
        self.assertEqual(an.analyze_multi(feeds, "kremenchuk", "molod", now)[0], an.RED)

    def test_default_channels(self):
        d = an.default_channels("kremenchuk")
        self.assertEqual(d[0], "kremen_sv")
        self.assertIn("kpszsu", d)
        self.assertNotIn("synegubov", d)

    def test_feed_clear_resets(self):
        now = 1_800_000_000
        posts = [{"id": 1, "ts": now - 300, "text": "Шахед над Молодіжним, вибухи!"},
                 {"id": 2, "ts": now - 60, "text": "Ну буде відбій 🤝"}]
        lvl, best, _ = an.analyze_feed(posts, "kremenchuk", "molod", now)
        self.assertEqual(lvl, an.GREEN); self.assertEqual(best["id"], 2)

    def test_feed_ttl(self):
        now = 1_800_000_000
        posts = [{"id": 1, "ts": now - 25 * 60, "text": "Шахед над Молодіжним, вибухи!"}]
        self.assertEqual(an.analyze_feed(posts, "kremenchuk", "molod", now)[0], an.YELLOW)
        posts[0]["ts"] = now - 50 * 60
        self.assertEqual(an.analyze_feed(posts, "kremenchuk", "molod", now)[0], an.GREEN)
        rk = [{"id": 2, "ts": now - 5 * 60, "text": "Ракета на Кременчук!"}]
        self.assertEqual(an.analyze_feed(rk, "kremenchuk", "molod", now)[0], an.RED)
        rk[0]["ts"] = now - 20 * 60     # ракетний пост старший 15 хв — неактуальний
        self.assertEqual(an.analyze_feed(rk, "kremenchuk", "molod", now)[0], an.GREEN)

    def test_feed_proximity_ages(self):
        now = 1_800_000_000
        p = [{"id": 1, "ts": now - 60, "text": "Шахед над Молодіжним"}]
        self.assertEqual(an.analyze_feed(p, "kremenchuk", "molod", now)[0], an.RED)
        p[0]["ts"] = now - 12 * 60
        self.assertEqual(an.analyze_feed(p, "kremenchuk", "molod", now)[0], an.ORANGE)
        p[0]["ts"] = now - 22 * 60
        self.assertEqual(an.analyze_feed(p, "kremenchuk", "molod", now)[0], an.YELLOW)
        c = [{"id": 2, "ts": now - 60, "text": "Шахед курсом на Кременчук"}]
        self.assertEqual(an.analyze_feed(c, "kremenchuk", "molod", now)[0], an.ORANGE)
        o = [{"id": 3, "ts": now - 60, "text": "Дрони на Полтавщині"}]
        self.assertEqual(an.analyze_feed(o, "kremenchuk", "molod", now)[0], an.YELLOW)

    def test_feed_drone_then_missile(self):
        now = 1_800_000_000
        posts = [{"id": 1, "ts": now - 300, "text": "Шахед в районе Глобино"},
                 {"id": 2, "ts": now - 60, "text": "Ракета на Кременчук!"}]
        lvl, best, ev = an.analyze_feed(posts, "kremenchuk", "molod", now)
        self.assertEqual((lvl, best["id"], ev.kind), (an.RED, 2, "Ракети"))


if __name__ == "__main__":
    unittest.main()
