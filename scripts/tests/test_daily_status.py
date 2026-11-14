"""Daily status JSON. Offline. Missing inputs stay null."""
import csv
import importlib.util
import json
import os
import shutil
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
MINT = "1" * 32
MINT_B = "2" * 32


def valid_twin(marker=1):
    return {
        "analysis": "random_twin",
        "marker": marker,
        "cost_model": "v3-exit-aware",
        "data_ref": "origin/desk-data:solana-desk",
        "sample": {"post_v3": 3, "pre_v3": 0, "mixed_excluded": 0},
        "scans": {
            "n": 4,
            "first": "20261102T0000",
            "last": "20261102T0400",
            "first_t": "2026-11-02T00:00:00Z",
            "last_t": "2026-11-02T04:00:00Z",
        },
    }


def load_mod():
    path = os.path.join(ROOT, "scripts", "status", "daily_status.py")
    spec = importlib.util.spec_from_file_location("daily_status", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


status = load_mod()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def trade_row(when, book, action, frac, pnl, why, mint, symbol, sol="0.5", cost_model=""):
    return {
        "time": str(when), "book": book, "agent": "EXIT" if action == "sell" else "EXECUTE",
        "mint": mint, "symbol": symbol, "action": action, "frac": str(frac), "sol": sol,
        "quote_px": "1", "impact_pct": "", "fee_sol": "0.00001", "haircut_pct": "0.6",
        "why": why, "pnl": "" if pnl is None else str(pnl), "check_delay_s": "0", "stale": "0",
        "cost_model": cost_model,
    }


class DailyStatusTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, True)
        self.addCleanup(shutil.rmtree, self.out, True)

    def fixture(self, extra_sell=None, symbol="AAA", steps=0):
        header = list(trade_row(0, "F", "buy", 1, None, "pass", MINT, "AAA"))
        rows = [
            trade_row(100, "F", "buy", 1, None, "pass", MINT, "AAA"),
            trade_row(200, "F", "sell", 1, "-0.1", "hard_stop", MINT, "AAA"),
            trade_row(100, "F", "buy", 1, None, "pass", MINT_B, "BBB"),
            trade_row(300, "F", "sell", 1, "0.2", "take_profit_40", MINT_B, "BBB"),
            trade_row(100, "R", "buy", 1, None, "random_match", MINT, "AAA"),
            trade_row(400, "R", "sell", 1, "-0.25", "hard_stop", MINT, "AAA"),
        ]
        if extra_sell:
            rows.append(extra_sell)
        path = os.path.join(self.data, "solana-desk", "trades.csv")
        write(path, "")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        positions = {
            "last_run_ts": 1762051200,
            "books": {
                "F": {"open": {"F-1-open": {
                    "mint": MINT, "symbol": symbol, "opened": 1762051200,
                    "frac": 1, "steps": steps, "why": "pass",
                }}, "closed": []},
                "R": {"open": {}, "closed": []},
                "S": {"open": {}, "closed": []},
            },
        }
        write(os.path.join(self.data, "solana-desk", "positions.json"), json.dumps(positions))
        health = {
            "day": "2026-11-02", "loop_ran": True, "runs": 4, "expected_runs": 48,
            "checks": 10, "error_rate": 0.0, "data_failure_rate": 0.1, "void_day": False,
            "ai": False, "last_run_utc": "20261102T0400", "mode": "fill",
            "data_fail_by_source": {"rugcheck": 0, "jupiter": 1, "dexscreener": 0},
        }
        write(os.path.join(self.data, "solana-desk", "health.json"), json.dumps(health))
        paper = {
            "schema_version": 2, "desk": "solada", "book": "solada", "unit": "ADA",
            "open": [],
            "closed": [{
                "id": "T1", "ticker": "AAA", "status": "closed",
                "opened_at": "2026-10-01T00:00:00Z", "closed_at": "2026-10-02T00:00:00Z",
                "result": {"pct": -10, "pnl": -10},
            }],
            "tests": [{
                "id": "EST-01", "title": "Established", "status": "active",
                "registered_at": "2026-11-01T16:58:00-05:00",
                "exit": "7 days, or the kill line",
                "tickets": [{
                    "id": "EST-OPEN", "ticker": "BBB", "status": "open",
                    "opened_at": "2026-11-01T00:00:00Z",
                }],
            }],
        }
        write(os.path.join(self.data, "solada", "paper.json"), json.dumps(paper))
        write(os.path.join(self.data, "reports", "random_twin_2026-11-01.json"), json.dumps({"n": 1}))
        write(os.path.join(self.data, "reports", "random_twin_2026-11-02.json"), json.dumps(valid_twin(marker=2)))

    def build(self, **kw):
        date = kw.pop("date", "2026-11-02")
        return status.build(ROOT, self.data, date, kw.get("as_of"))

    def test_books_checkpoints_and_sources(self):
        self.fixture()
        doc = self.build()
        book = doc["books"]["F"]
        mixed = book["all_closes"]
        self.assertEqual(mixed["n"]["value"], 2)
        self.assertEqual(mixed["n"]["source"], "solana-desk/trades.csv")
        self.assertEqual(mixed["total_pnl_sol"]["value"], "0.100000000")
        self.assertEqual(mixed["mean"]["value"], "0.100000000")
        self.assertEqual(mixed["mean"]["percent"], "10.000000000")
        self.assertEqual(mixed["median"]["value"], "0.100000000")
        self.assertEqual(mixed["ticket_logged_lots"]["value"], 2)
        self.assertEqual(mixed["ticket_assumed_lots"]["value"], 0)
        self.assertIn("buy row's sol", mixed["ticket_assumption"])
        self.assertIn("pre-v3 and post-v3", mixed["note"])
        self.assertEqual(book["post_v3"]["n"]["value"], 0)
        self.assertEqual(book["ticket_sol"]["value"], "0.500000000")
        self.assertEqual(book["open_count"]["value"], 1)
        pos = book["open_positions"][0]
        self.assertEqual(pos["exit_codes"], [
            "pool_gone", "no_route_3x", "hard_stop", "liquidity_pulled", "time_stop", "take_profit_40",
        ])
        self.assertNotIn("trailing_stop", pos["exit_codes"])
        self.assertEqual(doc["books"]["R"]["all_closes"]["mean"]["value"], "-0.500000000")
        self.assertEqual(doc["books"]["R"]["not_overlapping_f"]["n"]["value"], 0)
        self.assertEqual(doc["books"]["R"]["not_overlapping_f"]["overlapping_f"]["value"], 1)
        self.assertEqual(doc["books"]["S"]["all_closes"]["n"]["value"], 0)
        self.assertIsNone(doc["books"]["S"]["all_closes"]["mean"]["value"])
        self.assertIn("no closes", doc["books"]["S"]["all_closes"]["mean"]["reason"])
        day14 = doc["checkpoints"]["day14_verdict"]
        self.assertEqual(day14["at"]["value"], "2026-11-11T15:00:00Z")
        self.assertEqual(day14["days"]["value"], 9)
        day28 = doc["checkpoints"]["day28_checkpoint"]
        self.assertEqual(day28["at"]["value"], "2026-11-25T15:00:00Z")
        self.assertEqual(day28["at"]["source"], "scripts/status/daily_status.py DAY28_AT")
        self.assertEqual(day28["days"]["value"], 23)
        self.assertEqual(doc["random_twin"]["source"], "reports/random_twin_2026-11-02.json")
        self.assertEqual(doc["random_twin"]["root"], "data")
        self.assertEqual(doc["random_twin"]["value"]["marker"], 2)
        self.assertEqual(doc["random_twin"]["checked"]["cost_model"], "v3-exit-aware")
        self.assertEqual(doc["random_twin"]["checked"]["date"], "2026-11-02")
        self.assertFalse(doc["freshness"]["stale"])
        est = next(row for row in doc["registered_tests"] if row.get("id") == "EST-01")
        self.assertEqual(est["close_count"]["value"], 0)
        self.assertIsNone(est["mean_return_after_costs"]["value"])
        self.assertEqual(est["open_positions"][0]["exit_rule"], "7 days, or the kill line")
        self.assertIsNone(est["open_positions"][0]["exit_codes"])
        solada = next(row for row in doc["ledgers"] if row.get("id") == "solada")
        self.assertEqual(solada["close_count"]["value"], 1)
        self.assertEqual(solada["mean_stored_pct"]["value"], "-10.000000000")
        self.assertIsNone(solada["mean_return_after_costs"]["value"])
        self.assertIn("not label it as after costs", solada["mean_return_after_costs"]["reason"])
        health = doc["feed_health"]["solana_desk"]["fields"]
        self.assertEqual(health["data_failure_rate"], 0.1)
        self.assertEqual(health["data_fail_by_source"]["jupiter"], 1)
        self.assertFalse(health["ai"])
        paths = {row["path"]: row for row in doc["sources"]}
        self.assertTrue(paths["solana-desk/trades.csv"]["used"])
        self.assertTrue(paths["solana-desk/desk/config.py"]["used"])
        self.assertEqual(len(paths["solana-desk/trades.csv"]["sha256"]), 64)
        text = status.dumps(doc)
        self.assertIsNone(status.BANNED.search(text))
        one = status.dumps(self.build())
        self.assertEqual(text, one)

    def test_trailing_exit_and_withheld_symbol(self):
        self.fixture(symbol="bad symbol", steps=1)
        doc = self.build()
        pos = doc["books"]["F"]["open_positions"][0]
        self.assertIn("trailing_stop", pos["exit_codes"])
        self.assertIn("take_profit_100", pos["exit_codes"])
        self.assertNotIn("take_profit_40", pos["exit_codes"])
        self.assertIsNone(pos["symbol"])
        self.assertNotIn("bad symbol", status.dumps(doc))

    def test_blank_pnl_does_not_invent_a_mean(self):
        self.fixture(extra_sell=trade_row(500, "S", "sell", 1, None, "hard_stop", MINT, "AAA"))
        doc = self.build()
        book = doc["books"]["S"]
        self.assertIsNone(book["all_closes"]["value"])
        self.assertIn("blank pnl", book["all_closes"]["reason"])
        self.assertEqual(book["incomplete_rows"]["value"], 1)

    def test_missing_inputs_are_null(self):
        doc = self.build()
        for book in ("F", "R", "S"):
            self.assertIsNone(doc["books"][book]["all_closes"]["value"])
            self.assertIn("trades.csv is missing", doc["books"][book]["all_closes"]["reason"])
            self.assertIsNone(doc["books"][book]["open_count"]["value"])
        self.assertEqual(doc["random_twin"]["root"], "repo")
        self.assertEqual(doc["random_twin"]["checked"]["date"], "2026-11-02")
        self.assertEqual(doc["random_twin"]["checked"]["cost_model"], "v3-exit-aware")
        self.assertIn("solana-desk", doc["random_twin"]["checked"]["universe"])
        self.assertEqual(doc["checkpoints"]["day14_verdict"]["days"]["value"], 9)
        names = [row["path"] for row in doc["sources"] if not row["used"]]
        self.assertIn("solana-desk/trades.csv", names)
        self.assertNotIn("reports/random_twin_*.json", names)

    def test_twin_only_in_repo_checkout(self):
        repo = tempfile.mkdtemp()
        data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        self.addCleanup(shutil.rmtree, data, True)
        write(os.path.join(repo, "solana-desk", "desk", "config.py"), 'COST_MODEL = "v3-exit-aware"\nTICKET_SOL = 0.5\n')
        write(os.path.join(repo, "reports", "random_twin_2026-11-02.json"), json.dumps(valid_twin()))
        found = status.random_twin(status.Roots(repo, data))
        self.assertEqual(found["root"], "repo")
        self.assertEqual(found["checked"]["post_v3"], 3)
        self.assertIsNone(found["reason"])

    def test_twin_metadata_mismatch_is_null(self):
        repo = tempfile.mkdtemp()
        data = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo, True)
        self.addCleanup(shutil.rmtree, data, True)
        write(os.path.join(repo, "solana-desk", "desk", "config.py"), 'COST_MODEL = "v3-exit-aware"\n')
        bad = valid_twin()
        bad["cost_model"] = "pre-v3"
        write(os.path.join(repo, "reports", "random_twin_2026-11-02.json"), json.dumps(bad))
        found = status.random_twin(status.Roots(repo, data))
        self.assertIsNone(found["value"])
        self.assertIn("cost_model", found["reason"])
        bad = valid_twin()
        bad["scans"]["last_t"] = "2026-11-01T04:00:00Z"
        write(os.path.join(repo, "reports", "random_twin_2026-11-02.json"), json.dumps(bad))
        found = status.random_twin(status.Roots(repo, data))
        self.assertIsNone(found["value"])
        self.assertIn("filename date", found["reason"])
        bad = valid_twin()
        bad["data_ref"] = "somewhere-else"
        write(os.path.join(data, "reports", "random_twin_2026-11-03.json"), json.dumps(bad))
        bad["scans"]["last_t"] = "2026-11-03T00:00:00Z"
        write(os.path.join(data, "reports", "random_twin_2026-11-03.json"), json.dumps(bad))
        found = status.random_twin(status.Roots(repo, data))
        self.assertIsNone(found["value"])
        self.assertIn("universe", found["reason"])

    def test_post_v3_applies_the_void_window(self):
        self.fixture()
        void_open = int(status.parse_time("2026-10-31T02:00:00Z"))
        kept_open = int(status.parse_time("2026-11-02T01:00:00Z"))
        rows = [
            trade_row(void_open, "F", "buy", 1, None, "pass", MINT, "AAA", cost_model="v3-exit-aware"),
            trade_row(void_open + 10, "F", "sell", 1, "-0.2", "hard_stop", MINT, "AAA", cost_model="v3-exit-aware"),
            trade_row(kept_open, "F", "buy", 1, None, "pass", MINT_B, "BBB", cost_model="v3-exit-aware"),
            trade_row(kept_open + 10, "F", "sell", 1, "0.1", "time_stop", MINT_B, "BBB", cost_model="v3-exit-aware"),
            trade_row(kept_open, "R", "buy", 1, None, "random_match", "3" * 32, "CCC", sol="", cost_model="v3-exit-aware"),
            trade_row(kept_open + 10, "R", "sell", 1, "-0.05", "hard_stop", "3" * 32, "CCC", cost_model="v3-exit-aware"),
        ]
        path = os.path.join(self.data, "solana-desk", "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        doc = self.build()
        post = doc["books"]["F"]["post_v3"]
        self.assertEqual(post["n"]["value"], 1)
        self.assertEqual(post["excluded_void_entry"]["value"], 1)
        self.assertEqual(post["mean"]["value"], "0.200000000")
        self.assertEqual(post["cost_model"], "v3-exit-aware")
        self.assertEqual(post["void_window"]["start"], "2026-10-31T01:30:00Z")
        lone = doc["books"]["R"]["not_overlapping_f"]
        self.assertEqual(lone["n"]["value"], 1)
        self.assertEqual(lone["ticket_assumed_lots"]["value"], 1)
        self.assertEqual(lone["mean"]["value"], "-0.100000000")
        later = self.build(date="2026-11-03")
        self.assertTrue(later["freshness"]["stale"])
        self.assertEqual(later["freshness"]["expected_evening"], "2026-11-03T00:00:00Z")

    def test_days_follow_the_report_date(self):
        doc = status.build(ROOT, self.data, "2026-11-11")
        self.assertEqual(doc["checkpoints"]["day14_verdict"]["days"]["value"], 0)
        self.assertEqual(doc["checkpoints"]["day28_checkpoint"]["days"]["value"], 14)
        self.assertEqual(doc["checkpoints"]["day28_checkpoint"]["at"]["value"], status.DAY28_AT)
        later = status.build(ROOT, self.data, "2026-11-26")
        self.assertEqual(later["checkpoints"]["day14_verdict"]["days"]["value"], -15)
        self.assertEqual(later["checkpoints"]["day28_checkpoint"]["days"]["value"], -1)

    def test_writes_dated_and_latest(self):
        self.fixture()
        doc = self.build(as_of="2026-11-02T04:00:00Z")
        self.assertEqual(doc["as_of"]["value"], "2026-11-02T04:00:00Z")
        self.assertEqual(doc["as_of"]["source"], "cli --as-of")
        dated, latest = status.write_report(doc, self.out, "2026-11-02")
        self.assertTrue(dated.endswith("daily_status_2026-11-02.json"))
        self.assertTrue(latest.endswith("daily_status_latest.json"))
        with open(dated, encoding="utf-8") as fh:
            left = fh.read()
        with open(latest, encoding="utf-8") as fh:
            right = fh.read()
        self.assertEqual(left, right)
        self.assertEqual(json.loads(left)["books"]["F"]["all_closes"]["n"]["value"], 2)

    def test_repo_ledgers_match_their_files(self):
        doc = self.build()
        for rel, book_id in (
            ("solada/pumpfun/paper.json", "pumpfun"),
            ("nft/paper.json", "nft"),
        ):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
                paper = json.load(fh)
            closed = [row for row in paper.get("closed") or [] if row.get("closed_at")]
            ledger = next(row for row in doc["ledgers"] if row.get("id") == book_id)
            self.assertEqual(ledger["close_count"]["value"], len(closed))
            self.assertEqual(ledger["close_count"]["source"], rel)

    def test_workflow_is_daily_and_after_the_desk_run(self):
        path = os.path.join(ROOT, ".github", "workflows", "daily-status.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("scripts/status/daily_status.py", text)
        self.assertIn("desk-data", text)
        self.assertIn("cron:", text)
        self.assertNotIn("founder", text.lower())
        self.assertIn("secrets.PRIVACY_GUARD_PATTERNS", text)
        self.assertIn("secrets.PRIVACY_IDENTITY_HASHES", text)
        self.assertIn("secrets.SIGHTLINE_PRIVATE_TOKEN", text)
        self.assertNotIn("secrets.RUGGER_ID_PEPPER", text)


if __name__ == "__main__":
    unittest.main()
