"""Replay tests. Fixtures are synthetic. No book ledger and no network."""

import json
import subprocess
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from tools.replay.engine import (
    C,
    DESK_MODE,
    RuleError,
    buy_quote,
    fills,
    format_report,
    replay,
    sell_mark,
)
from tools.replay.load import BLIND_START_S, BLIND_UNTIL, ReplayRefused, load_bars

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINT_A = "SYNTHMINT00000000000000000000000001"
MINT_B = "SYNTHMINT00000000000000000000000002"
T0 = 1_710_000_000
BEFORE = datetime(2026, 11, 25, 14, 59, tzinfo=timezone.utc)
AT = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)


def _bar(mint, t_s, sol, token=1000.0, rules=None, sells=2, age=3000, status=None):
    bar = {"mint": mint, "t_s": t_s, "sol": sol, "token": token, "sells_h1": sells, "age_s": age}
    if rules is not None:
        bar["rules"] = rules
    if status is not None:
        bar["status"] = status
    return bar


def _rule(rule_id="fixed", entry=None, exit_spec=None, mode="desk_v65", **extra):
    rule = {"id": rule_id, "exit_mode": mode, "entry": {} if entry is None else entry}
    if exit_spec is not None:
        rule["exit"] = exit_spec
    rule.update(extra)
    return rule


def _sol_for_return(sol0, token0, ret, lp_fee_bps=0.0):
    tokens, entry_px = buy_quote(sol0, token0, C.TICKET_SOL, lp_fee_bps)
    return entry_px * (1.0 + ret) * (token0 + tokens)


class Replay(unittest.TestCase):
    def test_flat_round_trip_matches_desk_cost_without_future_bars_required(self):
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + 100, 100.0)]
        result = replay(bars, {"seed": 1, "rules": [_rule()]})
        self.assertEqual(result["cost_model"], "v3-exit-aware")
        self.assertFalse(result["lp_fee_in_desk_cost"])
        self.assertEqual(result["lp_fee_bps_default"], 0)
        trade = result["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "stale")
        self.assertEqual(trade["exit_fixed"]["mode"], DESK_MODE)
        tokens, entry_px = buy_quote(100.0, 1000.0, C.TICKET_SOL, 0)
        mark = sell_mark(100.0, 1000.0, tokens, 0)
        cost, h_entry = fills.entry_cost(100.0)
        pos = {"entry": entry_px, "entry_cost": cost}
        dx = fills.gross_proceeds(pos, 1.0, mark)
        verified = fills.verified_impact_bps(100.0, dx)
        haircut = fills.haircut_frac(100.0, verified, "stale")
        gross, net = fills.slice_pnl(pos, 1.0, mark, haircut)
        self.assertAlmostEqual(trade["gross_pnl"], gross)
        self.assertAlmostEqual(trade["net_pnl"], net)
        self.assertAlmostEqual(h_entry, (60 + 0.25 * (10000 * 0.5 / 100.5)) / 10000)
        self.assertLess(trade["net_pnl"], 0)
        self.assertEqual(result["rules"][0]["baseline"]["draws"], 1000)
        self.assertGreater(result["rules"][0]["baseline"]["effective_n"], 0)

    def test_single_bar_still_enters_and_closes(self):
        result = replay([_bar(MINT_A, T0, 100.0)], {"rules": [_rule()]})
        trade = result["rules"][0]["trades"][0]
        self.assertEqual(result["rules"][0]["n"], 1)
        self.assertEqual(trade["entry_t"], T0)
        self.assertEqual(trade["exit_reason"], "stale")
        self.assertEqual(trade["exit_t"], T0)

    def test_gap_closes_on_last_known_mark_not_the_late_bar(self):
        late = T0 + int(C.OUTAGE_VOID_H * 3600) + 10
        bars = [
            _bar(MINT_A, T0, 100.0, sells=2),
            _bar(MINT_A, late, 1.0, sells=0),
        ]
        rule = _rule(entry={"min_sells_h1": 1}, exit_spec={"max_gap_s": C.OUTAGE_VOID_H * 3600})
        trade = replay(bars, {"rules": [rule]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "stale")
        self.assertEqual(trade["exit_t"], T0)
        tokens, entry_px = buy_quote(100.0, 1000.0, C.TICKET_SOL, 0)
        self.assertAlmostEqual(trade["slices"][-1]["px"], entry_px)
        crashed = sell_mark(1.0, 1000.0, tokens, 0)
        self.assertNotAlmostEqual(trade["slices"][-1]["px"], crashed)

    def test_rug_is_entered_and_closed_pool_gone(self):
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + 300, 0.0, token=0.0)]
        row = replay(bars, {"rules": [_rule("rug")]})["rules"][0]
        self.assertEqual(row["n"], 1)
        trade = row["trades"][0]
        self.assertEqual(trade["entry_t"], T0)
        self.assertEqual(trade["exit_reason"], "pool_gone")
        self.assertEqual(trade["exit_t"], T0 + 300)
        self.assertLess(trade["net_pnl"], 0)
        self.assertEqual(trade["slices"][-1]["px"], 0.0)

    def test_plus_15_at_time_stop_is_kept(self):
        sol2 = _sol_for_return(100.0, 1000.0, 0.15)
        stop = C.EXIT["time_stop_min"] * 60
        bars = [
            _bar(MINT_A, T0, 100.0),
            _bar(MINT_A, T0 + stop, sol2),
            _bar(MINT_A, T0 + stop + 60, sol2),
        ]
        trade = replay(bars, {"rules": [_rule("kept")]})["rules"][0]["trades"][0]
        self.assertNotIn("time_stop", [sl["why"] for sl in trade["slices"]])
        self.assertGreater(trade["exit_t"], T0 + stop)
        self.assertEqual(trade["exit_reason"], "stale")
        tokens, entry_px = buy_quote(100.0, 1000.0, C.TICKET_SOL, 0)
        mark = sell_mark(sol2, 1000.0, tokens, 0)
        self.assertGreaterEqual(mark / entry_px - 1, 0.10)
        self.assertLess(mark / entry_px - 1, C.EXIT["ladder"][0][0])

    def test_plus_5_at_time_stop_is_sold(self):
        sol2 = _sol_for_return(100.0, 1000.0, 0.05)
        stop = C.EXIT["time_stop_min"] * 60
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + stop, sol2)]
        trade = replay(bars, {"rules": [_rule("flatish")]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "time_stop")
        self.assertEqual(trade["exit_t"], T0 + stop)

    def test_hard_stop_uses_stress_addon(self):
        # A 30% reserve drop is past the 25% hard stop and short of the 40% liquidity pull.
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + 50, 70.0)]
        trade = replay(bars, {"rules": [_rule()]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "hard_stop")
        sl = trade["slices"][0]
        verified = fills.verified_impact_bps(sl["pool_sol"], sl["dx_sol"])
        stressed = fills.haircut_frac(sl["pool_sol"], verified, "hard_stop")
        calm = fills.haircut_frac(sl["pool_sol"], verified, "stale")
        self.assertAlmostEqual(sl["exit_haircut"], stressed)
        self.assertAlmostEqual(stressed - calm, C.COST["stress_exit_bps"] / 10000)

    def test_no_route_three_times_and_one_stale_mark_does_not_sell(self):
        route = [
            _bar(MINT_A, T0, 100.0),
            _bar(MINT_A, T0 + 30, 100.0, status="no_route"),
            _bar(MINT_A, T0 + 60, 100.0, status="no_route"),
            _bar(MINT_A, T0 + 90, 100.0, status="no_route"),
        ]
        trade = replay(route, {"rules": [_rule("routes")]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "no_route_3x")
        stale = [
            _bar(MINT_A, T0, 100.0),
            _bar(MINT_A, T0 + 30, 100.0, status="temp_error"),
            _bar(MINT_A, T0 + 60, 100.0),
        ]
        held = replay(stale, {"rules": [_rule("stale")]})["rules"][0]["trades"][0]
        self.assertNotEqual(held["exit_t"], T0 + 30)
        self.assertGreater(held["exit_t"], T0 + 30)

    def test_entry_ignores_later_prices(self):
        passed = {"sol_reserve": "pass"}
        bars = [
            _bar(MINT_A, T0, 10.0, rules=passed),
            _bar(MINT_A, T0 + 100, 80.0, rules=passed),
            _bar(MINT_A, T0 + 200, 80.0, rules=passed),
        ]
        rule = _rule(entry={"min_sol": 50, "rules_all_pass": True})
        trade = replay(bars, {"rules": [rule]})["rules"][0]["trades"][0]
        self.assertEqual(trade["entry_t"], T0 + 100)

    def test_book_caps_and_cash_floor(self):
        bars = []
        for i in range(6):
            mint = f"SYNTH-CAP-{i}"
            bars.append(_bar(mint, T0, 100.0, sells=2))
            bars.append(_bar(mint, T0 + 60, 100.0, sells=0))
        capped = replay(bars, {"rules": [_rule("caps", entry={"min_sells_h1": 1})]})["rules"][0]
        self.assertEqual(capped["n"], C.MAX_OPEN_F)
        self.assertGreaterEqual(capped["skipped_open_cap"], 1)
        cost, _h = fills.entry_cost(100.0)
        poor = _rule("poor", entry={"min_sells_h1": 1}, starting_cash=cost - 1e-6)
        cashless = replay([_bar(MINT_A, T0, 100.0, sells=2)], {"rules": [poor]})["rules"][0]
        self.assertEqual(cashless["n"], 0)
        self.assertGreaterEqual(cashless["skipped_cash"], 1)
        day_bars = []
        for i in range(C.MAX_NEW_F_PER_DAY + 1):
            mint = f"SYNTH-DAY-{i}"
            start = T0 + i * 120
            day_bars.append(_bar(mint, start, 100.0, sells=2))
            day_bars.append(_bar(mint, start + 60, 100.0, sells=0))
        fixed = _rule(
            "day",
            entry={"min_sells_h1": 1},
            mode="fixed",
            exit_spec={"max_hold_s": 30, "hard_stop": -0.25},
        )
        day = replay(day_bars, {"rules": [fixed]})["rules"][0]
        self.assertEqual(day["n"], C.MAX_NEW_F_PER_DAY)
        self.assertGreaterEqual(day["skipped_day_cap"], 1)

    def test_null_is_same_universe_with_percentile_and_effective_n(self):
        bars = []
        for name, nxt in (
            ("SYNTH-A", 200.0), ("SYNTH-B", 200.0), ("SYNTH-C", 200.0), ("SYNTH-D", 200.0),
            ("SYNTH-E", 40.0), ("SYNTH-F", 40.0),
            ("SYNTH-THIN", 1.0),
        ):
            sol0 = 10.0 if name == "SYNTH-THIN" else 100.0
            bars.append(_bar(name, T0, sol0, sells=2))
            bars.append(_bar(name, T0 + 60, nxt if name != "SYNTH-THIN" else 1.0, sells=0))
        rule = _rule("pick", entry={"min_sol": 50, "min_sells_h1": 1})
        first = replay(bars, {"seed": 3, "rules": [rule]})
        second = replay(bars, {"seed": 3, "rules": [rule]})
        row = first["rules"][0]
        self.assertEqual(row["n"], 4)
        self.assertEqual(row["baseline"]["eligible_n"], 6)
        self.assertEqual(row["baseline"]["draws"], 1000)
        self.assertGreater(row["baseline"]["percentile"], 90)
        self.assertEqual(row["baseline"]["cluster_by"], "mint")
        self.assertAlmostEqual(row["baseline"]["effective_n"], 4.0)
        lo, hi = row["baseline"]["bootstrap_ci"]
        self.assertLessEqual(lo, hi)
        self.assertEqual({t["mint"] for t in row["trades"]}, {"SYNTH-A", "SYNTH-B", "SYNTH-C", "SYNTH-D"})
        hours = set(row["baseline"]["hour_counts"])
        self.assertEqual(len(hours), 1)
        self.assertEqual(format_report(first), format_report(second))
        self.assertIn("percentile=", format_report(first))
        self.assertIn("effective_n=", format_report(first))

    def test_lp_fee_defaults_to_zero_because_the_desk_has_none(self):
        plain = buy_quote(100.0, 1000.0, C.TICKET_SOL, 0)
        charged = buy_quote(100.0, 1000.0, C.TICKET_SOL, 30)
        self.assertNotAlmostEqual(plain[0], charged[0])
        self.assertNotIn("lp_fee", dir(C))
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + 100, 100.0)]
        base = replay(bars, {"rules": [_rule("a")]})["rules"][0]["net_pnl"]
        other = replay(bars, {"lp_fee_bps": 30, "rules": [_rule("a")]})["rules"][0]["net_pnl"]
        self.assertNotAlmostEqual(base, other)

    def test_fixed_mode_still_requires_a_hold_and_desk_mode_does_not(self):
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + 100, 160.0)]
        with self.assertRaises(RuleError):
            replay(bars, {"rules": [_rule("open", mode="fixed", exit_spec={"hard_stop": -0.25})]})
        trade = replay(bars, {"rules": [_rule("desk")]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_fixed"]["mode"], DESK_MODE)

    def test_death_rows_are_ignored_and_bars_need_numbers(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest-synth.jsonl").write_text(
                "\n".join([
                    json.dumps({"type": "scan", "t_s": T0}),
                    json.dumps({
                        "type": "candidate", "mint": MINT_A, "t_s": T0,
                        "inputs": {"sol": 100, "token": 1000, "txns_h1": {"sells": 1}},
                    }),
                    json.dumps({"type": "candidate", "mint": "SYNTH-BLANK", "t_s": T0, "inputs": {"sol": None, "token": 1}}),
                    json.dumps({"type": "obs", "mint": "SYNTH-DEATH-ROW", "t_s": T0, "sol": 5, "token": 5}),
                ]) + "\n",
                encoding="utf-8",
            )
            (root / "pool-snaps-synth.jsonl").write_text(
                "\n".join([
                    json.dumps({"mint": MINT_A, "t_s": T0, "sol": 100, "token": 1000}),
                    json.dumps({"mint": MINT_A, "t_s": T0 + 60, "sol": 100, "token": 1000}),
                    json.dumps({"mint": MINT_A, "t_s": T0 + 120, "sol": "nope", "token": 1000}),
                ]) + "\n",
                encoding="utf-8",
            )
            (root / "death-synth.jsonl").write_text(
                json.dumps({"type": "obs", "mint": "SYNTH-DEATH-FILE", "t_s": T0, "sol": 999999, "token": 1, "price_drop": 0.9}) + "\n",
                encoding="utf-8",
            )
            (root / "series.jsonl").write_text(
                json.dumps({"mint": "SYNTH-OTHER", "t_s": T0, "sol": 100, "token": 1000}) + "\n",
                encoding="utf-8",
            )
            bars = load_bars(root, now=BEFORE)
            self.assertEqual({b["mint"] for b in bars}, {MINT_A})
            self.assertEqual(sorted(b["t_s"] for b in bars), [T0, T0 + 60])
            self.assertNotIn("999999", json.dumps(bars))

    def test_blinding_guard_boundaries(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            early = root / "manifest-early.jsonl"
            early.write_text(json.dumps({
                "type": "candidate", "mint": MINT_A, "t_s": BLIND_START_S - 1,
                "inputs": {"sol": 80, "token": 1000},
            }) + "\n", encoding="utf-8")
            self.assertEqual(len(load_bars(early, now=BEFORE)), 1)
            late = root / "manifest-late.jsonl"
            late.write_text(json.dumps({
                "type": "candidate", "mint": MINT_A, "t_s": BLIND_START_S,
                "inputs": {"sol": 424242.5, "token": 1000},
            }) + "\n", encoding="utf-8")
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(late, now=BEFORE)
            self.assertNotIn("424242.5", str(caught.exception))
            self.assertEqual(len(load_bars(late, now=AT)), 1)
            self.assertEqual(len(load_bars(late, now=BEFORE, allow_blinded=True)), 1)
            self.assertEqual(AT, BLIND_UNTIL)
            v2 = root / "solana-desk" / "v2"
            v2.mkdir(parents=True)
            (v2 / "manifest-old.jsonl").write_text(json.dumps({
                "type": "candidate", "mint": MINT_A, "t_s": T0,
                "inputs": {"sol": 11, "token": 1000},
            }) + "\n", encoding="utf-8")
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(v2, now=BEFORE)
            self.assertIn("solana-desk/v2", str(caught.exception))
            self.assertNotIn("11", str(caught.exception))
            self.assertEqual(len(load_bars(v2, now=AT)), 1)
            self.assertEqual(len(load_bars(v2, now=BEFORE, allow_blinded=True)), 1)

    def test_refuses_ledgers_and_locked_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("positions.json", "trades.csv", "paper.json", "captures.jsonl"):
                path = root / name
                path.write_text('{"onset": 12.5, "d_bps": 99, "pnl": 123456}\n', encoding="utf-8")
                with self.assertRaises(ReplayRefused) as caught:
                    load_bars(path, now=AT)
                text = str(caught.exception)
                self.assertNotIn("123456", text)
                self.assertNotIn("12.5", text)
            locked = root / "pool-snaps-locked.jsonl"
            locked.write_text('{"mint": "SYNTH", "t_s": 1, "sol": 1, "token": 1, "onset": 4, "d_bps": 8}\n', encoding="utf-8")
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(locked, now=AT)
            self.assertNotIn("8", str(caught.exception))
            nested = root / "lst-count-data"
            nested.mkdir()
            banned = nested / "manifest-x.jsonl"
            banned.write_text('{"mint": "SYNTH", "t_s": 1, "sol": 1, "token": 1}\n', encoding="utf-8")
            with self.assertRaises(ReplayRefused):
                load_bars(banned, now=AT)

    def test_fixture_files_and_cli_are_read_only(self):
        before = {p: p.stat().st_mtime_ns for p in FIXTURES.iterdir() if p.is_file()}
        proc = subprocess.run(
            [sys.executable, "-m", "tools.replay", "--series", str(FIXTURES), "--rules", str(FIXTURES / "rules.json")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rule synth-hold trades=1", proc.stdout)
        self.assertIn("percentile=", proc.stdout)
        self.assertIn("effective_n=", proc.stdout)
        after = {p: p.stat().st_mtime_ns for p in FIXTURES.iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_sources_do_not_open_the_network(self):
        import re
        banned = re.compile(r"(?m)^\s*(import|from)\s+(urllib|requests|socket|http\.client)\b|urlopen\(")
        for rel in (
            "tools/replay/load.py",
            "tools/replay/engine.py",
            "tools/replay/__main__.py",
            "tools/idle_books_check.py",
        ):
            text = (ROOT / rel).read_text(encoding="utf-8")
            self.assertIsNone(banned.search(text), rel)
            self.assertNotIn("_covered", text)


if __name__ == "__main__":
    unittest.main()
