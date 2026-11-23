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
    DEVIATION_2H,
    RuleError,
    _null_verdict,
    buy_quote,
    fills,
    format_report,
    pumpswap_fee_bps,
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


def _bar(mint, t_s, sol, token=1000.0, rules=None, sells=2, age=3000, status=None, origin=None):
    bar = {"mint": mint, "t_s": t_s, "sol": sol, "token": token, "sells_h1": sells, "age_s": age}
    if rules is None:
        rules = {"s2": "pass"}
    if rules is not False:
        bar["rules"] = rules
    if status is not None:
        bar["status"] = status
    if origin is not None:
        bar["origin"] = origin
    return bar


def _rule(rule_id="fixed", entry=None, exit_spec=None, mode="desk_v65", **extra):
    rule = {"id": rule_id, "exit_mode": mode, "entry": {} if entry is None else entry}
    if exit_spec is not None:
        rule["exit"] = exit_spec
    rule.update(extra)
    return rule


def _sol_for_return(sol0, token0, ret, swap_fee_bps=125.0):
    tokens, entry_px = buy_quote(sol0, token0, C.TICKET_SOL, swap_fee_bps)
    return entry_px * (1.0 + ret) * (token0 + tokens)


class Replay(unittest.TestCase):
    def test_flat_round_trip_matches_desk_cost_without_future_bars_required(self):
        stop = C.EXIT["time_stop_min"] * 60
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + stop, 100.0)]
        result = replay(bars, {"seed": 1, "rules": [_rule()]})
        self.assertEqual(result["cost_model"], "v3-exit-aware")
        self.assertEqual(result["swap_fee_bps_default"], 125.0)
        self.assertIn("proxy inputs:", result["proxy_inputs"])
        trade = result["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "time_stop")
        self.assertEqual(trade["exit_fixed"]["mode"], DESK_MODE)
        self.assertEqual(result["rules"][0]["close_reasons"].get("time_stop"), 1)
        self.assertNotIn("stale", result["rules"][0]["close_reasons"])
        tokens, entry_px = buy_quote(100.0, 1000.0, C.TICKET_SOL, 125.0)
        mark = sell_mark(100.0, 1000.0, tokens, 125.0)
        cost, h_entry = fills.entry_cost(100.0)
        pos = {"entry": entry_px, "entry_cost": cost}
        dx = fills.gross_proceeds(pos, 1.0, mark)
        verified = fills.verified_impact_bps(100.0, dx)
        haircut = fills.haircut_frac(100.0, verified, "time_stop")
        gross, net = fills.slice_pnl(pos, 1.0, mark, haircut)
        self.assertAlmostEqual(trade["gross_pnl"], gross)
        self.assertAlmostEqual(trade["net_pnl"], net)
        self.assertAlmostEqual(h_entry, (60 + 0.25 * (10000 * 0.5 / 100.5)) / 10000)
        self.assertLess(trade["net_pnl"], 0)
        self.assertEqual(result["rules"][0]["baseline"]["draws"], 1000)
        self.assertEqual(result["rules"][0]["baseline"]["statistic"], "sum")

    def test_single_bar_stays_open(self):
        row = replay([_bar(MINT_A, T0, 100.0)], {"rules": [_rule()]})["rules"][0]
        self.assertEqual(row["n"], 0)
        self.assertEqual(row["open_n"], 1)
        self.assertEqual(row["close_reasons"], {})

    def test_gap_over_max_closes_missed_at_entry_cost(self):
        late = T0 + int(C.OUTAGE_VOID_H * 3600) + 10
        bars = [
            _bar(MINT_A, T0, 100.0, sells=2),
            _bar(MINT_A, late, 1.0, sells=0),
        ]
        rule = _rule(entry={"min_sells_h1": 1}, exit_spec={"max_gap_s": C.OUTAGE_VOID_H * 3600})
        row = replay(bars, {"rules": [rule]})["rules"][0]
        cost, _h = fills.entry_cost(100.0)
        self.assertEqual(row["n"], 0)
        self.assertEqual(row["unresolved_n"], 1)
        self.assertAlmostEqual(row["net_lower"], -cost)
        self.assertGreater(row["net_upper"], row["net_lower"])
        self.assertNotIn("missed", row["close_reasons"])
        self.assertNotIn("stale", row["close_reasons"])

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
        row = replay(bars, {"rules": [_rule("kept")]})["rules"][0]
        self.assertEqual(row["n"], 0)
        self.assertEqual(row["open_n"], 1)
        self.assertNotIn("time_stop", row["close_reasons"])
        self.assertNotIn("stale", row["close_reasons"])
        tokens, entry_px = buy_quote(100.0, 1000.0, C.TICKET_SOL, 125.0)
        mark = sell_mark(sol2, 1000.0, tokens, 125.0)
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

    def test_no_pool_error_row_books_pool_gone_at_entry_cost(self):
        bars = [
            _bar(MINT_A, T0, 100.0),
            {"mint": MINT_A, "t_s": T0 + 60, "sol": None, "token": None, "error": "no_pool"},
        ]
        row = replay(bars, {"rules": [_rule("dead")]})["rules"][0]
        trade = row["trades"][0]
        cost, _h = fills.entry_cost(100.0)
        self.assertEqual(trade["exit_reason"], "pool_gone")
        self.assertAlmostEqual(trade["net_pnl"], -cost)
        self.assertEqual(row["close_reasons"].get("pool_gone"), 1)
        self.assertNotIn("no_route_3x", row["close_reasons"])
        text = format_report({"proxy_inputs": replay(bars, {"rules": [_rule("dead")]})["proxy_inputs"], "rules": []})
        self.assertIn("no_route_3x can't fire", text)
        self.assertIn("pool math", text)

    def test_entry_ignores_later_prices(self):
        passed = {"sol_reserve": "pass"}
        bars = [
            _bar(MINT_A, T0, 10.0, rules=passed),
            _bar(MINT_A, T0 + 100, 80.0, rules=passed),
            _bar(MINT_A, T0 + 200, 80.0, rules=passed),
        ]
        rule = _rule(entry={"min_sol": 50, "rules_all_pass": True})
        row = replay(bars, {"rules": [rule]})["rules"][0]
        self.assertEqual(row["open_n"], 1)
        self.assertEqual(row["open"][0]["entry_t"], T0 + 100)

    def test_book_caps_and_cash_floor(self):
        bars = []
        for i in range(6):
            mint = f"SYNTH-CAP-{i}"
            bars.append(_bar(mint, T0, 100.0, sells=2))
            bars.append(_bar(mint, T0 + 60, 100.0, sells=0))
        capped = replay(bars, {"rules": [_rule("caps", entry={"min_sells_h1": 1})]})["rules"][0]
        self.assertEqual(capped["open_n"], C.MAX_OPEN_F)
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
        self.assertEqual(row["open_n"], 4)
        self.assertEqual(row["baseline"]["eligible_n"], 6)
        self.assertEqual(row["baseline"]["draws"], 1000)
        self.assertGreater(row["baseline"]["percentile_upper"], 90)
        self.assertEqual(row["baseline"]["cluster_by"], "overlap")
        self.assertEqual(row["baseline"]["statistic"], "sum")
        self.assertTrue(row["baseline"]["cluster_warning"])
        self.assertLess(row["baseline"]["n_clusters"], 5)
        self.assertAlmostEqual(row["baseline"]["effective_n"], 1.0)
        lo, hi = row["baseline"]["bootstrap_ci"]
        self.assertLessEqual(lo, hi)
        self.assertEqual({t["mint"] for t in row["open"]}, {"SYNTH-A", "SYNTH-B", "SYNTH-C", "SYNTH-D"})
        hours = set(row["baseline"]["hour_counts"])
        self.assertEqual(len(hours), 1)
        self.assertEqual(format_report(first), format_report(second))
        self.assertIn("proxy inputs:", format_report(first))
        self.assertIn("percentile_upper=", format_report(first))
        self.assertIn("holds past 2h cannot be resolved on v2 data", format_report(first))
        self.assertIn("unresolved_share=", format_report(first))
        self.assertIn("effective_n=", format_report(first))
        self.assertIn("warning=fewer-than-5-clusters", format_report(first))
        # Caps do not bind: one winner the rule takes, one loser outside the rule's filter.
        passed = {"s2": "pass"}
        loose = [
            _bar("SYNTH-WIN", T0, 100.0, sells=2, rules=passed),
            _bar("SYNTH-WIN", T0 + 60, 200.0, sells=0, rules=passed),
            _bar("SYNTH-LOSE", T0, 100.0, sells=2, rules=passed),
            _bar("SYNTH-LOSE", T0 + 60, 40.0, sells=0, rules=passed),
        ]
        picked = _rule("one", entry={"mints": ["SYNTH-WIN"], "min_sells_h1": 1})
        wide = replay(loose, {"seed": 5, "rules": [picked]})["rules"][0]
        self.assertEqual(wide["n"] + wide["open_n"], 1)
        self.assertEqual(wide["skipped_open_cap"], 0)
        self.assertEqual(wide["skipped_day_cap"], 0)
        self.assertGreater(wide["baseline"]["base_universe_n"], wide["baseline"]["eligible_n"])
        self.assertNotAlmostEqual(wide["baseline"]["null_mean_upper"], wide["net_upper"])

    def test_same_trade_at_0_and_125_bps(self):
        stop = C.EXIT["time_stop_min"] * 60
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + stop, 100.0)]
        plain = replay(bars, {"swap_fee_bps": 0, "rules": [_rule("a")]})["rules"][0]["net_upper"]
        charged = replay(bars, {"swap_fee_bps": 125, "rules": [_rule("a")]})["rules"][0]["net_upper"]
        self.assertNotAlmostEqual(plain, charged)
        self.assertEqual(pumpswap_fee_bps(100), 125.0)
        self.assertEqual(pumpswap_fee_bps(98240), 30.0)

    def test_fixed_mode_still_requires_a_hold_and_desk_mode_does_not(self):
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + 100, 160.0)]
        with self.assertRaises(RuleError):
            replay(bars, {"rules": [_rule("open", mode="fixed", exit_spec={"hard_stop": -0.25})]})
        opened = replay(bars, {"rules": [_rule("desk")]})["rules"][0]
        self.assertEqual(opened["open_n"], 1)
        self.assertEqual(opened["open"][0]["exit_fixed"]["mode"], DESK_MODE)

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
            bars = load_bars(root)
            (root / "pool-snaps-dead.jsonl").write_text(
                json.dumps({"mint": MINT_A, "t_s": T0 + 90, "sol": None, "token": None, "error": "no_pool"}) + "\n",
                encoding="utf-8",
            )
            dead = [b for b in load_bars(root) if b.get("error") == "no_pool"]
            self.assertEqual(len(dead), 1)
            self.assertIsNone(dead[0]["sol"])
            self.assertEqual({b["mint"] for b in bars}, {MINT_A})
            self.assertEqual(sorted(b["t_s"] for b in bars), [T0, T0 + 60])
            self.assertNotIn("999999", json.dumps(bars))

    def test_blinding_guard_boundaries(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            early = root / "manifest-early.jsonl"
            early.write_text(json.dumps({
                "type": "candidate", "mint": MINT_A, "t_s": BLIND_START_S - 1,
                "inputs": {"sol": 80, "token": 1000},
            }) + "\n", encoding="utf-8")
            self.assertEqual(len(load_bars(early)), 1)
            late_bar = {"mint": MINT_A, "t_s": BLIND_START_S, "sol": 80.0, "token": 1000.0}
            with self.assertRaises(RuleError) as caught:
                replay([late_bar], {"rules": [_rule("late")]})
            self.assertNotIn("80", str(caught.exception))
            late = root / "manifest-late.jsonl"
            late.write_text(json.dumps({
                "type": "candidate", "mint": MINT_A, "t_s": BLIND_START_S,
                "inputs": {"sol": 424242.5, "token": 1000},
            }) + "\n", encoding="utf-8")
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(late)
            self.assertNotIn("424242.5", str(caught.exception))
            self.assertEqual(AT, BLIND_UNTIL)
            v2 = root / "solana-desk" / "v2"
            v2.mkdir(parents=True)
            (v2 / "manifest-old.jsonl").write_text(json.dumps({
                "type": "candidate", "mint": MINT_A, "t_s": T0,
                "inputs": {"sol": 11, "token": 1000},
            }) + "\n", encoding="utf-8")
            alias = root / "alias-series"
            os.symlink(v2, alias)
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(alias)
            self.assertIn("solana-desk/v2", str(caught.exception))
            self.assertNotIn("11", str(caught.exception))

    def test_refuses_ledgers_and_locked_fields(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("positions.json", "trades.csv", "paper.json", "captures.jsonl"):
                path = root / name
                path.write_text('{"onset": 12.5, "d_bps": 99, "pnl": 123456}\n', encoding="utf-8")
                with self.assertRaises(ReplayRefused) as caught:
                    load_bars(path)
                text = str(caught.exception)
                self.assertNotIn("123456", text)
                self.assertNotIn("12.5", text)
            locked = root / "pool-snaps-locked.jsonl"
            locked.write_text('{"mint": "SYNTH", "t_s": 1, "sol": 1, "token": 1, "onset": 4, "d_bps": 8}\n', encoding="utf-8")
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(locked)
            self.assertNotIn("8", str(caught.exception))
            nested = root / "lst-count-data"
            nested.mkdir()
            banned = nested / "manifest-x.jsonl"
            banned.write_text('{"mint": "SYNTH", "t_s": 1, "sol": 1, "token": 1}\n', encoding="utf-8")
            with self.assertRaises(ReplayRefused):
                load_bars(banned)

    def test_fixture_files_and_cli_are_read_only(self):
        before = {p: p.stat().st_mtime_ns for p in FIXTURES.iterdir() if p.is_file()}
        proc = subprocess.run(
            [sys.executable, "-m", "tools.replay", "--series", str(FIXTURES), "--rules", str(FIXTURES / "rules.json")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rule synth-hold trades=1", proc.stdout)
        self.assertIn("percentile_upper=", proc.stdout)
        self.assertIn("unresolved_share=", proc.stdout)
        self.assertIn("effective_n=", proc.stdout)
        after = {p: p.stat().st_mtime_ns for p in FIXTURES.iterdir() if p.is_file()}
        self.assertEqual(before, after)

    def test_stopped_winner_is_unresolved_not_a_total_loss(self):
        sol2 = _sol_for_return(100.0, 1000.0, 0.15)
        stop = C.EXIT["time_stop_min"] * 60
        gap = int(C.OUTAGE_VOID_H * 3600)
        bars = [
            _bar("SYNTH-WIN", T0, 100.0, sells=2),
            _bar("SYNTH-WIN", T0 + stop, sol2, sells=2),
            _bar("SYNTH-OTHER", T0 + stop + gap + 30, 100.0, sells=0),
        ]
        row = replay(bars, {"rules": [_rule("kept", entry={"min_sells_h1": 1})]})["rules"][0]
        self.assertEqual(row["unresolved_n"], 1)
        self.assertEqual(row["open_n"], 0)
        self.assertLess(row["net_lower"], 0)
        self.assertGreater(row["net_upper"], 0)
        self.assertEqual(row["bounds"], "BOUNDS_DISAGREE")
        self.assertIn(row["verdict"], {"NO_EVIDENCE", "BEATS_NULL", "WORSE"})
        self.assertIn("percentile_lower", row["baseline"])
        self.assertIn("percentile_upper", row["baseline"])
        self.assertIn("bootstrap_ci_lower", row["baseline"])
        self.assertIn("bootstrap_ci_upper", row["baseline"])

    def test_each_reserve_error(self):
        def row_for(error):
            bars = [
                _bar(MINT_A, T0, 100.0, sells=2),
                {"mint": MINT_A, "t_s": T0 + 30, "sol": None, "token": None, "error": error},
                _bar(MINT_A, T0 + 60, 110.0, sells=0),
            ]
            return replay(bars, {"rules": [_rule(error, entry={"min_sells_h1": 1})]})["rules"][0]
        gone = row_for("no_pool")
        self.assertEqual(gone["close_reasons"].get("pool_gone"), 1)
        self.assertEqual(gone["open_n"], 0)
        for error in ("lookup_failed", "reserves_incomplete", "not_a_known_error"):
            held = row_for(error)
            self.assertEqual(held["open_n"], 1, error)
            self.assertEqual(held["unresolved_n"], 0, error)
            self.assertNotIn("pool_gone", held["close_reasons"])

    def test_tiny_pool_and_follow_snap_are_outside_the_null(self):
        rules = {"s2": "pass"}
        bars = [
            _bar("SYNTH-GOOD", T0, 100.0, sells=2, rules=rules),
            _bar("SYNTH-TINY", T0, 0.05, sells=2, rules=rules),
            _bar("SYNTH-FOLLOW", T0, 100.0, sells=2, rules=rules, origin="follow"),
        ]
        row = replay(bars, {"rules": [_rule("scan", entry={"mints": ["SYNTH-GOOD"]})]})["rules"][0]
        self.assertEqual(row["baseline"]["base_universe_n"], 1)
        self.assertEqual(row["open_n"], 1)

    def test_run_book_guard_ignores_a_patched_clock(self):
        import tools.replay.engine as eng
        import tools.replay.load as load
        saved = eng.BLIND_UNTIL, load.BLIND_UNTIL
        eng.BLIND_UNTIL = datetime(1999, 1, 1, tzinfo=timezone.utc)
        load.BLIND_UNTIL = eng.BLIND_UNTIL
        try:
            with self.assertRaises(RuleError):
                eng._run_book(
                    [{"mint": MINT_A, "t_s": BLIND_START_S, "sol": 100.0, "token": 1000.0}],
                    _rule("guard"),
                    None,
                )
        finally:
            eng.BLIND_UNTIL, load.BLIND_UNTIL = saved

    def test_unresolved_frees_the_open_slot_for_a_later_entry(self):
        gap = int(C.OUTAGE_VOID_H * 3600)
        sol2 = _sol_for_return(100.0, 1000.0, 0.15)
        bars = []
        for i in range(4):
            mint = f"SYNTH-W{i}"
            bars.append(_bar(mint, T0, 100.0, sells=2))
            bars.append(_bar(mint, T0 + 60, sol2, sells=2))
        clock1 = T0 + 60 + gap + 10
        bars.append(_bar("SYNTH-CLOCK1", clock1, 100.0, sells=0))
        bars.append(_bar("SYNTH-W4", clock1, 100.0, sells=2))
        bars.append(_bar("SYNTH-W4", clock1 + 60, sol2, sells=2))
        clock2 = clock1 + 60 + gap + 10
        bars.append(_bar("SYNTH-CLOCK2", clock2, 100.0, sells=0))
        bars.append(_bar("SYNTH-W5", clock2, 100.0, sells=2))
        row = replay(bars, {"rules": [_rule("slots", entry={"min_sells_h1": 1})]})["rules"][0]
        self.assertEqual(row["unresolved_n"], 5)
        self.assertEqual(row["open_n"], 1)
        self.assertEqual(row["open"][0]["mint"], "SYNTH-W5")
        self.assertEqual(row["skipped_open_cap"], 0)

    def test_blind_guard_coerces_decimal_and_numpy(self):
        from decimal import Decimal
        late = {"mint": MINT_A, "t_s": Decimal(BLIND_START_S), "sol": 100.0, "token": 1000.0,
                "sells_h1": 2, "rules": {"s2": "pass"}}
        with self.assertRaises(RuleError):
            replay([late], {"rules": [_rule("dec")]})
        early = dict(late, t_s=Decimal(T0))
        self.assertEqual(replay([early], {"rules": [_rule("early")]})["rules"][0]["open_n"], 1)
        with self.assertRaises(RuleError):
            replay([dict(late, t_s="not-a-time")], {"rules": [_rule("bad")]})
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy absent")
        with self.assertRaises(RuleError):
            replay([dict(late, t_s=np.int64(BLIND_START_S))], {"rules": [_rule("np")]})

    def test_percentiles_at_50_are_no_evidence(self):
        self.assertEqual(_null_verdict(50, 50), "NO_EVIDENCE")
        self.assertEqual(_null_verdict(95, 95), "BEATS_NULL")
        self.assertEqual(_null_verdict(5, 5), "WORSE")
        self.assertEqual(_null_verdict(96, 40), "NO_EVIDENCE")

    def test_forced_120m_is_labelled_a_deviation(self):
        sol2 = _sol_for_return(100.0, 1000.0, 0.15)
        stop = C.EXIT["time_stop_min"] * 60
        bars = [_bar(MINT_A, T0, 100.0), _bar(MINT_A, T0 + stop, sol2)]
        row = replay(bars, {"rules": [_rule("forced", mode="desk_v65_2h")]})["rules"][0]
        self.assertEqual(row["trades"][0]["exit_reason"], "time_stop")
        self.assertEqual(row["deviation"], DEVIATION_2H)
        self.assertIn(DEVIATION_2H, format_report({"proxy_inputs": "proxy inputs: x", "rules": [row]}))

    def test_rule_cannot_enter_a_tiny_pool_without_rules_all_pass(self):
        bars = [_bar(MINT_A, T0, 0.05, sells=2, rules={"s2": "pass"})]
        row = replay(bars, {"rules": [_rule("tiny")]})["rules"][0]
        self.assertEqual(row["n"] + row["open_n"] + row["unresolved_n"], 0)

    def test_no_route_does_not_refresh_the_clock(self):
        gap = int(C.OUTAGE_VOID_H * 3600)
        bars = [
            _bar(MINT_A, T0, 100.0, sells=2),
            {"mint": MINT_A, "t_s": T0 + 30, "sol": None, "token": None, "error": "no_route"},
            _bar(MINT_A, T0 + gap + 5, 100.0, sells=0),
        ]
        row = replay(bars, {"rules": [_rule("route", entry={"min_sells_h1": 1})]})["rules"][0]
        self.assertEqual(row["unresolved_n"], 1)
        self.assertEqual(row["open_n"], 0)

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
