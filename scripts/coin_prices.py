#!/usr/bin/env python3
"""USD quotes for native NFT floors. Fetched at build or scan time.

The browser reads the JSON this writes. It must not call a price host itself.
CoinGecko's public simple-price endpoint is the source. A previous file is
left in place when the fetch fails, so a build still has a stored quote.
"""
import argparse
import json
import os
import urllib.request
from datetime import datetime, timezone

IDS = (
    ("ethereum", "ETH"),
    ("solana", "SOL"),
    ("cardano", "ADA"),
    ("hyperliquid", "HYPE"),
)
STABLES = {"USD": 1.0, "USDC": 1.0, "USDT": 1.0}
COINGECKO = (
    "https://api.coingecko.com/api/v3/simple/price?ids="
    + ",".join(cid for cid, _ in IDS)
    + "&vs_currencies=usd"
)


def quotes_from_coingecko(payload):
    """Map a CoinGecko simple-price body to symbols. Non-positive quotes drop out."""
    out = {}
    if not isinstance(payload, dict):
        return out
    for cid, symbol in IDS:
        row = payload.get(cid)
        usd = row.get("usd") if isinstance(row, dict) else None
        if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd > 0:
            out[symbol] = float(usd)
    return out


def document(quotes, source, asof=None):
    merged = dict(STABLES)
    for symbol, usd in (quotes or {}).items():
        if isinstance(usd, (int, float)) and not isinstance(usd, bool) and usd > 0:
            merged[str(symbol).upper()] = float(usd)
    return {
        "schema_version": 1,
        "asof": asof or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source,
        "quotes": merged,
    }


def fetch_coingecko(timeout=15):
    req = urllib.request.Request(COINGECKO, headers={"accept": "application/json", "user-agent": "sightline-prices"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    quotes = quotes_from_coingecko(payload)
    if len(quotes) < 3:
        raise RuntimeError("price source returned too few quotes")
    return document(quotes, "coingecko")


def write_doc(path, doc):
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def refresh(path, timeout=15):
    """Write a fresh file. On failure, keep an existing file and return its doc.

    Raises if there is no file to keep.
    """
    try:
        doc = fetch_coingecko(timeout=timeout)
        write_doc(path, doc)
        return doc, "fetched"
    except Exception as exc:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh), "kept"
        raise RuntimeError("coin prices unavailable and %s is missing (%s)" % (path, exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Store USD coin quotes for the NFT watchlist")
    parser.add_argument("--out", default="prices.json")
    parser.add_argument("--timeout", type=float, default=15)
    args = parser.parse_args(argv)
    try:
        doc, how = refresh(args.out, timeout=args.timeout)
    except RuntimeError as exc:
        print(exc)
        return 1
    symbols = " ".join("%s %s" % (k, doc["quotes"][k]) for k in ("ETH", "SOL", "ADA", "HYPE") if k in doc.get("quotes", {}))
    print("prices %s %s %s" % (how, doc.get("asof"), symbols))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
