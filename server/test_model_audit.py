"""Аудит імовірнісної моделі (30.10): подвійний облік подій і дублікатів цілей, нормування p_zones,
«мертві» після fold() слова в регулярках звітів/прогнозів."""
import math, random, unittest
import tracker as tk
import analyzer as an

U = (49.068, 33.420)


def ev(p, place="Київ", lat=50.45, lon=30.52, reg=-1):
    return dict(cls="missile", p={5: p, 15: p, 30: p}, count=1, eta_min=0, src=["event"], lat=lat, lon=lon, heading=None)


class EventDedupTest(unittest.TestCase):
    def _trk(self):
        trk = tk.Tracker(seed=1)
        tk.Track._next = 1
        return trk

    def test_reposted_far_ballistic_not_red(self):
        # 3 канали пишуть «балістика на Київ» → раніше 3 незалежні події по 0.05 → 0.143 ≥ 0.10 → червоний у Кременчуці
        trk = self._trk()
        t0 = 1_800_000_000
        for i, ch in enumerate(("a", "b", "c")):
            trk.add_post("Балістика на Київ!", t0 + i * 20, None, src=f"tg:{ch}", post_id=f"{ch}/1")
        out = trk.assess(*U, now=t0 + 90, user_region=None, user_oblast=16)
        evs = [d for d in out if "event" in d["src"]]
        self.assertEqual(len(evs), 1)                              # одна подія, а не три
        s = tk.threat_summary(out, cal={})
        self.assertLess(s["raw"]["p_missile"][30], 0.10)
        self.assertLess(s["level"], 3)

    def test_oblast_event_still_red(self):
        trk = self._trk()
        t0 = 1_800_000_000
        trk.add_post("Балістика на Полтавщину!", t0, an.CITIES["kremenchuk"], src="tg:kremen_sv", post_id="a/1")
        out = trk.assess(*U, now=t0 + 30, user_region=None, user_oblast=16)
        s = tk.threat_summary(out, cal={})
        self.assertEqual(s["level"], 3)
        self.assertAlmostEqual(s["raw"]["p_missile"][30], tk.EVENT_P["oblast"], places=3)


class DedupTargetsTest(unittest.TestCase):
    """dedup_targets як опція (у проді DEDUP_KM=0 — вимкнено, див. tracker.py)."""
    def setUp(self):
        self._km = tk.DEDUP_KM; tk.DEDUP_KM = 15.0

    def tearDown(self):
        tk.DEDUP_KM = self._km

    def test_disabled_by_default_is_identity(self):
        tk.DEDUP_KM = self._km
        ts = [self.t(49.3, 33.5, 190, 0.3), self.t(49.3, 33.5, 190, 0.3)]
        self.assertEqual(len(tk.dedup_targets(ts)), 2 if not self._km else 1)

    def t(self, lat, lon, hd, p, cls="drone", hc=0.9, count=1):
        return dict(cls=cls, lat=lat, lon=lon, heading=hd, heading_conf=hc, p={5: p / 3, 15: p / 2, 30: p}, count=count,
                    eta_min=12, src=["tg:x"])

    def test_duplicate_tracks_not_compounded(self):
        a, b = self.t(49.3, 33.5, 190, 0.3), self.t(49.32, 33.52, 200, 0.3)
        s = tk.threat_summary([a, b], cal={})
        self.assertAlmostEqual(s["raw"]["p_drone"][30], 0.3, places=3)    # було 0.51
        self.assertEqual(a["p"][30], 0.3)                                  # вхідні цілі не змінено

    def test_distinct_targets_still_combine(self):
        far = self.t(49.6, 34.5, 250, 0.3)                                  # інша ціль за ~95 км
        opp = self.t(49.31, 33.51, 20, 0.3)                                 # поруч, але протилежний курс
        s = tk.threat_summary([self.t(49.3, 33.5, 190, 0.3), far, opp], cal={})
        self.assertAlmostEqual(s["raw"]["p_drone"][30], 1 - 0.7 ** 3, places=3)

    def test_classes_and_events_separate(self):
        s = tk.threat_summary([self.t(49.3, 33.5, 190, 0.3), self.t(49.3, 33.5, 190, 0.3, cls="missile")], cal={})
        self.assertAlmostEqual(s["raw"]["p_any"][30], 1 - 0.7 ** 2, places=3)

    def test_max_count_kept(self):
        out = tk.dedup_targets([self.t(49.3, 33.5, 190, 0.3, count=4), self.t(49.3, 33.5, 190, 0.2, count=1)])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["count"], out[0]["p"][30]), (4, 0.3))


class PZonesNormTest(unittest.TestCase):
    def test_subsample_weights_normalized(self):
        tk.Track._next = 1
        tr = tk.Track("shahed", 49.3, 33.5, 3.0, 0, random.Random(2), heading=math.radians(190), kappa=200)
        tr.w = [(3.0 if k % 3 == 0 else 0.5) for k in range(tk.N)]
        s = sum(tr.w); tr.w = [w / s for w in tr.w]                         # підвибірка [::3] важча за решту
        p = tr.p_zones([[(U[0], U[1], 15.0)], [(U[0], U[1], 500.0)]])
        self.assertLessEqual(p[1], 1.0)
        self.assertAlmostEqual(p[1], 1.0, places=3)                          # певна подія → рівно 1, не Σw·3
        tr2 = tk.Track("shahed", 49.3, 33.5, 3.0, 0, random.Random(2), heading=math.radians(190), kappa=200)
        self.assertAlmostEqual(tr2.p_zones([[(U[0], U[1], 500.0)]])[0], 1.0, places=3)


class FoldedRegexTest(unittest.TestCase):
    def test_no_dead_words(self):
        # слова в регулярках, що застосовуються до fold()-тексту, не можуть містити ь/ї/і/ы/э/є/ё/ґ
        import re
        bad = set("іїыэєёґьъ")
        for name in ("RETRO_RE", "RETRO_HINT_RE", "RETRO_STRONG_RE", "LIVE_RE", "FORECAST_RE", "LIVE_NOW_RE"):
            pat = getattr(an, name).pattern
            words = [w for w in re.findall(r"[а-яіїєґёыэьъ]+", pat) if set(w) & bad]
            self.assertEqual(words, [], name)

    def test_russian_reports_ignored(self):
        for t in ("В ночь на 30 октября враг атаковал Кременчуг ракетами",
                  "Утром противник использовал 4 ракеты по Кременчугу"):
            self.assertTrue(an.analyze_post(t, "kremenchuk").ignored, t)

    def test_live_words_after_fold(self):
        # «підльот» (fold → «пидлот») і «взрыв» (→ «взрив») — пост живий, не звіт
        self.assertFalse(an.analyze_post("Ворог атакував Кременчук, 2 ракети, ще підльот", "kremenchuk").ignored)
        self.assertFalse(an.analyze_post("Враг атаковал Кременчуг, 3 взрыва", "kremenchuk").ignored)

    def test_russian_possible_attack_is_forecast(self):
        self.assertTrue(an.is_forecast(an.fold("Возможный массированный удар: возможный ракетный атака в выходные")))


if __name__ == "__main__":
    unittest.main()
