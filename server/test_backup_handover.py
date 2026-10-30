"""30.10 20:08: під час деплою (основний сервер недоступний ~25 с) GitHub-резерв (live.yml, старий код) надіслав власне
повідомлення «НЕБЕЗПЕКА… В області, ризик для міста», яке основний не знав і не видаляв — у чаті дві тривоги."""
import os, shutil, sys, tempfile, unittest
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import live  # noqa: E402
import tg_notify as tn  # noqa: E402
from test_tg_live import CFG, FakeApi, st  # noqa: E402


class GraceTest(unittest.TestCase):
    def setUp(self):
        os.environ["PRIMARY_URL"] = "https://primary.example"
        live._prim.update(at=0.0, alive=False, down_since=0.0)

    def tearDown(self):
        os.environ.pop("PRIMARY_URL", None)

    def test_short_restart_keeps_backup_silent(self):
        ok = lambda u: {"ok": True, "notify": True, "now": T[0]}        # noqa: E731
        def down(u):
            raise OSError("refused")
        T = [1_000_000.0]
        self.assertTrue(live.primary_notifies(ok, T[0]))
        for dt in (61, 122, 183, 244):                                    # перезапуск / деплой < 5 хв
            T[0] = 1_000_000.0 + dt
            self.assertTrue(live.primary_notifies(down, T[0]), dt)
        T[0] = 1_000_000.0 + 61 + live.PRIMARY_GRACE_S + 1                # довга відмова → резерв
        self.assertFalse(live.primary_notifies(down, T[0]))
        T[0] += 61
        self.assertTrue(live.primary_notifies(ok, T[0]))                  # повернувся — резерв мовчить

    def test_stale_health_counts_as_down(self):
        T = 2_000_000.0
        self.assertTrue(live.primary_notifies(lambda u: {"ok": True, "notify": True, "now": T - 600}, T))
        self.assertFalse(live.primary_notifies(lambda u: {"ok": True, "notify": True, "now": T - 600}, T + live.PRIMARY_GRACE_S + 61))


class RetireTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_retire_deletes_own_messages(self):
        a = FakeApi()
        n = tn.Notifier(self.d, CFG, token="x", chat="1", api=a)
        n.tick(now=1000, st=st("red", scope="district"))
        self.assertEqual(len(a.alive), 1)
        a.fail["deleteMessage"] = [tn.TgError(0, "URLError")]
        self.assertFalse(n.retire())                                      # мережа — повторимо
        self.assertEqual(len(a.alive), 1)
        self.assertTrue(n.retire())
        self.assertEqual(len(a.alive), 0)
        self.assertFalse(n.mem["active"])
        n2 = tn.Notifier(self.d, CFG, token="x", chat="1", api=a)         # стан збережено
        self.assertEqual(n2.mem["episode"], [])


if __name__ == "__main__":
    unittest.main()
