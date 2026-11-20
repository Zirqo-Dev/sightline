"""Webhook outcome and queue age. Offline. No secrets, no live endpoint.

The posted request is unchanged. These tests check what last-run.json and
state.json remember after a post, and that a stale queue is not resent.
"""
import io
import json
import os
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import prefilter as P  # noqa: E402
import sw01  # noqa: E402
from test_regressions import fake_tracked, fresh_state, kill_patches, quiet_req, run_main  # noqa: E402

URL = "https://example.invalid/hook"
SECRET = "sekret-token-value"
BODY = b'{"token":"sekret-token-value"}'


def _http_error(code):
    return urllib.error.HTTPError(URL, code, "no", None, io.BytesIO(BODY))


class Status(unittest.TestCase):
    def test_an_http_code_is_kept_and_the_body_and_url_are_not(self):
        http = P.Http({"webhook": 0})
        with mock.patch("urllib.request.urlopen", side_effect=_http_error(401)), mock.patch("time.sleep"):
            self.assertFalse(http.post_webhook(URL, {"a": 1}, {"Authorization": "Bearer " + SECRET}))
        self.assertEqual(http.webhook_attempts, [{"status": 401, "ok": False}])
        blob = json.dumps(http.webhook_attempts)
        self.assertNotIn(URL, blob)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("sekret", blob)

    def test_a_network_error_stores_the_class_not_the_message(self):
        http = P.Http({"webhook": 0})

        def boom(req, timeout=30):
            raise urllib.error.URLError(URL + " " + SECRET)

        with mock.patch("urllib.request.urlopen", boom), mock.patch("time.sleep"):
            self.assertFalse(http.post_webhook(URL, {"a": 1}))
        self.assertEqual(http.webhook_attempts, [{"status": "URLError", "ok": False}])
        blob = json.dumps(http.webhook_attempts)
        self.assertNotIn("example.invalid", blob)
        self.assertNotIn(SECRET, blob)

    def test_a_retry_that_lands_is_one_success(self):
        http = P.Http({"webhook": 0})
        calls = {"n": 0}

        class Resp:
            status = 204

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def open_one(req, timeout=30):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _http_error(429)
            return Resp()

        with mock.patch("urllib.request.urlopen", open_one), mock.patch("time.sleep"):
            self.assertTrue(http.post_webhook(URL, {"a": 1}))
        self.assertEqual(http.webhook_attempts, [{"status": 204, "ok": True}])

    def test_sw01_still_leaves_a_failed_post_unmarked(self):
        http = P.Http({"webhook": 0})
        state = {}
        decision = {
            "now": 1700000000,
            "alerts": [{
                "kind": "sw01_exit",
                "policy": "ab" * 28,
                "ticker": "EXIT",
                "payload": {"reason": "sw01_exit", "names": ["EXIT"]},
            }],
        }

        def boom(req, timeout=30):
            raise TimeoutError(URL)

        with mock.patch("urllib.request.urlopen", boom), mock.patch("time.sleep"):
            out = sw01.deliver(http, {"webhook": {"idempotency_slot_minutes": 120}}, state, decision, URL, SECRET)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["candidates"], [])
        self.assertNotIn("sw01", state)
        self.assertEqual(http.webhook_attempts, [{"status": "TimeoutError", "ok": False}])
        blob = json.dumps(http.webhook_attempts)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("example.invalid", blob)


class Queue(unittest.TestCase):
    def test_a_missing_stamp_or_an_old_stamp_is_dropped(self):
        now = 1_700_000_000
        recent = now - 3600
        old = now - P.WEBHOOK_QUEUE_MAX_AGE_S - 1
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(recent))
        pending = [
            {"key": "a", "kind": "whale_move", "name": "LEAD", "queued_at": stamp},
            {"key": "b", "kind": "kill_line", "name": "OLD"},
            {"key": "c", "kind": "whale_move", "name": "NEW", "queued_at": stamp},
            {"key": "d", "kind": "kill_line", "name": "STALE", "queued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(old))},
        ]
        kept, dropped = P.partition_pending(pending, now)
        self.assertEqual([item["key"] for item in kept], ["a", "c"])
        self.assertEqual([item["key"] for item in dropped], ["b", "d"])

    def test_a_retry_keeps_the_first_queued_at(self):
        first = P.with_queued_at([{"key": "k", "kind": "kill_line", "name": "N"}], "2026-11-20T12:00:00Z")
        again = P.with_queued_at(first, "2026-11-21T12:00:00Z")
        self.assertEqual(again[0]["queued_at"], "2026-11-20T12:00:00Z")

    def test_a_run_records_the_code_and_drops_the_stale_queue(self):
        sent = []

        def post(self, url, body, headers=None):
            sent.append(body)
            self._note_webhook(503, False)
            return False

        now = int(time.time())
        stale = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - P.WEBHOOK_QUEUE_MAX_AGE_S - 60))
        pending = [
            {"key": "old-move", "kind": "whale_move", "name": "OLDMOVE", "metrics": {}},
            {"key": "old-kill", "kind": "kill_line", "name": "OLDKILL", "metrics": {}, "queued_at": stale},
        ]
        with tempfile.TemporaryDirectory() as data:
            fresh_state(data, {"pending_triggers": pending, "seen_launches": ["old"]})
            env = {"SOLADA_WEBHOOK_URL": URL, "SOLADA_WEBHOOK_KEY": SECRET}
            hook = mock.patch.object(P.Http, "post_webhook", post)
            run = run_main(data, quiet_req, kill_patches({}, None) + [hook], dry=False, env=env)
            state = json.load(open(os.path.join(data, "state.json")))
        self.assertEqual(sent, [])
        self.assertEqual(run["webhook"]["dropped_stale"], 2)
        self.assertEqual(run["webhook"]["dropped_by_kind"], {"kill_line": 1, "whale_move": 1})
        self.assertEqual(run["webhook"]["status"], "not_attempted")
        self.assertEqual(state["pending_triggers"], [])
        blob = json.dumps(run)
        self.assertNotIn(URL, blob)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("OLDMOVE", blob)

    def test_consecutive_failures_climb_and_a_success_clears_them(self):
        codes = iter([503, 204])

        class Resp:
            def __init__(self, status):
                self.status = status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def open_one(req, timeout=30):
            code = next(codes)
            if code >= 400:
                raise _http_error(code)
            return Resp(code)

        env = {"SOLADA_WEBHOOK_URL": URL, "SOLADA_WEBHOOK_KEY": SECRET}
        with tempfile.TemporaryDirectory() as data:
            fresh_state(data)
            with mock.patch("urllib.request.urlopen", open_one), mock.patch("time.sleep"):
                first = run_main(
                    data, quiet_req, kill_patches(fake_tracked(), 4200), dry=False, env=env,
                )
                state = json.load(open(os.path.join(data, "state.json")))
                self.assertEqual(first["webhook"]["status"], 503)
                self.assertFalse(first["webhook"]["ok"])
                self.assertEqual(first["webhook"]["consecutive_failures"], 1)
                self.assertTrue(state["pending_triggers"])
                self.assertTrue(all(P.parse_iso(item.get("queued_at")) for item in state["pending_triggers"]))
                second = run_main(data, quiet_req, kill_patches({}, None), dry=False, env=env)
            blob = json.dumps(first) + json.dumps(second)
        self.assertTrue(second["webhook"]["ok"])
        self.assertEqual(second["webhook"]["status"], 204)
        self.assertEqual(second["webhook"]["consecutive_failures"], 0)
        self.assertNotIn(URL, blob)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("sekret", blob)


if __name__ == "__main__":
    unittest.main()
