"""Reader field names must exist on the writer's output.

The names come from the reader source. Race output comes from a dry run of the
writer. Health and UA status come from the writer functions. Desk logs on main
are the producer's output where this repo has no writer function.
"""
import ast
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import field_contract as FC  # noqa: E402
from races.dry import DryTransport  # noqa: E402
from races.run import run_once  # noqa: E402


def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _keys_of(path, needle):
    tree = ast.parse(_read(path))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if needle in keys:
            found.append(keys)
    return found


def _hit(fields, group, keys):
    return bool(set(fields[group]) & set(keys))


class Races(unittest.TestCase):
    def test_writer_rows_use_names_the_reader_actually_loads(self):
        races_js = _read("revamp/races.js")
        app_js = _read("revamp/app.js")
        fields = FC.js_fields(races_js)
        self.assertTrue(fields)
        self.assertTrue(all(names for names in fields.values()))
        summary_body = FC.function_body(races_js, "applySummary")
        equity_body = FC.between(races_js, "parseLines(pack.equity", "function takeTrades")
        trade_body = FC.function_body(races_js, "takeTrades")
        position_body = FC.between(races_js, "positionRows(pack.positions)", "var wallets")
        overlap_body = FC.function_body(races_js, "readOverlap")

        with tempfile.TemporaryDirectory() as tmp:
            run_once(Path(tmp), datetime(2026, 11, 11, 12, 12, tzinfo=timezone.utc), dry_run=True, transport=DryTransport())
            summary = json.loads((Path(tmp) / "summary.json").read_text(encoding="utf-8"))
            equity = (Path(tmp) / "equity.jsonl").read_text(encoding="utf-8")
            positions = (Path(tmp) / "positions.json").read_text(encoding="utf-8")
            trades = (Path(tmp) / "trades.jsonl").read_text(encoding="utf-8")
            view = FC.read_race_view_in(ROOT, tmp, json.dumps(summary), equity, positions, trades)

        by_id = {row["id"]: row for row in summary["wallets"]}
        self.assertIn("SOL", by_id)
        for group in FC.required_first_alias(summary_body):
            first = fields[group][0]
            for row in summary["wallets"]:
                self.assertIn(first, row, group)
        for group in FC.required_any_alias(summary_body):
            for row in summary["wallets"]:
                self.assertTrue(_hit(fields, group, row), group)

        equity_rows = [json.loads(line) for line in equity.splitlines() if line.strip()]
        sol_lines = [row for row in equity_rows if row.get("wallet") == "SOL" or row.get("id") == "SOL"]
        self.assertTrue(sol_lines)
        for group in FC.required_any_alias(equity_body):
            for row in sol_lines:
                self.assertTrue(_hit(fields, group, row), group)
        self.assertGreater(view["solCanon"], 0)

        trade_rows = [json.loads(line) for line in trades.splitlines() if line.strip()]
        self.assertTrue(trade_rows)
        for group in FC.required_any_alias(trade_body):
            for row in trade_rows:
                self.assertTrue(_hit(fields, group, row), group)

        position_keys = _keys_of("races/run.py", "entry_price_sol")
        self.assertTrue(position_keys)
        for group in FC.required_any_alias(position_body):
            self.assertTrue(any(_hit(fields, group, keys) for keys in position_keys), group)
        trade_keys = _keys_of("races/fills.py", "fill_price_sol")
        self.assertTrue(trade_keys)
        for group in ("symbol", "action", "price", "size", "pnl", "exitReason"):
            self.assertIn(group, fields)
            self.assertTrue(any(_hit(fields, group, keys) for keys in trade_keys), group)

        seen = {row["id"]: row for row in view["wallets"]}
        sol = seen["SOL"]
        self.assertTrue(sol["listed"])
        self.assertEqual(sol["role"], "benchmark")
        self.assertEqual(sol["netPct"], by_id["SOL"]["official_net_pct"])
        self.assertGreater(sol["series"], 0)
        for wallet_id, row in by_id.items():
            got = seen[wallet_id]
            self.assertTrue(got["listed"])
            self.assertEqual(got["netPct"], row["official_net_pct"])
            self.assertEqual(got["n"], row["n_trades"])
            self.assertEqual(got["lo"], row["ci95_low"])
            self.assertEqual(got["hi"], row["ci95_high"])
            self.assertEqual(got["avgDeployed"], row["avg_pct_deployed"])

        overlap_names = set(FC.dotted(overlap_body, "raw"))
        for name in ("k", "n", "line", "note"):
            self.assertIn(name, overlap_names)
            self.assertIn(name, summary["r5_overlap"])
        self.assertEqual(view["overlap"]["k"], summary["r5_overlap"]["k"])
        self.assertEqual(view["overlap"]["n"], summary["r5_overlap"]["n"])

        split_body = FC.between(app_js, '["meme", "non_meme", "untagged"]', 'if (w.id === "R5" && view.overlap')
        self.assertIn("side.n", split_body)
        self.assertIn("side.net_pct", split_body)
        for key in ("meme", "non_meme", "untagged"):
            self.assertIn(key, summary["r5_split"])
            self.assertEqual(view["split"][key]["n"], summary["r5_split"][key]["n"])
            self.assertEqual(view["split"][key]["net_pct"], summary["r5_split"][key]["net_pct"])

        self.assertTrue(summary.get("wallets"))
        self.assertTrue(set(summary) & {"status", "run_status", "state"})
        self.assertIn("banner", summary)
        self.assertTrue(set(summary) & set(fields["updated"]))


class HealthAndStatus(unittest.TestCase):
    def test_health_reader_fields_are_on_the_heartbeat_sample(self):
        import heartbeat
        text = _read("revamp/health.js")
        now = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
        doc = heartbeat.build_health(
            [{"file": ".github/workflows/solana-desk-scan.yml", "name": "Solana desk scan", "crons": ["*/30 * * * *"]}],
            {".github/workflows/solana-desk-scan.yml": [{"at": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"), "conclusion": "success", "event": "schedule"}]},
            {},
            now,
            main_commit_at=now - timedelta(hours=1),
        )
        for key in FC.dotted(text, "doc"):
            self.assertIn(key, doc, key)
        row = doc["workflows"][0]
        for key in FC.dotted(text, "item"):
            self.assertIn(key, row, key)

    def test_ua_card_fields_are_on_the_status_writer(self):
        import ua_f1.ua_f1_weekly as weekly
        body = FC.function_body(_read("revamp/app.js"), "uaCard")
        doc_keys = set(FC.dotted(body, "doc"))
        count_keys = set(FC.dotted(body, "c"))
        self.assertIn("counts", doc_keys)
        self.assertIn("status", doc_keys)
        self.assertIn("as_of", doc_keys)
        old = weekly.DATA
        try:
            with tempfile.TemporaryDirectory() as tmp:
                weekly.set_data_dir(tmp)
                rec = weekly.write_status("paper", "contract test")
        finally:
            weekly.set_data_dir(old)
        for key in ("counts", "status", "as_of"):
            self.assertIn(key, rec)
        for key in ("registered", "usable"):
            self.assertIn(key, count_keys)
            self.assertIn(key, rec["counts"])

    def test_freshness_stamps_use_a_name_the_file_or_the_writer_has(self):
        app = _read("revamp/app.js")
        stamp_body = FC.function_body(app, "stampFrom")
        keys = FC.string_list(stamp_body, "keys")
        self.assertIn("last_run_utc", keys)
        load_body = FC.function_body(app, "loadStamps")
        files = FC.string_list(load_body, "files")
        self.assertIn("solana-desk/health.json", files)
        for rel in files:
            path = Path(ROOT) / rel
            if not path.is_file():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(set(doc) & set(keys), rel)
        writer = _read("solana-desk/desk/run.py")
        self.assertIn("last_run_utc=", writer)

    def test_report_and_preview_read_levels_and_the_log_has_that_key(self):
        readers = (
            "preview/cardano-v2.js",
            "preview/mono.js",
            "scripts/score_picks.py",
        )
        for rel in readers:
            text = _read(rel)
            self.assertTrue(
                ("log.levels" in text) or ('log.get("levels")' in text) or ("log.get('levels')" in text),
                rel,
            )
        log = json.loads((Path(ROOT) / "solada" / "log.json").read_text(encoding="utf-8"))
        self.assertIn("levels", log)


class Outside(unittest.TestCase):
    def test_hood_file_is_named_as_an_external_source(self):
        text = _read("revamp/hood.js")
        self.assertIn("hoodscout-data", text)
        self.assertIn("nft_discover.json", text)
