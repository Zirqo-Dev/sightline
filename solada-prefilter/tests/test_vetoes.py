"""SPD-65: dev-check time budget, shared slices, and research vetoes.

Offline. No secrets, no network. Wallet tags in this file are synthetic.
"""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, SRC)

import prefilter as P  # noqa: E402
import vetoes as V  # noqa: E402
from test_regressions import fresh_state, kill_patches, quiet_req, run_main  # noqa: E402

CFG = json.load(open(os.path.join(SRC, "config.json")))
UNIT = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789"
OWNER = "^zzOWNER$"


def _dev(ticker="RUG", **kw):
    row = {
        "ticker": ticker, "policy_short": "abcdef012345", "unit": UNIT,
        "dev_tag": "w-0123abcd", "dev_sold_ada": 0, "late_rug": False,
        "serial_3h": 0, "serial_7d": 0, "prior_rugs": 0,
    }
    row.update(kw)
    return {"thresholds": {"min_sell_ada": 25, "serial_caution_3h": 2, "serial_caution_7d": 3},
            "entries": [row]}


def _move(kind="lp_remove", **kw):
    # 8_000 ADA is 40% of a 20_000 pool and dump_ada is 25% of the pull.
    row = {
        "kind": kind, "ticker": "RUG", "policy_short": "abcdef01",
        "actor_tag": "w-89abcdef", "lp": False, "ahead_of_dump": True,
        "ada": 8000, "reserve_ada_after": 12000, "dump_ada": 2000,
    }
    row.update(kw)
    return {"moves": [row]}


def _derive(dev, liq, owner=OWNER, cfg=None):
    return V.build(dev, liq, cfg if cfg is not None else CFG, owner, "2026-10-28T02:07:00Z")


class Budget(unittest.TestCase):
    def test_budget_is_configurable_and_call_caps_stay(self):
        self.assertEqual(CFG["dev_checks"]["budget_s"], 180)
        self.assertEqual(CFG["dev_checks"]["max_calls"], 36)
        self.assertEqual(CFG["dev_checks"]["max_bf_calls"], 50)
        self.assertEqual(CFG["whale_ledger"]["budget_s"], 90)
        self.assertEqual(CFG["whale_ledger"]["max_calls"], 60)
        self.assertEqual(CFG["big_moves"]["budget_s"], 150)
        self.assertEqual(CFG["big_moves"]["max_calls"], 80)
        self.assertEqual(CFG["big_moves"]["min_ada"], 5000)
        self.assertEqual(CFG["liquidity_moves"]["budget_s"], 50)
        self.assertEqual(CFG["liquidity_moves"]["max_calls"], 36)
        self.assertEqual(CFG["liquidity_moves"]["max_bf_calls"], 12)
        self.assertEqual(CFG["dev_checks"]["min_sell_ada"], 25)
        self.assertEqual(CFG["dev_checks"]["serial_caution_3h"], 2)
        self.assertEqual(CFG["dev_checks"]["serial_caution_7d"], 3)
        self.assertGreaterEqual(CFG["pace_seconds"]["blockfrost"], 0.1)
        self.assertLessEqual(1 / CFG["pace_seconds"]["blockfrost"], 10)
        budgets = P.slice_budgets(CFG)
        self.assertEqual(CFG["research_budget_s"], int(budgets["whale_ledger"] + budgets["big_moves"] + budgets["liquidity_moves"]))
        custom = {"dev_checks": {"budget_s": 10, "enabled": True}, "whale_ledger": {"budget_s": 90},
                  "big_moves": {"budget_s": 150}, "liquidity_moves": {"budget_s": 50}}
        self.assertEqual(P.job_deadline(custom, 10_000, 10_000, "dev_checks"), 10_010)

    def test_shared_allocation_reserves_liquidity_and_big_moves(self):
        t0 = 10_000
        self.assertEqual(P.research_deadline(CFG, t0, t0), t0 + 720)
        self.assertEqual(P.job_deadline(CFG, t0, t0, "dev_checks"), t0 + 180)
        self.assertEqual(P.job_deadline(CFG, t0, t0 + 180, "whale_ledger"), t0 + 270)
        self.assertEqual(P.job_deadline(CFG, t0, t0 + 270, "big_moves"), t0 + 720 - 50)
        self.assertEqual(P.job_deadline(CFG, t0, t0 + 720 - 50, "liquidity_moves"), t0 + 720)
        # 500s already used: dev_checks yields, whale is shortened, big_moves and liquidity keep their slices.
        now = t0 + 500
        self.assertEqual(P.job_deadline(CFG, t0, now, "dev_checks"), now)
        self.assertEqual(P.job_deadline(CFG, t0, now, "whale_ledger"), now + 20)
        self.assertEqual(P.job_deadline(CFG, t0, now + 20, "big_moves"), now + 170)
        self.assertEqual(P.job_deadline(CFG, t0, now + 170, "liquidity_moves"), t0 + 720)
        # 70s left: liquidity keeps the tail, big_moves gets what remains, the front gets nothing.
        now = t0 + 650
        self.assertEqual(P.job_deadline(CFG, t0, now, "dev_checks"), now)
        self.assertEqual(P.job_deadline(CFG, t0, now, "whale_ledger"), now)
        self.assertEqual(P.job_deadline(CFG, t0, now, "big_moves"), now + 20)
        self.assertEqual(P.job_deadline(CFG, t0, now + 20, "liquidity_moves"), t0 + 720)
        for job in P.SLICE_ORDER:
            self.assertLessEqual(P.job_deadline(CFG, t0, t0 + 700, job), t0 + 720)


class Derive(unittest.TestCase):
    def test_three_codes_and_nothing_under_the_existing_floors(self):
        doc = _derive(_dev(dev_sold_ada=25, serial_3h=2, prior_rugs=1), _move())
        V.validate_doc(doc)
        self.assertIs(doc["buy_signal"], False)
        row = doc["tokens"][0]
        self.assertEqual([v["code"] for v in row["vetoes"]], ["dev_sold", "serial_launcher", "lp_pulled"])
        self.assertEqual(row["vetoes"][0]["evidence"], {"dev_tag": "w-0123abcd"})
        self.assertEqual(row["vetoes"][2]["evidence"], {"actor_tag": "w-89abcdef"})
        self.assertEqual(row["policy_short"], "abcdef012345")
        quiet = _derive(_dev(dev_sold_ada=24, serial_3h=1, serial_7d=2), _move(kind="lp_add"))
        self.assertEqual(quiet["tokens"], [])
        late = _derive(_dev(dev_sold_ada=0, late_rug=True), {"moves": []})
        self.assertEqual([v["code"] for v in late["tokens"][0]["vetoes"]], ["dev_sold"])
        week = _derive(_dev(serial_7d=3), {"moves": []})
        self.assertEqual([v["code"] for v in week["tokens"][0]["vetoes"]], ["serial_launcher"])
        # prior_rugs alone is a serial_launcher. Dropping that clause fails this.
        rugs = _derive(_dev(prior_rugs=1, dev_sold_ada=0, serial_3h=0, serial_7d=0), {"moves": []})
        self.assertEqual([v["code"] for v in rugs["tokens"][0]["vetoes"]], ["serial_launcher"])

    def test_role_tag_is_kept_and_owner_prefix_and_key_are_not(self):
        doc = _derive(_dev(dev_tag="repeat_launcher", dev_sold_ada=40), {"moves": []})
        self.assertEqual(doc["tokens"][0]["vetoes"][0]["evidence"], {"dev_tag": "repeat_launcher"})
        hidden = _derive(_dev(dev_sold_ada=100), _move(), owner="^w-0123abcd$")
        self.assertEqual(hidden["tokens"][0]["vetoes"][0]["evidence"], {})
        self.assertNotIn("w-0123abcd", json.dumps(hidden))
        leaked = _derive(
            _dev(dev_tag="stake1abc", dev_sold_ada=100),
            _move(actor_tag="addr1qxyz"),
            owner=OWNER)
        text = json.dumps(leaked)
        self.assertNotIn("stake1abc", text)
        self.assertNotIn("addr1qxyz", text)
        self.assertEqual(leaked["tokens"][0]["vetoes"][0]["evidence"], {})
        prefix = _derive(_dev(dev_tag="uynnk0mezk", dev_sold_ada=100), {"moves": []})
        self.assertEqual(prefix["tokens"][0]["vetoes"][0]["evidence"], {})
        self.assertNotIn("uynnk0mezk", json.dumps(prefix))

    def test_unusable_patterns_strip_tags_and_keep_the_code(self):
        doc = _derive(_dev(dev_sold_ada=100), _move(), owner="")
        self.assertEqual(doc["tokens"][0]["vetoes"][0]["code"], "dev_sold")
        self.assertEqual(doc["tokens"][0]["vetoes"][0]["evidence"], {})
        self.assertNotIn("w-", json.dumps(doc["tokens"]))

    def test_lp_token_rows_and_swaps_are_not_vetoes(self):
        doc = _derive({"entries": []}, _move(ticker="LP", lp=True, kind="lp_remove"))
        self.assertEqual(doc["tokens"], [])
        # lp true with a project ticker still must not veto. Removing the lp skip fails this.
        doc = _derive({"entries": []}, _move(lp=True, ahead_of_dump=True, actor_tag="repeat_launcher"))
        self.assertEqual(doc["tokens"], [])
        doc = _derive({"entries": []}, _move(kind="swap"))
        self.assertEqual(doc["tokens"], [])

    def test_third_party_pull_is_not_a_veto_but_a_dev_or_dump_is(self):
        third = _derive(_dev(dev_sold_ada=0), _move(ahead_of_dump=False, actor_tag="w-89abcdef"))
        self.assertEqual(third["tokens"], [])
        dumped = _derive(_dev(dev_sold_ada=0), _move(ahead_of_dump=True, actor_tag="w-89abcdef"))
        self.assertEqual([v["code"] for v in dumped["tokens"][0]["vetoes"]], ["lp_pulled"])
        role = _derive(_dev(dev_sold_ada=0), _move(ahead_of_dump=False, actor_tag="repeat_launcher"))
        self.assertEqual(role["tokens"][0]["vetoes"][0]["evidence"], {"actor_tag": "repeat_launcher"})
        dev = _derive(_dev(dev_tag="w-0123abcd", dev_sold_ada=0),
                      _move(ahead_of_dump=False, actor_tag="w-0123abcd"))
        self.assertEqual([v["code"] for v in dev["tokens"][0]["vetoes"]], ["lp_pulled"])
        self.assertEqual(dev["tokens"][0]["vetoes"][0]["evidence"], {"actor_tag": "w-0123abcd"})

    def test_lp_pulled_uses_the_tightened_ahead_of_dump(self):
        held = _derive(_dev(dev_sold_ada=0), _move())
        self.assertEqual([v["code"] for v in held["tokens"][0]["vetoes"]], ["lp_pulled"])
        # Under 2_000 ADA. The flag alone is not enough.
        small = _derive(_dev(dev_sold_ada=0), _move(ada=1500, reserve_ada_after=500, dump_ada=1500))
        self.assertEqual(small["tokens"], [])
        # Under 20% of the pool reserve.
        thin = _derive(_dev(dev_sold_ada=0), _move(ada=3000, reserve_ada_after=17000, dump_ada=3000))
        self.assertEqual(thin["tokens"], [])
        # Same-party sell total under 25% of the pull.
        shy = _derive(_dev(dev_sold_ada=0), _move(dump_ada=1000))
        self.assertEqual(shy["tokens"], [])
        cfg = json.loads(json.dumps(CFG))
        cfg["liquidity_moves"]["ahead_of_dump"]["sell_of_pull_pct"] = 90
        raised = _derive(_dev(dev_sold_ada=0), _move(), cfg=cfg)
        self.assertEqual(raised["tokens"], [])

    def test_two_units_under_one_policy_stay_separate(self):
        policy = "ab" * 6
        u1 = policy + "cd" * 22 + "1111"
        u2 = policy + "cd" * 22 + "2222"
        self.assertEqual(u1[:12], u2[:12])
        self.assertNotEqual(u1, u2)
        dev = {"entries": [
            {"ticker": "ONE", "policy_short": u1[:12], "unit": u1, "dev_tag": "w-0123abcd",
             "dev_sold_ada": 100, "late_rug": False, "serial_3h": 0, "serial_7d": 0, "prior_rugs": 0},
            {"ticker": "TWO", "policy_short": u2[:12], "unit": u2, "dev_tag": "w-89abcdef",
             "dev_sold_ada": 100, "late_rug": False, "serial_3h": 0, "serial_7d": 0, "prior_rugs": 0},
        ], "thresholds": {"min_sell_ada": 25}}
        doc = _derive(dev, {"moves": []})
        self.assertEqual(sorted(r["unit"] for r in doc["tokens"]), sorted((u1, u2)))
        # A liquidity row with no unit may still join on the policy prefix.
        joined = _derive(
            {"entries": [dev["entries"][0]], "thresholds": dev["thresholds"]},
            _move(policy_short=u1[:8], ahead_of_dump=True))
        self.assertEqual(len(joined["tokens"]), 1)
        self.assertEqual(joined["tokens"][0]["unit"], u1)
        self.assertIn("lp_pulled", [v["code"] for v in joined["tokens"][0]["vetoes"]])

    def test_schema_rejects_an_extra_evidence_field(self):
        doc = _derive(_dev(dev_sold_ada=100), {"moves": []})
        doc["tokens"][0]["vetoes"][0]["evidence"]["dev_sold_ada"] = 100
        with self.assertRaises(SystemExit):
            V.validate_doc(doc)
        doc["buy_signal"] = True
        with self.assertRaises(SystemExit):
            V.validate_doc(doc)

    def test_fixture_matches_the_schema(self):
        doc = json.load(open(os.path.join(HERE, "fixtures", "vetoes.json")))
        V.validate_doc(doc)
        try:
            import jsonschema
        except ImportError:
            return
        schema = json.load(open(os.path.join(ROOT, "schema", "solada-vetoes.schema.json")))
        jsonschema.Draft202012Validator(schema).validate(doc)
        jsonschema.Draft202012Validator(schema).validate(_derive(_dev(dev_sold_ada=30, serial_3h=2), _move()))


class FailSoft(unittest.TestCase):
    def test_corrupt_input_keeps_the_previous_file(self):
        now = 1_700_000_000
        with tempfile.TemporaryDirectory() as d:
            prev = {"schema_version": 1, "tokens": [{"ticker": "KEEP"}]}
            with open(os.path.join(d, "vetoes.json"), "w") as f:
                json.dump(prev, f)
            with open(os.path.join(d, "dev_checks.json"), "w") as f:
                f.write("{")
            summary = V.run(d, CFG, False, owner_env=OWNER, now_s=now)
            self.assertFalse(summary["wrote"])
            self.assertEqual(summary.get("reason"), "corrupt")
            self.assertEqual(json.load(open(os.path.join(d, "vetoes.json"))), prev)
            blocked = V.run(d, {"vetoes": {"enabled": False}}, False, owner_env=OWNER, now_s=now)
            self.assertFalse(blocked["wrote"])
            self.assertEqual(json.load(open(os.path.join(d, "vetoes.json"))), prev)

    def test_stale_source_is_not_stamped_as_today(self):
        now = 1_700_000_000
        entry = {"ticker": "RUG", "policy_short": "abcdef012345", "unit": UNIT, "dev_tag": "w-0123abcd",
                 "dev_sold_ada": 100, "late_rug": False, "serial_3h": 0, "serial_7d": 0, "prior_rugs": 0}

        def write_dev(d, at):
            with open(os.path.join(d, "dev_checks.json"), "w") as f:
                json.dump({"at": at, "entries": [entry], "thresholds": {"min_sell_ada": 25}}, f)

        with tempfile.TemporaryDirectory() as d:
            write_dev(d, V.DC.iso(now - 9 * 86400))
            summary = V.run(d, CFG, False, owner_env=OWNER, now_s=now)
            doc = json.load(open(os.path.join(d, "vetoes.json")))
            self.assertEqual(summary["stale"], ["dev_checks"])
            self.assertEqual(doc["tokens"], [])
            self.assertEqual(doc["at"], V.DC.iso(now))
            write_dev(d, V.DC.iso(now - 3600))
            V.run(d, CFG, False, owner_env=OWNER, now_s=now)
            doc = json.load(open(os.path.join(d, "vetoes.json")))
            self.assertEqual(doc["stale"], [])
            self.assertIn("dev_sold", [v["code"] for v in doc["tokens"][0]["vetoes"]])
            cfg = json.loads(json.dumps(CFG))
            cfg["vetoes"]["max_age_h"] = 1
            write_dev(d, V.DC.iso(now - 3 * 3600))
            V.run(d, cfg, False, owner_env=OWNER, now_s=now)
            doc = json.load(open(os.path.join(d, "vetoes.json")))
            self.assertEqual(doc["stale"], ["dev_checks"])
            self.assertEqual(doc["tokens"], [])

    def test_a_build_failure_is_swallowed_and_keeps_the_file(self):
        now = 1_700_000_000
        with tempfile.TemporaryDirectory() as d:
            prev = {"kept": True}
            with open(os.path.join(d, "vetoes.json"), "w") as f:
                json.dump(prev, f)
            with open(os.path.join(d, "dev_checks.json"), "w") as f:
                json.dump({"at": V.DC.iso(now), "entries": []}, f)
            with mock.patch.object(V, "build", side_effect=SystemExit("no")):
                out = V.run(d, CFG, False, owner_env=OWNER, now_s=now)
            self.assertEqual(out["error"], "SystemExit")
            self.assertFalse(out["wrote"])
            self.assertEqual(json.load(open(os.path.join(d, "vetoes.json"))), prev)

    def test_a_veto_crash_does_not_stop_the_prefilter(self):
        sent = []

        def post(self, url, body, h=None):
            sent.append(body)
            return True

        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            boom = mock.patch.object(P.vetoes_mod, "run", side_effect=SystemExit("vetoes failed"))
            hook = mock.patch.object(P.Http, "post_webhook", post)
            run = run_main(d, quiet_req, kill_patches(
                {"SER": {"kill": {"ada_side_below": 5000}, "text": "", "asset_id": "aa" * 28 + b"SER".hex(), "source": "test"}},
                4000) + [boom, hook], dry=False, env={"SOLADA_WEBHOOK_URL": "https://example.invalid/h"})
            self.assertTrue(os.path.exists(os.path.join(d, "state.json")))
            self.assertIsNone(run["vetoes"])
            self.assertTrue(run["webhook_sent"])
        self.assertTrue(sent)
        self.assertNotIn("vetoes", json.dumps(sent[0]))

    def test_primary_webhook_fires_before_the_dev_check_budget(self):
        order = []

        def post(self, url, body, h=None):
            order.append(("webhook", [t["kind"] for t in body["metrics"]["triggers"]]))
            if "vetoes" in json.dumps(body):
                order.append("leaked")
            return True

        def dc_run(*a, **k):
            order.append("dev")
            # Omitting deadline, or passing None, lets dev_checks run without the shared cap.
            self.assertIsInstance(k.get("deadline"), (int, float))
            self.assertGreater(k["deadline"], time.time() - 1)
            self.assertLessEqual(k["deadline"], time.time() + P.HARD_CAP_S)
            return {"entries": 1}, [{"key": "devsell:U:1", "kind": "dev_sell", "name": "QUIET",
                                    "metrics": {"dev_tag": "w-abcdef01", "dev_sold_ada": 30,
                                                "changed_since_last": True, "late_rug": False}}]

        tracked = {"SER": {"kill": {"ada_side_below": 5000}, "text": "", "asset_id": "aa" * 28 + b"SER".hex(), "source": "test"}}
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            patches = kill_patches(tracked, 4000) + [
                mock.patch.object(P.Http, "post_webhook", post),
                mock.patch.object(P.DC, "run", dc_run)]
            run_main(d, quiet_req, patches, dry=False, env={"SOLADA_WEBHOOK_URL": "https://example.invalid/h"})
        self.assertEqual(order[0][0], "webhook")
        self.assertIn("kill_line", order[0][1])
        self.assertNotIn("dev_sell", order[0][1])
        self.assertEqual(order[1], "dev")
        self.assertEqual(order[2][0], "webhook")
        self.assertEqual(order[2][1], ["dev_sell"])
        self.assertNotIn("leaked", order)
        src = open(os.path.join(SRC, "prefilter.py")).read()
        self.assertIn('deadline=job_deadline(cfg, t_start, time.time(), "dev_checks")', src)

    def test_research_budget_s_is_doc_only(self):
        self.assertIn("Doc-only", CFG["_research_budget_note"])
        other = json.loads(json.dumps(CFG))
        other["research_budget_s"] = 10
        self.assertEqual(P.research_deadline(CFG, 1000, 1100), P.research_deadline(other, 1000, 1100))
        self.assertEqual(P.job_deadline(CFG, 1000, 1000, "whale_ledger"),
                         P.job_deadline(other, 1000, 1000, "whale_ledger"))

    def test_schema_rejects_a_raw_stake_prefix(self):
        import jsonschema
        schema = json.load(open(os.path.join(ROOT, "schema", "solada-vetoes.schema.json")))
        enum = schema["$defs"]["tag"]["anyOf"][1]["enum"]
        self.assertEqual(sorted(enum), sorted(V.DC.known_roles(CFG)))
        validator = jsonschema.Draft202012Validator(schema)
        doc = json.load(open(os.path.join(HERE, "fixtures", "vetoes.json")))
        self.assertEqual(list(validator.iter_errors(doc)), [])
        doc["tokens"][0]["vetoes"][0]["evidence"]["dev_tag"] = "uynnk0mezk"
        self.assertTrue(list(validator.iter_errors(doc)))

    def test_validate_desks_checks_vetoes(self):
        script = open(os.path.join(ROOT, "scripts", "validate-desks.py")).read()
        self.assertIn("solada-prefilter/tests/fixtures/vetoes.json", script)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "validate-desks.py")],
                           cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ok solada-prefilter/tests/fixtures/vetoes.json (solada-vetoes)", r.stdout)


class FileGuard(unittest.TestCase):
    def _run(self, doc, args):
        with tempfile.TemporaryDirectory() as d:
            main = os.path.join(d, "main")
            os.makedirs(os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-public-keys.txt"), os.path.join(main, "scripts"))
            shutil.copytree(SRC, os.path.join(main, "solada-prefilter"),
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            desk = os.path.join(d, "desk-data", "solada-prefilter")
            os.makedirs(desk)
            with open(os.path.join(desk, "vetoes.json"), "w") as f:
                json.dump(doc, f)
            with open(os.path.join(desk, "note.json"), "w") as f:
                json.dump({"ok": True}, f)
            data = os.path.join(d, "desk-data")
            subprocess.run(["git", "init", "-q"], cwd=data, check=True)
            subprocess.run(["git", "add", "-A"], cwd=data, check=True)
            env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
            return subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data", *args],
                                  cwd=d, env=env, capture_output=True)

    def test_prefix_fails_even_if_the_caller_omits_the_file(self):
        doc = {"tokens": [{"ticker": "RUG", "vetoes": [{"code": "dev_sold", "evidence": {"dev_tag": "uynnk0mezk"}}]}]}
        r = self._run(doc, ["solada-prefilter/note.json"])
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"veto evidence is not a role label or w- hash", r.stderr)
        self.assertNotIn(b"uynnk0mezk", r.stdout + r.stderr)

    def test_role_and_hash_pass(self):
        doc = json.load(open(os.path.join(HERE, "fixtures", "vetoes.json")))
        r = self._run(doc, [])
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
