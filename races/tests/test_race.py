"""Caps, costs, dry-run files, and the first two run call counts."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from races.dry import DryTransport
from races.impact import buy_impact_bps, sell_impact_bps, side_cost_bps
from races.run import load_config, run_once

ROOT = Path(__file__).resolve().parents[2]
CFG, _locked = load_config(ROOT / "races")


class Caps(unittest.TestCase):
    def test_caps_are_14_14_7_3_2_5(self):
        caps = CFG["jupiter"]["caps"]
        self.assertEqual(caps, {"R1": 14, "R2": 14, "R3": 7, "R4": 3, "SOL": 2, "R5": 5})
        self.assertEqual(sum(caps.values()), 45)
        self.assertEqual(CFG["jupiter"]["total_cap"], 45)
        self.assertEqual(CFG["starting_sol"], 5.0)
        self.assertEqual(CFG["ticket_sol"], 0.5)


class Cost(unittest.TestCase):
    def test_sell_impact_is_out_over_reserve_and_missing_reserve_caps(self):
        self.assertAlmostEqual(sell_impact_bps(0.4, 50), 10000 * 0.4 / 50)
        self.assertAlmostEqual(buy_impact_bps(0.5, 50), 10000 * 0.5 / 50.5)
        cost, source = side_cost_bps(None, False, CFG)
        self.assertEqual(cost, 600)
        self.assertEqual(source, "cap_missing_reserve")
        stop, _src = side_cost_bps(80, True, CFG)
        plain, _src = side_cost_bps(80, False, CFG)
        self.assertAlmostEqual(stop - plain, 150)


class DryRun(unittest.TestCase):
    def test_two_runs_log_per_wallet_counts_and_the_board_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            stamps = ("2026-11-11T12:12:00Z", "2026-11-11T12:42:00Z")
            for stamp in stamps:
                now = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                run_once(path, now, dry_run=True, transport=DryTransport())
            runs = [json.loads(line) for line in (path / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
            summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
            positions = json.loads((path / "positions.json").read_text(encoding="utf-8"))
            self.assertTrue((path / "trades.jsonl").is_file())
            self.assertTrue((path / "equity.jsonl").is_file())
            self.assertFalse((path / "config.lock.json").exists())
            trades = [json.loads(line) for line in (path / "trades.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row.get("wallet") == "R1" for row in trades))
        self.assertEqual(len(runs), 2)
        names = ("R1", "R2", "R3", "R4", "SOL", "R5")
        for row in runs:
            self.assertEqual(set(names), set(row["wallets"]))
            self.assertEqual(row["calls"]["cap"], 45)
            self.assertEqual(sum(row["wallets"][name] for name in names), row["calls"]["total"])
            self.assertLessEqual(row["wallets"]["R5"], 5)
        self.assertEqual(summary["banner"], "Display only, untested. A lead in the race is luck until tested.")
        self.assertNotIn("winner", json.dumps(summary).lower())
        self.assertIn("avg_pct_deployed", summary["wallets"][0])
        self.assertEqual(positions["schema_version"], 1)
        from races.scrub import scrub
        leaked = "RaceMint11111111111111111111111111111111"
        cleaned = scrub({"wallet": leaked, "note": "kept"}, set())
        self.assertNotIn("wallet", cleaned)
        self.assertEqual(cleaned.get("note"), "kept")
        self.assertNotIn(leaked, json.dumps(cleaned))
        self.assertEqual(scrub({"wallet": "R1"}, set())["wallet"], "R1")
        self.assertEqual(scrub({"wallet": "SOL_HOLD"}, set())["wallet"], "SOL_HOLD")


class Isolation(unittest.TestCase):
    def test_sources_do_not_touch_the_frozen_desk(self):
        files = [p for p in (ROOT / "races").rglob("*.py") if "tests" not in p.parts]
        text = "\n".join(p.read_text(encoding="utf-8") for p in files)
        self.assertNotRegex(text, r"(import\s+desk|solana-desk|desk-data|JUPITER_API_KEY)")
        wf = (ROOT / ".github/workflows/races.yml").read_text(encoding="utf-8")
        self.assertIn("sightline-races", wf)
        self.assertIn('cron: "12,42 * * * *"', wf)
        self.assertIn("timeout-minutes: 5", wf)
        self.assertIn("races-data", wf)
        self.assertNotIn("ref: desk-data", wf)
        self.assertNotIn("JUPITER_API_KEY", wf)
        self.assertNotIn("\n  push:", wf)
