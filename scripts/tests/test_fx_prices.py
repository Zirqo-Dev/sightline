"""Coin prices are stored from a fetch body. No network."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from scripts.fx_prices import document, prices_from_body


class FxPricesTest(unittest.TestCase):
    def test_maps_symbols_and_skips_a_missing_coin(self):
        body = json.dumps({
            "ethereum": {"usd": 3200.5},
            "solana": {"usd": 140},
            "cardano": {"usd": 0.4},
        })
        prices = prices_from_body(body)
        self.assertEqual(prices["ETH"], 3200.5)
        self.assertEqual(prices["SOL"], 140)
        self.assertEqual(prices["ADA"], 0.4)
        self.assertNotIn("HYPE", prices)

    def test_document_names_the_source_and_a_utc_time(self):
        doc = document({"ETH": 1}, now=__import__("datetime").datetime(2026, 11, 1, 23, 0, tzinfo=__import__("datetime").timezone.utc))
        self.assertEqual(doc["source"], "CoinGecko")
        self.assertEqual(doc["asof"], "2026-11-01T23:00:00Z")
        self.assertEqual(doc["prices"]["ETH"], 1)


if __name__ == "__main__":
    unittest.main()
