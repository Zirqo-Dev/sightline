"""Decisions on a seeded fill run match a golden file produced by origin/main.

Logging fields are removed before the compare. check_delay_s is removed from the CSVs.
"""
import json, os, tempfile, unittest
from desk import observe, run
from tests.decision_market import run_scenario, scenario_facts, snapshot

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "decisions_golden.json")
REASONS = ("hard_stop", "trailing_stop", "time_stop", "liquidity_pulled", "no_route_3x")


def produce():
    scenario_facts()
    d = tempfile.mkdtemp()
    calls = run_scenario(run.scan, d)
    return snapshot(d, calls, observe.PUBLIC_KEYS)


class GoldenDecisions(unittest.TestCase):
    def test_fill_run_matches_main(self):
        got = produce()
        whys = {row["why"] for row in got["trades"] if row.get("action") == "sell"}
        for why in REASONS:
            self.assertIn(why, whys)
        self.assertTrue(any(w.startswith("take_profit_") for w in whys))
        with open(FIX) as fh:
            want = json.load(fh)
        for key in ("calls", "tasks", "trades", "state", "health", "positions", "paper"):
            self.assertEqual(got[key], want[key], key)


if __name__ == "__main__":
    unittest.main()
