"""Missing signals score 0, and the paper-ticket tier follows the count."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.score_picks import score_row


class ScorePicksTest(unittest.TestCase):
    def test_missing_signals_are_zero_and_named(self):
        got = score_row({"whale": None, "holders": None, "above": None, "buzz": None, "liquid": None, "rule_pass": False}, True)
        self.assertEqual(got["score"], [0, 0, 0, 0, 0])
        self.assertEqual(got["tier"], "Skip")
        self.assertTrue(any("not logged" in g or "no X buzz" in g for g in got["gaps"]))

    def test_three_without_a_rule_pass_is_small(self):
        got = score_row({"whale": "buy", "holders": "up", "above": False, "buzz": False, "liquid": True, "rule_pass": False}, True)
        self.assertEqual(got["counted"], 3)
        self.assertEqual(got["tier"], "Small")

    def test_four_and_a_rule_pass_is_standard(self):
        got = score_row({"whale": "buy", "holders": "up", "above": True, "buzz": True, "liquid": True, "rule_pass": True}, True)
        self.assertEqual(got["counted"], 5)
        self.assertEqual(got["tier"], "Standard")

    def test_a_failed_rule_cannot_be_standard(self):
        got = score_row({"whale": "buy", "holders": "up", "above": True, "buzz": True, "liquid": True, "rule_pass": False}, True)
        self.assertEqual(got["tier"], "Skip")

    def test_missing_buzz_feed_is_not_in_the_tier(self):
        got = score_row({"whale": "buy", "holders": "up", "above": True, "buzz": True, "liquid": True, "rule_pass": True}, False)
        self.assertEqual(got["score"][3], 0)
        self.assertTrue(got["buzz_excluded"])
        self.assertEqual(got["counted"], 4)
        self.assertTrue(any("X buzz" in g for g in got["gaps"]))


if __name__ == "__main__":
    unittest.main()
