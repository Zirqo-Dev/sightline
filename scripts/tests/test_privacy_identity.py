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


# Synthetic only. The preimage is not a real identity. Normalization is locked
# to the gram that existing private digests already use: lower case, no extra
# whitespace, no leading @, and no added salt.
SYNTHETIC_GRAM = "zzsynthetic zzphrase"
SYNTHETIC_SURFACE = "  @ZZSynthetic   ZZPhrase\n"


def quiet_env():
    env = dict(os.environ)
    for key in (
        "PRIVACY_IDENTITY_HASHES",
        "SIGHTLINE_PRIVATE_TOKEN",
        "PRIVACY_IDENTITY_REQUIRE",
        "PRIVACY_IDENTITY_KEY",
        "GITHUB_ACTIONS",
        "SIGHTLINE_PRIVATE_REPO",
    ):
        env.pop(key, None)
    return env


class Normalization(unittest.TestCase):
    def test_case_whitespace_and_at_prefix_hash_the_same_gram(self):
        grams = PI.grams(SYNTHETIC_SURFACE)
        self.assertIn(SYNTHETIC_GRAM, grams)
        self.assertIn("zzsynthetic", grams)
        self.assertNotIn("@zzsynthetic", grams)
        self.assertNotIn("zzsynthetic  zzphrase", grams)
        self.assertEqual(PI.grams("@ZZSynthetic"), PI.grams("zzsynthetic"))
        self.assertEqual(PI.grams("ZZSynthetic\tZZPhrase"), PI.grams("zzsynthetic zzphrase"))

    def test_no_extra_salt_and_only_a_long_key_changes_the_digest(self):
        self.assertEqual(PI.digest(SYNTHETIC_GRAM), sha(SYNTHETIC_GRAM))
        self.assertEqual(PI.digest(SYNTHETIC_GRAM, None), sha(SYNTHETIC_GRAM))
        key = b"k" * 16
        self.assertEqual(
            PI.digest(SYNTHETIC_GRAM, key),
            hmac.new(key, SYNTHETIC_GRAM.encode(), hashlib.sha256).hexdigest(),
        )
        self.assertNotEqual(PI.digest(SYNTHETIC_GRAM, key), sha(SYNTHETIC_GRAM))
        # The surface form is not what gets hashed. The normalized gram is.
        self.assertNotEqual(PI.digest(SYNTHETIC_SURFACE.strip()), sha(SYNTHETIC_GRAM))
        old = os.environ.get("PRIVACY_IDENTITY_KEY")
        try:
            os.environ["PRIVACY_IDENTITY_KEY"] = "short-key"
            self.assertIsNone(PI.identity_key())
            os.environ["PRIVACY_IDENTITY_KEY"] = "  " + ("k" * 16) + "  "
            self.assertEqual(PI.identity_key(), b"k" * 16)
        finally:
            if old is None:
                os.environ.pop("PRIVACY_IDENTITY_KEY", None)
            else:
                os.environ["PRIVACY_IDENTITY_KEY"] = old

    def test_uppercase_digest_lines_still_match_the_normalized_gram(self):
        listed = sha(SYNTHETIC_GRAM).upper()
        status, found = PI.classify_hash_text("\n  " + listed + "  \n")
        self.assertEqual(status, "ok")
        self.assertEqual(found, {sha(SYNTHETIC_GRAM)})
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "note.txt")
            with open(path, "w") as fh:
                fh.write(SYNTHETIC_SURFACE)
            hits = PI.scan_paths([path], found, None)
        self.assertIn(path, hits)


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

    def _guard(self, root, env, *files):
        return subprocess.run(
            ["bash", os.path.join(ROOT, "scripts", "privacy-guard.sh"), "--root", root, *files],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )

    def test_absent_hashes_fail_closed_when_require_is_unset(self):
        for require in (None, "", "0", "false", "no"):
            env = quiet_env()
            if require is not None:
                env["PRIVACY_IDENTITY_REQUIRE"] = require
            with tempfile.TemporaryDirectory() as d:
                path = self.write(d, "plain.txt", "hello")
                proc = self._guard(d, env, path)
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("identity hashes were not loaded", proc.stderr)
            self.assertNotIn("skipped", proc.stderr)

    def test_empty_hash_list_fails_closed(self):
        script = os.path.join(ROOT, "scripts", "privacy_identity.py")
        for body in ("", "\n", "\n  \n"):
            env = quiet_env()
            with tempfile.TemporaryDirectory() as d:
                with open(os.path.join(d, ".privacy-identity-hashes"), "w") as fh:
                    fh.write(body)
                path = self.write(d, "plain.txt", "hello")
                proc = subprocess.run(
                    [os.sys.executable, script, "--root", d, path],
                    cwd=d, env=env, capture_output=True, text=True,
                )
                guarded = self._guard(d, env, "plain.txt")
            self.assertNotEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("identity hash list is empty", proc.stderr)
            self.assertNotEqual(guarded.returncode, 0, guarded.stderr)
            self.assertIn("identity hash list is empty", guarded.stderr)

    def test_malformed_hash_file_fails_closed(self):
        script = os.path.join(ROOT, "scripts", "privacy_identity.py")
        env = quiet_env()
        bad = "not-a-digest\n" + ("f" * 63) + "\n"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".privacy-identity-hashes"), "w") as fh:
                fh.write(bad)
            path = self.write(d, "plain.txt", "hello")
            proc = subprocess.run(
                [os.sys.executable, script, "--root", d, path],
                cwd=d, env=env, capture_output=True, text=True,
            )
            guarded = self._guard(d, env, "plain.txt")
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("identity hash list is malformed", proc.stderr)
        self.assertNotIn("not-a-digest", proc.stderr)
        self.assertNotEqual(guarded.returncode, 0, guarded.stderr)
        self.assertIn("identity hash list is malformed", guarded.stderr)
        self.assertNotIn("not-a-digest", guarded.stderr)

    def test_a_non_digest_line_does_not_drop_a_real_digest(self):
        # Historical loader behavior: skip lines that are not digests.
        # Dropping the digest line would stop matching lists that already exist.
        good = sha(SYNTHETIC_GRAM)
        env = quiet_env()
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".privacy-identity-hashes"), "w") as fh:
                fh.write(good + "\nnot-a-digest\n")
            self.write(d, "plain.txt", "hello")
            self.write(d, "planted.txt", SYNTHETIC_SURFACE)
            clean = self._guard(d, env, "plain.txt")
            blocked = self._guard(d, env, "planted.txt")
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertNotEqual(blocked.returncode, 0, blocked.stderr)
        self.assertIn("identity match", blocked.stderr)
        self.assertNotIn("not-a-digest", clean.stderr + blocked.stderr)
        self.assertNotIn(good, clean.stderr + blocked.stderr)

    def test_a_blank_env_list_does_not_erase_a_well_formed_file(self):
        env = quiet_env()
        env["PRIVACY_IDENTITY_HASHES"] = "\n"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, ".privacy-identity-hashes"), "w") as fh:
                fh.write(sha(SYNTHETIC_GRAM) + "\n")
            self.write(d, "plain.txt", "hello")
            self.write(d, "planted.txt", SYNTHETIC_SURFACE)
            clean = self._guard(d, env, "plain.txt")
            blocked = self._guard(d, env, "planted.txt")
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertNotEqual(blocked.returncode, 0, blocked.stderr)
        self.assertNotIn("identity hash list is empty", clean.stderr)

    def test_a_blank_env_list_alone_fails_closed(self):
        env = quiet_env()
        env["PRIVACY_IDENTITY_HASHES"] = " \n"
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "plain.txt", "hello")
            proc = self._guard(d, env, path)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("identity hash list is empty", proc.stderr)

    def test_synthetic_phrase_is_blocked_with_exit_nonzero(self):
        env = quiet_env()
        env["PRIVACY_IDENTITY_HASHES"] = sha(SYNTHETIC_GRAM) + "\n"
        with tempfile.TemporaryDirectory() as d:
            planted = self.write(d, "planted.txt", SYNTHETIC_SURFACE)
            clean = self.write(d, "clean.txt", "codename only")
            blocked = self._guard(d, env, planted)
            ok = self._guard(d, env, clean)
        self.assertNotEqual(blocked.returncode, 0, blocked.stderr)
        self.assertIn("identity match", blocked.stderr)
        self.assertNotIn(SYNTHETIC_GRAM, blocked.stderr.lower())
        self.assertNotIn("zzsynthetic", blocked.stderr.lower())
        self.assertEqual(ok.returncode, 0, ok.stderr)

    def test_a_missing_private_hash_file_fails_closed_without_printing_the_token(self):
        env = quiet_env()
        env["SIGHTLINE_PRIVATE_TOKEN"] = "t" * 20
        env["SIGHTLINE_PRIVATE_REPO"] = "not a repo"
        script = os.path.join(ROOT, "scripts", "privacy_identity.py")
        with tempfile.TemporaryDirectory() as d:
            path = self.write(d, "plain.txt", "hello")
            failed = subprocess.run(
                [os.sys.executable, script, "--root", d, path],
                cwd=d, env=env, capture_output=True, text=True,
            )
            env["PRIVACY_IDENTITY_REQUIRE"] = "0"
            still = subprocess.run(
                [os.sys.executable, script, "--root", d, path],
                cwd=d, env=env, capture_output=True, text=True,
            )
        self.assertNotEqual(failed.returncode, 0, failed.stderr)
        self.assertIn("identity hash file unavailable", failed.stderr)
        self.assertNotIn("t" * 20, failed.stderr)
        self.assertNotIn("skipped", failed.stderr)
        self.assertNotEqual(still.returncode, 0, still.stderr)
        self.assertNotIn("t" * 20, still.stderr)

    def test_the_guard_source_does_not_list_blocked_phrases(self):
        with open(os.path.join(ROOT, "scripts", "privacy-guard.sh"), errors="replace") as fh:
            text = fh.read()
        # The old plaintext denylist is gone. Pieces are split so this file does not contain them either.
        for phrase in ("Bou" + "sky", "Bou" + "sky's", "Bou" + "sky holds", "Bou" + "sky wallet"):
            self.assertNotIn(phrase, text)
