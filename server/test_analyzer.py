"""python3 -m unittest test_analyzer  — ті самі кейси, що test/host/test_main.cpp"""
import unittest
import analyzer as an

import json
import os

_LV = {"green": an.GREEN, "yellow": an.YELLOW, "red": an.RED}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "test", "analyzer_cases.json"), encoding="utf-8") as _f:
    CASES = json.load(_f)   # спільні кейси з test/host (C++)


class T(unittest.TestCase):
    def test_posts(self):
        for c in CASES:
            with self.subTest(text=c["t"]):
                e = an.analyze_post(c["t"], "kremenchuk", c["d"])
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

    def test_feed_drone_then_missile(self):
        now = 1_800_000_000
        posts = [{"id": 1, "ts": now - 300, "text": "Шахед в районе Глобино"},
                 {"id": 2, "ts": now - 60, "text": "Ракета на Кременчук!"}]
        lvl, best, ev = an.analyze_feed(posts, "kremenchuk", "molod", now)
        self.assertEqual((lvl, best["id"], ev.kind), (an.RED, 2, "Ракети"))


if __name__ == "__main__":
    unittest.main()
