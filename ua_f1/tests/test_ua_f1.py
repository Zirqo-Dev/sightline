"""UA-F1 gates: universe, dry-run with no network, slots, hosts, judgment."""

from __future__ import annotations

import datetime
import io
import json
import re
import socket
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import ua_f1.ua_f1_weekly as weekly


ROOT = Path(weekly.__file__).resolve().parent
REPO = ROOT.parent


class _Restore(unittest.TestCase):
    def setUp(self):
        self._network = weekly.NETWORK
        self._data = weekly.DATA

    def tearDown(self):
        weekly.NETWORK = self._network
        weekly.set_data_dir(self._data)


class TestUniverse(_Restore):
    def test_loaded_universe_equals_manifest_included(self):
        uni = weekly.load_universe()
        addendum = json.loads((ROOT / "snapshots" / "S0" / "manifest_addendum1.json").read_text())
        lock = json.loads((ROOT / "snapshots" / "S0" / "manifest.json").read_text())
        included = addendum["included"]["protocols"]
        self.assertEqual(addendum["included"]["n"], 30)
        self.assertEqual(len(included), 30)
        self.assertEqual(uni, included)
        self.assertEqual(len(uni), 30)
        self.assertIn("helium-network", uni)
        self.assertNotIn("solana", uni)
        self.assertNotIn("uxd", uni)
        # The lock-time manifest is the file before the addendum. The addendum wins.
        self.assertEqual(lock["included"]["n"], 29)
        self.assertNotIn("helium-network", lock["included"]["protocols"])
        self.assertNotEqual(set(uni), set(lock["included"]["protocols"]))

    def test_later_manifest_overrides_and_solana_stays_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            s0 = root / "S0"
            s0.mkdir()
            (s0 / "manifest.json").write_text(json.dumps({
                "included": {"n": 1, "protocols": ["arcium"]},
            }))
            (s0 / "manifest_addendum1.json").write_text(json.dumps({
                "included": {"n": 2, "protocols": ["arcium", "helium-network"]},
            }))
            self.assertEqual(weekly.load_universe(root, root), ["arcium", "helium-network"])
            (s0 / "manifest_addendum2.json").write_text(json.dumps({
                "included": {"protocols": ["jito", "helium-network", "solana"]},
            }))
            self.assertEqual(
                weekly.load_universe(root, root),
                ["jito", "helium-network"],
            )
            s1 = root / "S1"
            s1.mkdir()
            (s1 / "manifest.json").write_text(json.dumps({
                "included": {"n": 1, "protocols": ["solana"]},
            }))
            self.assertEqual(weekly.load_universe(root, root), [])

    def test_no_hardcoded_exclusion_tuple(self):
        src = (ROOT / "ua_f1_weekly.py").read_text()
        self.assertNotIn('"uxd", "solana", "helium-network"', src)
        self.assertNotIn("'uxd', 'solana', 'helium-network'", src)
        self.assertIn("SOLANA_EXCLUSION = \"solana\"", src)


class TestSlots(_Restore):
    def test_late_run_targets_monday_0100_and_catchup_is_idempotent(self):
        nov16 = datetime.datetime(2026, 11, 16, 1, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(weekly.scheduled_slot(nov16), nov16)
        self.assertEqual(weekly.slot_stamp(weekly.first_scheduled_slot()), "2026-11-16T01:00:00Z")
        late = datetime.datetime(2026, 11, 17, 12, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(weekly.slot_stamp(weekly.scheduled_slot(late)), "2026-11-16T01:00:00Z")

        nov23 = datetime.datetime(2026, 11, 23, 2, 5, tzinfo=datetime.timezone.utc)
        plan = weekly.plan_slots(nov23, None)
        self.assertEqual(plan["scheduled_slot"], "2026-11-23T01:00:00Z")
        self.assertEqual(plan["actual_start"], "2026-11-23T02:05:00Z")
        self.assertEqual(plan["missed_slots"], ["2026-11-16T01:00:00Z"])
        self.assertTrue(plan["current_due"])
        self.assertFalse(plan["already_filled"])

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "run_log.jsonl"
            log.write_text(
                '{"event":"weekly_slot_filled","scheduled_slot":"2026-11-16T01:00:00Z"}\n'
                '{"event":"weekly_slot_filled","scheduled_slot":"2026-11-23T01:00:00Z"}\n'
            )
            again = weekly.plan_slots(nov23, log)
        self.assertEqual(again["missed_slots"], [])
        self.assertTrue(again["already_filled"])
        self.assertFalse(again["current_due"])

        early = datetime.datetime(2026, 11, 12, 15, 0, tzinfo=datetime.timezone.utc)
        pre = weekly.plan_slots(early, None)
        self.assertEqual(pre["scheduled_slot"], "2026-11-09T01:00:00Z")
        self.assertEqual(pre["missed_slots"], [])
        self.assertFalse(pre["current_due"])

        almost = datetime.datetime(2026, 11, 23, 0, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(weekly.slot_stamp(weekly.scheduled_slot(almost)), "2026-11-16T01:00:00Z")


class TestHosts(_Restore):
    def test_allow_list_is_the_three_keyless_hosts(self):
        self.assertEqual(
            weekly.ALLOWED_HOSTS,
            {
                "defillama-datasets.llama.fi",
                "api.geckoterminal.com",
                "data.binance.vision",
            },
        )
        self.assertTrue(weekly.host_allowed("defillama-datasets.llama.fi"))
        self.assertTrue(weekly.host_allowed("api.geckoterminal.com"))
        self.assertTrue(weekly.host_allowed("data.binance.vision"))
        self.assertFalse(weekly.host_allowed("api.llama.fi"))
        self.assertFalse(weekly.host_allowed("api.coingecko.com"))
        self.assertFalse(weekly.host_allowed("pro-api.coingecko.com"))
        weekly.NETWORK = True

        def boom(*_args, **_kwargs):
            raise AssertionError("network call")

        with mock.patch.object(weekly._OPENER, "open", boom):
            body, meta = weekly.http_get("https://api.llama.fi/emissions/jupiter")
            self.assertIsNone(body)
            self.assertFalse(meta["ok"])
            body, meta = weekly.http_get("https://api.coingecko.com/api/v3/coins/helium")
            self.assertIsNone(body)
            self.assertFalse(meta["ok"])
            body, meta = weekly.http_get("http://data.binance.vision/data/spot/daily/klines/SOLUSDT/1d/x.zip")
            self.assertIsNone(body)
            self.assertEqual(meta["error"], "https required")


class TestRunner(_Restore):
    def _tree_stamp(self) -> dict:
        stamp = {}
        for path in ROOT.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            stamp[path.relative_to(ROOT).as_posix()] = path.read_bytes()
        return stamp

    def test_dry_run_makes_no_network_calls_and_writes_nothing(self):
        before = self._tree_stamp()

        def boom(*_args, **_kwargs):
            raise AssertionError("network call")

        with mock.patch.object(weekly._OPENER, "open", boom), \
             mock.patch.object(socket, "create_connection", boom):
            rc = weekly.main(["--dry-run"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._tree_stamp(), before)

    def test_december_dry_run_does_not_fetch(self):
        def boom(*_args, **_kwargs):
            raise AssertionError("network call")

        dec = datetime.datetime(2026, 12, 2, 3, 0, tzinfo=datetime.timezone.utc)
        with mock.patch.object(weekly._OPENER, "open", boom), \
             mock.patch.object(weekly, "current_utc", lambda: dec):
            sid = weekly.take_monthly_snapshot(dry_run=True, now=dec)
        self.assertEqual(sid, "S1")

    def test_first_of_month_trigger_takes_snapshot_and_sha(self):
        started = datetime.datetime(2026, 12, 1, 0, 4, 30, tzinfo=datetime.timezone.utc)
        calls = []

        def fake_http(url, dest=None, timeout=60, retries=4):
            calls.append(url)
            if url.endswith("emissionsProtocolsList"):
                body = b'["fixture"]'
            else:
                body = b'{"name":"fixture","documentedData":{}}'
            if dest is not None:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(body)
            return body, {"url": url, "ok": True, "status": 200, "bytes": len(body)}

        before = self._tree_stamp()
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            with mock.patch.object(weekly, "current_utc", lambda: started), \
                 mock.patch.object(weekly, "http_get", fake_http):
                rc = weekly.main(["--monthly-snapshot", "--data-dir", str(data)])
            self.assertEqual(rc, 0)
            manifest = json.loads((data / "snapshots" / "S1" / "manifest.json").read_text())
            self.assertEqual(manifest["snapshot_id"], "S1")
            self.assertEqual(manifest["taken_at"], "2026-12-01T00:04:30Z")
            self.assertNotEqual(manifest["taken_at"], "2026-12-01T00:00:00Z")
            sha = data / "snapshots" / "S1.sha256"
            self.assertTrue(sha.is_file())
            proc = subprocess.run(
                ["sha256sum", "-c", "S1.sha256"],
                cwd=data / "snapshots",
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertNotIn("FAILED", proc.stdout)
            note = (data / "deviations.md").read_text()
            self.assertIn("2026-12-01T00:00:00Z", note)
            self.assertIn("2026-12-01T00:04:30Z", note)
            self.assertIn("270s late", note)
            self.assertNotIn("\n# UA-F1 deviations\n\n# UA-F1 deviations\n", note)
            log = (data / "run_log.jsonl").read_text()
            self.assertIn('"actual_start": "2026-12-01T00:04:30Z"', log)
            self.assertFalse((data / "episodes.jsonl").exists())
            self.assertTrue(calls)
            self.assertTrue(all("defillama-datasets.llama.fi" in url for url in calls))
            self.assertFalse(any("geckoterminal" in url or "binance" in url for url in calls))
        self.assertEqual(self._tree_stamp(), before)

    def test_monday_trigger_takes_no_snapshot_when_none_is_due(self):
        # 7 Dec 2026 is a Monday, six days after S1's due time.
        monday = datetime.datetime(2026, 12, 7, 1, 2, tzinfo=datetime.timezone.utc)
        self.assertEqual(monday.weekday(), 0)
        self.assertFalse(weekly.weekly_monthly_snapshot_due(monday))

        def boom(*_args, **_kwargs):
            raise AssertionError("monthly snapshot is not due")

        before = self._tree_stamp()
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with mock.patch.object(weekly, "current_utc", lambda: monday), \
                 mock.patch.object(weekly, "take_monthly_snapshot", boom), \
                 redirect_stdout(buf):
                rc = weekly.main(["--dry-run", "--data-dir", tmp])
            self.assertEqual(rc, 0)
            self.assertIn("no monthly snapshot is due on this weekly run", buf.getvalue())
            self.assertNotIn("would take monthly snapshot", buf.getvalue())
            self.assertFalse((Path(tmp) / "snapshots").exists())
        self.assertEqual(self._tree_stamp(), before)

    def test_judge_refuses_before_30_usable(self):
        before = self._tree_stamp()

        def boom(*_args, **_kwargs):
            raise AssertionError("network call")

        with mock.patch.object(weekly._OPENER, "open", boom), \
             mock.patch.object(socket, "create_connection", boom):
            rc = weekly.main(["--judge", "--dry-run"])
        self.assertEqual(rc, 2)
        self.assertEqual(self._tree_stamp(), before)

    def test_status_is_counts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            weekly.set_data_dir(Path(tmp))
            rec = weekly.write_status("RUNNING")
        self.assertEqual(set(rec["counts"]), {
            "registered",
            "resolved",
            "usable",
            "BLOCKED",
            "unusable",
            "open",
            "resolved_pending_cache",
        })
        for key in ("mean", "t", "win_rate", "p_value", "gross"):
            self.assertNotIn(key, rec)
        self.assertEqual(rec["counts"]["registered"], 0)

    def test_twin_rng_is_numpy_default_rng(self):
        import numpy
        for index in (0, 1, 7):
            got = weekly.load_numpy_rng(index)
            expect = numpy.random.default_rng(20261125 + index)
            self.assertEqual(int(got.integers(0, 10_000)), int(expect.integers(0, 10_000)))

    def test_workflow_gates(self):
        text = (REPO / ".github" / "workflows" / "ua-f1.yml").read_text()
        self.assertIn('cron: "0 1 * * 1"', text)
        self.assertIn('cron: "0 0 1 * *"', text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: write", text)
        self.assertIn("--monthly-snapshot", text)
        self.assertIn("pull_request:", text)
        self.assertIn("vars.UA_F1_ENABLED != 'false'", text)
        self.assertIn('numpy==2.5.4', text)
        self.assertIn("sha256sum -c prereg_ua_f1.sha256", text)
        self.assertIn("sha256sum -c S0.sha256", text)
        self.assertIn("sha256sum -c S0_addendum1.sha256", text)
        self.assertIn("ua-f1-data", text)
        self.assertIn("privacy-guard.sh", text)
        self.assertIn("main|master|desk-data", text)
        self.assertNotIn("git push origin HEAD:main", text)
        self.assertNotIn("git push origin main", text)

    def test_steps_that_reference_github_token_map_it(self):
        text = (REPO / ".github" / "workflows" / "ua-f1.yml").read_text()
        blocks = _workflow_steps(text)
        used = []
        for block in blocks:
            if not re.search(r"\$\{GITHUB_TOKEN\}|(?<!\{)\$GITHUB_TOKEN\b", block):
                continue
            used.append(block)
            self.assertRegex(
                block,
                r"(?m)^[ ]*GITHUB_TOKEN:\s*\$\{\{\s*secrets\.GITHUB_TOKEN\s*\}\}\s*$",
            )
        self.assertTrue(used, "the push step should reference GITHUB_TOKEN")


def _workflow_steps(text: str) -> list[str]:
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(r"^      - ", line)]
    blocks = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(lines)
        chunk = []
        for line in lines[start:end]:
            if chunk and re.match(r"^ {0,5}\S", line):
                break
            chunk.append(line)
        blocks.append("\n".join(chunk))
    return blocks


if __name__ == "__main__":
    unittest.main()
