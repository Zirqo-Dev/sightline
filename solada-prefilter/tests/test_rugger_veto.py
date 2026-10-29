"""SPD-77 Cardano hard veto. Offline. Synthetic wallets. No secrets."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

os.environ.setdefault("RUGGER_ID_PEPPER", "test-pepper-not-base64-and-at-least-32-chars")

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
        self.assertTrue(RB._ID_RE.fullmatch(ev["rugger_id"]))
        self.assertFalse(DC.TAG_RE.fullmatch(ev["rugger_id"]))
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
                    UNIT_A: {"stake": dev, "late_rug": True, "live_at_ms": int(NOW * 1000) - 86_400_000},
                    UNIT_B: {"stake": dev, "late_rug": False, "live_at_ms": int(NOW * 1000)},
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
            "time": NOW - 100,
            "inputs": [{"stake": dev, "addr": "", "lovelace": 10_000_000, "assets": {}}],
            "outputs": [
                {"stake": child, "addr": "", "lovelace": 6_000_000, "assets": {}},
                {"stake": stake("buyer"), "addr": "", "lovelace": 4_000_000, "assets": {UNIT_A: 1}},
            ],
        }]
        self.assertEqual([row["stake"] for row in DC._funded_out(dev, txs)], [child])
        party = [{
            "time": NOW - 100,
            "inputs": [
                {"stake": dev, "addr": "", "lovelace": 1, "assets": {}},
                {"stake": stake("pal"), "addr": "", "lovelace": 1, "assets": {}},
            ],
            "outputs": [{"stake": child, "addr": "", "lovelace": 6_000_000, "assets": {}}],
        }]
        self.assertEqual(DC._funded_out(dev, party), [])
        import flags as F
        from test_dev_checks import kh
        ent = F.b32_encode("addr", bytes([0x61]) + kh("copayer")[:28])
        copayer = [{
            "time": NOW - 100,
            "inputs": [
                {"stake": dev, "addr": "", "lovelace": 1, "assets": {}},
                {"stake": None, "addr": ent, "lovelace": 1, "assets": {}},
            ],
            "outputs": [{"stake": child, "addr": "", "lovelace": 6_000_000, "assets": {}}],
        }]
        self.assertEqual(DC._funded_out(dev, copayer), [])
        loaded = DC._patterns(OWNER)
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            DC._feed_rugger(
                {"units": {UNIT_A: {"stake": dev, "late_rug": True}}},
                {UNIT_A: {"stake": dev, "funded": DC._funded_out(dev, txs)}},
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
                {"kind": "lp_remove", "unit": UNIT_A, "tx_short": "aaaa", "pool_key": "pool",
                 "_lp_stake": dev, "actor_tag": "w-0123abcd", "ahead_of_dump": True,
                 "time": NOW - 10, "ada": 8000, "reserve_ada": 12000},
                {"kind": "lp_remove", "unit": UNIT_A, "tx_short": "bbbb", "pool_key": "pool",
                 "_lp_stake": bystander, "actor_tag": "w-89abcdef", "ahead_of_dump": False,
                 "time": NOW - 10, "ada": 100, "reserve_ada": 100000},
                {"kind": "lp_remove", "unit": UNIT_B, "tx_short": "cccc", "pool_key": "pool",
                 "_lp_stake": role_w, "actor_tag": "repeat_launcher", "ahead_of_dump": False,
                 "time": NOW - 10, "ada": 8000, "reserve_ada": 12000},
            ]
            LM._feed_rugger(cache, data, events, [], NOW, CFG, False)
            book = RB.open_book(cache, data)
            dev_hit = book.judge(dev, UNIT_B, NOW)[0]
            self.assertEqual(dev_hit["code"], "known_rugger")
            self.assertEqual(dev_hit["tags"], ["ahead_of_dump", "lp_pulled"])
            self.assertEqual(book.judge(bystander, UNIT_A, NOW), [])
            self.assertEqual(book.judge(role_w, "token-else", NOW), [])
            self.assertNotIn(dev, json.dumps(book.judge(dev, UNIT_B, NOW)))
            pulls = json.load(open(os.path.join(cache, RB.PULLS_NAME)))
            self.assertTrue(any(row.get("stake") == dev for row in pulls["pulls"]))
            self.assertFalse(os.path.exists(os.path.join(data, RB.PRIVATE_NAME)))
            self.assertFalse(os.path.exists(os.path.join(data, RB.PULLS_NAME)))

    def test_a_pull_flagged_on_a_later_run_still_feeds(self):
        dev = stake("rugger")
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            first = [{"kind": "lp_remove", "unit": UNIT_A, "tx_short": "dddd", "pool_key": "pool",
                      "_lp_stake": dev, "ahead_of_dump": False, "time": NOW - 50,
                      "ada": 8000, "reserve_ada": 12000}]
            LM._feed_rugger(cache, data, first, [], NOW - 50, CFG, False)
            later = [{"tx_short": "dddd", "pool_key": "pool", "unit": UNIT_A, "kind": "lp_remove",
                      "ahead_of_dump": True, "time": NOW - 50, "ada": 8000, "reserve_ada": 12000}]
            LM._feed_rugger(cache, data, [], later, NOW, CFG, False)
            book = RB.open_book(cache, data)
            hit = book.judge(dev, UNIT_B, NOW)[0]
            self.assertIn("ahead_of_dump", hit["tags"])
            self.assertNotIn(dev, json.dumps(hit))
            self.assertIn(dev, open(os.path.join(cache, RB.PULLS_NAME)).read())

    def test_pull_stakes_save_when_the_pepper_is_unset(self):
        dev = stake("rugger")
        old = os.environ.pop("RUGGER_ID_PEPPER", None)
        RB._pepper_warned = False
        try:
            self.assertIsNone(RB.public_id(dev))
            with tempfile.TemporaryDirectory() as d:
                data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
                os.makedirs(data)
                os.makedirs(cache)
                with open(os.path.join(cache, "dev-checks-cache.json"), "w") as fh:
                    json.dump({"units": {UNIT_A: {"stake": dev}}}, fh)
                first = [{"kind": "lp_remove", "unit": UNIT_A, "tx_short": "eeee", "pool_key": "pool",
                          "_lp_stake": dev, "ahead_of_dump": False, "time": NOW - 50,
                          "ada": 8000, "reserve_ada": 12000}]
                LM._feed_rugger(cache, data, first, [], NOW - 50, CFG, False)
                self.assertTrue(RB.wallet_ok(dev))
                self.assertIn(dev, open(os.path.join(cache, RB.PULLS_NAME)).read())
                later = [{"tx_short": "eeee", "pool_key": "pool", "unit": UNIT_A, "kind": "lp_remove",
                          "ahead_of_dump": True, "time": NOW - 50, "ada": 8000, "reserve_ada": 12000}]
                LM._feed_rugger(cache, data, [], later, NOW, CFG, False)
                hit = RB.open_book(cache, data).judge(dev, UNIT_B, NOW)[0]
                self.assertIn("ahead_of_dump", hit["tags"])
                self.assertIn("lp_pulled", hit["tags"])
                self.assertIsNone(hit["rugger_id"])
                self.assertNotIn(dev, json.dumps(hit))
        finally:
            if old is not None:
                os.environ["RUGGER_ID_PEPPER"] = old
            RB._pepper_warned = False

    def test_lp_pulled_uses_the_configured_floor(self):
        dev = stake("rugger")
        high = json.loads(json.dumps(CFG))
        high.setdefault("liquidity_moves", {}).setdefault("ahead_of_dump", {})["pull_min_ada"] = 50000
        low = json.loads(json.dumps(CFG))
        low.setdefault("liquidity_moves", {}).setdefault("ahead_of_dump", {})["pull_min_ada"] = 50
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            with open(os.path.join(cache, "dev-checks-cache.json"), "w") as fh:
                json.dump({"units": {UNIT_A: {"stake": dev}}}, fh)
            big = [{"kind": "lp_remove", "unit": UNIT_A, "tx_short": "ffff", "pool_key": "pool",
                    "_lp_stake": dev, "ahead_of_dump": False, "time": NOW - 10,
                    "ada": 8000, "reserve_ada": 12000}]
            LM._feed_rugger(cache, data, big, [], NOW, high, False)
            self.assertNotIn("lp_pulled", RB.open_book(cache, data).doc["entries"].get(dev, {}).get("evidence", {}))
        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(data)
            os.makedirs(cache)
            with open(os.path.join(cache, "dev-checks-cache.json"), "w") as fh:
                json.dump({"units": {UNIT_A: {"stake": dev}}}, fh)
            small = [{"kind": "lp_remove", "unit": UNIT_A, "tx_short": "gggg", "pool_key": "pool",
                      "_lp_stake": dev, "ahead_of_dump": False, "time": NOW - 10,
                      "ada": 100, "reserve_ada": 100}]
            LM._feed_rugger(cache, data, small, [], NOW, low, False)
            self.assertIn("lp_pulled", RB.open_book(cache, data).doc["entries"][dev]["evidence"])


class OwnerAndOutput(unittest.TestCase):
    def test_owner_hash_is_dropped_and_public_text_has_no_raw_wallet(self):
        dev = stake("owner")
        owners = RB.owner_ids(dev)
        self.assertIn(RB.public_id(dev), owners)
        self.assertIsNone(V.keep_tag(RB.public_id(dev), V.DC.known_roles(), (["."], set()), owners))
        book = RB.Book(RB.MemoryStore(None), RB.settings_from(CFG))
        book.note_evidence(stake("rugger"), "cardano", "late_rug", UNIT_A, AT)
        doc = V.build(_dev(UNIT_B, "NEXT", dev_tag=RB.public_id(dev)), {"moves": []}, CFG, OWNER, AT,
                      book=book, deployers={UNIT_B: stake("rugger")})
        # The owner id is not an evidence tag even if a caller put it on the row.
        text = json.dumps(doc)
        self.assertNotIn(dev, text)
        self.assertNotIn(stake("rugger"), text)


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
            "evidence": {"rugger_id": "r-0123abcd0123", "evidence_tags": ["late_rug"]},
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
                "mint": "mintok", "code": "known_rugger", "rugger_id": "r-0123abcd0123",
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
        base58 = "1" * 32
        extra = json.load(open(os.path.join(ROOT, "solana-desk", "tests", "fixtures", "rugger_would_veto.json")))
        extra["rows"][1]["note"] = base58
        leaked_b58 = self._desk({"solana-desk/rugger_would_veto.json": extra})
        self.assertNotEqual(leaked_b58.returncode, 0)
        self.assertNotIn(base58.encode(), leaked_b58.stdout + leaked_b58.stderr)
        shaped = {"schema": 1, "entries": {}, "edges": [], "exclusions": []}
        shaped_run = self._desk({"solada-prefilter/not-the-name.json": shaped})
        self.assertNotEqual(shaped_run.returncode, 0)
        self.assertIn(b"private rugger document", shaped_run.stderr)

    def test_owner_id_match_prints_a_count_only(self):
        dev = stake("guard-owner")
        wid = RB.public_id(dev)
        doc = {"note": wid}
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        main = os.path.join(d, "main")
        os.makedirs(os.path.join(main, "scripts"))
        shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
        shutil.copy(os.path.join(ROOT, "scripts", "privacy-public-keys.txt"), os.path.join(main, "scripts"))
        shutil.copytree(SRC, os.path.join(main, "solada-prefilter"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        data = os.path.join(d, "desk-data")
        os.makedirs(data)
        with open(os.path.join(data, "note.json"), "w") as fh:
            json.dump(doc, fh)
        subprocess.run(["git", "init", "-q"], cwd=data, check=True)
        subprocess.run(["git", "add", "-A"], cwd=data, check=True)
        env = dict(os.environ, PRIVACY_GUARD_PATTERNS=dev)
        r = subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data"],
                           cwd=d, env=env, capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"owner id matched", r.stderr)
        self.assertNotIn(dev.encode(), r.stdout + r.stderr)
        self.assertNotIn(wid.encode(), r.stdout + r.stderr)

    def test_guard_derives_the_stake_and_rejects_a_pulls_file(self):
        import flags as F
        from test_dev_checks import addr
        payment = addr("guard-pay")
        derived = F.addr_info(payment)[0]
        sol = "M" * 32
        doc = {"note": RB.public_id(derived) + " " + RB.public_id(sol)}
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        main = os.path.join(d, "main")
        os.makedirs(os.path.join(main, "scripts"))
        shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
        shutil.copy(os.path.join(ROOT, "scripts", "privacy-public-keys.txt"), os.path.join(main, "scripts"))
        shutil.copytree(SRC, os.path.join(main, "solada-prefilter"),
                        ignore=shutil.ignore_patterns("tests", "__pycache__"))
        data = os.path.join(d, "desk-data")
        os.makedirs(data)
        with open(os.path.join(data, "note.json"), "w") as fh:
            json.dump(doc, fh)
        subprocess.run(["git", "init", "-q"], cwd=data, check=True)
        subprocess.run(["git", "add", "-A"], cwd=data, check=True)
        env = dict(os.environ, PRIVACY_GUARD_PATTERNS=payment + "\n" + sol)
        r = subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data"],
                           cwd=d, env=env, capture_output=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn(b"owner id matched", r.stderr)
        self.assertNotIn(payment.encode(), r.stdout + r.stderr)
        self.assertNotIn(derived.encode(), r.stdout + r.stderr)
        self.assertNotIn(sol.encode(), r.stdout + r.stderr)
        pulls = os.path.join(data, "rugger-pulls.json")
        with open(pulls, "w") as fh:
            fh.write("{}\n")
        subprocess.run(["git", "add", "-A"], cwd=data, check=True)
        env2 = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
        r2 = subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data"],
                            cwd=d, env=env2, capture_output=True)
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn(b"private cache file", r2.stderr)


if __name__ == "__main__":
    unittest.main()
