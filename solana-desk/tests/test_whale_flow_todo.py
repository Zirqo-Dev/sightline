"""The Solana whale-flow ledger is a TODO. The frozen path does not import it."""
import os
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DESK = os.path.join(ROOT, "desk")
FROZEN = (
    "run.py",
    "fills.py",
    "exits.py",
    "skips.py",
    "observe.py",
    "books.py",
    "ledger.py",
    "config.py",
)


class WhaleFlowTodoTest(unittest.TestCase):
    def test_module_is_not_wired_and_says_why(self):
        import desk.whale_flow as wf
        self.assertFalse(wf.enabled())
        text = open(os.path.join(DESK, "whale_flow.py"), encoding="utf-8").read()
        self.assertIn("TODO", text)
        self.assertIn("not feasible", text.lower())
        verdict = open(os.path.join(ROOT, "specs", "day14_verdict.py"), encoding="utf-8").read()
        self.assertNotIn("whale_flow", verdict)
        for name in FROZEN:
            body = open(os.path.join(DESK, name), encoding="utf-8").read()
            self.assertNotIn("whale_flow", body, name)


if __name__ == "__main__":
    unittest.main()
