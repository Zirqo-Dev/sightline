"""The Pages build publishes the three feeds at the root and under solada/."""
import importlib.util
import json
import os
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_build():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build-site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FeedPublishTests(unittest.TestCase):
    def test_aliases_and_scrub(self):
        mod = load_build()
        platform = next(iter(mod.platform_addresses()))
        stake = "stake1" + ("a" * 53)
        raw = json.dumps({"whale_tag": "w-f3186187", "note": stake, "curve": platform}).encode()
        out = mod.scrub_public_feed(raw)
        self.assertNotIn(stake.encode(), out)
        self.assertIn(b"w-", out)
        self.assertIn(platform.encode(), out)
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp)
            mod.publish_aliases(dest, "solada-prefilter/whale_ledger.json", out)
            mod.publish_aliases(dest, "solada-prefilter/kill_status.json", out)
            mod.publish_aliases(dest, "x_buzz.json", out)
            for rel in (
                "whale_ledger.json",
                "kill_status.json",
                "solada/whale_ledger.json",
                "solada/kill_status.json",
                "solada/x_buzz.json",
            ):
                self.assertTrue((dest / rel).is_file(), rel)
                self.assertNotIn(b"stake1", (dest / rel).read_bytes())


if __name__ == "__main__":
    unittest.main()
