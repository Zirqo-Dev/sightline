"""Paper-test checks and the display-only risk panel. No ticket tiers."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.score_picks import change_line, est_checks, paper_status, risk_panel, row, sw_checks


ASCEND = {
    "ticker": "ASCEND",
    "pct_from_line": 43.2,
    "whale": "Grumpy Walrus",
    "whale_net_72h_ada": 28921,
    "holders_7d": "unverifiable (no logged count 7 days ago; baseline 866 today)",
    "pool_7d_pct": 8.3,
}
STRIKE = {
    "ticker": "STRIKE",
    "pct_from_line": 32.1,
    "whale": "Wobbly Kiwi",
    "whale_net_72h_ada": 52447,
    "holders_7d": "unverifiable (no logged count 7 days ago)",
    "pool_7d_pct": -2.7,
}


class ScorePicksTest(unittest.TestCase):
    def test_ascend_and_strike_are_three_of_four_on_holders(self):
        for src in (ASCEND, STRIKE):
            checks = est_checks(src)
            self.assertEqual(len(checks), 4)
            self.assertEqual(sum(c["pass"] for c in checks), 3)
            failed = [c["id"] for c in checks if not c["pass"]]
            self.assertEqual(failed, ["holders"])

    def test_pool_floor_is_not_a_fifth_check(self):
        ids = [c["id"] for c in est_checks(ASCEND)]
        self.assertEqual(ids, ["kill", "whale", "holders", "liquidity"])

    def test_whale_floor_and_liquidity_floor(self):
        low = dict(ASCEND, whale_net_72h_ada=999, pool_7d_pct=-10.1, pct_from_line=24.9)
        checks = {c["id"]: c["pass"] for c in est_checks(low)}
        self.assertFalse(checks["whale"])
        self.assertFalse(checks["liquidity"])
        self.assertFalse(checks["kill"])
        exact = dict(ASCEND, whale_net_72h_ada=1000, pool_7d_pct=-10, holders_7d=0)
        checks = {c["id"]: c["pass"] for c in est_checks(exact)}
        self.assertTrue(checks["whale"])
        self.assertTrue(checks["liquidity"])
        self.assertTrue(checks["holders"])

    def test_buzz_is_not_a_check(self):
        with_buzz = dict(ASCEND, buzz=True)
        without = dict(ASCEND, buzz=False)
        self.assertEqual(est_checks(with_buzz), est_checks(without))

    def test_sw01_pending_gate_fails_each_check(self):
        checks = sw_checks({"ticker": "ANAKIN", "bonded": False, "status": "pending_gate", "gate_check_due": "launch+24h"})
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(not c["pass"] for c in checks))
        self.assertIn("unverifiable", checks[1]["detail"])

    def test_status_lists_the_near_misses_and_no_size(self):
        log = {"est01_check": {"universe": ["ASCEND", "STRIKE"], "rows": [ASCEND, STRIKE]}}
        paper = {"tests": [
            {"id": "EST-01", "tickets": [], "check_log": [{"checked": ["ASCEND", "STRIKE"]}]},
            {"id": "SW-01", "tickets": [], "comparison_group": [
                {"ticker": "ANAKIN", "bonded": False, "status": "pending_gate", "gate_check_due": "launch+24h"},
            ]},
        ]}
        status = paper_status(log, paper, "cardano")
        self.assertEqual(status["label"], "Not a pick, not a ranking")
        self.assertIn("100 ADA", status["ticket"])
        self.assertEqual([r["ticker"] for r in status["est01"]], ["ASCEND", "STRIKE"])
        self.assertEqual(status["est01"][0]["missing"], ["holder trend"])
        self.assertEqual(status["est01"][0]["passed"], 3)
        self.assertEqual(status["sw01"][0]["ticker"], "ANAKIN")
        self.assertNotIn("score", status)
        self.assertNotIn("tier", status)

    def test_lines_do_not_name_a_size_or_buzz(self):
        facts = {"whale": None, "holders": None, "above": None, "swarm": False, "why": "Tiny lottery ticket", "change": "Standard ticket"}
        item = row("X", facts)
        self.assertNotIn("lottery", item["why"].lower())
        self.assertNotIn("standard", item["change"].lower())
        self.assertNotIn("tier", item)
        self.assertNotIn("score", item)
        self.assertNotIn("buzz", change_line({"holders": None, "above": None, "whale": None, "swarm": False}).lower())
        panel = risk_panel({"whale": "buy", "whale_name": "Grumpy Walrus", "holders": "up", "holders_n": 866, "above": True, "dist": "+43.2% vs line", "swarm": True})
        self.assertTrue(panel["swarm"])
        self.assertNotIn("score", panel)
        self.assertIn("Grumpy Walrus", panel["whale"])


if __name__ == "__main__":
    unittest.main()
