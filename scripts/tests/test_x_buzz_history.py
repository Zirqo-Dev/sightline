"""Schema for desk-data x_buzz_history.jsonl. Offline. No network."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCHEMA = os.path.join(ROOT, "schema", "x-buzz-history.schema.json")
VALID = os.path.join(ROOT, "scripts", "fixtures", "x-buzz-history.jsonl")
INVALID = os.path.join(ROOT, "scripts", "fixtures", "x-buzz-history-invalid.jsonl")


def _load_lines(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _validator():
    import jsonschema
    with open(SCHEMA, encoding="utf-8") as fh:
        schema = json.load(fh)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


def _row(**over):
    base = {
        "asof": "2026-10-29T01:49:00Z",
        "ticker": "RDOG",
        "chain": "solana",
        "id_short": "5kUx…nbwo",
        "mentions_1h": 5,
        "mentions_24h": 7,
        "ratio_1h_vs_prev_hr": 2.5,
        "unique_authors": 7,
        "bot_like_share": 0.0,
        "authors_over_10k": 1,
        "median_followers": 1178,
        "confidence": "high",
        "alert": False,
        "paper_position": None,
    }
    base.update(over)
    return base


class XBuzzHistorySchema(unittest.TestCase):
    def test_valid_fixture_lines_match_the_schema(self):
        validator = _validator()
        rows = _load_lines(VALID)
        self.assertGreaterEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(list(validator.iter_errors(row)), [])
        self.assertIs(rows[1]["alert"], True)
        self.assertEqual(rows[1]["paper_position"], "F")
        self.assertIsNone(rows[0]["ratio_1h_vs_prev_hr"])
        self.assertIsNone(rows[0]["paper_position"])
        self.assertIsNone(rows[-1]["median_followers"])
        self.assertIsNone(rows[-1]["bot_like_share"])
        self.assertIsNone(rows[-1]["unique_authors"])

    def test_invalid_values_are_rejected(self):
        validator = _validator()
        bad = _load_lines(INVALID)
        self.assertTrue(list(validator.iter_errors(bad[0])))
        self.assertEqual(bad[0]["confidence"], "certain")
        self.assertEqual(bad[0]["paper_position"], "FS")
        for over in (
            {"confidence": "certain"},
            {"paper_position": "FS"},
            {"paper_position": "f"},
            {"alert": "true"},
            {"chain": "ethereum"},
            {"id_short": "A" * 32},
            {"mentions_1h": -1},
            {"asof": "2026-10-29"},
            {"extra": 1},
        ):
            self.assertTrue(list(validator.iter_errors(_row(**over))), over)
        self.assertEqual(list(validator.iter_errors(_row(alert=True, paper_position="S"))), [])
        self.assertEqual(list(validator.iter_errors(_row(ratio_1h_vs_prev_hr=None, paper_position=None))), [])

    def test_validate_desks_checks_history_when_present(self):
        script = os.path.join(ROOT, "scripts", "validate-desks.py")
        with open(script) as fh:
            text = fh.read()
        self.assertIn("x_buzz_history.jsonl", text)
        self.assertIn("scripts/fixtures/x-buzz-history.jsonl", text)
        self.assertNotIn("x-buzz-history-invalid.jsonl", text)
        repo = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(repo.returncode, 0, repo.stdout + repo.stderr)
        self.assertIn("ok scripts/fixtures/x-buzz-history.jsonl (x-buzz-history)", repo.stdout)
        self.assertIn("skip x_buzz_history.jsonl (not written yet)", repo.stdout)
        self.assertNotIn("x-buzz-history-invalid", repo.stdout)

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "desks.json"), "w") as fh:
                json.dump({"desks": []}, fh)
            missing = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
            self.assertIn("skip x_buzz_history.jsonl (not written yet)", missing.stdout)

            with open(VALID, encoding="utf-8") as src, open(os.path.join(d, "x_buzz_history.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(src.read())
            ok = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("ok x_buzz_history.jsonl (x-buzz-history)", ok.stdout)

            with open(INVALID, encoding="utf-8") as src, open(os.path.join(d, "x_buzz_history.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(src.read().rstrip("\n") + "\nnot-json\n")
            rejected = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
            self.assertIn("INVALID x_buzz_history.jsonl (x-buzz-history) line 1", rejected.stdout)
            self.assertIn("INVALID x_buzz_history.jsonl (x-buzz-history) line 2", rejected.stdout)
