"""SPD-77 Cardano hard veto. Offline. Synthetic wallets. No secrets."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SRC = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, SRC)
sys.path.insert(0, ROOT)

import dev_checks as DC  # noqa: E402
import liquidity_moves as LM  # noqa: E402
import rugger_blacklist as RB  # noqa: E402
import vetoes as V  # noqa: E402
from test_dev_checks import stake  # noqa: E402

CFG = json.load(open(os.path.join(SRC, "config.json")))
OWNER = "^zzOWNER$"
AT = "2026-10-28T02:07:00Z"
NOW = RB.parse_iso(AT)
UNIT_A = "ab" * 30
UNIT_B = "cd" * 30


def _dev(unit, ticker, **kw):
    row = {
        "ticker": ticker, "policy_short": unit[:12], "unit": unit,
        "dev_tag": "w-0123abcd", "dev_sold_ada": 0, "late_rug": False,
        "serial_3h": 0, "serial_7d": 0, "prior_rugs": 0,
    }
    row.update(kw)
    return {"thresholds": {"min_sell_ada": 25, "serial_caution_3h": 2, "serial_caution_7d": 3},
            "entries": [row]}


class Veto(unittest.TestCase):
    def test_late_rug_on_an_earlier_token_is_a_hard_veto(self):
        book = RB.Book(RB.MemoryStore(None), RB.settings_from(CFG))
        dev = stake("rugger")
        book.note_evidence(dev, "cardano", "late_rug", UNIT_A, AT)
        book.note_evidence(dev, "cardano", "lp_pulled", UNIT_A, AT)
        doc = V.build(_dev(UNIT_B, "NEXT"), {"moves": []}, CFG, OWNER, AT,
                      book=book, deployers={UNIT_B: dev})
        V.validate_doc(doc)
        self.assertIs(doc["buy_signal"], False)
        codes = [v["code"] for v in doc["tokens"][0]["vetoes"]]
        self.assertEqual(codes, ["known_rugger"])
        ev = doc["tokens"][0]["vetoes"][0]["evidence"]
        self.assertEqual(ev["evidence_tags"], ["late_rug", "lp_pulled"])
        self.assertTrue(DC.TAG_RE.fullmatch(ev["rugger_id"]))
        self.assertNotIn(dev, json.dumps(doc))
        # The token that is the only evidence does not gain known_rugger by itself.
        same = V.build(_dev(UNIT_A, "RUG", late_rug=True), {"moves": []}, CFG, OWNER, AT,
                       book=book, deployers={UNIT_A: dev})
        self.assertNotIn("known_rugger", [v["code"] for v in same["tokens"][0]["vetoes"]])
        self.assertIn("dev_sold", [v["code"] for v in same["tokens"][0]["vetoes"]])

    def test_funded_wallet_is_its_own_code(self):
        book = RB.Book(RB.MemoryStore(None), RB.settings_from(CFG))
        dev, child = stake("rugger"), stake("child")
        book.note_evidence(dev, "cardano", "ahead_of_dump", UNIT_A, AT)
        book.note_edge(dev, child, "cardano", AT)
        doc = V.build(_dev(UNIT_B, "NEXT"), {"moves": []}, CFG, OWNER, AT,
                      book=book, deployers={UNIT_B: child})
        V.validate_doc(doc)
        self.assertEqual([v["code"] for v in doc["tokens"][0]["vetoes"]], ["known_rugger_funded"])
        ev = doc["tokens"][0]["vetoes"][0]["evidence"]
        self.assertEqual(ev["hop"], 1)
        self.assertEqual(ev["evidence_tags"], ["ahead_of_dump"])
        self.assertNotEqual(ev["rugger_id"], ev["wallet_id"])
        text = json.dumps(doc)
        self.assertNotIn(dev, text)
        self.assertNotIn(child, text)

    def test_serial_launcher_stays_beside_the_new_code(self):
        book = RB.Book(RB.MemoryStore(None), RB.settings_from(CFG))
        dev = stake("rugger")
        book.note_evidence(dev, "cardano", "late_rug", UNIT_A, AT)
        doc = V.build(_dev(UNIT_B, "NEXT", prior_rugs=1), {"moves": []}, CFG, OWNER, AT,
                      book=book, deployers={UNIT_B: dev})
        self.assertEqual([v["code"] for v in doc["tokens"][0]["vetoes"]], ["serial_launcher", "known_rugger"])

    def test_cache_miss_does_not_invent_a_veto(self):
        now = NOW
        entry = _dev(UNIT_B, "NEXT", late_rug=True)["entries"][0]
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            with open(os.path.join(data, "dev_checks.json"), "w") as fh:
                json.dump({"at": AT, "entries": [entry], "thresholds": {"min_sell_ada": 25}}, fh)
            summary = V.run(data, CFG, False, owner_env=OWNER, now_s=now, cache_dir=cache)
            doc = json.load(open(os.path.join(data, "vetoes.json")))
            self.assertEqual(summary["counts"]["known_rugger"], 0)
            self.assertEqual([v["code"] for v in doc["tokens"][0]["vetoes"]], ["dev_sold"])
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))

    def test_private_file_persists_and_the_public_file_stays_hashed(self):
        dev = stake("rugger")
        now = NOW
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            with open(os.path.join(cache, "dev-checks-cache.json"), "w") as fh:
                json.dump({"units": {
                    UNIT_A: {"stake": dev, "late_rug": True, "live_at_ms": 1},
                    UNIT_B: {"stake": dev, "late_rug": False, "live_at_ms": 2},
                }}, fh)
            with open(os.path.join(data, "dev_checks.json"), "w") as fh:
                json.dump({"at": AT, "entries": [_dev(UNIT_B, "NEXT")["entries"][0]],
                           "thresholds": {"min_sell_ada": 25}}, fh)
            V.run(data, CFG, False, owner_env=OWNER, now_s=now, cache_dir=cache)
            first = json.load(open(os.path.join(data, "vetoes.json")))
            self.assertIn("known_rugger", [v["code"] for v in first["tokens"][0]["vetoes"]])
            self.assertNotIn(dev, json.dumps(first))
            self.assertIn(dev, open(os.path.join(cache, RB.PRIVATE_NAME)).read())
            os.remove(os.path.join(data, "vetoes.json"))
            V.run(data, CFG, False, owner_env=OWNER, now_s=now, cache_dir=cache)
            second = json.load(open(os.path.join(data, "vetoes.json")))
            self.assertIn("known_rugger", [v["code"] for v in second["tokens"][0]["vetoes"]])
            self.assertNotIn(dev, json.dumps(second))

    def test_unusable_patterns_drop_the_new_codes(self):
        book = RB.Book(RB.MemoryStore(None), RB.settings_from(CFG))
        dev = stake("rugger")
        book.note_evidence(dev, "cardano", "late_rug", UNIT_A, AT)
        doc = V.build(_dev(UNIT_B, "NEXT"), {"moves": []}, CFG, "", AT,
                      book=book, deployers={UNIT_B: dev})
        self.assertEqual(doc["tokens"], [])
        self.assertNotIn(dev, json.dumps(doc))


class Feeds(unittest.TestCase):
    def test_dev_checks_feed_records_late_rug_and_an_ada_only_hop(self):
        dev, child = stake("rugger"), stake("child")
        txs = [{
            "inputs": [{"stake": dev, "addr": "", "lovelace": 10_000_000, "assets": {}}],
            "outputs": [
                {"stake": child, "addr": "", "lovelace": 6_000_000, "assets": {}},
                {"stake": stake("buyer"), "addr": "", "lovelace": 4_000_000, "assets": {UNIT_A: 1}},
            ],
        }]
        self.assertEqual(DC._funded_out(dev, txs), [child])
        loaded = DC._patterns(OWNER)
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            DC._feed_rugger(
                {"units": {UNIT_A: {"stake": dev, "late_rug": True}}},
                {UNIT_A: {"stake": dev, "funded": [child]}},
                cache, data, NOW, loaded, False)
            book = RB.open_book(cache, data)
            self.assertEqual([v["code"] for v in book.judge(dev, UNIT_B, NOW)], ["known_rugger"])
            self.assertEqual([v["code"] for v in book.judge(child, UNIT_B, NOW)], ["known_rugger_funded"])
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))
            self.assertNotIn(dev, json.dumps(book.judge(child, UNIT_B, NOW)))

    def test_liquidity_feed_records_pull_and_dump_only(self):
        dev = stake("rugger")
        bystander = stake("bystander")
        role_w = stake("role")
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            with open(os.path.join(cache, "dev-checks-cache.json"), "w") as fh:
                json.dump({"units": {UNIT_A: {"stake": dev}}}, fh)
            events = [
                {"kind": "lp_remove", "unit": UNIT_A, "_lp_stake": dev, "actor_tag": "w-0123abcd",
                 "ahead_of_dump": True},
                {"kind": "lp_remove", "unit": UNIT_A, "_lp_stake": bystander, "actor_tag": "w-89abcdef",
                 "ahead_of_dump": False},
                {"kind": "lp_remove", "unit": UNIT_B, "_lp_stake": role_w, "actor_tag": "repeat_launcher",
                 "ahead_of_dump": False},
            ]
            LM._feed_rugger(cache, data, events, NOW, CFG, False)
            book = RB.open_book(cache, data)
            dev_hit = book.judge(dev, UNIT_B, NOW)[0]
            self.assertEqual(dev_hit["code"], "known_rugger")
            self.assertEqual(dev_hit["tags"], ["ahead_of_dump", "lp_pulled"])
            self.assertEqual(book.judge(bystander, UNIT_A, NOW), [])
            role = book.judge(role_w, "token-else", NOW)[0]
            self.assertEqual(role["tags"], ["lp_pulled"])
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))


class Guard(unittest.TestCase):
    def _desk(self, files):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        main = os.path.join(d, "main")
        os.makedirs(os.path.join(main, "scripts"))
        shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
        shutil.copy(os.path.join(ROOT, "scripts", "privacy-public-keys.txt"), os.path.join(main, "scripts"))
        shutil.copytree(SRC, os.path.join(main, "solada-prefilter"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        data = os.path.join(d, "desk-data")
        os.makedirs(os.path.join(data, "solada-prefilter"))
        os.makedirs(os.path.join(data, "solana-desk"))
        for name, doc in files.items():
            with open(os.path.join(data, name), "w") as fh:
                json.dump(doc, fh)
        subprocess.run(["git", "init", "-q"], cwd=data, check=True)
        subprocess.run(["git", "add", "-A"], cwd=data, check=True)
        env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
        return subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data"],
                              cwd=d, env=env, capture_output=True)

    def test_hashed_veto_passes_and_a_raw_id_fails_without_being_printed(self):
        good = json.load(open(os.path.join(HERE, "fixtures", "vetoes.json")))
        good["tokens"][0]["vetoes"].append({
            "code": "known_rugger",
            "evidence": {"rugger_id": "w-0123abcd", "evidence_tags": ["late_rug"]},
        })
        good["counts"]["known_rugger"] = 1
        ok = self._desk({"solada-prefilter/vetoes.json": good})
        self.assertEqual(ok.returncode, 0, ok.stderr)
        raw = "Wallet" + "A" * 32
        bad_doc = json.load(open(os.path.join(HERE, "fixtures", "vetoes.json")))
        bad_doc["tokens"][0]["vetoes"].append({
            "code": "known_rugger",
            "evidence": {"rugger_id": raw, "evidence_tags": ["late_rug"]},
        })
        bad_doc["counts"]["known_rugger"] = 1
        bad = self._desk({"solada-prefilter/vetoes.json": bad_doc})
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn(b"veto evidence is not a role label or w- hash", bad.stderr)
        self.assertNotIn(raw.encode(), bad.stdout + bad.stderr)

    def test_private_blacklist_file_and_a_raw_solana_field_fail(self):
        raw = "Wallet" + "B" * 32
        planted = self._desk({"solada-prefilter/rugger-blacklist.json": {"entries": {"k": raw}}})
        self.assertNotEqual(planted.returncode, 0)
        self.assertIn(b"private cache file must not be published", planted.stderr)
        self.assertNotIn(raw.encode(), planted.stdout + planted.stderr)
        log = {
            "schema_version": 1, "desk": "solana", "mode": "log_only", "buy_signal": False,
            "note": "log only", "at": AT,
            "rows": [{
                "mint": "mintok", "code": "known_rugger", "rugger_id": "w-0123abcd",
                "evidence_tags": ["rugcheck_rugged"], "creator": raw,
            }],
        }
        leaked = self._desk({"solana-desk/rugger_would_veto.json": log})
        self.assertNotEqual(leaked.returncode, 0)
        self.assertIn(b"rugger_would_veto.json rejected", leaked.stderr)
        self.assertNotIn(raw.encode(), leaked.stdout + leaked.stderr)
        clean = json.load(open(os.path.join(ROOT, "solana-desk", "tests", "fixtures", "rugger_would_veto.json")))
        ok = self._desk({"solana-desk/rugger_would_veto.json": clean})
        self.assertEqual(ok.returncode, 0, ok.stderr)


if __name__ == "__main__":
    unittest.main()
