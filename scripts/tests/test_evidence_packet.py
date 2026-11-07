"""Evidence packet. Offline. Missing inputs stay unknown, not healthy."""
import calendar
import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
AS_OF = "2026-11-02T13:30:00Z"
AT = "2026-11-02T13:00:00Z"
RUN = "20261102T1300"
SHA = "a" * 40


def load_mod(name, rel):
    path = os.path.join(ROOT, rel)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


packet = load_mod("evidence_packet", "scripts/evidence/packet.py")
fetch = load_mod("evidence_fetch", "scripts/evidence/fetch_actions.py")


def epoch(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def mint(i):
    chars = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    x = i + 1
    head = ""
    for _ in range(4):
        head += chars[x % len(chars)]
        x //= len(chars)
    return head + ("1" * 28)


def pos_id(book, run_id, token):
    return "%s-%s-%s%s" % (book, run_id, token[:4], token[-4:])


def trade_row(when, book, action, frac, pnl, token, symbol):
    return {
        "time": str(when), "book": book, "agent": "EXIT" if action == "sell" else "EXECUTE",
        "mint": token, "symbol": symbol, "action": action, "frac": str(frac),
        "sol": "0.5", "quote_px": "1", "impact_pct": "", "fee_sol": "0.00001",
        "haircut_pct": "0.6", "why": "hard_stop" if action == "sell" else "pass",
        "pnl": "" if pnl is None else str(pnl), "check_delay_s": "0", "stale": "0",
    }


def task_row(when, run_id, skip="", fail=""):
    return {
        "time": str(when), "run_id": run_id, "agent": "scanner", "task": "scan",
        "mint": "", "book": "", "result": "skip" if skip else "pass",
        "skip_type": skip, "reason": "", "source": "profile", "check_delay_s": "0",
        "cfg_version": "v1-v6.5", "seed": "1", "error_src": "", "fail_reason": fail,
    }


def snapshot(conclusion="success", run_id=100, extra=None):
    run = {
        "id": run_id,
        "conclusion": conclusion,
        "status": "completed",
        "head_sha": SHA,
        "event": "workflow_dispatch",
        "html_url": "https://evil.example/secret",
        "created_at": AT,
        "updated_at": AT,
        "run_started_at": AT,
        "run_duration_ms": 61000,
        "billable_ubuntu_total_ms": 62000,
        "failed": conclusion in fetch.FAILED,
    }
    if extra:
        run.update(extra)
    workflows = {}
    for path in packet.WORKFLOW_PATHS:
        workflows[path] = {
            "error": None,
            "health_resolved": True,
            "run": dict(run),
            "latest": dict(run),
        }
    body = {
        "schema_version": 1,
        "kind": "sightline_actions_snapshot",
        "fetched": True,
        "error": None,
        "repo": "example/sightline",
        "workflows": workflows,
        "billing": fetch.blank_billing(),
    }
    return body


class EvidencePacketTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.data, True)
        self.addCleanup(shutil.rmtree, self.out, True)

    def write(self, rel, text):
        path = os.path.join(self.data, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def closes(self, n, pnl="-0.1", book="F"):
        rows = []
        closed = []
        base = epoch(AT)
        for i in range(n):
            token = mint(i)
            opened = base + i
            done = opened + 60
            rows.append(trade_row(opened, book, "buy", 1, None, token, "AAA"))
            rows.append(trade_row(done, book, "sell", 1, pnl, token, "AAA"))
            closed.append({
                "id": pos_id(book, RUN, token),
                "mint": token,
                "opened": opened,
                "closed": done,
                "pnl": float(pnl),
            })
        return rows, closed

    def plant(self, n=1, pnl="-0.1", rate=0.0, void_day=False, ai=False, tasks=None, pre_at=AT, daily_date="2026-11-02", health_run=RUN, health_times=None, notes=""):
        rows, closed = self.closes(n, pnl)
        header = list(trade_row(0, "F", "buy", 1, None, mint(0), "AAA"))
        path = os.path.join(self.data, "solana-desk", "trades.csv")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=header)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        positions = {
            "last_run_ts": epoch(AT),
            "books": {
                "F": {"open": {}, "closed": closed},
                "R": {"open": {}, "closed": []},
                "S": {"open": {}, "closed": []},
            },
        }
        self.write("solana-desk/positions.json", json.dumps(positions))
        times = health_times if health_times is not None else [AT]
        health = {
            "day": "2026-11-02", "loop_ran": True, "void_day": void_day, "ai": ai,
            "frozen": True, "mode": "fill", "last_run_utc": health_run,
            "run_times_utc": times, "data_failure_rate": rate, "notes": [notes] if notes else [],
            "cfg_version": "v1-v6.5",
        }
        self.write("solana-desk/health.json", json.dumps(health))
        task_rows = tasks if tasks is not None else [task_row(epoch(AT), RUN)]
        task_path = os.path.join(self.data, "solana-desk", "tasks.csv")
        with open(task_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(task_rows[0]))
            writer.writeheader()
            for row in task_rows:
                writer.writerow(row)
        self.write("solada-prefilter/last-run.json", json.dumps({
            "at": pre_at, "dry_run": False, "baseline": False,
            "launches": 3, "new_launches": 0, "candidates": 1, "flags_new": 0,
            "data_failures": {"koios": 1},
            "thresholds": {"min_holders": 120, "min_ada_side_k": 25000.0},
            "triggers": [{"name": "@" + "zzpacketpoison"}],
        }))
        self.write("reports/status/daily_status_latest.json", json.dumps({
            "schema_version": 1, "kind": "sightline_daily_status", "date": daily_date,
            "as_of": {"value": AT, "source": "fixture", "reason": None},
        }))
        token = mint(0)
        self.write("solada/forensics/index.jsonl", json.dumps({
            "mint": token, "symbol": "QBITS", "run_at": AT, "partial": False,
            "history_too_deep": False, "flags": ["CLUSTERED"],
        }) + "\n")
        return token

    def build(self, as_of=AS_OF, previous=None, snap=None):
        snap_path = None
        if snap is not None:
            snap_path = os.path.join(self.out, "snap.json")
            with open(snap_path, "w", encoding="utf-8") as fh:
                json.dump(snap, fh)
        return packet.build(
            ROOT, self.data, as_of, previous, snap_path, repo_slug="example/sightline",
        )

    def save(self, doc):
        path = os.path.join(self.out, "previous.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(packet.dumps(doc))
        return path

    def source(self, doc, source_id):
        for row in doc["sources"]:
            if row["id"] == source_id:
                return row
        self.fail(source_id)

    def test_contract_and_determinism(self):
        token = self.plant()
        snap = snapshot()
        first = self.build(snap=snap)
        second = self.build(snap=snap)
        self.assertEqual(packet.dumps(first), packet.dumps(second))
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["kind"], "sightline_evidence_packet")
        self.assertFalse(first["coverage"]["full"])
        self.assertFalse(first["ai"])
        self.assertFalse(first["actions_usage"]["equated_to_billed_minutes"])
        self.assertFalse(first["actions_usage"]["quota_remaining_known"])
        self.assertIn("deferred", first["actions_usage"]["note"])
        self.assertEqual(first["coverage"]["deferred"], ["forensics_detail", "billing_account"])
        self.assertTrue(first["recompute"]["failed_runs"]["actions_known"])
        self.assertFalse(first["delta"]["previous_present"])
        self.assertEqual(first["delta"]["previous_reason"], "no previous packet")
        text = packet.dumps(first)
        self.assertNotIn(token, text)
        self.assertNotIn(self.data, text)
        self.assertNotIn("zzpacketpoison", text)
        self.assertNotIn("evil.example", text)
        self.assertIn("does not estimate a token-saving percentage", text)
        self.assertTrue(any("provisional" in line for line in first["limitations"]))
        self.assertTrue(any("not a trading instruction" in line for line in first["limitations"]))
        schema_path = os.path.join(ROOT, "scripts", "evidence", "packet.schema.json")
        import jsonschema
        with open(schema_path, encoding="utf-8") as fh:
            schema = json.load(fh)
        jsonschema.Draft202012Validator(schema).validate(first)
        scan = self.source(first, "solana_scan")
        self.assertEqual(scan["state"], "ok")
        self.assertEqual(scan["desk_run_id"], RUN)
        self.assertEqual(first["recompute"]["closes"]["n"], 1)
        self.assertTrue(first["recompute"]["closes"]["ok"])
        self.assertEqual(first["recompute"]["verdict_inputs"]["finality"], "not_issued")
        self.assertEqual(first["recompute"]["verdict_inputs"]["rule_result"], "CONTINUE")
        self.assertFalse(first["recompute"]["threshold_lines"]["f_rule_would_stop"])
        fore = self.source(first, "forensics")
        self.assertEqual(fore["freshness"]["status"], "unsupported")
        self.assertNotEqual(fore["state"], "ok")
        guard = self.source(first, "workflow:.github/workflows/desk-data-guard.yml")
        self.assertEqual(guard["state"], "ok")
        self.assertEqual(guard["links"][0]["url"], "https://github.com/example/sightline/actions/runs/100")
        self.assertTrue(self.source(first, "forensics")["fields"]["deferred"])
        self.assertEqual(first["delta"]["predicates"]["prefilter_trading_verdict"]["status"], "not_implemented")
        self.assertEqual(first["delta"]["predicates"]["new_close_id"]["status"], "unknown")
        self.assertIsNone(first["delta"]["material"])

    def test_no_change_and_timestamp_only(self):
        self.plant()
        snap = snapshot()
        prev = self.save(self.build(snap=snap))
        again = self.build(previous=prev, snap=snap)
        again2 = self.build(previous=prev, snap=snap)
        self.assertEqual(packet.dumps(again), packet.dumps(again2))
        self.assertFalse(again["delta"]["material"])
        self.assertFalse(again["delta"]["timestamp_only"])
        self.assertEqual(again["delta"]["dropped"]["status"], "none")
        for name in ("new_close_id", "new_fill_id", "health_flip", "new_failed_run_id", "board_threshold_crossing", "material_verdict_inputs"):
            self.assertEqual(again["delta"]["predicates"][name]["status"], "no_change", name)
        self.plant(health_run="20261102T1310", health_times=["2026-11-02T13:10:00Z"])
        shifted = self.build(previous=prev, snap=snap)
        self.assertFalse(shifted["delta"]["material"])
        self.assertTrue(shifted["delta"]["timestamp_only"])
        self.assertEqual(shifted["delta"]["predicates"]["health_flip"]["status"], "no_change")
        self.assertEqual(shifted["delta"]["predicates"]["material_verdict_inputs"]["status"], "no_change")
        self.assertEqual(shifted["delta"]["predicates"]["new_close_id"]["status"], "no_change")

    def test_new_close_fill_threshold_and_verdict_inputs(self):
        self.plant(n=19, pnl="-0.1")
        snap = snapshot()
        prev = self.save(self.build(snap=snap))
        with open(prev, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["recompute"]["verdict_inputs"]["rule_result"], "CONTINUE")
        self.plant(n=20, pnl="-0.1")
        doc = self.build(previous=prev, snap=snap)
        self.assertEqual(doc["recompute"]["closes"]["n"], 20)
        self.assertEqual(doc["recompute"]["verdict_inputs"]["rule_result"], "STOP")
        self.assertEqual(doc["delta"]["predicates"]["new_close_id"]["status"], "change")
        self.assertEqual(doc["delta"]["predicates"]["new_fill_id"]["status"], "change")
        self.assertEqual(doc["delta"]["predicates"]["board_threshold_crossing"]["status"], "change")
        self.assertIn("f_rule_would_stop", doc["delta"]["predicates"]["board_threshold_crossing"]["lines"])
        self.assertEqual(doc["delta"]["predicates"]["material_verdict_inputs"]["status"], "change")
        self.assertIn("n_f_counted", doc["delta"]["predicates"]["material_verdict_inputs"]["fields"])
        self.assertTrue(doc["delta"]["material"])

    def test_pnl_change_is_material_without_a_new_id(self):
        self.plant(n=1, pnl="-0.1")
        snap = snapshot()
        prev = self.save(self.build(snap=snap))
        self.plant(n=1, pnl="-0.2")
        doc = self.build(previous=prev, snap=snap)
        self.assertEqual(doc["delta"]["predicates"]["new_close_id"]["status"], "no_change")
        self.assertEqual(doc["delta"]["predicates"]["material_verdict_inputs"]["status"], "change")
        self.assertTrue(doc["delta"]["material"])
        self.assertFalse(doc["delta"]["timestamp_only"])

    def test_health_flip_and_new_failed_run(self):
        self.plant(ai=False)
        snap = snapshot()
        prev = self.save(self.build(snap=snap))
        self.plant(ai=True)
        flipped = self.build(previous=prev, snap=snap)
        self.assertEqual(flipped["delta"]["predicates"]["health_flip"]["status"], "change")
        self.assertEqual(flipped["delta"]["predicates"]["health_flip"]["fields"], ["ai"])
        self.assertEqual(flipped["delta"]["predicates"]["board_threshold_crossing"]["status"], "no_change")
        failed_tasks = [
            task_row(epoch(AT), RUN),
            task_row(epoch(AT) + 60, "20261102T1400", skip="data"),
        ]
        self.plant(tasks=failed_tasks)
        failed = self.build(previous=prev, snap=snap)
        self.assertEqual(failed["delta"]["predicates"]["new_failed_run_id"]["status"], "change")
        self.assertIn("desk:20261102T1400", failed["delta"]["predicates"]["new_failed_run_id"]["ids"])

    def test_dropped_record_recount(self):
        self.plant(n=2)
        snap = snapshot()
        prev_doc = self.build(snap=snap)
        self.assertEqual(prev_doc["recompute"]["closes"]["n"], 2)
        prev = self.save(prev_doc)
        kept = pos_id("F", RUN, mint(0))
        dropped = pos_id("F", RUN, mint(1))
        self.plant(n=1)
        doc = self.build(previous=prev, snap=snap)
        self.assertEqual(doc["recompute"]["closes"]["n"], 1)
        self.assertEqual(doc["recompute"]["closes"]["ids"], [kept])
        self.assertEqual(doc["delta"]["dropped"]["status"], "found")
        self.assertEqual(doc["delta"]["dropped"]["close_ids"], [dropped])
        self.assertTrue(doc["delta"]["material"])

    def test_missing_stale_and_source_failure(self):
        self.plant()
        os.remove(os.path.join(self.data, "solana-desk", "health.json"))
        partial = self.build(snap=snapshot())
        self.assertEqual(self.source(partial, "solana_scan")["state"], "partial")
        self.assertIsNone(partial["recompute"]["health_flags"])
        for rel in ("solana-desk/trades.csv", "solana-desk/tasks.csv", "solana-desk/positions.json"):
            os.remove(os.path.join(self.data, rel))
        missing = self.build(snap=snapshot())
        self.assertEqual(self.source(missing, "solana_scan")["state"], "missing")
        self.plant()
        late = self.build(as_of="2026-11-02T23:00:00Z", snap=snapshot())
        self.assertEqual(self.source(late, "solana_scan")["state"], "stale")
        self.assertEqual(self.source(late, "solada_prefilter")["state"], "stale")
        self.assertEqual(self.source(late, "daily_status")["state"], "ok")
        self.assertFalse(self.source(late, "daily_status")["freshness"]["stale"])
        self.plant(health_run="20261102T0300", health_times=["2026-11-02T03:00:00Z"], pre_at="2026-11-02T03:00:00Z", daily_date="2026-11-01")
        daily_stale = self.build(as_of="2026-11-02T03:30:00Z", snap=snapshot())
        self.assertEqual(self.source(daily_stale, "solana_scan")["state"], "ok")
        self.assertEqual(self.source(daily_stale, "daily_status")["state"], "stale")
        self.plant(health_run="20261102T0240", health_times=["2026-11-02T02:40:00Z"], pre_at="2026-11-02T02:40:00Z", daily_date="2026-11-01")
        on_grace = self.build(as_of="2026-11-02T02:40:00Z", snap=snapshot())
        self.assertTrue(self.source(on_grace, "daily_status")["freshness"]["stale"])
        self.plant(health_run="20261102T0239", health_times=["2026-11-02T02:39:59Z"], pre_at="2026-11-02T02:39:59Z", daily_date="2026-11-01")
        in_grace = self.build(as_of="2026-11-02T02:39:59Z", snap=snapshot())
        self.assertFalse(self.source(in_grace, "daily_status")["freshness"]["stale"])
        self.assertEqual(self.source(in_grace, "daily_status")["state"], "ok")
        age = epoch(AS_OF) - 9000
        at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(age))
        self.plant(pre_at=at)
        edge = self.build(snap=snapshot())
        self.assertFalse(self.source(edge, "solada_prefilter")["freshness"]["stale"])
        self.plant(pre_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch(AS_OF) - 9001)))
        over = self.build(snap=snapshot())
        self.assertTrue(self.source(over, "solada_prefilter")["freshness"]["stale"])
        self.plant()
        self.write("solana-desk/health.json", "{")
        bad = self.build(snap=snapshot())
        self.assertEqual(self.source(bad, "solana_scan")["state"], "failed")
        failed_snap = snapshot(conclusion="failure", run_id=101)
        self.plant()
        failed = self.build(snap=failed_snap)
        self.assertEqual(self.source(failed, "workflow:.github/workflows/desk-data-guard.yml")["state"], "failed")
        self.plant()
        no_clock = self.build(as_of=None, snap=snapshot())
        self.assertEqual(self.source(no_clock, "solana_scan")["state"], "unknown")
        self.assertIsNone(no_clock["as_of"]["value"])

    def _run(self, run_id, event, conclusion):
        return {
            "id": run_id,
            "conclusion": conclusion,
            "status": "completed",
            "head_sha": SHA,
            "event": event,
            "created_at": AT,
            "updated_at": AT,
            "run_started_at": AT,
            "run_duration_ms": 1000,
            "billable_ubuntu_total_ms": 0,
        }

    def test_skipped_pr_does_not_erase_scheduled_health(self):
        self.plant()
        snap = snapshot()
        snap["workflows"][".github/workflows/run-forensics.yml"] = {
            "error": None,
            "health_resolved": True,
            "run": self._run(50, "schedule", "success"),
            "latest": self._run(99, "pull_request", "skipped"),
        }
        kept = self.build(snap=snap)
        src = self.source(kept, "workflow:.github/workflows/run-forensics.yml")
        self.assertEqual(src["state"], "ok")
        self.assertEqual(src["actions_run_id"], 50)
        self.assertEqual(src["fields"]["event"], "schedule")
        self.assertEqual(src["fields"]["latest_conclusion"], "skipped")
        self.assertEqual(src["fields"]["latest_event"], "pull_request")
        self.assertEqual(src["fields"]["latest_actions_run_id"], 99)
        snap["workflows"][".github/workflows/run-forensics.yml"] = {
            "error": None,
            "health_resolved": False,
            "run": None,
            "latest": self._run(99, "pull_request", "skipped"),
        }
        unresolved = self.build(snap=snap)
        src = self.source(unresolved, "workflow:.github/workflows/run-forensics.yml")
        self.assertEqual(src["state"], "unknown")
        self.assertNotEqual(src["state"], "ok")
        self.assertIsNone(src["actions_run_id"])
        raw = [
            self._run(99, "pull_request", "skipped"),
            self._run(50, "schedule", "success"),
        ]
        raw[0]["actor"] = {"login": "zzpacketpoison"}
        latest, health, resolved = fetch.pick_runs(raw, "example/sightline")
        self.assertEqual(latest["id"], 99)
        self.assertEqual(latest["conclusion"], "skipped")
        self.assertEqual(health["id"], 50)
        self.assertTrue(resolved)
        self.assertNotIn("zzpacketpoison", json.dumps(latest))
        only_skip, no_health, no_flag = fetch.pick_runs([self._run(99, "pull_request", "skipped")], "example/sightline")
        self.assertEqual(only_skip["id"], 99)
        self.assertIsNone(no_health)
        self.assertFalse(no_flag)

    def test_unresolved_workflow_is_not_zero_failures(self):
        self.plant()
        snap = snapshot()
        prev = self.save(self.build(snap=snap))
        snap["workflows"][".github/workflows/run-forensics.yml"] = {
            "error": "gh_exit_1",
            "run": None,
            "latest": None,
            "health_resolved": False,
        }
        errored = self.build(previous=prev, snap=snap)
        self.assertFalse(errored["recompute"]["failed_runs"]["actions_known"])
        self.assertEqual(errored["delta"]["predicates"]["new_failed_run_id"]["status"], "unknown")
        self.assertNotEqual(errored["delta"]["predicates"]["new_failed_run_id"]["status"], "no_change")
        snap = snapshot()
        snap["workflows"][".github/workflows/privacy-guard.yml"] = {
            "error": None,
            "health_resolved": False,
            "run": None,
            "latest": self._run(99, "pull_request", "skipped"),
        }
        unresolved = self.build(previous=prev, snap=snap)
        self.assertEqual(self.source(unresolved, "workflow:.github/workflows/privacy-guard.yml")["state"], "unknown")
        self.assertFalse(unresolved["recompute"]["failed_runs"]["actions_known"])
        self.assertEqual(unresolved["delta"]["predicates"]["new_failed_run_id"]["status"], "unknown")

    def test_null_threshold_is_unknown(self):
        full = {
            "f_closes_meet_minimum": False,
            "f_expectancy_below_stop": False,
            "f_rule_would_stop": False,
            "data_failure_rate_above_void": False,
        }
        partial = dict(full)
        partial["f_expectancy_below_stop"] = None
        self.assertEqual(packet.compare_lines(full, full)["status"], "no_change")
        self.assertEqual(packet.compare_lines(partial, partial)["status"], "unknown")
        self.assertEqual(packet.compare_lines(full, partial)["status"], "unknown")

    def test_duplicate_r_id_is_not_ok(self):
        self.plant(n=1)
        token_a = mint(8)
        token_b = mint(9)
        opened = epoch(AT) + 100
        shared = "R-20261102T1300-22222222"
        path = os.path.join(self.data, "solana-desk", "trades.csv")
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
            fields = list(rows[0].keys())
        rows.extend([
            trade_row(opened, "R", "buy", 1, None, token_a, "AAA"),
            trade_row(opened + 60, "R", "sell", 1, "-0.1", token_a, "AAA"),
            trade_row(opened + 1, "R", "buy", 1, None, token_b, "BBB"),
            trade_row(opened + 61, "R", "sell", 1, "-0.1", token_b, "BBB"),
        ])
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        with open(os.path.join(self.data, "solana-desk", "positions.json"), encoding="utf-8") as fh:
            positions = json.load(fh)
        positions["books"]["R"]["closed"] = [
            {"id": shared, "mint": token_a, "opened": opened, "closed": opened + 60, "pnl": -0.1},
            {"id": shared, "mint": token_b, "opened": opened + 1, "closed": opened + 61, "pnl": -0.1},
        ]
        self.write("solana-desk/positions.json", json.dumps(positions))
        doc = self.build(snap=snapshot())
        closes = doc["recompute"]["closes"]
        self.assertFalse(closes["ok"])
        self.assertIsNone(closes["n"])
        self.assertEqual(closes["ids"].count(shared), 1)
        self.assertEqual(closes["reason"], "duplicate position id")
        self.assertNotEqual((closes["ok"], closes["n"], len(closes["ids"])), (True, 2, 1))

    def test_duplicate_s_id_is_not_ok(self):
        self.plant(n=1)
        token_a = mint(10)
        token_b = mint(11)
        opened = epoch(AT) + 200
        shared = "S-20261102T1300-33333333"
        path = os.path.join(self.data, "solana-desk", "trades.csv")
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
            fields = list(rows[0].keys())
        rows.extend([
            trade_row(opened, "S", "buy", 1, None, token_a, "AAA"),
            trade_row(opened + 60, "S", "sell", 1, "-0.1", token_a, "AAA"),
            trade_row(opened + 1, "S", "buy", 1, None, token_b, "BBB"),
            trade_row(opened + 61, "S", "sell", 1, "-0.1", token_b, "BBB"),
        ])
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        with open(os.path.join(self.data, "solana-desk", "positions.json"), encoding="utf-8") as fh:
            positions = json.load(fh)
        positions["books"]["S"]["closed"] = [
            {"id": shared, "mint": token_a, "opened": opened, "closed": opened + 60, "pnl": -0.1},
            {"id": shared, "mint": token_b, "opened": opened + 1, "closed": opened + 61, "pnl": -0.1},
        ]
        self.write("solana-desk/positions.json", json.dumps(positions))
        doc = self.build(snap=snapshot())
        closes = doc["recompute"]["closes"]
        self.assertFalse(closes["ok"])
        self.assertIsNone(closes["n"])
        self.assertEqual(closes["by_book"]["S"], 2)
        self.assertEqual(closes["ids"].count(shared), 1)
        self.assertEqual(closes["reason"], "duplicate position id")

    def test_headerless_tasks_are_not_zero_failures(self):
        self.plant()
        self.write("solana-desk/tasks.csv", "1790000000,not-a-header,scanner\n")
        doc = self.build(snap=snapshot())
        failed = doc["recompute"]["failed_runs"]
        self.assertFalse(failed["ok"])
        self.assertIsNone(failed["all_desk_run_n"])
        self.assertEqual(failed["desk_ids"], [])
        self.assertIn("header", failed["reason"])
        self.write(
            "solana-desk/tasks.csv",
            "time,run_id,skip_type,fail_reason\n1,not-a-run,,\n",
        )
        bad_row = self.build(snap=snapshot())
        failed = bad_row["recompute"]["failed_runs"]
        self.assertFalse(failed["ok"])
        self.assertIsNone(failed["all_desk_run_n"])
        self.assertIn("unknown", failed["reason"])

    def test_requirements_install_and_previous_packet_path(self):
        workflow_path = os.path.join(ROOT, ".github", "workflows", "evidence-packet.yml")
        with open(workflow_path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("\n  schedule:", text)
        self.assertNotIn("contents: write", text)
        self.assertLess(text.index("scripts/evidence/requirements.txt"), text.index("unittest discover"))
        self.assertIn('gh run download "$PREV" --repo "$GITHUB_REPOSITORY"', text)
        self.assertIn("date -u +%Y-%m-%dT%H:%M:%SZ", text)
        self.assertIn("--retain-replay", text)
        self.assertIn("--check-upload", text)
        self.assertIn("retention-days: 7", text)
        self.assertIn('find "$GITHUB_WORKSPACE/packet" -type f', text)
        req_path = os.path.join(ROOT, "scripts", "evidence", "requirements.txt")
        with open(req_path, encoding="utf-8") as fh:
            requirements = fh.read()
        self.assertIn("jsonschema", requirements)
        dest = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, dest, True)
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "--target", dest, "-r", req_path],
            stdout=subprocess.DEVNULL,
        )
        schema = os.path.join(ROOT, "scripts", "evidence", "packet.schema.json")
        code = (
            "import json, sys; sys.path.insert(0, %r); "
            "import jsonschema; "
            "assert jsonschema.__file__.startswith(%r); "
            "from jsonschema import Draft202012Validator; "
            "Draft202012Validator.check_schema(json.load(open(%r)))"
        ) % (dest, dest, schema)
        subprocess.check_call([sys.executable, "-I", "-c", code])
        self.plant()
        snap_path = os.path.join(self.out, "snap.json")
        with open(snap_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot(), fh)
        first_dir = tempfile.mkdtemp()
        second_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, first_dir, True)
        self.addCleanup(shutil.rmtree, second_dir, True)
        cmd = [
            sys.executable, os.path.join(ROOT, "scripts", "evidence", "packet.py"),
            "--repo", ROOT, "--data", self.data, "--as-of", AS_OF,
            "--actions-snapshot", snap_path, "--repo-slug", "example/sightline",
        ]
        subprocess.check_call(cmd + ["--out", first_dir])
        with open(os.path.join(first_dir, "evidence-packet.json"), encoding="utf-8") as fh:
            first = json.load(fh)
        self.assertFalse(first["delta"]["previous_present"])
        self.assertEqual(first["delta"]["previous_reason"], "no previous packet")
        subprocess.check_call(cmd + [
            "--previous", os.path.join(first_dir, "evidence-packet.json"),
            "--out", second_dir,
        ])
        with open(os.path.join(second_dir, "evidence-packet.json"), encoding="utf-8") as fh:
            second = json.load(fh)
        self.assertTrue(second["delta"]["previous_present"])
        self.assertEqual(second["delta"]["predicates"]["new_close_id"]["status"], "no_change")

    def _commit_tree(self, path):
        subprocess.check_call(["git", "init", "-q"], cwd=path)
        subprocess.check_call(["git", "add", "-A"], cwd=path)
        subprocess.check_call(
            ["git", "-c", "user.email=" + "replay" + "@" + "example.com", "-c", "user.name=replay",
             "commit", "-q", "-m", "pin"],
            cwd=path,
        )
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()

    def _pinned_code(self):
        parent = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, parent, True)
        dest = os.path.join(parent, "code")
        shutil.copytree(
            ROOT, dest,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "dist"),
        )
        return dest, self._commit_tree(dest)

    def test_pinned_replay_match_mismatch_and_tamper(self):
        self.plant(health_run="20261102T1400", health_times=["2026-11-02T14:00:00Z"])
        code, code_sha = self._pinned_code()
        data_sha = self._commit_tree(self.data)
        as_of = "2026-11-02T13:00:00Z"
        snap_path = os.path.join(self.out, "snap.json")
        with open(snap_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot(), fh)
        bundle = tempfile.mkdtemp()
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, bundle, True)
        self.addCleanup(shutil.rmtree, out, True)
        with self.assertRaises(packet.PacketError):
            packet.retain_replay(bundle, as_of, snap_path, None, data_sha, "example/sightline", code_repo=code)
        cmd = [
            sys.executable, os.path.join(ROOT, "scripts", "evidence", "packet.py"),
            "--repo", code, "--data", self.data, "--as-of", as_of,
            "--actions-snapshot", snap_path, "--repo-slug", "example/sightline",
            "--code-commit", code_sha, "--data-commit", data_sha,
            "--retain-replay", "--out", bundle,
        ]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL)
        with open(os.path.join(bundle, "evidence-packet.json"), "rb") as fh:
            original = fh.read()
        doc = json.loads(original)
        scan = self.source(doc, "solana_scan")
        self.assertEqual(scan["state"], "unknown")
        self.assertIn("after as-of", scan["reason"])
        rebuilt = packet.rebuild_from_replay(bundle, code, self.data, out)
        with open(rebuilt, "rb") as fh:
            self.assertEqual(fh.read(), original)
        replay_path = os.path.join(bundle, "replay", "replay.json")
        with open(replay_path, encoding="utf-8") as fh:
            replay = json.load(fh)
        replay["code_commit"] = "c" * 40
        with open(replay_path, "w", encoding="utf-8") as fh:
            json.dump(replay, fh)
        with self.assertRaises(packet.PacketError) as mismatch:
            packet.rebuild_from_replay(bundle, code, self.data, out)
        self.assertIn("does not match checkout HEAD", str(mismatch.exception))
        replay["code_commit"] = code_sha
        with open(replay_path, "w", encoding="utf-8") as fh:
            fh.write(packet.dumps(replay))
        snap_file = os.path.join(bundle, "replay", "actions-snapshot.json")
        with open(snap_file, "ab") as fh:
            fh.write(b"\n")
        with self.assertRaises(packet.PacketError) as tamper:
            packet.rebuild_from_replay(bundle, code, self.data, out)
        self.assertIn("sha256 does not match", str(tamper.exception))
        with open(os.path.join(code, "scripts", "evidence", "freshness.json"), "a", encoding="utf-8") as fh:
            fh.write("\n")
        with open(snap_path, "rb") as src, open(snap_file, "wb") as fh:
            fh.write(src.read())
        with self.assertRaises(packet.PacketError) as dirty:
            packet.rebuild_from_replay(bundle, code, self.data, out)
        self.assertIn("tracked changes", str(dirty.exception))

    def test_privacy_poison_blocks_each_uploaded_file(self):
        self.plant()
        code, code_sha = self._pinned_code()
        data_sha = self._commit_tree(self.data)
        snap_path = os.path.join(self.out, "snap.json")
        with open(snap_path, "w", encoding="utf-8") as fh:
            json.dump(snapshot(), fh)
        first = tempfile.mkdtemp()
        bundle = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, first, True)
        self.addCleanup(shutil.rmtree, bundle, True)
        subprocess.check_call([
            sys.executable, os.path.join(ROOT, "scripts", "evidence", "packet.py"),
            "--repo", code, "--data", self.data, "--as-of", AS_OF,
            "--actions-snapshot", snap_path, "--repo-slug", "example/sightline",
            "--code-commit", code_sha, "--data-commit", data_sha,
            "--retain-replay", "--out", first,
        ], stdout=subprocess.DEVNULL)
        subprocess.check_call([
            sys.executable, os.path.join(ROOT, "scripts", "evidence", "packet.py"),
            "--repo", code, "--data", self.data, "--as-of", AS_OF,
            "--actions-snapshot", snap_path, "--repo-slug", "example/sightline",
            "--code-commit", code_sha, "--data-commit", data_sha,
            "--previous", os.path.join(first, "evidence-packet.json"),
            "--retain-replay", "--out", bundle,
        ], stdout=subprocess.DEVNULL)
        names = [
            "evidence-packet.json",
            os.path.join("replay", "replay.json"),
            os.path.join("replay", "actions-snapshot.json"),
            os.path.join("replay", "previous-packet.json"),
        ]
        clean = [os.path.join(bundle, name) for name in names]
        self.assertFalse(packet.upload_blocked(clean, code))
        poison = "stake1" + ("u" * 52)
        for name in names:
            path = os.path.join(bundle, name)
            with open(path, "rb") as fh:
                saved = fh.read()
            with open(path, "ab") as fh:
                fh.write(poison.encode("ascii"))
            self.assertTrue(packet.upload_blocked([path], code), name)
            blocked = subprocess.run(
                [sys.executable, os.path.join(ROOT, "scripts", "evidence", "packet.py"),
                 "--check-upload", bundle, "--repo", code],
                capture_output=True, text=True,
            )
            self.assertEqual(blocked.returncode, 2, name)
            with open(path, "wb") as fh:
                fh.write(saved)

    def test_slug_and_snapshot_drop_secrets(self):
        token = "ghs_" + "zzpacketpoison"
        url = "https://x-access-token:%s@github.com/example/sightline.git" % token
        self.assertEqual(packet.github_slug_from_url(url), "example/sightline")
        self.assertNotIn(token, packet.github_slug_from_url(url))
        raw = {
            "id": 5,
            "conclusion": "failure",
            "status": "completed",
            "head_sha": SHA,
            "event": "schedule",
            "html_url": "https://github.com/secret-user/private/actions/runs/5",
            "created_at": AT,
            "updated_at": AT,
            "run_started_at": AT,
            "actor": {"login": "zzpacketpoison"},
            "display_title": "stake1" + ("u" * 52),
        }
        safe = fetch.safe_run(raw, "example/sightline")
        text = json.dumps(safe)
        self.assertNotIn("zzpacketpoison", text)
        self.assertNotIn("stake1", text)
        self.assertNotIn("secret-user", text)
        self.assertEqual(safe["html_url"], "https://github.com/example/sightline/actions/runs/5")
        self.assertTrue(safe["failed"])

    def test_builder_has_no_wall_clock_or_network(self):
        path = os.path.join(ROOT, "scripts", "evidence", "packet.py")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertNotIn("time.time", text)
        self.assertNotIn("datetime.now", text)
        self.assertNotIn("urlopen", text)
        self.assertNotIn("requests.", text)


if __name__ == "__main__":
    unittest.main()
