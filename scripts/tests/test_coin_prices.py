"""USD quote file for NFT floors. Offline. No network."""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import coin_prices as cp  # noqa: E402


class CoinPrices(unittest.TestCase):
    def test_coingecko_payload_becomes_symbols_with_stables(self):
        doc = cp.document(cp.quotes_from_coingecko({
            "ethereum": {"usd": 2267.37},
            "solana": {"usd": 93.85},
            "cardano": {"usd": 0.192588},
            "hyperliquid": {"usd": 87.41},
            "nope": {"usd": -1},
        }), "coingecko", asof="2026-11-02T00:20:00Z")
        self.assertEqual(doc["source"], "coingecko")
        self.assertEqual(doc["asof"], "2026-11-02T00:20:00Z")
        self.assertEqual(doc["quotes"]["ETH"], 2267.37)
        self.assertEqual(doc["quotes"]["USD"], 1.0)
        self.assertEqual(doc["quotes"]["USDC"], 1.0)
        self.assertNotIn("NOPE", doc["quotes"])

    def test_refresh_keeps_a_stored_file_when_the_fetch_cannot_run(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "prices.json")
            stored = cp.document({"ETH": 100.0, "SOL": 50.0, "ADA": 0.2, "HYPE": 10.0}, "coingecko", asof="2026-11-01T00:00:00Z")
            cp.write_doc(path, stored)
            real = cp.fetch_coingecko
            def boom(timeout=15):
                raise OSError("offline")
            cp.fetch_coingecko = boom
            try:
                doc, how = cp.refresh(path, timeout=0.1)
            finally:
                cp.fetch_coingecko = real
            self.assertEqual(how, "kept")
            self.assertEqual(doc["quotes"]["ETH"], 100.0)


if __name__ == "__main__":
    unittest.main()
