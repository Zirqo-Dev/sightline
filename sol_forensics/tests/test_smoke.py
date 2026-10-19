"""The Actions secret check fails closed and never prints the endpoint."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from sol_forensics.rpc import RpcClient
from sol_forensics.smoke_rpc import QBITS_MINT, redact, smoke


SECRET = "https://rpc.example/helius?api-key=supersecret"


def scripted(payloads):
    calls = []

    def transport(body):
        sent = json.loads(body.decode())
        calls.append(sent)
        return json.dumps(payloads.pop(0)).encode()

    transport.calls = calls
    return transport


class SmokeTests(unittest.TestCase):
    def test_empty_secret_fails_clearly(self):
        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = smoke({})
        self.assertEqual(code, 1)
        self.assertIn("SOLANA_RPC_URL secret is empty", err.getvalue())

    def test_prints_slot_and_supply_and_hides_the_url(self):
        transport = scripted(
            [
                {"jsonrpc": "2.0", "id": 1, "result": 458581711},
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "value": {
                            "amount": "1000000000000000",
                            "decimals": 6,
                            "uiAmountString": "1000000000",
                        }
                    },
                },
            ]
        )
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = smoke({"SOLANA_RPC_URL": SECRET}, transport)
        self.assertEqual(code, 0)
        text = out.getvalue() + err.getvalue()
        self.assertIn("smoke: pass", text)
        self.assertIn("slot: 458581711", text)
        self.assertIn("qbits_supply_amount: 1000000000000000", text)
        self.assertIn("qbits_supply_ui: 1000000000", text)
        self.assertNotIn("supersecret", text)
        self.assertNotIn("rpc.example", text)
        self.assertEqual(transport.calls[0]["method"], "getSlot")
        self.assertEqual(transport.calls[1]["method"], "getTokenSupply")
        self.assertEqual(transport.calls[1]["params"][0], QBITS_MINT)

    def test_rpc_error_is_redacted(self):
        def transport(_body):
            raise OSError("failed calling " + SECRET)

        err = io.StringIO()
        with redirect_stderr(err), redirect_stdout(io.StringIO()):
            code = smoke({"SOLANA_RPC_URL": SECRET}, transport)
        self.assertEqual(code, 1)
        self.assertIn("smoke: fail", err.getvalue())
        self.assertNotIn("supersecret", err.getvalue())
        self.assertNotIn(SECRET, err.getvalue())
        self.assertIn("<rpc>", err.getvalue())

    def test_analysis_client_still_rejects_get_slot(self):
        rpc = RpcClient("https://rpc.example", budget=1, max_rps=1, transport=lambda _b: b"{}")
        with self.assertRaises(ValueError):
            rpc.call("getSlot", [])

    def test_redact_query_key(self):
        self.assertEqual(
            redact("see https://x?api-key=supersecret&z=1", ""),
            "see https://x?api-key=<redacted>&z=1",
        )


if __name__ == "__main__":
    unittest.main()
