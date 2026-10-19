"""Run one mint end to end and write the report, the index line, and the panel.

Research only. A budget hit, a stuck RPC, or a missing account becomes a partial
report instead of a crash. Known infrastructure is never paged.
"""

from __future__ import annotations

import datetime
import json
import pathlib
import time
import urllib.request

from sol_forensics.cluster import build_clusters
from sol_forensics.parse import (
    PROTOCOL_PROGRAMS,
    TOKEN_PROGRAMS,
    fee_payer,
    find_incoming,
    iter_value_transfers,
    protocol_accounts_created,
    token_deltas,
)
from sol_forensics.rpc import (
    PUBLIC_RPC_URL,
    BudgetExceeded,
    RpcClient,
    RpcMethodError,
    RpcRetriesExhausted,
    transaction_config,
)

PUMP_COIN_URL = "https://frontend-api-v3.pump.fun/coins-v2/{mint}"
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"

DISCLAIMER = (
    "Experimental research. Flags are uncalibrated hypotheses, not trading signals. "
    "No trading, no signing, no keys."
)


def rpc_url_from_env(env=None):
    import os

    source = env if env is not None else os.environ
    url = (source.get("SOLANA_RPC_URL") or "").strip()
    return url or PUBLIC_RPC_URL


def load_config(path):
    path = pathlib.Path(path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    cfg["_base"] = str(path.parent)
    cfg["_config_path"] = str(path)
    return cfg


def _path(cfg, key, default):
    rel = ((cfg.get("paths") or {}).get(key)) or default
    return pathlib.Path(cfg["_base"]) / rel


def labels_from_config(cfg):
    """CEX and infrastructure rows. An entry without a source_url is dropped."""
    labels = {}
    dropped = []
    for kind, key in (("infra", "known_infra"), ("cex", "known_cex")):
        for row in cfg.get(key) or []:
            address = (row or {}).get("address")
            source = (row or {}).get("source_url") or ""
            name = (row or {}).get("name") or kind
            if not address or not source.startswith("http"):
                dropped.append(address or name)
                continue
            labels[address] = {"kind": kind, "name": name, "source_url": source}
    return labels, dropped


def stables_from_config(cfg):
    out = {}
    for row in cfg.get("stablecoins") or []:
        mint = (row or {}).get("mint")
        source = (row or {}).get("source_url") or ""
        if not mint or not source.startswith("http"):
            continue
        out[mint] = row.get("symbol") or "STABLE"
    return out


def _iso(unix):
    if unix is None:
        return None
    return (
        datetime.datetime.fromtimestamp(int(unix), datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "sol_forensics/0.1", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def default_pump_lookup(mint):
    data = _http_json(PUMP_COIN_URL.format(mint=mint))
    if not isinstance(data, dict) or data.get("mint") != mint:
        return None
    if not data.get("bonding_curve"):
        return None
    return data


def default_symbol_lookup(mint):
    data = _http_json(DEXSCREENER_URL.format(mint=mint))
    if not isinstance(data, dict):
        return None
    for pair in data.get("pairs") or []:
        base = pair.get("baseToken") or {}
        if base.get("address") == mint and base.get("symbol"):
            return base["symbol"]
    return None


def _to_seconds(ts):
    if ts is None:
        return None
    try:
        ts = float(ts)
    except (TypeError, ValueError):
        return None
    if ts > 10**12:
        return ts / 1000.0
    return ts


class _Run:
    def __init__(self, rpc):
        self.rpc = rpc
        self.version_unsupported = 0
        self.tx_skipped = 0
        self.transfers = []
        self.memo = {}

    def get_tx(self, signature):
        try:
            tx = self.rpc.call(
                "getTransaction",
                [signature, transaction_config(self.rpc.commitment)],
            )
        except RpcMethodError as err:
            if err.code == -32015:
                self.version_unsupported += 1
            else:
                self.tx_skipped += 1
            return None
        if not tx or (tx.get("meta") or {}).get("err"):
            self.tx_skipped += 1
            return None
        return tx

    def note_transfers(self, tx, signature, mint, stables):
        for tr in iter_value_transfers(tx, mint, stables):
            tr["signature"] = signature
            self.transfers.append(tr)


def page_signatures(rpc, address, max_pages, limit=1000):
    sigs = []
    before = None
    pages = 0
    while pages < max_pages:
        cfg = {"limit": limit}
        if before:
            cfg["before"] = before
        res = rpc.call("getSignaturesForAddress", [address, cfg])
        pages += 1
        if res is None:
            return sigs, pages, False
        if len(res) == 0:
            return sigs, pages, True
        sigs.extend(res)
        if len(res) < limit:
            return sigs, pages, True
        before = res[-1]["signature"]
    return sigs, pages, False


def _merge_sigs(groups):
    by_sig = {}
    for sigs in groups:
        for row in sigs:
            sig = row.get("signature")
            if sig and sig not in by_sig:
                by_sig[sig] = row
    return sorted(
        by_sig.values(),
        key=lambda s: (s.get("slot") or 0, s.get("blockTime") or 0, s.get("signature") or ""),
    )


def _pct(raw, supply):
    if supply in (None, 0) or raw is None:
        return None
    return round(int(raw) / int(supply) * 100, 4)


def _empty_report(mint, now):
    return {
        "tool": {"name": "sol_forensics", "version": "0.1"},
        "experimental": True,
        "disclaimer": DISCLAIMER,
        "mint": mint,
        "symbol": None,
        "symbol_source": None,
        "run_at": _iso(now),
        "run_at_unix": int(now),
        "creation_slot": None,
        "creation_time": None,
        "creation_time_iso": None,
        "dev_wallet": None,
        "dev_pct_supply": None,
        "window": None,
        "buyers": [],
        "holders": [],
        "clusters": [],
        "summary": {
            "pct_supply_slot0": None,
            "pct_supply_window": None,
            "n_buyers_slot0": 0,
            "fresh_wallet_ratio": None,
            "largest_cluster_pct": 0,
            "largest_cluster_still_held_pct": 0,
            "flags": [],
        },
        "data_quality": {
            "partial": False,
            "partial_reason": None,
            "history_too_deep": False,
            "calls_used": {},
            "calls_total": 0,
            "budget": None,
            "wallets_skipped_old": 0,
            "wallets_unresolved_busy": 0,
            "cache_hits": 0,
            "version_unsupported": 0,
            "probable_infra_hubs": [],
        },
    }


def _trace_one(run, wallet, *, max_pages, oldest_txs, stables, labels, buy_time, old_seconds):
    lab = labels.get(wallet)
    if lab:
        return {
            "wallet": wallet,
            "status": "known_" + lab["kind"],
            "label": f"{lab['kind'].upper()}:{lab['name']}",
            "reached_oldest": None,
            "sig_pages": 0,
            "n_sigs": 0,
            "funder": None,
            "paged": False,
        }
    sigs, pages, done = page_signatures(run.rpc, wallet, max_pages)
    rec = {
        "wallet": wallet,
        "status": "no_incoming",
        "label": None,
        "reached_oldest": done,
        "sig_pages": pages,
        "n_sigs": len(sigs),
        "oldest_block_time": None,
        "funder": None,
        "asset": None,
        "mint": None,
        "amount_raw": None,
        "kind": None,
        "signature": None,
        "funded_at": None,
        "paged": True,
    }
    if not done:
        # Do not treat the oldest signature we happened to see as the funder.
        rec["status"] = "unresolved_busy"
        return rec
    if not sigs:
        return rec
    ordered = list(reversed(sigs))
    oldest_bt = ordered[0].get("blockTime")
    rec["oldest_block_time"] = oldest_bt
    age = None
    if buy_time is not None and oldest_bt is not None:
        age = int(buy_time) - int(oldest_bt)
    rec["wallet_age_seconds"] = age
    old = age is not None and old_seconds is not None and age >= old_seconds
    for sig in ordered[:oldest_txs]:
        if sig.get("err"):
            continue
        tx = run.get_tx(sig["signature"])
        if not tx:
            continue
        found = find_incoming(tx, wallet, stables)
        if not found:
            continue
        funder_lab = labels.get(found["funder"])
        rec.update(
            funder=found["funder"],
            asset=found["asset"],
            mint=found["mint"],
            amount_raw=str(found["amount_raw"]),
            kind=found["kind"],
            signature=sig["signature"],
            funded_at=tx.get("blockTime"),
        )
        if funder_lab:
            rec["label"] = f"{funder_lab['kind'].upper()}:{funder_lab['name']}"
        rec["status"] = "old_wallet" if old else "traced"
        return rec
    rec["status"] = "old_wallet" if old else "no_incoming"
    return rec


def _walk_hops(run, wallet, *, hops, cache, stats, **kwargs):
    """Trace `wallet` and up to `hops` funders. Returns the hop list for `wallet`."""
    chain = []
    seen = set()
    current = wallet
    for depth in range(1, hops + 1):
        if not current or current in seen:
            break
        seen.add(current)
        if current in cache:
            rec = cache[current]
            stats["cache_hits"] += 1
        elif current in run.memo:
            rec = run.memo[current]
        else:
            rec = _trace_one(run, current, **kwargs)
            if rec.get("status") == "unresolved_busy":
                # Remember it for this run only. A later run with a higher page cap can retry.
                run.memo[current] = rec
            else:
                cache[current] = rec
        hop = {
            "hop": depth,
            "wallet": current,
            "status": rec.get("status"),
            "funder": rec.get("funder"),
            "asset": rec.get("asset"),
            "amount_raw": rec.get("amount_raw"),
            "kind": rec.get("kind"),
            "funded_at": rec.get("funded_at"),
            "signature": rec.get("signature"),
            "label": rec.get("label"),
            "reached_oldest": rec.get("reached_oldest"),
            "wallet_age_seconds": rec.get("wallet_age_seconds"),
        }
        chain.append(hop)
        if not rec.get("funder") or rec.get("label"):
            break
        current = rec["funder"]
    return chain


def _still_holding(rpc, wallet, mint):
    res = rpc.call(
        "getTokenAccountsByOwner",
        [wallet, {"mint": mint}, {"encoding": "jsonParsed"}],
    )
    total = 0
    if not res:
        return None
    for item in res.get("value") or []:
        try:
            info = item["account"]["data"]["parsed"]["info"]
            total += int(info["tokenAmount"]["amount"])
        except (KeyError, TypeError, ValueError):
            continue
    return total


def _account_owner_kind(rpc, address):
    """Return 'program', 'protocol_pda', 'token_account', or 'wallet'."""
    res = rpc.call("getAccountInfo", [address, {"encoding": "jsonParsed"}])
    value = (res or {}).get("value") if isinstance(res, dict) else None
    if not value:
        return "missing"
    if value.get("executable"):
        return "program"
    owner = value.get("owner")
    if owner in PROTOCOL_PROGRAMS:
        return "protocol_pda"
    if owner in TOKEN_PROGRAMS:
        return "token_account"
    return "wallet"


def analyse(
    mint,
    *,
    config,
    rpc,
    now=None,
    write=True,
    holders=False,
    progress=False,
    hops=None,
    window_buys=None,
    window_seconds=None,
    max_pages=None,
    wallet_pages=None,
    top_buyers=None,
    pump_lookup=None,
    symbol_lookup=None,
):
    now = time.time() if now is None else now
    win = config.get("window") or {}
    flags_cfg = config.get("flags") or {}
    hops = win.get("hops", 3) if hops is None else hops
    window_buys = win.get("buys", 30) if window_buys is None else window_buys
    window_seconds = win.get("seconds", 30) if window_seconds is None else window_seconds
    max_pages = win.get("sig_pages", 50) if max_pages is None else max_pages
    wallet_pages = win.get("wallet_pages", 5) if wallet_pages is None else wallet_pages
    top_buyers = win.get("top_buyers", 100) if top_buyers is None else top_buyers
    oldest_txs = int(win.get("oldest_txs", 3))
    fresh_seconds = float(win.get("fresh_hours", 24)) * 3600
    old_seconds = float(win.get("old_wallet_days", 30)) * 86400
    hub_k = int(win.get("hub_k", 25))
    proximity = int(win.get("fund_proximity_seconds", 600))
    hub_tight = int(win.get("hub_tight_seconds", 600))

    labels, dropped_labels = labels_from_config(config)
    stables = stables_from_config(config)
    report = _empty_report(mint, now)
    report["data_quality"]["budget"] = rpc.budget
    report["data_quality"]["labels_dropped"] = dropped_labels
    report["window"] = {
        "buys": window_buys,
        "seconds": window_seconds,
        "hops": hops,
        "sig_pages": max_pages,
        "wallet_pages": wallet_pages,
        "top_buyers": top_buyers,
    }
    run = _Run(rpc)
    cache = _load_cache(_path(config, "cache", "cache/funding.json"))
    stats = {"cache_hits": 0, "busy": 0, "old": 0}
    partial_reason = None
    supply_raw = None

    def say(message):
        if progress:
            print(message, flush=True)

    run.say = say

    try:
        pump_lookup = pump_lookup if pump_lookup is not None else default_pump_lookup
        symbol_lookup = symbol_lookup if symbol_lookup is not None else default_symbol_lookup
        symbol = symbol_lookup(mint)
        if symbol:
            report["symbol"] = symbol
            report["symbol_source"] = "dexscreener"
        pump = pump_lookup(mint)
        if pump:
            report["pump"] = {
                "bonding_curve": pump.get("bonding_curve"),
                "associated_bonding_curve": pump.get("associated_bonding_curve"),
                "creator": pump.get("creator"),
                "created_timestamp": pump.get("created_timestamp"),
                "symbol": pump.get("symbol"),
                "complete": pump.get("complete"),
                "token_program": pump.get("token_program"),
                "quote_mint": pump.get("quote_mint"),
            }
        supply = _fetch_supply(rpc, mint)
        report["supply"] = supply
        supply_raw = int(supply["amount"]) if supply else None
        say(f"supply {'ok' if supply else 'missing'} calls={rpc.used}")

        history_address = mint
        history_kind = "mint"
        if pump and pump.get("bonding_curve"):
            history_address = pump["bonding_curve"]
            history_kind = "bonding_curve"
        sigs, pages, done = page_signatures(rpc, history_address, max_pages)
        report["data_quality"]["creation_source"] = history_kind
        report["data_quality"]["creation_pages"] = pages
        mint_capped = False
        if history_kind == "bonding_curve" and done:
            mint_sigs, mint_pages, mint_done = page_signatures(rpc, mint, max_pages)
            report["data_quality"]["mint_pages"] = mint_pages
            if not mint_done:
                mint_capped = True
            sigs = _merge_sigs([sigs, mint_sigs])
        else:
            sigs = _merge_sigs([sigs])
        report["data_quality"]["mint_history_capped"] = mint_capped
        if not done:
            report["data_quality"]["history_too_deep"] = True
            say(f"history too deep after {pages} pages calls={rpc.used}")
        else:
            say(f"creation source {history_kind} signatures={len(sigs)} calls={rpc.used}")
            _fill_buyers(
                report,
                run,
                sigs,
                mint=mint,
                pump=pump,
                stables=stables,
                supply_raw=supply_raw,
                window_buys=window_buys,
                window_seconds=window_seconds,
                labels=labels,
            )
            _analyse_selected(
                report,
                run,
                cache,
                stats,
                mint=mint,
                supply_raw=supply_raw,
                top_buyers=top_buyers,
                hops=hops,
                wallet_pages=wallet_pages,
                oldest_txs=oldest_txs,
                stables=stables,
                labels=labels,
                fresh_seconds=fresh_seconds,
                old_seconds=old_seconds,
            )
        if holders:
            _fill_holders(
                report,
                run,
                cache,
                stats,
                mint=mint,
                pump=pump,
                supply_raw=supply_raw,
                hops=hops,
                wallet_pages=wallet_pages,
                oldest_txs=oldest_txs,
                stables=stables,
                labels=labels,
                fresh_seconds=fresh_seconds,
                old_seconds=old_seconds,
            )
    except BudgetExceeded:
        partial_reason = "call budget reached"
    except RpcRetriesExhausted as err:
        partial_reason = "RPC retries exhausted (" + err.detail + ")"
    except Exception as err:
        partial_reason = "stopped: " + type(err).__name__
    try:
        _cluster_and_summarise(
            report,
            run.transfers,
            labels=labels,
            hub_k=hub_k,
            proximity=proximity,
            hub_tight=hub_tight,
            flags_cfg=flags_cfg,
            supply_raw=supply_raw,
            fresh_seconds=fresh_seconds,
        )
    except Exception:
        if not partial_reason:
            partial_reason = "stopped: cluster"
    if partial_reason:
        report["data_quality"]["partial"] = True
        report["data_quality"]["partial_reason"] = partial_reason
    report["data_quality"]["calls_used"] = dict(rpc.calls)
    report["data_quality"]["calls_total"] = rpc.used
    report["data_quality"]["budget"] = rpc.budget
    old_n, busy_n = _status_counts(report)
    report["data_quality"]["wallets_skipped_old"] = old_n
    report["data_quality"]["wallets_unresolved_busy"] = busy_n
    report["data_quality"]["cache_hits"] = stats["cache_hits"]
    report["data_quality"]["version_unsupported"] = run.version_unsupported
    if write:
        _persist(config, report, cache)
    return report


def _fetch_supply(rpc, mint):
    res = rpc.call("getTokenSupply", [mint])
    value = (res or {}).get("value") if isinstance(res, dict) else None
    if not value or value.get("amount") is None:
        return None
    return {
        "amount": str(value["amount"]),
        "decimals": value.get("decimals"),
        "uiAmountString": value.get("uiAmountString"),
    }


def _fill_buyers(report, run, sigs, *, mint, pump, stables, supply_raw, window_buys, window_seconds, labels):
    if not sigs:
        report["data_quality"]["partial"] = True
        report["data_quality"]["partial_reason"] = "no signatures"
        return
    oldest = sigs[0]
    create = run.get_tx(oldest["signature"])
    if not create:
        report["data_quality"]["partial"] = True
        report["data_quality"]["partial_reason"] = "create transaction unavailable"
        return
    creation_slot = create.get("slot")
    creation_time = create.get("blockTime")
    dev = fee_payer(create)
    report["creation_slot"] = creation_slot
    report["creation_time"] = creation_time
    report["creation_time_iso"] = _iso(creation_time)
    report["dev_wallet"] = dev
    report["creation_signature"] = oldest["signature"]
    pump_seconds = _to_seconds((pump or {}).get("created_timestamp")) if pump else None
    if pump_seconds is not None and creation_time is not None:
        report["data_quality"]["pump_time_matches"] = abs(pump_seconds - creation_time) <= 2
        report["data_quality"]["pump_created_time"] = int(pump_seconds)
    skip = set(protocol_accounts_created(create))
    skip.add(mint)
    if pump:
        for key in ("bonding_curve", "associated_bonding_curve"):
            if pump.get(key):
                skip.add(pump[key])
        if pump.get("complete") and pump.get("pool_address"):
            skip.add(pump["pool_address"])
    run.note_transfers(create, oldest["signature"], mint, stables)

    # events keyed by wallet, summed
    buyers = {}
    buy_txs = 0
    scanned = 0
    for sig in sigs:
        if sig.get("err"):
            continue
        bt = sig.get("blockTime")
        in_time = (
            bt is not None
            and creation_time is not None
            and bt <= creation_time + window_seconds
        )
        if bt is not None and not in_time and buy_txs >= window_buys:
            break
        scanned += 1
        tx = create if sig["signature"] == oldest["signature"] else run.get_tx(sig["signature"])
        if not tx:
            continue
        if bt is None:
            bt = tx.get("blockTime")
            in_time = (
                bt is not None
                and creation_time is not None
                and bt <= creation_time + window_seconds
            )
        deltas, _missing = token_deltas(tx, mint)
        fp = fee_payer(tx)
        run.note_transfers(tx, sig["signature"], mint, stables)
        found = False
        for owner, amt in deltas.items():
            if amt <= 0 or owner in skip:
                continue
            found = True
            slot = tx.get("slot")
            offset = None if slot is None or creation_slot is None else int(slot) - int(creation_slot)
            prev = buyers.get(owner)
            if prev is None:
                buyers[owner] = {
                    "wallet": owner,
                    "slot": slot,
                    "slot_offset": offset,
                    "block_time": bt,
                    "amount_raw": int(amt),
                    "signature": sig["signature"],
                    "fee_payer": fp,
                    "fee_payer_signature": sig["signature"] if fp and fp != owner else None,
                    "fee_payer_is_buyer": fp == owner,
                    "label": _label_text(labels.get(owner)),
                }
            else:
                prev["amount_raw"] += int(amt)
                if slot is not None and (prev["slot"] is None or slot < prev["slot"]):
                    prev["slot"] = slot
                    prev["slot_offset"] = offset
                    prev["block_time"] = bt
                    prev["signature"] = sig["signature"]
                if fp and fp != owner:
                    prev["fee_payer"] = fp
                    prev["fee_payer_is_buyer"] = False
                    prev["fee_payer_signature"] = sig["signature"]
        if found:
            buy_txs += 1
            if buy_txs >= window_buys and not in_time:
                break
    report["window"]["txs_scanned"] = scanned
    report["window"]["buy_txs"] = buy_txs
    report["window"]["skipped_owners"] = sorted(skip)
    ordered = sorted(buyers.values(), key=lambda b: (b["slot"] or 0, -b["amount_raw"], b["wallet"]))
    for row in ordered:
        row["pct_supply"] = _pct(row["amount_raw"], supply_raw)
        row["amount_raw"] = str(row["amount_raw"])
    report["buyers"] = ordered
    dev_raw = 0
    for row in ordered:
        if row["wallet"] == dev:
            dev_raw = int(row["amount_raw"])
    report["dev_pct_supply"] = _pct(dev_raw, supply_raw)


def _label_text(lab):
    if not lab:
        return None
    return f"{lab['kind'].upper()}:{lab['name']}"


def _analyse_selected(report, run, cache, stats, **kwargs):
    buyers = report["buyers"]
    if not buyers:
        return
    slot0 = [b for b in buyers if b.get("slot_offset") == 0]
    top = sorted(buyers, key=lambda b: (-int(b["amount_raw"]), b["wallet"]))[: kwargs["top_buyers"]]
    chosen = []
    seen = set()
    for row in slot0 + top:
        if row["wallet"] not in seen:
            seen.add(row["wallet"])
            chosen.append(row)
    supply_raw = kwargs["supply_raw"]
    for row in chosen:
        row["analysed"] = True
        getattr(run, "say", lambda _m: None)(
            f"trace {row['wallet'][:8]}… slot_offset={row.get('slot_offset')} calls={run.rpc.used}"
        )
        try:
            held = _still_holding(run.rpc, row["wallet"], kwargs["mint"])
        except (BudgetExceeded, RpcRetriesExhausted):
            raise
        except RpcMethodError:
            held = None
        row["still_holding_raw"] = None if held is None else str(held)
        row["still_holding_pct"] = _pct(held, supply_raw)
        buy_time = row.get("block_time") or report.get("creation_time")
        hops = _walk_hops(
            run,
            row["wallet"],
            hops=kwargs["hops"],
            cache=cache,
            stats=stats,
            max_pages=kwargs["wallet_pages"],
            oldest_txs=kwargs["oldest_txs"],
            stables=kwargs["stables"],
            labels=kwargs["labels"],
            buy_time=buy_time,
            old_seconds=kwargs["old_seconds"],
        )
        _attach_funding(row, hops, buy_time, kwargs["fresh_seconds"])
    for row in buyers:
        row.setdefault("analysed", False)


def _attach_funding(row, hops, buy_time, fresh_seconds):
    first = hops[0] if hops else {}
    funded_at = first.get("funded_at")
    fresh = False
    if (
        first.get("funder")
        and isinstance(funded_at, (int, float))
        and isinstance(buy_time, (int, float))
        and 0 <= (buy_time - funded_at) < fresh_seconds
    ):
        fresh = True
    age = first.get("wallet_age_seconds")
    if age is None and isinstance(funded_at, (int, float)) and isinstance(buy_time, (int, float)):
        age = int(buy_time - funded_at)
    row["funding"] = {
        "status": first.get("status"),
        "fresh": fresh,
        "wallet_age_at_buy_seconds": age,
        "hops": [
            {k: h.get(k) for k in (
                "hop",
                "wallet",
                "funder",
                "asset",
                "amount_raw",
                "kind",
                "funded_at",
                "signature",
                "label",
                "status",
            )}
            for h in hops
        ],
    }


def _fill_holders(report, run, cache, stats, **kwargs):
    res = run.rpc.call("getTokenLargestAccounts", [kwargs["mint"]])
    rows = (res or {}).get("value") if isinstance(res, dict) else None
    if not rows:
        return
    pump = kwargs["pump"] or {}
    skip = set()
    if pump.get("bonding_curve"):
        skip.add(pump["bonding_curve"])
    if pump.get("complete") and pump.get("pool_address"):
        skip.add(pump["pool_address"])
    already = {b["wallet"] for b in report["buyers"]}
    supply_raw = kwargs["supply_raw"]
    for item in rows[:20]:
        amount = int(item.get("amount") or 0)
        if amount <= 0:
            continue
        token_account = item.get("address")
        info = run.rpc.call("getAccountInfo", [token_account, {"encoding": "jsonParsed"}])
        value = (info or {}).get("value") if isinstance(info, dict) else None
        owner = None
        try:
            owner = value["data"]["parsed"]["info"]["owner"]
        except (TypeError, KeyError):
            owner = None
        if not owner or owner in skip:
            continue
        kind = _account_owner_kind(run.rpc, owner)
        if kind in ("program", "protocol_pda", "token_account"):
            continue
        held = amount
        if owner not in already:
            try:
                queried = _still_holding(run.rpc, owner, kwargs["mint"])
            except RpcMethodError:
                queried = None
            if queried is not None:
                held = queried
        row = {
            "token_account": token_account,
            "owner": owner,
            "amount_raw": str(held),
            "pct_supply": _pct(held, supply_raw),
            "label": _label_text(kwargs["labels"].get(owner)),
            "source": "holder",
        }
        report["holders"].append(row)
        if owner in already:
            continue
        hops = _walk_hops(
            run,
            owner,
            hops=kwargs["hops"],
            cache=cache,
            stats=stats,
            max_pages=kwargs["wallet_pages"],
            oldest_txs=kwargs["oldest_txs"],
            stables=kwargs["stables"],
            labels=kwargs["labels"],
            buy_time=report.get("creation_time"),
            old_seconds=kwargs["old_seconds"],
        )
        row["funding"] = {
            "status": (hops[0].get("status") if hops else None),
            "hops": hops,
        }
        # Stash a buyer-shaped node so clustering can see holder-only wallets.
        report.setdefault("_holder_nodes", []).append(
            {
                "wallet": owner,
                "slot": None,
                "slot_offset": None,
                "amount_raw": "0",
                "still_holding_raw": str(held),
                "signature": None,
                "fee_payer": None,
                "fee_payer_signature": None,
                "funding": row["funding"],
            }
        )


def _cluster_and_summarise(report, transfers, *, labels, hub_k, proximity, hub_tight, flags_cfg, supply_raw, fresh_seconds):
    nodes = {}
    for row in report["buyers"]:
        if not row.get("analysed"):
            continue
        nodes[row["wallet"]] = _node_from_buyer(row, supply_raw)
    for row in report.get("_holder_nodes") or []:
        if row["wallet"] not in nodes:
            nodes[row["wallet"]] = _node_from_buyer(row, supply_raw)
    report.pop("_holder_nodes", None)
    wallet_set = set(nodes)
    kept = []
    seen_pairs = set()
    for tr in transfers:
        if tr["src"] in wallet_set and tr["dst"] in wallet_set:
            key = (tr["src"], tr["dst"], tr.get("signature"), tr.get("asset"))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            kept.append(tr)
    clusters, extra = build_clusters(
        nodes,
        kept,
        labels=labels,
        hub_k=hub_k,
        proximity_seconds=proximity,
        hub_tight_seconds=hub_tight,
    )
    report["clusters"] = clusters
    report["data_quality"]["probable_infra_hubs"] = extra["probable_infra_hubs"]

    buyers = report["buyers"]
    slot0 = [b for b in buyers if b.get("slot_offset") == 0]
    slot0_raw = sum(int(b["amount_raw"]) for b in slot0)
    window_raw = sum(int(b["amount_raw"]) for b in buyers)
    analysed = [b for b in buyers if b.get("analysed")]
    fresh = [b for b in analysed if (b.get("funding") or {}).get("fresh")]
    ratio = None
    if analysed:
        ratio = round(len(fresh) / len(analysed), 4)
    largest = clusters[0] if clusters else None
    summary = report["summary"]
    summary["pct_supply_slot0"] = _pct(slot0_raw, supply_raw)
    summary["pct_supply_window"] = _pct(window_raw, supply_raw)
    summary["n_buyers_slot0"] = len(slot0)
    summary["fresh_wallet_ratio"] = ratio
    summary["largest_cluster_pct"] = (largest or {}).get("pct_supply_bought") or 0
    summary["largest_cluster_still_held_pct"] = (largest or {}).get("pct_supply_still_held") or 0
    summary["flags"] = _flags(report, flags_cfg)


def _node_from_buyer(row, supply_raw):
    funding = row.get("funding") or {}
    return {
        "slot": row.get("slot"),
        "slot_offset": row.get("slot_offset"),
        "amount_raw": int(row.get("amount_raw") or 0),
        "still_raw": int(row.get("still_holding_raw") or 0),
        "supply": supply_raw,
        "signature": row.get("signature"),
        "fee_payer": None if row.get("fee_payer_is_buyer", True) else row.get("fee_payer"),
        "fee_payer_signature": row.get("fee_payer_signature"),
        "hops": funding.get("hops") or [],
    }


def _flags(report, cfg):
    s = report["summary"]
    out = []

    def over(code, value, default):
        threshold = cfg.get(code, default)
        if value is None:
            return
        if code == "FRESH_HEAVY":
            if float(value) > float(threshold):
                out.append(code)
        elif float(value) > float(threshold):
            out.append(code)

    over("HIGH_SNIPE", s.get("pct_supply_slot0"), 20)
    over("CLUSTERED", s.get("largest_cluster_pct"), 10)
    over("FRESH_HEAVY", s.get("fresh_wallet_ratio"), 0.5)
    over("DEV_HEAVY", report.get("dev_pct_supply"), 5)
    over("STILL_LOADED", s.get("largest_cluster_still_held_pct"), 5)
    dev = report.get("dev_wallet")
    for buyer in report["buyers"]:
        if buyer["wallet"] != dev and buyer.get("fee_payer") == dev and not buyer.get("fee_payer_is_buyer", True):
            out.append("DEV_PAID_BUY")
            break
    return out


def _status_counts(report):
    old = busy = 0
    seen = set()
    rows = [b for b in report.get("buyers") or [] if b.get("analysed")]
    analysed = {b["wallet"] for b in rows}
    for holder in report.get("holders") or []:
        if holder.get("owner") not in analysed and holder.get("funding"):
            rows.append(holder)
    for row in rows:
        wallet = row.get("wallet") or row.get("owner")
        if wallet in seen:
            continue
        seen.add(wallet)
        status = (row.get("funding") or {}).get("status")
        if status == "old_wallet":
            old += 1
        elif status == "unresolved_busy":
            busy += 1
    return old, busy


def _load_cache(path):
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return {}
    return {}


def _persist(config, report, cache):
    reports = _path(config, "reports", "reports")
    index = _path(config, "index", "index.jsonl")
    cache_path = _path(config, "cache", "cache/funding.json")
    panel = _path(config, "panel", "panel.json")
    _write_json(reports / f"{report['mint']}.json", report)
    index.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "mint": report["mint"],
        "symbol": report.get("symbol"),
        "run_at": report["run_at"],
        "run_at_unix": report["run_at_unix"],
        "partial": report["data_quality"]["partial"],
        "history_too_deep": report["data_quality"]["history_too_deep"],
        "n_buyers": len(report["buyers"]),
        "n_buyers_slot0": report["summary"]["n_buyers_slot0"],
        "largest_cluster_pct": report["summary"]["largest_cluster_pct"],
        "largest_cluster_still_held_pct": report["summary"]["largest_cluster_still_held_pct"],
        "flags": report["summary"]["flags"],
        "calls_total": report["data_quality"]["calls_total"],
        "calls_used": report["data_quality"]["calls_used"],
    }
    with index.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(line) + "\n")
    _write_json(cache_path, cache)
    _write_panel(panel, reports)


def _write_panel(panel_path, reports_dir):
    """Notes for the dashboard. Flag codes are not rendered here."""
    notes = {
        "disclaimer": (
            "Experimental forensics. Early-buyer and funding notes are hypotheses about "
            "on-chain history. They are not trading signals. Uncalibrated labels stay in "
            "the report JSON and are not shown on this panel."
        )
    }
    if reports_dir.is_dir():
        for path in sorted(reports_dir.glob("*.json")):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            sym = report.get("symbol") or (report.get("pump") or {}).get("symbol") or report["mint"][:8]
            summary = report.get("summary") or {}
            quality = report.get("data_quality") or {}
            bits = []
            if quality.get("history_too_deep"):
                bits.append("History is deeper than the page cap, so early buyers were not traced.")
            if quality.get("partial"):
                bits.append("This run is partial.")
            bits.append(
                f"Slot-0 buyers: {summary.get('n_buyers_slot0', 0)}. "
                f"Window supply bought: {summary.get('pct_supply_window')}%. "
                f"Largest cluster bought {summary.get('largest_cluster_pct')}% "
                f"and still holds {summary.get('largest_cluster_still_held_pct')}%."
            )
            dev = report.get("dev_wallet")
            if dev:
                bits.append(f"Create fee payer {dev}.")
            clusters = report.get("clusters") or []
            if clusters:
                bits.append(
                    "Largest cluster " + clusters[0]["cluster_id"] + ": "
                    + ", ".join(clusters[0]["wallets"][:6])
                    + "."
                )
            notes[sym] = " ".join(bits)
    _write_json(panel_path, notes)


def recently_reported(index_path, mint, *, now, hours=24):
    if not index_path.is_file():
        return False
    cutoff = now - hours * 3600
    latest = None
    for line in index_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("mint") == mint and not row.get("partial"):
            latest = row
    if not latest:
        return False
    return float(latest.get("run_at_unix") or 0) >= cutoff


def make_client(config, url, budget=None, transport=None, now=None, sleep=None):
    rpc_cfg = config.get("rpc") or {}
    return RpcClient(
        url,
        budget=rpc_cfg.get("budget", 5000) if budget is None else budget,
        max_rps=rpc_cfg.get("max_rps", 8),
        max_retries=rpc_cfg.get("max_retries", 5),
        commitment=rpc_cfg.get("commitment", "confirmed"),
        transport=transport,
        now=now,
        sleep=sleep,
    )
