"""Offline tests for the shared rugger blacklist. No network, no secrets, no raw addresses in assertions."""
import base64
import json
import os
import tempfile
import unittest

os.environ.setdefault("RUGGER_ID_PEPPER", base64.b64encode(b"t" * 32).decode())

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
