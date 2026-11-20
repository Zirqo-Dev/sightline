"""Long hex in fixtures and data is a privacy failure unless it is an obvious fake or allowlisted."""
import hashlib
import os
import subprocess
import tempfile
import unittest

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


class Rules(unittest.TestCase):
    def setUp(self):
        self.rules = H.load_rules(RULES)

    def test_order_hash_and_salt_are_rejected(self):
        order = "0x" + token_of("fixture-order-not-a-secret")
        salt = token_of("fixture-salt-not-a-secret")
        line = '{"order_hash": "%s", "salt": "%s"}' % (order, salt)
        self.assertFalse(H.allowed(order, line, line.index(order), self.rules))
        self.assertFalse(H.allowed(salt, line, line.index(salt), self.rules))

    def test_a_sha256_field_and_the_public_aggregator_pass(self):
        digest = token_of("public-file")
        line = '{"sha256": "%s"}' % digest
        self.assertTrue(H.allowed(digest, line, line.index(digest), self.rules))
        addr = "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419"
        spec = "aggregator (`%s`) on Ethereum" % addr
        self.assertTrue(H.allowed(addr, spec, spec.index(addr), self.rules))

    def test_a_url_image_id_passes_and_a_bare_note_does_not(self):
        image = token_of("cdn-image")
        url = 'https://cdn.example.test/images/%s?width=800' % image
        self.assertTrue(H.allowed(image, url, url.index(image), self.rules))
        note = '{"note": "%s"}' % image
        self.assertFalse(H.allowed(image, note, note.index(image), self.rules))

    def test_the_tracked_tree_has_no_unlisted_long_hex(self):
        paths = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
        found = H.scan_paths([os.path.join(ROOT, path) for path in paths], self.rules)
        self.assertEqual(
            [(item["path"], item["line"], item["kind"], item["key"]) for item in found],
            [],
        )


class Guard(unittest.TestCase):
    def test_a_planted_order_hash_fails_and_the_value_is_not_printed(self):
        order = "0x" + token_of("fixture-order-not-a-secret")
        env = os.environ.copy()
        env["PRIVACY_IDENTITY_HASHES"] = token_of("zzsyntheticidentityblocktoken")
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
