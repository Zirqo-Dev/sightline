"""Budget, backoff, and redaction. No network."""

import io
import json
import unittest
import urllib.error

from sol_forensics.rpc import (
    PUBLIC_RPC_URL,
    BudgetExceeded,
    RpcClient,
    RpcMethodError,
    RpcRetriesExhausted,
    transaction_config,
)


class Clock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


def client(transport, budget=50, retries=5, rps=1e9):
    clock = Clock()
    rpc = RpcClient(
        "https://rpc.example/secret-path?api-key=supersecret",
        budget=budget,
        max_rps=rps,
        max_retries=retries,
        transport=transport,
        now=clock.now,
        sleep=clock.sleep,
    )
    rpc.clock = clock
    return rpc


class RpcTests(unittest.TestCase):
    def test_transaction_config_accepts_version_1(self):
        cfg = transaction_config()
        self.assertEqual(cfg["maxSupportedTransactionVersion"], 1)
        self.assertEqual(cfg["encoding"], "jsonParsed")

    def test_429_then_success_backs_off(self):
        calls = {"n": 0}

        def transport(_body):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.HTTPError(
                    PUBLIC_RPC_URL, 429, "rate", hdrs=None, fp=io.BytesIO(b"")
                )
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()

        rpc = client(transport)
        self.assertEqual(rpc.call("getTokenSupply", ["mint"]), {"ok": True})
        self.assertEqual(calls["n"], 3)
        self.assertEqual(rpc.clock.slept, [1, 2])
        self.assertEqual(rpc.calls["getTokenSupply"], 3)
        self.assertGreaterEqual(rpc.min_interval, 1.0)

    def test_retry_after_header_sets_the_wait(self):
        calls = {"n": 0}

        def transport(_body):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.HTTPError(
                    PUBLIC_RPC_URL,
                    429,
                    "rate",
                    {"Retry-After": "12"},
                    io.BytesIO(b'{"error":"slow"}'),
                )
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()

        rpc = client(transport)
        self.assertEqual(rpc.call("getTokenLargestAccounts", ["mint"]), {"ok": True})
        self.assertEqual(rpc.clock.slept, [12])

    def test_version_error_is_not_retried(self):
        calls = {"n": 0}

        def transport(_body):
            calls["n"] += 1
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "error": {"code": -32015, "message": "Transaction version (1) is not supported"},
                }
            ).encode()

        rpc = client(transport)
        with self.assertRaises(RpcMethodError) as caught:
            rpc.call("getTransaction", ["sig", {}])
        self.assertEqual(caught.exception.code, -32015)
        self.assertEqual(calls["n"], 1)

    def test_budget_stops_before_the_request(self):
        calls = {"n": 0}

        def transport(_body):
            calls["n"] += 1
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": 1}).encode()

        rpc = client(transport, budget=1)
        rpc.call("getAccountInfo", ["a"])
        with self.assertRaises(BudgetExceeded):
            rpc.call("getAccountInfo", ["b"])
        self.assertEqual(calls["n"], 1)
        self.assertEqual(rpc.used, 1)

    def test_retries_exhausted_do_not_leak_the_url(self):
        def transport(_body):
            raise urllib.error.HTTPError(
                "https://rpc.example/secret-path?api-key=supersecret",
                429,
                "no",
                hdrs=None,
                fp=io.BytesIO(b""),
            )

        rpc = client(transport, retries=1)
        with self.assertRaises(RpcRetriesExhausted) as caught:
            rpc.call("getTokenSupply", ["m"])
        text = str(caught.exception)
        self.assertNotIn("supersecret", text)
        self.assertNotIn("secret-path", text)
        self.assertNotIn("api-key=supersecret", rpc.redact("https://x?api-key=supersecret"))

    def test_disallowed_method(self):
        rpc = client(lambda _b: b"{}")
        with self.assertRaises(ValueError):
            rpc.call("getTransactionsForAddress", [])


if __name__ == "__main__":
    unittest.main()
