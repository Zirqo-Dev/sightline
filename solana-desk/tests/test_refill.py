"""One-time paper cash refill. Does not rewrite trades or per-trade returns."""
import csv, importlib.util, json, os, tempfile, unittest
from decimal import Decimal
from desk import config as C, fills, ledger

from tests.test_fills import Market, T0, lead, trades

# desk-data solana-desk/positions.json at 2026-11-05T02:30:27Z. Nothing open.
STALL = {
    "F": (0.4718103045614711, -9.528189695438533),
    "R": (0.1993308264770182, -9.800669173522987),
    "S": (0.4021697001625057, -9.597830299837497),
}

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(rel):
    path = os.path.join(REPO, rel)
    name = os.path.splitext(os.path.basename(path))[0]
    spec = importlib.util.spec_from_file_location(name + "_refill_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _book(cash, realized, open_lots=None):
    return dict(cash=cash, open=open_lots or {}, fills_day="2026-11-05", fills_today=0,
                realized_pnl=realized, realized_gross=realized, closed=[], would_halt=[],
                peak_equity=C.START_BALANCE_SOL)


def _write(d, books):
    doc = dict(cfg_version=C.CFG_VERSION, last_run_ts=T0, voids=[], books=books)
    with open(os.path.join(d, "positions.json"), "w") as fh:
        json.dump(doc, fh)


def _sell_row():
    row = {k: "" for k in ledger.TRADE_COLS}
    row.update(time=str(T0), book="F", agent="EXIT", mint="MintOld" + "x" * 20, symbol="OLD",
               action="sell", frac="1", sol="0.2", quote_px="1", why="hard_stop", pnl="-0.11",
               gross_pnl="-0.1", net_pnl="-0.11", cost_model="")
    return row


class Refill(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.mk = Market()
        self.st = dict(checks=12, data_failures=0)

    def step(self, now, leads=(), run_id=None):
        return fills.step(self.d, now, run_id or f"R{now}", now // 60, list(leads), self.st,
                          self.mk.sell, self.mk.liquidity)

    def test_stall_tops_up_once_and_leaves_past_trades(self):
        self.assertEqual(C.TICKET_SOL, 0.5)
        self.assertEqual(C.START_BALANCE_SOL, 10.0)
        self.assertTrue(C.FROZEN)
        books = {name: _book(cash, pnl) for name, (cash, pnl) in STALL.items()}
        _write(self.d, books)
        ledger.trade(self.d, _sell_row())
        path = os.path.join(self.d, "trades.csv")
        before = open(path, "rb").read()
        now = T0 + 60
        self.step(now)
        after = open(path, "rb").read()
        self.assertTrue(after.startswith(before))
        state = json.load(open(os.path.join(self.d, "positions.json")))
        self.assertEqual([r["id"] for r in state["refills"]], [fills.REFILL_ID])
        event = state["refills"][0]
        self.assertEqual(event["at"], fills.iso(now))
        self.assertEqual(event["reason"], "owner-approved refill Nov 4 2026, rule change")
        self.assertEqual(event["ticket_sol"], 0.5)
        self.assertEqual(event["target_cash"], 5.0)
        for name, (cash, pnl) in STALL.items():
            b = state["books"][name]
            self.assertEqual(b["cash"], 5.0)
            self.assertEqual(b["realized_pnl"], pnl)
            self.assertEqual(b["open"], {})
            self.assertEqual(b["closed"], [])
            self.assertEqual(b["peak_equity"], 5.0)
            self.assertEqual(b["would_halt"], [])
            info = event["books"][name]
            self.assertEqual(info["cash_before"], cash)
            self.assertAlmostEqual(info["added"], 5.0 - cash)
            self.assertEqual(info["cash_after"], 5.0)
            self.assertEqual(info["peak_before"], C.START_BALANCE_SOL)
            self.assertEqual(info["peak_after"], 5.0)
            self.assertEqual(info["halt_cleared"], [])
        rows = trades(self.d)
        old = [r for r in rows if r["symbol"] == "OLD"]
        self.assertEqual(len(old), 1)
        self.assertEqual(old[0]["pnl"], "-0.11")
        self.assertEqual(old[0]["action"], "sell")
        refills = [r for r in rows if r["action"] == "refill"]
        self.assertEqual([r["book"] for r in refills], ["F", "R", "S"])
        for r in refills:
            self.assertEqual(r["why"], event["reason"])
            self.assertEqual(r["pnl"], "")
            self.assertEqual(r["gross_pnl"], "")
            self.assertEqual(r["net_pnl"], "")
            self.assertEqual(r["agent"], "REFILL")
            self.assertEqual(r["time"], str(now))
            self.assertAlmostEqual(float(r["sol"]), event["books"][r["book"]]["added"])
        stats = fills.window_stats(rows, now)
        self.assertEqual(stats["windows"]["7"]["counted_closes"], 0)
        self.assertAlmostEqual(stats["windows"]["7"]["older_net_pnl"], -0.11)
        self.assertEqual(stats["windows"]["7"]["older_closes"], 1)
        paper = json.load(open(os.path.join(self.d, "paper.json")))
        self.assertEqual(paper["ticket_size"], 0.5)
        self.assertEqual(paper["refills"][0]["id"], fills.REFILL_ID)
        self.assertEqual(paper["summaries"][0]["type"], "refill")
        self.assertIn("F +4.528189695", paper["summaries"][0]["note"])
        self.assertIn("R +4.800669174", paper["summaries"][0]["note"])
        self.assertIn("S +4.5978303", paper["summaries"][0]["note"])
        for name in "FRS":
            self.assertEqual(paper["books"][name]["cash"], 5.0)
            self.assertEqual(paper["books"][name]["realized_pnl"], round(STALL[name][1], 9))
        from jsonschema import Draft202012Validator
        sch = json.load(open(os.path.join(REPO, "schema", "paper-book.schema.json")))
        self.assertEqual(list(Draft202012Validator(sch).iter_errors(paper)), [])

        self.step(now + 60)
        again = json.load(open(os.path.join(self.d, "positions.json")))
        self.assertEqual(len(again["refills"]), 1)
        self.assertEqual([r["book"] for r in trades(self.d) if r["action"] == "refill"], ["F", "R", "S"])
        for name in "FRS":
            self.assertEqual(again["books"][name]["cash"], 5.0)

        self.step(now + 120, [lead(1)])
        bought = json.load(open(os.path.join(self.d, "positions.json")))
        self.assertEqual(len(bought["books"]["F"]["open"]), 1)
        self.assertEqual(len(bought["refills"]), 1)
        buys = [r for r in trades(self.d) if r["action"] == "buy"]
        self.assertTrue(buys)
        self.assertTrue(all(float(r["sol"]) == 0.5 for r in buys))
        self.assertLess(bought["books"]["F"]["cash"], 5.0)
        self.assertEqual(bought["books"]["F"]["realized_pnl"], STALL["F"][1])

        twin = _load("scripts/analysis/random_twin_check.py")
        day14 = _load("solana-desk/specs/day14_verdict.py")
        lots = twin.build_closes(trades(self.d))
        self.assertEqual(len(lots), 0)  # the seeded sell has no matching buy; refill rows add none
        seeded = [
            dict(book="F", mint="MintOld", action="buy", time=str(T0 - 10), frac="1", pnl="", cost_model=""),
            dict(book="F", mint="MintOld", action="sell", time=str(T0), frac="1", pnl="-0.11", cost_model=""),
        ] + [r for r in trades(self.d) if r["action"] == "refill"]
        built = twin.build_closes(seeded)
        self.assertEqual(len(built), 1)
        self.assertAlmostEqual(built[0]["ret"], -0.11 / 0.5)
        closes, ok = day14.build_closes(seeded)
        self.assertTrue(ok)
        self.assertEqual(len(closes), 1)
        self.assertAlmostEqual(closes[0]["pnl"], -0.11)

    def test_stall_clears_halts_and_s_opens(self):
        """One step refills, S buys, and the first later mark does not log a new halt."""
        books = {name: _book(cash, pnl) for name, (cash, pnl) in STALL.items()}
        books["F"]["would_halt"] = [dict(day="2026-11-05", ts="2026-11-05T00:00:37Z", book="F", drawdown=0.9528)]
        books["R"]["would_halt"] = [dict(day="2026-11-05", ts="2026-11-05T00:00:37Z", book="R", drawdown=0.9801)]
        books["S"]["peak_equity"] = 10.89800187930826
        books["S"]["would_halt"] = [dict(day="2026-11-05", ts="2026-11-05T00:00:37Z", book="S", drawdown=0.9598)]
        _write(self.d, books)
        now = T0 + 60
        # S only gets a slot when F fills, so the step needs a pass lead and a safety lead.
        self.step(now, [lead(1), lead(2, "safety", "risk_score")])
        state = json.load(open(os.path.join(self.d, "positions.json")))
        event = state["refills"][0]
        for name in "FRS":
            self.assertEqual(state["books"][name]["would_halt"], [], name)
        self.assertEqual(len(state["books"]["S"]["open"]), 1)
        self.assertAlmostEqual(event["books"]["S"]["peak_before"], 10.89800187930826)
        self.assertEqual(event["books"]["S"]["peak_after"], 5.0)
        self.assertEqual(event["books"]["S"]["halt_cleared"], books["S"]["would_halt"])
        self.assertEqual(len(event["books"]["F"]["halt_cleared"]), 1)
        self.mk.ratio[lead(1)["mint"]] = 1.0
        self.mk.ratio[lead(2)["mint"]] = 1.0
        self.step(now + 600, [], run_id="mark")
        marked = json.load(open(os.path.join(self.d, "positions.json")))
        for name in "FRS":
            self.assertEqual(marked["books"][name]["would_halt"], [], name)
        self.assertEqual(len(marked["books"]["S"]["open"]), 1)
        self.assertEqual(len(marked["refills"]), 1)

    def test_readers_ignore_refill_rows(self):
        status = _load("scripts/status/daily_status.py")
        rows = [
            dict(book="F", action="buy", mint="M", time=str(T0), frac="1", pnl="", sol="0.5", cost_model=""),
            dict(book="F", action="sell", mint="M", time=str(T0 + 10), frac="1", pnl="-0.11", sol="0.2", cost_model=""),
            dict(book="F", action="refill", mint="", time=str(T0 + 20), frac="", pnl="", sol="4.528189695", cost_model=""),
        ]
        closed, incomplete = status.book_closes(rows, "F")
        self.assertEqual(incomplete, 0)
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["pnl"], Decimal("-0.11"))
        stats = fills.window_stats(rows, T0 + 30)
        self.assertEqual(stats["windows"]["7"]["counted_closes"], 0)
        self.assertAlmostEqual(stats["windows"]["7"]["older_net_pnl"], -0.11)
        self.assertNotAlmostEqual(stats["windows"]["7"]["older_net_pnl"], -0.11 + 4.528189695)

    def test_skips_when_any_book_can_still_buy_or_is_open(self):
        books = {name: _book(0.2, -1) for name in "FRS"}
        books["S"]["cash"] = 0.6
        _write(self.d, books)
        self.step(T0 + 60)
        state = json.load(open(os.path.join(self.d, "positions.json")))
        self.assertNotIn("refills", state)
        self.assertEqual(state["books"]["F"]["cash"], 0.2)
        self.assertEqual(state["books"]["S"]["cash"], 0.6)
        self.assertFalse(os.path.exists(os.path.join(self.d, "trades.csv")))

        books = {name: _book(0.2, -1) for name in "FRS"}
        books["F"]["open"] = {"F-old": {"mint": "m", "frac": 1}}
        _write(self.d, books)
        state = fills.load(self.d)
        self.assertIsNone(fills.apply_cash_refill(self.d, state, T0 + 60))
        self.assertNotIn("refills", state)
        self.assertEqual(state["books"]["R"]["cash"], 0.2)
        self.assertEqual(state["books"]["F"]["cash"], 0.2)

    def test_fresh_book_is_unchanged(self):
        self.step(T0, [lead(1)])
        state = json.load(open(os.path.join(self.d, "positions.json")))
        self.assertNotIn("refills", state)
        self.assertGreater(state["books"]["F"]["cash"], 5.0)
        self.assertEqual([r["action"] for r in trades(self.d)], ["buy", "buy"])

    def test_logged_rows_are_not_appended_twice_if_state_was_not_saved(self):
        books = {name: _book(cash, pnl) for name, (cash, pnl) in STALL.items()}
        _write(self.d, books)
        now = T0 + 60
        for name, (cash, _) in STALL.items():
            fills._refill_row(self.d, now, name, 5.0 - cash)
        self.step(now + 30, run_id="retry")
        state = json.load(open(os.path.join(self.d, "positions.json")))
        self.assertEqual(len(state["refills"]), 1)
        self.assertEqual([r["book"] for r in trades(self.d) if r["action"] == "refill"], ["F", "R", "S"])
        for name in "FRS":
            self.assertEqual(state["books"][name]["cash"], 5.0)


if __name__ == "__main__":
    unittest.main()
