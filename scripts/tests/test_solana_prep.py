"""Solana day slice. Offline. Unknown stays unknown."""
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PINNED_DATA = "c1600731da1a524ac86043357299960fe1beed9b"
DAY = "2026-11-08"
DAY_START = "1794096000"
CODE = "a" * 40
DATA = "b" * 40
# Audit key. Duplicated here so the check does not call the builder's hasher.
AUDIT = ("time", "book", "action", "why", "mint", "pnl", "frac")


def load_mod():
    path = os.path.join(ROOT, "scripts", "solana_prep.py")
    spec = importlib.util.spec_from_file_location("solana_prep", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


prep = load_mod()


def row_id(row):
    raw = "|".join((row.get(key) or "").strip() for key in AUDIT)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def set_hash(ids):
    body = "\n".join(sorted(ids)).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


class SolanaPrepTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write_trades(self, rows, header=None):
        header = header or ["time", "book", "action", "why", "mint", "pnl", "frac"]
        path = os.path.join(self.dir, "trades.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path

    def write_health(self, doc):
        path = os.path.join(self.dir, "health.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return path

    def health(self, day=DAY, **extra):
        doc = {
            "day": day, "last_run_utc": "20261108T1430", "loop_ran": True,
            "runs": 30, "expected_runs": 48, "checks": 360,
            "data_failure_rate": 0.0111, "void_day": False, "error_rate": 0.0083,
            "mode": "fill", "frozen": True, "cfg_version": "v1-v6.5",
        }
        doc.update(extra)
        return doc

    def build(self, trades, health, day=DAY):
        return prep.build(trades, health, day, CODE, DATA)

    def test_blank_exit_reason_stays_unknown(self):
        trades = self.write_trades([
            {"time": DAY_START, "book": "F", "action": "sell", "why": "", "mint": "m1", "pnl": "0.1", "frac": "1"},
        ])
        health = self.write_health(self.health())
        doc = self.build(trades, health)
        closes = doc["reconciliation"]["closes"]
        self.assertEqual(closes["n"], 1)
        self.assertEqual(closes["unknown_exit_reason"], 1)
        self.assertFalse(closes["ok"])
        self.assertEqual(doc["reconciliation"]["status"], "unknown")
        self.assertEqual(closes["exit_reasons"], {})
        self.assertEqual(doc["provenance"]["as_of"]["staleness"], "unknown")

    def test_health_day_mismatch_is_not_zero(self):
        trades = self.write_trades([
            {"time": DAY_START, "book": "F", "action": "buy", "why": "pass", "mint": "m1", "pnl": "", "frac": "1"},
        ])
        health = self.write_health(self.health(day="2026-11-07", runs=0, void_day=False))
        doc = self.build(trades, health)
        self.assertFalse(doc["health"]["known"])
        self.assertIsNone(doc["health"]["runs"])
        self.assertIsNone(doc["health"]["void_day"])
        self.assertEqual(doc["reconciliation"]["status"], "unknown")
        self.assertEqual(doc["reconciliation"]["opens"]["n"], 1)

    def test_bad_time_duplicate_and_missing_fail(self):
        bad = self.write_trades([
            {"time": "nope", "book": "F", "action": "buy", "why": "pass", "mint": "m1", "pnl": "", "frac": "1"},
        ])
        health = self.write_health(self.health())
        with self.assertRaises(prep.PrepError):
            self.build(bad, health)
        first = {"time": DAY_START, "book": "F", "action": "buy", "why": "pass", "mint": "m1", "pnl": "", "frac": "1"}
        dup = self.write_trades([first, dict(first)])
        with self.assertRaises(prep.PrepError):
            self.build(dup, health)
        with self.assertRaises(prep.PrepError):
            self.build(os.path.join(self.dir, "missing.csv"), health)

    def test_output_has_no_mint_and_does_not_write_the_snapshot(self):
        mint = "MintSecretValueNotForOutput"
        trades = self.write_trades([
            {"time": DAY_START, "book": "F", "action": "sell", "why": "hard_stop", "mint": mint, "pnl": "-0.1", "frac": "1"},
        ])
        health = self.write_health(self.health())
        before = open(trades, "rb").read()
        doc = self.build(trades, health)
        self.assertEqual(open(trades, "rb").read(), before)
        text = prep.dumps(doc)
        self.assertNotIn(mint, text)
        self.assertEqual(doc["reconciliation"]["closes"]["exit_reasons"]["hard_stop"], 1)
        self.assertEqual(doc["reconciliation"]["row_id_set_sha256"], set_hash([row_id({
            "time": DAY_START, "book": "F", "action": "sell", "why": "hard_stop",
            "mint": mint, "pnl": "-0.1", "frac": "1",
        })]))

    def test_pinned_snapshot_matches_independent_counts(self):
        try:
            trades_raw = subprocess.check_output(
                ["git", "-C", ROOT, "show", PINNED_DATA + ":solana-desk/trades.csv"],
            )
            health_raw = subprocess.check_output(
                ["git", "-C", ROOT, "show", PINNED_DATA + ":solana-desk/health.json"],
            )
        except subprocess.CalledProcessError:
            self.skipTest("pinned snapshot is not in this clone")
        snap = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, snap, True)
        trades = os.path.join(snap, "trades.csv")
        health = os.path.join(snap, "health.json")
        with open(trades, "wb") as fh:
            fh.write(trades_raw)
        with open(health, "wb") as fh:
            fh.write(health_raw)
        before_t = open(trades, "rb").read()
        before_h = open(health, "rb").read()
        code = subprocess.check_output(["git", "-C", ROOT, "rev-parse", "HEAD"], text=True).strip()
        doc = prep.build(trades, health, DAY, code, PINNED_DATA)
        self.assertEqual(open(trades, "rb").read(), before_t)
        self.assertEqual(open(health, "rb").read(), before_h)
        # Independent parse. Not the builder's loader.
        text = trades_raw.decode("utf-8")
        parsed = list(csv.DictReader(text.splitlines()))
        health_doc = json.loads(health_raw.decode("utf-8"))
        start = int(DAY_START)
        end = start + 86400
        chosen = []
        for row in parsed:
            ts = float(row["time"])
            if start <= ts < end:
                chosen.append(row)
        buys = [row for row in chosen if row["action"] == "buy"]
        sells = [row for row in chosen if row["action"] == "sell"]
        reasons = {}
        for row in sells:
            why = (row.get("why") or "").strip()
            self.assertTrue(why)
            reasons[why] = reasons.get(why, 0) + 1
        rec = doc["reconciliation"]
        self.assertEqual(rec["opens"]["n"], len(buys))
        self.assertEqual(rec["closes"]["n"], len(sells))
        self.assertEqual(rec["closes"]["exit_reasons"], reasons)
        self.assertEqual(rec["closes"]["unknown_exit_reason"], 0)
        self.assertTrue(rec["closes"]["ok"])
        self.assertEqual(sum(reasons.values()), len(sells))
        self.assertEqual(rec["row_id_set_sha256"], set_hash([row_id(row) for row in chosen]))
        self.assertEqual(rec["window_row_n"], len(chosen))
        for key in ("day", "last_run_utc", "runs", "expected_runs", "checks", "data_failure_rate", "void_day", "error_rate", "mode"):
            self.assertEqual(doc["health"][key], health_doc[key], key)
        self.assertEqual(doc["provenance"]["as_of"]["staleness"], "unknown")
        self.assertEqual(doc["provenance"]["data_commit"], PINNED_DATA)
        dumped = prep.dumps(doc)
        for row in chosen:
            self.assertNotIn(row["mint"], dumped)
        covered = 0
        for row in chosen:
            covered += len(",".join((row.get(key) or "") for key in ("time", "book", "action", "why"))) + 1
        self.assertLess(len(dumped.encode("utf-8")), len(trades_raw) + len(health_raw))
        # Honest size check for the report, not a token estimate.
        self.snapshot_sizes = (len(trades_raw), len(health_raw), covered, len(dumped.encode("utf-8")))


if __name__ == "__main__":
    unittest.main()
