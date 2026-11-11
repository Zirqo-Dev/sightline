"""Mocked keyless responses for --dry-run. No network."""

import json
from races.http import HTTPResult

WSOL = "So11111111111111111111111111111111111111112"
MINT = "RaceMint11111111111111111111111111111111"
POOL = "RacePool11111111111111111111111111111111"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _token(mint, symbol, *, meme=False, age="2026-01-01T00:00:00Z", mcap=30_000_000, fdv=40_000_000, verified=True, tags=None):
    base_tags = list(tags or [])
    if meme and "meme" not in base_tags:
        base_tags.append("meme")
    return {
        "id": mint,
        "symbol": symbol,
        "isVerified": verified,
        "mcap": mcap,
        "fdv": fdv,
        "liquidity": 2_000_000,
        "organicScore": 70,
        "tags": base_tags,
        "firstPool": {"id": "pool", "createdAt": age},
        "stats24h": {
            "priceChange": 20,
            "volumeChange": 150,
            "buyVolume": 1000,
            "buyOrganicVolume": 80,
        },
        "stats6h": {"priceChange": 2},
    }


TOKENS = [_token(f"RaceTok{i:02d}111111111111111111111111111111", f"T{i}", meme=(i % 2 == 0)) for i in range(8)]
TOKENS.append(_token("RaceYoung11111111111111111111111111111111", "YOUNG", age="2026-09-12T00:00:00Z"))


class DryTransport:
    def request(self, method, url, headers=None, body=None):
        if "geckoterminal.com" in url and "pumpswap/pools" in url and "page=1" in url:
            return HTTPResult(200, _gecko(), None)
        if "geckoterminal.com" in url:
            return HTTPResult(200, {"data": [], "included": []}, None)
        if "dexscreener.com/latest/dex/pairs/" in url:
            return HTTPResult(200, {"pairs": [_pair()]}, None)
        if "dexscreener.com/latest/dex/tokens/" in url:
            return HTTPResult(200, {"pairs": [_pair()]}, None)
        if "rugcheck.xyz" in url:
            return HTTPResult(200, {"score_normalised": 11}, None)
        if "lite-api.jup.ag/swap/v1/quote" in url:
            return HTTPResult(200, _quote(url), None)
        if "lite-api.jup.ag/tokens/v2/" in url:
            return HTTPResult(200, TOKENS, None)
        if "api.coingecko.com/api/v3/coins/markets" in url:
            return HTTPResult(200, [{"id": "bonk", "symbol": "bonk"}], None)
        if "api.coingecko.com/api/v3/coins/bonk" in url:
            return HTTPResult(200, {
                "id": "bonk",
                "symbol": "bonk",
                "platforms": {"solana": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"},
                "tickers": [{"market": {"name": "Binance"}, "target": "USDT", "base": "BONK"}],
            }, None)
        if "api.coingecko.com" in url:
            return HTTPResult(200, {"id": "cross"}, None)
        if "api.binance.com/api/v3/klines" in url:
            return HTTPResult(200, [[1609459200000, "1", "1", "1", "1", "1"]], None)
        if "api-manager.upbit.com" in url:
            return HTTPResult(200, {"success": True, "data": {"notices": []}}, None)
        if method == "POST" and body:
            return HTTPResult(200, {"jsonrpc": "2.0", "result": _rpc(json.loads(body.decode()))}, None)
        return HTTPResult(404, None, None)


def _gecko():
    return {
        "data": [{
            "id": "solana_" + POOL,
            "attributes": {
                "address": POOL,
                "name": "MEME / SOL",
                "pool_created_at": "2026-11-11T09:12:00Z",
                "transactions": {"h1": {"buys": 4, "sells": 2}},
            },
            "relationships": {
                "base_token": {"data": {"id": "solana_" + MINT}},
                "quote_token": {"data": {"id": "solana_" + WSOL}},
                "dex": {"data": {"id": "pumpswap"}},
            },
        }],
        "included": [
            {"id": "solana_" + MINT, "attributes": {"address": MINT, "symbol": "MEME", "decimals": 6}},
            {"id": "solana_" + WSOL, "attributes": {"address": WSOL, "symbol": "SOL", "decimals": 9}},
        ],
    }


def _pair():
    return {
        "pairAddress": POOL,
        "baseToken": {"address": MINT, "symbol": "MEME"},
        "quoteToken": {"address": WSOL, "symbol": "SOL"},
        "liquidity": {"base": 1000000, "quote": 80, "usd": 12000},
        "txns": {"h1": {"buys": 4, "sells": 2}},
    }


def _quote(url):
    if f"outputMint={USDC}" in url or "outputMint=EPj" in url:
        return {"inAmount": "500000000", "outAmount": "70000000", "priceImpactPct": "0.001",
                "inputMint": WSOL, "outputMint": USDC}
    if "outputMint=" + WSOL in url or f"outputMint={WSOL}" in url:
        return {"inAmount": "1000000", "outAmount": "400000000", "priceImpactPct": "0.01",
                "inputMint": MINT, "outputMint": WSOL}
    return {"inAmount": "500000000", "outAmount": "1000000", "priceImpactPct": "0.01",
            "inputMint": WSOL, "outputMint": MINT}


def _rpc(payload):
    method = payload.get("method")
    if method == "getAccountInfo":
        return {"value": {"data": {"parsed": {"info": {
            "mintAuthority": None, "freezeAuthority": None, "decimals": 6, "supply": "1000000000",
        }}}}}
    if method == "getTokenSupply":
        return {"value": {"amount": "1000000000", "decimals": 6}}
    if method == "getTokenLargestAccounts":
        return {"value": [{"address": "RaceHold11111111111111111111111111111111", "amount": "1000"}]}
    if method == "getMultipleAccounts":
        return {"value": [{"data": {"parsed": {"info": {"owner": "RaceOwn111111111111111111111111111111111", "tokenAmount": {"amount": "1000"}}}}}]}
    return {"value": None}
