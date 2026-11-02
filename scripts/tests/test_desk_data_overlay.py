"""Pages publish copies the live feeds that 404 when only main is deployed."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
_spec = importlib.util.spec_from_file_location("build_site", os.path.join(ROOT, "scripts", "build-site.py"))
build_site = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_site)


class DeskDataOverlay(unittest.TestCase):
    def test_overlay_copies_the_three_feeds_and_skips_state(self):
        with tempfile.TemporaryDirectory() as src, tempfile.TemporaryDirectory() as dest:
            for rel, body in {
                "x_buzz.json": {"kind": "x_buzz"},
                "solada-prefilter/whale_ledger.json": {"at": "2026-11-01T23:13:12Z"},
                "solada-prefilter/kill_status.json": {"BOLT": {"hit": False}},
                "solada-prefilter/state.json": {"whale": {"tag": "w-aaaaaaaa"}},
                "solada/briefs/2026-11-01.json": {"date": "2026-11-01"},
                "solada/briefs/index.json": ["2026-11-01"],
                "prices.json": {"quotes": {"ETH": 1}},
            }.items():
                path = os.path.join(src, rel)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(body, fh)
            copied = build_site.overlay_desk_data(dest, src)
            self.assertIn("x_buzz.json", copied)
            self.assertIn("solada-prefilter/whale_ledger.json", copied)
            self.assertIn("solada-prefilter/kill_status.json", copied)
            self.assertIn("prices.json", copied)
            self.assertIn("solada/briefs/2026-11-01.json", copied)
            self.assertNotIn("solada-prefilter/state.json", copied)
            self.assertNotIn("solada/briefs/index.json", copied)
            self.assertFalse(os.path.exists(os.path.join(dest, "solada-prefilter", "state.json")))
            with open(os.path.join(dest, "solada-prefilter", "whale_ledger.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["at"], "2026-11-01T23:13:12Z")


if __name__ == "__main__":
    unittest.main()
