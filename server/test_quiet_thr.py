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


if __name__ == "__main__":
    unittest.main()
