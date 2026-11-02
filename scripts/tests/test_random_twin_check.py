"""Random-twin check: lot building, exit replay, and the power formula. No desk-data."""
import importlib.util
import os
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(REPO, "scripts", "analysis", "random_twin_check.py")
spec = importlib.util.spec_from_file_location("random_twin_check", PATH)
twin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(twin)


def _buy(book, mint, ts, model="v3-exit-aware"):
    return dict(book=book, mint=mint, action="buy", time=str(ts), frac="1", pnl="", cost_model=model, why="lead")


def _sell(book, mint, ts, frac, pnl, model="v3-exit-aware", why="time_stop"):
    return dict(book=book, mint=mint, action="sell", time=str(ts), frac=str(frac), pnl=str(pnl),
                cost_model=model, why=why)


class LotTests(unittest.TestCase):
    def test_partial_sells_make_one_post_v3_close(self):
        rows = [
            _buy("F", "MintA", 10),
            _sell("F", "MintA", 20, 0.333333, -0.05, why="take_profit_40"),
            _sell("F", "MintA", 30, 0.666667, 0.20, why="trailing_stop"),
            _buy("R", "MintB", 10),
            _sell("R", "MintB", 40, 1, -0.2, model=""),
        ]
        built = twin.build_closes(rows)
        self.assertEqual(len(built), 2)
        post = [c for c in built if c["post_v3"]]
        self.assertEqual(len(post), 1)
        self.assertEqual(post[0]["book"], "F")
        self.assertAlmostEqual(post[0]["pnl"], 0.15, places=9)
        self.assertAlmostEqual(post[0]["ret"], 0.15 / twin.C.TICKET_SOL, places=9)

    def test_mixed_cost_model_is_not_post_v3(self):
        rows = [
            _buy("S", "MintC", 10, model=""),
            _sell("S", "MintC", 20, 0.5, 0.1, model=""),
            _sell("S", "MintC", 30, 0.5, 0.1, model="v3-exit-aware"),
        ]
        built = twin.build_closes(rows)
        self.assertEqual(len(built), 1)
        self.assertTrue(built[0]["mixed"])
        self.assertFalse(built[0]["post_v3"])


class ReplayTests(unittest.TestCase):
    def test_hard_stop_and_liquidity_order(self):
        # SOL unchanged, price -30%: hard stop, and the haircut makes it worse than -25%.
        path = [
            (0, 1.0, 100.0, 1_000_000.0),
            (1800, 0.70, 100.0, 1_000_000.0),
        ]
        got = twin.replay(path, 0)
        self.assertEqual(got["why"], "hard_stop")
        self.assertFalse(got["censored"])
        self.assertLess(got["ret"], -0.25)
        self.assertGreater(got["ret"], -0.50)

        # SOL through the 40% pull line wins over the price stop.
        pulled = [
            (0, 1.0, 100.0, 1_000_000.0),
            (1800, 0.70, 50.0, 1_000_000.0),
        ]
        got = twin.replay(pulled, 0)
        self.assertEqual(got["why"], "liquidity_pulled")
        self.assertLess(got["ret"], -0.25)

    def test_time_stop_and_censor(self):
        flat = [
            (0, 1.0, 100.0, 1_000_000.0),
            (121 * 60, 1.0, 100.0, 1_000_000.0),
        ]
        got = twin.replay(flat, 0)
        self.assertEqual(got["why"], "time_stop")
        self.assertLess(got["ret"], 0)

        short = [
            (0, 1.0, 100.0, 1_000_000.0),
            (1800, 1.02, 100.0, 1_000_000.0),
        ]
        got = twin.replay(short, 0)
        self.assertEqual(got["why"], "censored_last_mark")
        self.assertTrue(got["censored"])

    def test_broken_token_reserve_closes_at_zero(self):
        path = [
            (0, 1.0, 100.0, 1_000_000.0),
            (1800, 1e6, 10.0, 1e-6),
        ]
        got = twin.replay(path, 0)
        self.assertEqual(got["why"], "liquidity_pulled_unpriced")
        self.assertTrue(got["unpriced"])
        self.assertLess(got["ret"], -0.9)
        self.assertGreater(got["ret"], -1.3)

    def test_horizon_cap_stops_before_a_later_stop(self):
        path = [
            (0, 1.0, 100.0, 1_000_000.0),
            (1800, 1.01, 100.0, 1_000_000.0),
            (3600, 0.50, 100.0, 1_000_000.0),
        ]
        full = twin.replay(path, 0)
        capped = twin.replay(path, 0, until_t=1800)
        self.assertEqual(full["why"], "hard_stop")
        self.assertEqual(capped["why"], "censored_last_mark")
        self.assertTrue(capped["censored"])

    def test_healthy_rally_is_a_winner(self):
        path = [
            (0, 1.0, 100.0, 1_000_000.0),
            (1800, 5.0, 120.0, 800_000.0),
            (3600, 4.0, 110.0, 850_000.0),
        ]
        got = twin.replay(path, 0)
        self.assertGreater(got["ret"], 0)
        self.assertFalse(got["unpriced"])


class StatTests(unittest.TestCase):
    def test_power_formula_matches_the_normal_quantile(self):
        import math
        import statistics
        sd, delta = 0.60, 0.14
        z = statistics.NormalDist().inv_cdf(0.975) + statistics.NormalDist().inv_cdf(0.80)
        expect = int(math.ceil((z * sd / delta) ** 2 - 1e-9))
        self.assertEqual(twin.closes_needed(sd, delta, sided="two"), expect)
        self.assertGreater(twin.closes_needed(sd, 0.14), twin.closes_needed(sd, 0.25))
        self.assertGreater(
            twin.closes_needed(sd, 0.14, sided="two"),
            twin.closes_needed(sd, 0.14, sided="one"),
        )

    def test_overlap_of_the_same_bin_is_one(self):
        self.assertAlmostEqual(twin.overlap_coefficient([-0.2, -0.2], [-0.25, -0.15]), 1.0, places=9)
        self.assertAlmostEqual(twin.overlap_coefficient([-1.2], [5.0]), 0.0, places=9)

    def test_bootstrap_of_a_constant_is_the_constant(self):
        import random
        lo, hi = twin.bootstrap_ci([0.1, 0.1, 0.1], 200, random.Random(1))
        self.assertAlmostEqual(lo, 0.1, places=9)
        self.assertAlmostEqual(hi, 0.1, places=9)


if __name__ == "__main__":
    unittest.main()
