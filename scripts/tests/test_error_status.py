"""A missing file or an HTTP error must not look like a real result.

These tests call the producers as they are. They do not change a decision.
Where a producer still uses one reason for both an error and a real result,
the test is an expected failure and the pull request lists that gap.
"""
import io
import os
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "solana-desk"))
sys.path.insert(0, os.path.join(ROOT, "prereg"))
sys.path.insert(0, os.path.join(ROOT, "nft", "loop"))

from races.budget import Budget  # noqa: E402
from races.jupiter import Jupiter  # noqa: E402
from races.run import load_config  # noqa: E402
from desk import exits  # noqa: E402
from desk.run import fetch_fail_code, quote  # noqa: E402
from lst_count.run import _rate_for  # noqa: E402
import heartbeat  # noqa: E402
import pricing  # noqa: E402

CFG, _LOCKED = load_config(Path(ROOT) / "races")
WSOL = "So11111111111111111111111111111111111111112"
REAL_STOPS = {"hard_stop", "trailing_stop", "time_stop", "liquidity_pulled"}


class _Hit:
    def __init__(self, status, body):
        self.status = status
        self.body = body
        self.temporary = False
        self.error = None


class _Transport:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def request(self, method, url, headers=None, body=None):
        return _Hit(self.status, self.body)


def _quote_reasons(status, body):
    logs = []

    def record(wallet, kind, detail):
        logs.append(detail or {})

    jup = Jupiter(CFG, _Transport(status, body), Budget(CFG), record)
    got = jup.quote("R1", CFG["wsol_mint"], "FakeMint11111111111111111111111111111111", 1000)
    return got, [row.get("reason") for row in logs if row.get("reason")]


class RaceQuotes(unittest.TestCase):
    def test_a_rate_limit_is_not_a_quote_and_not_no_route(self):
        got, reasons = _quote_reasons(429, {})
        self.assertIsNone(got)
        self.assertIn("429", reasons)
        self.assertNotIn("no_route", reasons)

    @unittest.expectedFailure
    def test_http_401_and_403_are_not_logged_as_no_route(self):
        """Gap: races/jupiter.py logs reason no_route for any quote that is not
        HTTP 200, including 401 and 403. A real empty route uses that same reason.
        Left as an expected failure until the owner splits them.
        """
        for code in (401, 403):
            got, reasons = _quote_reasons(code, {"error": "denied"})
            self.assertIsNone(got)
            self.assertNotIn("no_route", reasons)
            self.assertTrue(any(str(code) in (item or "") for item in reasons), reasons)


class DeskFetches(unittest.TestCase):
    def test_a_401_is_not_a_no_route_quote(self):
        def deny(url, hdr=None):
            raise urllib.error.HTTPError(url, 401, "unauthorized", None, io.BytesIO(b""))

        with self.assertRaises(urllib.error.HTTPError) as caught:
            quote(deny, "FakeMint11111111111111111111111111111111", None, sleep=lambda _s: None)
        self.assertEqual(caught.exception.code, 401)
        self.assertEqual(fetch_fail_code(caught.exception), "401")
        self.assertNotIn(fetch_fail_code(caught.exception), REAL_STOPS | {"", "ok", "no_route"})

    def test_a_real_no_route_is_an_empty_quote(self):
        def missing(url, hdr=None):
            raise urllib.error.HTTPError(
                url, 400, "bad", None, io.BytesIO(b'{"errorCode":"NO_ROUTES_FOUND"}')
            )

        self.assertEqual(quote(missing, "FakeMint11111111111111111111111111111111", None, sleep=lambda _s: None), {})

    def test_a_temporary_error_is_not_a_hard_stop(self):
        pos = exits.new_position(1.0, 100.0, 0)
        why = exits.check(pos, None, None, 0, status="temp_error")[0][2]
        self.assertEqual(why, "stale")
        self.assertNotIn(why, REAL_STOPS)
        stopped = exits.check(exits.new_position(1.0, 100.0, 0), 0.5, 100.0, 10_000, status="ok")[0][2]
        self.assertEqual(stopped, "hard_stop")
        self.assertNotEqual(why, stopped)


class OtherProducers(unittest.TestCase):
    def test_a_missing_lst_pool_is_not_ok(self):
        status, info = _rate_for(
            {"kind": "spl", "mint": WSOL, "pool": "missing-pool"},
            {"pools": {}, "epoch_ok": True, "epoch": 1},
        )
        self.assertEqual(status, "rpc_fail")
        self.assertIsNone(info)
        self.assertNotEqual(status, "ok")

    def test_auth_failures_are_not_an_ok_measurement(self):
        self.assertEqual(pricing.outage_reason_label("error", "opensea http 401"), "auth")
        self.assertEqual(pricing.outage_reason_label("partial", "opensea http 403"), "auth")
        self.assertNotEqual(pricing.outage_reason_label("error", "opensea http 401"), "ok")

    def test_a_failed_workflow_is_not_green_and_a_missing_age_is_not_ok(self):
        self.assertEqual(heartbeat._status("ok", "ok", 1, False, None, None, False), "red")
        self.assertEqual(heartbeat._branch_level(None, 30, 15), "red")
        self.assertEqual(heartbeat._status("ok", "ok", 0, False, 100, None, False), "green")

    def test_a_ua_401_is_not_an_ok_fetch(self):
        import ua_f1.ua_f1_weekly as weekly
        from unittest import mock
        err = urllib.error.HTTPError(
            "https://data.binance.vision/data/spot/daily/klines/x",
            401, "unauthorized", {}, io.BytesIO(b""),
        )
        old = weekly.NETWORK
        weekly.NETWORK = True
        try:
            with mock.patch.object(weekly, "_OPENER") as opener:
                opener.open.side_effect = err
                body, meta = weekly.http_get("https://data.binance.vision/data/spot/daily/klines/x")
        finally:
            weekly.NETWORK = old
        self.assertIsNone(body)
        self.assertIs(meta["ok"], False)
        self.assertEqual(meta["status"], 401)
