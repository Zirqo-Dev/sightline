"""Cardano price and liquidity. GeckoTerminal, then DexScreener. No API key.

Same public sources as scripts/buzz_outcomes.py. Price is ADA. Liquidity is the
pool ADA side. A caller paces the requests.
"""
import json
import urllib.error
import urllib.request

GT = "https://api.geckoterminal.com/api/v2"
DS = "https://api.dexscreener.com"


def _num(value):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")) or num <= 0:
        return None
    return num


def _gt_quote(pools, unit):
    if not pools:
        return None

    def reserve(pool):
        try:
            return float((pool.get("attributes") or {}).get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            return 0.0

    best = max(pools, key=reserve)
    attr = best.get("attributes") or {}
    qusd = _num(attr.get("quote_token_price_usd"))
    res = _num(attr.get("reserve_in_usd"))
    base_id = (((best.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
    if str(base_id).endswith(unit):
        price = _num(attr.get("base_token_price_quote_token"))
    else:
        price = _num(attr.get("quote_token_price_base_token"))
        qusd = _num(attr.get("base_token_price_usd")) or qusd
    if not price or not qusd or not res:
        return None
    return price, res / 2 / qusd


def _pairs(doc):
    if isinstance(doc, list):
        return [p for p in doc if isinstance(p, dict)]
    if isinstance(doc, dict):
        return [p for p in (doc.get("pairs") or []) if isinstance(p, dict)]
    return []


def _get(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": "sightline-whale-roster", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read(1_000_000).decode())


def fetch_cardano(unit, get=None, timeout=20):
    """{answered, price, liquidity, reason, source}. get(url) is injectable."""
    getter = get or (lambda url: _get(url, timeout))
    try:
        doc = getter(GT + "/networks/cardano/tokens/" + unit + "/pools?page=1")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return {"answered": False, "price": None, "liquidity": None, "reason": "market unread", "source": None}
        doc = None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {"answered": False, "price": None, "liquidity": None, "reason": "market unread", "source": None}
    pools = (doc or {}).get("data") if isinstance(doc, dict) else None
    quote = _gt_quote(pools or [], unit)
    if quote:
        price, ada_side = quote
        return {"answered": True, "price": price, "liquidity": ada_side, "reason": None, "source": "geckoterminal"}
    try:
        doc = getter(DS + "/tokens/v1/cardano/" + unit)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"answered": True, "price": None, "liquidity": None, "reason": "no pair", "source": None}
        return {"answered": False, "price": None, "liquidity": None, "reason": "market unread", "source": None}
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return {"answered": False, "price": None, "liquidity": None, "reason": "market unread", "source": None}
    pairs = _pairs(doc)
    if not pairs:
        return {"answered": True, "price": None, "liquidity": None, "reason": "no pair", "source": None}
    best = max(pairs, key=lambda p: float(((p.get("liquidity") or {}).get("quote")) or 0))
    price = _num(best.get("priceNative"))
    liq = _num((best.get("liquidity") or {}).get("quote"))
    return {
        "answered": True,
        "price": price,
        "liquidity": liq,
        "reason": None if price is not None else "no ada price",
        "source": "dexscreener",
    }
