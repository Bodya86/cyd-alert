"""Таксономія цілей (rules.json threats + tracker.TYPES): класифікація постів, сленг/опечатки, хибні збіги,
розвідник не підвищує рівень, кольори/символи для мапи."""
import time
import unittest

import analyzer as an
import tracker as tk
import tg_notify


def ev(s, city="kremenchuk"):
    return an.analyze_post(s, city)


def sub_of(s):
    best, _ = an.threat_of(an.fold(s))
    return (best[1]["type"], best[1].get("sub", "")) if best else None


GAZ = None


def ttype(s):
    global GAZ
    GAZ = GAZ or tk.Tracker().gaz
    return tk.parse_cues(s, GAZ)["type"]


class AnalyzerTaxonomyTest(unittest.TestCase):
    CASES = [
        # (текст, (type, sub))
        ("Шахед курсом на Кременчук", ("drone", "")),
        ("2 мопеди на Хорол", ("drone", "")),
        ("Ударний Бп 1 грп. Полтавська область", ("drone", "")),
        ("Гербера на Кременчук", ("drone", "gerbera")),
        ("дрони-імітатори «Пародія» в районі Кременчука", ("drone", "gerbera")),
        ("Шахеди та Гербери курсом на Полтаву", ("drone", "")),          # змішано — загальний «Шахед»
        ("Реактивний БпЛА курсом на Кременчук", ("drone", "jet")),
        ("Реактивний на Кременчук", ("drone", "jet")),
        ("Герань-3 на Полтаву", ("drone", "jet")),
        ("Ланцет на Богодухів", ("drone", "lancet")),
        ("FPV курсом на Бобрівку", ("drone", "lancet")),
        ("Розвідувальний БпЛА в районі Котельви", ("recon", "recon")),
        ("Розвідник кружляє біля Миргорода", ("recon", "recon")),
        ("БПЛА типу «Zala» курсом на Циркуни", ("recon", "recon")),
        ("Орлан над Кременчуком", ("recon", "recon")),
        ("Розвідник і 3 шахеди на Кременчук", ("drone", "")),            # ударні важливіші за розвідника
        ("Ракета на Кременчук", ("missile", "")),
        ("Крилаті ракети курсом на Полтаву", ("missile", "")),
        ("Калібри з Чорного моря", ("missile", "")),
        ("Іскандер-К на Кременчук", ("missile", "")),                    # крилата, не балістика
        ("Х-59 на Кременчук", ("missile", "")),
        ("Ракета Х-22 на Кременчук", ("missile", "kh22")),
        ("Онікс на Одесу", ("missile", "kh22")),
        ("Бандероль на Хорол", ("missile", "banderol")),
        ("Пуски С-300 по Харкову", ("missile", "s300")),
        ("Балістика на Полтаву", ("ballistic", "")),
        ("Іскандер-М з Курщини", ("ballistic", "")),
        ("KN-23 на Київ", ("ballistic", "")),
        ("Кинджал! Загроза по всій території", ("ballistic", "kinzhal")),
        ("Циркон на Полтаву", ("ballistic", "kinzhal")),
        ("КАБи на Харківщину", ("kab", "")),
        ("Пуски керованих авіабомб на Сумщину", ("kab", "")),
        ("Зліт МіГ-31К", ("aircraft", "mig31k")),
        ("Зліт чотирьох Ту-22М3", ("aircraft", "strat")),
        ("Ту-95 у повітрі", ("aircraft", "strat")),
        ("Активність тактичної авіації на сході", ("aircraft", "")),
    ]

    def test_classify(self):
        for s, want in self.CASES:
            self.assertEqual(sub_of(s), want, s)

    def test_false_matches(self):
        # «бюджет»/«гаджет» ≠ «джет»; «Бортничі»/«на борту» ≠ авіація; «розвідка повідомляє» ≠ розвідник;
        # «вокзала» ≠ Zala; «вул. Бандери» ≠ Бандероль; «мкр.» (мікрорайон) ≠ мКР
        for s in ("Дефіцит бюджету України", "Зарядіть гаджети", "Борт виконує тренування", "Київ: на Бортничі",
                  "За даними розвідки, ворог готує удар", "Біля вокзала спокійно", "вул. Бандери", "мкр. Молодіжний"):
            b = sub_of(s)
            self.assertNotIn(b and b[1], ("jet", "recon", "banderol", "strat", "mig31k"), s)
            self.assertNotEqual(b and b[0], "aircraft", s)
            self.assertNotEqual(b and b[0], "recon", s)

    def test_recon_capped_yellow(self):
        for s in ("Розвідувальний БпЛА над Кременчуком", "Розвідник кружляє над Кременчуком, Молодіжний",
                  "Орлан над Кременчуком, вибухи"):
            e = an.analyze_post(s, "kremenchuk", "molod")
            self.assertEqual(e.threat, "recon", s)
            self.assertLessEqual(e.level, an.YELLOW, s)
        # шахед там само — як і раніше, помаранчевий/червоний
        self.assertGreaterEqual(an.analyze_post("Шахед над Кременчуком", "kremenchuk", "molod").level, an.ORANGE)

    def test_jet_and_gerbera_still_drone_level(self):
        self.assertGreaterEqual(ev("Реактивний БпЛА курсом на Кременчук").level, an.ORANGE)
        self.assertGreaterEqual(ev("Гербера курсом на Кременчук").level, an.ORANGE)

    def test_kinzhal_oblast_wide(self):
        e = ev("Кинджал, напрямок невідомий")
        self.assertEqual((e.threat, e.sub), ("ballistic", "kinzhal"))
        self.assertTrue(e.oblast_wide)

    def test_precursor_alone_no_level(self):
        for s in ("Зліт МіГ-31К", "Зліт Ту-95 з Оленьї", "Стратегічна авіація не активна"):
            self.assertEqual(ev(s).level, an.GREEN, s)


class TrackerTaxonomyTest(unittest.TestCase):
    def test_detect(self):
        for s, want in (("Шахед на Хорол", "shahed"), ("Гербера на Хорол", "gerbera"), ("Шахеди та гербери на Хорол", "shahed"),
                        ("Реактивний БпЛА на Хорол", "jet"), ("джет на Хорол", "jet"), ("Ланцет на Хорол", "lancet"),
                        ("Розвідувальний БпЛА на Хорол", "recon"), ("Zala на Хорол", "recon"), ("Зала на Хорол", "recon"),
                        ("БпЛА на Хорол", "shahed"), ("Ракета Х-22 на Хорол", "kh22"), ("Х-59 на Хорол", "cruise"),
                        ("Іскандер-К на Хорол", "cruise"), ("Бандероль на Хорол", "banderol"), ("КАБ на Хорол", "kab"),
                        ("Кинджал на Полтаву", "kinzhal"), ("С-300 по Харкову", "ballistic"), ("балістика на Полтаву", "ballistic"),
                        ("бюджет на Хорол", None), ("біля вокзала на Хорол", None)):
            self.assertEqual(ttype(s), want, s)

    def test_types_complete(self):
        for k, v in tk.TYPES.items():
            for f in ("v", "turn", "ttl", "cls", "label", "ch", "lc"):
                self.assertIn(f, v, k)
            self.assertIn(v["ch"], tg_notify.TYPE_COLORS, k)
            self.assertIn(v["lc"], "SJDUCKB", k)           # прошивки ≤1.3.11 знають лише ці
        chs = [v["ch"] for v in tk.TYPES.values()]
        self.assertEqual(len(chs), len(set(chs)))           # кожен тип — свій колір
        for k in ("shahed", "gerbera", "jet", "lancet", "recon"):   # дрони — фіолетова гама (B > G)
            r, g, b = tg_notify.TYPE_COLORS[tk.TYPES[k]["ch"]]
            self.assertGreater(b, g, k)

    def test_speeds_ordered(self):
        v = {k: t["v"][0] for k, t in tk.TYPES.items()}
        self.assertLess(v["recon"], v["shahed"])
        self.assertLess(v["lancet"], v["shahed"])
        self.assertLess(v["shahed"], v["jet"])
        self.assertLess(v["banderol"], v["cruise"])
        self.assertLess(v["cruise"], v["kh22"])
        self.assertLess(v["ballistic"], v["kinzhal"])

    def test_recon_tracked_but_no_threat(self):
        tr = tk.Tracker(seed=1)
        now = time.time()
        k = an.CITIES["kremenchuk"]
        tr.add_post("Розвідувальний БпЛА над Кременчуком", now)
        if not tr.tracks:
            tr.tracks.append(tk.Track("recon", k["lat"], k["lon"], 5.0, now, tr._trng(now, k["lat"], k["lon"], "recon")))
        out = tr.assess(k["lat"], k["lon"], now)
        self.assertTrue(out and out[0]["type"] == "recon" and out[0]["cls"] == "recon")
        s = tk.threat_summary(out)
        self.assertEqual(s["p_drone"][30], 0)
        self.assertEqual(s["p_any"][30], 0)
        self.assertEqual(tk.scope_probs(tr, {"city": [(k["lat"], k["lon"], 10)]}, now), {"city": 0})


if __name__ == "__main__":
    unittest.main()
