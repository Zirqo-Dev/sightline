"""SPD-43 big-move detector, phase 1 (zero AI, free Koios public tier, no key).

Writes desk-data:solada-prefilter/big_moves.json: any stake key (or a non-script address with no stake
key) moving roughly >= min_ada ADA, or >= min_pool_pct% of the token pool's ADA side, into or out of one
token within window_h hours. Rows: tag, ticker, direction, ada, qty, tx_short, time, next_hop (null until
phase 2), plus window_ada / pool_pct / lp. Rows are kept keep_h hours.

ADA per move = the owner's net lovelace summed over every input/output of that transaction (all ADA legs).
LP tokens are labelled ticker "LP" / lp=true. DEX pool / script addresses without a stake key are
counterparties, not movers, and are skipped.

Privacy: full addresses / stake keys live only in memory and the private Actions cache (same as
flags-cache.json); public output carries short tags (<= 12 chars). Owner wallets matching the
PRIVACY_GUARD_PATTERNS secret (owner patterns) are dropped before anything is cached or written; that
secret is never printed. Nothing here looks up the owner's wallet, stake key or holdings.

Koios politeness: shared Http pacing (config pace_seconds.koios), small windows (24h of blocks, a wide
_after_block_height window can hit the 30s query timeout), <= utxo_batch hashes per POST (1 KB public-tier
body limit), a per-run call budget, and a cache so each tx is fetched once.
"""
import hashlib, json, os, re
from datetime import datetime, timezone

import flags as F
import valuation as V

KOIOS = "https://api.koios.rest/api/v1"
BLOCKS_PER_HOUR = 180  # ~20 s blocks
DEFAULTS = {"enabled": True, "min_ada": 5000, "min_pool_pct": 10, "window_h": 24, "keep_h": 72,
            "max_units": 12, "max_txs_per_unit": 100, "utxo_batch": 10, "max_calls": 80}


def bm_cfg(cfg=None):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("big_moves") or {}).items() if not k.startswith("_")})
    return out


def tag(owner):
    return "m-" + hashlib.sha256(owner.encode()).hexdigest()[:8]  # 10 chars


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def owner_patterns(env_text):
    pats = []
    for p in (env_text or "").splitlines():
        p = p.strip()
        if p and not p.startswith("#"):
            try:
                pats.append(re.compile(p))
            except re.error:
                pass
    return pats


def _owner(addr, stake):
    """Stake key, else the address itself if its payment part is a key (not a pool/script); else None."""
    if stake:
        return stake
    s, script = F.addr_info(addr)
    if s:
        return s
    return None if script or not addr else addr


def legs_from_tx(unit, tx_hash, tx_time, utxo, lp_pols, excluded=()):
    """[{owner, unit, direction, lovelace, qty_raw, lp, tx, time}] for one tx.

    utxo = {'inputs'|'outputs': [(addr, lovelace, {unit: qty}, stake_or_None)]}. Net per owner across the tx.
    excluded: compiled owner patterns; an owner is dropped when ANY of its addresses or its stake key match."""
    acc, addrs = {}, {}
    for side, sign in (("inputs", -1), ("outputs", 1)):
        for row in utxo.get(side, []):
            addr, lov, assets = row[0], row[1], row[2]
            stake = row[3] if len(row) > 3 else None
            o = _owner(addr, stake)
            if not o:
                continue
            addrs.setdefault(o, set()).add(addr)
            a = acc.setdefault(o, {"lov": 0, "q": 0, "lp": False})
            a["lov"] += sign * int(lov)
            for u, q in assets.items():
                if u == unit:
                    a["q"] += sign * int(q)
                elif V.lp_dex(u, lp_pols) and int(q):
                    a["lp"] = True
    out = []
    for o, a in acc.items():
        if not a["q"]:
            continue
        if any(p.search(x) for p in excluded for x in addrs[o] | {o}):
            continue
        out.append({"owner": o, "unit": unit, "direction": "in" if a["q"] > 0 else "out", "lovelace": abs(a["lov"]),
                    "qty_raw": abs(a["q"]), "lp": a["lp"] or bool(V.lp_dex(unit, lp_pols)),
                    "lp_unit": bool(V.lp_dex(unit, lp_pols)), "tx": tx_hash, "time": int(tx_time or 0)})
    return out


def flag_moves(legs, meta, now_s, bc):
    """Public rows for legs whose (owner, unit, direction) 24h ADA sum meets min_ada or min_pool_pct of the pool ADA side."""
    win = bc["window_h"] * 3600
    groups = {}
    for l in legs:
        if now_s - l["time"] <= win:
            groups.setdefault((l["owner"], l["unit"], l["direction"]), []).append(l)
    rows = []
    for (o, u, d), ls in groups.items():
        m = meta.get(u) or {}
        total = sum(l["lovelace"] for l in ls) / 1e6
        side = m.get("ada_side")
        pct = round(100.0 * total / side, 1) if side else None
        if not (total >= bc["min_ada"] or (pct is not None and pct >= bc["min_pool_pct"])):
            continue
        dec = m.get("decimals")
        for l in sorted(ls, key=lambda x: x["time"]):
            rows.append({"tag": tag(o), "ticker": "LP" if l.get("lp_unit") else m.get("ticker", "?"),
                         "direction": d, "ada": round(l["lovelace"] / 1e6, 2),
                         "qty": round(l["qty_raw"] / 10 ** dec, 6) if dec is not None else l["qty_raw"],
                         "qty_basis": "whole" if dec is not None else "raw", "tx_short": l["tx"][:10], "time": iso(l["time"]),
                         "window_ada": round(total, 2), "pool_pct": pct, "lp": l["lp"], "unit_short": u[:10], "next_hop": None})
    return rows


def merge(prev_rows, new_rows, now_s, keep_h):
    keep = {}
    for r in (prev_rows or []) + new_rows:
        try:
            t = datetime.strptime(r["time"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except (KeyError, ValueError):
            continue
        if now_s - t <= keep_h * 3600:
            keep[(r["tag"], r.get("unit_short"), r["tx_short"], r["direction"])] = r  # newer window_ada wins
    return sorted(keep.values(), key=lambda r: (r["time"], r["tag"]))


class KoiosMoves:
    def __init__(self, http, bc):
        self.h, self.bc = http, bc
        self.start = http.calls.get("koios", 0)

    def budget_left(self):
        return self.bc["max_calls"] - (self.h.calls.get("koios", 0) - self.start)

    def tip(self):
        r = self.h.req("koios", KOIOS + "/tip")
        return (r[0]["block_height"], r[0]["block_time"]) if r else (None, None)

    def asset_txs(self, unit, after):
        r = self.h.req("koios", f"{KOIOS}/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}"
                       f"&_after_block_height={after}&_history=true&order=block_height.asc&limit={self.bc['max_txs_per_unit']}")
        return None if r is None else [(x["tx_hash"], x["block_height"], x.get("block_time")) for x in r]

    def tx_utxos(self, hashes):
        out = {}
        n = max(1, int(self.bc["utxo_batch"]))
        for i in range(0, len(hashes), n):
            if self.budget_left() <= 0:
                break
            r = self.h.req("koios", KOIOS + "/tx_utxos", method="POST", body={"_tx_hashes": hashes[i:i + n]})
            for tx in r or []:
                out[tx["tx_hash"]] = {s: [((u.get("payment_addr") or {}).get("bech32") or "", int(u.get("value") or 0),
                                           {a["policy_id"] + a["asset_name"]: int(a["quantity"]) for a in (u.get("asset_list") or [])},
                                           u.get("stake_addr")) for u in tx.get(s, [])] for s in ("inputs", "outputs")}
        return out


def run(http, cfg, meta, cache_dir, data_dir, dry_run, now_s, lp_pols, excluded):
    """meta = {unit: {ticker, ada_side, decimals}}. Returns (summary, doc)."""
    bc = bm_cfg(cfg)
    if not bc["enabled"]:
        return {"enabled": False}, None
    km = KoiosMoves(http, bc)
    cpath = os.path.join(cache_dir, "big-moves-cache.json")
    cache = json.load(open(cpath)) if os.path.exists(cpath) else {}
    cache.setdefault("height", {})
    cache.setdefault("legs", [])
    tip_h, _ = km.tip()
    units = sorted(meta, key=lambda u: -(meta[u].get("ada_side") or 0))[: bc["max_units"]]
    stats = {"units": len(units), "txs": 0, "capped": []}
    for u in units:
        if tip_h is None or km.budget_left() <= 1:
            stats["capped"].append(meta[u].get("ticker"))
            continue
        after = max(cache["height"].get(u, 0), tip_h - bc["window_h"] * BLOCKS_PER_HOUR)
        txs = km.asset_txs(u, after)
        if txs is None:
            continue
        ut = km.tx_utxos([t[0] for t in txs])
        done_h = after
        for hsh, hgt, bt in txs:
            if hsh not in ut:
                break  # budget ran out: resume from here next run
            cache["legs"].extend(legs_from_tx(u, hsh, bt, ut[hsh], lp_pols, excluded))
            done_h = hgt  # asset_txs is block-ordered; partial blocks are re-read next run (deduped below)
            stats["txs"] += 1
        if len(txs) >= bc["max_txs_per_unit"] or any(t[0] not in ut for t in txs):
            stats["capped"].append(meta[u].get("ticker"))
            done_h = max(after, done_h - 1)
        cache["height"][u] = done_h
    seen, legs = set(), []
    for l in cache["legs"]:
        k = (l["owner"], l["unit"], l["tx"])
        if k not in seen and now_s - l["time"] <= bc["window_h"] * 3600:
            seen.add(k)
            legs.append(l)
    cache["legs"] = legs
    rows = flag_moves(legs, meta, now_s, bc)
    path = os.path.join(data_dir, "big_moves.json")
    prev = json.load(open(path)).get("moves", []) if os.path.exists(path) else []
    moves = merge(prev, rows, now_s, bc["keep_h"])
    doc = F.assert_public({
        "at": iso(now_s), "phase": 1, "window_h": bc["window_h"], "keep_h": bc["keep_h"],
        "thresholds": {"min_ada": bc["min_ada"], "min_pool_pct": bc["min_pool_pct"]},
        "note": "Research data for Snek. next_hop is phase 2 (null). ada = the mover's net ADA across the whole tx; LP = liquidity token.",
        "moves": moves, "units_scanned": stats["units"], "txs_read": stats["txs"], "capped": stats["capped"],
        "koios_calls": http.calls.get("koios", 0) - km.start,
    })
    if not dry_run:
        os.makedirs(cache_dir, exist_ok=True)
        json.dump(cache, open(cpath, "w"))
        json.dump(doc, open(path, "w"), indent=1)
    return {k: doc[k] for k in ("units_scanned", "txs_read", "capped", "koios_calls")} | {"moves_new": len(rows)}, doc
