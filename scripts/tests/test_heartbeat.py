"""Heartbeat rules. Fake run histories only. No network."""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import heartbeat

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
DESK = ".github/workflows/solana-desk-scan.yml"
RACES = ".github/workflows/races.yml"
UA = ".github/workflows/ua-f1.yml"


def at(delta):
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(minutes, conclusion="success", event="schedule"):
    return {"at": at(timedelta(minutes=minutes)), "conclusion": conclusion, "event": event}


def spec(path, name, crons, branch=None):
    return {"file": path, "name": name, "crons": list(crons), "data_branch": branch}


def health(workflows, runs, commits=None):
    return heartbeat.build_health(workflows, runs, commits or {}, NOW)


class Cron(unittest.TestCase):
    def test_half_hour_and_three_hour_slots(self):
        half = heartbeat.recent_slots(["*/30 * * * *"], NOW, 2)
        self.assertEqual(half[0], datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(half[1], datetime(2026, 11, 11, 11, 30, tzinfo=timezone.utc))
        three = heartbeat.recent_slots(["7 */3 * * *"], NOW, 1)
        self.assertEqual(three[0], datetime(2026, 11, 11, 9, 7, tzinfo=timezone.utc))

    def test_expected_interval_is_the_longest_quiet_stretch(self):
        self.assertEqual(heartbeat.interval_minutes(("*/30 * * * *",)), 30)
        self.assertEqual(heartbeat.interval_minutes(("12,42 * * * *",)), 30)
        self.assertEqual(heartbeat.interval_minutes(("7 */3 * * *",)), 180)
        self.assertEqual(heartbeat.interval_minutes(("40 0 * * *",)), 24 * 60)
        weekly = ("0 1 * * 1", "0 0 1 * *", "23 0 1 * *", "23 3 1 * *")
        self.assertEqual(heartbeat.interval_minutes(weekly), 7 * 24 * 60)

    def test_grace_grows_with_the_interval(self):
        self.assertEqual(heartbeat.grace_minutes(30), 15)
        self.assertEqual(heartbeat.grace_minutes(180), 45)
        self.assertEqual(heartbeat.grace_minutes(24 * 60), 6 * 60)
        self.assertEqual(heartbeat.grace_minutes(7 * 24 * 60), 42 * 60)


class Cases(unittest.TestCase):
    def test_ok(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")],
            {DESK: [run(-5), run(-35)]},
            {"desk-data": at(timedelta(minutes=-4))},
        )
        row = doc["workflows"][0]
        self.assertEqual(doc["status"], "green")
        self.assertEqual(row["status"], "green")
        self.assertFalse(row["missed_slot"])
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["last_conclusion"], "success")
        self.assertEqual(row["last_event"], "schedule")
        self.assertFalse(row["data_branch"]["stale"])
        self.assertGreaterEqual(row["days_until_disable"], 59)
        self.assertFalse(row["disable_red"])
        heartbeat.assert_public(doc)

    def test_missed_slot(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")],
            {DESK: [
                run(-10, "success", "workflow_dispatch"),
                run(-120, "success", "schedule"),
            ]},
            {"desk-data": at(timedelta(minutes=-4))},
        )
        row = doc["workflows"][0]
        self.assertTrue(row["missed_slot"])
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["status"], "red")
        self.assertEqual(doc["status"], "red")
        self.assertFalse(row["disable_red"])

    def test_failed(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")],
            {DESK: [run(-5, "failure"), run(-35, "failure"), run(-65, "success")]},
            {"desk-data": at(timedelta(minutes=-4))},
        )
        row = doc["workflows"][0]
        self.assertEqual(row["consecutive_failures"], 2)
        self.assertEqual(row["last_conclusion"], "failure")
        self.assertFalse(row["missed_slot"])
        self.assertEqual(row["status"], "red")

    def test_stale_data_branch(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")],
            {DESK: [run(-5), run(-35)]},
            {"desk-data": at(timedelta(hours=-3))},
        )
        row = doc["workflows"][0]
        self.assertTrue(row["data_branch"]["stale"])
        self.assertEqual(row["data_branch"]["name"], "desk-data")
        self.assertEqual(row["data_branch"]["last_commit_at"], at(timedelta(hours=-3)))
        self.assertFalse(row["missed_slot"])
        self.assertEqual(row["status"], "red")

    def test_near_auto_disable(self):
        quiet = spec(".github/workflows/pages.yml", "pages", [])
        doc = health(
            [quiet],
            {quiet["file"]: [{"at": at(timedelta(days=-51)), "conclusion": "success", "event": "push"}]},
        )
        row = doc["workflows"][0]
        self.assertEqual(row["days_until_disable"], 9)
        self.assertTrue(row["disable_red"])
        self.assertFalse(row["missed_slot"])
        self.assertIsNone(row["data_branch"])
        self.assertEqual(row["status"], "red")
        self.assertEqual(doc["status"], "red")

    def test_disable_warning_is_amber_before_the_red_window(self):
        quiet = spec(".github/workflows/pages.yml", "pages", [])
        doc = health(
            [quiet],
            {quiet["file"]: [{"at": at(timedelta(days=-45)), "conclusion": "success", "event": "push"}]},
        )
        row = doc["workflows"][0]
        self.assertEqual(row["days_until_disable"], 15)
        self.assertFalse(row["disable_red"])
        self.assertEqual(row["status"], "amber")
        self.assertEqual(doc["status"], "amber")

    def test_aging_data_branch_is_amber(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")],
            {DESK: [run(-5), run(-35)]},
            {"desk-data": at(timedelta(minutes=-25))},
        )
        row = doc["workflows"][0]
        self.assertFalse(row["data_branch"]["stale"])
        self.assertEqual(row["status"], "amber")

    def test_shared_branch_uses_each_workflow_interval(self):
        daily = spec(".github/workflows/daily-status.yml", "Daily status", ["40 0 * * *"], "desk-data")
        scan = spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")
        commit = at(timedelta(hours=-2))
        doc = health(
            [daily, scan],
            {
                daily["file"]: [run(-30)],
                scan["file"]: [run(-5), run(-35)],
            },
            {"desk-data": commit},
        )
        by_file = {row["file"]: row for row in doc["workflows"]}
        self.assertFalse(by_file[daily["file"]]["data_branch"]["stale"])
        self.assertTrue(by_file[scan["file"]]["data_branch"]["stale"])
        self.assertEqual(doc["status"], "red")

    def test_in_progress_does_not_count_as_a_failure(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"])],
            {DESK: [
                {"at": at(timedelta(minutes=-1)), "conclusion": None, "event": "schedule"},
                run(-5, "success"),
            ]},
        )
        row = doc["workflows"][0]
        self.assertEqual(row["consecutive_failures"], 0)
        self.assertEqual(row["last_conclusion"], "in_progress")
        self.assertEqual(row["status"], "green")


class Privacy(unittest.TestCase):
    def test_api_rows_keep_only_time_conclusion_and_event(self):
        cleaned = heartbeat.public_run({
            "run_started_at": "2026-11-11T11:00:00Z",
            "conclusion": "success",
            "event": "schedule",
            "actor": {"login": "octocat"},
            "triggering_actor": {"login": "octocat"},
            "head_commit": {"message": "hello", "author": {"email": "person@example.com", "name": "Pat"}},
            "html_url": "https://github.com/example/example/actions/runs/1",
        })
        self.assertEqual(set(cleaned), {"at", "conclusion", "event"})
        blob = json.dumps(cleaned)
        self.assertNotIn("octocat", blob)
        self.assertNotIn("example.com", blob)
        self.assertNotIn("Pat", blob)

    def test_public_document_rejects_identities(self):
        doc = health(
            [spec(DESK, "Solana desk scan", ["*/30 * * * *"], "desk-data")],
            {DESK: [run(-5)]},
            {"desk-data": at(timedelta(minutes=-4))},
        )
        heartbeat.assert_public(doc)
        leaked = json.loads(json.dumps(doc))
        leaked["workflows"][0]["actor"] = "octocat"
        with self.assertRaises(ValueError):
            heartbeat.assert_public(leaked)
        named = json.loads(json.dumps(doc))
        named["workflows"][0]["name"] = "person@example.com"
        with self.assertRaises(ValueError):
            heartbeat.assert_public(named)
        handled = json.loads(json.dumps(doc))
        handled["workflows"][0]["name"] = "scan @someone"
        with self.assertRaises(ValueError):
            heartbeat.assert_public(handled)
        wallet = json.loads(json.dumps(doc))
        wallet["workflows"][0]["name"] = "addr1notawallet"
        with self.assertRaises(ValueError):
            heartbeat.assert_public(wallet)

    def test_refuses_every_branch_except_health_data(self):
        heartbeat.assert_target("health-data")
        for name in ("main", "master", "desk-data", "races-data", "ua-f1-data", "nft-data"):
            with self.assertRaises(SystemExit):
                heartbeat.assert_target(name)


class LiveFiles(unittest.TestCase):
    def test_workflow_files_map_crons_and_data_branches(self):
        specs = heartbeat.load_workflow_files(os.path.join(ROOT, ".github", "workflows"))
        by = {item["file"]: item for item in specs}
        self.assertEqual(by[DESK]["crons"], ["*/30 * * * *"])
        self.assertEqual(by[DESK]["data_branch"], "desk-data")
        self.assertEqual(by[RACES]["crons"], ["12,42 * * * *"])
        self.assertEqual(by[RACES]["data_branch"], "races-data")
        self.assertEqual(by[UA]["data_branch"], "ua-f1-data")
        self.assertEqual(by[UA]["crons"], ["0 1 * * 1", "0 0 1 * *", "23 0 1 * *", "23 3 1 * *"])
        self.assertIsNone(by[".github/workflows/desk-data-guard.yml"]["data_branch"])
        self.assertEqual(by[".github/workflows/desk-data-guard.yml"]["crons"], ["17 * * * *"])
        self.assertEqual(by[".github/workflows/solada-prefilter.yml"]["data_branch"], "desk-data")
        self.assertEqual(by[".github/workflows/solada-prefilter.yml"]["crons"], [])
        self.assertEqual(by[".github/workflows/run-forensics.yml"]["data_branch"], "desk-data")
        for item in specs:
            self.assertIn(item["data_branch"], (None, "desk-data", "races-data", "ua-f1-data"))

    def test_weekly_data_branch_stale_after_its_own_interval(self):
        ua = heartbeat.load_workflow_files(os.path.join(ROOT, ".github", "workflows"))
        ua = [item for item in ua if item["file"] == UA][0]
        fresh = health([ua], {UA: [run(-60)]}, {"ua-f1-data": at(timedelta(days=-3))})
        stale = health([ua], {UA: [run(-60)]}, {"ua-f1-data": at(timedelta(days=-8))})
        self.assertFalse(fresh["workflows"][0]["data_branch"]["stale"])
        self.assertTrue(stale["workflows"][0]["data_branch"]["stale"])
        self.assertEqual(stale["workflows"][0]["status"], "red")

    def test_publish_workflow_writes_only_health_data(self):
        with open(os.path.join(ROOT, ".github", "workflows", "heartbeat.yml"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn('cron: "7 */3 * * *"', text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("actions: read", text)
        self.assertIn("git push origin HEAD:health-data", text)
        for forbidden in ("HEAD:main", "HEAD:desk-data", "HEAD:races-data", "HEAD:ua-f1-data"):
            self.assertNotIn("git push origin " + forbidden, text)
        for name in ("main", "desk-data", "races-data", "ua-f1-data"):
            self.assertIn(name, text)


if __name__ == "__main__":
    unittest.main()
