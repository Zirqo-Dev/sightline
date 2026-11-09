"""NH-04 round-trip cost and the frozen universe rule. No network and no fees baked in."""
from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import date, datetime, timezone

# Frozen in PR1. num_owners is a stock of holders, not a 1-day or 7-day trend.
PAGE_ENDPOINT = "/api/v2/collections"
ORDER_BY = "num_owners"
PAGE_LIMIT = 100
PAGE_DEPTH = 1
CHAINS = ("ethereum", "base")
CAIP = {"ethereum": "eip155:1", "base": "eip155:8453"}
FLOOR_USD = 250
GAS_RATIO = 0.02
MIN_SALES = 10
MIN_BUYERS = 6
MIN_LISTINGS = 5
MIN_BIDDERS = 3
BID_BAND = 0.85
MAX_BUYER_SHARE = 0.20
MIN_MINT_DAYS = 14
LISTING_LIMIT = 20
OFFER_PAGE_LIMIT = 200
OFFER_MAX_PAGES = 3
EVENT_PAGE_LIMIT = 100
EVENT_MAX_PAGES = 2
# Two Seaport basic-order fulfillments. The RPC supplies the wei price.
ROUND_TRIP_GAS_UNITS = 500_000
# OpenSea Shared Storefront. One contract, many collections, so it has no single id.
SHARED_STOREFRONTS = frozenset({"0x495f947276749ce646f68ac8c248420045cb7b5e"})
GAS_RPC = {
    "ethereum": "https://ethereum-rpc.publicnode.com",
    "base": "https://base-rpc.publicnode.com",
}
MAX_CALLS = 520
HOURLY_LIMIT = 600
DATA_BRANCH = "nft-data"
AVOIDS_BRANCH = "desk-data"
RULE = "GET /api/v2/collections order_by=num_owners limit=100 depth=1 chains=ethereum,base"
ATTRIBUTION = "Market data from OpenSea (https://opensea.io). Derived metrics only."
_ADDR = re.compile(r"0x[0-9a-fA-F]{40}")
_HASH = re.compile(r"0x[0-9a-fA-F]{64}")


def upper_bound_calls(passers=20, pending=20):
    """Worst-case OpenSea reads for one hourly run at the frozen page depth.

    List pages, one listings read per candidate, one detail read for ETH/USD,
    then fee, offer, and sale pages for floor-capped passers, plus a fill read
    for each still-open unit. Offer and event pages are the caps, not the usual case.
    """
    candidates = len(CHAINS) * PAGE_LIMIT * PAGE_DEPTH
    return (len(CHAINS) * PAGE_DEPTH + 1 + candidates
            + passers * (1 + OFFER_MAX_PAGES + EVENT_MAX_PAGES)
            + pending * (1 + 1 + OFFER_MAX_PAGES))


def asset_id(chain_slug, address):
    addr = (address or "").lower()
    if chain_slug not in CAIP or not re.fullmatch(r"0x[0-9a-f]{40}", addr):
        raise ValueError("bad contract")
    return f"nft_collection:{CAIP[chain_slug]}:{addr}"


def parse_asset_id(value):
    parts = (value or "").split(":")
    if len(parts) != 4 or parts[0] != "nft_collection" or parts[1] != "eip155":
        raise ValueError("bad id")
    caip = "eip155:" + parts[2]
    if caip not in CAIP.values() or not re.fullmatch(r"0x[0-9a-f]{40}", parts[3]):
        raise ValueError("bad id")
    return {"asset_type": "nft_collection", "chain": caip, "contract": parts[3]}


def fee_rates(fees):
    """Marketplace rate is the sum of required fees. Creator rate is the rest.

    OpenSea's `fee` field is a percent (2.5 means 2.5%). Optional creator fees
    are still charged here. A missing list is unknown, not zero.
    """
    if not fees:
        return None
    mp = creator = 0.0
    for fee in fees:
        if "fee" not in fee or "required" not in fee:
            return None
        rate = float(fee["fee"]) / 100.0
        if fee["required"]:
            mp += rate
        else:
            creator += rate
    return mp, creator


def public_fees(fees):
    if not fees:
        return None
    out = []
    for fee in fees:
        if "fee" not in fee or "required" not in fee:
            return None
        out.append({"fee": fee["fee"], "required": bool(fee["required"])})
    return out


def _price(obj):
    cur = obj.get("current") if isinstance(obj, dict) and "current" in obj else obj
    if not isinstance(cur, dict):
        return None
    if str(cur.get("currency") or "").upper() not in ("ETH", "WETH"):
        return None
    decimals = int(cur.get("decimals") if cur.get("decimals") is not None else 18)
    return int(cur["value"]) / (10 ** decimals)


def parse_order(order, now, role="listing"):
    """A live ETH/WETH order, or None when it cannot be used. `valid` listings last 2h."""
    if not isinstance(order, dict) or str(order.get("status") or "ACTIVE").upper() != "ACTIVE":
        return None
    if order.get("flagged") or order.get("is_flagged"):
        return None
    params = ((order.get("protocol_data") or {}).get("parameters") or {})
    if int(params.get("orderType") or 0) in (2, 3):
        return None
    items = [it for it in (params.get("offer") or []) if isinstance(it, dict)]
    if len(items) > 1 or "bundle" in str(order.get("type") or "").lower():
        return None
    if int(order.get("remaining_quantity") or 1) > 1:
        return None
    price = _price(order.get("price"))
    if price is None:
        return None
    end = params.get("endTime")
    if end is None or str(end) == "":
        lasting = False
        expired = False
    else:
        end_i = int(end)
        expired = end_i <= now
        lasting = end_i >= now + 2 * 3600
    if expired:
        return None
    offerer = str(params.get("offerer") or "").lower()
    return {
        "price": price,
        "valid": lasting if role == "listing" else True,
        "hash": (order.get("order_hash") or "").lower(),
        "offerer": offerer,
        "item_types": [it.get("itemType") for it in items],
    }


def standard_of(parsed):
    types = set()
    for row in parsed:
        if row:
            types.update(row["item_types"])
    if 3 in types or 5 in types:
        return "erc1155"
    if 2 in types or 4 in types:
        return "erc721"
    return None


def floor_and_counts(parsed):
    live = [p for p in parsed if p]
    if not live:
        return None, 0
    return min(p["price"] for p in live), len(live)


def second_cheapest(parsed):
    prices = sorted(p["price"] for p in parsed if p and p["valid"])
    return prices[1] if len(prices) > 1 else None


def held_bid(prev, curr):
    """Highest bid whose order hash was on the previous read and this one."""
    old = {o["hash"] for o in prev if o.get("hash")}
    held = [o["price"] for o in curr if o.get("hash") and o["hash"] in old]
    return max(held) if held else None


def bidder_count(parsed):
    live = [p for p in parsed if p]
    if not live:
        return 0
    top = max(p["price"] for p in live)
    return len({p["offerer"] for p in live if p["offerer"] and p["price"] >= BID_BAND * top})


def round_trip(listing, buyer_fee, bid, mp, creator, gas):
    """Cost = (entry − exit) / entry. No held bid → 100%, and the unit stays."""
    fill = listing is not None
    if bid is None:
        return {"cost": 1.0, "unsellable": True, "fill": fill, "entry_eth": None, "exit_eth": None}
    if listing is None or mp is None or gas is None:
        return {"cost": None, "unsellable": False, "fill": fill, "entry_eth": None, "exit_eth": None}
    entry = listing + buyer_fee + gas
    exit_v = bid - bid * mp - bid * creator - gas
    if entry <= 0:
        return {"cost": None, "unsellable": False, "fill": False, "entry_eth": None, "exit_eth": None}
    return {
        "cost": (entry - exit_v) / entry,
        "unsellable": False,
        "fill": True,
        "entry_eth": entry,
        "exit_eth": exit_v,
    }


def mint_ok(created, now):
    if not created or len(str(created)) < 10:
        return False
    born = date.fromisoformat(str(created)[:10])
    day = datetime.fromtimestamp(now, timezone.utc).date()
    return (day - born).days >= MIN_MINT_DAYS


def rejection(obs):
    reasons = []
    if obs.get("n_contracts") != 1:
        reasons.append("contracts")
    if obs.get("shared"):
        reasons.append("shared")
    if obs.get("standard") != "erc721":
        reasons.append("standard")
    if not obs.get("mint_ok"):
        reasons.append("mint")
    if obs.get("disabled"):
        reasons.append("disabled")
    floor, usd, gas = obs.get("floor_eth"), obs.get("floor_usd"), obs.get("gas_eth")
    if floor is None or usd is None or usd > FLOOR_USD:
        reasons.append("floor")
    if floor is None or gas is None or not gas < GAS_RATIO * floor:
        reasons.append("gas")
    if obs.get("listings", 0) < MIN_LISTINGS:
        reasons.append("listings")
    if obs.get("sales", 0) < MIN_SALES:
        reasons.append("sales")
    if obs.get("buyers", 0) < MIN_BUYERS:
        reasons.append("buyers")
    if obs.get("bidders", 0) < MIN_BIDDERS:
        reasons.append("bidders")
    share = obs.get("top_share")
    if share is None or share > MAX_BUYER_SHARE:
        reasons.append("concentration")
    if obs.get("sales_truncated"):
        reasons.append("sales_truncated")
    return reasons


def sale_stats(events, after):
    buyers, n, complete = [], 0, True
    for ev in events or []:
        kind = ev.get("event_type", ev.get("eventType"))
        if kind not in (None, "sale"):
            continue
        if int(ev.get("event_timestamp") or ev.get("eventTimestamp") or 0) < after:
            continue
        n += 1
        raw = ev.get("buyer")
        if isinstance(raw, dict):
            raw = raw.get("address") or ""
        if isinstance(raw, str) and raw:
            buyers.append(raw.lower())
        else:
            complete = False
    distinct = len(set(buyers))
    share = None
    if n and complete and buyers:
        share = max(Counter(buyers).values()) / n
    return n, distinct, share


def gas_quote(current_wei, samples, chain, now):
    """Higher of the current wei price and the median of this hour plus the prior 7 days."""
    if current_wei is None:
        return None
    window = [s["wei"] for s in samples if s.get("chain") == chain and s.get("t", 0) >= now - 7 * 86400]
    window.append(current_wei)
    return max(float(current_wei), float(statistics.median(window)))


def wei_to_eth(wei):
    if wei is None:
        return None
    return ROUND_TRIP_GAS_UNITS * float(wei) / 1e18


def seasoned(dates, snapshot_day=None):
    """True once the collection has been snapshotted on 7 distinct UTC days."""
    days = {d if isinstance(d, date) else date.fromisoformat(d) for d in dates}
    if snapshot_day is not None:
        days.add(snapshot_day if isinstance(snapshot_day, date) else date.fromisoformat(snapshot_day))
    return len(days) >= 7


def _day_index(day, pilot_start):
    return (day - pilot_start).days + 1


def summarize(units, passes, pilot_start):
    def day_of(unit):
        return date.fromisoformat(unit["snapshot_t"][:10])

    def in_window(unit):
        if pilot_start is None:
            return True
        return _day_index(day_of(unit), pilot_start) <= 14

    window = [u for u in units if in_window(u)]
    costs = [u["cost"] for u in window if isinstance(u.get("cost"), (int, float))]
    seasoned_costs = [u["cost"] for u in window if u.get("seasoned") and isinstance(u.get("cost"), (int, float))]
    n = len(window)
    uns = sum(1 for u in window if u.get("unsellable"))
    pace, ready = None, False
    if pilot_start is not None:
        by_day = {}
        for day_s, ids in (passes or {}).items():
            num = _day_index(date.fromisoformat(day_s), pilot_start)
            if 8 <= num <= 14:
                by_day[num] = len(set(ids))
        if all(d in by_day for d in range(8, 15)):
            ready = True
            pace = statistics.median([by_day[d] for d in range(8, 15)])
    last = None
    if passes:
        last = max(date.fromisoformat(d) for d in passes)
    nh04 = bool(pilot_start and last and _day_index(last, pilot_start) >= 14)
    return {
        "median_cost": statistics.median(costs) if costs else None,
        "median_cost_seasoned": statistics.median(seasoned_costs) if seasoned_costs else None,
        "unsellable_share": (uns / n) if n else None,
        "units": n,
        "pace_median": pace,
        "pace_ready": ready,
        "nh04_ready": nh04,
    }


def stray_addresses(blob):
    """40-byte-looking addresses that are not the collection contract inside an asset id.

    Order hashes are 32 bytes and are removed first so they are not read as wallets.
    """
    scrub = _HASH.sub("", blob)
    allowed = {a.lower() for a in re.findall(r"nft_collection:eip155:\d+:(0x[0-9a-fA-F]{40})", scrub)}
    return {a.lower() for a in _ADDR.findall(scrub)} - allowed
