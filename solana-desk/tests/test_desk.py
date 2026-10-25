import json, os, tempfile, unittest
from desk import config as C, skips, books, exits, ledger, report
FX = os.path.join(os.path.dirname(__file__), "fixtures")
def L(n): return json.load(open(os.path.join(FX, n)))
PAIRS = {p["baseToken"]["address"][:6]: p for p in L("dex_pairs.json")}
NOW = 1793383000  # frozen ~fixture time (Oct 24 2026)

class Skips(unittest.TestCase):
    def test_pumpswap_lp_structural(self):
        rc = L("rugcheck_full_DUxnHr.json")
        self.assertIsNone(skips.lp_check(rc, PAIRS["DUxnHr"]["pairAddress"]))
    def test_bonding_curve_not_graduated(self):
        rc = L("rugcheck_full_7qLou7.json")
        self.assertEqual(skips.lp_check(rc, PAIRS["7qLou7"]["pairAddress"]), "not_graduated")
        self.assertEqual(skips.safety_check(PAIRS["7qLou7"], rc, NOW), "not_graduated")
    def test_sol_side_floor(self):
        self.assertEqual((C.SKIP_IF["min_quote_liquidity_sol"], C.SKIP_IF["min_liquidity_usd"]), (10, None))
        rc = L("rugcheck_full_DUxnHr.json"); p = dict(PAIRS["DUxnHr"], pairCreatedAt=(NOW - 3600) * 1000)
        self.assertEqual(p["liquidity"]["quote"] < 10, True)
        self.assertEqual(skips.safety_check(p, rc, NOW), "thin_pool")  # $83k usd but 6.3 SOL side
    def test_ticket_share_5pct_sol_side(self):
        q = dict(L("jup_lite_quote.json"), priceImpactPct="0.01")
        base = dict(PAIRS["DUxnHr"], txns={"h1": {"sells": 5}})
        self.assertIsNone(skips.execution_check(dict(base, liquidity={"quote": 10.0}), q))
        self.assertEqual(skips.execution_check(dict(base, liquidity={"quote": 9.9}), q), "ticket_share")
    def test_holder_source(self): self.assertEqual(C.HOLDER_SOURCE, "rugcheck")
    def test_dlmm_lp_not_applicable(self):
        rc = L("rugcheck_full_5dvXTZ.json")
        dlmm = next(m["pubkey"] for m in rc["markets"] if m["marketType"] == "meteoraDlmm")
        self.assertIsNone(skips.lp_check(rc, dlmm))
    def test_damm_unlocked_fails(self):
        rc = L("rugcheck_full_SNS8DJ.json")
        self.assertEqual(skips.lp_check(rc, "9PoWpAcZeUWufRHCBazk7yUbj9d8wkVYsQDU38Ep8trY"), "lp_unlocked")
    def test_top10_excludes_curve(self):
        rc = L("rugcheck_full_7qLou7.json")
        raw = sum(h["pct"] for h in rc["topHolders"][:10])
        self.assertGreater(raw, 80); self.assertLess(skips.top10_ex_pool(rc), 35)
    def test_score_normalised_used(self):
        rc = L("rugcheck_full_SNS8DJ.json")
        self.assertGreater(rc["score"], 1000); self.assertEqual(rc["score_normalised"], 58)
    def test_impact_fraction(self):
        q = L("jup_lite_quote.json"); self.assertAlmostEqual(skips.impact_pct(q), 4.914, places=2)
    def test_execution_same_for_all(self):
        q = L("jup_lite_quote.json"); p = PAIRS["DUxnHr"]
        self.assertEqual(skips.execution_check(p, q), "impact")
    def test_missing_is_skip(self):
        self.assertEqual(skips.classify(None, {}, {}), ("data", "missing_data"))

class Books(unittest.TestCase):
    def mk(self): return {k: books.Book(k) for k in "FRS"}
    def test_r_only_when_f_fills(self):
        b = self.mk(); out = books.select_fills([("a", "safety", "x"), ("b", "safety", "y")], b, 1)
        self.assertEqual(out["F"], []); self.assertEqual(out["R"], [])
    def test_r_capped(self):
        b = self.mk(); leads = [("f1", None, None)] + [(f"s{i}", "safety", "x") for i in range(10)]
        out = books.select_fills(leads, b, 7)
        self.assertEqual(len(out["F"]), 1); self.assertLessEqual(len(out["R"]), 2); self.assertLessEqual(len(out["S"]), 2)
    def test_seed_reproducible(self):
        leads = [("f1", None, None)] + [(f"s{i}", "safety", "x") for i in range(10)]
        self.assertEqual(books.select_fills(leads, self.mk(), 3), books.select_fills(leads, self.mk(), 3))

class Exits(unittest.TestCase):
    def pos(self): return exits.new_position(1.0, 100.0, 0)
    def test_hard_stop(self): self.assertEqual(exits.check(self.pos(), 0.7, 100, 60)[0][2], "hard_stop")
    def test_ladder_and_trail(self):
        p = self.pos(); a = exits.check(p, 1.45, 100, 60); self.assertEqual(a[0][2], "take_profit_40")
        a = exits.check(p, 1.0, 100, 120); self.assertEqual(a[-1][2], "trailing_stop")
    def test_stale_not_minus_100(self):
        p = self.pos(); a = exits.check(p, None, None, 60, "temp_error"); self.assertEqual(a[0][0], "mark"); self.assertEqual(p["stale"], 1)
    def test_no_route_three_checks(self):
        p = self.pos()
        self.assertEqual(exits.check(p, None, None, 60, "no_route")[0][0], "mark")
        self.assertEqual(exits.check(p, None, None, 70, "no_route")[0][0], "mark")
        self.assertEqual(exits.check(p, None, None, 80, "no_route")[0][2], "no_route_3x")
    def test_route_back_resets(self):
        p = self.pos(); exits.check(p, None, None, 60, "no_route"); exits.check(p, 1.0, 100, 70)
        self.assertEqual(p["no_route_count"], 0)
    def test_time_stop(self): self.assertEqual(exits.check(self.pos(), 1.02, 100, 121*60)[0][2], "time_stop")
    def test_liq_pull(self): self.assertEqual(exits.check(self.pos(), 1.0, 50, 60)[0][2], "liquidity_pulled")

class Ledger(unittest.TestCase):
    def test_files_and_lock(self):
        d = tempfile.mkdtemp()
        ledger.task(d, dict(run_id=1, book="F", result="skip")); ledger.trade(d, dict(book="F", sol=0.5))
        h = ledger.health(d, 100, 144, 1000, 5, 150)
        self.assertTrue(h["void_day"]); self.assertFalse(h["ai"])
        self.assertTrue(os.path.exists(os.path.join(d, "tasks.csv")))
        with ledger.Lock(os.path.join(d, "desk.lock")):
            with self.assertRaises(RuntimeError):
                with ledger.Lock(os.path.join(d, "desk.lock")): pass

class Decide(unittest.TestCase):
    def test_rules(self):
        self.assertEqual(report.decide(14, [-0.3]*20, [0]*20)["decision"], "STOP_CLEAR_FAILURE")
        self.assertEqual(report.decide(14, [-0.3]*19, [0])["decision"], "CONTINUE")
        self.assertEqual(report.decide(28, [0.1]*19, [0])["decision"], "STOP_FILTERS_TOO_STRICT")
        self.assertEqual(report.decide(28, [0.0]*20, [0.1]*20)["decision"], "KILL")
        self.assertEqual(report.decide(28, [0.1]*20, [0.0]*20)["decision"], "PASS")
        self.assertEqual(report.decide(28, [-0.05]*20, [-0.1]*20)["decision"], "KILL_LOSES_MONEY")
    def test_config(self):
        self.assertEqual((C.TICKET_SOL, C.MIN_F_CLOSES, C.EARLY_STOP_EXP), (0.5, 20, -0.15))

if __name__ == "__main__": unittest.main()
