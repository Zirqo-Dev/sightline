"""Binance vision fallback: S3 listing plus a real zip, and an HTML index is not a pass."""

import io
import json
import zipfile
from datetime import datetime, timezone

import unittest

from races.http import HTTPResult
from races.rules.r3 import _prove

MINT = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"
OPEN_MS = 1699920000000  # 2023-11-14, inside the 12-month window as of 2026-11-11


def _cfg():
    return {"wallets": {"R3": {
        "coingecko_coin": "https://api.coingecko.com/api/v3/coins/",
        "binance_klines": "https://api.binance.com/api/v3/klines",
        "binance_vision_list": "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
        "binance_vision": "https://data.binance.vision/",
    }}}


def _zip_bytes(open_time=OPEN_MS, stem="BONKUSDT-1d-2023-11"):
    # Same columns as a Binance spot 1d kline row. Open time is the first field.
    row = f"{open_time},0.00001234,0.00001300,0.00001100,0.00001250,1000000,1700006399999,12.5,100,500000,6.2,0"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(stem + ".csv", row + "\n")
    return buf.getvalue()


def _xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult>
<Contents><Key>data/spot/monthly/klines/BONKUSDT/1d/BONKUSDT-1d-2024-01.zip</Key></Contents>
<Contents><Key>data/spot/monthly/klines/BONKUSDT/1d/BONKUSDT-1d-2023-11.zip</Key></Contents>
</ListBucketResult>
"""


class _Transport:
    def __init__(self, listing, klines_open=None, zip_open=None):
        self.listing = listing
        self.klines_open = klines_open
        self.zip_open = OPEN_MS if zip_open is None else zip_open
        self.urls = []

    def request(self, method, url, headers=None, body=None):
        self.urls.append(url)
        if "api.coingecko.com/api/v3/coins/" in url:
            return HTTPResult(200, {
                "symbol": "bonk",
                "platforms": {"solana": MINT},
                "tickers": [{"market": {"name": "Binance"}, "target": "USDT", "base": "BONK"}],
            }, None)
        if "api.binance.com/api/v3/klines" in url:
            if self.klines_open is not None:
                return HTTPResult(200, [[self.klines_open, "1", "1", "1", "1", "1"]], None)
            return HTTPResult(451, {"code": 0, "msg": "Service unavailable from a restricted location."}, None)
        if "s3-ap-northeast-1.amazonaws.com/data.binance.vision" in url:
            if self.listing == "html":
                html = "<!DOCTYPE html><html><head><title>Binance Data Collection</title></head><body>index</body></html>"
                return HTTPResult(200, html, None, raw=html.encode())
            xml = _xml()
            return HTTPResult(200, xml, None, raw=xml.encode())
        if url.startswith("https://data.binance.vision/data/spot/monthly/klines/") and url.endswith(".zip"):
            raw = _zip_bytes(self.zip_open)
            return HTTPResult(200, None, None, raw=raw)
        return HTTPResult(404, None, None)


def _ctx(transport):
    logs = []
    ctx = type("Ctx", (), {})()
    ctx.cfg = _cfg()
    ctx.transport = transport
    ctx.log = lambda *args: logs.append(args)
    ctx.logs = logs
    return ctx


class Vision(unittest.TestCase):
    def test_451_then_vision_zip_proves_the_listing(self):
        transport = _Transport("xml")
        proved = _prove(_ctx(transport), _cfg()["wallets"]["R3"], "bonk", "bonk")
        self.assertIsNotNone(proved)
        self.assertEqual(proved["source"], "binance_vision")
        self.assertEqual(proved["mint"], MINT)
        self.assertEqual(proved["listed_on"], datetime.fromtimestamp(OPEN_MS / 1000, timezone.utc).strftime("%Y-%m-%d"))
        self.assertTrue(any(url.startswith("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=") for url in transport.urls))
        self.assertTrue(any(url.endswith("BONKUSDT-1d-2023-11.zip") for url in transport.urls))
        self.assertFalse(any(url.startswith("https://data.binance.vision?") or url.startswith("https://data.binance.vision/?") for url in transport.urls))

    def test_html_index_is_excluded(self):
        transport = _Transport("html")
        ctx = _ctx(transport)
        proved = _prove(ctx, _cfg()["wallets"]["R3"], "bonk", "bonk")
        self.assertIsNone(proved)
        actions = [row[2] for row in ctx.logs if len(row) > 2 and isinstance(row[2], dict)]
        hit = [row for row in actions if row.get("action") == "excluded"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["reason"], "vision_html")
        self.assertNotIn("winner", json.dumps(hit).lower())


class ListingCutoff(unittest.TestCase):
    def test_microsecond_zip_2025_01_19_is_accepted(self):
        # TRUMPUSDT vision open time is microseconds from 2025-01-01.
        transport = _Transport("xml", zip_open=1737244800000000)
        proved = _prove(_ctx(transport), _cfg()["wallets"]["R3"], "bonk", "bonk")
        self.assertIsNotNone(proved)
        self.assertEqual(proved["listed_on"], "2025-01-19")
        self.assertEqual(proved["source"], "binance_vision")

    def test_ms_2025_11_12_is_listing_too_new(self):
        opened = int(datetime(2025, 11, 12, tzinfo=timezone.utc).timestamp() * 1000)
        transport = _Transport("xml", klines_open=opened)
        ctx = _ctx(transport)
        proved = _prove(ctx, _cfg()["wallets"]["R3"], "bonk", "bonk")
        self.assertIsNone(proved)
        reasons = [row[2].get("reason") for row in ctx.logs if len(row) > 2 and isinstance(row[2], dict)]
        self.assertIn("listing_too_new", reasons)

    def test_2026_03_01_is_listing_too_new(self):
        opened = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
        transport = _Transport("xml", klines_open=opened)
        ctx = _ctx(transport)
        proved = _prove(ctx, _cfg()["wallets"]["R3"], "bonk", "bonk")
        self.assertIsNone(proved)
        reasons = [row[2].get("reason") for row in ctx.logs if len(row) > 2 and isinstance(row[2], dict)]
        self.assertIn("listing_too_new", reasons)
