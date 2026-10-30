"""мКР «Бандероль» (S8000): класифікація постів і тип/швидкість у трекері (розбір 30.10.2026 ~10:48, проліт над Кременчуком)."""
import time
import unittest

import analyzer as an
import tracker as tk


class BanderolAnalyzerTest(unittest.TestCase):
    def ev(self, s):
        return an.analyze_post(s, "kremenchuk")

    def test_banderol_is_missile(self):
        for s in ("Кременчук увага по бандеролі", "Бандеролі - Кременчук/Горишні Плавні", "Підлітає мКР «Бандероль» з півночі!",
                  "бандероди на Хорол", "Баражуючий боєприпас \"Бандероль\" з Полтавщини на Кременчук", "S8000 курсом на Кременчук"):
            e = self.ev(s)
            self.assertEqual((e.threat, e.sub), ("missile", "banderol"), s)
            self.assertGreaterEqual(e.level, an.ORANGE, s)
        self.assertEqual(self.ev("Кременчук увага по бандеролі").level, an.RED)
        self.assertEqual(self.ev("Ракета на Кременчук").sub, "")          # звичайна ракета — без підтипу

    def test_not_bandera_or_microdistrict(self):
        self.assertNotEqual(self.ev("Обстріл на вул. Бандери, Кременчук").threat, "missile")
        self.assertEqual(self.ev("мкр. Молодіжний, Кременчук").sub, "")

    def test_open_sky_escalates(self):
        self.assertEqual(self.ev("м. Кременчук Центр Покинути відкрите небо!!").level, an.RED)


class BanderolTrackerTest(unittest.TestCase):
    def test_detect(self):
        for s, want in (("Бандероль на Хорол", "banderol"), ("мКР «Бандероль» з півночі", "banderol"), ("МКР на Хорол", "banderol"),
                        ("мкр. Молодіжний", None), ("вул. Бандери", None), ("крилата ракета на Хорол", "cruise")):
            self.assertEqual(tk.parse_cues(s, tk.Tracker().gaz)["type"], want, s)
        self.assertEqual(tk.parse_cues("2 бандеролі на Кременчук", tk.Tracker().gaz)["count"], 2)

    def test_far_prefix_match_other_oblast(self):
        # 30.10 11:38: «в сторону с. Рокита, Великобагачанський район» (Полтавщина) геокодувалось у «Рокитне» (Київщина, ~210 км)
        # → 4 «Бандеролі» біля Білої Церкви з невідомим курсом сумарно давали p_missile 10 % і червоний Кременчуку
        poltava = an.CITIES["poltava"]
        c = tk.parse_cues("мКР «Бандероль» продовжує рух в сторону с. Рокита, Великобагачанський район", tk.Tracker().gaz, poltava)
        self.assertFalse([p for p in c["pos"] + c["heading"] if p["name"].startswith("рокит")])
        c = tk.parse_cues("Шахед на Кременчука", tk.Tracker().gaz, poltava)          # своя область — як і раніше
        self.assertEqual([p["name"] for p in c["heading"]], ["кременчук"])

    def test_speed_and_retype(self):
        self.assertLess(tk.TYPES["banderol"]["v"][0], 600)
        self.assertEqual(tk.TYPES["banderol"]["cls"], "missile")
        t0 = time.time()
        trk = tk.Tracker(seed=3)
        trk.add_post("Ракета в районі Хорол курсом на південь", t0, None)
        self.assertEqual([t.type for t in trk.tracks], ["cruise"])
        trk.add_post("Бандероль в районі Хорол", t0 + 30, None)
        self.assertEqual([t.type for t in trk.tracks], ["banderol"])     # та сама ціль, уточнений тип
        v = sum(q[3] for q in trk.tracks[0].p) / len(trk.tracks[0].p)
        self.assertLess(v, 700)


if __name__ == "__main__":
    unittest.main()
