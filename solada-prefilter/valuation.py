"""Pure helpers for sizing moves correctly (zero AI, fixture-testable).

- LP tokens: DEX liquidity-provider tokens (e.g. Minswap V2 LP assets have
  scrambled 32-byte hex names under one LP policy) are not buys. They are
  labelled LP and never count toward whale size thresholds or buy flags.
- Raw units: on-chain quantities are raw integer units. A move is valued by the
  ADA (lovelace) leg of the transactions that carried it; only when no ADA leg is
  known do we fall back to raw / 10**decimals * pool price. Never raw * price.
- Kill-line dedupe signature: a kill line re-fires only when its crossed values change.
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


def ada_leg_by_unit(stake, utxos, addr_info, pols=None):
    """{unit: ada} from the wallet's own net lovelace in txs that moved exactly one (non-LP) token.

    utxos = {tx: {'inputs': [(addr, lovelace, {unit: qty})], 'outputs': [...]}}.
    A tx counts only when token and ADA move in opposite directions (a swap leg).
    Txs that touch LP tokens (liquidity add/remove) are skipped."""
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
            out[u] = round(out.get(u, 0) + abs(lov) / 1e6, 6)
    return out


def whole_units(raw, decimals):
    return abs(raw) / (10 ** decimals) if decimals is not None else None


def value_ada(raw_delta, ada_leg=None, decimals=None, price_ada=None):
    """(ada_value, basis) for a raw-unit move, or (None, None) when it cannot be sized safely."""
    if ada_leg:
        return round(ada_leg, 2), "ada_leg"
    w = whole_units(raw_delta, decimals)
    if w is not None and price_ada:
        return round(w * price_ada, 2), "decimals_x_price"
    return None, None


def kill_signature(crossed):
    """Stable signature of a crossed kill line (its crossed metrics and values)."""
    return hashlib.sha256(json.dumps(sorted(crossed)).encode()).hexdigest()[:12]
