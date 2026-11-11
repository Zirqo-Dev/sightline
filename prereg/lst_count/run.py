"""Hourly LST discount capture. Count only."""

import argparse
import base64
import json
import time
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

from lst_count.b58 import b58decode
from lst_count.gecko import page1_sol_pools, pools_url, reserve_ok, reserve_total
from lst_count.http import RPC_URL, Transport, assert_allowed, rpc_body, rpc_result, with_retries
from lst_count.logic import (
    WINDOW_HOURS,
    arm_after,
    build_verdict,
    consider,
    coverage_of,
    discount_bps,
    floor_hour,
    floor_seconds,
    fmt_bps,
    fmt_ratio,
    has_record,
    hour_start,
    iso,
    parse_iso,
    previous_missing,
    slot_index,
)
from lst_count.quote import buy_url, parse_quote, sell_url, sol_per_lst
from lst_count.rate import RateError, read_mint_decimals, read_msol, read_spl
from lst_count.speccheck import assert_deviations, assert_spec
from lst_count.store import append_jsonl, captures_path, read_jsonl, read_status, status_path, write_status

ROOT = Path(__file__).resolve().parent
REFUSED_DIRS = frozenset({"desk-data", "races-data", "ua-f1-data", "solana-desk"})


def load_manifest(path=None):
    path = Path(path) if path is not None else ROOT / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _refuse_data_dir(data_dir):
    resolved = Path(data_dir).resolve()
    if resolved.name in REFUSED_DIRS or "solana-desk" in resolved.parts:
        raise SystemExit("refusing data dir")


def _get(transport, sleep, url):
    assert_allowed(url)

    def call():
        return transport.request("GET", url)

    return with_retries(call, sleep)


def _post_rpc(transport, sleep, body):
    assert_allowed(RPC_URL)

    def call():
        return transport.request(
            "POST",
            RPC_URL,
            body=body,
            headers={"content-type": "application/json"},
        )

    result = with_retries(call, sleep)
    return rpc_result(result)


def _b64_data(account):
    if not account or not isinstance(account, dict):
        return None
    data = account.get("data")
    if not isinstance(data, list) or not data:
        return None
    try:
        return base64.b64decode(data[0])
    except (ValueError, TypeError):
        return None


def _decimals(chain, cand):
    found = chain["decimals"].get(cand["mint"])
    if found is None:
        return int(cand["decimals"])
    return int(found)


def _read_chain(transport, sleep, manifest):
    epoch_raw = _post_rpc(transport, sleep, rpc_body("getEpochInfo", []))
    epoch = None
    if isinstance(epoch_raw, dict) and isinstance(epoch_raw.get("epoch"), int):
        epoch = epoch_raw["epoch"]
    spl = [cand for cand in manifest["candidates"] if cand["kind"] == "spl"]
    mints = [cand["mint"] for cand in manifest["candidates"]]
    addresses = [cand["pool"] for cand in spl] + mints
    accounts_raw = _post_rpc(
        transport,
        sleep,
        rpc_body("getMultipleAccounts", [addresses, {"encoding": "base64"}]),
    )
    pools = {}
    decimals = {}
    values = []
    if isinstance(accounts_raw, dict):
        values = accounts_raw.get("value") or []
    if len(values) == len(addresses):
        for cand, account in zip(spl, values[:len(spl)]):
            pools[cand["pool"]] = _b64_data(account)
        for mint, account in zip(mints, values[len(spl):]):
            decimals[mint] = read_mint_decimals(_b64_data(account))
    batch_ok = isinstance(accounts_raw, dict) and len(values) == len(addresses)
    marinade = None
    marinade_ok = True
    for cand in manifest["candidates"]:
        if cand["kind"] != "marinade":
            continue
        marinade_ok = False
        body = rpc_body("getAccountInfo", [cand["pool"], {"encoding": "base64"}])
        info = _post_rpc(transport, sleep, body)
        if isinstance(info, dict):
            marinade_ok = True
            marinade = _b64_data(info.get("value"))
    return {
        "epoch": epoch,
        "epoch_ok": epoch is not None,
        "batch_ok": batch_ok,
        "pools": pools,
        "decimals": decimals,
        "marinade": marinade,
        "marinade_ok": marinade_ok,
    }


def _rate_for(cand, chain):
    """Return (status, info). status is ok, rate_stale, rpc_fail, pool_mint_mismatch, or rate_undecodable."""
    mint = b58decode(cand["mint"])
    if cand["kind"] == "marinade":
        raw = chain["marinade"]
        if raw is None:
            return "rpc_fail", None
        try:
            info = read_msol(raw)
        except RateError:
            return "rate_undecodable", None
        if info["mint"] != mint:
            return "pool_mint_mismatch", None
        info["withdrawal_fees"] = None
        info["last_update_epoch"] = None
        return "ok", info
    raw = chain["pools"].get(cand["pool"])
    if raw is None:
        return "rpc_fail", None
    try:
        info = read_spl(raw)
    except RateError:
        return "rate_undecodable", None
    if info["pool_mint"] != mint:
        return "pool_mint_mismatch", None
    if not chain["epoch_ok"]:
        return "rpc_fail", info
    if info["last_update_epoch"] < chain["epoch"]:
        return "rate_stale", info
    return "ok", info


def _json_or_none(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _fetch_parsed(transport, sleep, url):
    result = _get(transport, sleep, url)
    if result.status != 200:
        return None
    return parse_quote(result.raw)


def _fetch_buy(transport, sleep, url, decimals):
    parsed = _fetch_parsed(transport, sleep, url)
    if parsed is None:
        return None
    try:
        price = sol_per_lst(parsed["outAmount"], decimals)
    except ValueError:
        return None
    parsed["price_num"] = str(price.numerator)
    parsed["price_den"] = str(price.denominator)
    parsed["price"] = fmt_ratio(price)
    return parsed


def _fee_public(fees):
    if not fees:
        return None
    out = {}
    for key in (
        "stake_withdrawal_fee",
        "sol_withdrawal_fee",
        "next_stake_withdrawal_fee",
        "next_sol_withdrawal_fee",
    ):
        out[key] = fees.get(key)
    return out


def _base_row(cand, slot, hour, now):
    return {
        "symbol": cand["symbol"],
        "mint": cand["mint"],
        "slot": slot,
        "hour_start": iso(hour),
        "observed_at": iso(now),
    }


def _write_missing(data_dir, records, cand, slot, hour, now, reason, detail=None):
    row = _base_row(cand, slot, hour, now)
    row["type"] = "missing"
    row["reason"] = reason
    if detail:
        row["detail"] = detail
    saved = append_jsonl(captures_path(data_dir), row, now)
    records.append(saved)
    return saved


def _capture_row(data_dir, records, cand, slot, hour, now, info, buy, sell, onset, armed_before, gap):
    price = Fraction(int(buy["price_num"]), int(buy["price_den"]))
    d_bps = discount_bps(price, info["rate"])
    row = _base_row(cand, slot, hour, now)
    row.update({
        "type": "capture",
        "epoch": info.get("epoch"),
        "last_update_epoch": info.get("last_update_epoch"),
        "rate_num": str(info["rate"].numerator),
        "rate_den": str(info["rate"].denominator),
        "rate": fmt_ratio(info["rate"]),
        "withdrawal_fees": _fee_public(info.get("withdrawal_fees")),
        "buy": {
            "outAmount": buy["outAmount"],
            "priceImpactPct": buy["priceImpactPct"],
            "route_labels": buy["route_labels"],
            "raw_sha256": buy["raw_sha256"],
            "price": buy["price"],
        },
        "sell": sell,
        "price_num": buy["price_num"],
        "price_den": buy["price_den"],
        "d_bps_num": str(d_bps.numerator),
        "d_bps_den": str(d_bps.denominator),
        "d_bps": fmt_bps(d_bps),
        "onset": onset,
        "onset_after_gap": bool(onset and gap),
        "armed_before": armed_before,
    })
    saved = append_jsonl(captures_path(data_dir), row, now)
    records.append(saved)
    return saved


def _sell_log(sell):
    if sell is None:
        return {"ok": False, "reason": "quote_fail"}
    return {
        "ok": True,
        "outAmount": sell["outAmount"],
        "priceImpactPct": sell["priceImpactPct"],
        "route_labels": sell["route_labels"],
        "raw_sha256": sell["raw_sha256"],
    }


def _quote_pair(transport, sleep, manifest, cand, decimals):
    sol = manifest["sol_mint"]
    buy = _fetch_buy(transport, sleep, buy_url(sol, cand["mint"]), decimals)
    sell = _fetch_parsed(transport, sleep, sell_url(sol, cand["mint"], decimals))
    return buy, sell


def _one_lst(data_dir, records, manifest, transport, sleep, cand, chain, slot, hour, now):
    if has_record(records, cand["symbol"], slot):
        return
    status, info = _rate_for(cand, chain)
    decimals = _decimals(chain, cand)
    buy, sell = _quote_pair(transport, sleep, manifest, cand, decimals)
    if status != "ok":
        detail = {"quote_ok": buy is not None}
        if info is not None and info.get("last_update_epoch") is not None:
            detail["last_update_epoch"] = info["last_update_epoch"]
        if chain.get("epoch") is not None:
            detail["epoch"] = chain["epoch"]
        _write_missing(data_dir, records, cand, slot, hour, now, status, detail)
        return
    if buy is None:
        _write_missing(data_dir, records, cand, slot, hour, now, "quote_fail")
        return
    info = dict(info)
    info["epoch"] = chain.get("epoch")
    arm = arm_after(records, cand["symbol"])
    armed_before = arm.armed
    price = Fraction(int(buy["price_num"]), int(buy["price_den"]))
    onset = consider(arm, discount_bps(price, info["rate"]), hour)
    gap = previous_missing(records, cand["symbol"], slot)
    _capture_row(
        data_dir, records, cand, slot, hour, now, info, buy, _sell_log(sell),
        onset, armed_before, gap,
    )


def _fill_gaps(data_dir, records, candidates, t0, upto_slot, now):
    """Past slots with no record are no_run. No quote and no RPC."""
    for slot in range(upto_slot):
        hour = hour_start(t0, slot)
        for cand in candidates:
            if has_record(records, cand["symbol"], slot):
                continue
            _write_missing(data_dir, records, cand, slot, hour, now, "no_run")


def _health(status, records, symbols, slot, now, chain_ok):
    cov = coverage_of(records, symbols)
    body = dict(status)
    body.pop("sha256", None)
    body.pop("written_at", None)
    body.update({
        "slot": slot,
        "expected_slots": WINDOW_HOURS,
        "captured": cov["captured"],
        "coverage": fmt_ratio(cov["ratio"]),
        "coverage_per_lst": cov["per_lst"],
        "runs_ok": True,
        "chain_ok": chain_ok,
    })
    return body


def _frozen_candidates(manifest, symbols):
    by_symbol = {cand["symbol"]: cand for cand in manifest["candidates"]}
    return [by_symbol[symbol] for symbol in symbols]


def _close_window(data_dir, records, manifest, status, now):
    t0 = parse_iso(status["t0"])
    candidates = _frozen_candidates(manifest, status["symbols"])
    _fill_gaps(data_dir, records, candidates, t0, WINDOW_HOURS, now)
    report = build_verdict(records, status["symbols"])
    body = {
        "frozen": True,
        "t0": status["t0"],
        "window_end": status["window_end"],
        "symbols": status["symbols"],
        "freeze_log": status.get("freeze_log") or [],
        "runs_ok": True,
        "slot": WINDOW_HOURS,
        "expected_slots": WINDOW_HOURS,
    }
    body.update(report)
    return write_status(status_path(data_dir), body, now)


def _qualify(data_dir, records, manifest, transport, sleep, chain, now):
    # A failed shared read is not a permanent exclusion. The window stays
    # unstarted so the next hour can try the same candidate list again.
    if not chain["epoch_ok"] or not chain["batch_ok"] or not chain["marinade_ok"]:
        return write_status(status_path(data_dir), {
            "frozen": False,
            "runs_ok": True,
            "chain_ok": False,
            "reason": "shared_rpc_fail",
        }, now)
    log = []
    included = []
    held_quotes = {}
    for cand in manifest["candidates"]:
        if cand["mint"] in {row["mint"] for row in manifest.get("excluded_by_rule") or []}:
            log.append({"symbol": cand["symbol"], "included": False, "reason": "excluded_by_rule"})
            continue
        status, info = _rate_for(cand, chain)
        entry = {
            "symbol": cand["symbol"],
            "mint": cand["mint"],
            "kind": cand["kind"],
            "pool": cand["pool"],
            "rate_status": status,
        }
        if info is not None and status in ("ok", "rate_stale"):
            entry["rate"] = fmt_ratio(info["rate"])
            entry["pool_mint_ok"] = True
            if info.get("last_update_epoch") is not None:
                entry["last_update_epoch"] = info["last_update_epoch"]
        else:
            entry["included"] = False
            entry["reason"] = status
            log.append(entry)
            continue
        gecko = _get(transport, sleep, pools_url(cand["mint"]))
        payload = _json_or_none(gecko.raw) if gecko.status == 200 else None
        pools = page1_sol_pools(payload, cand["mint"], manifest["sol_mint"]) if payload else []
        entry["pools"] = [
            {
                "address": pool["address"],
                "pair": pool["pair"],
                "reserve_in_usd": format(pool["reserve_in_usd"], "f"),
            }
            for pool in pools
        ]
        if pools:
            entry["reserve_in_usd"] = format(reserve_total(pools), "f")
        if not reserve_ok(pools):
            entry["included"] = False
            entry["reason"] = "reserve" if pools else "gecko"
            log.append(entry)
            continue
        decimals = _decimals(chain, cand)
        buy, sell = _quote_pair(transport, sleep, manifest, cand, decimals)
        if buy is None:
            entry["included"] = False
            entry["reason"] = "quote_fail"
            log.append(entry)
            continue
        entry["included"] = True
        entry["buy_out_amount"] = buy["outAmount"]
        entry["reason"] = "in" if status == "ok" else status
        log.append(entry)
        included.append(cand)
        held_quotes[cand["symbol"]] = (buy, sell, status, info, decimals)
    if not included:
        return write_status(status_path(data_dir), {
            "frozen": False,
            "freeze_log": log,
            "runs_ok": True,
            "chain_ok": chain["epoch_ok"],
        }, now)
    t0 = floor_hour(now)
    symbols = [cand["symbol"] for cand in included]
    fresh = [cand for cand in included if held_quotes[cand["symbol"]][2] == "ok"]
    if not fresh:
        return write_status(status_path(data_dir), {
            "frozen": False,
            "freeze_log": log,
            "runs_ok": True,
            "chain_ok": chain["epoch_ok"],
            "note": "no successful capture, window not started",
        }, now)
    for cand in included:
        buy, sell, status, info, _decimals_unused = held_quotes[cand["symbol"]]
        if status != "ok":
            _write_missing(
                data_dir, records, cand, 0, t0, now, status,
                {"last_update_epoch": info.get("last_update_epoch"), "epoch": chain.get("epoch")},
            )
            continue
        info = dict(info)
        info["epoch"] = chain.get("epoch")
        arm = arm_after(records, cand["symbol"])
        armed_before = arm.armed
        price = Fraction(int(buy["price_num"]), int(buy["price_den"]))
        onset = consider(arm, discount_bps(price, info["rate"]), t0)
        _capture_row(
            data_dir, records, cand, 0, t0, now, info, buy, _sell_log(sell),
            onset, armed_before, False,
        )
    body = {
        "frozen": True,
        "t0": iso(t0),
        "window_end": iso(t0 + timedelta(hours=WINDOW_HOURS)),
        "symbols": symbols,
        "freeze_log": log,
    }
    health = _health(body, records, symbols, 0, now, chain["epoch_ok"])
    return write_status(status_path(data_dir), health, now)


def run_once(data_dir, transport=None, now=None, sleep=None, manifest=None):
    assert_spec()
    assert_deviations()
    _refuse_data_dir(data_dir)
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    now = floor_seconds(now) if now is not None else floor_seconds(datetime.now(timezone.utc))
    if sleep is None:
        sleep = time.sleep
    if transport is None:
        transport = Transport()
    if manifest is None:
        manifest = load_manifest()
    records = read_jsonl(captures_path(data_dir))
    status = read_status(status_path(data_dir))
    if status.get("verdict"):
        return status
    if not status.get("frozen"):
        chain = _read_chain(transport, sleep, manifest)
        return _qualify(data_dir, records, manifest, transport, sleep, chain, now)
    t0 = parse_iso(status["t0"])
    if now < t0:
        return status
    if now >= parse_iso(status["window_end"]):
        return _close_window(data_dir, records, manifest, status, now)
    slot = slot_index(t0, now)
    candidates = _frozen_candidates(manifest, status["symbols"])
    _fill_gaps(data_dir, records, candidates, t0, slot, now)
    pending = [cand for cand in candidates if not has_record(records, cand["symbol"], slot)]
    chain_ok = True
    if pending:
        chain = _read_chain(transport, sleep, manifest)
        chain_ok = chain["epoch_ok"] and chain["batch_ok"] and chain["marinade_ok"]
        hour = hour_start(t0, slot)
        for cand in pending:
            _one_lst(data_dir, records, manifest, transport, sleep, cand, chain, slot, hour, now)
    health = _health(status, records, status["symbols"], slot, now, chain_ok)
    return write_status(status_path(data_dir), health, now)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lst_count")
    parser.add_argument("--data-dir", required=True)
    args = parser.parse_args(argv)
    run_once(args.data_dir)
