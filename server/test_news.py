"""Новини про влучання (server/news.py) і «куди летить» (зона: область/район/місто/ваш район).
Головне: новини та зона — лише інформація, рівень без офіційної тривоги/свіжого прямого поста не піднімають."""
import time
import unittest

import analyzer as an
import news
import server as srv
import tracker as tk

NOW = 1_793_265_600


class ExtractHits(unittest.TestCase):
    def test_city_missile(self):
        h = news.extract_hits("Унаслідок ракетного удару по Кременчуку пошкоджено підприємство, є поранені", default_ob=16)
        self.assertEqual([(x["name"], x["k"], x["q"]) for x in h], [("Кременчук", "missile", 1.0)])

    def test_case_forms_and_weak(self):
        h = news.extract_hits("У Києві пролунали вибухи під час атаки дронів")
        self.assertEqual(h[0]["name"], "Київ")
        self.assertEqual(h[0]["k"], "drone")
        self.assertLess(h[0]["q"], 1.0)          # «вибухи» — слабка ознака

    def test_oblast_only(self):
        h = news.extract_hits("Вночі ворог атакував Полтавщину дронами, пошкоджено інфраструктуру", default_ob=16)
        self.assertEqual(len(h), 1)
        self.assertIsNone(h[0]["name"])
        self.assertEqual(h[0]["ob"], 16)

    def test_not_hits(self):
        for t in ("Шахед курсом на Кременчук", "Відбій тривоги у Полтаві", "Суд виніс вирок за удар по Кременчуку",
                  "🔹 Харків 🔹 Суми 🔹 Одеса — головні події: влучання", "Можливі вибухи у Полтаві, працює ППО"):
            self.assertEqual(news.extract_hits(t, default_ob=16), [], t)


class Rss(unittest.TestCase):
    def test_rss_and_atom(self):
        rss = b"""<?xml version="1.0"?><rss><channel><item><title>Atack</title><link>https://x/1</link>
<description>Text</description><pubDate>Thu, 29 Oct 2026 08:00:00 +0200</pubDate></item></channel></rss>"""
        it = news.parse_rss(rss)
        self.assertEqual(len(it), 1)
        atom = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>T</title>
<id>urn:1</id><updated>2026-10-29T06:00:00Z</updated><summary>S</summary></entry></feed>"""
        self.assertEqual(len(news.parse_rss(atom)), 1)


class Events(unittest.TestCase):
    def test_merge_dedup_decay(self):
        h = news.NewsHits(30)
        txt = "Унаслідок ракетного удару по Кременчуку пошкоджено підприємство"
        self.assertEqual(h.add_post("a", 1, NOW - 3600, txt, 0.9), 1)
        self.assertEqual(h.add_post("a", 1, NOW - 3600, txt, 0.9), 0)        # той самий пост
        h.add_post("b", 7, NOW - 1800, txt, 0.5)
        ev = h.events()
        self.assertEqual(len(ev), 1)                                         # два джерела — одна подія
        self.assertAlmostEqual(ev[0]["conf"], 1 - 0.1 * 0.5, places=3)
        nodes = [["Кременчук", 49.068, 33.42, 0, 0]]
        s_new = news.node_scores(ev, nodes, NOW, 10)[0]
        s_old = news.node_scores(ev, nodes, NOW + 10 * 86400, 10)[0]
        self.assertAlmostEqual(s_old, s_new / 2, places=3)                   # період напіврозпаду
        sm = news.summary(ev, 49.068, 33.42, 16, now=NOW)
        self.assertEqual(sm["n24"], 1)
        self.assertEqual(sm["top"][0][0], "Кременчук")


class Scope(unittest.TestCase):
    def test_post_scope(self):
        cases = {"Шахед курсом на Кременчук": "city", "БпЛА на Кременчуцький район": "raion",
                 "Дрони на Полтавщину": "oblast"}
        for t, want in cases.items():
            _, _, ev, _ = an.analyze_multi({"kremen_sv": [{"id": 1, "ts": NOW - 60, "text": t}]}, "kremenchuk", None, NOW)
            self.assertEqual(ev.scope, want, (t, ev.reason))

    def test_scope_pick_and_levels(self):
        ev = an.PostEval()
        self.assertEqual(srv.scope_of({}, ev, an.GREEN, False, True, {"oblast": 0.5, "raion": 0.3, "city": 0.2}), "city")
        self.assertEqual(srv.scope_of({}, ev, an.GREEN, False, True, {"oblast": 0.5, "raion": 0.1}), "oblast")
        self.assertEqual(srv.scope_of({}, ev, an.GREEN, True, True, {}), "raion")
        self.assertEqual(srv.SCOPE_LVL["oblast"], an.YELLOW)
        self.assertEqual(srv.SCOPE_LVL["raion"], an.YELLOW)
        self.assertEqual(srv.SCOPE_LVL["city"], an.ORANGE)
        self.assertEqual(srv.SCOPE_LVL["district"], an.RED)

    def test_scope_probs_track(self):
        trk = tk.Tracker()
        z = srv.zones_for({"city": "kremenchuk"})
        self.assertTrue(z["oblast"] and z["raion"] and z["city"])
        self.assertEqual(z["district"], [])
        # шахед ~40 км на південь, курсом на північ → місто ймовірне, а область/район — щонайменше так само
        import random
        tr = tk.Track("shahed", 48.75, 33.42, 4.0, NOW - 60, random.Random(1), heading=0.0, kappa=20.0)
        trk.tracks.append(tr)
        p = tk.scope_probs(trk, z, now=NOW)
        self.assertGreaterEqual(p["oblast"], p["city"])
        self.assertGreaterEqual(p["raion"], p["city"])
        self.assertGreater(p["city"], 0.15)


class NeverRaises(unittest.TestCase):
    def test_news_and_scope_do_not_raise_without_alert(self):
        old_ch, old_al, old_time, old_ev = dict(srv.channels), dict(srv.alerts), time.time, srv.NEWS_EVENTS
        try:
            time.time = lambda: NOW
            srv.alerts.update(oblasts="N" * 27, regions="." * len(srv.al.REGIONS), src="test")
            q = {"city": "kremenchuk"}
            for ch in srv.channels_for(q):
                srv.channels[ch] = {"posts": [], "at": NOW, "err": None, "last_req": NOW}
            base = srv.state(q)
            h = news.NewsHits(30)
            for i in range(6):
                h.add_post("n", i, NOW - 7 * 3600 * (i + 1), "Унаслідок ракетного удару по Кременчуку пошкоджено підприємство", 0.9)
            srv.NEWS_EVENTS = h.events()
            st = srv.state(q)
            self.assertEqual(st["final"], base["final"])
            self.assertEqual(st["final"], "green")
            self.assertEqual(st["thr"], "Загроз немає")
            self.assertEqual(st["scope"], "")
            self.assertEqual(st["lt"], "Кременчук")                   # лише інформація (новини)
            self.assertEqual(st["lsrc"], "news")
        finally:
            time.time = old_time
            srv.NEWS_EVENTS = old_ev
            srv.channels.clear(); srv.channels.update(old_ch)
            srv.alerts.clear(); srv.alerts.update(old_al)


if __name__ == "__main__":
    unittest.main()
