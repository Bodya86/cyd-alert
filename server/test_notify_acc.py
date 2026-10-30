import json, os, shutil, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_notify as tn  # noqa: E402
import accuracy as acc  # noqa: E402

CFG = dict(city="kremenchuk", district=None, label="Кременчук", min_level="orange", photo=False,
           debounce_min=10, clear_after_min=3, send_clear=True)


def run(levels, step=60):
    mem, out = {}, []
    for i, l in enumerate(levels):
        r = tn.decide(mem, l, 1000 + i * step, CFG)
        if r:
            out.append((i, r[0], r[1] if r[0] == "alert" else None))
    return out


class DecideTest(unittest.TestCase):
    def test_rise_and_clear(self):
        r = run([0, 1, 2, 2, 2, 0, 0, 0, 0])
        self.assertEqual(r[0], (2, "alert", 2))
        self.assertEqual(r[1][1], "clear")
        self.assertEqual(r[1][0], 8)            # зелений стабільно 3 хв

    def test_yellow_only_silent(self):
        self.assertEqual(run([1, 1, 1, 0, 0, 0, 0]), [])

    def test_escalation_to_red(self):
        r = run([2, 2, 3, 3])
        self.assertEqual([x[2] for x in r], [2, 3])

    def test_flap_no_repeat(self):
        # помаранчевий ↔ жовтий протягом < 10 хв — одне повідомлення
        r = run([2, 1, 2, 1, 2, 1, 2])
        self.assertEqual(len(r), 1)

    def test_long_event_flap_no_repeat(self):
        r = run([2] * 30 + [1] + [2] * 3)
        self.assertEqual(len(r), 1)

    def test_new_event_after_quiet(self):
        r = run([2] + [1] * 12 + [2])
        self.assertEqual([x[2] for x in r], [2, 2])

    def test_short_green_no_clear(self):
        r = run([2, 0, 0, 2, 2])
        self.assertEqual(len(r), 1)

    def test_update_closer_and_new_red_kind(self):
        # 30.10: 05:32 «балістика на область» (червоний), 05:56 «ракети на Кременчук» — той самий рівень, але ближче/інший тип
        mem, cfg = {}, dict(CFG, update_min=2)
        self.assertEqual(tn.decide(mem, 3, 0, cfg, key=("Балістика", "oblast")), ("alert", 3))
        self.assertIsNone(tn.decide(mem, 3, 60, cfg, key=("Балістика", "oblast")))
        self.assertIsNone(tn.decide(mem, 3, 90, cfg, key=("Ракети", "city")))          # < 2 хв — ще рано
        self.assertEqual(tn.decide(mem, 3, 200, cfg, key=("Ракети", "city")), ("alert+", 3))
        self.assertIsNone(tn.decide(mem, 3, 400, cfg, key=("Ракети", "city")))
        self.assertIsNone(tn.decide(mem, 3, 600, cfg, key=("Балістика", "city")))      # тип уже надсилали
        self.assertIsNone(tn.decide(mem, 3, 800, cfg, key=("Ракети", "oblast")))       # віддалення — без повідомлення
        self.assertEqual(tn.decide(mem, 3, 1000, cfg, key=("Ракети", "district")), ("alert+", 3))
        self.assertEqual(tn.alert_text({"kind": "Ракети", "scope": "city"}, 3, cfg, update=True).split("\n")[0], "🔴 Кременчук · ближче")

    def test_update_drone_kind_change_silent(self):
        mem = {}
        self.assertEqual(tn.decide(mem, 2, 0, CFG, key=("Дрони", "city")), ("alert", 2))
        self.assertIsNone(tn.decide(mem, 2, 600, CFG, key=("Ціль", "city")))

    def test_texts(self):
        st = {"final": "orange", "kind": "Дрони", "reason": "шахед, Кременчук, курс", "p_near": 42, "eta": 12, "lt": "Кременчук",
              "lp": 55, "scope": "city", "scope_src": "post", "thr": "Дрони — Шахед/БпЛА, місто",
              "tgt": [{"c": "S", "la": 49.3, "lo": 33.9, "hd": 250, "n": 2, "pt": [[49.25, 33.8, 5]]}],
              "official": True, "text": "шахед курсом на Кременчук", "channel": "kremen_sv", "agree": 3}
        cfg = dict(CFG, label="Кременчук, Кременчуцький р-н")
        t = tn.alert_text(st, 2, cfg)
        self.assertEqual(t, "🟠 Кременчук, Кременчуцький р-н\nДрони — Шахед/БпЛА ×2\n➡️ Летить у бік міста\n"
                            "Проліт поруч за 30 хв: 42% (~12 хв)\nЙмовірна ціль: Кременчук — 55%")
        for bad in ("@", "«", "kremen_sv", "шахед курсом", "Дані", "JAAM", "канал"):
            self.assertNotIn(bad, t)
        self.assertIn("Не летить на місто", tn.alert_text(dict(st, scope="oblast"), 2, cfg))
        self.assertIn("ваш район", tn.alert_text(dict(st, scope=None), 3, cfg))
        self.assertEqual(tn.clear_text(cfg, 1500), "🟢 Відбій · Кременчук\nтривала ~25 хв")

    def test_render(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("немає Pillow")
        st = {"regions": "." * 139, "region": 100, "tgt": [{"c": "S", "la": 49.3, "lo": 33.9, "hd": 250, "n": 2,
                                                            "pt": [[49.25, 33.7, 5], [49.15, 33.5, 9]]}]}
        png = tn.render_map(st, 49.068, 33.42, 2, CFG)
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_notifier_disabled_without_secrets(self):
        d = tempfile.mkdtemp()
        try:
            n = tn.Notifier(d, CFG, token="", chat="")
            self.assertIsNone(n.tick(st={"final": "red"}))
            sent = []
            n = tn.Notifier(d, CFG, token="x", chat="1", sender=lambda t, p: sent.append(t) or True)
            self.assertEqual(n.tick(now=5000, st={"final": "red"}), "alert")
            self.assertIsNone(n.tick(now=5060, st={"final": "red"}))
            self.assertEqual(len(sent), 1)
            n2 = tn.Notifier(d, CFG, token="x", chat="1", sender=lambda t, p: sent.append(t) or True)   # стан із файлу
            self.assertIsNone(n2.tick(now=5120, st={"final": "red"}))
        finally:
            shutil.rmtree(d)


class AccuracyTest(unittest.TestCase):
    def test_predict_and_resolve(self):
        import random
        import tracker as tk
        import history
        d = tempfile.mkdtemp()
        try:
            trk = tk.Tracker(seed=1)
            trk.history = history.History.load()
            t0 = 1_800_000_000
            tr = tk.Track("shahed", 49.6, 34.5, 5.0, t0, random.Random(2), heading=4.0, kappa=8.0)   # ~229°, на Кременчук
            trk.tracks.append(tr)
            log = acc.AccuracyLog(d)
            log.tick(trk, t0)
            self.assertEqual(len(log.preds), 1)
            # наступне спостереження: 30 км на південний захід
            tr.predict_to(t0 + 600)
            tr.obs_pos(49.42, 34.17, 4.0); tr.last_obs = t0 + 600
            log.tick(trk, t0 + 600)
            trk.tracks = []            # ціль зникла біля Кременчука
            log.obs[tr.id].append([t0 + 1800, 49.07, 33.43])
            n = log.tick(trk, t0 + 1900)
            self.assertGreaterEqual(n, 1)
            log.save()
            s = log.summarize(t0 + 2000, force=True)
            self.assertGreaterEqual(s["rolling"]["24h"]["n_dir"], 1)
            rec = [json.loads(x) for x in open(os.path.join(d, "acc", "resolved.jsonl"))]
            self.assertIn("d1", rec[0])
            self.assertEqual(rec[0]["d1"], 1)
        finally:
            shutil.rmtree(d)


if __name__ == "__main__":
    unittest.main()
