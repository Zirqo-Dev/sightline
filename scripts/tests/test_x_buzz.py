"""Schema for desk-data x_buzz.json. Offline. No network."""
import json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SCHEMA = os.path.join(ROOT, "schema", "x-buzz.schema.json")
VALID = os.path.join(ROOT, "scripts", "fixtures", "x-buzz.json")
INVALID = os.path.join(ROOT, "scripts", "fixtures", "x-buzz-invalid.json")


def _load(path):
    with open(path) as fh:
        return json.load(fh)


def _validator():
    import jsonschema
    schema = _load(SCHEMA)
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema)


class XBuzzSchema(unittest.TestCase):
    def test_valid_fixture_matches_the_schema(self):
        doc = _load(VALID)
        self.assertEqual(list(_validator().iter_errors(doc)), [])
        self.assertEqual(doc["kind"], "x_buzz")
        self.assertIs(doc["veto"], False)
        self.assertIs(doc["buy_signal"], False)
        self.assertEqual(sorted(t["confidence"] for t in doc["tokens"]), ["high", "low", "medium"])
        # Nullable sample counts and a null velocity ratio are part of the shape.
        quiet = doc["tokens"][1]
        self.assertIsNone(quiet["velocity"]["ratio_1h_vs_prev_hr"])
        self.assertIsNone(quiet["sample"]["median_followers"])
        self.assertIsNone(quiet["sample"]["bot_like_share"])
        nulled = json.loads(json.dumps(doc))
        for key in ("posts", "unique_authors", "authors_over_10k"):
            nulled["tokens"][0]["sample"][key] = None
        self.assertEqual(list(_validator().iter_errors(nulled)), [])

    def test_invalid_fixture_is_rejected(self):
        validator = _validator()
        bad = _load(INVALID)
        self.assertIs(bad["veto"], True)
        self.assertTrue(list(validator.iter_errors(bad)))
        wrong = _load(VALID)
        wrong["tokens"][0]["confidence"] = "certain"
        self.assertTrue(list(validator.iter_errors(wrong)))
        short = _load(VALID)
        short["tokens"][0]["hourly_24h"] = [0] * 23
        self.assertTrue(list(validator.iter_errors(short)))

    def test_validate_desks_checks_x_buzz_when_present(self):
        script = os.path.join(ROOT, "scripts", "validate-desks.py")
        with open(script) as fh:
            text = fh.read()
        self.assertIn("x_buzz.json", text)
        self.assertIn("scripts/fixtures/x-buzz.json", text)
        repo = subprocess.run([sys.executable, script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(repo.returncode, 0, repo.stdout + repo.stderr)
        self.assertIn("ok scripts/fixtures/x-buzz.json (x-buzz)", repo.stdout)
        self.assertIn("skip x_buzz.json (not written yet)", repo.stdout)
        self.assertNotIn("x-buzz-invalid.json", repo.stdout)

        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "desks.json"), "w") as fh:
                json.dump({"desks": []}, fh)
            missing = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
            self.assertIn("skip x_buzz.json (not written yet)", missing.stdout)

            with open(os.path.join(d, "x_buzz.json"), "w") as fh:
                json.dump(_load(VALID), fh)
            ok = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
            self.assertIn("ok x_buzz.json (x-buzz)", ok.stdout)

            with open(os.path.join(d, "x_buzz.json"), "w") as fh:
                json.dump(_load(INVALID), fh)
            rejected = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0, rejected.stdout + rejected.stderr)
            self.assertIn("INVALID x_buzz.json (x-buzz)", rejected.stdout)
