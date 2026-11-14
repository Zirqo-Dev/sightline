"""Caps, costs, dry-run files, and the first two run call counts."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from races.dry import DryTransport
from races.impact import buy_impact_bps, sell_impact_bps, side_cost_bps
from races.run import load_config, run_once

ROOT = Path(__file__).resolve().parents[2]
CFG, _locked = load_config(ROOT / "races")


class Caps(unittest.TestCase):
    def test_caps_are_14_14_7_3_2_5(self):
        caps = CFG["jupiter"]["caps"]
        self.assertEqual(caps, {"R1": 14, "R2": 14, "R3": 7, "R4": 3, "SOL": 2, "R5": 5})
        self.assertEqual(sum(caps.values()), 45)
        self.assertEqual(CFG["jupiter"]["total_cap"], 45)
        self.assertEqual(CFG["starting_sol"], 5.0)
        self.assertEqual(CFG["ticket_sol"], 0.5)


class Cost(unittest.TestCase):
    def test_sell_impact_is_out_over_reserve_and_missing_reserve_caps(self):
        self.assertAlmostEqual(sell_impact_bps(0.4, 50), 10000 * 0.4 / 50)
        self.assertAlmostEqual(buy_impact_bps(0.5, 50), 10000 * 0.5 / 50.5)
        cost, source = side_cost_bps(None, False, CFG)
        self.assertEqual(cost, 600)
        self.assertEqual(source, "cap_missing_reserve")
        stop, _src = side_cost_bps(80, True, CFG)
        plain, _src = side_cost_bps(80, False, CFG)
        self.assertAlmostEqual(stop - plain, 150)


class DryRun(unittest.TestCase):
    def test_two_runs_log_per_wallet_counts_and_the_board_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            stamps = ("2026-11-11T12:12:00Z", "2026-11-11T12:42:00Z")
            for stamp in stamps:
                now = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                run_once(path, now, dry_run=True, transport=DryTransport())
            runs = [json.loads(line) for line in (path / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
            summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
            positions = json.loads((path / "positions.json").read_text(encoding="utf-8"))
            self.assertTrue((path / "trades.jsonl").is_file())
            self.assertTrue((path / "equity.jsonl").is_file())
            self.assertFalse((path / "config.lock.json").exists())
            trades = [json.loads(line) for line in (path / "trades.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(row.get("wallet") == "R1" for row in trades))
            equity = [json.loads(line) for line in (path / "equity.jsonl").read_text(encoding="utf-8").splitlines()]
            by_slot = {}
            for row in equity:
                self.assertIn("wallet", row)
                self.assertTrue(row["wallet"])
                by_slot.setdefault(row["ts"], set()).add(row["wallet"])
            self.assertTrue(by_slot)
            for wallets in by_slot.values():
                self.assertEqual(wallets, {"R1", "R2", "R3", "R4", "R5", "SOL"})
        self.assertEqual(len(runs), 2)
        names = ("R1", "R2", "R3", "R4", "SOL", "R5")
        for row in runs:
            self.assertEqual(set(names), set(row["wallets"]))
            self.assertEqual(row["calls"]["cap"], 45)
            self.assertEqual(sum(row["wallets"][name] for name in names), row["calls"]["total"])
            self.assertLessEqual(row["wallets"]["R5"], 5)
        self.assertEqual(summary["banner"], "Display only, untested. A lead in the race is luck until tested.")
        self.assertNotIn("winner", json.dumps(summary).lower())
        self.assertIn("avg_pct_deployed", summary["wallets"][0])
        self.assertEqual(positions["schema_version"], 1)
        from races.scrub import scrub
        leaked = "1" * 44
        kept = {"symbol": "MEME", "action": "buy", "mint": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"}
        for good in ("R1", "SOL"):
            cleaned = scrub({"wallet": good, **kept}, {kept["mint"]})
            self.assertEqual(cleaned["wallet"], good)
            self.assertEqual(cleaned["symbol"], "MEME")
            self.assertEqual(cleaned["action"], "buy")
            self.assertEqual(cleaned["mint"], kept["mint"])
        for bad in ("R1\n", "r1", "R6", "SOL_HOLD", "sol", "SOL\n", leaked):
            cleaned = scrub({"wallet": bad, **kept}, {kept["mint"]})
            self.assertNotIn("wallet", cleaned)
            self.assertNotIn(bad, json.dumps(cleaned))
            self.assertEqual(cleaned["symbol"], "MEME")
            self.assertEqual(cleaned["action"], "buy")
            self.assertEqual(cleaned["mint"], kept["mint"])


class Isolation(unittest.TestCase):
    def test_sources_do_not_touch_the_frozen_desk(self):
        files = [p for p in (ROOT / "races").rglob("*.py") if "tests" not in p.parts]
        text = "\n".join(p.read_text(encoding="utf-8") for p in files)
        self.assertNotRegex(text, r"(import\s+desk|solana-desk|desk-data|JUPITER_API_KEY)")
        wf = (ROOT / ".github/workflows/races.yml").read_text(encoding="utf-8")
        self.assertIn("sightline-races", wf)
        self.assertIn('cron: "12,42 * * * *"', wf)
        self.assertIn("timeout-minutes: 5", wf)
        self.assertIn("races-data", wf)
        self.assertNotIn("ref: desk-data", wf)
        self.assertNotIn("JUPITER_API_KEY", wf)
        self.assertNotIn("\n  push:", wf)
        self.assertIn("contents: write", wf)


_TOKEN_REF = re.compile(r"\$\{\{\s*GITHUB_TOKEN\s*\}\}|\$\{GITHUB_TOKEN\}|\$GITHUB_TOKEN")


def _workflow_steps(text):
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header = re.match(r"^(\s*)steps:\s*$", lines[i])
        if not header:
            i += 1
            continue
        base = len(header.group(1))
        i += 1
        current = None
        buf = []
        while i < len(lines):
            line = lines[i]
            if not line.strip():
                if current is not None:
                    buf.append(line)
                i += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= base:
                break
            if indent == base + 2 and line.lstrip().startswith("- "):
                if current is not None:
                    yield "\n".join(buf)
                current = line
                buf = [line]
            elif current is not None:
                buf.append(line)
            i += 1
        if current is not None:
            yield "\n".join(buf)


def _run_script(step):
    lines = step.splitlines()
    script = []
    in_run = False
    run_indent = None
    for line in lines[1:]:
        if not line.strip():
            if in_run:
                script.append("")
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if in_run and indent > run_indent:
            script.append(stripped)
            continue
        in_run = False
        if stripped == "run:" or stripped.startswith("run:") :
            in_run = True
            run_indent = indent
            rest = stripped[4:].strip()
            if rest and rest not in ("|", ">", "|-", ">-"):
                script.append(rest)
    return "\n".join(script)


def _maps_github_token(step):
    in_env = False
    env_indent = None
    in_run = False
    run_indent = None
    for line in step.splitlines()[1:]:
        if not line.strip() or line.strip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if in_run and indent > run_indent:
            continue
        in_run = False
        if in_env and indent > env_indent:
            if stripped.startswith("GITHUB_TOKEN:"):
                return True
            continue
        in_env = False
        if stripped == "env:" or stripped.startswith("env:"):
            in_env = True
            env_indent = indent
            continue
        if stripped == "run:" or stripped.startswith("run:"):
            in_run = True
            run_indent = indent
    return False


def unmapped_github_token(text):
    bad = []
    for step in _workflow_steps(text):
        if _TOKEN_REF.search(_run_script(step)) and not _maps_github_token(step):
            bad.append(step.splitlines()[0].strip())
    return bad


class WorkflowToken(unittest.TestCase):
    def test_a_step_that_uses_the_token_must_map_it(self):
        sample = "jobs:\n  j:\n    steps:\n      - name: Push\n        run: echo ${GITHUB_TOKEN}\n"
        self.assertEqual(unmapped_github_token(sample), ["- name: Push"])
        mapped = "jobs:\n  j:\n    steps:\n      - name: Push\n        env:\n          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n        run: echo ${GITHUB_TOKEN}\n"
        self.assertEqual(unmapped_github_token(mapped), [])

    def test_live_workflows_map_github_token_on_the_step(self):
        root = ROOT / ".github" / "workflows"
        names = sorted(path.name for path in root.glob("*.yml"))
        self.assertIn("heartbeat.yml", names)
        for path in sorted(root.glob("*.yml")):
            bad = unmapped_github_token(path.read_text(encoding="utf-8"))
            self.assertEqual(bad, [], "%s: %s" % (path.name, bad))


def _step_named(text, name):
    for step in _workflow_steps(text):
        if step.lstrip().startswith("- name: %s" % name):
            return step
    raise AssertionError("missing step %s" % name)


def _guard_env(workspace):
    """Wallet checks still run. The digest is synthetic and matches nothing here."""
    env = os.environ.copy()
    for key in (
        "PRIVACY_GUARD_PATTERNS",
        "PRIVACY_IDENTITY_KEY",
        "PRIVACY_IDENTITY_REQUIRE",
        "SIGHTLINE_PRIVATE_TOKEN",
        "SIGHTLINE_PRIVATE_REPO",
        "RUGGER_ID_PEPPER",
        "GITHUB_ACTIONS",
    ):
        env.pop(key, None)
    env["PRIVACY_IDENTITY_HASHES"] = hashlib.sha256(b"zzsynthetic zzphrase").hexdigest() + "\n"
    env["GITHUB_WORKSPACE"] = str(workspace)
    env["GIT_AUTHOR_NAME"] = "sightline-races"
    env["GIT_AUTHOR_EMAIL"] = "41898282+github-actions[bot]@users.noreply.github.com"
    env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
    env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]
    return env


def _bash(script, cwd, env):
    return subprocess.run(
        ["bash", "-c", script],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
    )


class EmptyBranchGuard(unittest.TestCase):
    """First races-data checkout: stage the slot file, then scan it."""

    def _scripts(self):
        text = (ROOT / ".github" / "workflows" / "races.yml").read_text(encoding="utf-8")
        guard = _run_script(_step_named(text, "Privacy guard"))
        commit = _run_script(_step_named(text, "Commit to races-data only"))
        commit_step = _step_named(text, "Commit to races-data only")
        return guard, commit, commit_step

    def test_workflow_stages_the_first_tree_before_the_guard(self):
        guard, commit, commit_step = self._scripts()
        self.assertLess(guard.index('git ls-files'), guard.index("privacy-guard.sh"))
        self.assertIn('if [ -z "$(git ls-files)" ]', guard)
        self.assertIn("no tracked files yet", guard)
        add_at = commit.index("git add -A")
        guard_at = commit.index("privacy-guard.sh")
        commit_at = commit.index("git commit")
        self.assertLess(add_at, guard_at)
        self.assertLess(guard_at, commit_at)
        for key in (
            "PRIVACY_GUARD_PATTERNS",
            "PRIVACY_IDENTITY_HASHES",
            "SIGHTLINE_PRIVATE_TOKEN",
            "RUGGER_ID_PEPPER",
        ):
            self.assertIn("secrets.%s" % key, commit_step)

    def test_empty_branch_dry_run_guards_after_stage(self):
        guard_script, commit_script, _step = self._scripts()
        prefix = commit_script.split("git commit", 1)[0]
        self.assertNotIn("git push", prefix)
        self.assertNotIn("git commit", prefix)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            data = workspace / "data"
            data.mkdir()
            os.symlink(ROOT, workspace / "code")
            env = _guard_env(workspace)
            subprocess.check_call(["git", "init", "-q", "-b", "races-data"], cwd=data)
            (data / "state.json").write_text('{"slot":"2026-11-14T13:42:00Z"}\n', encoding="utf-8")

            direct = _bash(
                'bash "$GITHUB_WORKSPACE/code/scripts/privacy-guard.sh" --root "$GITHUB_WORKSPACE/data"',
                data,
                env,
            )
            self.assertNotEqual(direct.returncode, 0, direct.stderr)
            self.assertIn("no tracked files found under --root", direct.stderr)

            early = _bash(guard_script, data, env)
            self.assertEqual(early.returncode, 0, early.stderr)
            self.assertIn("no tracked files yet", early.stdout)

            staged = _bash(prefix, data, env)
            self.assertEqual(staged.returncode, 0, staged.stderr)
            self.assertIn("privacy-guard: OK", staged.stdout)
            listed = subprocess.check_output(["git", "diff", "--cached", "--name-only"], cwd=data, text=True)
            self.assertIn("state.json", listed)
            self.assertIn("NOTES.md", listed)
            head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=data, capture_output=True)
            self.assertNotEqual(head.returncode, 0)

    def test_staged_leak_still_fails_closed(self):
        _guard_script, commit_script, _step = self._scripts()
        prefix = commit_script.split("git commit", 1)[0]
        stake = "stake1" + ("u" * 53)
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            data = workspace / "data"
            data.mkdir()
            os.symlink(ROOT, workspace / "code")
            env = _guard_env(workspace)
            subprocess.check_call(["git", "init", "-q", "-b", "races-data"], cwd=data)
            (data / "state.json").write_text(stake + "\n", encoding="utf-8")
            leaked = _bash(prefix, data, env)
            self.assertNotEqual(leaked.returncode, 0, leaked.stdout)
            self.assertIn("match a private pattern", leaked.stderr)
            self.assertIn("not committing or pushing", leaked.stdout)
            self.assertNotIn(stake, leaked.stdout + leaked.stderr)
            listed = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                cwd=data, capture_output=True, text=True, check=True,
            )
            self.assertEqual(listed.stdout.strip(), "")
            head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=data, capture_output=True)
            self.assertNotEqual(head.returncode, 0)

    def test_staged_tree_without_digests_still_fails_closed(self):
        _guard_script, commit_script, _step = self._scripts()
        prefix = commit_script.split("git commit", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            data = workspace / "data"
            data.mkdir()
            os.symlink(ROOT, workspace / "code")
            env = _guard_env(workspace)
            env["PRIVACY_IDENTITY_HASHES"] = "\n"
            subprocess.check_call(["git", "init", "-q", "-b", "races-data"], cwd=data)
            (data / "state.json").write_text('{"slot":"2026-11-14T13:42:00Z"}\n', encoding="utf-8")
            blocked = _bash(prefix, data, env)
            self.assertNotEqual(blocked.returncode, 0, blocked.stdout)
            self.assertIn("identity hash list is empty (PRIVACY_IDENTITY_HASHES)", blocked.stderr)
            self.assertNotIn("privacy-guard: OK", blocked.stdout)
