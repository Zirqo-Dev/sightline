"""Offline tests for the shared rugger blacklist. No network, no secrets, no raw addresses in assertions."""
import contextlib
import io
import json
import os
import tempfile
import unittest

os.environ.setdefault("RUGGER_ID_PEPPER", "test-pepper-not-base64-and-at-least-32-chars")

import rugger_blacklist as RB

AT = "2026-10-28T02:07:00Z"
LATER = "2026-11-28T02:07:00Z"
NOW = RB.parse_iso(AT)


def _book(hops=1, expiry=None, hub=25, exclusions=()):
    cfg = {"rugger_blacklist": {"hops": hops, "expiry_days": expiry, "shared_funder_min": hub}}
    return RB.Book(RB.MemoryStore(None), RB.settings_from(cfg), exclusions)


def _ids(blob):
    return [item.get("code") for item in blob]


class EvidencePaths(unittest.TestCase):
    def test_each_tag_is_kept_and_a_later_token_is_known_rugger(self):
        for tag in RB.TAGS:
            book = _book()
            self.assertTrue(book.note_evidence("wallet-rugger", "cardano" if tag != "rugcheck_rugged" else "solana",
                                               tag, "token-a", AT))
            self.assertEqual(book.judge("wallet-rugger", "token-a", NOW), [])
            hit = book.judge("wallet-rugger", "token-b", NOW)
            self.assertEqual(_ids(hit), ["known_rugger"])
            self.assertEqual(hit[0]["tags"], [tag])
            self.assertTrue(RB._ID_RE.fullmatch(hit[0]["rugger_id"]))
            self.assertNotIn("wallet-rugger", json.dumps(hit))

    def test_unknown_tag_and_stake_shaped_token_are_dropped(self):
        book = _book()
        self.assertFalse(book.note_evidence("wallet-rugger", "cardano", "not_a_tag", "token-a", AT))
        self.assertFalse(book.note_evidence("wallet-rugger", "cardano", "late_rug", "stake1u" + "q" * 40, AT))
        self.assertEqual(book.doc["entries"], {})

    def test_same_token_does_not_veto_itself(self):
        book = _book()
        book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        book.note_evidence("wallet-rugger", "cardano", "lp_pulled", "token-a", AT)
        self.assertEqual(book.judge("wallet-rugger", "token-a", NOW), [])
        hit = book.judge("wallet-rugger", "token-b", NOW)
        self.assertEqual(hit[0]["tags"], ["late_rug", "lp_pulled"])


class FundingHop(unittest.TestCase):
    def _rug(self, book):
        book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)

    def test_one_hop_outbound_and_not_the_grandchild_by_default(self):
        book = _book(hops=1)
        self._rug(book)
        book.note_edge("wallet-rugger", "wallet-child", "cardano", AT)
        book.note_edge("wallet-child", "wallet-grand", "cardano", AT)
        child = book.judge("wallet-child", "token-b", NOW)
        self.assertEqual(_ids(child), ["known_rugger_funded"])
        self.assertEqual(child[0]["hop"], 1)
        self.assertNotIn("wallet-rugger", json.dumps(child))
        self.assertNotIn("wallet-child", json.dumps(child))
        self.assertEqual(book.judge("wallet-grand", "token-c", NOW), [])
        entry = book.doc["entries"]["wallet-rugger"]
        self.assertEqual(entry["funded"], ["wallet-child"])
        self.assertEqual(entry["first_seen"], AT)
        self.assertEqual(entry["last_seen"], AT)

    def test_two_hops_reaches_the_grandchild(self):
        book = _book(hops=2)
        self._rug(book)
        book.note_edge("wallet-rugger", "wallet-child", "cardano", AT)
        book.note_edge("wallet-child", "wallet-grand", "cardano", AT)
        grand = book.judge("wallet-grand", "token-c", NOW)
        self.assertEqual(_ids(grand), ["known_rugger_funded"])
        self.assertEqual(grand[0]["hop"], 2)

    def test_zero_hops_keeps_the_deployer_only(self):
        book = _book(hops=0)
        self._rug(book)
        book.note_edge("wallet-rugger", "wallet-child", "cardano", AT)
        self.assertEqual(_ids(book.judge("wallet-rugger", "token-b", NOW)), ["known_rugger"])
        self.assertEqual(book.judge("wallet-child", "token-b", NOW), [])

    def test_shared_funder_sibling_is_a_caution_only_when_enabled(self):
        book = _book(hops=1, hub=25)
        book.note_edge("wallet-funder", "wallet-rugger", "cardano", AT)
        book.note_edge("wallet-funder", "wallet-sibling", "cardano", AT)
        self._rug(book)
        self.assertEqual(book.judge("wallet-sibling", "token-b", NOW), [])
        book.settings["sibling"] = True
        hit = book.judge("wallet-sibling", "token-b", NOW)
        self.assertEqual(_ids(hit), ["known_rugger_sibling"])
        self.assertEqual(hit[0]["hop"], 1)
        self.assertNotIn("known_rugger_funded", _ids(hit))
        self.assertEqual(book.judge("wallet-funder", "token-b", NOW), [])


class ExclusionAndHub(unittest.TestCase):
    def test_excluded_funder_does_not_taint_and_is_not_stored(self):
        book = _book(exclusions=("wallet-cex",))
        self.assertFalse(book.note_edge("wallet-cex", "wallet-rugger", "cardano", AT))
        self.assertFalse(book.note_edge("wallet-cex", "wallet-other", "cardano", AT))
        self.assertFalse(book.note_evidence("wallet-cex", "solana", "rugcheck_rugged", "token-a", AT))
        book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        self.assertEqual(book.doc["edges"], [])
        book.note_edge("wallet-rugger", "wallet-child", "cardano", AT)
        self.assertEqual(_ids(book.judge("wallet-child", "token-b", NOW)), ["known_rugger_funded"])
        self.assertEqual(book.judge("wallet-other", "token-b", NOW), [])
        self.assertNotIn("wallet-cex", book.doc["entries"])

    def test_high_fanout_does_not_taint_unrelated_wallets(self):
        book = _book(hub=4)
        book.note_evidence("wallet-rugger", "cardano", "ahead_of_dump", "token-a", AT)
        for i in range(4):
            book.note_edge("wallet-hub", "wallet-%d" % i, "cardano", AT)
        book.note_edge("wallet-hub", "wallet-rugger", "cardano", AT)
        for i in range(4):
            self.assertEqual(book.judge("wallet-%d" % i, "token-b", NOW), [])
        self.assertEqual(_ids(book.judge("wallet-rugger", "token-b", NOW)), ["known_rugger"])

    def test_a_rugger_who_funded_many_wallets_still_taints_them(self):
        """The fan-out floor does not gate the rugger's own first hop."""
        book = _book(hub=3)
        book.note_evidence("wallet-rugger", "cardano", "lp_pulled", "token-a", AT)
        for name in ("wallet-a", "wallet-b", "wallet-c"):
            book.note_edge("wallet-rugger", name, "cardano", AT)
        for name in ("wallet-a", "wallet-b", "wallet-c"):
            self.assertEqual(_ids(book.judge(name, "token-b", NOW)), ["known_rugger_funded"])
        self.assertEqual(_ids(book.judge("wallet-rugger", "token-b", NOW)), ["known_rugger"])
        # A wallet funded by one of those recipients is an intermediate hop and stays clean.
        book.note_edge("wallet-a", "wallet-later", "cardano", AT)
        self.assertEqual(book.judge("wallet-later", "token-c", NOW), [])

    def test_exclude_after_the_fact_drops_the_link(self):
        book = _book(hub=25)
        book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        book.note_edge("wallet-hub", "wallet-rugger", "cardano", AT)
        book.note_edge("wallet-hub", "wallet-other", "cardano", AT)
        self.assertEqual(book.judge("wallet-other", "token-b", NOW), [])
        book.settings["sibling"] = True
        self.assertEqual(_ids(book.judge("wallet-other", "token-b", NOW)), ["known_rugger_sibling"])
        book.exclude("wallet-hub")
        self.assertEqual(book.judge("wallet-other", "token-b", NOW), [])
        self.assertNotIn("wallet-hub", json.dumps(book.doc["edges"]))


class ExpiryAndPersistence(unittest.TestCase):
    def test_expiry_ignores_old_rows_and_the_default_does_not(self):
        fresh = _book(expiry=None)
        fresh.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        self.assertEqual(_ids(fresh.judge("wallet-rugger", "token-b", RB.parse_iso(LATER))), ["known_rugger"])
        aged = _book(expiry=10)
        aged.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        aged.note_edge("wallet-rugger", "wallet-child", "cardano", AT)
        later = RB.parse_iso(LATER)
        self.assertEqual(aged.judge("wallet-rugger", "token-b", later), [])
        self.assertEqual(aged.judge("wallet-child", "token-b", later), [])
        self.assertIn("wallet-rugger", aged.doc["entries"])

    def test_file_store_survives_a_second_open(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            book = RB.open_book(cache, public)
            book.note_evidence("wallet-rugger", "solana", "rugcheck_rugged", "mint-a", AT)
            book.note_edge("wallet-rugger", "wallet-child", "solana", AT)
            self.assertTrue(book.save())
            again = RB.open_book(cache, public)
            hit = again.judge("wallet-child", "mint-b", NOW)
            self.assertEqual(_ids(hit), ["known_rugger_funded"])
            raw = open(os.path.join(cache, RB.PRIVATE_NAME)).read()
            self.assertIn("wallet-rugger", raw)
            self.assertNotIn("wallet-rugger", json.dumps(hit))

    def test_cache_miss_vetoes_nobody_until_this_run_records(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            self.assertFalse(os.path.exists(os.path.join(cache, RB.PRIVATE_NAME)))
            book = RB.open_book(cache, public)
            self.assertEqual(book.judge("wallet-rugger", "token-b", NOW), [])
            self.assertTrue(book.writable)
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            book.save()
            self.assertTrue(os.path.exists(os.path.join(cache, RB.PRIVATE_NAME)))
            kept = RB.open_book(cache, public)
            self.assertEqual(_ids(kept.judge("wallet-rugger", "token-b", NOW)), ["known_rugger"])

    def test_corrupt_store_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            path = os.path.join(cache, RB.PRIVATE_NAME)
            with open(path, "w") as fh:
                fh.write("{")
            book = RB.open_book(cache, public)
            self.assertFalse(book.writable)
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            self.assertFalse(book.save())
            self.assertEqual(open(path).read(), "{")
            again = RB.open_book(cache, public)
            self.assertEqual(again.judge("wallet-rugger", "token-b", NOW), [])

    def test_public_directory_is_not_a_store(self):
        with tempfile.TemporaryDirectory() as public:
            book = RB.open_book(public, public)
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            self.assertFalse(book.save())
            self.assertFalse(os.path.exists(os.path.join(public, RB.PRIVATE_NAME)))
            again = RB.open_book(public, public)
            self.assertEqual(again.judge("wallet-rugger", "token-b", NOW), [])


class Seal(unittest.TestCase):
    def test_roundtrip_and_a_wrong_key_does_not_overwrite(self):
        key = b"test-key-not-a-secret"
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            path = os.path.join(cache, "rugger-blacklist.json.enc")
            store = RB.EncryptedFileStore(path, key, public)
            book = RB.Book(store, RB.settings_from(None))
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            self.assertTrue(book.save())
            raw = open(path, "rb").read()
            self.assertTrue(raw.startswith(b"RG1"))
            self.assertNotIn(b"wallet-rugger", raw)
            again = RB.Book(RB.EncryptedFileStore(path, key, public), RB.settings_from(None))
            self.assertEqual(_ids(again.judge("wallet-rugger", "token-b", NOW)), ["known_rugger"])
            wrong = RB.Book(RB.EncryptedFileStore(path, b"other-test-key!!", public), RB.settings_from(None))
            self.assertFalse(wrong.writable)
            wrong.note_evidence("wallet-other", "cardano", "late_rug", "token-z", AT)
            self.assertFalse(wrong.save())
            self.assertEqual(open(path, "rb").read(), raw)

    def test_defaults(self):
        self.assertEqual(RB.DEFAULTS["hops"], 1)
        self.assertIsNone(RB.DEFAULTS["expiry_days"])
        self.assertEqual(RB.DEFAULTS["shared_funder_min"], 25)
        tuned = RB.settings_from({"rugger_blacklist": {"hops": 9, "expiry_days": -1, "shared_funder_min": 1}})
        self.assertEqual(tuned["hops"], RB.MAX_HOPS)
        self.assertIsNone(tuned["expiry_days"])
        self.assertEqual(tuned["shared_funder_min"], 2)


class AdaOnly(unittest.TestCase):
    def test_ada_only_output_is_a_hop_and_a_token_output_is_not(self):
        dev = "wallet-rugger"
        txs = [{
            "inputs": [{"stake": dev}],
            "outputs": [
                {"stake": "wallet-child", "lovelace": 6_000_000, "assets": {}},
                {"stake": "wallet-buyer", "lovelace": 9_000_000, "assets": {"token": 1}},
                {"stake": "wallet-dust", "lovelace": 1_000_000, "assets": {}},
                {"stake": "wallet-script", "lovelace": 8_000_000, "assets": {}, "script": True},
            ],
        }]
        got = RB.ada_only_recipients(dev, [dict(txs[0], time=100)])
        self.assertEqual([(row["stake"], row["time"]) for row in got], [("wallet-child", 100.0)])
        mixed = [{
            "time": 100,
            "inputs": [{"stake": dev}, {"stake": "wallet-other"}],
            "outputs": [{"stake": "wallet-child", "lovelace": 6_000_000, "assets": {}}],
        }]
        self.assertEqual(RB.ada_only_recipients(dev, mixed), [])
        via_script = [{
            "time": 50,
            "inputs": [{"stake": dev}, {"stake": "wallet-batcher", "script": True}],
            "outputs": [{"stake": "wallet-child", "lovelace": 6_000_000, "assets": {}}],
        }]
        self.assertEqual([row["stake"] for row in RB.ada_only_recipients(dev, via_script)], ["wallet-child"])
        enterprise = [{
            "time": 100,
            "inputs": [{"stake": dev}, {"enterprise": True}],
            "outputs": [{"stake": "wallet-child", "lovelace": 6_000_000, "assets": {}}],
        }]
        self.assertEqual(RB.ada_only_recipients(dev, enterprise), [])


class WindowAndPrune(unittest.TestCase):
    def test_edge_after_launch_and_a_later_funder_do_not_count(self):
        book = _book()
        book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        launch = NOW
        book.note_edge("wallet-rugger", "wallet-child", "cardano", RB.iso(launch + 10))
        self.assertEqual(book.judge("wallet-child", "token-b", NOW, launch_s=launch), [])
        book.note_edge("wallet-first", "wallet-next", "cardano", RB.iso(launch - 3600))
        book.note_edge("wallet-rugger", "wallet-next", "cardano", RB.iso(launch - 60))
        self.assertEqual(book.judge("wallet-next", "token-b", NOW, launch_s=launch), [])
        alone = _book()
        alone.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        alone.note_edge("wallet-rugger", "wallet-next", "cardano", RB.iso(launch - 3600))
        self.assertEqual(_ids(alone.judge("wallet-next", "token-b", NOW, launch_s=launch)), ["known_rugger_funded"])
        old = _book()
        old.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        old.note_edge("wallet-rugger", "wallet-stale", "cardano", RB.iso(launch - 80 * 3600))
        self.assertEqual(old.judge("wallet-stale", "token-b", NOW, launch_s=launch), [])

    def test_max_edges_keeps_the_newest(self):
        book = _book()
        cap = 30
        old = RB.MAX_EDGES
        RB.MAX_EDGES = cap
        try:
            for i in range(cap + 5):
                book.note_edge("wallet-rugger", "wallet-%d" % i, "cardano", RB.iso(NOW + i))
        finally:
            RB.MAX_EDGES = old
        self.assertEqual(len(book.doc["edges"]), cap)
        funded = {e["funded"] for e in book.doc["edges"]}
        self.assertNotIn("wallet-0", funded)
        self.assertIn("wallet-%d" % (cap + 4), funded)

    def test_exclusion_hash_matches_at_runtime(self):
        digest = RB.wallet_sha256("wallet-cex")
        book = _book()
        book.exclusion_sha.add(digest)
        self.assertFalse(book.note_evidence("wallet-cex", "solana", "rugcheck_rugged", "token-a", AT))
        self.assertFalse(book.note_edge("wallet-cex", "wallet-child", "cardano", AT))
        self.assertEqual(book.judge("wallet-cex", "token-b", NOW), [])

    def test_cache_miss_after_a_non_empty_status_is_read_only(self):
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            prior = {"store": "hit", "entries": 4, "edges": 2}
            book = RB.open_book(cache, public, prior=prior)
            self.assertTrue(book.held)
            self.assertFalse(book.writable)
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            self.assertFalse(book.save())
            self.assertFalse(os.path.exists(os.path.join(cache, RB.PRIVATE_NAME)))
            self.assertEqual(book.status()["entries"], 4)
            self.assertEqual(book.status()["store"], "miss")
            RB.mark_hold(cache, book)
            self.assertTrue(os.path.exists(os.path.join(cache, RB.HOLD_NAME)))


class PepperAndPrivate(unittest.TestCase):
    def test_id_is_r_and_does_not_match_a_dev_tag(self):
        rid = RB.public_id("wallet-rugger")
        self.assertTrue(RB._ID_RE.fullmatch(rid))
        self.assertNotEqual(rid, RB.unsalted_id("wallet-rugger"))
        self.assertTrue(rid.startswith("r-") and len(rid) == 14)

    def test_a_long_non_base64_string_derives_a_stable_id(self):
        secret = "pw-manager-!@#$%^&*()-not-base64-40chars!!"
        self.assertGreaterEqual(len(secret), 40)
        old = os.environ.get("RUGGER_ID_PEPPER")
        try:
            os.environ["RUGGER_ID_PEPPER"] = secret
            key = RB.load_pepper()
            self.assertEqual(len(key), 32)
            self.assertEqual(key, RB._hkdf_sha256(secret.encode("utf-8")))
            rid = RB.public_id("wallet-rugger")
            self.assertEqual(RB.public_id("wallet-rugger"), rid)
            self.assertTrue(RB._ID_RE.fullmatch(rid))
            self.assertNotEqual(rid, RB.unsalted_id("wallet-rugger"))
        finally:
            if old is None:
                os.environ.pop("RUGGER_ID_PEPPER", None)
            else:
                os.environ["RUGGER_ID_PEPPER"] = old

    def test_shorter_than_32_is_missing_and_the_value_is_not_printed(self):
        short = "SHORTPEPPERVALUE"
        self.assertLess(len(short), 32)
        old = os.environ.get("RUGGER_ID_PEPPER")
        RB._pepper_warned = False
        try:
            os.environ["RUGGER_ID_PEPPER"] = short
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                self.assertIsNone(RB.load_pepper())
                self.assertFalse(RB.pepper_ready())
                self.assertIsNone(RB.public_id("wallet-rugger"))
                RB.warn_no_pepper()
            text = buf.getvalue()
            self.assertIn("::warning::", text)
            self.assertIn("shorter than 32 characters", text)
            self.assertNotIn(short, text)
            os.environ["RUGGER_ID_PEPPER"] = "x" * 31
            self.assertIsNone(RB.load_pepper())
        finally:
            if old is None:
                os.environ.pop("RUGGER_ID_PEPPER", None)
            else:
                os.environ["RUGGER_ID_PEPPER"] = old
            RB._pepper_warned = False

    def test_surrounding_whitespace_matches_the_stripped_string(self):
        secret = "a" * 32
        old = os.environ.get("RUGGER_ID_PEPPER")
        try:
            os.environ["RUGGER_ID_PEPPER"] = secret
            rid = RB.public_id("wallet-rugger")
            os.environ["RUGGER_ID_PEPPER"] = " \n\t" + secret + " \t\n"
            self.assertEqual(RB.public_id("wallet-rugger"), rid)
            self.assertEqual(RB.load_pepper(), RB._hkdf_sha256(secret.encode("utf-8")))
            os.environ["RUGGER_ID_PEPPER"] = "   " + ("y" * 31) + "   "
            self.assertIsNone(RB.load_pepper())
            os.environ["RUGGER_ID_PEPPER"] = "   \n\t  "
            self.assertIsNone(RB.load_pepper())
        finally:
            if old is None:
                os.environ.pop("RUGGER_ID_PEPPER", None)
            else:
                os.environ["RUGGER_ID_PEPPER"] = old

    def test_missing_pepper_keeps_the_row_and_drops_the_id(self):
        old = os.environ.pop("RUGGER_ID_PEPPER", None)
        RB._pepper_warned = False
        try:
            self.assertIsNone(RB.public_id("wallet-rugger"))
            book = _book()
            self.assertTrue(book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT))
            hit = book.judge("wallet-rugger", "token-b", NOW)
            self.assertEqual(hit[0]["code"], "known_rugger")
            self.assertIsNone(hit[0]["rugger_id"])
        finally:
            if old:
                os.environ["RUGGER_ID_PEPPER"] = old

    def test_readable_private_file_round_trip(self):
        book = _book()
        book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
        book.note_edge("wallet-rugger", "wallet-child", "cardano", AT)
        out = RB.export_readable(book.doc, "cardano")
        row = out["entries"][0]
        self.assertEqual(row["wallet"], "wallet-rugger")
        self.assertEqual(row["evidence_tags"], ["late_rug"])
        self.assertEqual(row["tokens"]["late_rug"], ["token-a"])
        self.assertEqual(row["first_seen"], AT)
        self.assertEqual(row["last_seen"], AT)
        self.assertEqual(row["funded"], ["wallet-child"])
        loaded = RB._clean_doc(out)
        self.assertEqual(loaded["entries"]["wallet-rugger"]["evidence"]["late_rug"], ["token-a"])

    def test_missing_token_does_not_call_the_network(self):
        os.environ.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        RB._token_warned = False
        RB._remote_read.clear()
        status, doc = RB.read_private("cardano")
        self.assertEqual(status, "unset")
        self.assertIsNone(doc)
        self.assertFalse(RB.push_private({"entries": {}, "edges": [], "exclusions": []}, "cardano"))

    def test_save_does_not_push(self):
        called = []
        old = RB.push_private
        RB.push_private = lambda doc, desk: called.append(desk) or True
        try:
            book = _book()
            book.desk = "cardano"
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            self.assertTrue(book.save())
            self.assertEqual(called, [])
        finally:
            RB.push_private = old

    def test_union_keeps_the_newer_last_seen_and_both_wallets(self):
        local = RB.Book(RB.MemoryStore(None), RB.settings_from(None))
        remote = RB.Book(RB.MemoryStore(None), RB.settings_from(None))
        local.note_evidence("wallet-newer", "cardano", "late_rug", "token-a", LATER)
        remote.note_evidence("wallet-newer", "cardano", "late_rug", "token-b", AT)
        remote.note_evidence("wallet-other", "cardano", "lp_pulled", "token-c", AT)
        remote.note_edge("wallet-other", "wallet-child", "cardano", AT)
        merged = RB.merge_docs(local.doc, RB.export_readable(remote.doc, "cardano"))
        row = merged["entries"]["wallet-newer"]
        self.assertEqual(row["last_seen"], LATER)
        self.assertEqual(sorted(row["evidence"]["late_rug"]), ["token-a", "token-b"])
        self.assertIn("wallet-other", merged["entries"])
        self.assertTrue(any(e["funded"] == "wallet-child" for e in merged["edges"]))

    def test_a_base58_key_is_an_owner_id(self):
        sol = "K" * 44
        ids = RB.owner_ids(sol)
        self.assertIn(RB.public_id(sol), ids)
        self.assertIn(RB.unsalted_id(sol), ids)
        self.assertEqual(RB.owner_wallets("not-a-wallet"), [])

    def test_reseed_does_not_invent_now_when_live_at_is_missing(self):
        wallet = "wallet-rugger"
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            with open(os.path.join(cache, "dev-checks-cache.json"), "w") as fh:
                json.dump({"units": {"token-a": {"late_rug": True, "stake": wallet}}}, fh)
            book = RB.open_book(cache, public)
            self.assertEqual(RB.reseed_late_rugs(book, cache, public, LATER), 1)
            self.assertIsNone(book.doc["entries"][wallet]["last_seen"])
            book.note_evidence(wallet, "cardano", "late_rug", "token-a", AT)
            self.assertEqual(book.doc["entries"][wallet]["last_seen"], AT)
            RB.reseed_late_rugs(book, cache, public, LATER)
            self.assertEqual(book.doc["entries"][wallet]["last_seen"], AT)

    def test_accept_reset_clears_a_hold(self):
        os.environ["RUGGER_ACCEPT_RESET"] = "1"
        try:
            with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
                prior = {"store": "hit", "entries": 4, "edges": 2}
                book = RB.open_book(cache, public, prior=prior)
                self.assertFalse(book.held)
                self.assertTrue(book.writable)
                self.assertTrue(book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT))
                self.assertTrue(book.save())
                RB.mark_hold(cache, book)
                self.assertFalse(os.path.exists(os.path.join(cache, RB.HOLD_NAME)))
                self.assertTrue(os.path.exists(os.path.join(cache, RB.PRIVATE_NAME)))
        finally:
            os.environ.pop("RUGGER_ACCEPT_RESET", None)


class Sync(unittest.TestCase):
    def _patch(self, clone, private, commit):
        old = (RB._clone_private, RB._repo_is_private, RB._commit_export, RB._github_repo)
        RB._clone_private = clone
        RB._repo_is_private = private
        RB._commit_export = commit
        return old

    def _restore(self, old):
        RB._clone_private, RB._repo_is_private, RB._commit_export, RB._github_repo = old
        os.environ.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        RB._remote_read.clear()
        RB._token_warned = False

    def test_push_failure_keeps_a_newer_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cache, public = os.path.join(d, "cache"), os.path.join(d, "public")
            os.makedirs(cache)
            os.makedirs(public)
            book = RB.open_book(cache, public, desk="cardano")
            book.note_evidence("wallet-newer", "cardano", "late_rug", "token-a", LATER)
            book.save()
            remote_book = RB.Book(RB.MemoryStore(None), RB.settings_from(None))
            remote_book.note_evidence("wallet-newer", "cardano", "late_rug", "token-b", AT)
            remote_book.note_evidence("wallet-other", "cardano", "lp_pulled", "token-c", AT)
            clone = os.path.join(d, "clone")
            os.makedirs(os.path.join(clone, "rugger", "cardano"))
            with open(os.path.join(clone, "rugger", "cardano", RB.PRIVATE_NAME), "w") as fh:
                json.dump(RB.export_readable(remote_book.doc, "cardano"), fh)
            os.environ["SIGHTLINE_PRIVATE_TOKEN"] = "t" * 40
            old = self._patch(lambda repo, token: clone, lambda repo, token: True,
                              lambda *a, **k: (_ for _ in ()).throw(RB._GitFail("push failed")))
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    self.assertFalse(RB.sync_cache(cache, "cardano", public))
                text = buf.getvalue()
                self.assertIn("private repo not updated", text)
                self.assertNotIn("Actions cache still has this run", text)
                self.assertNotIn("t" * 40, text)
                again = RB.open_book(cache, public)
                self.assertEqual(again.doc["entries"]["wallet-newer"]["last_seen"], LATER)
                self.assertEqual(sorted(again.doc["entries"]["wallet-newer"]["evidence"]["late_rug"]),
                                 ["token-a", "token-b"])
                self.assertIn("wallet-other", again.doc["entries"])
            finally:
                self._restore(old)

    def test_corrupt_cache_and_a_good_remote_clears_the_hold(self):
        with tempfile.TemporaryDirectory() as d:
            cache, public = os.path.join(d, "cache"), os.path.join(d, "public")
            os.makedirs(cache)
            os.makedirs(public)
            with open(os.path.join(cache, RB.PRIVATE_NAME), "w") as fh:
                fh.write("{")
            book = RB.open_book(cache, public, prior={"entries": 3, "edges": 1})
            RB.mark_hold(cache, book)
            self.assertTrue(os.path.exists(os.path.join(cache, RB.HOLD_NAME)))
            remote_book = RB.Book(RB.MemoryStore(None), RB.settings_from(None))
            remote_book.note_evidence("wallet-remote", "cardano", "late_rug", "token-a", AT)
            clone = os.path.join(d, "clone")
            os.makedirs(os.path.join(clone, "rugger", "cardano"))
            with open(os.path.join(clone, "rugger", "cardano", RB.PRIVATE_NAME), "w") as fh:
                json.dump(RB.export_readable(remote_book.doc, "cardano"), fh)
            os.environ["SIGHTLINE_PRIVATE_TOKEN"] = "t" * 40
            old = self._patch(lambda repo, token: clone, lambda repo, token: True, lambda *a, **k: None)
            try:
                self.assertTrue(RB.sync_cache(cache, "cardano", public))
                self.assertFalse(os.path.exists(os.path.join(cache, RB.HOLD_NAME)))
                healed = RB.open_book(cache, public, prior={"entries": 3, "edges": 1})
                self.assertFalse(healed.held)
                self.assertEqual(healed.judge("wallet-remote", "token-b", NOW)[0]["code"], "known_rugger")
            finally:
                self._restore(old)

    def test_a_repo_that_is_not_private_is_refused(self):
        calls = []
        os.environ["SIGHTLINE_PRIVATE_TOKEN"] = "t" * 40
        old_gh, old_clone = RB._github_repo, RB._clone_private
        def github(repo, token):
            return {"private": False}
        def clone(repo, token):
            calls.append("clone")
            raise AssertionError("cloned")
        RB._github_repo = github
        RB._clone_private = clone
        RB._remote_read.clear()
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                self.assertFalse(RB.sync_cache("/tmp/does-not-need-to-exist", "solana", None))
                self.assertEqual(RB.read_private("solana"), ("error", None))
            text = buf.getvalue()
            self.assertIn("visibility is not private", text)
            self.assertNotIn("t" * 40, text)
            self.assertEqual(calls, [])
            RB._github_repo = lambda repo, token: (_ for _ in ()).throw(OSError("down"))
            RB._remote_read.clear()
            buf2 = io.StringIO()
            with contextlib.redirect_stdout(buf2):
                self.assertFalse(RB.sync_cache("/tmp/does-not-need-to-exist", "cardano", None))
            self.assertIn("visibility is not private", buf2.getvalue())
            self.assertNotIn("down", buf2.getvalue())
        finally:
            RB._github_repo, RB._clone_private = old_gh, old_clone
            self._restore((old_clone, RB._repo_is_private, RB._commit_export, old_gh))

    def test_rebase_retries_a_non_fast_forward_and_never_forces(self):
        calls = []
        def fake(args, cwd, token):
            calls.append(list(args))
            if args == ["git", "push"] and sum(1 for c in calls if c == ["git", "push"]) == 1:
                raise RB._GitFail("! [rejected] non-fast-forward")
        old = RB._git
        RB._git = fake
        try:
            RB._rebase_and_push(".", "token-value")
        finally:
            RB._git = old
        self.assertEqual(calls, [
            ["git", "pull", "--rebase"],
            ["git", "push"],
            ["git", "pull", "--rebase"],
            ["git", "push"],
        ])
        self.assertNotIn("--force", json.dumps(calls))
        self.assertNotIn("token-value", json.dumps(calls))


class SealFaults(unittest.TestCase):
    def _book(self, path, key, public):
        return RB.Book(RB.EncryptedFileStore(path, key, public), RB.settings_from(None))

    def test_tamper_truncation_and_wrong_magic_do_not_overwrite(self):
        key = b"test-key-not-a-secret"
        with tempfile.TemporaryDirectory() as cache, tempfile.TemporaryDirectory() as public:
            path = os.path.join(cache, "rugger-blacklist.json.enc")
            book = self._book(path, key, public)
            book.note_evidence("wallet-rugger", "cardano", "late_rug", "token-a", AT)
            self.assertTrue(book.save())
            raw = open(path, "rb").read()
            for blob, label in (
                (raw[:10] + bytes([raw[10] ^ 1]) + raw[11:], "tamper"),
                (raw[:20], "trunc"),
                (b"NO1" + raw[3:], "magic"),
            ):
                with open(path, "wb") as fh:
                    fh.write(blob)
                bad = self._book(path, key, public)
                self.assertFalse(bad.writable, label)
                bad.note_evidence("wallet-other", "cardano", "late_rug", "token-z", AT)
                self.assertFalse(bad.save(), label)
                self.assertEqual(open(path, "rb").read(), blob)


if __name__ == "__main__":
    unittest.main()
