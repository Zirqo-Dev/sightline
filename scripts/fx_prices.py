#!/usr/bin/env python3
"""Store native-coin USD prices for the NFT watchlist.

The browser reads dashboard/prices.json and never calls a price host.
Build and the desk scan run this. A failed fetch leaves the previous file.
"""
import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone

# CoinGecko ids. The page multiplies a floor by prices[symbol].
IDS = {
    "ADA": "cardano",
    "SOL": "solana",
    "ETH": "ethereum",
    "HYPE": "hyperliquid",
    "POL": "polygon-ecosystem-token",
    "MATIC": "polygon-ecosystem-token",
    "RON": "ronin",
}
SOURCE = "CoinGecko"
URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids="
    + ",".join(sorted(set(IDS.values())))
    + "&vs_currencies=usd"
)


def prices_from_body(body):
    data = json.loads(body)
    out = {}
    for symbol, coin_id in IDS.items():
        row = data.get(coin_id) or {}
        usd = row.get("usd")
        if isinstance(usd, (int, float)) and usd > 0:
            out[symbol] = float(usd)
    return out


def fetch_prices(url=URL, timeout=20):
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "sightline-build"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return prices_from_body(resp.read().decode("utf-8"))


def document(prices, now=None):
    stamp = now or datetime.now(timezone.utc)
    return {
        "asof": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": SOURCE,
        "vs": "usd",
        "note": "Fetched at build or scan time. The browser reads this file only.",
        "prices": prices,
    }


def write_prices(path, prices, now=None):
    doc = document(prices, now)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    return doc


def refresh(path):
    try:
        prices = fetch_prices()
    except Exception as exc:
        print(f"fx-prices: fetch failed ({exc}); keeping {path}")
        return None
    if "ETH" not in prices or "SOL" not in prices:
        print(f"fx-prices: response missing ETH or SOL; keeping {path}")
        return None
    doc = write_prices(path, prices)
    print(f"fx-prices: {doc['source']} {doc['asof']} ETH {prices.get('ETH')} SOL {prices.get('SOL')} ADA {prices.get('ADA')}")
    return doc


def main():
    parser = argparse.ArgumentParser(description="Write dashboard/prices.json")
    parser.add_argument("--out", default="dashboard/prices.json")
    args = parser.parse_args()
    refresh(args.out)


if __name__ == "__main__":
    main()
