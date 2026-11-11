"""GeckoTerminal free pool list, used once at freeze. Not a rate source.

§1b addendum: liquidity is the sum of reserve_in_usd over every page-1
LST/SOL and LST/WSOL pool, and that sum must be at least $5,000,000.
"""

from decimal import Decimal

MIN_RESERVE_USD = Decimal("5000000")


def pools_url(mint):
    return "https://api.geckoterminal.com/api/v2/networks/solana/tokens/%s/pools?page=1" % mint


def page1_sol_pools(payload, lst_mint, sol_mint):
    """Every LST/SOL or LST/WSOL pool on page 1.

    GeckoTerminal quotes both SOL and WSOL against the WSOL mint. Each row
    carries address, pair, and reserve_in_usd so the freeze log can list them.
    """
    if not isinstance(payload, dict):
        return []
    lst_id = "solana_" + lst_mint
    sol_id = "solana_" + sol_mint
    found = []
    for pool in payload.get("data") or []:
        if not isinstance(pool, dict):
            continue
        rel = pool.get("relationships") or {}
        base = ((rel.get("base_token") or {}).get("data") or {}).get("id")
        quote = ((rel.get("quote_token") or {}).get("data") or {}).get("id")
        if {base, quote} != {lst_id, sol_id}:
            continue
        attrs = pool.get("attributes") or {}
        raw = attrs.get("reserve_in_usd")
        if raw is None or raw == "":
            continue
        found.append({
            "address": attrs.get("address"),
            "pair": attrs.get("name"),
            "reserve_in_usd": Decimal(str(raw)),
        })
    return found


def reserve_total(pools):
    return sum((pool["reserve_in_usd"] for pool in pools), Decimal(0))


def reserve_ok(pools):
    return bool(pools) and reserve_total(pools) >= MIN_RESERVE_USD
