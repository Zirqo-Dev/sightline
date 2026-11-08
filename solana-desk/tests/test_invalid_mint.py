"""Malformed scan mints are not sent to RugCheck or Jupiter.

Desk-data 64fbb37 run 20261105T1930 has one tasks.csv mint of length 42 that
contains the character 0. That cell is not copied. The stand-in has the same
defect. A string of 44 ones is base58 and 44 characters, and it decodes to
44 zero bytes, so length alone is not an address.
"""
import tempfile
import unittest

from desk import run
from tests.decision_market import _b58_32

# Length 42 and contains 0, which is not in the base58 alphabet.
BAD = "Mint" + ("0" * 38)
# Base58, 44 characters, 44 zero bytes. Not a 32-byte address.
COARSE = "1" * 44
VALID = run.SOL


def _b58(raw):
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw, "big")
    chars = []
    while n:
        n, rem = divmod(n, 58)
        chars.append(alphabet[rem])
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return ("1" * pad) + ("".join(reversed(chars)) or "1")


class SolanaAddress(unittest.TestCase):
    def test_decoded_length_not_character_length(self):
        self.assertEqual(len(BAD), 42)
        self.assertIn("0", BAD)
        self.assertFalse(run.solana_address(BAD))
        self.assertEqual(len(COARSE), 44)
        self.assertTrue(set(COARSE) <= set(run._B58))
        self.assertFalse(run.solana_address(COARSE))
        self.assertTrue(run.solana_address(VALID))
        self.assertTrue(run.solana_address("1" * 32))
        self.assertFalse(run.solana_address(_b58(b"\x11" * 31)))
        self.assertTrue(run.solana_address(_b58_32(b"\x02" + (b"\x11" * 31))))
        self.assertFalse(run.solana_address(""))
        self.assertFalse(run.solana_address(None))

    def _get(self, leads, calls):
        now = 1_793_500_000

        def get(url, headers=None):
            calls.append(url)
            if BAD in url or COARSE in url:
                raise AssertionError("invalid mint was requested")
            if "token-profiles" in url:
                return [{"chainId": "solana", "tokenAddress": m} for m in leads]
            if "token-boosts" in url:
                return []
            if "/latest/dex/tokens/" in url:
                self.assertIn(VALID, url)
                return {"pairs": [dict(
                    chainId="solana", dexId="raydium", pairAddress="P",
                    quoteToken={"address": VALID},
                    baseToken={"address": VALID, "symbol": "SOL"},
                    liquidity={"quote": 500, "usd": 1e5},
                    pairCreatedAt=(now - 3600) * 1000,
                    txns={"h1": {"sells": 5}},
                )]}
            if "rugcheck" in url:
                self.assertIn(VALID, url)
                return {
                    "markets": [{"marketType": "raydium", "pubkey": "P", "lp": {"lpLockedPct": 100}}],
                    "topHolders": [], "score_normalised": 1, "creatorBalance": 0,
                    "token": {"supply": 1},
                }
            if "jup.ag" in url:
                self.assertIn(VALID, url)
                return {"outAmount": "1000000", "priceImpactPct": "0.001", "routePlan": [1]}
            raise AssertionError(url)

        return get, now

    def test_invalid_mint_is_not_requested_and_does_not_pass(self):
        import csv
        import os
        calls = []
        get, now = self._get([BAD, COARSE, VALID], calls)
        d = tempfile.mkdtemp()
        run.scan(d, get, key=None, now=now, mode="scan", sleep=lambda s: None)
        self.assertTrue(any("rugcheck" in url and VALID in url for url in calls))
        self.assertTrue(any("jup.ag" in url and VALID in url for url in calls))
        self.assertFalse(any(BAD in url or COARSE in url for url in calls))
        with open(os.path.join(d, "tasks.csv"), newline="") as fh:
            rows = list(csv.DictReader(fh))
        invalid = [row for row in rows if row["reason"] == "invalid_mint"]
        self.assertEqual(len(invalid), 2)
        for row in invalid:
            self.assertEqual(row["result"], "skip")
            self.assertEqual(row["skip_type"], "data")
            self.assertEqual(row["fail_reason"], "skip:invalid_mint")
            self.assertFalse(row["fail_reason"].startswith(("rugcheck:", "jupiter:", "dexscreener:")))
        self.assertTrue(any(row["mint"] == VALID and row["result"] == "pass" for row in rows))

    def test_invalid_mint_is_not_paper_traded(self):
        import csv
        import os
        calls = []
        get, now = self._get([BAD], calls)
        d = tempfile.mkdtemp()
        h = run.scan(d, get, key=None, now=now, mode="fill", sleep=lambda s: None)
        self.assertFalse(any("rugcheck" in url or "jup.ag" in url or "/latest/dex/tokens/" in url for url in calls))
        self.assertEqual(h["fill"]["open"]["F"], 0)
        trades = os.path.join(d, "trades.csv")
        if os.path.exists(trades):
            with open(trades, newline="") as fh:
                self.assertEqual([row for row in csv.DictReader(fh) if row["action"] == "buy"], [])

    def test_valid_mint_can_still_be_selected(self):
        calls = []
        get, now = self._get([VALID], calls)
        d = tempfile.mkdtemp()
        h = run.scan(d, get, key=None, now=now, mode="fill", sleep=lambda s: None)
        self.assertTrue(any("rugcheck" in url for url in calls))
        self.assertTrue(any("jup.ag" in url for url in calls))
        self.assertEqual(h["fill"]["open"]["F"], 1)
