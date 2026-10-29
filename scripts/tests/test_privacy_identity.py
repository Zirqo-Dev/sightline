"""Identity n-grams are blocked by digest, and the guard script does not list them."""
import hashlib
import hmac
import os
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.sys.path.insert(0, os.path.join(ROOT, "scripts"))
import privacy_identity as PI  # noqa: E402


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Grams(unittest.TestCase):
    def test_token_bigram_and_prefix(self):
        text = "Alpha Beta carried SAMPLEPREFIX999 yesterday"
        grams = set(PI.grams(text))
        self.assertIn("alpha", grams)
        self.assertIn("alpha beta", grams)
        self.assertIn("sampleprefix", grams)
        self.assertNotIn("sample key", grams)

    def test_machine_key_is_not_the_two_word_phrase(self):
        grams = set(PI.grams("kind=sample_key only"))
        self.assertIn("sample_key", grams)
        self.assertIn("samplekey", grams)
        self.assertNotIn("sample key", grams)

    def test_trailing_mark_strips_to_the_token(self):
        self.assertIn("readacct", set(PI.grams("saw readacct_ in the note")))


class Digests(unittest.TestCase):
    def test_sha256_without_a_key(self):
        self.assertEqual(PI.digest("alpha"), sha("alpha"))

    def test_hmac_when_a_key_is_set(self):
        key = b"k" * 16
        self.assertEqual(PI.digest("alpha", key), hmac.new(key, b"alpha", hashlib.sha256).hexdigest())


class Scan(unittest.TestCase):
    def write(self, directory, name, text):
        path = os.path.join(directory, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def test_a_listed_token_fails_and_a_clean_file_passes(self):
        token = "alphaowner"
        hashes = {sha(token)}
        with tempfile.TemporaryDirectory() as d:
            bad = self.write(d, "bad.txt", "note from AlphaOwner today")
            good = self.write(d, "good.txt", "the lead whale is a codename")
            hits = PI.scan_paths([bad, good], hashes, None)
        self.assertEqual(list(hits), [bad])
        self.assertGreater(hits[bad], 0)

    def test_a_two_word_phrase_fails(self):
        phrase = "sample phrase"
        hashes = {sha(phrase)}
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "note.txt", "This Sample Phrase stayed in the file.")
            hits = PI.scan_paths([path], hashes, None)
        self.assertIn(path, hits)

    def test_a_prefix_inside_a_longer_token_fails(self):
        hashes = {sha("sampleprefix")}
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "note.txt", "id SAMPLEPREFIX999")
            hits = PI.scan_paths([path], hashes, None)
        self.assertIn(path, hits)

    def test_hmac_digest_does_not_match_plain_sha256(self):
        key = b"k" * 16
        hashes = {PI.digest("alphaowner", key)}
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "note.txt", "alphaowner")
            self.assertEqual(PI.scan_paths([path], {sha("alphaowner")}, key), {})
            self.assertIn(path, PI.scan_paths([path], hashes, key))

    def test_guard_script_blocks_a_planted_phrase_and_keeps_wallet_checks(self):
        phrase = "alpha phrase"
        env = dict(os.environ)
        env["PRIVACY_IDENTITY_HASHES"] = sha(phrase) + "\n"
        env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        env.pop("PRIVACY_IDENTITY_KEY", None)
        env.pop("GITHUB_ACTIONS", None)
        with tempfile.TemporaryDirectory() as d:
            bad = self.write(d, "bad.txt", "kept the Alpha Phrase here")
            good = self.write(d, "good.txt", "codename only")
            stake = "stake1" + "u" * 53
            wallet = self.write(d, "wallet.txt", stake + "\n")
            guard = os.path.join(ROOT, "scripts", "privacy-guard.sh")
            blocked = subprocess.run(["bash", guard, bad], cwd=ROOT, env=env, capture_output=True, text=True)
            clean = subprocess.run(["bash", guard, good], cwd=ROOT, env=env, capture_output=True, text=True)
            wallets = subprocess.run(["bash", guard, wallet], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(blocked.returncode, 0, blocked.stderr)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertNotEqual(wallets.returncode, 0, wallets.stderr)
        self.assertNotIn(phrase, blocked.stderr.lower())
        self.assertIn("identity match", blocked.stderr)

    def test_missing_hashes_warn_and_do_not_fail(self):
        env = dict(os.environ)
        env.pop("PRIVACY_IDENTITY_HASHES", None)
        env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        env.pop("PRIVACY_IDENTITY_REQUIRE", None)
        env.pop("GITHUB_ACTIONS", None)
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "plain.txt", "hello")
            proc = subprocess.run(
                ["bash", os.path.join(ROOT, "scripts", "privacy-guard.sh"), path],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("identity hashes not loaded", proc.stderr)

    def test_a_missing_hash_file_warns_unless_required(self):
        env = dict(os.environ)
        env.pop("PRIVACY_IDENTITY_HASHES", None)
        env.pop("PRIVACY_IDENTITY_REQUIRE", None)
        env["SIGHTLINE_PRIVATE_TOKEN"] = "t" * 20
        env["GITHUB_ACTIONS"] = "true"
        env["SIGHTLINE_PRIVATE_REPO"] = "not a repo"
        script = os.path.join(ROOT, "scripts", "privacy_identity.py")
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "plain.txt", "hello")
            warned = subprocess.run(
                [os.sys.executable, script, "--root", d, path],
                cwd=d, env=env, capture_output=True, text=True,
            )
            env["PRIVACY_IDENTITY_REQUIRE"] = "1"
            required = subprocess.run(
                [os.sys.executable, script, "--root", d, path],
                cwd=d, env=env, capture_output=True, text=True,
            )
        self.assertEqual(warned.returncode, 0, warned.stderr)
        self.assertIn("identity hash file unavailable", warned.stderr)
        self.assertNotIn("t" * 20, warned.stderr)
        self.assertNotEqual(required.returncode, 0)
        self.assertIn("identity hashes were not loaded", required.stderr)
        self.assertNotIn("t" * 20, required.stderr)

    def test_the_guard_source_does_not_list_blocked_phrases(self):
        with open(os.path.join(ROOT, "scripts", "privacy-guard.sh"), errors="replace") as fh:
            text = fh.read()
        # The old plaintext denylist is gone. Pieces are split so this file does not contain them either.
        for phrase in ("Bou" + "sky", "Bou" + "sky's", "Bou" + "sky holds", "Bou" + "sky wallet"):
            self.assertNotIn(phrase, text)
