"""Data-health alert. Offline. No scan, skip, or void changes."""
import ast, calendar, csv, io, json, os, sys, tempfile, time, unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import data_health_alert as alert

MINT = "MintFail1111111111111111111111111111111111"
POISON = "poison-token-should-not-leak"
COLS = [
    "time", "run_id", "agent", "task", "mint", "book", "result", "skip_type", "reason",
    "source", "check_delay_s", "cfg_version", "seed", "error_src", "fail_reason",
]


def ts(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ"))


def run(checks, fails, sources=None, run_id="20261031T1200", reasons=None):
    src = {name: 0 for name in alert.SOURCES}
    if sources:
        src.update(sources)
    return {
        "run_id": run_id, "time": 0, "checks": checks, "data_fails": fails,
        "sources": src, "reasons": reasons or {},
    }


def rec(when, run_id, skip="", fail="", mint=MINT):
    return {
        "time": str(when), "run_id": run_id, "agent": "scanner", "task": "scan",
        "mint": mint, "book": "", "result": "skip" if skip else "pass",
        "skip_type": skip, "reason": "missing_data" if skip == "data" else "",
        "source": "profile", "check_delay_s": "0.1", "cfg_version": "v1-v6.5",
        "seed": "1", "error_src": "", "fail_reason": fail,
    }


def write_desk(health, records, cols=None):
    directory = tempfile.mkdtemp()
    with open(os.path.join(directory, "health.json"), "w", encoding="utf-8") as fh:
        json.dump(health, fh)
    with open(os.path.join(directory, "tasks.csv"), "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols or COLS, extrasaction="ignore")
        writer.writeheader()
        for row in records:
            writer.writerow(row)
    return directory


def health(last="20261031T1230", times=None, **extra):
    base = {
        "day": "2026-10-31",
        "last_run_utc": last,
        "run_times_utc": times or ["2026-10-31T12:00:00Z", "2026-10-31T12:30:00Z"],
        "notes": [POISON, MINT],
        "data_fail_by_reason": {MINT: 9},
    }
    base.update(extra)
    return base


class Hub:
    def __init__(self, issues=None, labels=None):
        self.labels = list(labels or [])
        self.issues = [dict(item) for item in (issues or [])]
        self.created = []
        self.comments = []
        self.closed = []
        self.ensured = False

    def open_issues(self):
        opened = [item for item in self.issues if item["state"] == "open"]
        opened.sort(key=lambda item: item["number"])
        return [{"number": item["number"]} for item in opened]

    def ensure_label(self):
        self.ensured = True
        if alert.LABEL not in self.labels:
            self.labels.append(alert.LABEL)

    def create_issue(self, title, body):
        number = 1 + max([item["number"] for item in self.issues], default=0)
        self.issues.append({"number": number, "title": title, "state": "open", "body": body})
        self.created.append((number, title, body))
        return f"https://github.com/example/sightline/issues/{number}"

    def comment(self, number, body):
        self.comments.append((number, body))

    def close_recovered(self, number):
        self.comments.append((number, alert.RECOVERED))
        self.closed.append(number)
        for item in self.issues:
            if item["number"] == number:
                item["state"] = "closed"


class JudgeTests(unittest.TestCase):
    def test_alert_when_share_is_at_least_25_percent(self):
        kind, rules, _ = alert.judge(run(4, 1), None, 0, 0, 1, 4)
        self.assertEqual(kind, "alert")
        self.assertIn("share", rules)
        kind, rules, _ = alert.judge(run(5, 1), None, 1, 20, 2, 25)
        self.assertNotIn("share", rules)

    def test_alert_when_the_same_source_fails_on_two_runs(self):
        prev = run(40, 1, {"rugcheck": 1}, "20261031T1200")
        cur = run(40, 1, {"rugcheck": 1}, "20261031T1230")
        kind, rules, repeated = alert.judge(cur, prev, 1, 40, 2, 80)
        self.assertEqual(kind, "alert")
        self.assertEqual(rules, ["source"])
        self.assertEqual(repeated, ["rugcheck"])
        self.assertLess(cur["data_fails"] / cur["checks"], 0.05)

    def test_source_streak_is_only_the_previous_run(self):
        older = run(10, 1, {"rugcheck": 1}, "20261031T1100")
        prev = run(10, 0, run_id="20261031T1200")
        cur = run(10, 1, {"rugcheck": 1}, "20261031T1230")
        kind, rules, _ = alert.judge(cur, prev, 1, 20, 2, 30)
        self.assertNotIn("source", rules)
        self.assertEqual(kind, "ok")
        self.assertEqual(older["sources"]["rugcheck"], 1)

    def test_alert_when_the_day_rate_reaches_0_10(self):
        prev = run(20, 1, {"jupiter": 1}, "20261031T1200")
        cur = run(10, 2, {"rugcheck": 2}, "20261031T1230")
        kind, rules, _ = alert.judge(cur, prev, 1, 20, 3, 30)
        self.assertEqual((kind, rules), ("alert", ["day_rate"]))

    def test_no_alert_when_the_day_rate_was_already_at_0_10(self):
        prev = run(10, 1, {"rugcheck": 1}, "20261031T1200")
        cur = run(10, 1, {"jupiter": 1}, "20261031T1230")
        kind, rules, _ = alert.judge(cur, prev, 1, 10, 2, 20)
        self.assertEqual((kind, rules), ("ok", []))

    def test_no_alert_below_every_line(self):
        prev = run(10, 0, run_id="20261031T1200")
        cur = run(10, 1, {"dexscreener": 1}, "20261031T1230")
        kind, rules, _ = alert.judge(cur, prev, 0, 10, 1, 20)
        self.assertEqual((kind, rules), ("ok", []))

    def test_recovery_needs_two_runs_under_5_percent(self):
        prev = run(20, 0, run_id="20261031T1200")
        cur = run(21, 1, run_id="20261031T1230")
        self.assertEqual(alert.judge(cur, prev, 0, 20, 1, 41)[0], "recover")
        self.assertEqual(alert.judge(run(20, 1), run(20, 0), 0, 20, 1, 40)[0], "ok")
        self.assertEqual(alert.judge(run(20, 0), None, 0, 0, 0, 20)[0], "ok")

    def test_repeated_source_alerts_instead_of_recovering(self):
        prev = run(40, 1, {"jupiter": 1}, "20261031T1200")
        cur = run(40, 1, {"jupiter": 1}, "20261031T1230")
        kind, rules, _ = alert.judge(cur, prev, 1, 40, 2, 80)
        self.assertEqual(kind, "alert")
        self.assertNotIn("recover", rules)

    def test_title_uses_the_repeated_source_and_breaks_ties(self):
        cur = run(10, 4, {"rugcheck": 2, "jupiter": 2, "dexscreener": 4})
        self.assertEqual(alert.title_source(cur, ["jupiter", "rugcheck"]), "rugcheck")
        self.assertEqual(alert.title_source(cur, []), "dexscreener")
        self.assertEqual(alert.title_source(run(4, 1), []), "checks")


class ApplyTests(unittest.TestCase):
    def _alert(self, source="rugcheck"):
        decision = alert._base("alert", ["share"])
        decision.update(
            source=source,
            run_id="20261031T1230",
            previous_run_id="20261031T1200",
            checks=4,
            data_fails=1,
            share_text="0.2500 (1/4 checks)",
            sources={"rugcheck": 1, "jupiter": 0, "dexscreener": 0},
            reason_counts={"rugcheck:timeout": 1},
            day_rate_text="0.2500 (1/4 checks)",
            day_checks=4,
            day_data_fails=1,
            day_sources={"rugcheck": 1, "jupiter": 0, "dexscreener": 0},
            day_reason_counts={"rugcheck:timeout": 1},
            previous_sources={"rugcheck": 0, "jupiter": 0, "dexscreener": 0},
            actions_run_id="4242",
            run_url="https://github.com/example/sightline/actions/runs/4242",
        )
        decision["title"] = alert.render_title(source, "2026-10-31")
        decision["body"] = alert.render_body(decision)
        return decision

    def test_alert_opens_one_issue_and_creates_the_label(self):
        hub = Hub()
        decision = self._alert()
        result = alert.apply(decision, hub)
        self.assertEqual(result["action"], "create")
        self.assertEqual(hub.labels, ["data-health"])
        self.assertEqual(len(hub.created), 1)
        number, title, body = hub.created[0]
        self.assertEqual(title, "Data health: rugcheck failing (2026-10-31)")
        self.assertIn("Run 20261031T1230", body)
        self.assertIn("Failure share: 0.2500 (1/4 checks)", body)
        self.assertIn("Sources: rugcheck 1, jupiter 0, dexscreener 0", body)
        self.assertIn("Reasons: rugcheck:timeout 1", body)
        self.assertIn("GitHub run: https://github.com/example/sightline/actions/runs/4242", body)
        self.assertNotIn(MINT, title + body)
        self.assertNotIn(POISON, title + body)
        self.assertEqual(hub.comments, [])
        self.assertEqual(number, 1)

    def test_dedupe_comments_instead_of_opening(self):
        hub = Hub(issues=[{"number": 9, "state": "open"}, {"number": 3, "state": "open"}])
        decision = self._alert()
        result = alert.apply(decision, hub)
        self.assertEqual(result, {"action": "comment", "number": 3})
        self.assertEqual(hub.created, [])
        self.assertEqual(len(hub.comments), 1)
        self.assertEqual(hub.comments[0][0], 3)
        self.assertIn("Failure share: 0.2500 (1/4 checks)", hub.comments[0][1])
        self.assertNotIn(MINT, hub.comments[0][1])
        self.assertFalse(hub.ensured)
        self.assertEqual([item["state"] for item in hub.issues], ["open", "open"])

    def test_recovery_comments_recovered_and_closes(self):
        hub = Hub(issues=[{"number": 8, "state": "open"}, {"number": 2, "state": "open"}])
        decision = alert._base("recover", ["recover"])
        result = alert.apply(decision, hub)
        self.assertEqual(result["action"], "close")
        self.assertEqual(result["numbers"], [2, 8])
        self.assertEqual(hub.comments, [(2, "recovered"), (8, "recovered")])
        self.assertEqual(hub.closed, [2, 8])
        self.assertTrue(all(item["state"] == "closed" for item in hub.issues))
        self.assertEqual(hub.created, [])

    def test_recovery_without_an_open_issue_does_nothing(self):
        hub = Hub(issues=[{"number": 2, "state": "closed"}])
        result = alert.apply(alert._base("recover", ["recover"]), hub)
        self.assertEqual(result, {"action": "none"})
        self.assertEqual(hub.comments, [])
        self.assertEqual(hub.closed, [])

    def test_no_alert_does_not_touch_issues(self):
        hub = Hub(issues=[{"number": 4, "state": "open"}])
        decision = alert._base("ok", [])
        decision["body"] = MINT
        decision["title"] = MINT
        result = alert.apply(decision, hub)
        self.assertEqual(result, {"action": "none"})
        self.assertEqual(hub.comments, [])
        self.assertEqual(hub.closed, [])
        self.assertEqual(hub.created, [])
        self.assertEqual(hub.issues[0]["state"], "open")


class FileTests(unittest.TestCase):
    def test_reads_this_run_and_drops_addresses(self):
        when_a = ts("2026-10-31T12:00:10Z")
        when_b = ts("2026-10-31T12:30:10Z")
        rows = [rec(when_a, "20261031T1200", "safety", "")]
        rows += [rec(when_b, "20261031T1230", "data", "rugcheck:timeout") for _ in range(3)]
        rows.append(rec(when_b, "20261031T1230", "safety", ""))
        rows.append(rec(when_b, "20261031T1230", "data", "rugcheck:" + ("a" * 40)))
        rows.append(rec(when_b, "20261031T1230", "data", "rugcheck:BAD"))
        rows.append(rec(when_b, MINT, "data", "rugcheck:timeout"))
        directory = write_desk(health(), rows)
        decision = alert.assess(
            directory, actions_run_id="99", repository="example/sightline", server="https://github.com")
        self.assertEqual(decision["kind"], "alert")
        self.assertIn("share", decision["rules"])
        self.assertEqual(decision["run_id"], "20261031T1230")
        self.assertEqual(decision["checks"], 6)
        self.assertEqual(decision["data_fails"], 5)
        self.assertEqual(decision["sources"]["rugcheck"], 3)
        self.assertEqual(decision["reason_counts"], {"rugcheck:timeout": 3})
        blob = decision["title"] + decision["body"]
        self.assertNotIn(MINT, blob)
        self.assertNotIn(POISON, blob)
        self.assertNotIn("a" * 40, blob)
        self.assertIn("https://github.com/example/sightline/actions/runs/99", decision["body"])

    def test_same_source_across_utc_midnight(self):
        # Fetch failed, skip type is not data, so the 25% and 0.10 rules stay quiet.
        rows = [
            rec(ts("2026-10-30T23:30:05Z"), "20261030T2330", "data", "jupiter:429"),
            rec(ts("2026-10-31T00:00:05Z"), "20261031T0000", "eligibility", "jupiter:timeout"),
        ]
        rows += [rec(ts("2026-10-31T00:00:05Z"), "20261031T0000") for _ in range(9)]
        directory = write_desk(health(last="20261031T0000", times=[
            "2026-10-30T23:30:05Z", "2026-10-31T00:00:05Z",
        ], day="2026-10-31"), rows)
        decision = alert.assess(directory)
        self.assertEqual(decision["rules"], ["source"])
        self.assertEqual(decision["source"], "jupiter")
        self.assertEqual(decision["title"], "Data health: jupiter failing (2026-10-31)")

    def test_duplicate_run_does_not_alert_but_can_recover(self):
        when = ts("2026-10-31T12:00:10Z")
        rows = [rec(when, "20261031T1200", "data", "rugcheck:429") for _ in range(4)]
        stamped = health(
            last="20261031T1200",
            times=["2026-10-31T12:00:10Z"],
            last_skipped_duplicate_utc="2026-10-31T12:20:00Z",
        )
        decision = alert.assess(write_desk(stamped, rows))
        self.assertEqual(decision["kind"], "ok")
        self.assertEqual(decision["rules"], ["duplicate"])
        clean = [
            rec(ts("2026-10-31T12:00:10Z"), "20261031T1200"),
            rec(ts("2026-10-31T12:30:10Z"), "20261031T1230"),
        ]
        recovered = alert.assess(write_desk(health(
            last_skipped_duplicate_utc="2026-10-31T12:40:00Z",
        ), clean))
        self.assertEqual(recovered["kind"], "recover")

    def test_an_older_duplicate_stamp_does_not_hide_a_new_run(self):
        rows = [rec(ts("2026-10-31T13:00:10Z"), "20261031T1300", "data", "jupiter:conn") for _ in range(2)]
        rows += [rec(ts("2026-10-31T13:00:10Z"), "20261031T1300") for _ in range(2)]
        decision = alert.assess(write_desk(health(
            last="20261031T1300",
            times=["2026-10-31T13:00:10Z"],
            last_skipped_duplicate_utc="2026-10-31T12:10:00Z",
        ), rows))
        self.assertEqual(decision["kind"], "alert")
        self.assertEqual(decision["source"], "jupiter")

    def test_bad_files_are_a_no_alert(self):
        directory = tempfile.mkdtemp()
        with open(os.path.join(directory, "health.json"), "w", encoding="utf-8") as fh:
            fh.write("{")
        with open(os.path.join(directory, "tasks.csv"), "w", encoding="utf-8") as fh:
            fh.write("time,run_id\nnot-a-time,not-a-run\n")
        decision = alert.assess(directory)
        self.assertEqual(decision["kind"], "ok")
        self.assertEqual(decision["rules"], ["no-run"])
        self.assertNotIn(MINT, json.dumps(decision))


class GithubTextTests(unittest.TestCase):
    def test_run_url_is_only_an_actions_link(self):
        self.assertEqual(
            alert.run_url("https://github.com", "example/sightline", "99"),
            "https://github.com/example/sightline/actions/runs/99",
        )
        self.assertEqual(alert.run_url("https://evil.example", "example/sightline", "99"), "")
        self.assertEqual(alert.run_url("https://github.com", "bad repo", "99"), "")
        self.assertEqual(alert.run_url("https://github.com", "example/sightline", MINT), "")

    def test_issue_numbers_are_integers(self):
        self.assertEqual(alert.parse_issues([
            {"number": 4}, {"number": True}, {"number": "4"}, {"number": 0}, {"title": MINT},
        ]), [{"number": 4}])

    def test_close_comment_is_recovered(self):
        self.assertEqual(alert.RECOVERED, "recovered")


class MainTests(unittest.TestCase):
    def test_dry_run_exits_0_and_hides_the_mint(self):
        when = ts("2026-10-31T12:30:10Z")
        rows = [rec(when, "20261031T1230", "data", "dexscreener:parse") for _ in range(1)]
        rows += [rec(when, "20261031T1230") for _ in range(3)]
        directory = write_desk(health(last="20261031T1230", times=["2026-10-31T12:30:10Z"]), rows)
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = alert.main(["--data-dir", directory, "--dry-run"])
        self.assertEqual(code, 0)
        text = buf.getvalue()
        payload = json.loads(text)
        self.assertEqual(payload["kind"], "alert")
        self.assertEqual(payload["source"], "dexscreener")
        self.assertNotIn(MINT, text)
        self.assertNotIn(POISON, text)

    def test_missing_args_and_errors_exit_0(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(alert.main([]), 0)
        self.assertIn("skipped", err.getvalue())
        def _boom(*args, **kwargs):
            raise RuntimeError("boom")

        orig = alert.assess
        alert.assess = _boom
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                self.assertEqual(alert.main(["--data-dir", tempfile.mkdtemp()]), 0)
        finally:
            alert.assess = orig
        self.assertIn("RuntimeError", err.getvalue())
        self.assertNotIn("boom", err.getvalue())


class WorkflowTests(unittest.TestCase):
    def test_alert_step_cannot_fail_the_job(self):
        path = os.path.join(ROOT, ".github", "workflows", "solana-desk-scan.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        pre, post = text.split("jobs:", 1)
        self.assertNotIn("issues:", pre)
        self.assertEqual(text.count("issues: write"), 1)
        scan = post.split("scan:", 1)[1].split("steps:", 1)[0]
        self.assertIn("issues: write", scan)
        self.assertIn("contents: write", scan)
        self.assertLess(post.index("Commit to desk-data"), post.index("Data health alert"))
        self.assertLess(post.index("Save private rugger list"), post.index("Data health alert"))
        block = post.split("Data health alert", 1)[1].split("\n      - ", 1)[0]
        self.assertIn("if: always()", block)
        self.assertIn("continue-on-error: true", block)
        self.assertIn("timeout-minutes: 1", block)
        self.assertIn("data_health_alert.py", block)
        self.assertIn("github.token", block)
        self.assertNotIn("uses:", block)

    def test_script_is_stdlib_only(self):
        path = os.path.join(ROOT, "scripts", "data_health_alert.py")
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                mods.add(node.module.split(".")[0])
        allowed = {"argparse", "calendar", "csv", "json", "os", "re", "subprocess", "sys", "time"}
        self.assertTrue(mods <= allowed)


if __name__ == "__main__":
    unittest.main()
