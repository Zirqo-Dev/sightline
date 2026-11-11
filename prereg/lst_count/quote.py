"""Jupiter lite buy and sell quotes. No API key. Default routing."""

import hashlib
import json
from fractions import Fraction
from urllib.parse import urlencode

HOST = "lite-api.jup.ag"
QUOTE_PATH = "/swap/v1/quote"
SLIPPAGE_BPS = 50
BUY_LAMPORTS = 500_000_000


def quote_url(input_mint, output_mint, amount):
    query = urlencode((
        ("inputMint", input_mint),
        ("outputMint", output_mint),
        ("amount", str(int(amount))),
        ("slippageBps", str(SLIPPAGE_BPS)),
        ("swapMode", "ExactIn"),
    ))
    return "https://%s%s?%s" % (HOST, QUOTE_PATH, query)


def buy_url(sol_mint, lst_mint):
    return quote_url(sol_mint, lst_mint, BUY_LAMPORTS)


def sell_amount(decimals):
    """0.5 LST, in native units."""
    return 10 ** int(decimals) // 2


def sell_url(sol_mint, lst_mint, decimals):
    return quote_url(lst_mint, sol_mint, sell_amount(decimals))


def sol_per_lst(out_amount, decimals):
    """P = 0.5 / (outAmount / 10^decimals)."""
    out_amount = int(out_amount)
    if out_amount <= 0:
        raise ValueError("outAmount")
    return Fraction(10 ** int(decimals), 2 * out_amount)


def parse_quote(raw):
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    out_amount = body.get("outAmount")
    if not str(out_amount or "").isdigit() or int(out_amount) <= 0:
        return None
    labels = []
    for leg in body.get("routePlan") or []:
        if not isinstance(leg, dict):
            continue
        label = (leg.get("swapInfo") or {}).get("label")
        if label:
            labels.append(str(label))
    impact = body.get("priceImpactPct")
    return {
        "outAmount": str(out_amount),
        "priceImpactPct": impact if isinstance(impact, str) else json.dumps(impact),
        "route_labels": labels,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
    }
