"""Журнал прогнозів/результатів (python3 -m unittest test_outcomes)."""
import json, os, random, tempfile, unittest
import tracker as tk
import outcomes as oc
from test_tracker import KREM, offset


def cue(lat, lon, typ="shahed"):
    return dict(type=typ, count=1, pos=[dict(name="p", lat=lat, lon=lon, sigma=5, region=-1, oblast=-1)],
                heading=[], origin=[], kill=False)


class OutcomeTest(unittest.TestCase):
    def setUp(self):
        tk.Track._next = 1
        self.dir = tempfile.mkdtemp()

    def run_track(self, bearing, start_km, start_brg, steps=8):
        trk = tk.Tracker(seed=3)
        log = oc.OutcomeLog(self.dir)
        start = offset(*KREM, start_brg, start_km)
        t = 1_800_000_000
        for k in range(steps):
            la, lo = offset(*start, bearing, 170 * k * 300 / 3600)
            trk.apply_cues(cue(la, lo), t)
            for s in range(0, 300, 60):
                now = t + s
                log.tick(trk, *KREM, now=now, alert=k >= 3,
                         assess=lambda: trk.assess(*KREM, now=now, R=15), summary=tk.threat_summary)
            t += 300
        # ціль зникла → результати всіх прогнозів
        trk.tracks = []
        log.tick(trk, *KREM, now=t + 10, alert=False)
        log.save()
        return oc.load(self.dir)

    def test_inbound_passes_near(self):
        fc, out, sums = self.run_track(240, 90, 60)
        self.assertTrue(fc and sums)
        self.assertEqual(set(out), set(fc), "кожен прогноз отримав результат")
        near = [o for o in out.values() if o["near"]]
        self.assertTrue(near, "ціль, що летить на місто, пройшла поруч")
        m = oc.metrics(fc, out)
        self.assertGreater(m["n"], 5)
        self.assertLess(m["brier"], 0.35)
        self.assertTrue(any(o["alert"] for o in out.values()))

    def test_parallel_not_near(self):
        fc, out, _ = self.run_track(0, 80, 90)   # 80 км на схід, летить на північ — повз
        self.assertTrue(out)
        self.assertFalse(any(o["near"] for o in out.values()))
        self.assertTrue(all(fc[f]["p30"] < 0.2 for f in fc))

    def test_restart_keeps_pending(self):
        trk = tk.Tracker(seed=1)
        trk.apply_cues(cue(*offset(*KREM, 90, 60)), 1_800_000_000)
        log = oc.OutcomeLog(self.dir)
        log.tick(trk, *KREM, now=1_800_000_010, assess=lambda: trk.assess(*KREM, now=1_800_000_010))
        log.save()
        log2 = oc.OutcomeLog(self.dir)
        self.assertEqual(len(log2.pending), 1)
        self.assertIn(1, log2.trails)

    def test_rotation_and_size(self):
        old = oc.MAX_BYTES
        oc.MAX_BYTES = 200
        try:
            log = oc.OutcomeLog(self.dir)
            for i in range(20):
                log._write([dict(k="sum", t=1_800_000_000 + i, x="y" * 30)], 1_800_000_000)
            files = sorted(os.listdir(self.dir))
            self.assertGreater(len([f for f in files if f.startswith("fc-")]), 1)
            p = os.path.join(self.dir, files[0])
            os.utime(p, (0, 0))
            log.rotate(now=1_800_000_000)
            self.assertFalse(os.path.exists(p))
        finally:
            oc.MAX_BYTES = old

    def test_seg_min(self):
        a, b = offset(*KREM, 270, 30), offset(*KREM, 90, 30)
        self.assertLess(oc.seg_min_km([a, b], *KREM), 1.0)   # відрізок проходить через місто
        self.assertGreater(oc.seg_min_km([a], *KREM), 29)


if __name__ == "__main__":
    unittest.main()
