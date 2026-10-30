"""Постійна мапа України в Telegram (30.10): одне повідомлення, editMessageMedia лише при зміні, 429/«not modified»,
id переживає перезапуск, логіка тривог його не видаляє."""
import json, os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_notify as tn  # noqa: E402
import tg_overview as ov  # noqa: E402

ST = {"regions": "R" * 10 + "." * 129}
TG = {"targets": [{"lc": "S", "lat": 49.9, "lon": 32.3, "heading": 120, "trail": [[50.5, 31.2], [50.0, 32.1]],
                   "path": [[49.8, 32.5, 10], [49.6, 33.0, 30]]}]}


class Api:
    def __init__(self):
        self.calls, self.fail, self.n = [], {}, 100

    def __call__(self, m, f, files=None):
        self.calls.append(m)
        q = self.fail.get(m)
        if q:
            raise q.pop(0)
        if m == "sendPhoto":
            self.n += 1
            return {"message_id": self.n}
        return True


def mk(d, api, **kw):
    return ov.OverviewMap(d, token="x", chat="1", api=api, render=lambda s, t: b"png", **kw)


class OverviewTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_send_pin_then_edit_only_on_change(self):
        a = Api()
        m = mk(self.d, a)
        self.assertEqual(m.step(1000, ST, TG), "sent")
        self.assertEqual(a.calls, ["sendPhoto", "pinChatMessage"])
        self.assertEqual(m.step(1003, ST, TG), "wait")              # не частіше 5 с
        self.assertEqual(m.step(1006, ST, TG), "same")              # нічого не змінилось — без запиту
        self.assertEqual(len(a.calls), 2)
        tg2 = json.loads(json.dumps(TG)); tg2["targets"][0]["lat"] = 49.8
        self.assertEqual(m.step(1012, ST, tg2), "edited")
        self.assertEqual(a.calls[-1], "editMessageMedia")

    def test_id_persists_across_restart(self):
        a = Api()
        mk(self.d, a).step(1000, ST, TG)
        m2 = mk(self.d, a)
        self.assertEqual(m2.mem["id"], 101)
        self.assertEqual(m2.step(2000, ST, TG), "same")            # без нового повідомлення після перезапуску
        self.assertEqual(m2.step(2010, {"regions": "Y" * 139}, TG), "edited")
        self.assertEqual(a.calls.count("sendPhoto"), 1)

    def test_not_modified_and_429(self):
        a = Api()
        m = mk(self.d, a)
        m.step(1000, ST, TG)
        a.fail["editMessageMedia"] = [tn.TgError(400, "Bad Request: message is not modified")]
        self.assertEqual(m.step(1010, {"regions": "Y"}, TG), "same")
        a.fail["editMessageMedia"] = [tn.TgError(429, "Too Many Requests", 7)]
        self.assertEqual(m.step(1020, {"regions": "R"}, TG), "error")
        self.assertEqual(m.step(1021, {"regions": "R"}, TG), "hold")
        self.assertEqual(a.calls.count("sendPhoto"), 1)

    def test_deleted_message_recreated(self):
        a = Api()
        m = mk(self.d, a)
        m.step(1000, ST, TG)
        a.fail["editMessageMedia"] = [tn.TgError(400, "Bad Request: message to edit not found")]
        self.assertEqual(m.step(1010, {"regions": "Y"}, TG), "sent")
        self.assertEqual(m.mem["id"], 102)

    def test_network_error_keeps_id(self):
        a = Api()
        m = mk(self.d, a)
        m.step(1000, ST, TG)
        a.fail["editMessageMedia"] = [tn.TgError(0, "URLError")]
        self.assertEqual(m.step(1010, {"regions": "Y"}, TG), "error")
        self.assertEqual(m.mem["id"], 101)
        self.assertEqual(a.calls.count("sendPhoto"), 1)

    def test_alert_cleanup_never_deletes_overview(self):
        a = Api()
        mk(self.d, a).step(1000, ST, TG)
        dels = []
        n = tn.Notifier(self.d, {"city": "kremenchuk"}, token="x", chat="1",
                        api=lambda m, f, files=None: dels.append(f.get("message_id")))
        self.assertTrue(n._delete(101))
        self.assertEqual(dels, [])
        self.assertTrue(n._delete(55))
        self.assertEqual(dels, [55])

    def test_render_no_text_real(self):
        try:
            import PIL  # noqa: F401
        except ImportError:
            self.skipTest("no Pillow")
        png = ov.render_overview(ST, TG)
        self.assertTrue(png.startswith(b"\x89PNG"))


if __name__ == "__main__":
    unittest.main()
