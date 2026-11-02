"""X buzz shortlist. Offline. Stays inside the existing row budget."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import x_buzz_shortlist as XB  # noqa: E402


UNIVERSE = ["ASCEND", "STRIKE"]
WHALE = ["ASCEND", "ATLAS", "COCK", "NIGHT", "SNEK", "SOLADA", "STRIKE", "ZZZ", "AAA", "BBB", "CCC", "DDD"]
PREVIOUS = ["ASCEND", "NIGHT", "STRIKE", "MIN", "SNEK", "COCK", "SOLADA"]


class ShortlistTest(unittest.TestCase):
    def test_pins_the_universe_and_stays_inside_ten_rows(self):
        this_run, cursor, over, deferred, continuity = XB.select(UNIVERSE, WHALE, PREVIOUS, 0)
        self.assertFalse(over)
        self.assertEqual(deferred, [])
        self.assertLessEqual(len(this_run), XB.CARDANO_BUDGET)
        self.assertEqual(this_run[:2], ["ASCEND", "STRIKE"])
        self.assertIn("COCK", this_run)
        self.assertTrue(continuity)
        self.assertNotIn("MIN", this_run)

    def test_rotation_covers_the_rest_of_the_ledger(self):
        first, cursor, _, _, _ = XB.select(UNIVERSE, WHALE, PREVIOUS, 0)
        second, _, _, _, _ = XB.select(UNIVERSE, WHALE, PREVIOUS, cursor)
        self.assertNotEqual(first, second)
        self.assertEqual(second[:2], ["ASCEND", "STRIKE"])
        rotated = [n for n in second if n not in first]
        self.assertTrue(rotated)

    def test_a_full_ledger_is_the_candidate_set(self):
        ledger = {"positions": {name: 1 for name in WHALE}, "rows": [{"token": "BLUE BULL"}, {"token": "ADA"}, {"token": "6e917b+fda00259"}]}
        log = {"est01_check": {"universe": UNIVERSE}}
        buzz = {"tokens": [{"ticker": t, "chain": "cardano"} for t in PREVIOUS] + [{"ticker": "unknown", "chain": "solana"}]}
        doc = XB.build(log, {}, ledger, buzz, None, 1_700_000_000)
        self.assertEqual(len(doc["this_run"]), XB.CARDANO_BUDGET)
        self.assertIn("COCK", doc["candidates"])
        self.assertIn("ASCEND", doc["candidates"])
        self.assertIn("STRIKE", doc["candidates"])
        self.assertEqual(doc["solana_this_run"], [])
        self.assertTrue(doc["skipped"])
        blob = json.dumps(doc)
        self.assertNotIn("@", blob)
        self.assertNotIn("stake1", blob)
        self.assertFalse(doc["buy_signal"])

    def test_record_advances_the_cursor_on_disk(self):
        ledger = {"positions": {name: 1 for name in WHALE}}
        log = {"est01_check": {"universe": UNIVERSE}}
        with tempfile.TemporaryDirectory() as d:
            json.dump(ledger, open(os.path.join(d, "whale_ledger.json"), "w"))
            buzz = os.path.join(d, "x_buzz.json")
            json.dump({"tokens": [{"ticker": t, "chain": "cardano"} for t in PREVIOUS]}, open(buzz, "w"))
            first = XB.record(d, log, {}, 1_700_000_000, buzz_path=buzz)
            second = XB.record(d, log, {}, 1_700_003_600, buzz_path=buzz)
            self.assertIn("ASCEND", first["this_run"])
            self.assertIn("STRIKE", first["this_run"])
            self.assertIn("ASCEND", second["this_run"])
            self.assertLessEqual(len(first["this_run"]), 10)
            self.assertNotEqual(first["this_run"], second["this_run"])
            self.assertNotEqual(second["cursor"], first["cursor"])


if __name__ == "__main__":
    unittest.main()
