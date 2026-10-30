import json
import os
import tempfile
import unittest

import analyzer as an
import tracker as tk


class ChannelRel(unittest.TestCase):
    def test_override_clamped_official_kept(self):
        cfg = [{"id": "a", "weight": 0.9, "kind": "monitor"}, {"id": "B", "weight": 0.6, "kind": "monitor"},
               {"id": "off", "weight": 1.0, "kind": "official"}, {"id": "none", "weight": 0.7}]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"channels": {"a": {"weight": 0.5, "sigma_mult": 1.2}, "b": {"weight": 0.65, "sigma_mult": 1.0},
                                    "off": {"weight": 0.7, "sigma_mult": 0.9}}}, f)
        try:
            sig = an.load_reliability(cfg, f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual([c["weight"] for c in cfg], [0.75, 0.65, 1.0, 0.7])
        self.assertEqual(sig["off"], 0.9)

    def test_missing_file(self):
        self.assertEqual(an.load_reliability([], "/nonexistent.json"), {})

    def test_tracker_sigma(self):
        old = tk._CH_SIGMA
        tk._CH_SIGMA = {"x": 1.3}
        try:
            self.assertEqual(tk.ch_sigma("tg:X"), 1.3)
            self.assertEqual(tk.ch_sigma("tg:y"), 1.0)
            self.assertEqual(tk.ch_sigma(None), 1.0)
        finally:
            tk._CH_SIGMA = old


if __name__ == "__main__":
    unittest.main()
