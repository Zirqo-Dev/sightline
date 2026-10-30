"""Logging-only pool snapshots. Decisions match a run with the log switched off."""
import csv, json, os, tempfile, time, unittest
from desk import config as C, exits, fills, observe, run

T0 = 1793500000  # after 2026-07-21, so a fresh PumpSwap pool can sit in the boost window
TOK = 1_000_000_000
SCH = os.path.join(os.path.dirname(__file__), "..", "..", "schema")

DECISION_POS = (
    "entry", "peak", "steps", "liq0", "opened", "frac", "last_mark", "stale", "no_route_count",
    "mint", "symbol", "tokens", "pnl", "gross_pnl", "entry_haircut", "entry_cost", "why", "book",
)


def M(i):
    return f"Mint{i:02d}" + "x" * 30 + f"{i:02d}pp"


def lead(i, sol=100.0, token=1_000_000.0, dex="pumpswap", created_s=None, out=TOK):
    created_s = T0 - 300 if created_s is None else created_s
    return dict(
        mint=M(i), stype=None, reason=None,
        quote={"outAmount": str(out), "priceImpactPct": "0.01", "routePlan": [1]},
        pair={
            "dexId": dex, "pairAddress": "Pentry", "pairCreatedAt": int(created_s * 1000),
            "liquidity": {"quote": sol, "base": token},
            "baseToken": {"symbol": f"T{i}"},
        },
    )


class Pool:
    """Sell quote and pool snapshot. mode 'float' is the historical SOL-only liquidity callback."""
    def __init__(self, mode="pair"):
        self.mode = mode
        self.ratio = {}
        self.sol = {}
        self.token = {}
        self.sells = 0
        self.liqs = 0

    def sell(self, m, amount):
        self.sells += 1
        q = {"outAmount": str(int(amount * 0.5 * self.ratio.get(m, 1.0))), "routePlan": [1], "priceImpactPct": "0.02"}
        return q

    def liquidity(self, m):
        self.liqs += 1
        sol = self.sol.get(m, 100.0)
        if self.mode == "float":
            return sol
        return {
            "dexId": "pumpswap", "pairAddress": "Pentry", "pairCreatedAt": (T0 - 300) * 1000,
            "baseToken": {"symbol": "T"},
            "liquidity": {"quote": sol, "base": self.token.get(m, 1_000_000.0)},
        }


def fingerprint(d):
    state = json.load(open(os.path.join(d, "positions.json")))
    books = {}
    for name, b in state["books"].items():
        books[name] = {
            "cash": round(b["cash"], 8),
            "realized_pnl": round(b["realized_pnl"], 8),
            "realized_gross": round(b.get("realized_gross") or 0.0, 8),
            "fills_today": b["fills_today"],
            "open": {pid: {k: p.get(k) for k in DECISION_POS} for pid, p in sorted(b["open"].items())},
            "closed": [
                {k: c.get(k) for k in ("mint", "entry", "exit_px", "why", "pnl", "gross_pnl", "pct", "gross_pct")}
                for c in b["closed"]
            ],
        }
    rows = []
    p = os.path.join(d, "trades.csv")
    if os.path.exists(p):
        for r in csv.DictReader(open(p)):
            rows.append({k: r[k] for k in r if k != "check_delay_s"})
    return {"books": books, "trades": rows}


def play(log, mode="pair", mutate=None):
    """Entry, a higher mark, a lower mark, then a -30% hard stop. mutate(mk, mint) runs before each later scan."""
    d = tempfile.mkdtemp()
    mk = Pool(mode)
    st = dict(checks=12, data_failures=0)

    def step(now, leads=()):
        return fills.step(d, now, f"R{now}", now // 60, list(leads), st, mk.sell, mk.liquidity, log=log)

    step(T0, [lead(1)])
    m = M(1)
    mk.ratio[m] = 1.10
    mk.token[m] = 1_100_000.0
    if mutate: mutate(mk, m, 1)
    step(T0 + 1800)
    mk.ratio[m] = 0.90
    if mutate: mutate(mk, m, 2)
    step(T0 + 3600)
    mk.ratio[m] = 0.70
    if mutate: mutate(mk, m, 3)
    step(T0 + 5400)
    return d, mk


class Decisions(unittest.TestCase):
    def test_same_path_with_and_without_logging(self):
        logged, mk_on = play(True)
        quiet, mk_off = play(False)
        self.assertEqual(fingerprint(logged), fingerprint(quiet))
        self.assertEqual((mk_on.sells, mk_on.liqs), (mk_off.sells, mk_off.liqs))
        self.assertGreater(mk_on.sells, 0)
        state = json.load(open(os.path.join(logged, "positions.json")))
        closed = state["books"]["F"]["closed"][-1]
        self.assertEqual(closed["why"], "hard_stop")
        self.assertEqual(closed["entry_sol_reserve"], 100.0)
        self.assertEqual(closed["entry_token_reserve"], 1_000_000.0)
        self.assertEqual(closed["entry_k"], 100.0 * 1_000_000.0)
        self.assertEqual(closed["exit_sol_reserve"], 100.0)
        self.assertEqual(closed["exit_token_reserve"], 1_100_000.0)
        self.assertEqual(closed["exit_k"], 100.0 * 1_100_000.0)
        self.assertIsNone(closed["entry_reserve_error"])
        self.assertTrue(closed["graduated_pump"])
        self.assertIs(closed["boost_window"], True)
        self.assertEqual(closed["pool_created_at"], observe.iso(T0 - 300))
        self.assertEqual(closed["pool_age_at_entry_s"], 300.0)
        self.assertEqual(closed["obs_v"], 1)
        self.assertEqual(closed["entry_pool"], "Pentry")
        self.assertEqual(closed["exit_pool"], "Pentry")
        self.assertIs(closed["same_pool"], True)
        self.assertEqual(closed["reserve_snaps"][0]["pool"], "Pentry")
        entry = 0.5 * 1e9 / TOK
        self.assertEqual(closed["entry"], entry)
        self.assertEqual(closed["entry_quote_out"], TOK)
        self.assertEqual(closed["entry_quote_px"], entry)
        self.assertEqual(closed["entry_quote_impact"], 0.01)
        self.assertIsNone(closed["entry_quote_error"])
        self.assertAlmostEqual(closed["max_price_held"], entry * 1.10)
        self.assertAlmostEqual(closed["min_price_held"], entry * 0.70)
        self.assertAlmostEqual(closed["max_price_held_pct"], 10.0)
        self.assertAlmostEqual(closed["min_price_held_pct"], -30.0)
        self.assertEqual(closed["exit_quote_impact"], 0.02)
        self.assertAlmostEqual(closed["exit_quote_px"], closed["exit_px"])
        quiet_pos = json.load(open(os.path.join(quiet, "positions.json")))["books"]["F"]["closed"][-1]
        self.assertNotIn("entry_k", quiet_pos)
        self.assertNotIn("max_price_held", quiet_pos)
        self.assertFalse(set(observe.PUBLIC_KEYS) & fills._DECISION)

    def test_pair_snapshot_matches_the_old_sol_float(self):
        """Returning the whole pair must leave the SOL number, and the trades, unchanged."""
        pair_run, _ = play(True, mode="pair")
        float_run, _ = play(True, mode="float")
        self.assertEqual(fingerprint(pair_run), fingerprint(float_run))

    def test_sol_liq_matches_the_historical_expression(self):
        def old(p):
            return ((p or {}).get("liquidity") or {}).get("quote")
        samples = [
            None, {}, {"liquidity": None}, {"liquidity": {}}, {"liquidity": 0},
            {"liquidity": {"quote": 0, "base": 5}}, {"liquidity": {"quote": 12.5, "base": 9}},
            {"dexId": "pumpswap"},
        ]
        for s in samples:
            self.assertEqual(fills.sol_liq(s), old(s), s)
        self.assertEqual(fills.sol_liq(12.5), 12.5)
        self.assertEqual(fills.sol_liq(0), 0)
        self.assertIsNone(fills.sol_liq(None))
        with self.assertRaises(AttributeError):
            fills.sol_liq({"liquidity": 5})
        with self.assertRaises(AttributeError):
            old({"liquidity": 5})

    def test_exit_rule_ignores_log_fields(self):
        hostile = dict(
            max_price_held=1e9, min_price_held=1e-9, entry_k=1, last_k=1,
            entry_quote_px=50, exit_quote_px=50, boost_window=True, graduated_pump=True,
            entry_sol_reserve=1, entry_token_reserve=1,
        )
        base = exits.new_position(1.0, 100.0, 0)
        a = exits.check(dict(base), 0.70, 100.0, 60)
        b = exits.check(dict(base, **hostile), 0.70, 100.0, 60)
        self.assertEqual(a, b)
        self.assertEqual(a[0][2], "hard_stop")
        trail = exits.new_position(1.0, 100.0, 0)
        trail.update(peak=2.1, steps=2, frac=1 / 3, **hostile)
        trail["max_price_held"] = 0.01
        acts = exits.check(trail, 1.5, 100.0, 1000)
        self.assertEqual(acts[-1][2], "trailing_stop")
        self.assertNotIn("peak", observe.note_price({"entry": 1.0}, 2.0))

    def test_logged_quote_price_is_not_the_fill(self):
        real = observe.quote_numbers

        def shifted(*a, **k):
            out, px, impact, err = real(*a, **k)
            return out, (px * 10 if px else px), impact, err

        observe.quote_numbers = shifted
        try:
            logged, _ = play(True)
        finally:
            observe.quote_numbers = real
        clean, _ = play(True)
        self.assertEqual(fingerprint(logged), fingerprint(clean))
        closed = json.load(open(os.path.join(logged, "positions.json")))["books"]["F"]["closed"][-1]
        self.assertAlmostEqual(closed["entry_quote_px"], closed["entry"] * 10)
        self.assertAlmostEqual(closed["exit_quote_px"], closed["exit_px"] * 10)
        buy = [r for r in csv.DictReader(open(os.path.join(logged, "trades.csv"))) if r["action"] == "buy" and r["book"] == "F"][-1]
        sell = [r for r in csv.DictReader(open(os.path.join(logged, "trades.csv"))) if r["action"] == "sell" and r["book"] == "F"][-1]
        self.assertAlmostEqual(float(buy["quote_px"]), closed["entry"])
        self.assertAlmostEqual(float(sell["quote_px"]), closed["exit_px"])
        self.assertAlmostEqual(float(buy["impact_pct"]), 1.0)  # trade column stays percent
        self.assertEqual(closed["entry_quote_impact"], 0.01)  # stored field stays the Jupiter fraction

    def test_log_exceptions_leave_the_decision(self):
        saved = (observe.entry_log, observe.note_held, observe.note_price, observe.stamp_exit)

        def boom(*a, **k):
            raise RuntimeError("log down")

        observe.entry_log = observe.note_held = observe.note_price = observe.stamp_exit = boom
        try:
            broken, _ = play(True)
        finally:
            observe.entry_log, observe.note_held, observe.note_price, observe.stamp_exit = saved
        quiet, _ = play(False)
        self.assertEqual(fingerprint(broken), fingerprint(quiet))
        closed = json.load(open(os.path.join(broken, "positions.json")))["books"]["F"]["closed"][-1]
        self.assertEqual(closed["why"], "hard_stop")
        self.assertEqual(closed["entry_reserve_error"], "log_failed")

    def test_a_hostile_log_dict_cannot_overwrite_the_fill(self):
        real = observe.entry_log

        def hostile(*a, **k):
            fields = real(*a, **k)
            fields.update(entry=999, frac=0, liq0=1, peak=0.01, last_mark=0.01, pnl=123)
            return fields

        observe.entry_log = hostile
        try:
            logged, _ = play(True)
        finally:
            observe.entry_log = real
        quiet, _ = play(False)
        self.assertEqual(fingerprint(logged), fingerprint(quiet))


class ReserveShapes(unittest.TestCase):
    def test_k_is_the_product_and_a_missing_side_is_null(self):
        self.assertEqual(observe.snapshot_reserves({"liquidity": {"quote": 2.5, "base": 4}}), (2.5, 4.0, 10.0, None))
        sol, tok, k, err = observe.snapshot_reserves({"liquidity": {"quote": 80, "base": 1_250_000}})
        self.assertEqual((sol, tok, k, err), (80, 1_250_000, 100_000_000, None))
        self.assertEqual(80 * 1_250_000, 100 * 1_000_000)  # dump: k flat. A withdrawal drops both sides and k.
        self.assertEqual(observe.snapshot_reserves({"liquidity": {"quote": 40, "base": 400_000}})[2], 16_000_000)
        self.assertEqual(observe.snapshot_reserves({"liquidity": {"quote": 1}}), (None, None, None, "reserves_incomplete"))
        self.assertEqual(observe.snapshot_reserves({"liquidity": {"quote": -1, "base": 2}}), (None, None, None, "reserves_negative"))
        self.assertEqual(observe.snapshot_reserves(None)[3], "no_pool")
        self.assertEqual(observe.snapshot_reserves(100.0)[3], "reserves_incomplete")
        self.assertEqual(observe.quote_numbers({}, "buy")[3], "no_quote")
        self.assertEqual(observe.quote_numbers({"outAmount": "nope"}, "buy")[3], "bad_out_amount")

    def test_dump_shape_is_stored_and_the_exit_stays_the_time_stop(self):
        d = tempfile.mkdtemp()
        mk = Pool()
        st = dict(checks=12, data_failures=0)
        m = M(1)
        fills.step(d, T0, "R0", T0 // 60, [lead(1)], st, mk.sell, mk.liquidity)
        mk.sol[m] = 80.0
        mk.token[m] = 1_250_000.0
        mk.ratio[m] = 1.05
        fills.step(d, T0 + 1800, "R1", 1, [], st, mk.sell, mk.liquidity)
        mk.ratio[m] = 1.0
        fills.step(d, T0 + 120 * 60, "R2", 2, [], st, mk.sell, mk.liquidity)
        closed = json.load(open(os.path.join(d, "positions.json")))["books"]["F"]["closed"][-1]
        self.assertEqual(closed["why"], "time_stop")
        self.assertEqual(closed["entry_k"], closed["exit_k"])
        self.assertLess(closed["exit_sol_reserve"], closed["entry_sol_reserve"])
        self.assertGreater(closed["exit_token_reserve"], closed["entry_token_reserve"])
        self.assertAlmostEqual(closed["max_price_held_pct"], 5.0)
        self.assertEqual(closed["min_price_held_pct"], 0.0)
        self.assertGreaterEqual(len(closed["reserve_snaps"]), 3)

    def test_withdrawal_shape_is_stored_beside_the_existing_liquidity_pull(self):
        d = tempfile.mkdtemp()
        mk = Pool()
        st = dict(checks=12, data_failures=0)
        m = M(1)
        fills.step(d, T0, "R0", T0 // 60, [lead(1)], st, mk.sell, mk.liquidity)
        mk.sol[m] = 50.0
        mk.token[m] = 400_000.0
        fills.step(d, T0 + 1800, "R1", 1, [], st, mk.sell, mk.liquidity)
        closed = json.load(open(os.path.join(d, "positions.json")))["books"]["F"]["closed"][-1]
        self.assertEqual(closed["why"], "liquidity_pulled")
        self.assertLess(closed["exit_k"], closed["entry_k"])
        self.assertLess(closed["exit_sol_reserve"], closed["entry_sol_reserve"])
        self.assertLess(closed["exit_token_reserve"], closed["entry_token_reserve"])


class Migration(unittest.TestCase):
    def test_boost_window_boundaries(self):
        start = observe.BOOST_SINCE
        self.assertEqual(observe.iso(start), "2026-07-21T14:23:00Z")
        self.assertIs(observe.boost_window(start, start + 300, True), True)
        self.assertIs(observe.boost_window(start, start + 301, True), False)
        self.assertIs(observe.boost_window(start - 1, start + 60, True), False)
        self.assertIs(observe.boost_window(start, start - 1, True), False)
        self.assertIsNone(observe.boost_window(None, start, True))
        self.assertIsNone(observe.boost_window(start, start, None))
        self.assertIs(observe.boost_window(start, start + 60, False), False)
        self.assertIs(observe.graduated_pump({"dexId": "PumpSwap"}), True)
        self.assertIs(observe.graduated_pump({"dexId": "raydium"}), False)
        self.assertIsNone(observe.graduated_pump({}))
        self.assertIs(observe.graduated_pump({"dexId": "pumpfun"}), False)
        self.assertEqual(observe.pair_created_s({"pairCreatedAt": start * 1000}), start)
        self.assertEqual(observe.pair_created_s({"pairCreatedAt": start}), start)
        self.assertIsNone(observe.pair_created_s({"pairCreatedAt": 0}))
        fields = observe.entry_log(lead(1, dex="raydium", created_s=start + 30), 0.5, start + 60)
        self.assertIs(fields["graduated_pump"], False)
        self.assertIs(fields["boost_window"], False)
        self.assertIsNone(observe.entry_log({"pair": None, "quote": None}, 0.5, start)["boost_window"])
        aged = observe.entry_log(lead(1, created_s=start - 120.4), 0.5, start)
        self.assertEqual(aged["pool_age_at_entry_s"], 120.4)
        self.assertIsNone(observe.pool_addr({"pairAddress": ""}))
        self.assertEqual(observe.pool_addr({"pairAddress": "PoolA"}), "PoolA")
        zero = observe.stamp_exit({"entry": 1.0, "last_mark": 0, "entry_pool": "PoolA", "min_price_held": 0.4}, None, {}, 0, False)
        self.assertEqual(zero["min_price_held"], 0.0)
        self.assertEqual(zero["min_price_held_pct"], -100.0)
        self.assertIsNone(zero["exit_pool"])
        self.assertIsNone(zero["same_pool"])


class ScanTiming(unittest.TestCase):
    def _get(self):
        def get(url, headers=None):
            if "token-profiles" in url or "token-boosts" in url:
                return []
            raise AssertionError(url)
        return get

    def test_slot_start_and_delay_append_once(self):
        from jsonschema import Draft202012Validator
        slot = 1_800_000_000
        now = slot + 12.5
        sch = Draft202012Validator(json.load(open(os.path.join(SCH, "scan-timing.schema.json"))))
        with tempfile.TemporaryDirectory() as d:
            h = run.scan(d, self._get(), now=now, mode="scan", sleep=lambda s: None)
            path = os.path.join(d, observe.TIMING_NAME)
            lines = [json.loads(x) for x in open(path) if x.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(list(sch.iter_errors(lines[0])), [])
            self.assertEqual(lines[0]["scheduled_utc"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(slot)))
            self.assertEqual(lines[0]["started_utc"], time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
            self.assertEqual(lines[0]["delay_s"], 12.5)
            self.assertEqual(lines[0]["run_id"], h["last_run_utc"])
            self.assertEqual(lines[0]["mode"], "scan")
            self.assertEqual(lines[0]["trigger"], "local")
            run.scan(d, self._get(), now=now + 10, mode="scan", sleep=lambda s: None)
            self.assertEqual(sum(1 for x in open(path) if x.strip()), 1)
            run.scan(d, self._get(), now=slot + 1800 + 3, mode="fill", sleep=lambda s: None)
            lines = [json.loads(x) for x in open(path) if x.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(lines[1]["delay_s"], 3.0)
            self.assertEqual(lines[1]["mode"], "fill")

    def test_a_timing_failure_still_returns_the_scan(self):
        saved = observe.append_scan_timing

        def boom(*a, **k):
            raise RuntimeError("disk")

        observe.append_scan_timing = boom
        try:
            with tempfile.TemporaryDirectory() as d:
                h = run.scan(d, self._get(), now=1_800_000_000, mode="scan", sleep=lambda s: None)
                self.assertEqual(h["runs"], 1)
                self.assertFalse(h.get("duplicate"))
                self.assertFalse(os.path.exists(os.path.join(d, observe.TIMING_NAME)))
        finally:
            observe.append_scan_timing = saved


class ApiCalls(unittest.TestCase):
    def test_fill_scan_adds_no_quote_and_no_pair_call(self):
        """Buys are the lead scan. Sells and extra pair reads are the open positions. Logging adds neither."""
        mint = M(7)
        quotes, dex = [], []

        def get(url, headers=None):
            if "token-profiles" in url:
                return [{"chainId": "solana", "tokenAddress": mint}]
            if "token-boosts" in url:
                return []
            if "/latest/dex/tokens/" in url:
                dex.append(url)
                return {"pairs": [dict(
                    chainId="solana", dexId="pumpswap", pairAddress="P",
                    quoteToken={"address": run.SOL}, baseToken={"address": mint, "symbol": "TST"},
                    liquidity={"quote": 500, "base": 2_000_000, "usd": 1e5},
                    pairCreatedAt=(T0 - 3600) * 1000, txns={"h1": {"sells": 5}},
                )]}
            if "rugcheck" in url:
                return {"markets": [{"marketType": "raydium", "pubkey": "P", "lp": {"lpLockedPct": 100}}],
                        "topHolders": [], "score_normalised": 1, "creatorBalance": 0, "token": {"supply": 1}}
            if "quote" in url or "jup.ag" in url:
                quotes.append(url)
                if f"inputMint={run.SOL}" in url:
                    return {"outAmount": str(TOK), "priceImpactPct": "0.001", "routePlan": [1]}
                return {"outAmount": str(TOK // 2), "priceImpactPct": "0.002", "routePlan": [1]}
            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as d:
            h = run.scan(d, get, now=T0, mode="fill", sleep=lambda s: None)
            self.assertEqual(h["fill"]["open"]["F"], 1)
            self.assertEqual(sum(1 for u in quotes if f"inputMint={run.SOL}" in u), 1)
            self.assertEqual(sum(1 for u in quotes if f"inputMint={run.SOL}" not in u), 0)
            self.assertEqual(len(dex), 1)
            pos = next(iter(json.load(open(os.path.join(d, "positions.json")))["books"]["F"]["open"].values()))
            self.assertEqual(pos["entry_sol_reserve"], 500)
            self.assertEqual(pos["entry_token_reserve"], 2_000_000)
            self.assertEqual(pos["entry_k"], 500 * 2_000_000)
            self.assertIs(pos["graduated_pump"], True)
            self.assertIs(pos["boost_window"], False)  # pool is an hour old, outside the 300s window
            self.assertEqual(pos["entry_quote_out"], TOK)
            self.assertEqual(pos["entry_quote_px"], pos["entry"])
            n_open = sum(len(json.load(open(os.path.join(d, "positions.json")))["books"][b]["open"]) for b in "FRS")
            quotes.clear(); dex.clear()
            run.scan(d, get, now=T0 + 1800, mode="fill", sleep=lambda s: None)
            buys = sum(1 for u in quotes if f"inputMint={run.SOL}" in u)
            sells = len(quotes) - buys
            self.assertEqual(buys, 1)
            self.assertEqual(sells, n_open)
            self.assertEqual(len(dex), 1 + n_open)
            self.assertGreaterEqual(n_open, 1)


class Schema(unittest.TestCase):
    def test_optional_log_fields_and_the_timing_fixture(self):
        from jsonschema import Draft202012Validator
        paper = Draft202012Validator(json.load(open(os.path.join(SCH, "paper-book.schema.json"))))
        base = {"schema_version": 2, "desk": "solana-desk", "book": "F", "closed": [], "rules": [{"rule": "log"}]}
        bad = dict(base, open=[{"id": "1", "ticker": "T", "opened_at": "2026-10-30T00:00:00Z", "entry_k": "nope"}])
        self.assertTrue(list(paper.iter_errors(bad)))
        good_ticket = {
            "id": "1", "ticker": "T", "opened_at": "2026-10-30T00:00:00Z",
            "entry_k": None, "boost_window": False, "graduated_pump": True,
            "entry_quote_impact": 0.01, "max_price_held_pct": 0,
            "reserve_snaps": [{"t": "2026-10-30T00:00:00Z", "sol": 1, "token": 2, "k": 2}],
        }
        self.assertEqual(list(paper.iter_errors(dict(base, open=[good_ticket]))), [])
        d = tempfile.mkdtemp()
        mk = Pool()
        fills.step(d, T0, "R0", 1, [lead(1)], dict(checks=4, data_failures=0), mk.sell, mk.liquidity)
        live = json.load(open(os.path.join(d, "paper.json")))
        self.assertEqual(list(paper.iter_errors(live)), [])
        self.assertTrue(live["open"])
        timing = Draft202012Validator(json.load(open(os.path.join(SCH, "scan-timing.schema.json"))))
        line = json.loads(open(os.path.join(SCH, "..", "scripts", "fixtures", "solana-desk", "scan_timing.jsonl")).readline())
        self.assertEqual(list(timing.iter_errors(line)), [])
        self.assertTrue(list(timing.iter_errors(dict(line, delay_s=-1))))
        self.assertTrue(list(timing.iter_errors(dict(line, wallet="x"))))


if __name__ == "__main__":
    unittest.main()
