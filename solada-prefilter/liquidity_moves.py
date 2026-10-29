"""SPD-55 liquidity moves, phase 1 (zero AI, free Koios public tier, no key).

Writes desk-data:solada-prefilter/liquidity_moves.json for Snek's cardano_top10 (desk log, current
row, else the newest history row). A row's full unit comes from the row, pulse/config, the private
cache, or known_units.json. Tracks Minswap, SundaeSwap and Splash pool reserves: liquidity
adds, liquidity pulls, and big swaps. ahead_of_dump uses the Balanced floors in AHEAD_OF_DUMP.

Swaps are the pool's own reserve change. The batcher signs the transaction. actor_tag is a w- hash
or a role label, never an address or a stake key. It is set on an add or a pull when a key wallet
moved the LP token, and on a sell when a key wallet in that transaction spent the token. A
batcher-only swap stays untagged and cannot set ahead_of_dump.

ADA is the pool reserve's ADA leg (lovelace in versus out), never raw quantity times price.
Token display keys are policy-based (whale_ledger.assign_token_keys, PR #37). LP tokens are
labelled ticker "LP". Full tx hashes stay in the private Actions cache. A failure here is swallowed
by the pre-filter so the webhook, state.json and kill_status.json still finish.

Koios call budget per run (default max_calls 36, shared pace): 1 tip, then per token one LP or
marker asset_txs plus tx_info batches under 1 KB. Blockfrost is used only when a Koios read
returns nothing, and only up to max_bf_calls (default 12). The job stops at its own budget_s,
measured from when the slice starts, and at the run's late-slice end, whichever is sooner.
Tokens are scanned least-recently-checked first. The private cache stores each ticker's
last_checked time. That time moves only after a successful read. A budget skip, or a token
opened before any page comes back, keeps its previous stamp, so later runs cover it first.
budget_s and the Koios cap stay where they are.
"""
import hashlib, json, os, sys, time
from datetime import datetime, timezone

import dev_checks as DC
import flags as F
import koios_tx as KT
import valuation as V
import whale_ledger as WL

KOIOS = "https://api.koios.rest/api/v1"
BF = "https://cardano-mainnet.blockfrost.io/api/v0"
BLOCKS_PER_HOUR = 180

# Public protocol script payment credentials and LP policies. Not wallets.
MINSWAP_V2_SCRIPT = "ea07b733d932129c378af627436e7cbc2ef0bf96e0036bb51b3bde6b"
MINSWAP_V1_SCRIPT = "e1317b152faac13426e6a83e06ff88a4d62cce3c1634ab0a5ec13309"
MINSWAP_V2_LP = "f5808c2c990d86da54bfc97d89cee6efa20cd8461616359478d96b4c"
MINSWAP_V1_LP = "e4214b7cce62ac6fbba385d164df48e157eae5863521b4b67ca71d86"
SUNDAE_V3_LP = "e0302560ced2fdcbfcb2602697df970cd0d6a38f94b32703f51c312b"
SUNDAE_V1_LP = "0029cb7c88c7567b63d1a512c0ed626aa169688ec980730c0473b913"
SCRIPTS = {MINSWAP_V2_SCRIPT: "minswap_v2", MINSWAP_V1_SCRIPT: "minswap_v1"}
LP_DEX = {
    MINSWAP_V2_LP: "minswap_v2", MINSWAP_V1_LP: "minswap_v1",
    SUNDAE_V3_LP: "sundaeswap_v3", SUNDAE_V1_LP: "sundaeswap_v1",
}
DEX_LP = {v: k for k, v in LP_DEX.items()}
TRACKED_DEX = ("minswap_v1", "minswap_v2", "sundaeswap_v1", "sundaeswap_v3", "splash")
# Minswap V2 auth tokens share one name across every pool (MSP, factory, global setting).
SHARED_AUTH_NAMES = {"4d5350", "4d5346", "4d534753"}

# Balanced ahead_of_dump floors. One block, four knobs. config.json liquidity_moves.ahead_of_dump
# overrides these. A pull counts only at or above pull_reserve_pct of the pool reserve (ADA in the
# pool before the pull) and pull_min_ada. The flag is set only when sells from that same party
# (same w- hash or same dev/role tag), inside window_h, total at least sell_of_pull_pct of the pull.
AHEAD_OF_DUMP = {
    "pull_reserve_pct": 20,
    "pull_min_ada": 2000,
    "sell_of_pull_pct": 25,
    "window_h": 6,
}

DEFAULTS = {
    "enabled": True, "budget_s": 50, "max_calls": 36, "max_bf_calls": 12, "utxo_batch": 10,
    "max_tokens": 10, "max_txs_per_pool": 15, "discover_txs": 12, "discover_every_h": 24,
    "min_swap_ada": 5000, "min_lp_ada": 1000, "min_pool_ada": 500,
    "ahead_of_dump": dict(AHEAD_OF_DUMP),
    "keep_h": 72, "keep_rows": 400, "keep_seen": 4000,
}

NOTE = (
    "Research only, for Snek. Swaps are read at pool level: the batcher signs the transaction, "
    "so a sell is tagged only when a key wallet in that transaction spent the token. actor_tag is a "
    "w- hash or a role label, on a liquidity add or pull and on such a sell. ADA is the pool "
    "reserve's ADA leg, not raw quantity times price. LP tokens are labelled LP."
)
LIMITS = [
    "Koios: at most max_calls per run (default 36), on the shared pre-filter pace. One tip, then one asset_txs page per pool marker and tx_info batches kept under 1 KB.",
    "Blockfrost runs only after a Koios read returns nothing, and only up to max_bf_calls (default 12). An empty BLOCKFROST_PROJECT_ID skips it.",
    "Minswap V2 markers are the derived LP asset name. Sundae and Splash pools are recognized when a continuing pool UTxO is inside a transaction this job already read. Discovery of those pools rechecks the newest token transactions at most every discover_every_h.",
    "The job gets budget_s from the moment it starts (default 50s), and still stops before the process kill. A capped pool resumes next run. Nothing here changes desk rules, kill lines, or the webhook.",
    "Tokens are scanned least-recently-checked first. The private cache stores each ticker's last_checked time. That time is set only after a successful read. A token skipped for budget, or opened before any page comes back, keeps its previous stamp, so the next run reads it first. budget_s and max_calls do not change. tokens[].last_checked is that time in UTC, or null.",
]


def _ada(v):
    """A real number, or None. Booleans are not ADA."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _overlay_floors(floors, block):
    out = dict(floors)
    if not isinstance(block, dict):
        return out
    for k in AHEAD_OF_DUMP:
        if k not in block or str(k).startswith("_"):
            continue
        n = _ada(block.get(k))
        if n is not None:
            out[k] = n
    return out


def floors_of(bc=None):
    """The Balanced block. Accepts lm_cfg(), a raw ahead_of_dump dict, or None."""
    if isinstance(bc, dict) and "pull_min_ada" in bc and "window_h" in bc and "ahead_of_dump" not in bc:
        return _overlay_floors(AHEAD_OF_DUMP, bc)
    block = bc.get("ahead_of_dump") if isinstance(bc, dict) else None
    return _overlay_floors(AHEAD_OF_DUMP, block)


def resolve_floors(liq_doc, cfg=None):
    """Floors for a veto read. The file's thresholds win, then config, then AHEAD_OF_DUMP."""
    floors = _overlay_floors(AHEAD_OF_DUMP, ((cfg or {}).get("liquidity_moves") or {}).get("ahead_of_dump"))
    th = liq_doc.get("thresholds") if isinstance(liq_doc, dict) else None
    block = th.get("ahead_of_dump") if isinstance(th, dict) else None
    return _overlay_floors(floors, block)


def lm_cfg(cfg=None):
    out = dict(DEFAULTS)
    out["ahead_of_dump"] = dict(AHEAD_OF_DUMP)
    block = (cfg or {}).get("liquidity_moves") or {}
    for k, v in block.items():
        if str(k).startswith("_") or k in ("ahead_of_dump", "dump_window_h", "dump_ada", "dump_reserve_pct"):
            continue
        out[k] = v
    out["ahead_of_dump"] = _overlay_floors(AHEAD_OF_DUMP, block.get("ahead_of_dump"))
    return out


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hex(s):
    return "".join(c for c in (s or "") if c in "0123456789abcdefABCDEF").lower()


def minswap_v2_lp_name(unit):
    """Minswap V2 LP asset name. SHA3-256 of the ada hash concatenated with the token hash.

    Ada is the empty policy and empty name, and it sorts first. The hash input is the raw
    policy and name bytes. Same formula as the Minswap V2 SDK computeLPAssetName.
    """
    raw = _hex(unit)
    if len(raw) < 56:
        return None
    try:
        token = bytes.fromhex(raw)
    except ValueError:
        return None
    k1 = hashlib.sha3_256(b"").hexdigest()
    k2 = hashlib.sha3_256(token).hexdigest()
    return hashlib.sha3_256(bytes.fromhex(k1 + k2)).hexdigest()


def payment_cred(addr):
    """28-byte payment credential hex, or None. Script hashes only; the address is not returned."""
    hrp, raw = F.b32_decode(addr or "")
    if not raw or hrp not in ("addr", "addr_test") or len(raw) < 29:
        return None
    return raw[1:29].hex()


def _script(addr):
    return bool(addr) and bool(F.addr_info(addr)[1])


def dex_tables(cfg):
    """(script cred -> dex, extra splash policies). Config may add creds; it cannot drop the built-ins."""
    scripts = dict(SCRIPTS)
    splash = set()
    extra = (lm_cfg(cfg).get("dexes") or {})
    for cred, name in (extra.get("scripts") or {}).items():
        c = _hex(cred)
        if not str(cred).startswith("_") and len(c) == 56 and isinstance(name, str) and name in TRACKED_DEX:
            scripts[c] = name
    for pol in extra.get("splash_policies") or []:
        p = _hex(pol)
        if len(p) == 56:
            splash.add(p)
    return scripts, splash


def _ascii_name(unit):
    name = _hex(unit)[56:]
    try:
        s = bytes.fromhex(name).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return ""
    return s if s.isascii() and s.isprintable() else ""


def _splashish(unit, splash_pols):
    if _hex(unit)[:56] in splash_pols:
        return True
    s = _ascii_name(unit)
    return s.endswith("_NFT") or s.endswith("_LQ")


def _marker_units(assets, token_unit, splash_pols):
    out = []
    for unit, qty in (assets or {}).items():
        if not qty or unit == token_unit:
            continue
        pol = _hex(unit)[:56]
        if pol in LP_DEX or _splashish(unit, splash_pols):
            out.append(unit.lower() if isinstance(unit, str) else unit)
    return out


def _dex_from_markers(markers, splash_pols):
    for unit in sorted(markers):
        pol = _hex(unit)[:56]
        if pol in LP_DEX:
            return LP_DEX[pol]
        if _splashish(unit, splash_pols):
            return "splash"
    return None


def _shared_auth(unit):
    """True for a marker that is the same asset in every pool of that DEX."""
    raw = _hex(unit)
    return raw[:56] == MINSWAP_V2_LP and raw[56:] in SHARED_AUTH_NAMES


def _pick_marker(markers, dex):
    """Per-pool marker. Shared auth tokens (Minswap MSP) are not an identity."""
    ranked = sorted(m for m in (markers or []) if m and not _shared_auth(m))
    if not ranked:
        return None
    prefer = DEX_LP.get(dex)
    if prefer:
        owned = [m for m in ranked if _hex(m)[:56] == prefer]
        if owned:
            return owned[0]
    return ranked[0]


def _sum_assets(rows):
    out = {}
    for u in rows:
        for unit, qty in (u.get("assets") or {}).items():
            out[unit] = out.get(unit, 0) + int(qty)
    return out


def _key_stake(u):
    if _script(u.get("addr") or ""):
        return None
    return u.get("stake") or F.addr_info(u.get("addr") or "")[0]


def lp_mover(tx, dex, marker, splash_pols):
    """(stake, addr) of the key wallet whose LP balance changed the most, or (None, None).

    Swaps do not use this. The stake and address are for the owner check only and are not stored.
    """
    policy = DEX_LP.get(dex) or (_hex(marker)[:56] if marker else "")
    acc, addrs = {}, {}
    for side, sign in (("inputs", -1), ("outputs", 1)):
        for u in tx.get(side) or []:
            st = _key_stake(u)
            if not st:
                continue
            for unit, qty in (u.get("assets") or {}).items():
                if not qty or _shared_auth(unit):
                    continue
                pol = _hex(unit)[:56]
                if policy and pol == policy:
                    pass
                elif dex == "splash" and _splashish(unit, splash_pols):
                    pass
                else:
                    continue
                acc[st] = acc.get(st, 0) + sign * int(qty)
                addrs.setdefault(st, u.get("addr") or "")
    acc = {s: q for s, q in acc.items() if q}
    if not acc:
        return None, None
    st = max(acc, key=lambda s: abs(acc[s]))
    return st, addrs.get(st) or ""


def token_spender(tx, token_unit):
    """(stake, addr) of the key wallet that spent this token, or (None, None).

    A sell tag uses this. Script addresses are not a party. The stake and address are for
    the tag only and are not stored.
    """
    acc, addrs = {}, {}
    for side, sign in (("inputs", -1), ("outputs", 1)):
        for u in tx.get(side) or []:
            st = _key_stake(u)
            if not st:
                continue
            qty = int((u.get("assets") or {}).get(token_unit) or 0)
            if qty:
                acc[st] = acc.get(st, 0) + sign * qty
                addrs.setdefault(st, u.get("addr") or "")
    spent = {s: q for s, q in acc.items() if q < 0}
    if not spent:
        return None, None
    st = min(spent, key=lambda s: spent[s])
    return st, addrs.get(st) or ""


def events_from_tx(tx, token_unit, scripts, splash_pols, min_pool_lovelace):
    """Pool reserve deltas for one transaction. Pure: no network, no tags.

    A pool is a script UTxO that holds the token on both sides of the tx. Minswap is the
    known pool script. Sundae and Splash also need a marker asset (LP policy, or a Splash
    *_NFT / *_LQ name) that stays in that script from input to output, so a batcher order
    that merely holds the token is not a pool.
    """
    groups = {}
    for side in ("inputs", "outputs"):
        for u in tx.get(side) or []:
            addr = u.get("addr") or ""
            if not _script(addr):
                continue
            cred = payment_cred(addr)
            if not cred or token_unit not in (u.get("assets") or {}):
                continue
            groups.setdefault(cred, {"inputs": [], "outputs": []})[side].append(u)
    events = []
    for cred, g in groups.items():
        if not g["inputs"] or not g["outputs"]:
            continue
        known = scripts.get(cred)
        # One event per continuing unique marker, so two Minswap pools in one tx stay apart.
        # A marker only on the input (an order spending an LP) is not a pool.
        buckets, plain = {}, {"inputs": [], "outputs": []}
        for side in ("inputs", "outputs"):
            for u in g[side]:
                mark = _pick_marker(_marker_units(u.get("assets"), token_unit, splash_pols), known)
                if mark:
                    buckets.setdefault(mark, {"inputs": [], "outputs": []})[side].append(u)
                else:
                    plain[side].append(u)
        groups = [(m, b) for m, b in buckets.items() if b["inputs"] and b["outputs"]]
        if not groups and plain["inputs"] and plain["outputs"]:
            groups = [(None, plain)]
        for marker, b in groups:
            if not known and not marker:
                continue
            ins, outs = _sum_assets(b["inputs"]), _sum_assets(b["outputs"])
            both = set(_marker_units(ins, token_unit, splash_pols)) & set(_marker_units(outs, token_unit, splash_pols))
            if marker and marker not in both and not _shared_auth(marker):
                # The chosen marker has to be on the pool input and the pool output.
                if not known:
                    continue
            in_lov = sum(int(u.get("lovelace") or 0) for u in b["inputs"])
            out_lov = sum(int(u.get("lovelace") or 0) for u in b["outputs"])
            if max(in_lov, out_lov) < int(min_pool_lovelace):
                continue
            dex = known or _dex_from_markers(both or ([marker] if marker else []), splash_pols)
            if dex not in TRACKED_DEX:
                continue
            if not marker:
                marker = _pick_marker(both, dex)
            st, lp_addr = lp_mover(tx, dex, marker, splash_pols)
            events.append({
                "dex": dex, "marker": marker, "unit": token_unit,
                "ada_delta": out_lov - in_lov,
                "token_delta": int(outs.get(token_unit, 0)) - int(ins.get(token_unit, 0)),
                "reserve_lovelace": out_lov,
                "reserve_qty": int(outs.get(token_unit, 0)),
                "lp_stake": st, "lp_addr": lp_addr,
            })
    return events


def kind_of(ada_delta, token_delta, bc):
    """(kind, direction) or (None, None). ada_delta is lovelace into the pool."""
    ada = abs(int(ada_delta)) / 1e6
    if ada_delta > 0 and token_delta < 0 and ada >= bc["min_swap_ada"]:
        return "swap", "buy"
    if ada_delta < 0 and token_delta > 0 and ada >= bc["min_swap_ada"]:
        return "swap", "sell"
    if ada_delta > 0 and token_delta > 0 and ada >= bc["min_lp_ada"]:
        return "lp_add", None
    if ada_delta < 0 and token_delta < 0 and ada >= bc["min_lp_ada"]:
        return "lp_remove", None
    return None, None


def pool_key(dex, marker, token_unit):
    """Short public id: dex plus 8 hex of sha256(full marker unit).

    The asset-name prefix is not unique. Sundae V3 CIP-68 names share 000de140, and
    Minswap's MSP auth token is the same asset in every V2 pool. A shared auth token
    is not used as the marker; the token unit is the fallback.
    """
    raw = _hex(marker) if marker and not _shared_auth(marker) else ""
    if not raw:
        raw = _hex(token_unit) or "pool"
    return f"{dex}~{hashlib.sha256(raw.encode()).hexdigest()[:8]}"


def _reserve_after(row):
    """ADA left in the pool after the pull. Public rows say reserve_ada_after; events say reserve_ada."""
    if not isinstance(row, dict):
        return None
    if row.get("reserve_ada_after") is not None:
        return row.get("reserve_ada_after")
    return row.get("reserve_ada")


def pull_is_material(row, floors=None):
    """True when the pull is at least pull_reserve_pct of the pre-pull reserve and pull_min_ada.

    reserve_ada / reserve_ada_after is the ADA left after the pull. The pool reserve the
    percentage uses is that remainder plus the ADA removed.
    """
    floors = floors_of(floors)
    if not isinstance(row, dict):
        return False
    ada, after = _ada(row.get("ada")), _ada(_reserve_after(row))
    if ada is None or after is None or ada <= 0 or after < 0:
        return False
    before = after + ada
    if before <= 0:
        return False
    return ada >= float(floors["pull_min_ada"]) and 100.0 * ada / before >= float(floors["pull_reserve_pct"])


def sells_cover_pull(dump_ada, pull_ada, floors=None):
    """True when dump_ada is at least sell_of_pull_pct of the pulled ADA."""
    floors = floors_of(floors)
    dumped, ada = _ada(dump_ada), _ada(pull_ada)
    if dumped is None or ada is None or ada <= 0 or dumped < 0:
        return False
    return 100.0 * dumped / ada >= float(floors["sell_of_pull_pct"])


def tight_ahead(move, floors=None):
    """True when a public lp_remove carries a Balanced ahead_of_dump, not a loose flag.

    The party match and the window are applied before the flag is set. The amounts on the
    row still have to meet the pull floors and the sell-of-pull floor.
    """
    if not isinstance(move, dict) or move.get("ahead_of_dump") is not True:
        return False
    return pull_is_material(move, floors) and sells_cover_pull(move.get("dump_ada"), move.get("ada"), floors)


def _party_tag(tag):
    """A w- hash or a known dev/role label, else None."""
    if not isinstance(tag, str) or not tag or not DC.dev_tag_ok(tag):
        return None
    return tag


def _same_party(pull, sell):
    """Same pulling party: one w- hash, or one dev/role tag, on both rows."""
    a, b = _party_tag(pull.get("actor_tag")), _party_tag(sell.get("actor_tag"))
    return bool(a) and a == b


def mark_dumps(events, open_pulls, bc, now_s):
    """Flag a material LP pull followed by same-party sells into the same pool.

    A pull counts only when pull_is_material is true. ahead_of_dump is set only when sells
    from that same w- hash or dev/role tag, inside window_h, total at least sell_of_pull_pct
    of the pulled ADA. events and open_pulls use time (unix), pool_key, kind, direction,
    ada (ADA), reserve_ada, actor_tag, tx_short. Returns (pulls still waiting, pulls that
    were flagged). Does not copy stakes.
    """
    floors = floors_of(bc)
    window = float(floors["window_h"]) * 3600
    pulls = []
    for p in open_pulls or []:
        cleaned = _pull_state(p)
        if not cleaned or not pull_is_material(cleaned, floors):
            continue
        if now_s - cleaned["time"] <= window:
            pulls.append(cleaned)
    for ev in sorted(events or [], key=lambda e: (e.get("time") or 0, e.get("tx_short") or "")):
        if ev.get("kind") == "lp_remove":
            if pull_is_material(ev, floors):
                ev["sold_ada"] = float(ev.get("sold_ada") or 0)
                pulls.append(ev)
            continue
        if ev.get("kind") != "swap" or ev.get("direction") != "sell":
            continue
        for p in pulls:
            if p.get("ahead_of_dump") or not _same_pool(p, ev) or not _same_party(p, ev):
                continue
            if not (int(p.get("time") or 0) < int(ev.get("time") or 0) <= int(p["time"]) + window):
                continue
            total = float(p.get("sold_ada") or 0) + abs(float(ev.get("ada") or 0))
            p["sold_ada"] = total
            if sells_cover_pull(total, p.get("ada"), floors):
                _stamp_dump(p, ev, events, total)
    flagged = [p for p in pulls if p.get("ahead_of_dump")]
    waiting = [p for p in pulls if not p.get("ahead_of_dump") and now_s - int(p["time"]) <= window]
    return waiting, flagged


def _stamp_dump(pull, sell, events, total):
    short = sell.get("tx_short")
    dumped = round(float(total), 2)
    pull["ahead_of_dump"] = True
    pull["dump_tx_short"] = short
    pull["dump_ada"] = dumped
    ev_hash = pull.get("hash")
    for src in events or []:
        if src is pull or (ev_hash and src.get("hash") == ev_hash and src.get("kind") == "lp_remove"):
            src["ahead_of_dump"] = True
            src["dump_tx_short"] = short
            src["dump_ada"] = dumped


def _same_pool(a, b):
    """Pull and sell match only for the same token unit and the same pool_key."""
    if a.get("pool_key") != b.get("pool_key"):
        return False
    ua = a.get("unit") or a.get("_unit")
    ub = b.get("unit") or b.get("_unit")
    return bool(ua) and ua == ub


def _safe_tag(stake, addr, watch, loaded, ex_tags):
    """w- hash or role label. Hidden when owner patterns miss, or when they match the stake or the address.

    Address patterns contribute their stake (DC._patterns / DC._hidden), so an owner listed only as
    an address does not get a public tag on a sibling of that wallet.
    """
    if not loaded or not (stake or addr):
        return None
    pats, stakes = loaded
    if DC._hidden(pats, stakes, stake, addr):
        return None
    if not stake:
        return None
    tag = DC.short_tag(stake, watch)
    if not DC.dev_tag_ok(tag):
        return None
    if tag in (ex_tags or {}):
        return None
    return tag


def public_move(ev, token, ticker, policy_short, decimals, lp):
    raw = abs(int(ev["token_delta"]))
    if decimals is not None:
        qty, basis = round(raw / 10 ** int(decimals), 6), "whole"
    else:
        qty, basis = raw, "raw"
    row = {
        "time": iso(ev["time"]), "tx_short": ev["hash"][:10], "kind": ev["kind"],
        "ticker": "LP" if lp else ticker, "token": token, "policy_short": policy_short,
        "dex": ev["dex"], "pool_key": ev["pool_key"], "direction": ev.get("direction"),
        "ada": round(abs(int(ev["ada_delta"])) / 1e6, 2), "ada_basis": "ada_leg",
        "qty": qty, "qty_basis": basis,
        "reserve_ada_after": round(int(ev["reserve_lovelace"]) / 1e6, 2),
        "lp": bool(lp), "actor_tag": ev.get("actor_tag"),
        "ahead_of_dump": bool(ev.get("ahead_of_dump")),
        "dump_tx_short": ev.get("dump_tx_short"), "dump_ada": ev.get("dump_ada"), "level": "pool",
    }
    return row


def known_units():
    """ticker -> full unit from known_units.json. Empty when that file is missing or bad.

    The file is a static map: Cardano token-registry subjects, Koios asset_info for the
    one name the registry does not list (VAMP), and the desk pulse asset ids for the
    Solada launches. It is not fetched at run time.
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_units.json")
    doc = KT.read_json(path, {})
    block = doc.get("units") if isinstance(doc, dict) else None
    out = {}
    if not isinstance(block, dict):
        return out
    for tk, row in block.items():
        if not isinstance(tk, str) or tk.startswith("_"):
            continue
        unit = row.get("unit") if isinstance(row, dict) else row
        h = _hex(unit) if isinstance(unit, str) else ""
        if len(h) >= 56:
            out[tk.upper()] = h
    return out


def resolve_tokens(top10, units_by_ticker, cached, max_tokens):
    """[{ticker, unit, rank, policy_short, unit_source}] from Snek's top 10.

    A candidate is the row's unit or asset_id, then units_by_ticker (pulse, config, tracked),
    then the private cache, then known_units(). The first full unit that starts with
    policy_short wins. A candidate that fails the prefix is not used, so a different token
    of the same ticker cannot block the verified unit. When every candidate fails the
    prefix the row is policy_mismatch. When none is long enough it is unresolved.
    Full units are not invented past that map.
    """
    if not isinstance(top10, list):
        return [], []
    ordered = sorted(
        (r for r in top10 if isinstance(r, dict) and r.get("ticker")),
        key=lambda r: r.get("rank") if isinstance(r.get("rank"), (int, float)) and not isinstance(r.get("rank"), bool) else 99)
    rev = {}
    for tk, unit in (units_by_ticker or {}).items():
        if isinstance(unit, str) and len(_hex(unit)) >= 56:
            rev[str(tk).upper()] = _hex(unit)
    cached = cached if isinstance(cached, dict) else {}
    static = known_units()
    rows, skipped, seen = [], [], set()
    for r in ordered:
        if len(rows) >= int(max_tokens):
            break
        tk = str(r.get("ticker") or "").upper()
        short = _hex(r.get("policy_short") if isinstance(r.get("policy_short"), str) else "")
        unit, via, saw = None, None, False
        for kind, src in (
            ("row", r.get("unit")), ("row", r.get("asset_id")),
            ("listed", rev.get(tk)), ("cache", cached.get(tk)), ("static", static.get(tk)),
        ):
            h = _hex(src) if isinstance(src, str) else ""
            if len(h) < 56:
                continue
            saw = True
            if short and not h.startswith(short):
                continue
            unit, via = h, kind
            break
        if not unit:
            skipped.append({"ticker": tk, "skipped": "policy_mismatch" if saw else "unresolved"})
            continue
        if unit in seen:
            continue
        seen.add(unit)
        rows.append({
            "ticker": tk, "unit": unit, "rank": r.get("rank"),
            "policy_short": WL.policy_short(unit, 8), "unit_source": via,
        })
    return rows, skipped


def _rank_key(row):
    rank = row.get("rank") if isinstance(row, dict) else None
    if isinstance(rank, bool) or not isinstance(rank, (int, float)):
        return 99
    return rank


def order_least_recent(rows, last_checked):
    """Least-recently-checked first.

    A missing or non-positive stamp sorts as never checked, ahead of any real time.
    Rank, then ticker, breaks ties, so a fresh cache still follows Snek rank.
    """
    stamps = last_checked if isinstance(last_checked, dict) else {}

    def key(row):
        ts = _safe_int(stamps.get(row.get("ticker")))
        return (ts if ts > 0 else 0, _rank_key(row), str(row.get("ticker") or ""))

    return sorted(rows or [], key=key)


def _unix(v):
    """Unix seconds. time.time() is fractional; int() truncates it. A bool is not a time."""
    if isinstance(v, bool) or v is None:
        return 0
    if isinstance(v, (int, float)):
        n = int(v)
        return n if n > 0 else 0
    return _safe_int(v)


def stamp_checked(cache, checked, tickers, now_s):
    """Record last_checked for tokens that received a successful read.

    `checked` is tickers whose Koios or Blockfrost page came back this run. A budget
    skip, or a token the slice opened and then did not read, keeps its previous stamp.
    Keys that are not in this run's top 10 are dropped. The map is ticker to unix time.
    """
    prev = cache.get("last_checked") if isinstance(cache, dict) and isinstance(cache.get("last_checked"), dict) else {}
    stamps = {}
    for k, v in prev.items():
        n = _unix(v)
        if isinstance(k, str) and k and not k.startswith("_") and n > 0:
            stamps[k.upper()] = n
    now = _unix(now_s)
    if now > 0:
        for tk in checked or []:
            if isinstance(tk, str) and tk and not tk.startswith("_"):
                stamps[tk.upper()] = now
    keep = {str(t).upper() for t in (tickers or []) if isinstance(t, str) and t and not str(t).startswith("_")}
    stamps = {k: v for k, v in stamps.items() if k in keep}
    if isinstance(cache, dict):
        cache["last_checked"] = stamps
    return stamps


def _token_public(row, stamps):
    ts = _safe_int((stamps or {}).get(row.get("ticker")))
    return {
        "ticker": row["ticker"], "policy_short": row["policy_short"], "rank": row.get("rank"),
        "unit_source": row.get("unit_source"), "last_checked": iso(ts) if ts > 0 else None,
    }


class Reader:
    def __init__(self, http, bc, bf_key, deadline):
        self.h, self.bc = http, bc
        self.bf_key = (bf_key or "").strip()
        self.deadline = deadline
        self.start = http.calls.get("koios", 0)
        self.bf_start = http.calls.get("blockfrost", 0)
        self.fallbacks = 0

    def late(self):
        return bool(self.deadline) and time.time() >= self.deadline

    def koios_left(self):
        return int(self.bc["max_calls"]) - (self.h.calls.get("koios", 0) - self.start)

    def bf_left(self):
        return int(self.bc["max_bf_calls"]) - (self.h.calls.get("blockfrost", 0) - self.bf_start)

    def _bf(self, path):
        if not self.bf_key or self.bf_left() <= 0 or self.late():
            return None
        self.fallbacks += 1
        return self.h.req("blockfrost", BF + path, headers={"project_id": self.bf_key}, quiet404=False)

    def tip(self):
        if self.h is None or self.late() or self.koios_left() <= 0:
            return None
        r = self.h.req("koios", KOIOS + "/tip", quiet404=False)
        # A bare list, or a list whose first row is not an object, is not a tip.
        # r[0].get on that shape used to raise AttributeError and skip the whole job.
        if isinstance(r, list) and r and isinstance(r[0], dict):
            return r[0].get("block_height"), r[0].get("block_time")
        b = self._bf("/blocks/latest")
        if not isinstance(b, dict):
            return None
        return b.get("height"), b.get("time")

    def asset_txs(self, unit, after=None, limit=20, newest=False):
        """(rows, complete) or None when the read was not answered.

        Blockfrost has no after-height filter. Asking it for ascending order returns the
        asset's first transactions, which then look like a finished short page. The fallback
        always reads newest-first and stops once a row is older than the cursor. A full page
        that never reaches the cursor is incomplete.
        """
        if self.late() or not unit or len(unit) < 56 or self.koios_left() <= 0:
            return None
        order = "desc" if newest else "asc"
        path = (f"/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}&_history=true"
                f"&order=block_height.{order}&limit={int(limit)}")
        if after is not None and not newest:
            path += f"&_after_block_height={int(after)}"
        r = self.h.req("koios", KOIOS + path, quiet404=False)
        if r is None:
            return self._bf_asset_txs(unit, after, limit, newest)
        out = []
        for t in r:
            if not t.get("tx_hash"):
                continue
            hgt = t.get("block_height")
            # Keep the cursor height. Already-applied txs there are dropped by the seen set.
            if after is not None and not newest and hgt is not None and int(hgt) < int(after):
                continue
            out.append((t["tx_hash"], None if hgt is None else int(hgt), t.get("block_time")))
        return out, len(out) < int(limit), bool(newest)

    def _bf_asset_txs(self, unit, after, limit, newest):
        count = min(100, max(int(limit), 1))
        b = self._bf(f"/assets/{unit}/transactions?order=desc&count={count}")
        if not isinstance(b, list):
            return None
        rows, reached = [], False
        for t in b:
            hgt = t.get("block_height")
            if not t.get("tx_hash") or hgt is None:
                continue
            if after is not None and not newest and int(hgt) < int(after):
                reached = True
                break
            rows.append((t["tx_hash"], int(hgt), t.get("block_time")))
            if len(rows) >= int(limit):
                break
        complete = reached or len(b) < count
        if newest:
            complete = len(b) < count
        return rows, complete, True

    def utxos(self, hashes):
        """(found, absent). absent is hashes a provider list omitted.

        A budget or deadline cut before the request is neither found nor absent, so it is
        not a miss. A Koios failure falls back per tx; a Blockfrost error is not a miss either.
        """
        found, absent = {}, set()
        pending = [h for h in hashes if isinstance(h, str)]
        for body in KT.batches(pending, "_tx_hashes", self.bc["utxo_batch"], KT.TX_INFO_FLAGS):
            if self.late() or self.koios_left() <= 0:
                break
            chunk = body["_tx_hashes"]
            r = self.h.req("koios", KOIOS + "/tx_info", method="POST", body=body, quiet404=False)
            if isinstance(r, list):
                got = {}
                for tx in r:
                    if tx.get("tx_hash"):
                        got[tx["tx_hash"]] = {"inputs": [KT.norm_utxo(u) for u in tx.get("inputs") or []],
                                              "outputs": [KT.norm_utxo(u) for u in tx.get("outputs") or []]}
                for h in chunk:
                    if h in got:
                        found[h] = got[h]
                    else:
                        absent.add(h)
                continue
            for h in chunk:
                if self.late() or self.bf_left() <= 0:
                    break
                x = self._bf(f"/txs/{h}/utxos")
                if isinstance(x, dict):
                    found[h] = _bf_utxo(x)
        return found, absent


def _bf_utxo(x):
    """Blockfrost /txs/{hash}/utxos. Collateral and reference inputs are not spends."""
    def side(rows, which):
        out = []
        for u in rows or []:
            if u.get("collateral") or u.get("reference"):
                continue
            lov, assets = 0, {}
            for a in u.get("amount") or []:
                if a.get("unit") == "lovelace":
                    lov += int(a.get("quantity") or 0)
                elif a.get("unit"):
                    assets[a["unit"]] = assets.get(a["unit"], 0) + int(a.get("quantity") or 0)
            addr = u.get("address") or ""
            out.append({"addr": addr, "stake": F.addr_info(addr)[0], "lovelace": lov, "assets": assets})
        return out
    return {"inputs": side(x.get("inputs"), "inputs"), "outputs": side(x.get("outputs"), "outputs")}


def _markers_for(unit, cached_markers):
    out = []
    name = minswap_v2_lp_name(unit)
    if name:
        out.append({"dex": "minswap_v2", "marker": MINSWAP_V2_LP + name, "derived": True})
    for m in cached_markers or []:
        if isinstance(m, dict) and isinstance(m.get("marker"), str) and len(_hex(m["marker"])) >= 56:
            if m.get("dex") in TRACKED_DEX and m.get("dex") != "minswap_v2":
                out.append({"dex": m["dex"], "marker": _hex(m["marker"]), "derived": False})
    return out


def _apply_tx(tx, token_row, scripts, splash_pols, bc, lp_pols, watch, loaded, ex_tags, names):
    """Public-ready event dicts for one tx, or [] when the tx is not a sized pool move."""
    events = events_from_tx(tx, token_row["unit"], scripts, splash_pols, int(bc["min_pool_ada"] * 1e6))
    out = []
    lp = bool(V.lp_dex(token_row["unit"], lp_pols))
    token = WL.token_key(token_row["unit"], names)
    for ev in events:
        kind, direction = kind_of(ev["ada_delta"], ev["token_delta"], bc)
        ev["hash"] = tx["hash"]
        ev["tx_short"] = tx["hash"][:10]
        ev["time"] = tx["time"]
        ev["unit"] = token_row["unit"]
        ev["pool_key"] = pool_key(ev["dex"], ev["marker"], token_row["unit"])
        ev["ticker"] = token_row["ticker"]
        ev["token"] = token
        ev["policy_short"] = token_row["policy_short"]
        ev["kind"] = kind
        ev["direction"] = direction
        ev["ada"] = round(abs(int(ev["ada_delta"])) / 1e6, 2)
        ev["reserve_ada"] = round(int(ev["reserve_lovelace"]) / 1e6, 2)
        ev["actor_tag"] = None
        kept_stake = ev.get("lp_stake") if kind == "lp_remove" and not lp else None
        if kind in ("lp_add", "lp_remove"):
            ev["actor_tag"] = _safe_tag(ev.get("lp_stake"), ev.get("lp_addr"), watch, loaded, ex_tags)
        elif kind == "swap" and direction == "sell":
            st, who = token_spender(tx, token_row["unit"])
            ev["actor_tag"] = _safe_tag(st, who, watch, loaded, ex_tags)
        ev.pop("lp_stake", None)
        ev.pop("lp_addr", None)
        # Private hop input. public_move does not copy it. Popped before the public file is built.
        if kept_stake and ev.get("actor_tag"):
            ev["_lp_stake"] = kept_stake
        out.append(ev)
    return out


def _row_time(row):
    try:
        return datetime.strptime(row["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def merge_moves(prev, new_rows, now_s, keep_h):
    keep = {}
    for r in list(prev or []) + list(new_rows or []):
        if not isinstance(r, dict):
            continue
        t = _row_time(r)
        if t is None or now_s - t > keep_h * 3600:
            continue
        key = (r.get("tx_short"), r.get("pool_key"), r.get("kind"), r.get("token"))
        old = keep.get(key)
        if old and old.get("ahead_of_dump") and not r.get("ahead_of_dump"):
            r = dict(r, ahead_of_dump=True, dump_tx_short=old.get("dump_tx_short"), dump_ada=old.get("dump_ada"))
        elif old and r.get("ahead_of_dump"):
            r = dict(r)
        keep[key] = r
    return sorted(keep.values(), key=lambda r: (r.get("time") or "", r.get("tx_short") or ""))


def merge_pools(prev, fresh):
    by = {}
    for p in prev or []:
        if isinstance(p, dict) and p.get("pool_key"):
            by[(p.get("pool_key"), p.get("token"))] = dict(p, stale=True)
    for p in fresh or []:
        by[(p.get("pool_key"), p.get("token"))] = dict(p)
        by[(p.get("pool_key"), p.get("token"))].pop("stale", None)
    return sorted(by.values(), key=lambda p: (p.get("ticker") or "", p.get("dex") or "", p.get("pool_key") or ""))


def _basis(koios_calls, bf_calls):
    if bf_calls and koios_calls:
        return "mixed"
    if bf_calls:
        return "blockfrost"
    return "koios"


def _rb():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    import rugger_blacklist as RB
    return RB


def _pull_key(ev):
    if not isinstance(ev, dict):
        return None
    unit = ev.get("unit") or ev.get("_unit")
    short = ev.get("tx_short") or ((ev.get("hash") or "")[:10] if isinstance(ev.get("hash"), str) else "")
    pool = ev.get("pool_key")
    if not unit or not short or not pool:
        return None
    return (str(short), str(pool), str(unit))


def _load_pull_stakes(cache_dir, public_dir):
    """Private pull rows. Empty when the file is missing or inside the public directory."""
    RB = _rb()
    if not cache_dir:
        return []
    path = os.path.join(cache_dir, RB.PULLS_NAME)
    if RB._inside(path, public_dir) or not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return []
    rows = doc.get("pulls") if isinstance(doc, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _save_pull_stakes(cache_dir, public_dir, rows):
    RB = _rb()
    path = os.path.join(cache_dir, RB.PULLS_NAME)
    if RB._inside(path, public_dir):
        return False
    os.makedirs(cache_dir, exist_ok=True)
    kept = []
    seen = set()
    for row in rows or []:
        key = _pull_key(row)
        stake = row.get("stake")
        if not key or not RB.public_id(stake) or key in seen:
            continue
        seen.add(key)
        when = row.get("time")
        if isinstance(when, bool) or not isinstance(when, (int, float)) or when <= 0:
            continue
        kept.append({
            "tx_short": key[0], "pool_key": key[1], "unit": key[2], "stake": stake,
            "time": float(when), "ada": row.get("ada"), "reserve_ada": row.get("reserve_ada"),
        })
    with open(path, "w") as fh:
        json.dump({"pulls": kept[-2000:]}, fh)
        fh.write("\n")
    return True


def _feed_rugger(cache_dir, public_dir, events, flagged, now_s, cfg, dry_run, prior=None):
    """Record material deployer pulls and ahead_of_dump actors. Stakes stay in rugger-pulls.json.

    liquidity-moves-cache.json stays stake-free. A pull flagged on a later run is joined
    back to the stake saved when the pull was first seen. lp_pulled requires a material
    pull by that token's deployer. A role label alone is not evidence.
    """
    if dry_run or not cache_dir:
        return
    try:
        RB = _rb()
        stored = _load_pull_stakes(cache_dir, public_dir)
        fresh = []
        for ev in events or []:
            if not isinstance(ev, dict) or ev.get("kind") != "lp_remove" or not ev.get("_lp_stake"):
                continue
            fresh.append({
                "tx_short": ev.get("tx_short"), "hash": ev.get("hash"), "pool_key": ev.get("pool_key"),
                "unit": ev.get("unit") or ev.get("_unit"), "stake": ev.get("_lp_stake"),
                "time": ev.get("time"), "ada": ev.get("ada"), "reserve_ada": ev.get("reserve_ada"),
            })
        if not RB._inside(os.path.join(cache_dir, RB.PULLS_NAME), public_dir):
            _save_pull_stakes(cache_dir, public_dir, stored + fresh)
            stored = _load_pull_stakes(cache_dir, public_dir)
        by_key = {}
        for row in stored:
            key = _pull_key(row)
            if key:
                by_key[key] = row
        book = RB.open_book(cache_dir, public_dir, cfg, prior=prior)
        if book is None:
            return
        RB.mark_hold(cache_dir, book)
        if not book.writable:
            return
        devs = RB.deployers_from_dev_cache(cache_dir, public_dir)
        owners = RB.owner_ids(os.environ.get("PRIVACY_GUARD_PATTERNS", ""), derive=lambda a: F.addr_info(a)[0])
        rows = [ev for ev in (events or []) if isinstance(ev, dict)]
        seen_ids = {id(ev) for ev in rows}
        for ev in flagged or []:
            if isinstance(ev, dict) and id(ev) not in seen_ids:
                rows.append(ev)
        for ev in rows:
            kind = ev.get("kind")
            if kind not in (None, "lp_remove"):
                continue
            if kind is None and ev.get("ahead_of_dump") is not True:
                continue
            unit = ev.get("unit") or ev.get("_unit")
            stake = ev.get("_lp_stake")
            saved = by_key.get(_pull_key(ev))
            if not stake and saved:
                stake = saved.get("stake")
            if not stake or not unit or RB.public_id(stake) in owners:
                continue
            when = ev.get("time")
            if (isinstance(when, bool) or not isinstance(when, (int, float)) or when <= 0) and saved:
                when = saved.get("time")
            if isinstance(when, bool) or not isinstance(when, (int, float)) or when <= 0:
                continue
            at = RB.iso(when)
            if ev.get("ahead_of_dump") is True:
                book.note_evidence(stake, "cardano", "ahead_of_dump", unit, at)
            material = pull_is_material(ev) or (saved and pull_is_material(saved))
            if material and devs.get(unit) == stake:
                book.note_evidence(stake, "cardano", "lp_pulled", unit, at)
        book.save()
    except Exception as e:
        print(f"liquidity_moves: rugger blacklist skipped ({type(e).__name__})")


def run(http, cfg, top10, units_by_ticker, meta, lp_pols, data_dir, cache_dir, dry_run, now_s,
        bf_key=None, deadline=None, owner_env=None, watch=None):
    """Write liquidity_moves.json unless dry_run. Returns a small summary. Never raises.

    top10 is Snek's cardano_top10. units_by_ticker maps a ticker to a policy+name from pulse,
    config, or an open ticket. meta is {unit: {decimals}}. Full hashes and asset ids used as
    cursors stay in the private cache.
    """
    bc = lm_cfg(cfg)
    path = os.path.join(data_dir, "liquidity_moves.json")
    if not bc.get("enabled", True):
        return {"enabled": False}
    # Reader.__init__ reads http.calls. A missing client (the offline crash probe) used to
    # raise AttributeError here, which this swallow logged as "liquidity_moves: skipped (AttributeError)".
    if http is None:
        print("liquidity_moves: skipped (no http)")
        return {"error": "no_http", "wrote": False}
    try:
        return _run(http, cfg, bc, top10, units_by_ticker, meta, lp_pols, path, cache_dir, dry_run, now_s,
                    bf_key, deadline, owner_env, watch)
    except (SystemExit, Exception) as e:
        print(f"liquidity_moves: skipped ({type(e).__name__})")
        return {"error": type(e).__name__, "wrote": False}


def _run(http, cfg, bc, top10, units_by_ticker, meta, lp_pols, path, cache_dir, dry_run, now_s,
         bf_key, deadline, owner_env, watch):
    if bf_key is None:
        bf_key = os.environ.get("BLOCKFROST_PROJECT_ID", "")
    if owner_env is None:
        owner_env = os.environ.get("PRIVACY_GUARD_PATTERNS", "")
    loaded = DC._patterns(owner_env, who="liquidity_moves")
    ex_tags = set((F.excluded_tags((cfg or {}).get("flags")) or {}))
    scripts, splash_pols = dex_tables(cfg)
    cpath = os.path.join(cache_dir, "liquidity-moves-cache.json") if cache_dir else None
    same_dir = bool(cache_dir) and os.path.abspath(cache_dir) == os.path.abspath(os.path.dirname(path) or ".")
    raw_cache = KT.read_json(cpath, {}) if cpath and not same_dir else {}
    cache = _cache_public(raw_cache)
    kept_next = cache.get("next") or 0
    prev_doc = KT.read_json(path, {})
    if not isinstance(prev_doc, dict):
        prev_doc = {}
    rows, skipped = resolve_tokens(top10, units_by_ticker, cache.get("units"), bc["max_tokens"])
    listed = list(rows)
    # Least-recently-checked first. The legacy `next` index advanced by one, so a run that
    # finished two tokens re-read the second one next time and the tail stayed on
    # skipped: budget. `next` is still stored so a tip or deadline skip keeps it.
    rows = order_least_recent(rows, cache.get("last_checked"))
    keep_next = False
    reader = Reader(http, bc, bf_key, deadline)
    names = WL.assign_token_keys([row["unit"] for row in rows])
    seen_list = [h for h in cache.get("seen") or [] if isinstance(h, str)]
    seen = set(seen_list)
    events, pools, coverage, capped, checked = [], [], {}, [], []
    tip = reader.tip() if rows else None
    tip_h = tip[0] if tip else None
    if rows and (reader.late() or tip_h is None):
        why = "budget" if reader.late() else "tip"
        for row in rows:
            coverage[row["ticker"]] = {"read": 0, "complete": False, "skipped": why}
        keep_next = True
        rows = []
    window_after = (int(tip_h) - int(bc["keep_h"]) * BLOCKS_PER_HOUR) if tip_h else 0
    for row in rows:
        tk, unit = row["ticker"], row["unit"]
        if reader.late() or reader.koios_left() <= 0:
            coverage[tk] = {"read": 0, "complete": False, "skipped": "budget"}
            capped.append(tk)
            continue
        try:
            evs, cov, new_markers, served = _scan_token(
                reader, row, cache, scripts, splash_pols, bc, lp_pols or V.lp_policies(cfg), watch, loaded, ex_tags,
                names, seen, seen_list, window_after, now_s)
        except (SystemExit, Exception) as e:
            coverage[tk] = {"read": 0, "complete": False, "skipped": type(e).__name__}
            continue
        # Run 40742102339 stamped STRIKE after the deadline passed with 0 txs read.
        # A page that came back counts, including an empty one. No page does not.
        if served:
            checked.append(tk)
        events.extend(evs)
        coverage[tk] = cov
        if not cov.get("complete", True):
            capped.append(tk)
        if new_markers:
            cache["markers"][unit] = new_markers
        cache["units"][tk] = unit
        for ev in sorted(evs, key=lambda e: e.get("time") or 0):
            pools.append({
                "ticker": "LP" if V.lp_dex(unit, lp_pols or {}) else tk,
                "token": ev["token"], "policy_short": row["policy_short"], "dex": ev["dex"],
                "pool_key": ev["pool_key"], "reserve_ada": ev["reserve_ada"],
                "reserve_qty": ev["reserve_qty"], "asof": iso(ev["time"]) if ev.get("time") else iso(now_s),
            })
    sized = [e for e in events if e.get("kind")]
    waiting, flagged_pulls = mark_dumps(sized, cache.get("open_pulls"), bc, now_s)
    try:
        prior = _rb().read_prior(os.path.join(os.path.dirname(path), "last-run.json"))
        _feed_rugger(cache_dir, os.path.dirname(path), sized, flagged_pulls, now_s, cfg, dry_run, prior)
    finally:
        for ev in sized:
            ev.pop("_lp_stake", None)
    flagged = {}
    for p in flagged_pulls:
        short = p.get("tx_short") or (p.get("hash") or "")[:10]
        flagged[(short, p.get("pool_key"), p.get("token"))] = p
    moves = []
    prev_keys = {(r.get("tx_short"), r.get("pool_key"), r.get("kind"), r.get("token"))
                 for r in (prev_doc.get("moves") or []) if isinstance(r, dict)}
    for e in sized:
        if not e.get("time"):
            continue
        dec = _decimals(meta, e.get("_unit"))
        moves.append(public_move(e, e["token"], e["ticker"], e["policy_short"], dec, V.lp_dex(e.get("_unit"), lp_pols or {})))
    prev_moves = [dict(r) for r in prev_doc.get("moves") if isinstance(r, dict)] if isinstance(prev_doc.get("moves"), list) else []
    for r in prev_moves:
        hit = flagged.get((r.get("tx_short"), r.get("pool_key"), r.get("token")))
        if hit and r.get("kind") == "lp_remove":
            r["ahead_of_dump"] = True
            r["dump_tx_short"] = hit.get("dump_tx_short")
            r["dump_ada"] = hit.get("dump_ada")
    merged = merge_moves(prev_moves, moves, now_s, bc["keep_h"])
    cache["open_pulls"] = [_pull_state(p) for p in waiting]
    cache["seen"] = seen_list[-int(bc["keep_seen"]):]
    if keep_next:
        # Tip or deadline skip: nobody was read, so last_checked and the legacy index stay.
        cache["capped"] = True
        cache["next"] = kept_next
    elif capped:
        cache["capped"] = True
        cache["next"] = kept_next + 1
    else:
        cache["capped"] = False
        cache["next"] = 0
    stamps = stamp_checked(cache, checked, [r["ticker"] for r in listed], now_s)
    koios_calls = http.calls.get("koios", 0) - reader.start
    bf_calls = http.calls.get("blockfrost", 0) - reader.bf_start
    doc = {
        "at": iso(now_s), "phase": 1, "source": "snek_cardano_top10",
        "note": NOTE, "limits": LIMITS, "level": "pool",
        "thresholds": {
            "min_swap_ada": bc["min_swap_ada"], "min_lp_ada": bc["min_lp_ada"],
            "ahead_of_dump": {k: floors_of(bc)[k] for k in AHEAD_OF_DUMP},
        },
        "budget": {"max_calls": bc["max_calls"], "max_bf_calls": bc["max_bf_calls"], "budget_s": bc["budget_s"]},
        "actor_tags": bool(loaded),
        "moves": merged[-int(bc["keep_rows"]):],
        "pools": merge_pools(prev_doc.get("pools"), [_pool_public(p) for p in pools]),
        "tokens": [_token_public(r, stamps) for r in listed],
        "skipped": skipped, "coverage": coverage, "capped": capped,
        "koios_calls": koios_calls, "blockfrost_calls": bf_calls,
        "blockfrost_fallbacks": reader.fallbacks, "blockfrost_available": bool(reader.bf_key),
        "basis": _basis(koios_calls, bf_calls),
    }
    try:
        F.assert_public(doc)
    except SystemExit:
        print("liquidity_moves: not written (public check)")
        return {"wrote": False, "error": "privacy"}
    if not dry_run:
        if cpath and not same_dir:
            KT.write_json(cpath, _cache_public(cache), indent=1)
        KT.write_json(path, doc, indent=1)
    new = sum(1 for r in moves if (r["tx_short"], r["pool_key"], r["kind"], r["token"]) not in prev_keys)
    return {
        "tokens": len(listed), "moves": len(doc["moves"]), "moves_new": new, "capped": capped,
        "koios_calls": koios_calls, "blockfrost_calls": bf_calls, "blockfrost_fallbacks": reader.fallbacks,
        "basis": doc["basis"], "ahead_of_dump": sum(1 for r in doc["moves"] if r.get("ahead_of_dump")),
        "wrote": not dry_run,
    }


def _decimals(meta, unit):
    if not isinstance(meta, dict) or not isinstance(unit, str):
        return None
    row = meta.get(unit) or meta.get(unit.lower()) or {}
    if not isinstance(row, dict):
        return None
    dec = row.get("decimals")
    if isinstance(dec, bool) or not isinstance(dec, (int, float)):
        return None
    return int(dec)


def _safe_int(v, default=0):
    if isinstance(v, bool) or v is None:
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str):
        try:
            return int(v, 10)
        except ValueError:
            return default
    return default


def _pull_state(p):
    """One cached pull with an integer time, or None when the row cannot be used.

    A time of 'abc' must be dropped here. mark_dumps reads that int every run, so leaving
    the bad row in the cache makes every later run raise before it can rewrite the file.
    """
    if not isinstance(p, dict) or not p.get("pool_key"):
        return None
    raw = p.get("time")
    if isinstance(raw, bool) or isinstance(raw, float) and not raw.is_integer():
        return None
    if isinstance(raw, str):
        try:
            raw = int(raw, 10)
        except ValueError:
            return None
    elif isinstance(raw, float):
        raw = int(raw)
    if not isinstance(raw, int) or raw <= 0:
        return None
    out = {"time": raw, "pool_key": p.get("pool_key")}
    for k in ("hash", "tx_short", "reserve_ada", "ticker", "unit", "token"):
        if k in p:
            out[k] = p.get(k)
    ada = _ada(p.get("ada"))
    if ada is not None and ada > 0:
        out["ada"] = ada
    after = _ada(p.get("reserve_ada"))
    if after is None:
        after = _ada(p.get("reserve_ada_after"))
    if after is not None and after >= 0:
        out["reserve_ada"] = after
    tag = _party_tag(p.get("actor_tag"))
    if tag:
        out["actor_tag"] = tag
    sold = _ada(p.get("sold_ada"))
    if sold is not None and sold >= 0:
        out["sold_ada"] = sold
    return out


def _pool_public(p):
    keep = ("ticker", "token", "policy_short", "dex", "pool_key", "reserve_ada", "reserve_qty", "asof")
    return {k: p.get(k) for k in keep}


def _cache_public(cache):
    """A cache the run can use. Bad types (units: [], next: 'x') become empty cursors, not an error."""
    if not isinstance(cache, dict):
        cache = {}
    height = {}
    raw_h = cache.get("height")
    if isinstance(raw_h, dict):
        for k, v in raw_h.items():
            if isinstance(k, str) and k:
                height[k] = _safe_int(v)
    markers = {}
    raw_m = cache.get("markers")
    if isinstance(raw_m, dict):
        for unit, rows in raw_m.items():
            if not isinstance(unit, str) or not isinstance(rows, list):
                continue
            kept = []
            for m in rows:
                if not isinstance(m, dict) or m.get("dex") not in TRACKED_DEX or not isinstance(m.get("marker"), str):
                    continue
                mk = _hex(m["marker"])
                if len(mk) >= 56:
                    kept.append({"dex": m["dex"], "marker": mk})
            if kept:
                markers[unit] = kept
    units = {}
    raw_u = cache.get("units")
    if isinstance(raw_u, dict):
        for k, v in raw_u.items():
            if isinstance(k, str) and isinstance(v, str) and len(_hex(v)) >= 56:
                units[k] = _hex(v)
    discovered = {}
    raw_d = cache.get("discovered_at")
    if isinstance(raw_d, dict):
        for k, v in raw_d.items():
            if isinstance(k, str):
                discovered[k] = _safe_int(v)
    misses = {}
    raw_miss = cache.get("misses")
    if isinstance(raw_miss, dict):
        for k, v in list(raw_miss.items())[-50:]:
            if isinstance(k, str):
                misses[k] = _safe_int(v)
    seen = [h for h in cache.get("seen") or [] if isinstance(h, str)][-4000:] if isinstance(cache.get("seen"), list) else []
    raw_pulls = cache.get("open_pulls") if isinstance(cache.get("open_pulls"), list) else []
    pulls = [row for row in (_pull_state(p) for p in raw_pulls) if row]
    checked = {}
    raw_c = cache.get("last_checked")
    if isinstance(raw_c, dict):
        for k, v in raw_c.items():
            if not isinstance(k, str) or not k or k.startswith("_"):
                continue
            n = _unix(v)
            if n > 0:
                checked[k.upper()] = n
    return {
        "height": height, "markers": markers, "units": units, "seen": seen, "open_pulls": pulls,
        "discovered_at": discovered, "misses": misses, "last_checked": checked,
        "capped": cache.get("capped") is True, "next": _safe_int(cache.get("next")),
    }


def _scan_token(reader, row, cache, scripts, splash_pols, bc, lp_pols, watch, loaded, ex_tags, names, seen, seen_list,
                window_after, now_s):
    unit = row["unit"]
    markers = _markers_for(unit, (cache.get("markers") or {}).get(unit))
    height = cache.setdefault("height", {})
    txs, complete, served = [], True, False
    tracked = {m["marker"] for m in markers}
    # Newest-first page that never reaches the cursor must not move the cursor to that page.
    hold_height = set()
    for m in markers:
        if reader.late() or reader.koios_left() <= 0:
            complete = False
            break
        after = max(_safe_int(height.get(m["marker"])), int(window_after))
        page = reader.asset_txs(m["marker"], after=after, limit=bc["max_txs_per_pool"], newest=False)
        if page is None:
            complete = False
            hold_height.add(m["marker"])
            continue
        served = True
        rows, page_done, newest_first = page
        txs.extend((h, hg, bt, m["marker"]) for h, hg, bt in rows)
        if not page_done or len(rows) >= int(bc["max_txs_per_pool"]):
            complete = False
        # Newest-first and still above the cursor: leave height[marker] where it was.
        if newest_first and not page_done:
            hold_height.add(m["marker"])
    discovered = cache.setdefault("discovered_at", {})
    last = _safe_int(discovered.get(unit))
    extra = [m for m in ((cache.get("markers") or {}).get(unit) or [])
             if isinstance(m, dict) and m.get("dex") not in (None, "minswap_v2")]
    due = now_s - last >= float(bc["discover_every_h"]) * 3600
    if due and not extra and not reader.late() and reader.koios_left() > 0:
        page = reader.asset_txs(unit, limit=bc["discover_txs"], newest=True)
        if page is None:
            complete = False
        else:
            served = True
            rows, page_done, _newest = page
            txs.extend((h, hg, bt, None) for h, hg, bt in rows)
            discovered[unit] = int(now_s)
            if not page_done or len(rows) >= int(bc["discover_txs"]):
                complete = False
    uniq = {}
    for h, hg, bt, src in txs:
        if h and h not in uniq:
            uniq[h] = (h, hg, bt, src)
    ordered = sorted(uniq.values(), key=lambda t: (t[1] is None, t[1] or 0, t[0]))
    todo = [t for t in ordered if t[0] not in seen]
    found, absent = reader.utxos([t[0] for t in todo]) if todo else ({}, set())
    evs, read = [], 0
    misses = cache.setdefault("misses", {})
    if not isinstance(misses, dict):
        misses = {}
        cache["misses"] = misses
    new_markers = [m for m in ((cache.get("markers") or {}).get(unit) or []) if isinstance(m, dict)]
    known = {(m.get("dex"), m.get("marker")) for m in new_markers}
    for h, hg, bt, src in ordered:
        if h in seen:
            read += 1
            if hg is not None and src in tracked and src not in hold_height:
                height[src] = max(_safe_int(height.get(src)), int(hg))
            continue
        if h not in found:
            if h in absent:
                n = _safe_int(misses.get(h)) + 1
                misses[h] = n
                if n >= 2:
                    seen.add(h)
                    seen_list.append(h)
                    misses.pop(h, None)
                    continue
            complete = False
            break
        if not bt:
            complete = False
            break
        misses.pop(h, None)
        tx = {"hash": h, "time": int(bt), "inputs": found[h]["inputs"], "outputs": found[h]["outputs"]}
        got_rows = _apply_tx(tx, row, scripts, splash_pols, bc, lp_pols, watch, loaded, ex_tags, names)
        for ev in got_rows:
            ev["_unit"] = unit
            if ev.get("dex") not in (None, "minswap_v2") and ev.get("marker"):
                key = (ev["dex"], ev["marker"])
                if key not in known:
                    known.add(key)
                    new_markers.append({"dex": ev["dex"], "marker": ev["marker"]})
            evs.append(ev)
        seen.add(h)
        seen_list.append(h)
        read += 1
        if hg is not None and src in tracked and src not in hold_height:
            height[src] = max(_safe_int(height.get(src)), int(hg))
    if len(misses) > 50:
        for old in list(misses)[:-50]:
            misses.pop(old, None)
    return evs, {"read": read, "complete": complete and not reader.late(), "pools": len({e["pool_key"] for e in evs})}, new_markers, served
