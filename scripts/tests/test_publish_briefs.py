"""Daily briefs are derived from the log, and a hand-written day is left alone."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.publish_briefs import brief_from_log, publish, scrub


class PublishBriefsTest(unittest.TestCase):
    def test_scrub_drops_handles_addresses_and_the_old_bot_name(self):
        text = scrub("Snek Sentinel saw @someone buy stake1u9y0z8k7h6g5f4e3d2c1b0a9z8y7x6w5v4u3t2s1r0q addr1q" + ("x" * 90))
        self.assertNotIn("Snek Sentinel", text)
        self.assertIn("Sentinel", text)
        self.assertNotIn("@someone", text)
        self.assertNotIn("stake1", text)
        self.assertNotIn("addr1", text)

    def test_brief_uses_the_toronto_date_and_does_not_copy_wallets(self):
        log = {
            "asof": "2026-11-01T19:00:00-05:00",
            "headline": "ASCEND cleared the pool test.",
            "learned_or_changed": ["Holder baseline logged."],
            "founder_whale": {"address": "addr1qshouldnotappear"},
            "whales": [{"codename": "Grumpy Walrus", "stake_prefix": "w-f3186187"}],
        }
        paper = {"unit": "ADA", "open": [], "closed": [{"result": {"pnl": -10, "pct": -5}}]}
        doc = brief_from_log(log, paper, "solada")
        self.assertEqual(doc["date"], "2026-11-01")
        self.assertEqual(doc["desk"], "solada")
        self.assertTrue(doc["generated"])
        self.assertEqual(doc["scoreboard"]["pnl"], -10)
        self.assertNotIn("founder_whale", doc)
        self.assertNotIn("w-f3186187", json.dumps(doc))

    def test_publish_skips_a_hand_written_day_and_writes_a_manifest(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "solada", "briefs"))
            hand = {
                "schema_version": 2,
                "desk": "solada",
                "date": "2026-10-19",
                "headline": "Hand written.",
            }
            with open(os.path.join(d, "solada", "briefs", "2026-10-19.json"), "w") as fh:
                json.dump(hand, fh)
            log = {
                "asof": "2026-11-01T19:00:00-05:00",
                "headline": "Today's run.",
                "learned_or_changed": ["Changed: a whale codename was added."],
                "history": [
                    {"asof": "2026-10-19T19:00:00-04:00", "headline": "Should not replace the hand file."},
                    {"asof": "2026-10-27T20:00:00-04:00", "headline": "A later day from history."},
                ],
            }
            with open(os.path.join(d, "solada", "log.json"), "w") as fh:
                json.dump(log, fh)
            with open(os.path.join(d, "solada", "paper.json"), "w") as fh:
                json.dump({"unit": "ADA", "open": [], "closed": []}, fh)
            found = publish(d)
            self.assertIn("2026-11-01", found["solada"]["written"])
            self.assertIn("2026-10-27", found["solada"]["written"])
            self.assertNotIn("2026-10-19", found["solada"]["written"])
            kept = json.load(open(os.path.join(d, "solada", "briefs", "2026-10-19.json")))
            self.assertEqual(kept["headline"], "Hand written.")
            manifest = json.load(open(os.path.join(d, "solada", "briefs", "manifest.json")))
            self.assertEqual(manifest["dates"], ["2026-10-19", "2026-10-27", "2026-11-01"])
            self.assertTrue(manifest["generated_at"].endswith("Z"))


if __name__ == "__main__":
    unittest.main()
