"""Регресія 30.10.2026 16:31: хибний ЧЕРВОНИЙ у Кременчуці без тривоги (TG alert 3) від поста-попередження ppo_poltava:
«США попередили… протягом найближчих 48 годин… переміщення Іскандерів… 📣 ППО Полтавщина». Причини: прогноз/новина
вважалась ціллю; підпис каналу «ППО Полтавщина» давав «балістика на область» і settl:полтав; балістика на область
без офіційної тривоги рахувалась як «прямо про місто» (explicit) і обходила cap_level."""
import time
import unittest

import analyzer as an
import server as srv

NOW = 1_793_457_000
TXT = ("США попередили Україну про можливу масовану ракетну атаку протягом найближчих 48 годин За даними розвідки, "
       "зафіксовано переміщення шести ракет «Іскандер» та систем С-400 до ймовірних районів розгортання пускових "
       "установок. 📣 ППО Полтавщина")


class FalseRedForecast(unittest.TestCase):
    def test_post_ignored(self):
        for d in (None, "center"):
            e = an.analyze_post(TXT, "kremenchuk", d, False, False)
            self.assertTrue(e.ignored, e)
            self.assertEqual(e.level, an.GREEN)
            self.assertFalse(e.explicit)

    def test_multi_green(self):
        lvl, _, ev, _ = an.analyze_multi({"ppo_poltava": [{"id": 58293, "ts": NOW - 60, "text": TXT}]}, "kremenchuk", "center", NOW)
        self.assertEqual(lvl, an.GREEN)
        self.assertFalse(ev.explicit)

    def test_signature_not_location(self):
        e = an.analyze_post("Іскандер-М балістика 📣 ППО Полтавщина", "kremenchuk", None, False, False)
        self.assertNotEqual(e.place, "settl:полтав")
        self.assertFalse(e.explicit)

    def test_ballistic_oblast_capped_without_alert(self):
        e = an.analyze_post("Балістика на Полтавщину! 📣 ППО Полтавщина", "kremenchuk", None, False, False)
        self.assertFalse(e.explicit)
        self.assertEqual(an.cap_level(e.level, False, e.explicit), an.YELLOW)
        self.assertEqual(an.cap_level(e.level, True, e.explicit), an.RED)     # з тривогою в області — червоний

    def test_ballistic_on_city_still_explicit(self):
        e = an.analyze_post("Балістика на Кременчук!", "kremenchuk", None, False, True)
        self.assertTrue(e.explicit)
        self.assertEqual(e.level, an.RED)

    def test_live_warning_not_ignored(self):
        e = an.analyze_post("Загроза балістики! Можлива атака, всім в укриття", "kremenchuk", None, False, False)
        self.assertFalse(e.ignored)

    def test_state_not_red(self):
        old_ch, old_al, old_time = dict(srv.channels), dict(srv.alerts), time.time
        try:
            time.time = lambda: NOW
            srv.alerts.update(oblasts="N" * 27, regions="." * len(srv.al.REGIONS), src="test")
            q = {"city": "kremenchuk", "district": "center"}
            for ch in srv.channels_for(q):
                srv.channels[ch] = {"posts": [{"id": 58293, "ts": NOW - 60, "text": TXT}] if ch == "ppo_poltava" else [],
                                    "at": NOW, "err": None, "last_req": NOW}
            st = srv.state(q)
            self.assertIn(st["final"], ("green", "yellow"), st["reason"])
            self.assertNotEqual(st["zone"], "district")
            self.assertFalse(st["explicit"])
        finally:
            time.time = old_time
            srv.channels.clear(); srv.channels.update(old_ch)
            srv.alerts.clear(); srv.alerts.update(old_al)


if __name__ == "__main__":
    unittest.main()
