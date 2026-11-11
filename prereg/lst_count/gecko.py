"""GeckoTerminal free pool list, used once at freeze. Not a rate source."""

from decimal import Decimal

MIN_RESERVE_USD = Decimal("5000000")


def pools_url(mint):
    return "https://api.geckoterminal.com/api/v2/networks/solana/tokens/%s/pools?page=1" % mint


def largest_sol_pool(payload, lst_mint, sol_mint):
    """Largest LST/SOL or LST/WSOL pool on page 1, by reserve_in_usd."""
    if not isinstance(payload, dict):
        return None
    lst_id = "solana_" + lst_mint
    sol_id = "solana_" + sol_mint
    best = None
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
        reserve = Decimal(str(raw))
        if best is None or reserve > best["reserve_in_usd"]:
            best = {
                "reserve_in_usd": reserve,
                "name": attrs.get("name"),
                "address": attrs.get("address"),
            }
    return best


def reserve_ok(pool):
    return pool is not None and pool["reserve_in_usd"] >= MIN_RESERVE_USD
