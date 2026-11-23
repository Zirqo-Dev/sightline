"""Replay tests. Fixtures are synthetic. No book ledger and no network."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools.replay.engine import C, RuleError, buy_quote, fills, format_report, replay, sell_mark
from tools.replay.load import ReplayRefused, load_bars

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINT_A = "SYNTHMINT00000000000000000000000001"
MINT_B = "SYNTHMINT00000000000000000000000002"


def _bar(mint, t_s, sol, token=1000.0, rules=None, sells=2, age=3000):
    bar = {"mint": mint, "t_s": t_s, "sol": sol, "token": token, "sells_h1": sells, "age_s": age}
    if rules is not None:
        bar["rules"] = rules
    return bar


def _rule(rule_id="fixed", entry=None, exit_spec=None):
    return {
        "id": rule_id,
        "entry": {} if entry is None else entry,
        "exit": {"max_hold_s": 100} if exit_spec is None else exit_spec,
    }


class Replay(unittest.TestCase):
    def test_flat_round_trip_matches_desk_cost(self):
        bars = [_bar(MINT_A, 1_000, 100.0), _bar(MINT_A, 1_100, 100.0)]
        result = replay(bars, {"seed": 1, "rules": [_rule()]})
        self.assertEqual(result["cost_model"], "v3-exit-aware")
        trade = result["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "max_hold")
        self.assertEqual(trade["exit_t"], 1_100)
        self.assertEqual(trade["exit_fixed"]["max_hold_s"], 100)
        tokens, entry_px = buy_quote(100.0, 1000.0, C.TICKET_SOL)
        mark = sell_mark(100.0, 1000.0, tokens)
        cost, h_entry = fills.entry_cost(100.0)
        pos = {"entry": entry_px, "entry_cost": cost}
        dx = fills.gross_proceeds(pos, 1.0, mark)
        verified = fills.verified_impact_bps(100.0, dx)
        haircut = fills.haircut_frac(100.0, verified, "max_hold")
        gross, net = fills.slice_pnl(pos, 1.0, mark, haircut)
        self.assertAlmostEqual(trade["gross_pnl"], gross)
        self.assertAlmostEqual(trade["net_pnl"], net)
        self.assertAlmostEqual(trade["slices"][0]["exit_haircut"], haircut)
        self.assertLess(trade["net_pnl"], 0)
        self.assertAlmostEqual(h_entry, (60 + 0.25 * (10000 * 0.5 / 100.5)) / 10000)
        self.assertEqual(result["rules"][0]["n"], 1)
        self.assertEqual(result["rules"][0]["hit_rate"], 0.0)
        self.assertEqual(result["rules"][0]["baseline"]["n"], 0)
        self.assertEqual(result["rules"][0]["baseline"]["shortfall"], 1)
        self.assertIsNone(result["rules"][0]["baseline"]["hit_rate"])

    def test_hard_stop_uses_stress_addon_and_closes(self):
        bars = [
            _bar(MINT_A, 1_000, 100.0),
            _bar(MINT_A, 1_050, 40.0),
            _bar(MINT_A, 1_200, 40.0),
        ]
        rule = _rule(exit_spec={"hard_stop": -0.25, "max_hold_s": 200})
        trade = replay(bars, {"rules": [rule]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "hard_stop")
        self.assertEqual(trade["exit_t"], 1_050)
        self.assertLess(trade["net_pnl"], 0)
        sl = trade["slices"][0]
        verified = fills.verified_impact_bps(sl["pool_sol"], sl["dx_sol"])
        stressed = fills.haircut_frac(sl["pool_sol"], verified, "hard_stop")
        calm = fills.haircut_frac(sl["pool_sol"], verified, "max_hold")
        self.assertAlmostEqual(sl["exit_haircut"], stressed)
        self.assertGreater(stressed, calm)
        self.assertAlmostEqual(stressed - calm, C.COST["stress_exit_bps"] / 10000)

    def test_take_profit_then_max_hold_leaves_nothing_open(self):
        bars = [
            _bar(MINT_A, 1_000, 100.0),
            _bar(MINT_A, 1_050, 160.0),
            _bar(MINT_A, 1_200, 160.0),
        ]
        rule = _rule(exit_spec={
            "hard_stop": -0.25,
            "take_profit": [[0.4, 1 / 3]],
            "trail": 0.25,
            "max_hold_s": 200,
        })
        trade = replay(bars, {"rules": [rule]})["rules"][0]["trades"][0]
        self.assertEqual([s["why"] for s in trade["slices"]], ["take_profit_40", "max_hold"])
        self.assertAlmostEqual(sum(s["frac"] for s in trade["slices"]), 1.0)
        self.assertGreater(trade["net_pnl"], 0)
        self.assertEqual(trade["exit_fixed"]["max_hold_s"], 200)

    def test_liquidity_pull_before_hard_stop(self):
        bars = [
            _bar(MINT_A, 1_000, 100.0),
            _bar(MINT_A, 1_050, 50.0),
            _bar(MINT_A, 1_200, 50.0),
        ]
        rule = _rule(exit_spec={"hard_stop": -0.25, "liq_drop": 0.40, "max_hold_s": 200})
        trade = replay(bars, {"rules": [rule]})["rules"][0]["trades"][0]
        self.assertEqual(trade["exit_reason"], "liquidity_pulled")
        sl = trade["slices"][0]
        verified = fills.verified_impact_bps(sl["pool_sol"], sl["dx_sol"])
        self.assertAlmostEqual(sl["exit_haircut"], fills.haircut_frac(sl["pool_sol"], verified, "liquidity_pulled"))

    def test_open_ended_rule_is_refused(self):
        bars = [_bar(MINT_A, 1_000, 100.0), _bar(MINT_A, 1_100, 100.0)]
        with self.assertRaises(RuleError):
            replay(bars, {"rules": [{"id": "open", "entry": {}, "exit": {"hard_stop": -0.25}}]})
        with self.assertRaises(RuleError):
            replay(bars, {"rules": [_rule(exit_spec={"max_hold_s": 0})]})

    def test_short_tape_does_not_enter(self):
        bars = [_bar(MINT_A, 1_000, 100.0), _bar(MINT_A, 1_050, 400.0)]
        result = replay(bars, {"rules": [_rule(exit_spec={"max_hold_s": 200})]})
        self.assertEqual(result["rules"][0]["n"], 0)
        self.assertIsNone(result["rules"][0]["hit_rate"])

    def test_entry_ignores_later_prices(self):
        passed = {"sol_reserve": "pass"}
        bars = [
            _bar(MINT_A, 1_000, 10.0, rules=passed),
            _bar(MINT_A, 1_100, 80.0, rules=passed),
            _bar(MINT_A, 1_300, 80.0, rules=passed),
        ]
        rule = _rule(entry={"min_sol": 50, "rules_all_pass": True}, exit_spec={"max_hold_s": 200})
        result = replay(bars, {"rules": [rule]})
        self.assertEqual(result["rules"][0]["n"], 1)
        self.assertEqual(result["rules"][0]["trades"][0]["entry_t"], 1_100)

    def test_random_baseline_same_exits_different_entries(self):
        passed = {"sol_reserve": "pass", "activity_h1": "pass"}
        bars = [
            _bar(MINT_A, 1_000, 100.0, rules=passed),
            _bar(MINT_A, 1_200, 400.0, rules=passed),
            _bar(MINT_B, 1_000, 100.0, rules=passed),
            _bar(MINT_B, 1_200, 20.0, rules=passed),
        ]
        rule = _rule(
            "rally",
            entry={"mints": [MINT_A], "rules_all_pass": True},
            exit_spec={"hard_stop": -0.25, "max_hold_s": 200},
        )
        first = replay(bars, {"seed": 3, "rules": [rule]})
        second = replay(bars, {"seed": 3, "rules": [rule]})
        row = first["rules"][0]
        self.assertEqual(row["hit_rate"], 1.0)
        self.assertGreater(row["net_pnl"], 0)
        self.assertEqual(row["baseline"]["n"], 1)
        self.assertEqual(row["baseline"]["hit_rate"], 0.0)
        self.assertLess(row["baseline"]["net_pnl"], 0)
        self.assertEqual(row["baseline"]["trades"][0]["mint"], MINT_B)
        self.assertEqual(row["baseline"]["trades"][0]["exit_reason"], "hard_stop")
        self.assertEqual(row["baseline"]["trades"][0]["exit_fixed"], row["trades"][0]["exit_fixed"])
        self.assertEqual(row["trades"][0]["exit_reason"], "max_hold")
        rule_keys = {(t["mint"], t["entry_t"]) for t in row["trades"]}
        base_keys = {(t["mint"], t["entry_t"]) for t in row["baseline"]["trades"]}
        self.assertTrue(rule_keys.isdisjoint(base_keys))
        self.assertEqual(format_report(first), format_report(second))

    def test_two_rules_are_separate(self):
        bars = [
            _bar(MINT_A, 1_000, 100.0),
            _bar(MINT_A, 1_100, 100.0),
            _bar(MINT_B, 1_000, 100.0),
            _bar(MINT_B, 1_100, 180.0),
        ]
        rules = [
            _rule("flat", entry={"mints": [MINT_A]}),
            _rule("up", entry={"mints": [MINT_B]}, exit_spec={"max_hold_s": 100}),
        ]
        result = replay(bars, {"seed": 1, "rules": rules})
        self.assertEqual([r["id"] for r in result["rules"]], ["flat", "up"])
        self.assertLess(result["rules"][0]["net_pnl"], 0)
        self.assertGreater(result["rules"][1]["net_pnl"], 0)
        self.assertEqual(result["rules"][0]["hit_rate"], 0.0)
        self.assertEqual(result["rules"][1]["hit_rate"], 1.0)

    def test_loader_joins_synthetic_manifest_and_snaps(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest-synth.jsonl").write_text(
                "\n".join([
                    json.dumps({"type": "scan", "t_s": 1710000000}),
                    json.dumps({
                        "type": "candidate",
                        "mint": MINT_A,
                        "t_s": 1710000000,
                        "rules": {"sol_reserve": "pass"},
                        "inputs": {"sol": 100, "token": 1000, "age_s": 3000, "txns_h1": {"buys": 1, "sells": 2}},
                    }),
                ]) + "\n",
                encoding="utf-8",
            )
            (root / "pool-snaps-synth.jsonl").write_text(
                "\n".join([
                    json.dumps({"mint": MINT_A, "t_s": 1710000000, "sol": 100, "token": 1000}),
                    json.dumps({"mint": MINT_A, "t_s": 1710007200, "sol": 100, "token": 1000}),
                ]) + "\n",
                encoding="utf-8",
            )
            (root / "positions.json").write_text('{"books": {"F": {"cash": 1}}}', encoding="utf-8")
            bars = load_bars(root)
            self.assertEqual(len(bars), 2)
            self.assertEqual(bars[0]["rules"]["sol_reserve"], "pass")
            self.assertEqual(bars[0]["sells_h1"], 2)
            result = replay(bars, {"rules": [_rule("joined", entry={"rules_all_pass": True}, exit_spec={"max_hold_s": 7200})]})
            self.assertEqual(result["rules"][0]["n"], 1)

    def test_refuses_ledgers_and_locked_fields_without_reading_values_into_the_error(self):
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
                self.assertNotIn("99", text)
            locked = root / "series.jsonl"
            locked.write_text('{"mint": "SYNTH", "t_s": 1, "onset": 4, "d_bps": 8}\n', encoding="utf-8")
            with self.assertRaises(ReplayRefused) as caught:
                load_bars(locked)
            self.assertNotIn("4", str(caught.exception))
            self.assertNotIn("8", str(caught.exception))
            nested = root / "lst-count-data"
            nested.mkdir()
            banned = nested / "series.jsonl"
            banned.write_text('{"mint": "SYNTH", "t_s": 1, "sol": 1, "token": 1}\n', encoding="utf-8")
            with self.assertRaises(ReplayRefused):
                load_bars(banned)

    def test_fixture_files_and_cli_are_read_only(self):
        before = {p: p.stat().st_mtime_ns for p in FIXTURES.iterdir()}
        proc = subprocess.run(
            [sys.executable, "-m", "tools.replay", "--series", str(FIXTURES / "series.jsonl"),
             "--rules", str(FIXTURES / "rules.json")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rule synth-hold trades=1", proc.stdout)
        self.assertIn("baseline synth-hold", proc.stdout)
        self.assertIn("hit_rate=", proc.stdout)
        self.assertIn("net_pnl_sol=", proc.stdout)
        after = {p: p.stat().st_mtime_ns for p in FIXTURES.iterdir()}
        self.assertEqual(before, after)
        refused = subprocess.run(
            [sys.executable, "-m", "tools.replay", "--series", str(FIXTURES / "series.jsonl") + ".missing",
             "--rules", str(FIXTURES / "rules.json")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, "")

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


if __name__ == "__main__":
    unittest.main()
