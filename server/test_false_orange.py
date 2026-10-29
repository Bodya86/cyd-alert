"""Регресія 29.10.2026: плата світилась помаранчевим («КУРС НА МІСТО») без офіційної тривоги в Кременчуці.
Причини: «над містом» з загальнонаціонального каналу (про Дніпро) рахувалось як Кременчук; «Кременчуцький район»
(прикметник) — як місто; КАБ/ракети в сусідній області давали помаранчевий без тривоги; прогноз трекера теж."""
import time
import unittest

import analyzer as an
import server as srv

NOW = 1_793_265_600   # 29.10.2026 ~11:20 за Києвом
POSTS = {  # справжні пости, які були у стрічці
    "kpszsu": [{"id": 1, "ts": NOW - 45 * 60 + 300, "text": "💣 КАБи на Дніпропетровщину."},
               {"id": 2, "ts": NOW - 12 * 60, "text": "⚠ Дніпро 🏍 Реактивний БпЛА над містом"},
               {"id": 3, "ts": NOW - 5 * 60, "text": "🏍 Полтавщина - реактивні БпЛА повз Велику Багачку курсом на Ромодан/Миргород."}],
    "poltavskaODA": [{"id": 4, "ts": NOW - 14 * 60, "text": "🟡 Кременчуцький район — повітряна тривога, жовтий рівень: Дронова загроза (жовтий рівень)"}],
    "ppo_poltava": [{"id": 5, "ts": NOW - 14 * 60, "text": "Кременчуцький, Полтавський, Миргородський райони, тривога: загроза БпЛА 📣 ППО Полтавщина"}],
}


class FalseOrange(unittest.TestCase):
    def test_multi_not_orange(self):
        lvl, best, ev, _ = an.analyze_multi(POSTS, "kremenchuk", None, NOW)
        self.assertLessEqual(lvl, an.YELLOW, (best, ev.reason))
        self.assertFalse(ev.explicit)

    def test_kab_other_oblast_capped_without_alert(self):
        feeds = {"kpszsu": [{"id": 1, "ts": NOW - 60, "text": "💣 КАБи на Дніпропетровщину."}]}
        lvl, _, ev, _ = an.analyze_multi(feeds, "kremenchuk", None, NOW)
        self.assertEqual(an.cap_level(lvl, False, ev.explicit), an.YELLOW)
        self.assertEqual(an.cap_level(lvl, True, ev.explicit), lvl)     # з тривогою в області — як було

    def test_explicit_course_fresh_vs_stale(self):
        feeds = {"kremen_sv": [{"id": 1, "ts": NOW - 3 * 60, "text": "Шахед курсом на Кременчук"}]}
        lvl, _, ev, _ = an.analyze_multi(feeds, "kremenchuk", None, NOW)
        self.assertEqual(an.cap_level(lvl, False, ev.explicit), an.ORANGE)   # свіжий прямий пост — помаранчевий навіть без тривоги
        feeds["kremen_sv"][0]["ts"] = NOW - 25 * 60
        lvl, _, ev, _ = an.analyze_multi(feeds, "kremenchuk", None, NOW)
        self.assertLessEqual(an.cap_level(lvl, False, ev.explicit), an.YELLOW)

    def test_state_no_alert_not_orange(self):
        old_ch, old_al, old_time = dict(srv.channels), dict(srv.alerts), time.time
        try:
            time.time = lambda: NOW
            srv.alerts.update(oblasts="N" * 27, regions="." * len(srv.al.REGIONS), src="test")
            q = {"city": "kremenchuk"}
            for ch in srv.channels_for(q):
                srv.channels[ch] = {"posts": POSTS.get(ch, []), "at": NOW, "err": None, "last_req": NOW}
            st = srv.state(q)
            self.assertIn(st["final"], ("green", "yellow"), st["reason"])
            self.assertIn(st["level"], ("green", "yellow"))
            self.assertIn(st["tlevel"], ("green", "yellow"))
            self.assertFalse(st["obl_alert"])
        finally:
            time.time = old_time
            srv.channels.clear(); srv.channels.update(old_ch)
            srv.alerts.clear(); srv.alerts.update(old_al)


if __name__ == "__main__":
    unittest.main()
