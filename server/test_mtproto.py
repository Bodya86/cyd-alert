"""MTProto-джерело: конфіг, перетворення повідомлень, злиття з t.me (без мережі й без логіну)."""
import datetime, os, sys, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("NO_HISTORY", "1")
import tg_mtproto as mt  # noqa: E402
import server as srv     # noqa: E402


class Msg:
    def __init__(self, i, text, ts=1_760_000_000):
        self.id, self.message = i, text
        self.date = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)


class TestMtproto(unittest.TestCase):
    def test_config(self):
        self.assertIsNone(mt.config({}))
        self.assertIsNone(mt.config({"TG_API_ID": "12345", "TG_API_HASH": "short"}))
        self.assertEqual(mt.config({"TG_API_ID": "12345", "TG_API_HASH": "0123456789abcdef0123456789abcdef"}),
                         (12345, "0123456789abcdef0123456789abcdef"))
        self.assertIsNone(mt.config({"TG_API_ID": "12345", "TG_API_HASH": "0123456789abcdef0123", "TG_MTPROTO": "0"}))

    def test_session_base(self):
        self.assertEqual(mt.session_base("/var/lib/x/tg_user.session"), "/var/lib/x/tg_user")

    def test_to_post(self):
        p = mt.to_post(Msg(7, "Шахед\n курсом  на Кременчук"))
        self.assertEqual(p, {"id": 7, "ts": 1_760_000_000.0, "text": "Шахед курсом на Кременчук"})
        self.assertIsNone(mt.to_post(Msg(8, "")))
        self.assertIsNone(mt.to_post(Msg(9, None)))

    def test_merge(self):
        ch = "test_merge_ch"
        srv.channels.pop(ch, None)
        srv.merge_web(ch, [{"id": 1, "ts": 1, "text": "a"}, {"id": 2, "ts": 2, "text": "b"}])
        t0 = srv.INGEST["new_at"]
        self.assertEqual(srv.merge_mt(ch, [{"id": 3, "ts": 3, "text": "c"}]), 1)   # MTProto випередив t.me
        self.assertGreaterEqual(srv.INGEST["new_at"], t0)
        self.assertEqual(srv.INGEST["new_src"], "mt")
        self.assertEqual(srv.merge_mt(ch, [{"id": 3, "ts": 3, "text": "c (ред.)"}]), 0)   # редагування — не новий
        srv.merge_web(ch, [{"id": 1, "ts": 1, "text": "a"}, {"id": 2, "ts": 2, "text": "b"}])  # t.me ще відстає
        self.assertEqual([p["id"] for p in srv.channels[ch]["posts"]], [1, 2, 3])
        self.assertEqual(srv.channels[ch]["posts"][-1]["text"], "c (ред.)")
        srv.merge_web(ch, [{"id": 1, "ts": 1, "text": "a"}, {"id": 3, "ts": 3, "text": "c2"}])  # 2 видалено в каналі
        self.assertEqual([p["id"] for p in srv.channels[ch]["posts"]], [1, 3])
        srv.channels.pop(ch, None)

    def test_off_without_config(self):
        mt.STATUS.update(state="off")
        s = mt.MtprotoSource(["kpszsu"], lambda c, p: None, log=lambda *a: None)
        s.cfg = None
        s.run()                                  # повертається одразу, нічого не запитує
        self.assertEqual(mt.STATUS["state"], "off")
        self.assertFalse(mt.covers("kpszsu"))


if __name__ == "__main__":
    unittest.main()
