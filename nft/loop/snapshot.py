"""Hourly OpenSea snapshot. Derived rows on nft-data. Raw bodies are not kept."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import opensea
import pricing as P

DIR = "nft-pilot"


def _now_iso(now):
    return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(now):
    return datetime.fromtimestamp(now, timezone.utc).date()


def _paths(data_dir):
    root = os.path.join(data_dir, DIR)
    return root, os.path.join(root, "status.json"), os.path.join(root, "units.jsonl"), os.path.join(root, "books.json")


def _dump(path, doc):
    blob = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    stray = P.stray_addresses(blob)
    if stray:
        raise RuntimeError("public row has a wallet")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        fh.write(blob)
    os.replace(tmp, path)


def _status(code_sha, now, **extra):
    doc = {
        "schema_version": 1, "kind": "status", "status": "ok", "measured": False, "partial": False,
        "ran_at": _now_iso(now), "commit_sha": code_sha, "rule": P.RULE,
        "data_branch": P.DATA_BRANCH, "avoids_branch": P.AVOIDS_BRANCH,
        "opensea_calls": 0, "rate_limit": {"limit": None, "remaining": None, "reset": None},
        "floor_passing_this_run": None, "pending": None, "units": None,
        "unsellable_share": None, "median_cost": None, "median_cost_seasoned": None,
        "null_cost_count": None, "null_cost_share": None, "null_cost_share_max": P.NULL_COST_MAX,
        "pace_median": None, "pace_ready": False, "nh04": "not_ready", "nh04_ready": False,
        "buyer_fee_eth": 0.0, "eth_usd": None, "raw": "not_kept", "reason": None,
        "attribution": P.ATTRIBUTION,
    }
    doc.update(extra)
    return doc


def load_state(path):
    if not os.path.exists(path):
        return {"pilot_start": None, "seen": {}, "passes": {}, "complete_days": [], "offers": {}, "pending": [], "gas": []}
    with open(path) as fh:
        doc = json.load(fh)
    doc.setdefault("pending", [])
    doc.setdefault("gas", [])
    doc.setdefault("seen", {})
    doc.setdefault("passes", {})
    doc.setdefault("complete_days", [])
    doc.setdefault("offers", {})
    return doc


def load_units(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _append_units(path, rows, prior):
    if not rows:
        return
    seen = {(u["asset_id"], u["snapshot_t"]) for u in prior}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as fh:
        for row in rows:
            key = (row["asset_id"], row["snapshot_t"])
            if key in seen:
                continue
            blob = json.dumps(row, sort_keys=True) + "\n"
            if P.stray_addresses(blob):
                raise RuntimeError("public row has a wallet")
            fh.write(blob)
            seen.add(key)


def _eth_usd(detail):
    block = (detail or {}).get("pricing_currencies") or {}
    token = block.get("listing_currency") or {}
    if str(token.get("symbol") or "").upper() not in ("ETH", "WETH"):
        return None
    raw = token.get("usd_price")
    if raw in (None, ""):
        return None
    return float(raw)


def _contracts(row, chain):
    out = []
    for item in row.get("contracts") or []:
        if item.get("chain") == chain and item.get("address"):
            out.append(item["address"].lower())
    return out


def _remember(state, asset, day, keep_days=21):
    days = state["seen"].setdefault(asset, [])
    iso = day.isoformat()
    if iso not in days:
        days.append(iso)
    cutoff = (day.toordinal() - keep_days)
    state["seen"][asset] = [d for d in days if date_ok(d, cutoff)]


def date_ok(iso, cutoff_ordinal):
    from datetime import date
    return date.fromisoformat(iso).toordinal() >= cutoff_ordinal


def _page_offers(client, slug, now):
    found, cursor, pages = [], None, 0
    for _ in range(P.OFFER_MAX_PAGES):
        batch, cursor = client.offers(slug, cursor)
        pages += 1
        found.extend(batch)
        parsed = [p for p in (P.parse_order(o, now, "offer") for o in found) if p]
        page = [p for p in (P.parse_order(o, now, "offer") for o in batch) if p]
        if not cursor:
            break
        if parsed and page and min(p["price"] for p in page) < P.BID_BAND * max(p["price"] for p in parsed):
            break
    return found, pages


def _page_events(client, slug, after):
    found, cursor, pages = [], None, 0
    for _ in range(P.EVENT_MAX_PAGES):
        batch, cursor = client.events(slug, after, cursor)
        pages += 1
        found.extend(batch)
        if not cursor:
            return found, pages, False
    return found, pages, bool(cursor)


def _unit(pending, listings, offers, prev, gas_eth, fees, now, code_sha):
    parsed = [P.parse_order(x, now, "listing") for x in listings]
    curr = [p for p in (P.parse_order(x, now, "offer") for x in offers) if p]
    rates = P.fee_rates(fees)
    mp, creator = rates if rates else (None, None)
    result = P.round_trip(P.second_cheapest(parsed), 0.0, P.held_bid(prev, curr), mp, creator, gas_eth)
    ident = P.parse_asset_id(pending["asset_id"])
    return {
        "schema_version": 1, "kind": "unit", "asset_id": pending["asset_id"],
        "chain": ident["chain"], "asset_type": ident["asset_type"], "contract": ident["contract"],
        "slug": pending["slug"], "snapshot_t": pending["snapshot_t"], "filled_at": _now_iso(now),
        "commit_sha": code_sha, "rule": P.RULE, "seasoned": bool(pending.get("seasoned")),
        "fill": result["fill"], "unsellable": result["unsellable"], "cost": result["cost"],
        "entry_eth": result["entry_eth"], "exit_eth": result["exit_eth"], "gas_eth": gas_eth,
        "buyer_fee_eth": 0.0, "marketplace_fee_rate": mp, "creator_fee_rate": creator,
        "floor_eth": pending.get("floor_eth"), "floor_usd": pending.get("floor_usd"),
        "counts": pending.get("counts") or {}, "top_buyer_share": pending.get("top_buyer_share"),
        "attribution": P.ATTRIBUTION,
    }


def _call_cap(client):
    return getattr(client, "max_calls", P.MAX_CALLS)


def _collect(client, quotes, detail, now):
    """Page num_owners until FLOOR_TARGET collections are under the $250 cap, or calls run out."""
    cursors = {chain: "" for chain in P.CHAINS}
    pages = {chain: 0 for chain in P.CHAINS}
    seen, under, cache = {}, [], {}
    partial = False
    while len(under) < P.FLOOR_TARGET and any(cursors[c] is not None for c in P.CHAINS):
        for chain in P.CHAINS:
            if cursors[chain] is None or len(under) >= P.FLOOR_TARGET:
                continue
            if pages[chain] >= P.MAX_LIST_PAGES or client.calls >= _call_cap(client):
                cursors[chain] = None
                if client.calls >= _call_cap(client):
                    partial = True
                continue
            try:
                batch, nxt = client.list_collections(chain, cursors[chain] or None)
            except opensea.Budget:
                return under, cache, True
            except opensea.OpenSeaError:
                cursors[chain] = None
                partial = True
                continue
            pages[chain] += 1
            cursors[chain] = nxt or None
            for row in batch:
                if len(under) >= P.FLOOR_TARGET:
                    break
                addrs = _contracts(row, chain)
                item = {
                    "chain": chain, "slug": row.get("collection") or "", "addrs": addrs,
                    "n_contracts": len(addrs),
                    "shared": len(addrs) != 1 or bool(addrs and addrs[0] in P.SHARED_STOREFRONTS),
                }
                if len(addrs) == 1 and addrs[0] not in P.SHARED_STOREFRONTS:
                    prev = seen.get(addrs[0])
                    if prev is not None:
                        item["shared"] = True
                        prev["shared"] = True
                        if prev in under:
                            under.remove(prev)
                    else:
                        seen[addrs[0]] = item
                if item["shared"] or not item["slug"]:
                    continue
                if client.calls >= _call_cap(client):
                    partial = True
                    return under, cache, partial
                try:
                    listings = client.listings(item["slug"])
                except opensea.Budget:
                    return under, cache, True
                except opensea.OpenSeaError:
                    continue
                cache[item["slug"]] = listings
                parsed = [P.parse_order(x, now, "listing") for x in listings]
                floor, n_live = P.floor_and_counts(parsed)
                standard = P.standard_of(parsed)
                if standard is None and client.calls < _call_cap(client):
                    try:
                        standard = (client.contract(chain, addrs[0]) or "").lower()
                    except opensea.Budget:
                        return under, cache, True
                    except opensea.OpenSeaError:
                        standard = None
                if detail.usd is None and floor is not None and client.calls < _call_cap(client):
                    try:
                        detail(item["slug"])
                    except opensea.Budget:
                        return under, cache, True
                    except opensea.OpenSeaError:
                        pass
                floor_usd = floor * detail.usd if floor is not None and detail.usd else None
                trip = P.round_trip_eth(quotes.get(chain))
                if _under_cap(floor, floor_usd, trip, standard, n_live, item["shared"]):
                    under.append(item)
    return under, cache, partial


def _under_cap(floor, floor_usd, trip, standard, n_live, shared):
    return (not shared and floor is not None and floor_usd is not None and floor_usd <= P.FLOOR_USD
            and trip is not None and trip < P.GAS_RATIO * floor and standard == "erc721"
            and n_live >= P.MIN_LISTINGS)


def _candidates(client):
    rows = []
    for chain in P.CHAINS:
        cursor = None
        for _ in range(P.MAX_LIST_PAGES):
            batch, cursor = client.list_collections(chain, cursor)
            for row in batch:
                rows.append((chain, row))
            if not cursor:
                break
    counts = {}
    for chain, row in rows:
        for addr in _contracts(row, chain):
            counts[addr] = counts.get(addr, 0) + 1
    kept = []
    for chain, row in rows:
        addrs = _contracts(row, chain)
        shared = len(addrs) != 1 or counts.get(addrs[0] if addrs else "", 0) != 1 or (addrs and addrs[0] in P.SHARED_STOREFRONTS)
        kept.append({
            "chain": chain, "slug": row.get("collection") or "", "addrs": addrs,
            "shared": bool(shared) or len(addrs) != 1, "n_contracts": len(addrs),
        })
    return kept


def _close_read_error(pending, now, code_sha):
    ident = P.parse_asset_id(pending["asset_id"])
    return {
        "schema_version": 1, "kind": "unit", "asset_id": pending["asset_id"],
        "chain": ident["chain"], "asset_type": ident["asset_type"], "contract": ident["contract"],
        "slug": pending["slug"], "snapshot_t": pending["snapshot_t"], "filled_at": _now_iso(now),
        "commit_sha": code_sha, "rule": P.RULE, "seasoned": bool(pending.get("seasoned")),
        "fill": False, "unsellable": False, "cost": None, "entry_eth": None, "exit_eth": None,
        "gas_eth": None, "buyer_fee_eth": 0.0, "marketplace_fee_rate": None, "creator_fee_rate": None,
        "floor_eth": pending.get("floor_eth"), "floor_usd": pending.get("floor_usd"),
        "counts": pending.get("counts") or {}, "top_buyer_share": pending.get("top_buyer_share"),
        "reason": "read_error", "attribution": P.ATTRIBUTION,
    }


def run(data_dir, client, now, code_sha, gas_wei):
    _, status_path, units_path, books_path = _paths(data_dir)
    state = load_state(books_path)
    day = _day(now)
    if state["pilot_start"] is None:
        state["pilot_start"] = day.isoformat()
    quotes = {}
    for chain, wei in (gas_wei or {}).items():
        quotes[chain] = P.gas_quote(wei, state["gas"], chain, now)
        if wei is not None:
            state["gas"].append({"t": int(now), "chain": chain, "wei": int(wei)})
    state["gas"] = [s for s in state["gas"] if s.get("t", 0) >= now - 8 * 86400]
    if any(quotes.get(c) is None for c in P.CHAINS):
        _dump(status_path, _status(code_sha, now, status="error", reason="gas"))
        return
    details, eth_usd = {}, None
    new_units, still_pending, passing, done = [], [], [], set()

    def detail(slug):
        nonlocal eth_usd
        if slug not in details:
            details[slug] = client.collection(slug)
            eth_usd = eth_usd or _eth_usd(details[slug])
        detail.usd = eth_usd
        return details[slug]

    detail.usd = None
    try:
        under, listing_cache, partial = _collect(client, quotes, detail, now)
    except opensea.AuthError:
        raise
    pending_by_slug = {p["slug"]: p for p in state["pending"]}
    slugs = []
    for item in under:
        if item["slug"] and item["slug"] not in slugs:
            slugs.append(item["slug"])
    for slug in pending_by_slug:
        if slug not in slugs:
            slugs.append(slug)
    by_slug = {item["slug"]: item for item in under if item["slug"]}

    def keep(pending, failed=False):
        done.add(pending["slug"])
        if not failed:
            still_pending.append(pending)
            return
        pending["tries"] = pending.get("tries", 0) + 1
        if pending["tries"] >= 3:
            new_units.append(_close_read_error(pending, now, code_sha))
        else:
            still_pending.append(pending)

    for slug in slugs:
        item = by_slug.get(slug) or {
            "chain": pending_by_slug[slug]["chain"], "slug": slug, "shared": False, "n_contracts": 1, "addrs": [],
        }
        pending = pending_by_slug.get(slug)
        if item.get("shared") and not pending:
            continue
        listings = listing_cache.get(slug)
        if listings is None:
            try:
                listings = client.listings(slug)
            except opensea.Budget:
                partial = True
                if pending:
                    keep(pending)
                break
            except opensea.AuthError:
                raise
            except opensea.OpenSeaError:
                if pending:
                    keep(pending, failed=True)
                continue
        parsed = [P.parse_order(x, now, "listing") for x in listings]
        floor, n_live = P.floor_and_counts(parsed)
        standard = P.standard_of(parsed)
        if standard is None and item.get("addrs") and not item.get("shared"):
            try:
                standard = (client.contract(item["chain"], item["addrs"][0]) or "").lower()
            except opensea.Budget:
                partial = True
                if pending:
                    keep(pending)
                break
            except opensea.OpenSeaError:
                standard = None
        if eth_usd is None and floor is not None:
            try:
                detail(slug)
            except opensea.Budget:
                partial = True
                if pending:
                    keep(pending)
                break
            except opensea.OpenSeaError:
                pass
        floor_usd = floor * eth_usd if floor is not None and eth_usd else None
        leg = P.leg_eth(quotes.get(item["chain"]))
        trip = P.round_trip_eth(quotes.get(item["chain"]))
        cheap = _under_cap(floor, floor_usd, trip, standard, n_live, item.get("shared"))
        if not pending and not cheap:
            if item.get("addrs") and not item.get("shared"):
                try:
                    _remember(state, P.asset_id(item["chain"], item["addrs"][0]), day)
                except ValueError:
                    pass
            continue
        try:
            info = detail(slug)
            offers, offer_pages = _page_offers(client, slug, now)
            if slug in by_slug and not item.get("shared"):
                events, event_pages, truncated = _page_events(client, slug, int(now) - 86400)
            else:
                events, event_pages, truncated = [], 0, False
        except opensea.Budget:
            partial = True
            if pending:
                keep(pending)
            break
        except opensea.OpenSeaError:
            if pending:
                keep(pending, failed=True)
            continue
        offer_parsed = [p for p in (P.parse_order(o, now, "offer") for o in offers) if p]
        sales, buyers, share = P.sale_stats(events, int(now) - 86400)
        asset = None
        if item.get("addrs"):
            try:
                asset = P.asset_id(item["chain"], item["addrs"][0])
            except ValueError:
                asset = None
        if asset is None and pending:
            asset = pending["asset_id"]
        book = [{"hash": p["hash"], "price_eth": p["price"]} for p in offer_parsed if p.get("hash")]
        if asset:
            _remember(state, asset, day)
            state["offers"][asset] = book
        if pending and asset:
            done.add(slug)
            new_units.append(_unit(
                pending, listings, offers, pending.get("book") or [], leg, (info or {}).get("fees"), now, code_sha,
            ))
        obs = {
            "n_contracts": item.get("n_contracts"), "shared": item.get("shared"), "standard": standard,
            "mint_ok": P.mint_ok((info or {}).get("created_date"), now),
            "disabled": bool((info or {}).get("is_disabled")),
            "floor_eth": floor, "floor_usd": floor_usd, "gas_eth": trip, "listings": n_live,
            "sales": sales, "buyers": buyers, "bidders": P.bidder_count(offer_parsed),
            "top_share": share, "sales_truncated": truncated,
        }
        if asset and slug in by_slug and not P.rejection(obs):
            passing.append(asset)
            done.add(slug)
            still_pending.append({
                "asset_id": asset, "slug": slug, "chain": item["chain"], "snapshot_t": _now_iso(now),
                "seasoned": P.seasoned(state["seen"].get(asset, []), day),
                "floor_eth": floor, "floor_usd": floor_usd, "tries": 0,
                "fees": P.public_fees((info or {}).get("fees")),
                "book": [dict(row) for row in book], "top_buyer_share": share,
                "counts": {
                    "sales_24h": sales, "buyers_24h": buyers, "listings": n_live,
                    "bidders_near_top": obs["bidders"], "offer_pages": offer_pages, "event_pages": event_pages,
                },
            })
    for pending in state["pending"]:
        if pending["slug"] not in done:
            still_pending.append(pending)
    state["pending"] = still_pending
    # A partial run, including a budget hit while listing, does not record a zero day.
    iso = day.isoformat()
    if not partial:
        if iso not in state["complete_days"]:
            state["complete_days"].append(iso)
        bucket = state["passes"].setdefault(iso, [])
        for asset in passing:
            if asset not in bucket:
                bucket.append(asset)
    cutoff = day.toordinal() - 21
    state["passes"] = {k: v for k, v in state["passes"].items() if date_ok(k, cutoff)}
    state["complete_days"] = [d for d in state["complete_days"] if date_ok(d, cutoff)]
    prior = load_units(units_path)
    _append_units(units_path, new_units, prior)
    pilot = datetime.fromisoformat(state["pilot_start"]).date() if state["pilot_start"] else None
    summary = P.summarize(prior + new_units, state["passes"], pilot, state["complete_days"])
    if getattr(client, "raw", None) is not None:
        client.raw.clear()
    _dump(books_path, state)
    _dump(status_path, _status(
        code_sha, now, status="partial" if partial else "ok", measured=True, partial=partial,
        opensea_calls=client.calls, rate_limit=dict(client.rate),
        floor_passing_this_run=len(set(passing)) if not partial else None,
        pending=len(still_pending), eth_usd=eth_usd, reason=None, **summary,
    ))


def fetch_gas(post=None):
    post = post or _rpc_post
    out = {}
    for chain, url in P.GAS_RPC.items():
        body = post(url, {"jsonrpc": "2.0", "id": 1, "method": "eth_gasPrice", "params": []})
        out[chain] = int(body["result"], 16)
    return out


def _rpc_post(url, payload):
    # Publicnode returns 403 for urllib's default Python-urllib agent.
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={
        "content-type": "application/json",
        "user-agent": "sightline-nft-snapshot",
    })
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


def _snapshot_error_line(exc):
    """Type, HTTP status, and host. The URL, query, headers, and message stay out."""
    parts = [type(exc).__name__]
    code = getattr(exc, "code", None)
    if not isinstance(code, int):
        code = getattr(exc, "status", None)
    if isinstance(code, int):
        parts.append(str(code))
    url = getattr(exc, "url", None)
    if not isinstance(url, str) or not url:
        url = getattr(exc, "filename", None)
    if isinstance(url, str) and url:
        host = urllib.parse.urlparse(url).hostname
        if host:
            parts.append(host)
    return "snapshot error: " + " ".join(parts)


def _no_key_unchanged(path, code_sha):
    """True when the published status is already this code's no-key row."""
    if not os.path.exists(path):
        return False
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return False
    return (doc.get("status") == "no_key" and doc.get("reason") == "no key"
            and doc.get("commit_sha") == code_sha and doc.get("measured") is False
            and doc.get("buyer_fee_eth") == 0 and doc.get("raw") == "not_kept")


def main(argv=None):
    parser = argparse.ArgumentParser(description="NFT snapshot pilot")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--code-sha", default=os.environ.get("GITHUB_SHA", "local"))
    args = parser.parse_args(argv)
    now = time.time()
    key = os.environ.get("OPENSEA_API_KEY", "").strip()
    _, status_path, _, _ = _paths(args.data_dir)
    if not key:
        if not _no_key_unchanged(status_path, args.code_sha):
            _dump(status_path, _status(args.code_sha, now, status="no_key", reason="no key"))
        print("no key")
        return 0
    try:
        client = opensea.OpenSea(key)
        gas = fetch_gas()
        run(args.data_dir, client, now, args.code_sha, gas)
    except opensea.AuthError:
        _dump(status_path, _status(args.code_sha, now, status="error", reason="auth"))
        return 0
    except Exception as exc:
        print(_snapshot_error_line(exc), file=sys.stderr)
        _dump(status_path, _status(args.code_sha, now, status="error", reason="error"))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
