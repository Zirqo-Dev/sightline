"""Short Cardano fragments are scrubbed at publish time and fail the privacy guard.

Fragments are built in pieces so this file does not contain one. Counts and
paths only: a failure must not print an id.
"""
import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load_build():
    spec = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build-site.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ellipsis_fragment():
    """Head of one bech32 character, then a one-character ellipsis. Tail is bech32."""
    return "addr" + "1q" + "\u2026" + "qxyz"


def dots_fragment():
    return "addr" + "1qx" + ".." + "qxyz"


def plain_fragment():
    return "stake" + "1" + ("u" * 6)


def kind_marks():
    return "addr" + "1q", "stake" + "1u"


def guard(text, name="note.json"):
    env = os.environ.copy()
    env["PRIVACY_IDENTITY_HASHES"] = hashlib.sha256(b"zzsynthetic" + b"identityblocktoken").hexdigest()
    env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
    env.pop("PRIVACY_IDENTITY_KEY", None)
    env.pop("PRIVACY_GUARD_PATTERNS", None)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        proc = subprocess.run(
            ["bash", os.path.join(ROOT, "scripts", "privacy-guard.sh"), path],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )
    return proc


class ScrubTests(unittest.TestCase):
    def test_ellipsis_and_plain_fragments_become_stable_tags(self):
        mod = load_build()
        dotted = dots_fragment()
        marked = ellipsis_fragment()
        plain = plain_fragment()
        raw = json.dumps(
            {"note": "paid %s and %s then %s" % (dotted, marked, plain)},
            ensure_ascii=False,
        ).encode()
        out = mod.scrub_public_feed(raw)
        text = out.decode("utf-8")
        for frag in (dotted, marked, plain):
            self.assertNotIn(frag, text)
            self.assertIn(mod.cardano_tag(frag), text)
        self.assertEqual(text.count(mod.cardano_tag(dotted)), 1)
        again = mod.scrub_public_feed(out)
        self.assertEqual(again, out)

    def test_allowlisted_address_and_ordinary_text_stay(self):
        mod = load_build()
        platform = next(iter(mod.platform_addresses()))
        kind, stake_kind = kind_marks()
        sol = "1" * 32
        mint = "Mint" + ("1" * 28) + "pump"
        evm = "0x" + ("1" * 40)
        bare = "ab" * 20
        note = "The tape was quiet. %s %s %s %s %s" % (kind, stake_kind, sol, mint, bare)
        raw = json.dumps({"curve": platform, "ada": 12.5, "note": note, "evm": evm}).encode()
        out = mod.scrub_public_feed(raw)
        doc = json.loads(out)
        self.assertEqual(doc["curve"], platform)
        self.assertEqual(doc["ada"], 12.5)
        self.assertEqual(doc["note"], note)
        self.assertEqual(doc["evm"], evm)
        self.assertNotIn(b"w-", out)

    def test_long_id_is_still_replaced(self):
        mod = load_build()
        stake = "stake" + "1" + ("a" * 53)
        out = mod.scrub_public_feed(json.dumps({"note": stake}).encode())
        self.assertNotIn(stake.encode(), out)
        self.assertIn(mod.cardano_tag(stake).encode(), out)

    def test_published_tree_scrub_keeps_numbers(self):
        mod = load_build()
        frag = dots_fragment()
        with tempfile.TemporaryDirectory() as tmp:
            dest = pathlib.Path(tmp)
            (dest / "note.json").write_text(
                '{"ada": 12.5, "note": "paid %s today"}\n' % frag, encoding="utf-8"
            )
            (dest / "ok.txt").write_text("no wallet here\n", encoding="utf-8")
            changed = mod.scrub_public_tree(dest)
            self.assertEqual(changed, 1)
            doc = json.loads((dest / "note.json").read_text(encoding="utf-8"))
            self.assertEqual(doc["ada"], 12.5)
            self.assertNotIn(frag, doc["note"])
            self.assertIn(mod.cardano_tag(frag), doc["note"])
            self.assertEqual((dest / "ok.txt").read_text(encoding="utf-8"), "no wallet here\n")


class GuardTests(unittest.TestCase):
    def test_short_fragments_fail_and_are_not_printed(self):
        for frag in (ellipsis_fragment(), dots_fragment(), plain_fragment()):
            proc = guard(json.dumps({"note": frag}, ensure_ascii=False))
            report = proc.stdout + proc.stderr
            self.assertNotEqual(proc.returncode, 0, report)
            self.assertNotIn(frag, report)
            self.assertIn("PRIVACY GUARD", report)

    def test_allowlisted_address_passes(self):
        platform = next(iter(load_build().platform_addresses()))
        proc = guard(json.dumps({"curve": platform}, ensure_ascii=False))
        report = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, report)
        self.assertNotIn(platform, report)
        self.assertIn("privacy-guard: OK", proc.stdout)

    def test_ordinary_solana_and_evm_text_passes(self):
        kind, stake_kind = kind_marks()
        body = {
            "note": "The tape was quiet and the pool stayed open.",
            "kinds": [kind, stake_kind],
            "sol": "1" * 32,
            "mint": "Mint" + ("1" * 28) + "pump",
            "evm": "0x" + ("1" * 40),
            "bare": "ab" * 20,
        }
        proc = guard(json.dumps(body))
        report = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, report)
        self.assertIn("privacy-guard: OK", proc.stdout)
        self.assertNotIn("long hex", report)


if __name__ == "__main__":
    unittest.main()
