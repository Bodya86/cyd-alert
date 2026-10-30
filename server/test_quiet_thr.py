import unittest
import server as s
an = s.an


class QuietThr(unittest.TestCase):
    def test_yellow_oblast(self):
        self.assertEqual(s.quiet_thr(an.YELLOW, "oblast", "shahed", [], True), "Дрони в області · до нас не летять")
        self.assertEqual(s.quiet_thr(an.YELLOW, "raion", "drone", [], True), "Дрони у районі · на місто не летять")
        self.assertEqual(s.quiet_thr(an.YELLOW, "", "recon", [], True), "Розвідник в області · до нас не летить")
        self.assertEqual(s.quiet_thr(an.YELLOW, "oblast", "ballistic", [], True), "Балістика · загроза області")

    def test_yellow_city_and_higher_untouched(self):
        self.assertIsNone(s.quiet_thr(an.YELLOW, "city", "drone", [], True))
        self.assertIsNone(s.quiet_thr(an.ORANGE, "oblast", "drone", [], True))
        self.assertIsNone(s.quiet_thr(an.RED, "district", "drone", [], True))

    def test_green(self):
        tg = [{"type": "recon", "cls": "drone", "dist_km": 20}, {"type": "shahed", "cls": "drone", "dist_km": 60},
              {"type": "missile", "cls": "missile", "dist_km": 300}]
        self.assertEqual(s.quiet_thr(an.GREEN, "", "", tg, False), "Дрони в області · до нас не летять")
        self.assertEqual(s.quiet_thr(an.GREEN, "", "", tg[2:], True), "Тривога в області · місту не загрожує")
        self.assertIsNone(s.quiet_thr(an.GREEN, "", "", tg[2:], False))

    def test_fits_device_buffer(self):
        for k in s.QUIET_SHORT:
            for w in ("oblast", "raion"):
                self.assertLess(len(s.quiet_thr(an.YELLOW, w, k, [], True).encode()), 72)


    def test_threat_line(self):   # 30.10: «Балістика, місто» читалось як «летить на місто»
        self.assertEqual(s.threat_line(an.RED, "oblast", "ballistic", "Кременчук"), "Балістика · в області, ризик для міста")
        self.assertEqual(s.threat_line(an.RED, "oblast", "ballistic", "Кременчук", True), "Балістика · загроза вашому району")
        self.assertEqual(s.threat_line(an.RED, "city", "missile", "Кременчук"), "Ракети · загроза вашому району")
        self.assertEqual(s.zone_of(an.RED, "oblast"), "risk")
        self.assertEqual(s.zone_of(an.RED, "raion"), "risk")
        self.assertEqual(s.zone_of(an.RED, "raion", True), "district")
        self.assertEqual(s.zone_of(an.RED, "district"), "district")
        self.assertEqual(s.threat_line(an.ORANGE, "city", "drone", "Кременчук"), "Дрони · курс на Кременчук")
        self.assertEqual(s.threat_line(an.ORANGE, "city", "kab", "Полтава"), "КАБ · курс на Полтаву")
        self.assertEqual(s.threat_line(an.ORANGE, "oblast", "ballistic", "Кременчук"), "Балістика · в області, ризик для міста")
        self.assertEqual(s.threat_line(an.ORANGE, "raion", "kab", "Кременчук"), "КАБ · в області, ризик для міста")
        self.assertEqual(s.threat_line(an.YELLOW, "city", "missile", "Кременчук"), "Ракети · в області")
        self.assertEqual(s.threat_line(an.GREEN, "", "", "Кременчук"), "Загроз немає")
        self.assertEqual(s.zone_of(an.ORANGE, "city"), "course")
        self.assertEqual(s.zone_of(an.ORANGE, "oblast"), "risk")
        for k in s.KIND_TXT:
            for lv, sc in ((an.RED, "district"), (an.ORANGE, "city"), (an.ORANGE, "oblast"), (an.YELLOW, "city")):
                t = s.threat_line(lv, sc, k, "Кременчук")
                self.assertLessEqual(len(t), 40, t)
                self.assertLess(len(t.encode()), 96)
                self.assertNotIn(", місто", t)


if __name__ == "__main__":
    unittest.main()
