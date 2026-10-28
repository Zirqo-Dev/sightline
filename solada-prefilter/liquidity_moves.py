"""SPD-55 liquidity moves, phase 1 (zero AI, free Koios public tier, no key).

Writes desk-data:solada-prefilter/liquidity_moves.json for Snek's cardano_top10 (desk log, current
row, else the newest history row). Tracks Minswap, SundaeSwap and Splash pool reserves: liquidity
adds, liquidity pulls, and big swaps. An LP pull followed by a sell into the same pool is flagged
ahead_of_dump.

Swaps are the pool's own reserve change. The batcher signs the transaction, so the trader is not
named until per-DEX order decoders exist. actor_tag is set only on an add or a pull, and only when
a key wallet moved the LP token. It is a w- hash or a role label, never an address or a stake key.

ADA is the pool reserve's ADA leg (lovelace in versus out), never raw quantity times price.
Token display keys are policy-based (whale_ledger.assign_token_keys, PR #37). LP tokens are
labelled ticker "LP". Full tx hashes stay in the private Actions cache. A failure here is swallowed
by the pre-filter so the webhook, state.json and kill_status.json still finish.

Koios call budget per run (default max_calls 36, shared pace): 1 tip, then per token one LP or
marker asset_txs plus tx_info batches under 1 KB. Blockfrost is used only when a Koios read
returns nothing, and only up to max_bf_calls (default 12). The job also stops at its budget_s and
at the shared research deadline, whichever is sooner.
"""
import hashlib, os, time
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

DEFAULTS = {
    "enabled": True, "budget_s": 50, "max_calls": 36, "max_bf_calls": 12, "utxo_batch": 10,
    "max_tokens": 10, "max_txs_per_pool": 15, "discover_txs": 12, "discover_every_h": 24,
    "min_swap_ada": 5000, "min_lp_ada": 1000, "min_pool_ada": 500,
    "dump_window_h": 6, "dump_ada": 5000, "dump_reserve_pct": 8,
    "keep_h": 72, "keep_rows": 400, "keep_seen": 4000,
}

NOTE = (
    "Research only, for Snek. Swaps are read at pool level only: the batcher signs the transaction, "
    "so the trader is not named until per-DEX order decoders exist. actor_tag is set on a liquidity "
    "add or pull when a key wallet moved the LP token, as a w- hash or a role label. ADA is the pool "
    "reserve's ADA leg, not raw quantity times price. LP tokens are labelled LP."
)
LIMITS = [
    "Koios: at most max_calls per run (default 36), on the shared pre-filter pace. One tip, then one asset_txs page per pool marker and tx_info batches kept under 1 KB.",
    "Blockfrost runs only after a Koios read returns nothing, and only up to max_bf_calls (default 12). An empty BLOCKFROST_PROJECT_ID skips it.",
    "Minswap V2 markers are the derived LP asset name. Sundae and Splash pools are recognized when a continuing pool UTxO is inside a transaction this job already read. Discovery of those pools rechecks the newest token transactions at most every discover_every_h.",
    "The job stops at budget_s or the shared research deadline. A capped pool resumes next run. Nothing here changes desk rules, kill lines, or the webhook.",
]


def lm_cfg(cfg=None):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("liquidity_moves") or {}).items() if not str(k).startswith("_")})
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


def mark_dumps(events, open_pulls, bc, now_s):
    """Flag an LP pull that is followed by a sell into the same pool.

    events and open_pulls use time (unix), pool_key, kind, direction, ada (ADA), reserve_ada,
    tx_short. Returns (pulls still waiting, pulls that were flagged). Does not copy stakes.
    """
    window = float(bc["dump_window_h"]) * 3600
    pulls = []
    for p in open_pulls or []:
        if isinstance(p, dict) and p.get("pool_key") and now_s - int(p.get("time") or 0) <= window:
            pulls.append(dict(p))
    for ev in sorted(events, key=lambda e: (e.get("time") or 0, e.get("tx_short") or "")):
        if ev.get("kind") == "lp_remove":
            pulls.append(ev)
        elif ev.get("kind") == "swap" and ev.get("direction") == "sell":
            for p in pulls:
                if p.get("ahead_of_dump") or not _same_pool(p, ev):
                    continue
                if not (int(p.get("time") or 0) < int(ev.get("time") or 0) <= int(p.get("time") or 0) + window):
                    continue
                ada = abs(float(ev.get("ada") or 0))
                reserve = float(p.get("reserve_ada") or 0)
                pct = (100.0 * ada / reserve) if reserve else 0
                if ada >= bc["dump_ada"] or pct >= bc["dump_reserve_pct"]:
                    p["ahead_of_dump"] = True
                    p["dump_tx_short"] = ev.get("tx_short")
                    p["dump_ada"] = round(ada, 2)
                    ev_hash = p.get("hash")
                    for src in events:
                        if src is p or (ev_hash and src.get("hash") == ev_hash and src.get("kind") == "lp_remove"):
                            src["ahead_of_dump"] = True
                            src["dump_tx_short"] = ev.get("tx_short")
                            src["dump_ada"] = round(ada, 2)
    flagged = [p for p in pulls if p.get("ahead_of_dump")]
    waiting = [p for p in pulls if not p.get("ahead_of_dump") and now_s - int(p.get("time") or 0) <= window]
    return waiting, flagged


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


def resolve_tokens(top10, units_by_ticker, cached, max_tokens):
    """[{ticker, unit, rank, policy_short}] from Snek's top 10. A policy_short that does not
    prefix the resolved unit is skipped. Full units are not invented."""
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
    rows, skipped, seen = [], [], set()
    for r in ordered:
        if len(rows) >= int(max_tokens):
            break
        tk = str(r.get("ticker") or "").upper()
        short = _hex(r.get("policy_short") if isinstance(r.get("policy_short"), str) else "")
        unit = None
        for src in (r.get("unit"), r.get("asset_id"), rev.get(tk), cached.get(tk)):
            h = _hex(src) if isinstance(src, str) else ""
            if len(h) >= 56:
                unit = h
                break
        if not unit:
            skipped.append({"ticker": tk, "skipped": "unresolved"})
            continue
        if short and not unit.startswith(short):
            skipped.append({"ticker": tk, "skipped": "policy_mismatch"})
            continue
        if unit in seen:
            continue
        seen.add(unit)
        rows.append({"ticker": tk, "unit": unit, "rank": r.get("rank"), "policy_short": WL.policy_short(unit, 8)})
    return rows, skipped


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
        if self.late() or self.koios_left() <= 0:
            return None
        r = self.h.req("koios", KOIOS + "/tip", quiet404=False)
        if r:
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
        return out, len(out) < int(limit)

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
        return rows, complete

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
        if kind in ("lp_add", "lp_remove"):
            ev["actor_tag"] = _safe_tag(ev.get("lp_stake"), ev.get("lp_addr"), watch, loaded, ex_tags)
        ev.pop("lp_stake", None)
        ev.pop("lp_addr", None)
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
    loaded = DC._patterns(owner_env)
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
    if rows and cache.get("capped"):
        k = int(cache.get("next") or 0) % len(rows)
        rows = rows[k:] + rows[:k]
    listed = list(rows)
    keep_next = False
    reader = Reader(http, bc, bf_key, deadline)
    names = WL.assign_token_keys([row["unit"] for row in rows])
    seen_list = [h for h in cache.get("seen") or [] if isinstance(h, str)]
    seen = set(seen_list)
    events, pools, coverage, capped = [], [], {}, []
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
            evs, cov, new_markers = _scan_token(
                reader, row, cache, scripts, splash_pols, bc, lp_pols or V.lp_policies(cfg), watch, loaded, ex_tags,
                names, seen, seen_list, window_after, now_s)
        except (SystemExit, Exception) as e:
            coverage[tk] = {"read": 0, "complete": False, "skipped": type(e).__name__}
            continue
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
    cache["capped"] = bool(capped)
    if keep_next:
        cache["next"] = kept_next
    elif capped:
        cache["next"] = kept_next + 1
    else:
        cache["next"] = 0
    koios_calls = http.calls.get("koios", 0) - reader.start
    bf_calls = http.calls.get("blockfrost", 0) - reader.bf_start
    doc = {
        "at": iso(now_s), "phase": 1, "source": "snek_cardano_top10",
        "note": NOTE, "limits": LIMITS, "level": "pool",
        "thresholds": {k: bc[k] for k in ("min_swap_ada", "min_lp_ada", "dump_window_h", "dump_ada", "dump_reserve_pct")},
        "budget": {"max_calls": bc["max_calls"], "max_bf_calls": bc["max_bf_calls"], "budget_s": bc["budget_s"]},
        "actor_tags": bool(loaded),
        "moves": merged[-int(bc["keep_rows"]):],
        "pools": merge_pools(prev_doc.get("pools"), [_pool_public(p) for p in pools]),
        "tokens": [{"ticker": r["ticker"], "policy_short": r["policy_short"], "rank": r.get("rank")} for r in listed],
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
    out = {}
    for k in ("hash", "time", "pool_key", "tx_short", "reserve_ada", "ticker", "unit", "token"):
        if k in p:
            out[k] = p.get(k)
    if "time" in out:
        out["time"] = _safe_int(out["time"])
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
    pulls = [p for p in cache.get("open_pulls") or [] if isinstance(p, dict)] if isinstance(cache.get("open_pulls"), list) else []
    return {
        "height": height, "markers": markers, "units": units, "seen": seen, "open_pulls": pulls,
        "discovered_at": discovered, "misses": misses,
        "capped": cache.get("capped") is True, "next": _safe_int(cache.get("next")),
    }


def _scan_token(reader, row, cache, scripts, splash_pols, bc, lp_pols, watch, loaded, ex_tags, names, seen, seen_list,
                window_after, now_s):
    unit = row["unit"]
    markers = _markers_for(unit, (cache.get("markers") or {}).get(unit))
    height = cache.setdefault("height", {})
    txs, complete = [], True
    tracked = {m["marker"] for m in markers}
    for m in markers:
        if reader.late() or reader.koios_left() <= 0:
            complete = False
            break
        after = max(_safe_int(height.get(m["marker"])), int(window_after))
        page = reader.asset_txs(m["marker"], after=after, limit=bc["max_txs_per_pool"], newest=False)
        if page is None:
            complete = False
            continue
        rows, page_done = page
        txs.extend((h, hg, bt, m["marker"]) for h, hg, bt in rows)
        if not page_done or len(rows) >= int(bc["max_txs_per_pool"]):
            complete = False
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
            rows, page_done = page
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
            if hg is not None and src in tracked:
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
        if hg is not None and src in tracked:
            height[src] = max(int(height.get(src) or 0), int(hg))
    if len(misses) > 50:
        for old in list(misses)[:-50]:
            misses.pop(old, None)
    return evs, {"read": read, "complete": complete and not reader.late(), "pools": len({e["pool_key"] for e in evs})}, new_markers
