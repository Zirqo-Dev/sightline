"""Pure helpers for sizing moves correctly (zero AI, fixture-testable).

- LP tokens: DEX liquidity-provider tokens (e.g. Minswap V2 LP assets have
  scrambled 32-byte hex names under one LP policy) are not buys. They are
  labelled LP and never count toward whale size thresholds or buy flags.
- Raw units: on-chain quantities are raw integer units. A move is valued by the
  ADA (lovelace) leg of the transactions that carried it; only when no ADA leg is
  known do we fall back to raw / 10**decimals * pool price. Never raw * price.
- Kill-line dedupe: keyed by ticker + crossed metric names (not live values). A crossed line re-fires
  only when a value is >= refire_deeper_pct deeper than the last reported value; a recovery needs
  hysteresis (back above the line by recover_margin_pct with full data on recover_runs runs).
"""
import hashlib, json

# Policy ID -> DEX label. Minswap V1/V2 are the ones that caused the false whale alert.
DEFAULT_LP_POLICIES = {
    "e4214b7cce62ac6fbba385d164df48e157eae5863521b4b67ca71d86": "minswap_v1",
    "f5808c2c990d86da54bfc97d89cee6efa20cd8461616359478d96b4c": "minswap_v2",
    "0029cb7c88c7567b63d1a512c0ed626aa169688ec980730c0473b913": "sundaeswap_v1",
    "e0302560ced2fdcbfcb2602697df970cd0d6a38f94b32703f51c312b": "sundaeswap_v3",
    "026a18d04a0c642759bb3d83b12e3344894e5c1c7b2aeb1a2113a570": "wingriders_v1",
}


def lp_policies(cfg=None):
    """Known LP policies, extended/overridden by config.json lp_policies {policy: dex}."""
    out = dict(DEFAULT_LP_POLICIES)
    for k, v in ((cfg or {}).get("lp_policies") or {}).items():
        if not k.startswith("_"):
            out[k.lower()] = v
    return out


def lp_dex(unit, pols):
    """DEX label if unit is an LP token, else None."""
    return pols.get((unit or "")[:56].lower())


def ada_leg_detail(stake, utxos, addr_info, pols=None):
    """{unit: {"ada": ada, "qty": raw}} from the wallet's own net lovelace in txs that moved exactly one (non-LP) token.

    utxos = {tx: {'inputs': [(addr, lovelace, {unit: qty})], 'outputs': [...]}}.
    A tx counts only when token and ADA move in opposite directions (a swap leg).
    Txs that touch LP tokens (liquidity add/remove) are skipped. qty is the raw token amount those
    legs paid for, so callers can tell when the tx window does not match the snapshot delta."""
    pols = pols or {}
    out = {}
    for tx in utxos.values():
        lov, toks = 0, {}
        for side, sign in (("inputs", -1), ("outputs", 1)):
            for addr, l, assets in tx.get(side, []):
                if addr_info(addr)[0] != stake:
                    continue
                lov += sign * l
                for u, q in assets.items():
                    toks[u] = toks.get(u, 0) + sign * q
        moved = {u: q for u, q in toks.items() if q}
        if not moved or any(lp_dex(u, pols) for u in moved) or len(moved) != 1:
            continue
        (u, q), = moved.items()
        if lov and (q > 0) != (lov > 0):
            e = out.setdefault(u, {"ada": 0.0, "qty": 0})
            e["ada"] = round(e["ada"] + abs(lov) / 1e6, 6)
            e["qty"] += abs(q)
    return out


def ada_leg_by_unit(stake, utxos, addr_info, pols=None):
    """{unit: ada} (see ada_leg_detail)."""
    return {u: e["ada"] for u, e in ada_leg_detail(stake, utxos, addr_info, pols).items()}


def whole_units(raw, decimals):
    return abs(raw) / (10 ** decimals) if decimals is not None else None


LEG_MATCH_PCT = 5.0  # the ADA leg is used as-is only when its token qty matches the delta this closely


def value_ada(raw_delta, ada_leg=None, decimals=None, price_ada=None, leg_qty=None):
    """(ada_value, basis) for a raw-unit move, or (None, None) when it cannot be sized safely.

    With leg_qty (raw tokens the ADA legs paid for): when it matches |raw_delta| within LEG_MATCH_PCT the
    leg is the value ("ada_leg"); otherwise the tx window and the snapshot delta disagree (older txs in the
    window, token-for-token swaps, transfers), so the delta is valued at the legs' ADA-per-token rate
    ("ada_leg_rate"). Never more ADA than the legs' own price implies for the tokens that actually moved."""
    if ada_leg and leg_qty:
        d = abs(raw_delta)
        if abs(leg_qty - d) <= d * LEG_MATCH_PCT / 100.0:
            return round(ada_leg, 2), "ada_leg"
        return round(ada_leg * d / leg_qty, 2), "ada_leg_rate"
    if ada_leg:
        return round(ada_leg, 2), "ada_leg"
    w = whole_units(raw_delta, decimals)
    if w is not None and price_ada:
        return round(w * price_ada, 2), "decimals_x_price"
    return None, None


DUST_DEFAULTS = {"pct_min_ada": 25.0, "pct_min_qty": 1.0}


def dust_cfg(wcfg=None):
    out = dict(DUST_DEFAULTS)
    out.update({k: v for k, v in ((wcfg or {}).get("dust") or {}).items() if not k.startswith("_")})
    return out


def is_dust(raw_delta, decimals=None, ada_value=None, dc=None):
    """True when a move is too small for its position % to mean anything (e.g. 0.75 STRIKE = 6.4% of a
    12-token leftover). Dust = known ADA value under pct_min_ada, or whole-token size under pct_min_qty.
    With neither value nor decimals known the move is not called dust."""
    dc = dc or DUST_DEFAULTS
    if ada_value is not None and ada_value < dc["pct_min_ada"]:
        return True
    w = whole_units(raw_delta, decimals)
    return w is not None and w < dc["pct_min_qty"]


def kill_signature(crossed):
    """Stable signature of a crossed kill line: the crossed metric NAMES only (no live numbers).

    Accepts {metric: (value, line)} / [metric, ...] or legacy strings like "holders 55 < 80"."""
    names = sorted({str(c).split()[0] for c in crossed})
    return hashlib.sha256(json.dumps(names).encode()).hexdigest()[:12]


KILL_DEFAULTS = {"refire_deeper_pct": 25.0, "recover_margin_pct": 10.0, "recover_runs": 2}


def kill_cfg(cfg=None):
    out = dict(KILL_DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("kill_dedupe") or {}).items() if not k.startswith("_")})
    return out


def parse_crossed(text):
    """Legacy crossed string "metric value < line" -> (metric, value, line) or None."""
    p = str(text).split()
    try:
        return p[0], float(p[1]), float(p[3])
    except (IndexError, ValueError):
        return None


def migrate_kill_entry(entry):
    """Old {sig, crossed: ["holders 55 < 80"], at} -> new {metrics: {name: {value, line}}, at, recover_streak}.
    The values already reported become the baseline, so currently crossed lines do not re-fire."""
    if not isinstance(entry, dict):
        return None
    if "metrics" in entry:
        entry.setdefault("recover_streak", 0)
        return entry
    mets = {}
    for c in entry.get("crossed") or []:
        pc = parse_crossed(c)
        if pc:
            mets[pc[0]] = {"value": pc[1], "line": pc[2]}
    return {"metrics": mets, "at": entry.get("at"), "recover_streak": 0, "migrated_from_sig": entry.get("sig")}


def deeper(value, last, pct):
    """True when value is at least pct% deeper (lower) than the last reported value."""
    if last is None:
        return True
    if last <= 0:
        return value < last
    return value <= last * (1 - pct / 100.0)


def kill_decide(entry, crossed, values, lines, complete, kc):
    """Decide one tracked ticker's kill line for this run.

    entry: migrated kill_reported entry or None. crossed: {metric: (value, line)} currently below the line.
    values/lines: {metric: value or None} / {metric: line} for every metric the kill line checks.
    complete: every checked metric was read. Returns (fire, new_entry_or_None, reasons) where new_entry
    is the state to keep if the report is delivered (fire) or right now (no fire)."""
    entry = migrate_kill_entry(entry) if entry else None
    if crossed:
        prev = (entry or {}).get("metrics") or {}
        reasons = []
        for m, (v, line) in sorted(crossed.items()):
            if m not in prev:
                reasons.append(f"{m} newly crossed")
            elif deeper(v, prev[m].get("value"), kc["refire_deeper_pct"]):
                reasons.append(f"{m} {kc['refire_deeper_pct']:g}%+ deeper")
        if reasons:
            mets = dict(prev)
            for m, (v, line) in crossed.items():
                mets[m] = {"value": v, "line": line}
            return True, {"metrics": mets, "recover_streak": 0}, reasons
        if entry:
            entry["recover_streak"] = 0
        return False, entry, []
    if not entry:
        return False, None, []
    if not complete:
        return False, entry, []  # a data gap is never a recovery (streak unchanged)
    margin = 1 + kc["recover_margin_pct"] / 100.0
    clear = all(values.get(m) is not None and values[m] >= line * margin for m, line in lines.items())
    if not clear:
        entry["recover_streak"] = 0
        return False, entry, []
    entry["recover_streak"] = entry.get("recover_streak", 0) + 1
    if entry["recover_streak"] >= kc["recover_runs"]:
        return False, None, ["recovered"]
    return False, entry, []
