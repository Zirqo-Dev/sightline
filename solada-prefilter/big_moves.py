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
import koios_tx as KT
import valuation as V

KOIOS = "https://api.koios.rest/api/v1"
BLOCKS_PER_HOUR = 180  # ~20 s blocks
DEFAULTS = {"enabled": True, "min_ada": 5000, "min_pool_pct": 10, "window_h": 24, "keep_h": 72,
            "max_units": 12, "max_txs_per_unit": 100, "max_pages_per_unit": 4, "utxo_batch": 10, "max_calls": 80,
            "budget_s": 150}


def bm_cfg(cfg=None):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("big_moves") or {}).items() if not k.startswith("_")})
    return out


def tag(owner):
    return "m-" + hashlib.sha256(owner.encode()).hexdigest()[:8]  # 10 chars


def otag(owner):
    """Tag an owner once (cached legs already hold m- tags)."""
    return owner if str(owner).startswith("m-") and len(owner) == 10 else tag(owner)


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# POSIX classes ([[:alnum:]]) and GNU word boundaries (\< \>) compile in Python but mean something else / never
# match there, so the owner exclusion would fail open: treat them as unusable (re-review nice-to-have)
_POSIX = re.compile(r"\[\[:[a-z]+:\]\]|\[:[a-z]+:\]|\\[<>]")


def owner_patterns(env_text):
    """(compiled patterns, n_unusable). The privacy guard reads these with grep -E (POSIX ERE); a line Python's re
    cannot compile, or one using a POSIX class like [[:alnum:]] (a different meaning in Python), is unusable.
    Callers skip the run when the list is empty or anything is unusable (fail closed, S4). Never printed."""
    pats, bad = [], 0
    for p in (env_text or "").splitlines():
        p = p.strip()
        if not p or p.startswith("#"):
            continue
        if _POSIX.search(p):
            bad += 1
            continue
        try:
            pats.append(re.compile(p))
        except re.error:
            bad += 1
    return pats, bad


def _owner(addr, stake):
    """Stake key, else the address itself if its payment part is a key (not a pool/script); else None."""
    if stake:
        return stake
    s, script = F.addr_info(addr)
    if s:
        return s
    return None if script or not addr else addr


def _row(row):
    if isinstance(row, dict):
        return row["addr"], row["lovelace"], row["assets"], row.get("stake")
    return row[0], row[1], row[2], (row[3] if len(row) > 3 else None)


def legs_from_tx(unit, tx_hash, tx_time, utxo, lp_pols, excluded=(), m=None, exclude_tags=()):
    """[{owner, unit, direction, lovelace, qty_raw, lp, ada_basis, tx, time}] for one tx.

    utxo = {'inputs'|'outputs': [(addr, lovelace, {unit: qty}, stake_or_None) or {addr, lovelace, assets, stake}]}.
    Net per owner across the tx. A script-payment row (addr1z/addr1x pool or order contract carrying a stake key)
    is a counterparty, not that stake's wallet, unless the same stake also has a key-payment UTxO in the tx (M2).
    ADA per leg (S2): the owner's net ADA only when it moves opposite to the token and this is the owner's only
    non-LP token in the tx (ada_basis "tx"); otherwise qty x price_ada from m (ada_basis "price"), else 0 ("none").
    excluded: compiled owner patterns (dropped when ANY of its addresses or its stake key match).
    exclude_tags: flags-style w- tags (curve reserve etc.) dropped as movers."""
    rows = {side: [_row(r) for r in utxo.get(side, [])] for side in ("inputs", "outputs")}
    key_stakes = set()
    for side in rows.values():
        for addr, _, _, stake in side:
            s, script = F.addr_info(addr)
            if not script and (stake or s):
                key_stakes.add(stake or s)
    acc, addrs = {}, {}
    for side, sign in (("inputs", -1), ("outputs", 1)):
        for addr, lov, assets, stake in rows[side]:
            if F.addr_info(addr)[1] and (stake or F.addr_info(addr)[0]) not in key_stakes:
                continue  # script output with a stake part: a pool / contract, not the stake's wallet
            o = _owner(addr, stake)
            if not o:
                continue
            addrs.setdefault(o, set()).add(addr)
            a = acc.setdefault(o, {"lov": 0, "q": 0, "lp": False, "other": {}})
            a["lov"] += sign * int(lov)
            for u, q in assets.items():
                if u == unit:
                    a["q"] += sign * int(q)
                elif V.lp_dex(u, lp_pols) and int(q):
                    a["lp"] = True
                elif int(q):
                    a["other"][u] = a["other"].get(u, 0) + sign * int(q)
    m = m or {}
    out = []
    for o, a in acc.items():
        if not a["q"]:
            continue
        if any(p.search(x) for p in excluded for x in addrs[o] | {o}):
            continue
        if exclude_tags and any(F.tag(x) in exclude_tags for x in addrs[o] | {o}):
            continue
        others = [u for u, q in a["other"].items() if q]
        lp_unit = bool(V.lp_dex(unit, lp_pols))
        if a["lov"] and ((a["lov"] > 0) != (a["q"] > 0) and not others and not lp_unit or lp_unit):
            lov, basis = abs(a["lov"]), "tx"  # LP-token legs keep the tx's ADA (the liquidity added/removed)
        elif m.get("price_ada") and m.get("decimals") is not None:
            lov, basis = int(abs(a["q"]) / 10 ** m["decimals"] * float(m["price_ada"]) * 1e6), "price"
        else:
            lov, basis = 0, "none"
        out.append({"owner": o, "unit": unit, "direction": "in" if a["q"] > 0 else "out", "lovelace": lov,
                    "qty_raw": abs(a["q"]), "lp": a["lp"] or lp_unit, "lp_unit": lp_unit, "ada_basis": basis,
                    "tx": tx_hash, "time": int(tx_time or 0)})
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
            rows.append({"tag": otag(o), "ticker": "LP" if l.get("lp_unit") else m.get("ticker", "?"),
                         "direction": d, "ada": round(l["lovelace"] / 1e6, 2),
                         "qty": round(l["qty_raw"] / 10 ** dec, 6) if dec is not None else l["qty_raw"],
                         "qty_basis": "whole" if dec is not None else "raw", "tx_short": l["tx"][:10], "time": iso(l["time"]),
                         "window_ada": round(total, 2), "pool_pct": pct, "lp": l["lp"], "unit_short": u[:10],
                         "ada_basis": l.get("ada_basis", "tx"), "next_hop": None})
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
        r = self.h.req("koios", KOIOS + "/tip", quiet404=False)
        return (r[0]["block_height"], r[0]["block_time"]) if r else (None, None)

    def asset_txs(self, unit, after, desc=False, offset=0):
        r = self.h.req("koios", f"{KOIOS}/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}"
                       f"&_after_block_height={after}&_history=true&order=block_height.{'desc' if desc else 'asc'}"
                       f"&limit={self.bc['max_txs_per_unit']}&offset={int(offset)}", quiet404=False)
        if r is None:
            return None
        return sorted(((x["tx_hash"], x["block_height"], x.get("block_time")) for x in r), key=lambda t: t[1])

    def tx_utxos(self, hashes):
        """/tx_info (S9): spend inputs only (collateral/reference excluded), batches under 1 KB."""
        return KT.tx_info(self.h, hashes, self.bc["utxo_batch"], lambda: self.budget_left() > 0)


def run(http, cfg, meta, cache_dir, data_dir, dry_run, now_s, lp_pols, excluded, deadline=None):
    """meta = {unit: {ticker, ada_side, decimals, price_ada}}. excluded = PRIVACY_GUARD_PATTERNS text (or a
    pre-compiled list). Returns (summary, doc). Skips (fails closed) when the owner patterns are empty or unusable."""
    bc = bm_cfg(cfg)
    if not bc["enabled"]:
        return {"enabled": False}, None
    if isinstance(excluded, str):
        excluded, bad = owner_patterns(excluded)
        if bad or not excluded:
            print(f"big_moves: skipped: owner patterns unusable ({bad} bad, {len(excluded)} usable)")
            return {"skipped": "owner patterns unusable", "bad_patterns": bad}, None
    ex_tags = F.excluded_tags(cfg.get("flags"))
    km = KoiosMoves(http, bc)
    cpath = os.path.join(cache_dir, "big-moves-cache.json")
    cache = KT.read_json(cpath, {})
    cache.setdefault("height", {})
    cache.setdefault("legs", [])
    cache.setdefault("capped", [])
    cache["legs"] = [{**l, "owner": otag(l["owner"])} for l in cache["legs"] if isinstance(l, dict) and l.get("owner")]
    tip_h, _ = km.tip()
    units = sorted(meta, key=lambda u: -(meta[u].get("ada_side") or 0))[: bc["max_units"]]
    if units:  # rotate the start so a budget cap never starves the same tail units (S3)
        k = int(cache.get("rot", 0)) % len(units)
        units = units[k:] + units[:k]
        cache["rot"] = k + 1
    stats = {"units": len(units), "txs": 0, "capped": []}
    was_capped = set(cache["capped"])
    now_capped = []
    coverage = {}
    lim = bc["max_txs_per_unit"]
    for u in units:
        tk = meta[u].get("ticker")
        if tip_h is None or km.budget_left() <= 1 or (deadline and __import__("time").time() >= deadline):
            stats["capped"].append(tk)
            now_capped.append(u) if u in was_capped else None
            coverage[tk] = {"read": 0, "complete": False, "skipped": "budget/deadline"}
            continue
        after = max(cache["height"].get(u, 0), tip_h - bc["window_h"] * BLOCKS_PER_HOUR)
        desc = u in was_capped  # busy token: read newest-first, paging back toward the cached height (S3)
        txs, pages, complete = [], 0, False
        while True:
            page = km.asset_txs(u, after, desc=desc, offset=pages * lim)
            if page is None:
                break
            txs += page
            pages += 1
            if len(page) < lim:
                complete = True
                break
            # asc: one page per run (resumes next run); desc: keep paging back while the budget allows
            if not desc or km.budget_left() <= 2 + (lim + bc["utxo_batch"] - 1) // bc["utxo_batch"] or \
                    pages >= bc["max_pages_per_unit"] or (deadline and __import__("time").time() >= deadline):
                break
        if not txs and not complete:
            coverage[tk] = {"read": 0, "complete": False, "skipped": "koios"}
            continue
        txs = sorted({t[0]: t for t in txs}.values(), key=lambda t: t[1])
        ut = km.tx_utxos([t[0] for t in txs])
        done_h, read = after, 0
        for hsh, hgt, bt in txs:
            if hsh not in ut:
                if desc:
                    continue
                break  # budget ran out: resume from here next run
            cache["legs"].extend({**l, "owner": otag(l["owner"])}  # cache holds tags only (S4)
                                 for l in legs_from_tx(u, hsh, bt, ut[hsh], lp_pols, excluded, meta.get(u), ex_tags))
            done_h = max(done_h, hgt)
            stats["txs"] += 1
            read += 1
        unread = sum(1 for t in txs if t[0] not in ut)
        cov = {"read": read, "complete": complete and not unread, "pages": pages}
        if not cov["complete"]:
            stats["capped"].append(tk)
            now_capped.append(u)
            if desc and txs:  # the block range below the oldest tx read was not reached this run: say how big it is
                cov["gap_blocks"] = max(0, txs[0][1] - after - 1)
            cov["unread_txs"] = unread
            # asc: partial blocks are re-read next run (deduped). desc: newest pages read; the gap is reported
            done_h = max(after, done_h - 1)
        coverage[tk] = cov
        cache["height"][u] = done_h
    cache["capped"] = now_capped
    seen, legs = set(), []
    for l in cache["legs"]:
        k = (l["owner"], l["unit"], l["tx"])
        if k not in seen and now_s - l["time"] <= bc["window_h"] * 3600:
            seen.add(k)
            legs.append(l)
    cache["legs"] = legs
    rows = flag_moves(legs, meta, now_s, bc)
    path = os.path.join(data_dir, "big_moves.json")
    prev = KT.read_json(path, {}).get("moves", [])
    prev_keys = {(r.get("tag"), r.get("unit_short"), r.get("tx_short"), r.get("direction")) for r in prev}
    moves = merge(prev, rows, now_s, bc["keep_h"])
    doc = F.assert_public({
        "at": iso(now_s), "phase": 1, "window_h": bc["window_h"], "keep_h": bc["keep_h"],
        "thresholds": {"min_ada": bc["min_ada"], "min_pool_pct": bc["min_pool_pct"]},
        "note": "Research data for Snek. next_hop is phase 2 (null). ada = the mover's net ADA when it is the only token "
                "against ADA in the tx (ada_basis tx), else qty x price (ada_basis price). LP = liquidity token.",
        "moves": moves, "units_scanned": stats["units"], "txs_read": stats["txs"], "capped": stats["capped"], "coverage": coverage,
        "koios_calls": http.calls.get("koios", 0) - km.start,
    })
    if not dry_run:
        KT.write_json(cpath, cache)
        KT.write_json(path, doc, indent=1)
    new = sum(1 for r in rows if (r["tag"], r["unit_short"], r["tx_short"], r["direction"]) not in prev_keys)
    return {k: doc[k] for k in ("units_scanned", "txs_read", "capped", "koios_calls")} | {"moves_new": new}, doc
