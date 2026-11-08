"""Non-200 RugCheck and Jupiter bodies are logged only after a whole-body privacy check.

The body is judged before it is cut to 300 characters. A wallet, identity mark,
token, URL, or header anywhere in the body drops the body. The HTTP status and
the endpoint name stay. Ordinary safety skips are not fetch errors.
"""
import csv
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from desk import run  # noqa: E402
from tests.decision_market import address_n  # noqa: E402
import data_health_alert as alert  # noqa: E402

MINT = address_n(9101)
NOW = 1793383000
WALLET = "So11111111111111111111111111111111111111112"
KEY = "super-secret-key-value"


def _http(code, body):
    if isinstance(body, str):
        body = body.encode("utf-8")
    return urllib.error.HTTPError("https://api.example.test/x", code, "x", {}, io.BytesIO(body))


def _rows(path):
    with open(os.path.join(path, "tasks.csv"), newline="") as fh:
        return list(csv.DictReader(fh))


def _pair(dex="raydium", liquidity=True):
    body = {
        "chainId": "solana", "dexId": dex, "pairAddress": "P",
        "quoteToken": {"address": run.SOL},
        "baseToken": {"address": MINT, "symbol": "TST"},
        "pairCreatedAt": (NOW - 3600) * 1000,
        "txns": {"h1": {"sells": 5}},
    }
    if liquidity:
        body["liquidity"] = {"quote": 500, "usd": 1e5}
    return body


RC = {
    "markets": [{"marketType": "raydium", "pubkey": "P", "lp": {"lpLockedPct": 100}}],
    "topHolders": [], "score_normalised": 1, "creatorBalance": 0, "token": {"supply": 1},
}
QUOTE = {"outAmount": "1000000", "priceImpactPct": "0.001", "routePlan": [1]}


def _scan(leads_get, key=None):
    calls = []

    def get(url, headers=None):
        calls.append((url, headers))
        if "token-profiles" in url:
            return [{"chainId": "solana", "tokenAddress": MINT}]
        if "token-boosts" in url:
            return []
        return leads_get(url, headers)

    d = tempfile.mkdtemp()
    h = run.scan(d, get, key=key, now=NOW, mode="scan", sleep=lambda s: None)
    return d, h, _rows(d), calls


def _degraded(rows):
    runs = alert.collect(rows)
    bad = []
    for rid, run in runs.items():
        if run["data_fails"] > 0 or any(run["sources"].get(name, 0) > 0 for name in alert.SOURCES):
            bad.append(rid)
    return bad, runs


class SanitizeBody(unittest.TestCase):
    def test_known_code_is_kept_and_message_is_not(self):
        text = json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": "rejected"})
        self.assertEqual(run.sanitize_error_body(text), "token_not_tradable")
        self.assertNotIn("rejected", run.sanitize_error_body(text))

    def test_hostile_body_is_discarded_before_truncation(self):
        pad = "AB_" * 150
        samples = [
            json.dumps({"errorCode": "SAFE", "message": pad + WALLET}),
            json.dumps({"errorCode": "SAFE", "message": "see https://evil.example/a"}),
            json.dumps({"message": "Authorization Bearer " + KEY}),
            json.dumps({"errorCode": "SAFE", "message": "ghp_" + "a" * 20}),
            json.dumps({"errorCode": "SAFE", "message": "stake1" + "u" * 20}),
            json.dumps({"errorCode": "SAFE", "message": "@notanowner"}),
            json.dumps({"errorCode": "SAFE", "message": "caf\u00e9"}),
            "not-json",
            "",
            None,
        ]
        for text in samples:
            got = run.sanitize_error_body(text)
            self.assertEqual(got, "", repr(text)[:80])
            if isinstance(text, str):
                self.assertNotIn(WALLET, got)
                self.assertNotIn(KEY, got)
                self.assertNotIn("https://", got)
                self.assertNotIn("ghp_", got)

    def test_long_message_is_not_stored(self):
        message = "AB_" * 400
        text = json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": message})
        got = run.sanitize_error_body(text)
        self.assertEqual(got, "token_not_tradable")
        self.assertNotIn(message, got)
        self.assertLessEqual(len(got), 300)

    def test_hex_long_base58_private_key_nested_and_escaped_at(self):
        samples = [
            json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": "0x" + "ab" * 20}),
            json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": "1" * 64}),
            json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": "-----BEGIN PRIVATE KEY-----"}),
            json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "extra": "fine"}),
            json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "error": {"code": "TOKEN_NOT_TRADABLE"}}),
            '{"errorCode":"TOKEN_NOT_TRADABLE","message":"\\u0040tracked"}',
            json.dumps({"errorCode": "BAD_MINT"}),
        ]
        for text in samples:
            self.assertEqual(run.sanitize_error_body(text), "", text[:80])

    def test_absent_and_non_json_and_nested_are_discarded(self):
        self.assertEqual(run.sanitize_error_body(None), "")
        self.assertEqual(run.sanitize_error_body("   "), "")
        self.assertEqual(run.sanitize_error_body("<html>nope</html>"), "")
        self.assertEqual(run.sanitize_error_body(json.dumps(["nope"])), "")
        self.assertEqual(run.sanitize_error_body(json.dumps({"error": {"code": "X"}})), "")


class LoggedReply(unittest.TestCase):
    def test_rugcheck_400_keeps_status_name_and_safe_body(self):
        body = json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": "rejected"})

        def leads(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair()]}
            if "rugcheck" in url:
                raise _http(400, body)
            if "jup.ag" in url:
                return QUOTE
            raise AssertionError(url)

        d, h, rows, calls = _scan(leads, key=KEY)
        cell = rows[0]["fail_reason"]
        self.assertIn("rugcheck:400", cell)
        self.assertIn("endpoint:rugcheck_report", cell)
        self.assertIn("detail:token_not_tradable", cell)
        self.assertNotIn("rejected", cell)
        self.assertNotIn("TOKEN_NOT_TRADABLE", cell)
        self.assertNotIn("http://", cell)
        self.assertNotIn("https://", cell)
        self.assertNotIn(KEY, cell)
        self.assertNotIn("x-api-key", cell)
        with open(os.path.join(d, "tasks.csv"), encoding="utf-8") as fh:
            self.assertNotIn(KEY, fh.read())
        self.assertEqual(h["data_fail_by_reason"], {"rugcheck:400": 1})
        self.assertEqual(alert.tokens(cell), [("rugcheck", "rugcheck:400")])

    def test_jupiter_hostile_body_keeps_status_and_drops_body(self):
        secret = json.dumps({
            "errorCode": "SAFE",
            "message": ("AB_" * 120) + WALLET,
            "headers": "Authorization: Bearer " + KEY,
        })

        def leads(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair()]}
            if "rugcheck" in url:
                return RC
            if "jup.ag" in url:
                raise _http(400, secret)
            raise AssertionError(url)

        _d, _h, rows, _calls = _scan(leads, key=KEY)
        cell = rows[0]["fail_reason"]
        self.assertIn("jupiter:400", cell)
        self.assertIn("endpoint:jupiter_quote", cell)
        self.assertNotIn("detail:", cell)
        self.assertNotIn(WALLET, cell)
        self.assertNotIn(KEY, cell)
        self.assertNotIn("Authorization", cell)
        self.assertNotIn("https://", cell)
        self.assertEqual(alert.tokens(cell), [("jupiter", "jupiter:400")])

    def test_absent_body_and_non_json_log_the_name_only(self):
        def leads(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair()]}
            if "rugcheck" in url:
                raise _http(404, b"")
            if "jup.ag" in url:
                raise _http(400, b"not-json")
            raise AssertionError(url)

        _d, _h, rows, _calls = _scan(leads)
        cell = rows[0]["fail_reason"]
        self.assertIn("rugcheck:404;endpoint:rugcheck_report", cell)
        self.assertIn("jupiter:400;endpoint:jupiter_quote", cell)
        self.assertNotIn("detail:", cell)
        self.assertNotIn("not-json", cell)
        kinds = {src for src, _tok in alert.tokens(cell)}
        self.assertEqual(kinds, {"rugcheck", "jupiter"})

    def test_multibyte_body_is_not_stored(self):
        raw = json.dumps({"errorCode": "SAFE", "message": "caf\u00e9"}).encode("utf-8")

        def leads(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair()]}
            if "rugcheck" in url:
                raise _http(400, raw)
            if "jup.ag" in url:
                return QUOTE
            raise AssertionError(url)

        _d, _h, rows, _calls = _scan(leads)
        cell = rows[0]["fail_reason"]
        self.assertIn("rugcheck:400;endpoint:rugcheck_report", cell)
        self.assertNotIn("detail:", cell)
        self.assertNotIn("caf", cell)
        self.assertNotIn("SAFE", cell.split("endpoint:", 1)[-1])

    def test_safety_skip_is_not_a_fetch_error_and_missing_data_stays_sourceless(self):
        def safety(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair(dex="pumpfun")]}
            if "rugcheck" in url:
                return RC
            if "jup.ag" in url:
                return QUOTE
            raise AssertionError(url)

        def missing(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair(liquidity=False)]}
            if "rugcheck" in url:
                return RC
            if "jup.ag" in url:
                return QUOTE
            raise AssertionError(url)

        d1, _h1, safe_rows, _c1 = _scan(safety)
        d2 = tempfile.mkdtemp()
        # A later slot so the second scan is not a duplicate.
        calls = []

        def get(url, headers=None):
            calls.append(url)
            if "token-profiles" in url:
                return [{"chainId": "solana", "tokenAddress": MINT}]
            if "token-boosts" in url:
                return []
            return missing(url, headers)

        run.scan(d2, get, key=None, now=NOW + 1800, mode="scan", sleep=lambda s: None)
        miss_rows = _rows(d2)
        self.assertEqual(safe_rows[0]["skip_type"], "safety")
        self.assertEqual(safe_rows[0]["reason"], "not_graduated")
        self.assertEqual(safe_rows[0]["fail_reason"], "skip:not_graduated")
        self.assertEqual(safe_rows[0]["result"], "skip")
        self.assertEqual(alert.tokens(safe_rows[0]["fail_reason"]), [])
        bad, runs = _degraded(safe_rows)
        self.assertEqual(bad, [])
        self.assertEqual(runs[safe_rows[0]["run_id"]]["data_fails"], 0)

        self.assertEqual(miss_rows[0]["skip_type"], "data")
        self.assertEqual(miss_rows[0]["reason"], "missing_data")
        self.assertEqual(miss_rows[0]["fail_reason"], "skip:missing_data")
        self.assertEqual(alert.tokens(miss_rows[0]["fail_reason"]), [])
        bad, runs = _degraded(miss_rows)
        self.assertEqual(len(bad), 1)
        self.assertEqual(runs[bad[0]]["sources"], {name: 0 for name in alert.SOURCES})
        self.assertNotIn("rugcheck:", miss_rows[0]["fail_reason"])
        self.assertNotIn("jupiter:", miss_rows[0]["fail_reason"])

    def test_persisted_row_drops_poison_and_keeps_oversized_no_route(self):
        wallet = "0x" + "ab" * 20
        ones = "1" * 64
        key = "-----BEGIN PRIVATE KEY-----"
        cases = {
            "hex": json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": wallet}).encode(),
            "ones": json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": ones}).encode(),
            "pem": json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "message": key}).encode(),
            "nested": json.dumps({"errorCode": "TOKEN_NOT_TRADABLE", "error": {"code": "X"}}).encode(),
            "escaped": b'{"errorCode":"TOKEN_NOT_TRADABLE","message":"\\u0040tracked"}',
            "utf8": b'\xff\xfe{"errorCode":"TOKEN_NOT_TRADABLE"}',
        }

        def leads(kind):
            def one(url, headers=None):
                if "/latest/dex/tokens/" in url:
                    return {"pairs": [_pair()]}
                if "rugcheck" in url:
                    raise _http(400, cases[kind])
                if "jup.ag" in url:
                    return QUOTE
                raise AssertionError(url)
            return one

        for kind, raw in cases.items():
            d, _h, rows, _calls = _scan(leads(kind))
            with open(os.path.join(d, "tasks.csv"), "rb") as fh:
                blob = fh.read()
            cell = rows[0]["fail_reason"]
            self.assertIn("rugcheck:400", cell, kind)
            self.assertIn("endpoint:rugcheck_report", cell, kind)
            self.assertNotIn("detail:", cell, kind)
            self.assertNotIn(wallet.encode(), blob, kind)
            self.assertNotIn(ones.encode(), blob, kind)
            self.assertNotIn(b"PRIVATE KEY", blob, kind)
            self.assertNotIn(b"\xff", blob, kind)
            self.assertNotIn(b"@tracked", blob, kind)
            self.assertNotIn(b"\\u0040", blob, kind)

        pad = b"A" * 70000
        huge = b'{"errorCode":"COULD_NOT_FIND_ANY_ROUTE","pad":"' + pad + b'"}'

        def no_route(url, headers=None):
            if "/latest/dex/tokens/" in url:
                return {"pairs": [_pair()]}
            if "rugcheck" in url:
                return RC
            if "jup.ag" in url:
                raise _http(400, huge)
            raise AssertionError(url)

        d, _h, rows, _calls = _scan(no_route)
        with open(os.path.join(d, "tasks.csv"), "rb") as fh:
            blob = fh.read()
        self.assertNotIn(b"jupiter:400", blob)
        self.assertNotIn(pad[:100], blob)
        self.assertEqual(rows[0]["fail_reason"], "skip:" + rows[0]["reason"])
        self.assertEqual(alert.tokens(rows[0]["fail_reason"]), [])
        self.assertNotEqual(rows[0]["skip_type"], "data")
