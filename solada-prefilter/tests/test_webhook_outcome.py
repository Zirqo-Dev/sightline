"""Webhook outcome and queue age. Offline. No secrets, no live endpoint.

The posted request is unchanged. These tests check what last-run.json and
state.json remember after a post, and that a stale queue is not resent.
"""
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
        with mock.patch.object(P, "_open_webhook", side_effect=_http_error(401)), mock.patch("time.sleep"):
            self.assertFalse(http.post_webhook(URL, {"a": 1}, {"Authorization": "Bearer " + SECRET}))
        self.assertEqual(http.webhook_attempts, [{"status": 401, "ok": False, "auth_header_present": True}])
        blob = json.dumps(http.webhook_attempts)
        self.assertNotIn(URL, blob)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("sekret", blob)

    def test_a_network_error_stores_the_class_not_the_message(self):
        http = P.Http({"webhook": 0})

        def boom(req, timeout=30):
            raise urllib.error.URLError(URL + " " + SECRET)

        with mock.patch.object(P, "_open_webhook", boom), mock.patch("time.sleep"):
            self.assertFalse(http.post_webhook(URL, {"a": 1}))
        self.assertEqual(http.webhook_attempts, [{"status": "URLError", "ok": False, "auth_header_present": False}])
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

        with mock.patch.object(P, "_open_webhook", open_one), mock.patch("time.sleep"):
            self.assertTrue(http.post_webhook(URL, {"a": 1}))
        self.assertEqual(http.webhook_attempts, [{"status": 204, "ok": True, "auth_header_present": False}])

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

        with mock.patch.object(P, "_open_webhook", boom), mock.patch("time.sleep"):
            out = sw01.deliver(http, {"webhook": {"idempotency_slot_minutes": 120}}, state, decision, URL, SECRET)
        self.assertEqual(out["failed"], 1)
        self.assertEqual(out["candidates"], [])
        self.assertNotIn("sw01", state)
        self.assertEqual(http.webhook_attempts, [{
            "status": "TimeoutError", "ok": False, "auth_header_present": True,
        }])
        blob = json.dumps(http.webhook_attempts)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("example.invalid", blob)


class Queue(unittest.TestCase):
    def test_a_missing_stamp_is_kept_and_a_future_one_is_reset(self):
        now = 1_700_000_000
        iso_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        hour = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + P.WEBHOOK_FUTURE_SKEW_S))
        ahead = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now + P.WEBHOOK_FUTURE_SKEW_S + 1))
        recent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600))
        old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - P.WEBHOOK_QUEUE_MAX_AGE_S - 1))
        pending = [
            {"key": "missing", "kind": "whale_move", "name": "M"},
            {"key": "future", "kind": "kill_line", "name": "F", "queued_at": ahead},
            {"key": "edge", "kind": "whale_move", "name": "E", "queued_at": hour},
            {"key": "old", "kind": "kill_line", "name": "O", "queued_at": old},
            {"key": "recent", "kind": "whale_move", "name": "R", "queued_at": recent},
        ]
        normalized = P.normalize_queued(pending, now)
        kept, dropped = P.partition_pending(normalized, now)
        self.assertEqual([item["key"] for item in dropped], ["old"])
        by = {item["key"]: item["queued_at"] for item in kept}
        self.assertEqual(by["missing"], iso_now)
        self.assertEqual(by["future"], iso_now)
        self.assertEqual(by["edge"], hour)
        self.assertEqual(by["recent"], recent)

    def test_a_retry_keeps_the_first_queued_at(self):
        first = P.with_queued_at([{"key": "k", "kind": "kill_line", "name": "N"}], "2026-11-20T12:00:00Z")
        again = P.with_queued_at(first, "2026-11-21T12:00:00Z")
        self.assertEqual(again[0]["queued_at"], "2026-11-20T12:00:00Z")

    def test_a_missing_stamp_is_posted_without_the_stamp_and_an_old_one_is_dropped(self):
        sent = []

        def post(self, url, body, headers=None):
            sent.append(json.dumps(body))
            self._note_webhook(503, False, auth=True)
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
        self.assertEqual(len(sent), 1)
        self.assertIn("OLDMOVE", sent[0])
        self.assertNotIn("OLDKILL", sent[0])
        self.assertNotIn("queued_at", sent[0])
        self.assertEqual(run["webhook"]["dropped_stale"], 1)
        self.assertEqual(run["webhook"]["dropped_by_kind"], {"kill_line": 1})
        self.assertEqual([item["key"] for item in state["pending_triggers"]], ["old-move"])
        self.assertIsNotNone(P.parse_iso(state["pending_triggers"][0].get("queued_at")))
        blob = json.dumps(run)
        self.assertNotIn("queued_at", blob)
        self.assertNotIn(URL, blob)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("OLDKILL", blob)

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
            with mock.patch.object(P, "_open_webhook", open_one), mock.patch("time.sleep"):
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
        self.assertNotIn("queued_at", blob)
        self.assertIs(first["webhook"]["auth_header_present"], True)


class Redirect(unittest.TestCase):
    def test_a_redirect_is_recorded_as_that_status_and_not_followed(self):
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                hits.append(("POST", self.path))
                self.send_response(302)
                self.send_header("Location", "/landed")
                self.end_headers()

            def do_GET(self):
                hits.append(("GET", self.path))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, fmt, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            http = P.Http({"webhook": 0})
            ok = http.post_webhook(
                "http://127.0.0.1:%d/hook" % port,
                {"reason": "threshold"},
                {"Authorization": "Bearer " + SECRET},
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertFalse(ok)
        self.assertEqual(hits, [("POST", "/hook")])
        self.assertEqual(http.webhook_attempts, [{
            "status": 302, "ok": False, "auth_header_present": True,
        }])
        blob = json.dumps(http.webhook_attempts)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("127.0.0.1", blob)
        self.assertNotIn("landed", blob)


if __name__ == "__main__":
    unittest.main()
