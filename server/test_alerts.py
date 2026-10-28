"""python3 -m unittest test_alerts — райони/тип загрози і нормалізація графіків відключень"""
import unittest
import alerts as al
import outage


def idx(name, oblast):
    return next(i for i, r in enumerate(al.REGIONS) if r["name"] == name and r["oblast"] == oblast)


class Alerts(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(al.alert_level({"alert_type": "air_raid"}), 1)
        self.assertEqual(al.alert_level({"alert_type": "air_raid", "threats": [{"threat_type": "drones"}]}), 1)
        self.assertEqual(al.alert_level({"alert_type": "air_raid", "threats": [{"threat_type": "ballistic_missiles"}]}), 2)
        self.assertEqual(al.alert_level({"alert_type": "air_raid", "threats": [{"threat_type": "guided_aerial_bombs"}]}), 2)
        self.assertEqual(al.alert_level({"alert_type": "artillery_shelling"}), 2)

    def test_regions(self):
        sample = [
            {"location_type": "raion", "location_uid": "107", "location_oblast_uid": 19, "alert_type": "air_raid",
             "threats": [{"threat_type": "cruise_missiles"}]},
            {"location_type": "hromada", "location_uid": "9999", "location_oblast_uid": 19,
             "location_raion": "Лубенський район", "alert_type": "air_raid"},
            {"location_type": "oblast", "location_uid": "24", "location_oblast_uid": 24, "alert_type": "air_raid"},
        ]
        rs, ob = al.regions_from_active(sample)
        self.assertEqual(rs[idx("Кременчуцький", 16)], "R")
        self.assertEqual(rs[idx("Лубенський", 16)], "y")
        self.assertEqual(rs[idx("Полтавський", 16)], ".")
        self.assertEqual(rs[idx("Черкаський", 24)], "Y")
        self.assertEqual((ob[16], ob[24], ob[9]), ("P", "A", "N"))

    def test_from_oblasts(self):
        rs = al.regions_from_oblasts("N" * 16 + "A" + "N" * 10)
        self.assertEqual(rs[idx("Кременчуцький", 16)], "Y")
        self.assertEqual(rs[idx("Черкаський", 24)], ".")


class Outage(unittest.TestCase):
    def test_gpv(self):
        j = {"fact": {"data": {"1793138400": {"GPV3.1": {str(h): "yes" for h in range(1, 25)} | {
            "8": "no", "9": "first", "10": "second", "11": "mfirst"}}}, "update": "x"}}
        c = outage.parse_gpv(j, "3.1")["days"][0]["cells"]
        self.assertEqual(c[14:22], "11100120")
        self.assertEqual(len(c), 48)
        self.assertEqual(outage.parse_gpv({"fact": {"data": []}}, "3.1")["days"], [])

    def test_yasno(self):
        j = {"1.1": {"today": {"slots": [{"start": 0, "end": 450, "type": "NotPlanned"},
                                         {"start": 450, "end": 660, "type": "Definite"},
                                         {"start": 660, "end": 1440, "type": "NotPlanned"}],
                               "date": "2026-10-28T00:00:00+02:00", "status": "ScheduleApplies"},
                     "tomorrow": {"slots": [], "date": "2026-10-29T00:00:00+02:00", "status": "WaitingForSchedule"}}}
        d = outage.parse_yasno(j, "1.1")["days"]
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["cells"][14:23], "011111110")


if __name__ == "__main__":
    unittest.main()
