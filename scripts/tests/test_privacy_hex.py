"""Long hex in fixtures and data is a privacy failure unless it is an obvious fake or allowlisted."""
import datetime
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.sys.path.insert(0, os.path.join(ROOT, "scripts"))
import privacy_hex as H  # noqa: E402

RULES = os.path.join(ROOT, "scripts", "privacy-hex-allow.txt")


def token_of(label):
    return hashlib.sha256(label.encode()).hexdigest()


class Fakes(unittest.TestCase):
    def test_repeated_nibbles_dead_words_and_small_counters_pass(self):
        for token in (
            "0x" + "0" * 40,
            "0x" + "f" * 64,
            "0x" + "dead",
            "0x" + "dead".ljust(40, "0"),
            "0x" + "deadbeef" + "0" * 32,
            "0x" + "0" * 39 + "1",
            "0x" + "0" * 63 + "1",
            "abcdef0123456789" * 4,
            "aa11bb22cc33dd44ee55ff6677889900aabbccdd11223344556677889900ab01",
        ):
            self.assertTrue(H.obvious_fake(token), token)

    def test_a_sha256_of_a_label_is_not_a_fake(self):
        self.assertFalse(H.obvious_fake(token_of("fixture-order-not-a-secret")))
        self.assertFalse(H.obvious_fake("0x" + token_of("fixture-address-not-a-secret")[:40]))

    def test_a_dummy_marker_must_be_the_whole_value(self):
        marker = "bb22cc33dd44ee55ff6677889900"
        body = ("ab" * 18) + marker
        self.assertEqual(len(body), 64)
        self.assertIn(marker, body)
        self.assertFalse(H.obvious_fake(body))
        self.assertTrue(H.obvious_fake(
            "aa11bb22cc33dd44ee55ff6677889900aabbccdd11223344556677889900ab01"
        ))


def hits(rules, rel, text, today=None):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        found = H.scan_file(path, rules, today=today)
    blob = json.dumps(found)
    return found, blob


class Rules(unittest.TestCase):
    def setUp(self):
        self.rules = H.load_rules(RULES)

    def test_order_hash_and_salt_are_rejected(self):
        order = "0x" + token_of("fixture-order-not-a-secret")
        salt = token_of("fixture-salt-not-a-secret")
        found, blob = hits(self.rules, "order.json", json.dumps({"order_hash": order, "salt": salt}))
        self.assertEqual(len(found), 2)
        self.assertNotIn(order, blob)
        self.assertNotIn(salt, blob)

    def test_whole_values_of_the_right_shape_pass(self):
        digest = token_of("public-file")
        addr = "0x" + "cd" * 20
        utxo = token_of("pool-tx") + "#1 (pool note)"
        found, _blob = hits(self.rules, "doc.json", json.dumps({
            "sha256": digest,
            "lp_name": token_of("lp-name"),
            "unit": token_of("unit-id"),
            "contract_address": addr,
            "pool_utxo": utxo,
        }))
        self.assertEqual(found, [])
        spec = "aggregator (`0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419`) on Ethereum\n"
        found, _blob = hits(self.rules, "SPEC.md", spec)
        self.assertEqual(found, [])

    def test_wrong_shapes_and_a_csv_sha256_cell_fail(self):
        digest = token_of("shape-probe")
        probes = (
            ("sha.json", {"sha256": "0x" + "ab" * 20}),
            ("unit.json", {"unit": digest + "!"}),
            ("lp.json", {"lp_name": "0x" + digest}),
            ("contract.json", {"contract_address": "0x" + digest}),
        )
        for name, doc in probes:
            found, blob = hits(self.rules, name, json.dumps(doc))
            self.assertEqual(len(found), 1, name)
            self.assertNotIn(digest, blob)
        csv_text = 'time,note\n1,"sha256: %s"\n' % digest
        found, blob = hits(self.rules, "tasks.csv", csv_text)
        self.assertEqual(len(found), 1)
        self.assertNotIn(digest, blob)

    def test_markdown_label_is_only_a_bare_64_directly_after_the_word(self):
        digest = token_of("md-digest")
        found, _blob = hits(self.rules, "note.md", "file sha256 %s\n" % digest)
        self.assertEqual(found, [])
        found, _blob = hits(self.rules, "note.md", "%s then the word sha256\n" % digest)
        self.assertEqual(len(found), 1)
        found, _blob = hits(self.rules, "note.md", "sha256 0x%s\n" % digest)
        self.assertEqual(len(found), 1)
        found, _blob = hits(self.rules, "note.txt", "sha256 %s\n" % digest)
        self.assertEqual(len(found), 1)

    def test_cdn_urls_allow_bare_64_and_explorers_do_not(self):
        image = token_of("cdn-image")
        addr = "0x" + "ab" * 20
        cdn = "https://cdn.dexscreener.com/cms/images/%s?width=800\n" % image
        found, _blob = hits(self.rules, "icons.json", json.dumps({"origin": cdn.strip()}))
        self.assertEqual(found, [])
        found, _blob = hits(self.rules, "icons.json", json.dumps({
            "origin": "https://cdn.dexscreener.com/address/%s" % addr,
        }))
        self.assertEqual(len(found), 1)
        found, blob = hits(self.rules, "note.md", "https://etherscan.io/address/%s\n" % addr)
        self.assertEqual(len(found), 1)
        self.assertNotIn(addr, blob)
        found, _blob = hits(self.rules, "note.json", json.dumps({"note": image}))
        self.assertEqual(len(found), 1)

    def test_nft_pilot_paths_allow_collection_ids_digests_and_contracts(self):
        digest = token_of("order-digest")
        addr = "0x" + "ef" * 20
        collection = "nft_collection:eip155:1:" + addr
        books = {
            "offers": {collection: {"digests": [digest]}},
            "passes": {"2026-11-20": [collection]},
            "note": addr,
        }
        found, blob = hits(self.rules, "nft-pilot/books.json", json.dumps(books))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["key"], "note")
        self.assertNotIn(addr, blob)
        found, _blob = hits(self.rules, "nft-pilot/units.jsonl", json.dumps({
            "asset_id": collection, "contract": addr,
        }) + "\n")
        self.assertEqual(found, [])
        found, _blob = hits(self.rules, "elsewhere.json", json.dumps({"asset_id": collection}))
        self.assertEqual(len(found), 1)
        digest = token_of("prereg-file")
        found, _blob = hits(
            self.rules, "decisions.jsonl",
            json.dumps({"message": "prereg sha256 " + digest}) + "\n",
        )
        self.assertEqual(found, [])
        found, _blob = hits(
            self.rules, "decisions.jsonl",
            json.dumps({"message": "note " + digest}) + "\n",
        )
        self.assertEqual(len(found), 1)

    def test_the_tracked_tree_has_no_unlisted_long_hex(self):
        paths = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        found = H.scan_paths([os.path.join(ROOT, path) for path in paths], self.rules)
        self.assertEqual(
            [(item["path"], item["line"], item["kind"], item["key"]) for item in found],
            [],
        )


class Hold(unittest.TestCase):
    def test_the_hold_is_one_cell_until_the_expiry_and_the_value_is_not_listed(self):
        text = Path(RULES).read_text(encoding="utf-8")
        hold_lines = [line for line in text.splitlines() if line.startswith("hold-cell:")]
        self.assertEqual(len(hold_lines), 1)
        self.assertNotIn("0x", hold_lines[0].lower())
        self.assertIn("until:2026-11-26", hold_lines[0])
        held = "0x" + token_of("desk-task-row")[:40]
        other = "0x" + token_of("other-mint-same-run")[:40]
        pin = hashlib.sha256(held.encode()).hexdigest()
        rules = H.load_rules(self._rules(pin))
        header = "time,run_id,agent,task,mint,book,result\n"
        same = header + "1,20261105T1930,scanner,scan,%s,,skip\n" % held
        different = header + "1,20261105T1930,scanner,scan,%s,,skip\n" % other
        found, blob = hits(rules, "solana-desk/tasks.csv", same)
        self.assertEqual(found, [])
        self.assertNotIn(held, blob)
        found, blob = hits(rules, "solana-desk/tasks.csv", different)
        self.assertEqual(len(found), 1)
        self.assertNotIn(other, blob)
        found, _blob = hits(rules, "notsolana-desk/tasks.csv", same)
        self.assertEqual(len(found), 1)
        found, _blob = hits(
            rules, "solana-desk/tasks.csv", same, today=datetime.date(2026, 11, 27),
        )
        self.assertEqual(len(found), 1)
        salt = token_of("salt-on-the-held-row")
        salty = header + "1,20261105T1930,scanner,scan,%s,%s,skip\n" % (held, salt)
        found, blob = hits(rules, "solana-desk/tasks.csv", salty)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "bare-64")
        self.assertNotIn(salt, blob)
        self.assertNotIn(held, blob)

    def _rules(self, pin):
        handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        handle.write(
            "field:sha256 shape:bare-64\n"
            "hold-cell:solana-desk/tasks.csv column:mint run:20261105T1930 "
            "sha256:%s until:2026-11-26\n" % pin
        )
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name


class Guard(unittest.TestCase):
    def test_a_planted_order_hash_fails_and_the_value_is_not_printed(self):
        order = "0x" + token_of("fixture-order-not-a-secret")
        env = os.environ.copy()
        env["PRIVACY_IDENTITY_HASHES"] = token_of("zzsynthetic" + "identityblocktoken")
        env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        env.pop("PRIVACY_IDENTITY_KEY", None)
        env.pop("GITHUB_ACTIONS", None)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "order.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"order_hash": "%s"}\n' % order)
            proc = subprocess.run(
                ["bash", os.path.join(ROOT, "scripts", "privacy-guard.sh"), path],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("long hex", proc.stderr)
        self.assertNotIn(order, proc.stdout + proc.stderr)
        self.assertNotIn(order[2:], proc.stdout + proc.stderr)
