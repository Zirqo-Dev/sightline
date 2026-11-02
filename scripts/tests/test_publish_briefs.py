"""Daily brief publisher. Offline. No network."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import publish_briefs as pb  # noqa: E402


def _desk(folder):
    os.makedirs(os.path.join(folder, "solada", "briefs"), exist_ok=True)
    with open(os.path.join(folder, "desks.json"), "w", encoding="utf-8") as fh:
        json.dump({"desks": [{
            "id": "solada",
            "log": "solada/log.json",
            "briefs": "solada/briefs/",
            "books": [{"id": "solada", "paper": "solada/paper.json"}],
        }]}, fh)
    with open(os.path.join(folder, "solada", "paper.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "unit": "ADA",
            "open": [],
            "closed": [
                {"result": {"pnl": -10, "pct": -5}},
                {"result": {"pnl": 4, "pct": 2}},
            ],
        }, fh)


def _log(folder, headline, asof="2026-11-01T19:00:00-05:00"):
    with open(os.path.join(folder, "solada", "log.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "asof": asof,
            "headline": headline,
            "learned_or_changed": ["Holder baseline logged for ASCEND (866)."],
        }, fh)


class PublishBriefs(unittest.TestCase):
    def test_missing_day_is_written_from_the_log_and_a_bot_brief_is_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            _desk(folder)
            _log(folder, "First EST-01 check. Wallet stake1u9xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx stays off the page.")
            bot = os.path.join(folder, "solada", "briefs", "2026-10-19.json")
            with open(bot, "w", encoding="utf-8") as fh:
                json.dump({"schema_version": 2, "desk": "solada", "date": "2026-10-19", "headline": "Bot wrote this."}, fh)
            results = pb.publish(folder)
            self.assertEqual(results["solada"], "wrote")
            with open(os.path.join(folder, "solada", "briefs", "2026-11-01.json"), encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["schema_version"], 2)
            self.assertEqual(doc["date"], "2026-11-01")
            self.assertNotIn("stake1", doc["headline"])
            self.assertEqual(doc["generator"], "publish_briefs")
            self.assertEqual(doc["scoreboard"]["wins"], 1)
            self.assertEqual(doc["scoreboard"]["closed"], 2)
            self.assertEqual(doc["highlights"], ["Holder baseline logged for ASCEND (866)."])
            with open(os.path.join(folder, "solada", "briefs", "index.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh), ["2026-10-19", "2026-11-01"])
            again = pb.publish(folder)
            self.assertEqual(again["solada"], "kept")
            with open(bot, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["headline"], "Bot wrote this.")

    def test_generated_brief_refreshes_when_the_headline_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            _desk(folder)
            _log(folder, "Morning note")
            self.assertEqual(pb.publish(folder)["solada"], "wrote")
            _log(folder, "Evening note")
            self.assertEqual(pb.publish(folder)["solada"], "refreshed")
            with open(os.path.join(folder, "solada", "briefs", "2026-11-01.json"), encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["headline"], "Evening note")

    def test_founder_wording_is_stripped(self):
        self.assertNotIn("founder", pb.clean("the founder wallet moved").lower())


if __name__ == "__main__":
    unittest.main()
