import csv, json, os, tempfile, unittest
from desk import config as C, fills, run

T0 = 1793500000  # fixed test time
TOK = 1_000_000_000  # raw tokens bought for 0.5 SOL -> entry px 0.5 lamports/unit

def M(i): return f"Mint{i:02d}" + "x" * 30 + f"{i:02d}pp"
def lead(i, stype=None, reason=None, out=TOK, liq=100.0):
    return dict(mint=M(i), stype=stype, reason=reason, quote={"outAmount": str(out), "priceImpactPct": "0.01", "routePlan": [1]},
                pair={"liquidity": {"quote": liq}, "baseToken": {"symbol": f"T{i}"}})

class Market:
    """Fake Jupiter sell quotes: ratio[mint] = mark/entry; 'noroute' / 'err' statuses.
    impact[mint] is an optional Jupiter priceImpactPct fraction string."""
    def __init__(self): self.ratio = {}; self.status = {}; self.liq = {}; self.impact = {}
    def sell(self, m, amount):
        st = self.status.get(m)
        if st == "err": raise RuntimeError("timeout")
        if st == "noroute": return {}
        q = {"outAmount": str(int(amount * 0.5 * self.ratio.get(m, 1.0))), "routePlan": [1]}
        if m in self.impact: q["priceImpactPct"] = self.impact[m]
        return q
    def liquidity(self, m): return self.liq.get(m, 100.0)

def trades(d):
    p = os.path.join(d, "trades.csv")
    return list(csv.DictReader(open(p))) if os.path.exists(p) else []

class Fills(unittest.TestCase):
    def setUp(self): self.d = tempfile.mkdtemp(); self.mk = Market(); self.st = dict(checks=12, data_failures=0)
    def step(self, now, leads=(), st=None, run_id=None):
        return fills.step(self.d, now, run_id or f"R{now}", now // 60, list(leads), st or self.st, self.mk.sell, self.mk.liquidity)
    def state(self): return json.load(open(os.path.join(self.d, "positions.json")))

    def test_entry_fixed_ticket_and_books(self):
        r = self.step(T0, [lead(1), lead(2, "safety", "risk_score"), lead(3, "execution", "impact")])
        s = self.state()
        self.assertEqual(len(s["books"]["F"]["open"]), 1)
        self.assertEqual(len(s["books"]["S"]["open"]), 1)  # safety-failed but execution-ok
        self.assertEqual(len(s["books"]["R"]["open"]), 1)  # R drawn only because F filled
        pos = next(iter(s["books"]["F"]["open"].values()))
        self.assertAlmostEqual(pos["entry"], C.TICKET_SOL * 1e9 / TOK)
        buys = [t for t in trades(self.d) if t["action"] == "buy"]
        self.assertTrue(all(float(t["sol"]) == C.TICKET_SOL for t in buys))
        cost, h = fills.entry_cost(100.0, lead(1)["quote"])
        curve = 10000 * 0.5 / 100.5
        self.assertAlmostEqual(h, (60 + 0.25 * curve) / 10000)  # curve impact, not the 1% Jupiter field
        other, h2 = fills.entry_cost(100.0, dict(lead(1)["quote"], priceImpactPct="0.09"))
        self.assertEqual((cost, h), (other, h2))
        self.assertAlmostEqual(s["books"]["F"]["cash"], C.START_BALANCE_SOL - cost)
        self.assertEqual(r["opened"], 3)

    def test_no_r_without_f_and_excluded_safety(self):
        self.step(T0, [lead(1, "safety", "not_graduated"), lead(2, "safety", "missing_data"), lead(3, "eligibility", "no_sol_pair")])
        s = self.state()
        self.assertEqual(sum(len(b["open"]) for b in s["books"].values()), 0)

    def test_caps_max_open_and_per_day(self):
        self.step(T0, [lead(i) for i in range(10)])
        self.assertEqual(len(self.state()["books"]["F"]["open"]), C.MAX_OPEN_F)
        # close all by hard stop, then keep entering: 8 new F per day max
        for i in range(10): self.mk.ratio[M(i)] = 0.5
        self.step(T0 + 1800, [lead(i) for i in range(10, 20)])
        self.assertEqual(self.state()["books"]["F"]["fills_today"], 8)
        for i in range(10, 20): self.mk.ratio[M(i)] = 0.5
        self.step(T0 + 3600, [lead(i) for i in range(20, 30)])
        self.assertEqual(self.state()["books"]["F"]["fills_today"], 8)  # day cap reached
        self.assertEqual(self.step(T0 + 86400, [lead(i) for i in range(30, 33)], run_id="nextday")["void"], ["outage_over_2h"])
        self.step(T0 + 86400 + 1800, [lead(i) for i in range(30, 33)], run_id="nextday2")
        self.assertEqual(self.state()["books"]["F"]["fills_today"], 3)  # reset on a new UTC day

    def test_r_s_capped_at_2x_f(self):
        self.step(T0, [lead(1)] + [lead(i, "safety", "dev_holds") for i in range(2, 9)])
        s = self.state()
        self.assertLessEqual(len(s["books"]["S"]["open"]), 2 * len(s["books"]["F"]["open"]))
        self.assertLessEqual(len(s["books"]["R"]["open"]), 2 * len(s["books"]["F"]["open"]))

    def _one(self): self.step(T0, [lead(1)]); return M(1)
    def _sells(self): return [t for t in trades(self.d) if t["action"] == "sell" and t["book"] == "F"]

    def test_hard_stop(self):
        m = self._one(); self.mk.ratio[m] = 0.70; self.step(T0 + 1800)
        s = self._sells(); self.assertEqual(s[-1]["why"], "hard_stop"); self.assertLess(float(s[-1]["pnl"]), -0.15)
        self.assertEqual(self.state()["books"]["F"]["open"], {})

    def test_ladder_and_trail(self):
        m = self._one()
        self.mk.ratio[m] = 1.45; self.step(T0 + 1800)
        self.assertEqual([t["why"] for t in self._sells()], ["take_profit_40"])
        self.mk.ratio[m] = 2.10; self.step(T0 + 3600)
        self.assertEqual(self._sells()[-1]["why"], "take_profit_100")
        self.mk.ratio[m] = 1.50; self.step(T0 + 5400)  # > 25% below the 2.10 peak
        self.assertEqual(self._sells()[-1]["why"], "trailing_stop")
        self.assertEqual(self.state()["books"]["F"]["open"], {})
        total = sum(float(t["pnl"]) for t in self._sells()); self.assertGreater(total, 0)
        self.assertAlmostEqual(sum(float(t["frac"]) for t in self._sells()), 1.0, places=5)

    def test_time_stop(self):
        m = self._one(); self.mk.ratio[m] = 1.02
        self.step(T0 + 3600); self.assertEqual(self._sells(), [])
        self.step(T0 + 120 * 60); self.assertEqual(self._sells()[-1]["why"], "time_stop")

    def test_time_stop_keeps_winner(self):
        m = self._one(); self.mk.ratio[m] = 1.15; self.step(T0 + 120 * 60)
        self.assertEqual(self._sells(), [])

    def test_liquidity_pull(self):
        m = self._one(); self.mk.liq[m] = 50.0; self.step(T0 + 1800)
        self.assertEqual(self._sells()[-1]["why"], "liquidity_pulled")

    def test_stale_on_temp_error_then_recover(self):
        m = self._one(); self.mk.status[m] = "err"; self.step(T0 + 1800)
        marks = [t for t in trades(self.d) if t["action"] == "mark"]
        self.assertEqual((marks[-1]["why"], marks[-1]["stale"]), ("stale", "1"))
        pos = next(iter(self.state()["books"]["F"]["open"].values())); self.assertEqual(pos["stale"], 1)
        paper = json.load(open(os.path.join(self.d, "paper.json")))
        self.assertEqual([t["status"] for t in paper["open"] if t["book"] == "F"], ["stale"])
        self.mk.status[m] = None; self.step(T0 + 3600)
        pos = next(iter(self.state()["books"]["F"]["open"].values())); self.assertEqual(pos["stale"], 0)

    def test_no_route_three_times_is_minus_100(self):
        m = self._one(); self.mk.status[m] = "noroute"
        for k in (1, 2): self.step(T0 + 1800 * k)
        self.assertEqual(self._sells(), [])
        self.step(T0 + 1800 * 3)
        s = self._sells()[-1]; self.assertEqual(s["why"], "no_route_3x")
        cost, _ = fills.entry_cost(100.0, lead(1)["quote"])
        self.assertAlmostEqual(float(s["pnl"]), -cost, places=6)
        self.assertAlmostEqual(float(s["gross_pnl"]), -C.TICKET_SOL, places=6)
        self.assertAlmostEqual(float(s["net_pnl"]), float(s["pnl"]), places=9)

    def test_persistence_across_runs(self):
        m = self._one()
        s1 = self.state(); self.assertEqual(len(s1["books"]["F"]["open"]), 1)
        self.mk.ratio[m] = 1.1; self.step(T0 + 1800, [lead(1)])  # same mint again: not doubled
        s2 = self.state(); pos = next(iter(s2["books"]["F"]["open"].values()))
        self.assertEqual(len(s2["books"]["F"]["open"]), 1); self.assertAlmostEqual(pos["last_mark"] / pos["entry"], 1.1, places=6)
        self.assertEqual(s2["last_run_ts"], T0 + 1800)

    def test_data_failure_void(self):
        r = self.step(T0, [lead(1)], st=dict(checks=20, data_failures=3))
        self.assertEqual(r["void"], ["data_failures_over_10pct"]); self.assertEqual(r["opened"], 0)
        r = self.step(T0 + 1800, [lead(1)], st=dict(checks=20, data_failures=2))  # exactly 10% is not over
        self.assertGreaterEqual(r["opened"], 1); self.assertEqual(len(self.state()["books"]["F"]["open"]), 1)

    def test_outage_void_but_exits_still_run(self):
        m = self._one(); self.mk.ratio[m] = 0.5
        r = self.step(T0 + 3 * 3600, [lead(2)])
        self.assertEqual(r["void"], ["outage_over_2h"]); self.assertEqual(r["opened"], 0)
        self.assertEqual(self._sells()[-1]["why"], "hard_stop")
        self.assertGreaterEqual(self.step(T0 + 3 * 3600 + 1800, [lead(3)])["opened"], 1)

    def test_would_halt_logged_never_blocks(self):
        for k in range(3):
            leads = [lead(10 * k + i) for i in range(4)]
            self.step(T0 + k * 3600, leads)
            for l in leads: self.mk.status[l["mint"]] = "noroute"
            for j in (1, 2, 3): self.step(T0 + k * 3600 + j * 600)
        # 12 F positions lost 100% = -6 SOL on 10 SOL -> drawdown >= 30%, but entries continue
        b = self.state()["books"]["F"]
        self.assertTrue(b["would_halt"]); self.assertEqual(len(b["would_halt"]), 1)
        self.assertGreaterEqual(b["would_halt"][0]["drawdown"], C.SESSION_LOSS_HALT_PCT)

    def test_paper_json_matches_schema(self):
        from jsonschema import Draft202012Validator
        m = self._one(); self.mk.ratio[m] = 0.6; self.step(T0 + 1800, [lead(5)])
        sch = json.load(open(os.path.join(os.path.dirname(__file__), "..", "..", "schema", "paper-book.schema.json")))
        p = json.load(open(os.path.join(self.d, "paper.json")))
        self.assertEqual(list(Draft202012Validator(sch).iter_errors(p)), [])
        self.assertTrue(p["closed"] and p["open"])

    def test_privacy_no_wallet_fields(self):
        self._one()
        blob = open(os.path.join(self.d, "paper.json")).read() + open(os.path.join(self.d, "positions.json")).read()
        for bad in ("wallet", "owner", "privateKey", "secret", "signature"): self.assertNotIn(bad, blob)

class CostHaircut(unittest.TestCase):
    """Slippage+MEV haircut is PnL accounting. Exit triggers still use the raw quote price."""
    def setUp(self): self.d = tempfile.mkdtemp(); self.mk = Market(); self.st = dict(checks=12, data_failures=0)
    def step(self, now, leads=(), st=None, run_id=None):
        return fills.step(self.d, now, run_id or f"R{now}", now // 60, list(leads), st or self.st, self.mk.sell, self.mk.liquidity)
    def _one(self): self.step(T0, [lead(1)]); return M(1)
    def _sells(self): return [t for t in trades(self.d) if t["action"] == "sell" and t["book"] == "F"]

    def test_freeze_kill_and_exit_numbers_unchanged(self):
        self.assertTrue(C.FROZEN); self.assertEqual(C.CFG_VERSION, "v1-v6.5")
        self.assertEqual(C.TICKET_SOL, 0.5)
        self.assertEqual(C.EXIT["hard_stop"], -0.25)
        self.assertEqual(C.EXIT["ladder"], [(0.40, 1/3), (1.00, 1/3)])
        self.assertEqual(C.EXIT["trail"], 0.25)
        self.assertEqual(C.EXIT["time_stop_min"], 120)
        self.assertEqual(C.SESSION_LOSS_HALT_PCT, 0.30)
        self.assertEqual(C.EARLY_STOP_EXP, -0.15)
        self.assertEqual(C.MIN_F_CLOSES, 20)
        self.assertEqual(C.DATA_FAIL_VOID, 0.10)
        self.assertEqual(C.OUTAGE_VOID_H, 2)
        self.assertEqual(C.COST["network_fee_sol"], 0.00001)
        self.assertEqual(C.COST["legacy_haircut_per_side"], 0.005)
        self.assertEqual(C.COST["base_bps"], 60)
        self.assertEqual(C.COST["impact_weight"], 0.25)
        self.assertEqual(C.COST["stress_exit_bps"], 150)
        self.assertEqual(C.COST["stress_exit_reasons"], ("hard_stop", "trailing_stop", "liquidity_pulled"))
        self.assertEqual(C.COST["max_bps"], 600)
        self.assertEqual(C.COST["haircut_per_side"], C.COST["base_bps"] / 10000)
        self.assertEqual(C.COST_MODEL, "v3-exit-aware")

    def test_haircut_uses_curve_impact_then_caps(self):
        for pool, pct in ((148.2672, 0.3361), (179.7921, 0.2773), (257.1357, 0.1941)):
            self.assertAlmostEqual(fills.verified_impact_bps(pool) / 100, pct, places=3)
        v = fills.verified_impact_bps(148.2672)
        self.assertAlmostEqual(fills.haircut_frac(148.2672), (60 + 0.25 * v) / 10000)
        self.assertAlmostEqual(fills.haircut_frac(100, 0), 0.006)            # explicit zero impact: base only
        self.assertAlmostEqual(fills.haircut_frac(100), (60 + 0.25 * fills.verified_impact_bps(100)) / 10000)
        self.assertAlmostEqual(fills.haircut_frac(None, None), 0.006)        # missing reserve: no invented impact
        self.assertAlmostEqual(fills.haircut_frac(0, None), 0.006)
        self.assertAlmostEqual(fills.haircut_frac(float("nan"), None), 0.006)
        self.assertAlmostEqual(fills.haircut_frac(0.5), 0.06)                 # curve term runs into the 600 bps cap
        base = fills.haircut_frac(100)
        self.assertAlmostEqual(fills.haircut_frac(100, None, "time_stop"), base)
        self.assertAlmostEqual(fills.haircut_frac(100, None, "take_profit_40"), base)
        self.assertAlmostEqual(fills.haircut_frac(100, None, "hard_stop"), base + 0.015)
        self.assertAlmostEqual(fills.haircut_frac(100, None, "trailing_stop"), base + 0.015)
        self.assertAlmostEqual(fills.haircut_frac(100, None, "liquidity_pulled"), base + 0.015)
        self.assertAlmostEqual(fills.haircut_frac(0.5, None, "hard_stop"), 0.06)
        self.assertIsNone(fills.verified_impact_bps(None))
        self.assertIsNone(fills.verified_impact_bps(0))
        self.assertIsNone(fills.impact_bps({}))
        self.assertIsNone(fills.impact_bps({"priceImpactPct": "nan"}))
        self.assertAlmostEqual(fills.impact_bps({"priceImpactPct": "0.01"}), 100)

    def test_both_sides_on_the_fill_gross_and_net(self):
        m = self._one()
        buy = [t for t in trades(self.d) if t["action"] == "buy" and t["book"] == "F"][-1]
        self.assertAlmostEqual(float(buy["sol"]), C.TICKET_SOL)
        self.assertAlmostEqual(float(buy["impact_pct"]), 1.0, places=4)  # Jupiter field is logged
        self.assertAlmostEqual(float(buy["entry_haircut_pct"]), 0.7244, places=4)  # not half of that 1%
        self.assertEqual(buy["cost_model"], "v3-exit-aware")
        self.assertTrue(buy["cost_model_started_at"].endswith("Z"))
        self.assertEqual(buy["pnl"], ""); self.assertEqual(buy["gross_pnl"], ""); self.assertEqual(buy["net_pnl"], "")
        self.mk.ratio[m] = 1.0
        self.step(T0 + 120 * 60)
        s = self._sells()[-1]
        self.assertEqual(s["why"], "time_stop")
        self.assertAlmostEqual(float(s["gross_pnl"]), 0.0, places=6)
        self.assertAlmostEqual(float(s["entry_haircut_pct"]), 0.7244, places=4)
        self.assertAlmostEqual(float(s["exit_haircut_pct"]), 0.7244, places=4)  # time_stop: no stress add-on
        self.assertEqual(s["cost_model"], buy["cost_model"])
        self.assertEqual(s["cost_model_started_at"], buy["cost_model_started_at"])
        cost, h = fills.entry_cost(100.0, lead(1)["quote"])
        pro = C.TICKET_SOL * (1 - h) - C.COST["network_fee_sol"]
        self.assertAlmostEqual(float(s["net_pnl"]), pro - cost, places=6)
        self.assertAlmostEqual(float(s["pnl"]), float(s["net_pnl"]), places=9)
        self.assertLess(float(s["net_pnl"]), float(s["gross_pnl"]))
        paper = json.load(open(os.path.join(self.d, "paper.json")))
        closed = [t for t in paper["closed"] if t["book"] == "F"][0]
        self.assertEqual(closed["result"]["pnl"], closed["result"]["net_pnl"])
        self.assertLess(closed["result"]["net_pnl"], closed["result"]["gross_pnl"])
        self.assertEqual(paper["books"]["F"]["realized_pnl"], closed["result"]["net_pnl"])
        self.assertEqual(paper["books"]["F"]["realized_gross_pnl"], closed["result"]["gross_pnl"])
        self.assertEqual(paper["costs"]["base_bps"], 60)
        self.assertEqual(paper["costs"]["model"], "v3-exit-aware")
        self.assertIn("Assumption, not a measured constant", paper["costs"]["note"])
        self.assertEqual(paper["stats"]["model"], "v3-exit-aware")
        self.assertGreaterEqual(paper["stats"]["windows"]["7"]["counted_closes"], 1)
        self.assertEqual(paper["stats"]["windows"]["7"]["older_closes"], 0)
        self.assertEqual(paper["closed"][0]["cost_model"], "v3-exit-aware")

    def test_thin_pool_raises_curve_impact_and_a_stop_adds_stress(self):
        deep = lead(1, liq=100.0); deep["quote"] = dict(deep["quote"], priceImpactPct="0")
        thin = lead(2, liq=10.0); thin["quote"] = dict(thin["quote"], priceImpactPct="0.03")
        self.step(T0, [deep, thin])
        buys = {t["symbol"]: t for t in trades(self.d) if t["action"] == "buy" and t["book"] == "F"}
        self.assertAlmostEqual(float(buys["T1"]["haircut_pct"]), 0.7244, places=4)
        self.assertAlmostEqual(float(buys["T2"]["haircut_pct"]), 1.7905, places=4)  # 10 SOL curve, not the 3% Jupiter field
        self.assertAlmostEqual(float(buys["T2"]["impact_pct"]), 3.0, places=4)
        m = M(1); self.mk.liq[m] = 10.0; self.mk.impact[m] = "0.03"; self.mk.ratio[m] = 1.0
        self.step(T0 + 1800)
        s = [t for t in self._sells() if t["symbol"] == "T1"][-1]
        self.assertEqual(s["why"], "liquidity_pulled")
        self.assertAlmostEqual(float(s["entry_haircut_pct"]), 0.7244, places=4)
        v = fills.verified_impact_bps(10.0, C.TICKET_SOL)
        self.assertAlmostEqual(float(s["exit_haircut_pct"]), round((60 + 0.25 * v + 150) / 100, 4), places=4)
        self.assertAlmostEqual(float(s["impact_pct"]), 3.0, places=4)
        self.assertGreater(float(s["exit_haircut_pct"]), 3.0)

    def test_price_stop_ignores_the_haircut(self):
        """A mark inside the -25% price stop stays open even when net-of-cost is past -25% of the ticket."""
        m = self._one(); self.mk.ratio[m] = 0.76; self.step(T0 + 1800)
        self.assertEqual(self._sells(), [])
        self.assertEqual(len(json.load(open(os.path.join(self.d, "positions.json")))["books"]["F"]["open"]), 1)
        paper = json.load(open(os.path.join(self.d, "paper.json")))
        self.assertLess(paper["books"]["F"]["equity"] - C.START_BALANCE_SOL, C.EXIT["hard_stop"] * C.TICKET_SOL)

    def test_legacy_row_and_position_keep_old_numbers(self):
        import csv
        from desk import ledger
        p = os.path.join(self.d, "trades.csv")
        old = ledger.TRADE_COLS[:16]
        with open(p, "w", newline="") as f:
            w = csv.writer(f); w.writerow(old)
            w.writerow(["1", "F", "EXIT", "MintOld", "OLD", "sell", "1", "0.4", "0.4", "1", "1e-05", "0.5", "hard_stop", "-0.1", "0", "0"])
        old_cost = fills.legacy_entry_cost()
        mint = M(1)
        pos = dict(entry=0.5, peak=0.5, steps=0, liq0=100.0, opened=T0, frac=1.0, last_mark=0.5, stale=0,
                   no_route_count=0, mint=mint, symbol="T1", tokens=TOK, pnl=0.0)
        book = dict(cash=C.START_BALANCE_SOL - old_cost, open={"F-old": pos}, fills_day="x", fills_today=1,
                    realized_pnl=0.0, closed=[], would_halt=[], peak_equity=C.START_BALANCE_SOL)
        json.dump(dict(cfg_version=C.CFG_VERSION, last_run_ts=T0, voids=[], books={"F": book}), open(os.path.join(self.d, "positions.json"), "w"))
        self.mk.ratio[mint] = 1.0
        self.step(T0 + 120 * 60)
        rows = list(csv.DictReader(open(p)))
        self.assertEqual(list(rows[0].keys()), ledger.TRADE_COLS)
        self.assertEqual((rows[0]["pnl"], rows[0]["gross_pnl"], rows[0]["net_pnl"]), ("-0.1", "", ""))
        self.assertEqual(rows[0]["cost_model"], "")
        s = self._sells()[-1]
        h = fills.haircut_frac(100.0)
        pro = C.TICKET_SOL * (1 - h) - C.COST["network_fee_sol"]
        self.assertAlmostEqual(float(s["net_pnl"]), pro - old_cost, places=6)
        self.assertAlmostEqual(float(s["gross_pnl"]), 0.0, places=6)
        self.assertAlmostEqual(float(s["entry_haircut_pct"]), 0.5, places=4)
        b = json.load(open(os.path.join(self.d, "positions.json")))["books"]["F"]
        self.assertAlmostEqual(b["cash"], C.START_BALANCE_SOL - old_cost + pro, places=6)
        legacy = fills.ticket("F-old", dict(mint="m" * 32, symbol="OLD", book="F", opened=T0, closed=T0 + 1,
                                            entry=0.5, exit_px=0.3, why="hard_stop", pnl=-0.2, pct=-40.0), closed=True)
        self.assertEqual(legacy["result"]["pnl"], -0.2)
        self.assertEqual(legacy["result"]["net_pnl"], -0.2)
        self.assertEqual(legacy["cost_model"], "pre-v3")
        self.assertNotIn("gross_pnl", legacy["result"])
        self.assertEqual(s["cost_model"], "v3-exit-aware")

    def test_window_stats_label_older_fills_and_leave_them_unchanged(self):
        from desk import ledger
        self._one()
        p = os.path.join(self.d, "trades.csv")
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=ledger.TRADE_COLS)
            row = {k: "" for k in ledger.TRADE_COLS}
            row.update(time=str(T0 + 10), book="F", action="sell", pnl="-0.2", why="hard_stop", symbol="OLD")
            w.writerow(row)
        self.mk.ratio[M(1)] = 1.0
        self.step(T0 + 120 * 60)
        rows = list(csv.DictReader(open(p)))
        old = [r for r in rows if r["symbol"] == "OLD"][0]
        self.assertEqual((old["pnl"], old["cost_model"], old["gross_pnl"]), ("-0.2", "", ""))
        new = [r for r in rows if r["action"] == "sell" and r["symbol"] == "T1"][-1]
        self.assertEqual(new["cost_model"], "v3-exit-aware")
        started = [r["cost_model_started_at"] for r in rows if r["cost_model"] == "v3-exit-aware"]
        self.assertTrue(started and len(set(started)) == 1)
        paper = json.load(open(os.path.join(self.d, "paper.json")))
        for days in ("7", "28"):
            wdw = paper["stats"]["windows"][days]
            self.assertEqual(wdw["older_closes"], 1)
            self.assertEqual(wdw["older_labels"], {"pre-v3": 1})
            self.assertAlmostEqual(wdw["older_net_pnl"], -0.2)
            self.assertGreaterEqual(wdw["counted_closes"], 1)
            self.assertNotAlmostEqual(wdw["net_pnl"], -0.2)
        self.assertIn("v3-exit-aware", [t["cost_model"] for t in paper["closed"]])

class RunFill(unittest.TestCase):
    def test_scan_fill_end_to_end_offline(self):
        """run.scan in fill mode with a fake HTTP layer: one passing lead becomes an F paper fill."""
        mint = M(7)
        def get(url, headers=None):
            if "token-profiles" in url: return [{"chainId": "solana", "tokenAddress": mint}]
            if "token-boosts" in url: return []
            if "/latest/dex/tokens/" in url: return {"pairs": [dict(chainId="solana", dexId="raydium", pairAddress="P",
                quoteToken={"address": run.SOL}, baseToken={"address": mint, "symbol": "TST"},
                liquidity={"quote": 500, "usd": 1e5}, pairCreatedAt=(T0 - 3600) * 1000, txns={"h1": {"sells": 5}})]}
            if "rugcheck" in url: return {"markets": [{"marketType": "raydium", "pubkey": "P", "lp": {"lpLockedPct": 100}}],
                                          "topHolders": [], "score_normalised": 1, "creatorBalance": 0, "token": {"supply": 1}}
            if "quote" in url:
                if f"inputMint={run.SOL}" in url: return {"outAmount": str(TOK), "priceImpactPct": "0.001", "routePlan": [1]}
                return {"outAmount": "1", "routePlan": [1]}
            raise AssertionError(url)
        d = tempfile.mkdtemp()
        h = run.scan(d, get=get, now=T0, mode="fill")
        self.assertIn("fill", h); self.assertEqual(h["mode"], "fill")
        rows = list(csv.DictReader(open(os.path.join(d, "tasks.csv"))))
        self.assertEqual(rows[0]["result"], "pass"); self.assertEqual(h["fill"]["open"]["F"], 1)
        self.assertEqual([t["action"] for t in trades(d) if t["book"] == "F"], ["buy"])
        self.assertTrue(os.path.exists(os.path.join(d, "paper.json")))

    def test_auto_mode_resolves(self):
        self.assertTrue(run.mode_allowed("fill"))

if __name__ == "__main__": unittest.main()
