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
        "outage_runs": 0,
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
        # A reset clock has no outages "since v03_init_at". early_kill does not read this.
        doc.pop("outage_at", None)
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


def _unit(pending, listings, offers, prev, gas_eth, fees, now, code_sha, rule=None):
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
        "commit_sha": code_sha, "rule": rule or pending.get("rule") or P.RULE,
        "seasoned": bool(pending.get("seasoned")),
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


def _blocked(client, call_limit):
    """Global max, or a lane ceiling. `call_limit` None is the global cap only."""
    if _cap_hit(client):
        return True
    return call_limit is not None and client.calls >= call_limit


def _chain_stats():
    return {
        "screened": 0, "passing": 0, "requested": False,
        "pages": 0, "calls": 0, "stop": None,
    }


def _blank_by_chain(chains=None):
    return {chain: _chain_stats() for chain in (chains if chains is not None else P.CHAINS)}


_CHAIN_STOPS = (None, "pass_target", "depth", "call_cap", "deadline", "not_run_budget")
_CHAIN_KEYS = ("screened", "passing", "requested", "pages", "calls", "stop")


def public_by_chain(by_chain):
    """Refuse a by_chain object whose fields are not counts.

    requested is a bool. pages, calls, screened and passing are counts.
    stop is the lane enum or null. A slug, an address, or an extra key is not written.
    """
    if by_chain is None:
        return
    if not isinstance(by_chain, dict) or set(by_chain) - set(P.CHAINS):
        raise RuntimeError("status by_chain is not counts")
    for row in by_chain.values():
        if not isinstance(row, dict) or set(row) != set(_CHAIN_KEYS):
            raise RuntimeError("status by_chain is not counts")
        if not isinstance(row["requested"], bool):
            raise RuntimeError("status by_chain is not counts")
        if row["stop"] not in _CHAIN_STOPS:
            raise RuntimeError("status by_chain is not counts")
        for key in ("screened", "passing", "pages", "calls"):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError("status by_chain is not counts")


def _fresh_base():
    return {
        "version": P.BASE_STATE_VERSION,
        "init_at": None,
        "pending": [],
        "passes": {},
        "run_passes": [],
    }


def _ensure_base(state, now):
    """Base count clock. A missing or other version starts at v0.4-base.

    Ethereum pilot_start, passes, first_runs, pending and v03_init_at stay.
    """
    base = state.get("base")
    if not isinstance(base, dict) or base.get("version") != P.BASE_STATE_VERSION:
        base = _fresh_base()
        state["base"] = base
    base.setdefault("pending", [])
    base.setdefault("passes", {})
    base.setdefault("run_passes", [])
    base.setdefault("init_at", None)
    base["pending"] = [p for p in base["pending"] if isinstance(p, dict) and isinstance(p.get("book"), dict)]
    if base.get("init_at") is None:
        base["init_at"] = int(now)
    return base


_GUARDED_CALLS = ("list_collections", "listings", "collection", "offers", "events", "contract")


def _guard_client(client, ceiling):
    """Raise Budget before a read that would pass `ceiling` or the global cap.

    Pacing is unchanged. This only refuses the next read. Restore with the returned dict.
    """
    saved = {}

    def wrap(fn):
        def wrapped(*args, **kwargs):
            if client.calls >= ceiling or _cap_hit(client):
                raise opensea.Budget()
            return fn(*args, **kwargs)
        return wrapped

    for name in _GUARDED_CALLS:
        fn = getattr(client, name, None)
        if not callable(fn):
            continue
        saved[name] = fn
        setattr(client, name, wrap(fn))
    return saved


def _restore_client(client, saved):
    for name, fn in saved.items():
        setattr(client, name, fn)


def _load_listing_book(client, slug, caches, detail, now):
    """Listings, collection, then offers. Same order as the ETH pending serve."""
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
    return listings, info, offers


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
            listings, info, offers = _load_listing_book(client, slug, caches, detail, now)
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


def _retry(pending, keep, new_units, now, code_sha, reason, rule=None):
    pending["tries"] = pending.get("tries", 0) + 1
    if pending["tries"] >= 3:
        row = _close_read_error(pending, now, code_sha, rule=rule)
        row["reason"] = reason
        new_units.append(row)
    else:
        keep.append(pending)


def _budget_stop(made):
    """A lane that spent calls hit the cap. A lane that spent none was never started."""
    return "call_cap" if made > 0 else "not_run_budget"


def _base_lane(client, quotes, detail, now, state, caches, day, deadline, code_sha, new_units, publish_usd):
    """Base after ETH, on the same screen, with its own 5-pass target.

    The budget is whatever ETH left under the 480 cap, and at most 200. Pending
    re-checks are served first and do not count toward the 5, matching ETH.
    This lane does not set the run's list error, deadline flag, or partial bit.
    Zero calls because no budget remains is `not_run_budget`, never `call_cap`.
    A deadline that arrives first stays `deadline`.
    """
    base = _ensure_base(state, now)
    row = _chain_stats()
    row["requested"] = True
    start = client.calls
    own_flag = {"hit": False}
    passing, fresh = [], []
    http = None

    def finish(stop):
        row["calls"] = client.calls - start
        row["requested"] = True
        # call_cap means this lane spent calls and then hit its ceiling.
        # not_run_budget means it was not given a single call.
        if row["calls"] == 0 and stop == "call_cap":
            stop = "not_run_budget"
        elif row["calls"] > 0 and stop == "not_run_budget":
            stop = "call_cap"
        row["stop"] = stop
        if not publish_usd:
            for item in fresh:
                item["floor_usd"] = None
        for item in fresh:
            item["rule"] = P.BASE_RULE
        fresh_ids = {p["asset_id"] for p in fresh}
        base["pending"] = [p for p in keep if p.get("asset_id") not in fresh_ids] + fresh
        return row, list(passing), http

    keep = list(base.get("pending") or [])
    if _past_deadline(deadline, own_flag):
        return finish("deadline")
    room = min(P.BASE_MAX_CALLS, P.MAX_CALLS - client.calls, _call_cap(client) - client.calls)
    if room <= 0:
        return finish("not_run_budget")
    ceiling = start + room
    saved = _guard_client(client, ceiling)
    try:
        queue = list(base.get("pending") or [])
        keep = []
        served_stop = None
        for idx, pending in enumerate(queue):
            if _past_deadline(deadline, own_flag):
                keep.extend(queue[idx:])
                served_stop = "deadline"
                break
            if client.calls >= ceiling or _cap_hit(client):
                keep.extend(queue[idx:])
                served_stop = _budget_stop(client.calls - start)
                break
            try:
                listings, info, offers = _load_listing_book(client, pending["slug"], caches, detail, now)
            except opensea.Budget:
                keep.extend(queue[idx:])
                served_stop = _budget_stop(client.calls - start)
                break
            except opensea.AuthError:
                raise
            except opensea.OpenSeaError as exc:
                if getattr(exc, "status", None) == 429:
                    http = 429
                    keep.extend(queue[idx:])
                    served_stop = None
                    break
                _retry(pending, keep, new_units, now, code_sha, "read_error", rule=P.BASE_RULE)
                continue
            leg = P.leg_eth(quotes.get(pending.get("chain")))
            new_units.append(_unit(
                pending, listings, offers, pending.get("book") or [], leg,
                (info or {}).get("fees"), now, code_sha, rule=P.BASE_RULE,
            ))
            offer_parsed = [p for p in (P.parse_order(o, now, "offer") for o in offers) if p]
            state["offers"][pending["asset_id"]] = P.book_summary(offer_parsed)
            _remember(state, pending["asset_id"], day)
        if served_stop is not None or http is not None:
            return finish(served_stop)
        b_pass, b_fresh, b_err, _b_screened, _b_stop, b_rows = _screen(
            client, quotes, detail, now, state, caches, day, deadline=deadline,
            rejections={}, deadline_flag=own_flag, flags={},
            chains=("base",), pass_target=P.BASE_PASS_TARGET, call_limit=ceiling,
            record_list_error=False,
        )
        sub = b_rows["base"]
        row["pages"] = sub["pages"]
        row["screened"] = sub["screened"]
        row["passing"] = sub["passing"]
        fresh = list(b_fresh)
        passing = list(b_pass)
        if b_err:
            return finish(None)
        if own_flag["hit"]:
            return finish("deadline")
        return finish(sub["stop"])
    finally:
        _restore_client(client, saved)


def _screen(client, quotes, detail, now, state, caches, day, deadline=None, rejections=None,
            deadline_flag=None, flags=None, chains=None, pass_target=None, call_limit=None,
            record_list_error=True):
    """Page by market cap; screen each under-cap collection at once; stop at pass_target.

    Default `chains` is ethereum only. The v0.3 walk passed both chains and stopped
    at one shared target, so Base was reached only when ETH had not filled 10.
    Returns (passing, new_pending, list_error, screened, stop, by_chain). `stop` is
    pass_target, depth (frozen page depth or cursors ran out), call_cap, deadline,
    or not_run_budget when the lane made no calls. Every stop is a measured run;
    only a list error makes the run partial.
    """
    chains = tuple(chains) if chains is not None else ("ethereum",)
    if pass_target is None:
        pass_target = P.PASS_TARGET
    cursors = {chain: "" for chain in chains}
    pages = {chain: 0 for chain in chains}
    seen, passing, new_pending = {}, [], []
    screened = 0
    partial = False
    flags = flags if flags is not None else {}
    by_chain = _blank_by_chain(chains)
    for chain in chains:
        by_chain[chain]["requested"] = True

    def charge(chain, before):
        by_chain[chain]["calls"] += max(0, client.calls - before)

    def done(stop):
        for chain in chains:
            by_chain[chain]["passing"] = sum(1 for p in new_pending if p["chain"] == chain)
            chain_stop = None if partial else stop
            if chain_stop == "call_cap" and by_chain[chain]["calls"] == 0:
                chain_stop = "not_run_budget"
            by_chain[chain]["stop"] = chain_stop
        lane_stop = stop
        if stop == "call_cap" and all(by_chain[c]["calls"] == 0 for c in chains):
            lane_stop = "not_run_budget"
        return passing, new_pending, partial, screened, lane_stop, by_chain

    def drop_shared(prev):
        prev["shared"] = True
        asset = prev.get("asset")
        if asset and asset in passing:
            passing.remove(asset)
            new_pending[:] = [p for p in new_pending if p["asset_id"] != asset]
        if prev.get("screened"):
            _add_reasons(rejections, ["shared"])

    while len(passing) < pass_target and any(cursors[c] is not None for c in chains):
        if _past_deadline(deadline, deadline_flag):
            return done("deadline")
        for chain in chains:
            if cursors[chain] is None or len(passing) >= pass_target:
                continue
            if pages[chain] >= P.MAX_LIST_PAGES:
                cursors[chain] = None
                continue
            if _blocked(client, call_limit):
                return done("call_cap")
            before = client.calls
            try:
                batch, nxt = client.list_collections(chain, cursors[chain] or None)
            except opensea.Budget:
                charge(chain, before)
                return done("call_cap")
            except opensea.OpenSeaError as exc:
                charge(chain, before)
                cursors[chain] = None
                partial = True
                if record_list_error:
                    setattr(client, "list_error", exc.status)
                continue
            charge(chain, before)
            pages[chain] += 1
            by_chain[chain]["pages"] = pages[chain]
            cursors[chain] = nxt or None
            for row in batch:
                if len(passing) >= pass_target:
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
                if _blocked(client, call_limit):
                    return done("call_cap")
                if _past_deadline(deadline, deadline_flag):
                    return done("deadline")
                before = client.calls
                try:
                    out = _screen_one(client, item, quotes, detail, now, state, caches, day, rejections, flags)
                except opensea.Budget:
                    charge(chain, before)
                    return done("call_cap")
                charge(chain, before)
                if out is None:
                    continue
                screened += 1
                by_chain[chain]["screened"] += 1
                if out.get("pending"):
                    passing.append(out["pending"]["asset_id"])
                    new_pending.append(out["pending"])
    return done("pass_target" if len(passing) >= pass_target else "depth")


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


def _close_read_error(pending, now, code_sha, rule=None):
    ident = P.parse_asset_id(pending["asset_id"])
    return {
        "schema_version": 1, "kind": "unit", "asset_id": pending["asset_id"],
        "chain": ident["chain"], "asset_type": ident["asset_type"], "contract": ident["contract"],
        "slug": pending["slug"], "snapshot_t": pending["snapshot_t"], "filled_at": _now_iso(now),
        "commit_sha": code_sha, "rule": rule or pending.get("rule") or P.RULE,
        "seasoned": bool(pending.get("seasoned")),
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
    anchor_existed = state["v03_init_at"] is not None
    if not anchor_existed:
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
        # Do not save this run's anchor or gas samples. A gas failure must not start the backstop.
        count = _persist_outage(data_dir, now, "gas") if anchor_existed else 0
        _dump(status_path, _status(code_sha, now, status="error", reason="gas", outage_runs=count))
        return
    if not _usable_price(eth_usd):
        got = (fetch_price or fetch_eth_usd)()
        eth_usd, eth_usd_source = got if isinstance(got, tuple) else (got, None)
    if not _usable_price(eth_usd):
        # The $250 cap cannot be evaluated. Do not spend the shared read bucket.
        # A no-price run did not measure supply. Record it once the anchor exists (this run sets it).
        if state.get("v03_init_at") is not None:
            _append_outage(state, now, len(state.get("first_runs") or []), "no eth price")
        _dump(books_path, state)
        _dump(status_path, _status(
            code_sha, now, status="partial", measured=False, partial=True,
            opensea_calls=getattr(client, "calls", 0),
            rate_limit=_rate(client), eth_usd=None, reason="no eth price",
            rejections=_rejection_counts(None),
            outage_runs=_outage_count(state),
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
    publish_usd = eth_usd_box["source"] == "chainlink"
    # 1. ETH pending re-checks first, inside the 280-call ETH ceiling.
    # 2. ETH screen only. Base is a later lane, so filling 10 cannot skip it,
    # and missing 10 cannot spend the Base reserve.
    eth_start = client.calls
    eth_ceiling = min(eth_start + P.ETH_MAX_CALLS, _call_cap(client))
    saved = _guard_client(client, eth_ceiling)
    try:
        kept, served, deferred, pend_partial = _serve_pending(
            client, state, detail, now, code_sha, quotes, deadline, deadline_flag, caches, new_units, day,
        )
        passing, fresh, list_error, screened, stop = [], [], False, 0, None
        eth_rows = _blank_by_chain(("ethereum",))
        if not pend_partial:
            passing, fresh, list_error, screened, stop, eth_rows = _screen(
                client, quotes, detail, now, state, caches, day, deadline=deadline,
                rejections=rejections, deadline_flag=deadline_flag, flags=flags,
                chains=("ethereum",), call_limit=eth_ceiling,
            )
    finally:
        _restore_client(client, saved)
    eth_calls = client.calls - eth_start
    eth_row = eth_rows["ethereum"]
    eth_row["requested"] = True
    eth_row["calls"] = eth_calls
    if pend_partial:
        if deadline_flag["hit"]:
            eth_row["stop"] = "deadline"
        elif eth_calls == 0:
            eth_row["stop"] = "not_run_budget"
        else:
            eth_row["stop"] = "call_cap"
    elif eth_row["stop"] == "not_run_budget" and eth_calls > 0:
        eth_row["stop"] = "call_cap"
        stop = "call_cap"
    elif stop == "not_run_budget" and eth_calls > 0:
        stop = "call_cap"
        eth_row["stop"] = "call_cap"
    # A measured ETH lane that actually reached the 280 ceiling is not an uncapped v0.3 run.
    eth_capped = (
        eth_calls >= P.ETH_MAX_CALLS and eth_row["stop"] == "call_cap" and not deadline_flag["hit"]
    )
    run_rule = P.ETH_CAPPED_RULE if eth_capped else P.RULE
    if eth_capped:
        for item in fresh:
            item["rule"] = P.ETH_CAPPED_RULE
    # 3. Base gets whatever ETH left, up to 200, and never the ETH stop label.
    base_row, base_passing, _base_http = _base_lane(
        client, quotes, detail, now, state, caches, day, deadline, code_sha, new_units, publish_usd,
    )
    by_chain = _blank_by_chain()
    by_chain["ethereum"] = eth_row
    by_chain["base"] = base_row
    # Measured = every pending row served and the screen ran to any stop. Partial only for
    # deferred pending or a list error (no eth price / gas failure return earlier).
    partial = pend_partial or list_error
    usd_public = publish_usd
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
    if isinstance(state.get("base"), dict) and base_row["stop"] in ("pass_target", "depth", "call_cap", "deadline"):
        # Base's own count. It is not the v0.3 pass list and does not move first_runs.
        base_state = state["base"]
        if base_row["calls"] > 0:
            bucket = base_state["passes"].setdefault(iso, [])
            for asset in base_passing:
                if asset not in bucket:
                    bucket.append(asset)
            base_state["run_passes"].append(len(set(base_passing)))
            base_state["passes"] = {
                k: v for k, v in base_state["passes"].items() if date_ok(k, cutoff)
            }
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
    reason = None
    code = getattr(client, "list_error", None)
    if list_error and isinstance(code, int):
        reason = "opensea http %s" % code
    elif pend_partial:
        reason = "pending deferred"
    elif deadline_flag["hit"]:
        reason = "deadline"
    status_name = "partial" if partial else "ok"
    # Every partial missed the supply measurement: 429, 5xx, auth, pending deferred, and the rest.
    # error and no_key are recorded in main. A measured ok run is not recorded.
    if status_name == "partial" and state.get("v03_init_at") is not None:
        _append_outage(state, now, len(state.get("first_runs") or []), reason)
    summary["verdict_footnote"] = P.with_outage_footnote(
        summary.get("verdict_footnote"), kill, state.get("outage_at") or [],
        state.get("v03_init_at"), state.get("first_measured_at"),
    )
    if getattr(client, "raw", None) is not None:
        client.raw.clear()
    public_by_chain(by_chain)
    _dump(books_path, state)
    _dump(status_path, _status(
        code_sha, now, status=status_name, measured=True, partial=partial,
        opensea_calls=client.calls, rate_limit=_rate(client),
        floor_passing_this_run=len(set(passing)) if not partial else None,
        stop=stop if not partial else None, by_chain=by_chain, rule=run_rule,
        pending=len(state["pending"]), eth_usd=eth_usd if usd_public else None,
        eth_usd_source="chainlink" if usd_public else "fallback", eth_usd_runs=usd_runs, reason=reason,
        rejections=_rejection_counts(rejections), flags=_flag_counts(flags),
        screened=screened, pending_served=served, pending_deferred=deferred,
        passers_footnoted=sum(1 for p in fresh if P.footnoted(p.get("counts"))),
        outage_runs=_outage_count(state),
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


# OpenSea auth failures that are written as a partial reason rather than status "error".
_AUTH_HTTP_REASONS = ("opensea http 401", "opensea http 403")


def _counts_as_outage(status, reason):
    """Red-step cases only: error, no_key, and OpenSea auth (401/403).

    A 429, a 5xx, a missing ETH price, and any other partial stay green. They are still
    counted in the footnote when they did not measure supply.
    """
    if status in ("error", "no_key"):
        return True
    return status == "partial" and reason in _AUTH_HTTP_REASONS


def outage_status_fails(doc):
    """True when the published status must fail the job after the nft-data commit."""
    if not isinstance(doc, dict):
        return True
    return _counts_as_outage(doc.get("status"), doc.get("reason"))


def fail_on_published_status(path):
    """Exit 1 on a published outage. A missing status fails too: a green run must have one."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        print("published status missing or unreadable", file=sys.stderr)
        return 1
    if outage_status_fails(doc):
        print("published status %s (%s)" % (doc.get("status"), doc.get("reason")), file=sys.stderr)
        return 1
    return 0


def _outage_count(state):
    times = state.get("outage_at") if isinstance(state, dict) else None
    return len(times) if isinstance(times, list) else 0


def _append_outage(state, now, early_runs, reason):
    """Record one non-measured run since the anchor. `early_runs` is the measured sample so far."""
    times = state.get("outage_at")
    if not isinstance(times, list):
        times = []
    times.append({
        "t": int(now),
        "early": int(early_runs),
        "reason": P.outage_reason_label(None, reason),
    })
    state["outage_at"] = times
    return len(times)


def _persist_outage(data_dir, now, reason):
    """Count one non-measured run on disk when the v0.3 anchor is already saved.

    Reloads books.json so a failed run cannot write in-memory gas, passes, or a new anchor.
    Returns the count since v03_init_at, or 0 when the anchor is not on disk yet.
    `reason` is stored as the footnote label (auth, http 429, no eth price, no_key, ...).
    """
    _, _, _, books_path = _paths(data_dir)
    if not os.path.exists(books_path):
        return 0
    try:
        with open(books_path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return 0
    if doc.get("state_version") != P.STATE_VERSION or doc.get("v03_init_at") is None:
        return 0
    early = doc.get("first_runs")
    early_n = len(early) if isinstance(early, list) else 0
    count = _append_outage(doc, now, early_n, reason)
    _dump(books_path, doc)
    return count


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
    public_by_chain(doc.get("by_chain"))
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
    parser.add_argument("--data-dir", default=None)
    parser.add_argument("--code-sha", default=os.environ.get("GITHUB_SHA", "local"))
    parser.add_argument("--dry-run", action="store_true",
                        help="snapshot into an empty temp dir and write only the step summary")
    parser.add_argument("--fail-on-outage", metavar="STATUS_JSON",
                        help="exit 1 when this published nft-pilot status is an outage")
    args = parser.parse_args(argv)
    if args.fail_on_outage:
        return fail_on_published_status(args.fail_on_outage)
    if not args.data_dir:
        parser.error("--data-dir is required")
    if args.dry_run and not _dry_dir_ok(args.data_dir):
        print("dry run: --data-dir must be an empty directory outside any git checkout", file=sys.stderr)
        return 2
    now = time.time()
    key = os.environ.get("OPENSEA_API_KEY", "").strip()
    _, status_path, _, _ = _paths(args.data_dir)
    if not key:
        # Count only after the anchor is already saved, so a missing key cannot start the backstop.
        count = _persist_outage(args.data_dir, now, "no key")
        if count or not _no_key_unchanged(status_path, args.code_sha):
            _dump(status_path, _status(args.code_sha, now, status="no_key", reason="no key", outage_runs=count))
        print("no key")
        return 0
    try:
        client = opensea.OpenSea(key)
        gas = fetch_gas()
        run(args.data_dir, client, now, args.code_sha, gas)
        if args.dry_run:
            write_summary(status_path)
    except opensea.AuthError:
        count = _persist_outage(args.data_dir, now, "auth")
        _dump(status_path, _status(args.code_sha, now, status="error", reason="auth", outage_runs=count))
        return 0
    except Exception as exc:
        print(_snapshot_error_line(exc), file=sys.stderr)
        # Return 1 so the commit step is skipped. The local count is not published.
        # The job is already red. See SPEC-v0.3 section 16.
        count = _persist_outage(args.data_dir, now, "error")
        _dump(status_path, _status(args.code_sha, now, status="error", reason="error", outage_runs=count))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
