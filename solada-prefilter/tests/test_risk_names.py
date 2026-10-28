"""Public pre-filter files use risk_check / scanner names. A coin ticker stays put."""
import json, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import koios_tx as KT  # noqa: E402

RETIRED = "".join(chr(n) for n in (115, 117, 114, 105))
COIN = RETIRED.upper()


class RiskNames(unittest.TestCase):
    def test_rename_keeps_coin_ticker_and_dev_checks(self):
        doc = KT.neutral_public_names({
            RETIRED: {
                "SNEK": {"risk": "High risk", "tags": ["Dev sold"], "source": RETIRED},
                "BOLT": {"risk": None, "tags": [], "source": "dev_checks"},
            },
            "seen_launches": ["draft-" + RETIRED, "draft-snek"],
            "positions": {COIN: 1020},
            "rows": [{"token": COIN, "source": "koios"}],
        })
        self.assertNotIn(RETIRED, doc)
        self.assertEqual(doc["risk_check"]["SNEK"]["source"], "scanner")
        self.assertEqual(doc["risk_check"]["BOLT"]["source"], "dev_checks")
        self.assertIsNone(doc["risk_check"]["BOLT"]["risk"])
        self.assertIn("draft-" + RETIRED, doc["seen_launches"])
        self.assertEqual(doc["positions"][COIN], 1020)
        self.assertEqual(doc["rows"][0]["token"], COIN)
        self.assertEqual(doc["rows"][0]["source"], "koios")
        text = json.dumps(doc)
        self.assertNotIn('"' + RETIRED + '"', text)
        self.assertIn(COIN, text)
        self.assertIn("draft-" + RETIRED, text)

    def test_state_and_whale_ledger_writes_drop_the_retired_key(self):
        raw = {RETIRED: {"SNEK": {"risk": "Low risk", "tags": [], "source": RETIRED}}, "positions": {COIN: 3}}
        with tempfile.TemporaryDirectory() as d:
            for name in ("state.json", "whale_ledger.json", "kill_status.json"):
                path = os.path.join(d, name)
                KT.write_json(path, json.loads(json.dumps(raw)), indent=1, sort_keys=True)
                with open(path) as fh:
                    doc = json.load(fh)
                if name == "kill_status.json":
                    self.assertIn(RETIRED, doc)
                    self.assertNotIn("risk_check", doc)
                else:
                    self.assertNotIn(RETIRED, doc)
                    self.assertEqual(doc["risk_check"]["SNEK"]["source"], "scanner")
                    self.assertEqual(doc["positions"][COIN], 3)

    def test_validate_desks_accepts_both_names(self):
        import subprocess
        script = os.path.join(ROOT, "scripts", "validate-desks.py")
        log = {
            "schema_version": 2,
            "desk": "solada",
            "asof": "2026-10-28T00:00:00Z",
            "headline": "ok",
            "risk_check": {"SNEK": {"risk": "High risk", "tags": ["Dev sold"], "source": "scanner"}},
            "history": [{
                "asof": "2026-10-27T00:00:00Z",
                RETIRED: {"BOLT": {"risk": None, "tags": [], "source": "dev_checks", "note": "kept"}},
            }],
        }
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "solada"))
            with open(os.path.join(d, "desks.json"), "w") as fh:
                json.dump({"desks": [{"id": "solada", "log": "solada/log.json"}]}, fh)
            with open(os.path.join(d, "solada", "log.json"), "w") as fh:
                json.dump(log, fh)
            r = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("ok solada/log.json (run-log)", r.stdout)
            self.assertNotIn(RETIRED, r.stdout + r.stderr)

    def test_validate_desks_rejects_a_bad_risk_row(self):
        import subprocess
        script = os.path.join(ROOT, "scripts", "validate-desks.py")
        log = {
            "schema_version": 2, "desk": "solada", "asof": "2026-10-28T00:00:00Z", "headline": "ok",
            "risk_check": {"SNEK": {"risk": 1, "tags": [], "source": "scanner"}},
        }
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "solada"))
            with open(os.path.join(d, "desks.json"), "w") as fh:
                json.dump({"desks": [{"id": "solada", "log": "solada/log.json"}]}, fh)
            with open(os.path.join(d, "solada", "log.json"), "w") as fh:
                json.dump(log, fh)
            r = subprocess.run([sys.executable, script, d], capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("INVALID", r.stdout)
