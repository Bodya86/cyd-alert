"""Історичне апріорі + кілька цілей (python3 -m unittest test_history)."""
import math, os, random, unittest
import tracker as tk
import history

KREM = (49.068, 33.42)
SUMY = (50.907, 34.798)
ZAP = (47.838, 35.139)


def synth(field_dir=2):
    """модель: у всіх комірках навколо Кременчука/Сум дрони летять на схід (dir 2); 3 вузли-цілі"""
    rows, cols, lat0, lon0, st = 34, 74, 52.5, 22.0, 0.25
    dirs = {}
    for la, lo in (KREM, SUMY):
        r0, c0 = int((lat0 - la) / st), int((lo - lon0) / st)
        for r in range(r0 - 3, r0 + 4):
            for c in range(c0 - 3, c0 + 4):
                v = [0.0] * 8; v[field_dir] = 20.0
                dirs[str(r * cols + c)] = v
    return dict(grid=dict(lat0=lat0, lon0=lon0, step=st, rows=rows, cols=cols), dir=dirs,
                heat={"0": 1.0, str(int((lat0 - KREM[0]) / st) * cols + int((KREM[1] - lon0) / st)): 50.0},
                targets=[["Кременчук", KREM[0], KREM[1], 5, 5], ["Полтава", 49.589, 34.551, 5, 5],
                         ["Кропивницький", 48.508, 32.262, 5, 5]],
                pairs={}, params=dict(base=1.0, gamma=0.5, lam=120.0))


class HistoryTest(unittest.TestCase):
    def setUp(self):
        tk.Track._next = 1
        self.h = history.History(synth())

    def test_field_sampling(self):
        rnd = random.Random(3)
        hs = [math.degrees(self.h.sample_heading(KREM[0], KREM[1], rnd)) for _ in range(400)]
        east = sum(1 for x in hs if 45 < x < 135) / len(hs)
        self.assertGreater(east, 0.6)
        self.assertIsNone(self.h.sample_heading(45.0, 23.0, rnd), "комірка без даних — без апріорі")

    def test_p_target_heading(self):
        # між Кременчуком і Кропивницьким, курс на південний захід → Кропивницький імовірніший за Полтаву
        P = self.h.p_target(48.8, 32.9, hd=235)
        self.assertGreater(P[2], P[1])
        self.assertAlmostEqual(sum(P.values()), 1.0, places=6)
        name, p = self.h.likely_target(48.9, 33.9, 250, *KREM)   # на захід, до Кременчука
        self.assertEqual(name, "Кременчук")
        self.assertGreater(p, 0.4)

    def test_heat_window(self):
        w = self.h.heat_window(*KREM)
        self.assertEqual(len(w["v"]), w["w"] * w["h"])
        self.assertIn("z", w["v"], "максимум → 'z'")
        r = int((w["la"] - KREM[0]) / 0.25); c = int((KREM[1] - w["lo"]) / 0.25)
        self.assertEqual(w["v"][r * w["w"] + c], "z")

    def test_tracker_uses_prior_without_heading(self):
        tr = tk.Tracker(seed=2); tr.history = self.h
        cues = dict(type="shahed", count=1, pos=[dict(name="x", lat=SUMY[0], lon=SUMY[1], sigma=6, region=-1, oblast=-1)],
                    heading=[], origin=[], kill=False)
        tr.apply_cues(cues, 0)
        t = tr.tracks[0]
        self.assertTrue(t.prior_used)
        s = sum(w * math.sin(q[2]) for w, q in zip(t.w, t.p)); c = sum(w * math.cos(q[2]) for w, q in zip(t.w, t.p))
        self.assertLess(abs(((math.degrees(math.atan2(s, c)) - 90) + 180) % 360 - 180), 30)
        d = tr.assess(*KREM, now=60)[0]
        self.assertIsNotNone(d["likely"])

    def test_no_prior_for_missiles_or_known_heading(self):
        tr = tk.Tracker(seed=2); tr.history = self.h
        cues = dict(type="shahed", count=1, pos=[dict(name="x", lat=SUMY[0], lon=SUMY[1], sigma=6, region=-1, oblast=-1)],
                    heading=[dict(name="Полтава", lat=49.589, lon=34.551, sigma=5, region=-1, oblast=-1)], origin=[], kill=False)
        tr.apply_cues(cues, 0)
        self.assertFalse(tr.tracks[0].prior_used, "курс відомий з поста — поле не підмішуємо")

    def test_two_shahed_groups_distinct(self):
        """Шахеди над Сумщиною і над Запоріжжям — дві окремі цілі, обидві у видачі"""
        tr = tk.Tracker(seed=4)
        for k, (la, lo) in enumerate((SUMY, ZAP)):
            cues = dict(type="shahed", count=2 - k, pos=[dict(name=str(k), lat=la, lon=lo, sigma=8, region=-1, oblast=-1)],
                        heading=[], origin=[], kill=False)
            tr.apply_cues(cues, 0)
        self.assertEqual(len(tr.tracks), 2)
        res = tr.assess(*KREM, now=30)
        got = sorted(round(d["lat"]) for d in res if d["lat"] is not None)
        self.assertEqual(got, [48, 51])
        # однакова p=0 → порядок за відстанню (ближча — Запоріжжя)
        self.assertLess(res[0]["dist_km"], res[1]["dist_km"])

    def test_real_model_file_loads(self):
        path = history.DEFAULT_PATH
        if not os.path.exists(path):
            self.skipTest("немає data/history_model.json")
        h = history.History.load(path)
        self.assertIsNotNone(h)
        self.assertGreater(len(h.nodes), 20)
        name, p = h.likely_target(49.4, 34.3, 240, *KREM)
        self.assertTrue(name)


if __name__ == "__main__":
    unittest.main()
