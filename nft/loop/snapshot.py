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
# Leave the privacy guard and the nft-data push inside the 15-minute job.
RUN_LIMIT_SECONDS = 11 * 60


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
        "rejections": _rejection_counts(None), "flags": _flag_counts(None),
        "screened": None, "pending_served": None, "pending_deferred": None, "passers_footnoted": None,
        "nh04_verdict": "not_ready", "verdict_footnote": None,
        "distinct_collections": None, "distinct_per_day_median": None,
        "stop": None, "by_chain": None, "eth_usd_source": None,
        "eth_usd_runs": {"chainlink": 0, "fallback": 0},
        "early_kill": P.early_kill([], [], None, None, None, []),
        "attribution": P.ATTRIBUTION,
    }
    doc.update(extra)
    return doc


def _fresh_state():
    return {
        "state_version": P.STATE_VERSION,
        "pilot_start": None, "seen": {}, "passes": {}, "complete_days": [],
        "offers": {}, "pending": [], "gas": [], "first_runs": [],
        "first_measured_at": None, "early_distinct": [], "measured_days": [], "v03_init_at": None,
        "usd_runs": {"chainlink": 0, "fallback": 0},
    }


def load_state(path):
    """books.json. A state_version other than v0.3 drops the earlier clock.

    v0.2 (and older) pilot_start, complete_days, passes, first_runs and pending
    are not part of the v0.3 season: the next ok run starts day 1 with a fresh
    14-day clock. Gas, seen, and offers stay.
    """
    if not os.path.exists(path):
        return _fresh_state()
    with open(path) as fh:
        doc = json.load(fh)
    doc.setdefault("pending", [])
    doc.setdefault("gas", [])
    doc.setdefault("seen", {})
    doc.setdefault("passes", {})
    doc.setdefault("complete_days", [])
    doc.setdefault("offers", {})
    doc.setdefault("first_runs", [])
    for key, empty in (("first_measured_at", None), ("early_distinct", []), ("measured_days", []), ("v03_init_at", None)):
        doc.setdefault(key, empty)
    doc.setdefault("usd_runs", {"chainlink": 0, "fallback": 0})
    # Never carry raw order hashes or per-order prices forward (owner decision, Nov 10).
    doc["offers"] = {k: v for k, v in doc["offers"].items() if isinstance(v, dict) and "digests" in v}
    doc["pending"] = [p for p in doc["pending"] if isinstance(p.get("book"), dict)]
    if doc.get("state_version") != P.STATE_VERSION:
        doc["pilot_start"] = None
        doc["passes"] = {}
        doc["complete_days"] = []
        doc["first_runs"] = []
        doc["offers"] = {}
        doc["first_measured_at"] = None
        doc["early_distinct"] = []
        doc["measured_days"] = []
        doc["v03_init_at"] = None
        doc["usd_runs"] = {"chainlink": 0, "fallback": 0}
        # v0.2 passers were screened on the 24h rule; their units are not v0.3 units.
        doc["pending"] = []
        doc["state_version"] = P.STATE_VERSION
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


def _past_deadline(deadline, flag=None):
    """True once the wall clock reaches the run limit. `flag['hit']` records it."""
    if deadline is None or time.time() < deadline:
        return False
    if flag is not None:
        flag["hit"] = True
    return True


def _add_reasons(bucket, reasons):
    if bucket is None:
        return
    for reason in reasons:
        bucket[reason] = bucket.get(reason, 0) + 1


def _flag_counts(bucket):
    found = bucket or {}
    return {name: int(found.get(name, 0)) for name in P.FLAG_NAMES}


def _rejection_counts(bucket):
    """Reason hits, not collections. One collection can increment several reasons."""
    found = bucket or {}
    return {name: int(found.get(name, 0)) for name in P.REJECTION_REASONS}


def _cap_fail_reasons(item, floor, floor_usd, trip, standard, n_live):
    reasons = []
    if item.get("n_contracts") != 1:
        reasons.append("contracts")
    if item.get("shared"):
        reasons.append("shared")
    if standard != "erc721":
        reasons.append("standard")
    if floor is None or floor_usd is None or floor_usd > P.FLOOR_USD:
        reasons.append("floor")
    if floor is None or trip is None or not (trip < P.GAS_RATIO * floor):
        reasons.append("gas")
    if (n_live or 0) < P.MIN_LISTINGS:
        reasons.append("listings")
    return reasons


def _cap_hit(client):
    return client.calls >= _call_cap(client)


def _serve_pending(client, state, detail, now, code_sha, quotes, deadline, deadline_flag, caches, new_units, day):
    """Pending re-checks are served before any new screening.

    Each costs listings + collection + offers (1-3 pages): at most 5 reads. A pending
    row the run cannot reach (call cap or wall clock) counts as a try; after 3 tries it
    closes with reason "budget" so pending cannot grow without bound.
    Returns (rows to keep, served count, deferred count, partial).
    """
    keep, served, deferred, partial = [], 0, 0, False
    queue = list(state["pending"])
    for idx, pending in enumerate(queue):
        slug = pending["slug"]
        if _cap_hit(client) or _past_deadline(deadline, deadline_flag):
            partial = True
            for rest in queue[idx:]:
                deferred += 1
                _retry(rest, keep, new_units, now, code_sha, "budget")
            break
        try:
            listings = caches["listings"].get(slug)
            if listings is None:
                listings = client.listings(slug)
                caches["listings"][slug] = listings
            info = detail(slug)
            offers = caches["offers"].get(slug)
            if offers is None:
                offers, pages = _page_offers(client, slug, now)
                caches["offers"][slug] = offers
                caches["offer_pages"][slug] = pages
        except opensea.Budget:
            partial = True
            for rest in queue[idx:]:
                deferred += 1
                _retry(rest, keep, new_units, now, code_sha, "budget")
            break
        except opensea.AuthError:
            raise
        except opensea.OpenSeaError:
            _retry(pending, keep, new_units, now, code_sha, "read_error")
            continue
        served += 1
        leg = P.leg_eth(quotes.get(pending.get("chain")))
        new_units.append(_unit(
            pending, listings, offers, pending.get("book") or [], leg, (info or {}).get("fees"), now, code_sha,
        ))
        offer_parsed = [p for p in (P.parse_order(o, now, "offer") for o in offers) if p]
        state["offers"][pending["asset_id"]] = P.book_summary(offer_parsed)
        _remember(state, pending["asset_id"], day)
    return keep, served, deferred, partial


def _retry(pending, keep, new_units, now, code_sha, reason):
    pending["tries"] = pending.get("tries", 0) + 1
    if pending["tries"] >= 3:
        row = _close_read_error(pending, now, code_sha)
        row["reason"] = reason
        new_units.append(row)
    else:
        keep.append(pending)


def _screen(client, quotes, detail, now, state, caches, day, deadline=None, rejections=None,
            deadline_flag=None, flags=None):
    """Page by market cap; screen each under-cap collection at once; stop at PASS_TARGET.

    Returns (passing, new_pending, list_error, screened, stop, by_chain). `stop` is
    pass_target, depth (frozen page depth or cursors ran out), call_cap, or deadline.
    Every stop is a measured run; only a list error makes the run partial.
    """
    cursors = {chain: "" for chain in P.CHAINS}
    pages = {chain: 0 for chain in P.CHAINS}
    seen, passing, new_pending = {}, [], []
    screened = 0
    partial = False
    flags = flags if flags is not None else {}
    by_chain = {chain: {"screened": 0, "passing": 0} for chain in P.CHAINS}

    def done(stop):
        for chain in P.CHAINS:
            by_chain[chain]["passing"] = sum(1 for p in new_pending if p["chain"] == chain)
        return passing, new_pending, partial, screened, stop, by_chain

    def drop_shared(prev):
        prev["shared"] = True
        asset = prev.get("asset")
        if asset and asset in passing:
            passing.remove(asset)
            new_pending[:] = [p for p in new_pending if p["asset_id"] != asset]
        if prev.get("screened"):
            _add_reasons(rejections, ["shared"])

    while len(passing) < P.PASS_TARGET and any(cursors[c] is not None for c in P.CHAINS):
        if _past_deadline(deadline, deadline_flag):
            return done("deadline")
        for chain in P.CHAINS:
            if cursors[chain] is None or len(passing) >= P.PASS_TARGET:
                continue
            if pages[chain] >= P.MAX_LIST_PAGES:
                cursors[chain] = None
                continue
            if _cap_hit(client):
                return done("call_cap")
            try:
                batch, nxt = client.list_collections(chain, cursors[chain] or None)
            except opensea.Budget:
                return done("call_cap")
            except opensea.OpenSeaError as exc:
                cursors[chain] = None
                partial = True
                setattr(client, "list_error", exc.status)
                continue
            pages[chain] += 1
            cursors[chain] = nxt or None
            for row in batch:
                if len(passing) >= P.PASS_TARGET:
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
                        drop_shared(prev)
                    else:
                        seen[addrs[0]] = item
                if item["shared"] or not item["slug"]:
                    if item["shared"]:
                        reasons = ["shared"]
                        if item.get("n_contracts") != 1:
                            reasons.insert(0, "contracts")
                        _add_reasons(rejections, reasons)
                    continue
                if _cap_hit(client):
                    return done("call_cap")
                if _past_deadline(deadline, deadline_flag):
                    return done("deadline")
                try:
                    out = _screen_one(client, item, quotes, detail, now, state, caches, day, rejections, flags)
                except opensea.Budget:
                    return done("call_cap")
                if out is None:
                    continue
                screened += 1
                by_chain[chain]["screened"] += 1
                if out.get("pending"):
                    passing.append(out["pending"]["asset_id"])
                    new_pending.append(out["pending"])
    return done("pass_target" if len(passing) >= P.PASS_TARGET else "depth")


def _screen_one(client, item, quotes, detail, now, state, caches, day, rejections, flags):
    """One collection. None when it is not under the cap (or a read failed)."""
    slug, chain = item["slug"], item["chain"]
    try:
        listings = caches["listings"].get(slug)
        if listings is None:
            listings = client.listings(slug)
            caches["listings"][slug] = listings
    except opensea.OpenSeaError:
        return None
    parsed = [P.parse_order(x, now, "listing") for x in listings]
    floor, n_live = P.floor_and_counts(parsed)
    standard = P.standard_of(parsed)
    if standard is None and not _cap_hit(client):
        try:
            standard = (client.contract(chain, item["addrs"][0]) or "").lower()
        except opensea.OpenSeaError:
            standard = None
    if detail.usd is None and floor is not None and not _cap_hit(client):
        try:
            detail(slug)
        except opensea.OpenSeaError:
            pass
    floor_usd = floor * detail.usd if floor is not None and detail.usd else None
    trip = P.round_trip_eth(quotes.get(chain))
    asset = None
    try:
        asset = P.asset_id(chain, item["addrs"][0])
    except ValueError:
        return None
    if not _under_cap(floor, floor_usd, trip, standard, n_live, item["shared"]):
        _add_reasons(rejections, _cap_fail_reasons(item, floor, floor_usd, trip, standard, n_live))
        _remember(state, asset, day)
        return None
    item["asset"] = asset
    item["screened"] = True
    try:
        info = detail(slug)
        offers = caches["offers"].get(slug)
        offer_pages = caches["offer_pages"].get(slug, 0)
        if offers is None:
            offers, offer_pages = _page_offers(client, slug, now)
            caches["offers"][slug] = offers
        after = int(now) - P.WINDOW_SECONDS
        events, event_pages, truncated = _page_events(client, slug, after)
    except opensea.OpenSeaError:
        return None
    offer_parsed = [p for p in (P.parse_order(o, now, "offer") for o in offers) if p]
    sales, buyers, share = P.sale_stats(events, after)
    seller_share, day_share = P.sale_profile(events, after, now)
    book = P.book_summary(offer_parsed)
    _remember(state, asset, day)
    state["offers"][asset] = book
    obs = {
        "n_contracts": item.get("n_contracts"), "shared": item.get("shared"), "standard": standard,
        "mint_ok": P.mint_ok((info or {}).get("created_date"), now),
        "disabled": bool((info or {}).get("is_disabled")),
        "floor_eth": floor, "floor_usd": floor_usd, "gas_eth": trip, "listings": n_live,
        "sales": sales, "buyers": buyers, "bidders": P.bidder_count(offer_parsed),
        "top_share": share,
    }
    if truncated:
        flags["sales_truncated"] = flags.get("sales_truncated", 0) + 1
    reasons = P.rejection(obs)
    if reasons:
        _add_reasons(rejections, reasons)
        return {"pending": None}
    if truncated:
        flags["liquid_tail"] = flags.get("liquid_tail", 0) + 1
    return {"pending": {
        "asset_id": asset, "slug": slug, "chain": chain, "snapshot_t": _now_iso(now),
        "seasoned": P.seasoned(state["seen"].get(asset, []), day),
        "floor_eth": floor, "floor_usd": floor_usd, "tries": 0,
        "fees": P.public_fees((info or {}).get("fees")),
        "book": dict(book), "top_buyer_share": share,
        "counts": {
            "sales_7d": sales, "buyers_7d": buyers, "listings": n_live,
            "bidders_near_top": obs["bidders"], "offer_pages": offer_pages, "event_pages": event_pages,
            "top_seller_share_7d": seller_share, "max_day_share_7d": day_share,
            "sales_truncated": bool(truncated), "liquid_tail": bool(truncated),
        },
    }}


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


def run(data_dir, client, now, code_sha, gas_wei, eth_usd=None, fetch_price=None, deadline=None,
        eth_usd_source="chainlink"):
    """`eth_usd` given by a caller is treated as `eth_usd_source` (tests pass a Chainlink-like quote).
    Otherwise the price comes from fetch_eth_usd: Chainlink first, DefiLlama internal-only fallback."""
    _, status_path, units_path, books_path = _paths(data_dir)
    state = load_state(books_path)
    if state["v03_init_at"] is None:
        # Calendar anchor for the pace backstop: the first v0.3 run after merge/deploy.
        state["v03_init_at"] = int(now)
    day = _day(now)
    if deadline is None:
        deadline = time.time() + RUN_LIMIT_SECONDS
    deadline_flag = {"hit": False}
    quotes = {}
    for chain, wei in (gas_wei or {}).items():
        quotes[chain] = P.gas_quote(wei, state["gas"], chain, now)
        if wei is not None:
            state["gas"].append({"t": int(now), "chain": chain, "wei": int(wei)})
    state["gas"] = [s for s in state["gas"] if s.get("t", 0) >= now - 8 * 86400]
    if any(quotes.get(c) is None for c in P.CHAINS):
        _dump(status_path, _status(code_sha, now, status="error", reason="gas"))
        return
    if not _usable_price(eth_usd):
        got = (fetch_price or fetch_eth_usd)()
        eth_usd, eth_usd_source = got if isinstance(got, tuple) else (got, None)
    if not _usable_price(eth_usd):
        # The $250 cap cannot be evaluated. Do not spend the shared read bucket.
        _dump(books_path, state)
        _dump(status_path, _status(
            code_sha, now, status="partial", measured=False, partial=True,
            opensea_calls=getattr(client, "calls", 0),
            rate_limit=_rate(client), eth_usd=None, reason="no eth price",
            rejections=_rejection_counts(None),
        ))
        return
    details = {}
    eth_usd_box = {"source": eth_usd_source}
    caches = {"listings": {}, "offers": {}, "offer_pages": {}}
    new_units = []
    rejections, flags = {}, {}

    def detail(slug):
        nonlocal eth_usd
        if slug not in details:
            details[slug] = client.collection(slug)
            # A missing OpenSea usd_price must not replace a keyless quote.
            if not _usable_price(eth_usd):
                eth_usd = _eth_usd(details[slug])
                eth_usd_box["source"] = "opensea"
        detail.usd = eth_usd
        return details[slug]

    detail.usd = eth_usd
    # 1. Pending re-checks first, so a long screen can never starve a unit fill.
    kept, served, deferred, pend_partial = _serve_pending(
        client, state, detail, now, code_sha, quotes, deadline, deadline_flag, caches, new_units, day,
    )
    # 2. Screen until PASS_TARGET pass, or the cap / clock / page depth.
    passing, fresh, list_error, screened, stop = [], [], False, 0, None
    by_chain = {chain: {"screened": 0, "passing": 0} for chain in P.CHAINS}
    if not pend_partial:
        passing, fresh, list_error, screened, stop, by_chain = _screen(
            client, quotes, detail, now, state, caches, day, deadline=deadline,
            rejections=rejections, deadline_flag=deadline_flag, flags=flags,
        )
    # Measured = every pending row served and the screen ran to any stop. Partial only for
    # deferred pending or a list error (no eth price / gas failure return earlier).
    partial = pend_partial or list_error
    usd_public = eth_usd_box["source"] == "chainlink"
    if not usd_public:
        # A fallback (DefiLlama) USD price screens the $250 cap internally but is never published.
        for row in fresh:
            row["floor_usd"] = None
    fresh_ids = {p["asset_id"] for p in fresh}
    state["pending"] = [p for p in kept if p["asset_id"] not in fresh_ids] + fresh
    iso = day.isoformat()
    if not partial:
        if state["pilot_start"] is None:
            state["pilot_start"] = iso
        if iso not in state["complete_days"]:
            state["complete_days"].append(iso)
        bucket = state["passes"].setdefault(iso, [])
        for asset in passing:
            if asset not in bucket:
                bucket.append(asset)
        if len(state["first_runs"]) < P.EARLY_RUNS:
            state["first_runs"].append(len(set(passing)))
        if state["first_measured_at"] is None:
            state["first_measured_at"] = int(now)
        if now < state["first_measured_at"] + P.EARLY_WINDOW_SECONDS:
            for asset in passing:
                if asset not in state["early_distinct"]:
                    state["early_distinct"].append(asset)
        if iso not in state["measured_days"]:
            state["measured_days"].append(iso)
        # Q1: count measured runs by ETH/USD source (fallback = internal-only, USD not published).
        src = "chainlink" if usd_public else "fallback"
        state["usd_runs"][src] = state["usd_runs"].get(src, 0) + 1
    cutoff = day.toordinal() - 21
    state["passes"] = {k: v for k, v in state["passes"].items() if date_ok(k, cutoff)}
    state["complete_days"] = [d for d in state["complete_days"] if date_ok(d, cutoff)]
    prior = load_units(units_path)
    _append_units(units_path, new_units, prior)
    pilot = datetime.fromisoformat(state["pilot_start"]).date() if state["pilot_start"] else None
    summary = P.summarize(prior + new_units, state["passes"], pilot, state["complete_days"])
    usd_runs = {"chainlink": state["usd_runs"].get("chainlink", 0), "fallback": state["usd_runs"].get("fallback", 0)}
    summary["verdict_footnote"] = P.with_usd_footnote(summary.get("verdict_footnote"), usd_runs)
    kill = P.early_kill(
        state["first_runs"], state["early_distinct"], state["first_measured_at"], now,
        state["v03_init_at"], state["measured_days"],
    )
    if getattr(client, "raw", None) is not None:
        client.raw.clear()
    _dump(books_path, state)
    reason = None
    code = getattr(client, "list_error", None)
    if list_error and isinstance(code, int):
        reason = "opensea http %s" % code
    elif pend_partial:
        reason = "pending deferred"
    elif deadline_flag["hit"]:
        reason = "deadline"
    _dump(status_path, _status(
        code_sha, now, status="partial" if partial else "ok", measured=True, partial=partial,
        opensea_calls=client.calls, rate_limit=_rate(client),
        floor_passing_this_run=len(set(passing)) if not partial else None,
        stop=stop if not partial else None, by_chain=by_chain,
        pending=len(state["pending"]), eth_usd=eth_usd if usd_public else None,
        eth_usd_source="chainlink" if usd_public else "fallback", eth_usd_runs=usd_runs, reason=reason,
        rejections=_rejection_counts(rejections), flags=_flag_counts(flags),
        screened=screened, pending_served=served, pending_deferred=deferred,
        passers_footnoted=sum(1 for p in fresh if P.footnoted(p.get("counts"))),
        early_kill=kill, **summary,
    ))


def _rate(client):
    rate = getattr(client, "rate", None) or {}
    return {
        "limit": rate.get("limit"),
        "remaining": rate.get("remaining"),
        "reset": rate.get("reset"),
    }


def _usable_price(value):
    return isinstance(value, (int, float)) and value == value and 0 < value < float("inf")


# Primary ETH/USD: the Chainlink ETH/USD aggregator on Ethereum, read with eth_call over the
# same keyless RPC as the gas price. latestRoundData() -> (roundId, answer, startedAt,
# updatedAt, answeredInRound); answer has 8 decimals. Rejected when updatedAt is over 1h old.
# DefiLlama is an internal-only fallback: its price screens the cap but is never published.
CHAINLINK_ETH_USD = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
LATEST_ROUND_DATA = "0xfeaf968c"
CHAINLINK_DECIMALS = 8
CHAINLINK_MAX_AGE = 3600
DEFILLAMA_URL = "https://coins.llama.fi/prices/current/coingecko:ethereum"


def _positive_price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number <= 0 or number == float("inf"):
        return None
    return number


def parse_chainlink(result_hex, now):
    """A positive, fresh ETH/USD answer from latestRoundData, or None."""
    if not isinstance(result_hex, str) or not result_hex.startswith("0x"):
        return None
    raw = result_hex[2:]
    if len(raw) < 64 * 5:
        return None
    try:
        words = [int(raw[i:i + 64], 16) for i in range(0, 64 * 5, 64)]
    except ValueError:
        return None
    round_id, answer, _started, updated, answered = words
    if answer >= 2 ** 255:          # int256 negative
        return None
    if updated <= 0 or now - updated > CHAINLINK_MAX_AGE or updated > now + 300:
        return None
    if answered < round_id:
        return None
    return _positive_price(answer / 10 ** CHAINLINK_DECIMALS)


def parse_eth_usd(kind, body, now=None):
    """DefiLlama quote (internal fallback), or None. Zero, blanks and >1h-old stay unknown."""
    if not isinstance(body, dict) or kind != "defillama":
        return None
    coins = body.get("coins") if isinstance(body.get("coins"), dict) else {}
    row = coins.get("coingecko:ethereum")
    if not isinstance(row, dict):
        return None
    if str(row.get("symbol") or "").upper() not in ("ETH", "WETH"):
        return None
    price = _positive_price(row.get("price"))
    if price is None:
        return None
    stamp = row.get("timestamp")
    if stamp is None:
        return None
    try:
        stamp_f = float(stamp)
    except (TypeError, ValueError):
        return None
    clock = time.time() if now is None else float(now)
    if clock - stamp_f > 3600:
        return None
    return price


def fetch_eth_usd(get=None, post=None, now=None):
    """(price, source): Chainlink first; DefiLlama only when Chainlink is missing or stale."""
    get = get or _get_json
    post = post or _rpc_post
    clock = time.time() if now is None else now
    try:
        body = post(P.GAS_RPC["ethereum"], {
            "jsonrpc": "2.0", "id": 1, "method": "eth_call",
            "params": [{"to": CHAINLINK_ETH_USD, "data": LATEST_ROUND_DATA}, "latest"],
        })
        price = parse_chainlink((body or {}).get("result"), clock)
        if price is not None:
            return price, "chainlink"
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        price = parse_eth_usd("defillama", get(DEFILLAMA_URL), now=clock)
    except (OSError, ValueError):
        price = None
    if price is not None:
        return price, "defillama"
    return None, None


def _get_json(url):
    req = urllib.request.Request(url, headers={
        "accept": "application/json",
        "user-agent": "sightline-nft-snapshot",
    })
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.load(res)


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


def _dry_dir_ok(path):
    """A dry run writes only into an empty directory that is not inside a git checkout."""
    real = os.path.realpath(path)
    if not os.path.isdir(real) or os.listdir(real):
        return False
    probe = real
    while True:
        if os.path.exists(os.path.join(probe, ".git")):
            return False
        parent = os.path.dirname(probe)
        if parent == probe:
            return True
        probe = parent


SUMMARY_KEYS = ("status", "stop", "opensea_calls", "floor_passing_this_run", "by_chain", "rejections")


def write_summary(status_path, out=None):
    """Counts only (no slugs, no ids) to the job step summary, or stdout."""
    with open(status_path) as fh:
        doc = json.load(fh)
    lines = ["### NFT snapshot DRY_RUN (nothing published)", ""]
    for key in SUMMARY_KEYS:
        lines.append("- %s: `%s`" % (key, json.dumps(doc.get(key), sort_keys=True)))
    text = "\n".join(lines) + "\n"
    if P.stray_addresses(text):
        raise RuntimeError("summary has an address")
    target = out or os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a") as fh:
            fh.write(text)
    else:
        print(text)
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description="NFT snapshot pilot")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--code-sha", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument("--dry-run", action="store_true",
                        help="snapshot into an empty temp dir and write only the step summary")
    args = parser.parse_args(argv)
    if args.dry_run and not _dry_dir_ok(args.data_dir):
        print("dry run: --data-dir must be an empty directory outside any git checkout", file=sys.stderr)
        return 2
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
        if args.dry_run:
            write_summary(status_path)
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
