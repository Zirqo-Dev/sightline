import csv, json, os, tempfile, unittest
from desk import config as C, fills, run

T0 = 1793500000  # fixed test time
TOK = 1_000_000_000  # raw tokens bought for 0.5 SOL -> entry px 0.5 lamports/unit

def M(i): return f"Mint{i:02d}" + "x" * 30 + f"{i:02d}pp"
def lead(i, stype=None, reason=None, out=TOK, liq=100.0):
    return dict(mint=M(i), stype=stype, reason=reason, quote={"outAmount": str(out), "priceImpactPct": "0.01", "routePlan": [1]},
                pair={"liquidity": {"quote": liq}, "baseToken": {"symbol": f"T{i}"}})

class Market:
    """Fake Jupiter sell quotes: ratio[mint] = mark/entry; 'noroute' / 'err' statuses."""
    def __init__(self): self.ratio = {}; self.status = {}; self.liq = {}
    def sell(self, m, amount):
        st = self.status.get(m)
        if st == "err": raise RuntimeError("timeout")
        if st == "noroute": return {}
        return {"outAmount": str(int(amount * 0.5 * self.ratio.get(m, 1.0))), "routePlan": [1]}
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
        self.assertAlmostEqual(s["books"]["F"]["cash"], C.START_BALANCE_SOL - fills.basis())
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
        self.assertAlmostEqual(float(s["pnl"]), -fills.basis(), places=6)

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
