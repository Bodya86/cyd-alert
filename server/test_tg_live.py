import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tg_notify as tn  # noqa: E402

CFG = {"city": "kremenchuk", "district": None, "label": "Кременчук", "min_level": "orange", "photo": False,
       "debounce_min": 10, "clear_after_min": 3, "send_clear": True, "update_min": 2, "live_edit_s": 10}


class FakeApi:
    def __init__(self):
        self.calls, self.n, self.alive, self.fail = [], 100, set(), {}

    def __call__(self, method, f, files=None):
        self.calls.append((method, dict(f)))
        if method in self.fail:
            e = self.fail[method]
            if isinstance(e, list):
                e = e.pop(0) if e else None
            if e:
                raise e
        if method.startswith("send"):
            self.n += 1
            self.alive.add(self.n)
            return {"message_id": self.n}
        if method == "deleteMessage":
            self.alive.discard(f["message_id"])
            return True
        return True

    def m(self, name):
        return [c for c in self.calls if c[0] == name]


def st(final, scope="city", kind="drone", tgt=None, pn=40):
    return {"final": final, "scope": scope, "kind": kind, "thr": "Дрони — Шахед", "p_near": pn, "eta": 7,
            "tgt": tgt or [{"k": "S", "la": 49.3, "lo": 33.9, "hd": 250, "n": 1}], "regions": "." * 10}


class LiveTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.api = FakeApi()
        self.n = tn.Notifier(self.d, CFG, token="x", chat="1", api=self.api)

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_episode(self):
        n, a = self.n, self.api
        self.assertEqual(n.tick(now=1000, st=st("orange")), "alert")
        self.assertEqual(len(a.m("sendMessage")), 1)
        live = n.mem["live"]["id"]
        self.assertIsNone(n.tick(now=1005, st=st("orange", tgt=[{"k": "S", "la": 49.2, "lo": 33.8, "hd": 250}])))  # < 10 с
        self.assertEqual(n.tick(now=1011, st=st("orange", tgt=[{"k": "S", "la": 49.2, "lo": 33.8, "hd": 250}], pn=55)), "edit")
        self.assertEqual(len(a.m("editMessageText")), 1)
        self.assertIsNone(n.tick(now=1025, st=st("orange", tgt=[{"k": "S", "la": 49.2, "lo": 33.8, "hd": 250}], pn=55)))  # без змін
        self.assertEqual(len(a.m("editMessageText")), 1)
        # підвищення до червоного → нове повідомлення (зі звуком), старе видалено
        self.assertEqual(n.tick(now=1040, st=st("red", scope="district", pn=90)), "alert")
        new = n.mem["live"]["id"]
        self.assertNotEqual(new, live)
        self.assertNotIn(live, a.alive)
        send = a.m("sendMessage")[-1][1]
        self.assertNotIn("disable_notification", send)
        self.assertNotIn("%", send["text"].split("\n")[0] + "".join(l for l in send["text"].split("\n") if "Проліт" in l))
        self.assertNotIn("Проліт", send["text"])     # червоний — без %
        self.assertEqual(n.mem["episode"], [new])
        # відбій: зелений 3 хв → видалити все, лишити лише «Відбій»
        n.tick(now=1100, st=st("green", tgt=[]))
        self.assertEqual(n.tick(now=1100 + 181, st=st("green", tgt=[])), "clear")
        self.assertNotIn(new, a.alive)
        self.assertEqual(len(a.alive), 1)
        self.assertIn("Відбій", a.m("sendMessage")[-1][1]["text"])
        self.assertEqual(n.mem["episode"], [])

    def test_persist_and_errors(self):
        n, a = self.n, self.api
        n.tick(now=1000, st=st("orange"))
        mid = n.mem["live"]["id"]
        n2 = tn.Notifier(self.d, CFG, token="x", chat="1", api=a)          # перезапуск сервера
        self.assertEqual(n2.mem["episode"], [mid])
        a.fail["editMessageText"] = [tn.TgError(400, "Bad Request: message is not modified")]
        self.assertEqual(n2.tick(now=1020, st=st("orange", pn=60)), "edit")
        a.fail["editMessageText"] = [tn.TgError(429, "Too Many Requests", 30)]
        self.assertIsNone(n2.tick(now=1040, st=st("orange", pn=70)))
        self.assertGreater(n2.hold_until, 0)
        n2.hold_until = 0
        a.fail["editMessageText"] = [tn.TgError(400, "Bad Request: message to edit not found")]
        n2.tick(now=1060, st=st("orange", pn=80))                          # зникло → нове без звуку
        self.assertEqual(a.m("sendMessage")[-1][1].get("disable_notification"), "true")
        self.assertEqual(len(n2.mem["episode"]), 2)
        n2.tick(now=1100, st=st("green", tgt=[]))
        n2.tick(now=1300, st=st("green", tgt=[]))
        self.assertEqual(n2.mem["episode"], [])
        self.assertEqual(len(a.alive), 1)                                   # лише «Відбій»

    def test_photo_edit_media(self):
        cfg = dict(CFG, photo=True)
        n = tn.Notifier(self.d, cfg, token="x", chat="1", api=self.api)
        n._png = lambda s, l: b"\x89PNGfake"
        n.tick(now=1000, st=st("orange"))
        self.assertEqual(len(self.api.m("sendPhoto")), 1)
        n.tick(now=1011, st=st("orange", pn=50))                           # лише текст
        self.assertEqual(len(self.api.m("editMessageCaption")), 1)
        n.tick(now=1022, st=st("orange", pn=50, tgt=[{"k": "S", "la": 49.1, "lo": 33.7, "hd": 250}]))   # мапа
        self.assertEqual(len(self.api.m("editMessageMedia")), 1)
        self.assertIn("attach://map", self.api.m("editMessageMedia")[0][1]["media"])


if __name__ == "__main__":
    unittest.main()
