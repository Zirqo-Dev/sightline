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
        self.assertEqual(skips.classify(None, None, {}), ("data", "missing_data"))
        self.assertEqual(skips.classify(None, {}, {}, errors={"dex"}), ("data", "missing_data"))
    def test_no_sol_pair_is_eligibility(self):  # e.g. pumpswap/BTC only
        rc = {"markets": [{"marketType": "meteora_damm_v2", "pubkey": "x"}]}
        self.assertEqual(skips.classify(None, rc, {"routePlan": [1]}), ("eligibility", "no_sol_pair"))
    def test_bonding_curve_is_not_graduated(self):
        rc = {"markets": [{"marketType": "pump_fun", "pubkey": "p"}]}
        self.assertEqual(skips.classify(None, rc, {}), ("safety", "not_graduated"))
        pair = {"dexId": "pumpfun", "pairAddress": "p", "pairCreatedAt": 1, "liquidity": None}
        self.assertEqual(skips.classify(pair, rc, {"routePlan": [1]}), ("safety", "not_graduated"))
    def test_quote_failure_vs_no_route(self):
        p = PAIRS["DUxnHr"]; rc = L("rugcheck_full_DUxnHr.json")
        self.assertEqual(skips.classify(p, rc, None, errors={"jup"}), ("data", "quote_error"))
        self.assertEqual(skips.classify(p, rc, {})[1], "no_route")

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

class Runner(unittest.TestCase):
    def fake(self, fail_rug=False):
        pair = dict(PAIRS["DUxnHr"]); q = L("jup_lite_quote.json"); rc = L("rugcheck_full_DUxnHr.json")
        def get(url, headers=None):
            if "token-profiles" in url: return [{"chainId": "solana", "tokenAddress": "MINT1"}, {"chainId": "base", "tokenAddress": "X"}]
            if "token-boosts" in url: return [{"chainId": "solana", "tokenAddress": "MINT1"}]
            if "/latest/dex/tokens/" in url: return {"pairs": [pair]}
            if "rugcheck" in url:
                if fail_rug: raise OSError("down")
                return rc
            if "jup.ag" in url: self.urls.append((url, headers)); return q
        self.urls = []; return get
    def test_scan_writes_tasks_and_health(self):
        from desk import run
        with tempfile.TemporaryDirectory() as d:
            h = run.scan(d, self.fake(), key=None, now=NOW)
            self.assertEqual((h["runs"], h["checks"], h["ai"], h["mode"]), (1, 1, False, "scan"))
            self.assertIn("JUPITER_API_KEY missing", h["notes"][0]); self.assertIn("lite-api", self.urls[0][0])
            h = run.scan(d, self.fake(fail_rug=True), key="k", now=NOW + 1800)
            self.assertEqual((h["runs"], h["checks"], h["data_failure_rate"]), (2, 2, 0.5))
            self.assertEqual(self.urls[0][1], {"x-api-key": "k"})
            self.assertFalse(os.path.exists(os.path.join(d, "trades.csv")))
            self.assertEqual(sum(1 for _ in open(os.path.join(d, "tasks.csv"))), 3)
            self.assertEqual((len(h["run_times_utc"]), h["max_gap_min"]), (2, 30.0))
            h = run.scan(d, self.fake(), key="k", now=NOW + 1801)  # same slot -> no-op
            self.assertTrue(h["duplicate"]); self.assertEqual(sum(1 for _ in open(os.path.join(d, "tasks.csv"))), 3)
    def test_quote_retries_429_and_maps_no_route(self):
        import io, urllib.error
        from desk import run
        def err(code, body=b""): return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body))
        calls = []
        def get(url, headers=None):
            calls.append(url)
            if len(calls) == 1: raise err(429)
            return {"routePlan": [1]}
        self.assertEqual(run.quote(get, "M", "k", sleep=lambda s: None), {"routePlan": [1]}); self.assertEqual(len(calls), 2)
        def nr(url, headers=None): raise err(400, b'{"errorCode":"COULD_NOT_FIND_ANY_ROUTE"}')
        self.assertEqual(run.quote(nr, "M", "k", sleep=lambda s: None), {})
        def always(url, headers=None): raise err(429)
        with self.assertRaises(urllib.error.HTTPError): run.quote(always, "M", "k", sleep=lambda s: None)
    def test_fill_refused_until_frozen(self):
        from desk import run
        self.assertTrue(C.FROZEN); self.assertEqual(C.CFG_VERSION, "v1-v6.5")
        self.assertTrue(run.FILL_READY); self.assertTrue(run.mode_allowed("fill")); self.assertTrue(run.mode_allowed("scan"))
        old = run.FILL_READY; run.FILL_READY = False
        try: self.assertEqual(run.main(["--mode", "fill", "--data-dir", tempfile.mkdtemp()]), 2)
        finally: run.FILL_READY = old

class ErrorSource(unittest.TestCase):
    def test_header_upgrade_and_error_src(self):
        import csv, os
        from desk import ledger
        d = tempfile.mkdtemp(); p = os.path.join(d, "tasks.csv")
        old = [c for c in ledger.TASK_COLS if c not in ("error_src", "fail_reason")]
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(old); w.writerow(["1"] * len(old))
        ledger.task(d, dict(time=2, result="skip", error_src="rugcheck", fail_reason="rugcheck:429"))
        rows = list(csv.DictReader(open(p)))
        self.assertEqual(list(rows[0].keys()), ledger.TASK_COLS)
        self.assertEqual(rows[0]["error_src"], ""); self.assertEqual(rows[0]["fail_reason"], "")
        self.assertEqual(rows[1]["error_src"], "rugcheck"); self.assertEqual(rows[1]["fail_reason"], "rugcheck:429")
