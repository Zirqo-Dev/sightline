"""EST-01 universe log. Offline. Does not open a ticket or change a kill line."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import est_log  # noqa: E402


def log():
    return {
        "est01_check": {"universe": ["ASCEND", "STRIKE"], "excluded": "COCK under 300k"},
        "kill_lines": {
            "ASCEND": "ADA side under 750k",
            "STRIKE": "ADA side under 900k",
            "COCK": "pool ADA side under ~150k ADA",
            "NIGHT": "Grumpy Walrus selling 10k+ NIGHT clips again",
        },
        "cardano_top10": [
            {"ticker": "ASCEND", "holders": 866, "ada_side_depth": 1080610},
            {"ticker": "STRIKE", "holders": 3610, "ada_side_depth": 1193888},
            {"ticker": "COCK", "holders": None, "ada_side_depth": 145111},
            {"ticker": "NIGHT", "holders": None, "ada_side_depth": 3661973},
        ],
        "verdicts": [
            {"ticker": "ASCEND", "launchpad": "established", "pool_ada": 1080610, "holders": 866},
            {"ticker": "STRIKE", "launchpad": "established", "pool_ada": 1193888, "holders": 3610},
            {"ticker": "COCK", "launchpad": "established", "pool_ada": 145111, "holders": None},
            {"ticker": "NIGHT", "launchpad": "established", "pool_ada": 3661973, "holders": None},
        ],
    }


class EstLogTest(unittest.TestCase):
    def test_universe_is_the_named_tokens_and_the_numeric_gate(self):
        names = est_log.universe_tickers(log(), {})
        self.assertEqual(names, ["ASCEND", "STRIKE"])
        self.assertFalse(est_log.numeric_pool_kill("selling 10k+ clips"))
        self.assertTrue(est_log.numeric_pool_kill("ADA side under 750k"))

    def test_top10_without_an_established_verdict_stays_out(self):
        doc = log()
        doc["cardano_top10"].append({"ticker": "EXTRA", "holders": 10, "ada_side_depth": 400000})
        doc["kill_lines"]["EXTRA"] = "ADA side under 300k"
        self.assertNotIn("EXTRA", est_log.universe_tickers(doc, {}))

    def test_a_new_established_name_with_a_pool_kill_is_logged(self):
        doc = log()
        doc["verdicts"].append({"ticker": "BOOK", "launchpad": "established", "pool_ada": 400000, "holders": 12})
        doc["kill_lines"]["BOOK"] = "pool ADA side under 300k"
        self.assertIn("BOOK", est_log.universe_tickers(doc, {}))

    def test_rows_carry_holders_and_kill_lines(self):
        rows = {r["ticker"]: r for r in est_log.rows_for(log(), {})}
        self.assertEqual(rows["ASCEND"]["holders"], 866)
        self.assertEqual(rows["ASCEND"]["kill_line"], "ADA side under 750k")
        self.assertEqual(rows["STRIKE"]["holders_source"], "cardano_top10")
        self.assertFalse(rows["ASCEND"]["buy_signal"])
        self.assertNotIn("COCK", rows)

    def test_record_appends_once_per_timestamp_and_does_not_mutate_the_log(self):
        doc = log()
        before = json.dumps(doc)
        with tempfile.TemporaryDirectory() as d:
            first = est_log.record(d, doc, {}, 1_700_000_000)
            second = est_log.record(d, doc, {}, 1_700_000_000)
            third = est_log.record(d, doc, {}, 1_700_003_600)
            self.assertEqual(first["appended"], 2)
            self.assertEqual(second["appended"], 0)
            self.assertEqual(third["appended"], 2)
            lines = open(os.path.join(d, "est_universe_log.jsonl"), encoding="utf-8").read().strip().splitlines()
            self.assertEqual(len(lines), 4)
            latest = json.load(open(os.path.join(d, "est_universe_latest.json"), encoding="utf-8"))
            self.assertEqual([r["ticker"] for r in latest["rows"]], ["ASCEND", "STRIKE"])
            self.assertNotIn("stake", json.dumps(latest))
        self.assertEqual(json.dumps(doc), before)


if __name__ == "__main__":
    unittest.main()
