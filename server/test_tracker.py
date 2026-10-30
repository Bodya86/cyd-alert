"""Синтетичні траєкторії для частинкового фільтра (python3 -m unittest test_tracker)."""
import math, random, struct, unittest
import tracker as tk
import analyzer as an
import jaam_ws

KREM = (49.068, 33.42)


def offset(lat, lon, bearing_deg, km):
    b = math.radians(bearing_deg)
    return lat + km * math.cos(b) / 110.57, lon + km * math.sin(b) / (111.32 * math.cos(math.radians(lat)))


def fly(tr, start, bearing, v_kmh, t0, n, every_s, noise_km, sigma, rnd, typ="shahed"):
    """Синтетичні спостереження положення вздовж прямої (з шумом) → трекер."""
    ids = set()
    for k in range(n):
        t = t0 + k * every_s
        lat, lon = offset(*start, bearing, v_kmh * (k * every_s) / 3600)
        lat, lon = offset(lat, lon, rnd.uniform(0, 360), abs(rnd.gauss(0, noise_km)))
        cues = dict(type=typ, count=1, pos=[dict(name=f"p{k}", lat=lat, lon=lon, sigma=sigma, region=-1, oblast=-1)],
                    heading=[], origin=[], kill=False)
        for op, i in tr.apply_cues(cues, t):
            ids.add(i)
    return ids, t


class TrackerTest(unittest.TestCase):
    def setUp(self):
        tk.Track._next = 1
        self.rnd = random.Random(7)

    def test_shahed_inbound(self):
        tr = tk.Tracker(seed=1)
        start = offset(*KREM, 60, 110)            # 110 км на сх-пн-сх, летить на місто (азимут 240)
        ids, t = fly(tr, start, 240, 170, 0, 4, 300, 3, 6, self.rnd)
        self.assertEqual(len(tr.tracks), 1, "одна ціль, не плодимо дублікатів")
        res = tr.assess(*KREM, now=t, R=15)
        d = res[0]
        self.assertLess(abs(tk.ang_diff(math.radians(d["heading"]), math.radians(240))), math.radians(20), d["heading"])
        self.assertTrue(120 < d["speed"] < 220, d["speed"])
        # σ курсу ≈ 20° при σ спостережень 9 км; кут кола R=15 км з ~70 км ≈ 12° → P ≈ 0.25–0.5
        self.assertGreater(d["p"][30], 0.2, d["p"])
        remaining = 110 - 170 * 0.25 - 15
        self.assertAlmostEqual(d["eta_min"], remaining / 170 * 60, delta=6)
        s = tk.threat_summary(res)
        self.assertEqual(s["level"], 2)     # помаранчевий: дрон, ймовірно, пройде поруч з містом

    def test_precise_fixes_converge(self):
        tr = tk.Tracker(seed=11)
        start = offset(*KREM, 60, 110)
        ids, t = fly(tr, start, 240, 170, 0, 7, 150, 0.7, 2, self.rnd)
        d = tr.assess(*KREM, now=t, R=15)[0]
        self.assertGreaterEqual(d["heading_conf"], 0.97)
        # 0.5: p30 for a perfect inbound track varies ~0.6-0.7 with the RNG seed (turn noise)
        self.assertGreater(d["p"][30], 0.5, d["p"])
        self.assertLess(d["p"][5], 0.05)

    def test_shahed_passing_by(self):
        tr = tk.Tracker(seed=2)
        start = offset(*KREM, 0, 60)               # 60 км на північ, летить на захід
        ids, t = fly(tr, start, 270, 170, 0, 4, 300, 3, 6, self.rnd)
        d = tr.assess(*KREM, now=t, R=15)[0]
        self.assertLess(d["p"][30], 0.05, d["p"])

    def test_cruise_inbound(self):
        tr = tk.Tracker(seed=3)
        start = offset(*KREM, 200, 330)
        ids, t = fly(tr, start, 20, 800, 0, 4, 120, 4, 8, self.rnd, "cruise")
        d = tr.assess(*KREM, now=t, R=15)[0]
        self.assertEqual(d["cls"], "missile")
        self.assertGreater(d["p"][30], 0.05, d["p"])
        self.assertAlmostEqual(d["eta_min"], (330 - 800 * 6 / 60 - 15) / 800 * 60, delta=4)
        self.assertEqual(tk.threat_summary([d])["level"], 3)

    def test_two_targets_kept_apart(self):
        tr = tk.Tracker(seed=4)
        a = offset(*KREM, 90, 100); b = offset(*KREM, 270, 120)
        for k in range(3):
            fly(tr, offset(*a, 180, 14 * k), 180, 170, k * 300, 1, 300, 2, 6, self.rnd)
            fly(tr, offset(*b, 0, 14 * k), 0, 170, k * 300 + 10, 1, 300, 2, 6, self.rnd)
        self.assertEqual(len(tr.tracks), 2)
        self.assertTrue(all(t.nobs == 3 for t in tr.tracks))

    def test_kab_has_limited_range(self):
        tr = tk.Tracker(seed=5)
        start = offset(*KREM, 90, 100)
        fly(tr, start, 270, 450, 0, 2, 60, 2, 6, self.rnd, "kab")
        d = tr.assess(*KREM, now=60, R=15)[0]
        self.assertLess(d["p"][30], 0.1, "КАБ не долітає далі ~70 км")

    def test_text_pipeline(self):
        tr = tk.Tracker(seed=6)
        city = an.CITIES["kremenchuk"]
        tr.add_post("Шахед курсом на Кременчук з боку Глобиного", 1000, city, post_id=1)
        self.assertEqual(len(tr.tracks), 1)
        d = tr.assess(*KREM, now=1000, R=15)[0]
        self.assertGreater(d["p"][30], 0.4, d)
        self.assertIsNotNone(d["eta_min"])
        self.assertLess(d["eta_min"], 25)
        tr.add_post("Шахед курсом на Кременчук з боку Глобиного", 1000, city, post_id=1)   # дубль поста
        self.assertEqual(len(tr.tracks), 1)
        tr.add_post("Збито шахед біля Глобиного", 1200, city, post_id=2)
        self.assertEqual(len(tr.tracks), 0)

    def test_clear_post_removes_drones(self):
        tr = tk.Tracker(seed=7)
        city = an.CITIES["kremenchuk"]
        tr.add_post("Шахед в районі Козельщини", 0, city, post_id=1)
        self.assertEqual(len(tr.tracks), 1)
        tr.add_post("Манжелия/Ламаное/Глобино и на вылет с области", 300, city, post_id=2)
        self.assertEqual(len(tr.tracks), 0)

    def test_digest_split_into_targets(self):
        tr = tk.Tracker(seed=4)
        txt = "Чернігівщина: 2х БпЛА курсом на Семенівку БпЛА курсом на Ніжин 3х Реактивні БпЛА курсом на Славутич"
        tr.add_post(txt, 1000, None, post_id="d/1")
        types = sorted(t.type for t in tr.tracks)
        self.assertEqual(types, ["jet", "shahed", "shahed"], "кожна ціль дайджесту — окремий трек свого типу")
        self.assertEqual(max(t.count for t in tr.tracks), 3)
        # повтор «курсом на Ніжин» оновлює ту саму ціль, а не плодить нову
        tr.add_post("Чернігівщина: БпЛА курсом на Ніжин", 1300, None, post_id="d/2")
        self.assertEqual(len(tr.tracks), 3)

    def test_heading_only_does_not_hijack_far_track(self):
        tr = tk.Tracker(seed=5)
        start = offset(*KREM, 60, 60)
        fly(tr, start, 240, 170, 0, 3, 300, 1, 3, self.rnd)
        self.assertEqual(len(tr.tracks), 1)
        hd0 = tr.tracks[0].mean()[2]
        tr.add_post("Житомирщина: БпЛА курсом на Коростень", 700, None, post_id="z/1")
        self.assertEqual(len(tr.tracks), 2, "ціль біля Коростеня — нова, не курс кременчуцької")
        self.assertAlmostEqual(tr.tracks[0].mean()[2], hd0, delta=0.35)

    def test_alert_and_signature_not_positions(self):
        g = tk.Tracker(seed=1).gaz
        c = tk.parse_cues("Кременчуцький район, тривога: загроза БпЛА 📣 ППО Полтавщина", g)
        self.assertEqual(c["pos"], [], "оголошення тривоги — не положення цілі")
        c = tk.parse_cues("Полтавщина: БпЛА над Глобиним", g)
        self.assertEqual([p["name"] for p in c["pos"]], ["глобин"], "«Полтавщина» — область, не місто Полтава")
        c = tk.parse_cues("Загроза БпЛА: шахед курсом на Кременчук", g)
        self.assertEqual([p["name"] for p in c["heading"]], ["кременчук"])

    def test_ttl(self):
        tr = tk.Tracker(seed=8)
        fly(tr, offset(*KREM, 0, 100), 180, 170, 0, 1, 60, 1, 6, self.rnd)
        self.assertEqual(len(tr.assess(*KREM, now=10 * 60)), 1)
        self.assertEqual(len(tr.assess(*KREM, now=60 * 60)), 0)

    def test_region_event_and_alert_change(self):
        tr = tk.Tracker(seed=9)
        tr.add_region_event(95, ["drone"], 0)            # Кременчуцький район
        self.assertEqual(len(tr.tracks), 1)
        tr.add_region_event(95, ["drone"], 120)          # те саме — оновлення, не нова ціль
        self.assertEqual(len(tr.tracks), 1)
        tr.alert_change(95, False, 200)
        tr.add_region_event(95, ["ballistic"], 200)
        res = tr.assess(*KREM, now=210, user_region=95, user_oblast=16)
        bal = [d for d in res if d["type"] == "ballistic"]
        self.assertEqual(len(bal), 1)
        self.assertGreaterEqual(bal[0]["p"][5], 0.5)
        self.assertEqual(tk.threat_summary(res)["level"], 3)

    def test_group_probability(self):
        one = dict(cls="drone", count=1, p={5: 0.2, 15: 0.2, 30: 0.2}, eta_min=10)
        grp = dict(one, count=4)
        self.assertGreater(tk.threat_summary([grp])["p_drone"][30], tk.threat_summary([one])["p_drone"][30])
        self.assertLess(tk.threat_summary([grp])["p_drone"][30], 1 - 0.8 ** 4)


class JaamTest(unittest.TestCase):
    def test_feed(self):
        st = jaam_ws.JaamState()
        body = struct.pack("<HH", 100, 0) + struct.pack("<HH", 48, 1 | 1 << 12) + struct.pack("<HH", 114, 1 | 1 << 11)
        self.assertTrue(st.feed(bytes([0xA1]) + body))
        self.assertEqual(st.alerts, {48: 1 | 1 << 12, 114: 1 | 1 << 11})
        self.assertTrue(st.feed(bytes([0xA1]) + struct.pack("<HHHH", 101, 100, 48, 0)))
        self.assertEqual(list(st.alerts), [114])
        self.assertFalse(st.feed(bytes([0xA1]) + struct.pack("<HHHH", 102, 999, 1, 1)), "розрив ланцюжка хешів")
        st.feed(bytes([0xA2]) + struct.pack("<HH", 114, 1 << 5 | 1 << 8), now=5)
        self.assertEqual(st.events[-1], (5, 114, ["drone", "ballistic"]))
        self.assertTrue(st.feed(bytes([0xA3, 1, 2, 3])))   # невідомий/битий — ігнор



class DetectTypeTest(unittest.TestCase):
    def test_iskander_k_cruise(self):
        for t, want in (("Крилаті ракети, ймовірно «Іскандер-К» курсом на Кременчук", "cruise"),
                        ("5 крилатих ракет Іскандер-К на Кременчук", "cruise"),
                        ("Іскандер-М з Брянська", "ballistic"), ("Балістика на Полтаву", "ballistic")):
            self.assertEqual(tk.detect_type(" " + tk._norm(t) + " ", t), want, t)


if __name__ == "__main__":
    unittest.main()


class CityCalTest(unittest.TestCase):
    CAL = {"drone": {30: (0.1, -2.65), 15: (0.115, -3.19), 5: (0.135, -4.03)}, "any": {30: (0.17, -2.2)}}
    T = [{"cls": "drone", "p": {5: .3, 15: .6, 30: .8}, "count": 3, "eta_min": 4}]

    def test_calibrated_lower_monotone_level_from_raw(self):
        s = tk.threat_summary(self.T, cal=self.CAL)
        raw = tk.threat_summary(self.T, cal={})
        self.assertLess(s["p_drone"][30], raw["p_drone"][30])
        self.assertLessEqual(s["p_drone"][5], s["p_drone"][15])
        self.assertLessEqual(s["p_drone"][15], s["p_drone"][30])
        self.assertEqual(s["level"], raw["level"])            # рівень тривоги — з сирих
        self.assertEqual(s["raw"]["p_drone"], raw["p_drone"])
        self.assertEqual(s["p_missile"], raw["p_missile"])   # немає калібрування — сирі

    def test_no_targets_zero(self):
        s = tk.threat_summary([], cal=self.CAL)
        self.assertEqual(s["p_drone"][30], 0.0)
        self.assertEqual(s["p_any"][30], 0.0)
        s = tk.threat_summary([{"cls": "missile", "p": {5: .1, 15: .2, 30: .3}, "count": 1, "eta_min": None}], cal=self.CAL)
        self.assertEqual(s["p_drone"][30], 0.0)               # дронів немає → 0, а не базова частота

    def test_params_file_loads(self):
        cc = tk.load_city_cal()
        self.assertIn("drone", cc)
        self.assertIn(30, cc["drone"])


class TrailWeaveTest(unittest.TestCase):
    def _trk(self):
        trk = tk.Tracker(seed=3)
        tk.Track._next = 1
        return trk

    def test_trail_accumulates_and_persists(self):
        trk = self._trk()
        t0 = 1_800_000_000
        for i, place in enumerate(["Шахед над Полтавою", "Шахед над Решетилівкою", "Шахед над Глобиним"]):
            trk.add_post(place, t0 + i * 900, None, src="tg:x", post_id=f"x/{i}")
        tr = max(trk.tracks, key=lambda t: len(getattr(t, "trail", [])))
        self.assertGreaterEqual(len(tr.trail), 2)
        self.assertLessEqual(len(tr.trail), tk.TRAIL_MAX)
        d = tk.tracker_dump(trk)
        trk2 = self._trk()
        tk.tracker_load(trk2, d)
        self.assertEqual([t.trail for t in trk2.tracks if t.id == tr.id][0], tr.trail)
        a = trk.assess(49.07, 33.42, t0 + 2000)
        self.assertTrue(any(len(x["trail"]) >= 2 for x in a))

    def test_forecast_turn_diffusion_widens_cone(self):
        rs = []
        old = tk.FC_TURN
        try:
            for ft in (0.0, 2.0):
                tk.FC_TURN = ft
                tr = tk.Track("shahed", 49.6, 34.5, 5.0, 0, random.Random(7), heading=math.radians(250), kappa=50)
                rs.append(tr.forecast(49.07, 33.42)["path"][-1][2])
        finally:
            tk.FC_TURN = old
        self.assertGreater(rs[1], rs[0])
