"""Flag-only whale/cabal signals for the Solada pre-filter (zero AI).

FLAGS ARE NOT TRIGGERS. Nothing here feeds rules k/y/aa or kill lines, and no
flag fires the webhook on its own. prefilter.py writes them to
desk-data:solada-prefilter/flags.json for Snek's daily run and, when a real
threshold trigger is already firing, attaches the run's new flags to that payload.

Privacy: wallets leave this module only as w-xxxxxxxx tags (sha256 of the
stake key) or as Snek's existing stake-prefix tags from config. Full stake keys,
addresses and per-wallet balances stay in the private Actions cache
(flags-cache.json). assert_public() refuses to write anything that looks like a
full address or stake key.
"""
import hashlib, json, os, re, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import valuation as V  # noqa: E402
import koios_tx as KT  # noqa: E402

KOIOS = "https://api.koios.rest/api/v1"
BF = "https://cardano-mainnet.blockfrost.io/api/v0"

# ---------------------------------------------------------------- bech32 (local stake derivation, no API calls)
_CS = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _polymod(values):
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _hrp_expand(h):
    return [ord(x) >> 5 for x in h] + [0] + [ord(x) & 31 for x in h]


def _convert(data, frm, to, pad):
    acc = bits = 0
    out, maxv = [], (1 << to) - 1
    for v in data:
        acc = (acc << frm) | v
        bits += frm
        while bits >= to:
            bits -= to
            out.append((acc >> bits) & maxv)
    if pad and bits:
        out.append((acc << (to - bits)) & maxv)
    return out


def b32_decode(s):
    s = s.lower()
    pos = s.rfind("1")
    if pos < 1:
        return None, None
    hrp, data = s[:pos], s[pos + 1:]
    try:
        d = [_CS.index(c) for c in data]
    except ValueError:
        return None, None
    if _polymod(_hrp_expand(hrp) + d) != 1:
        return None, None
    return hrp, bytes(_convert(d[:-6], 5, 8, False))


def b32_encode(hrp, raw):
    d = _convert(list(raw), 8, 5, True)
    pm = _polymod(_hrp_expand(hrp) + d + [0] * 6) ^ 1
    return hrp + "1" + "".join(_CS[c] for c in d + [(pm >> 5 * (5 - i)) & 31 for i in range(6)])


def addr_info(addr):
    """(stake_address or None, payment_is_script). Shelley base addresses only carry a stake part."""
    hrp, raw = b32_decode(addr or "")
    if not raw or hrp not in ("addr", "addr_test"):
        return None, False
    typ, net = raw[0] >> 4, raw[0] & 0x0F
    script_pay = typ in (1, 3, 5, 7)
    if typ in (0, 1, 2, 3) and len(raw) >= 57:
        hdr = (0xF0 if typ in (2, 3) else 0xE0) | net
        return b32_encode("stake" if hrp == "addr" else "stake_test", bytes([hdr]) + raw[29:57]), script_pay
    return None, script_pay


def tag(s):
    return "w-" + hashlib.sha256(s.encode()).hexdigest()[:8]


FULL_ID_RE = re.compile(r"\b(?:stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,})")


def assert_public(obj):
    s = json.dumps(obj)
    if FULL_ID_RE.search(s):
        raise SystemExit("flags: refusing to write a full address/stake key to public output")
    return obj


# ---------------------------------------------------------------- data fetch (cheap, budgeted)
class FlagChain:
    def __init__(self, http, bf_key):
        self.h, self.bf_key = http, bf_key

    def asset_txs(self, unit, n, newest=False):
        """[(tx_hash, block_height, block_time)] first (or last) n txs for the unit. 1 call."""
        if self.bf_key:
            r = self.h.req("blockfrost", f"{BF}/assets/{unit}/transactions?order={'desc' if newest else 'asc'}&count={min(n, 100)}",
                           headers={"project_id": self.bf_key})
            if r is not None:
                return [(t["tx_hash"], t["block_height"], t.get("block_time")) for t in r]
        r = self.h.req("koios", f"{KOIOS}/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}&_history=true")
        if r is None:
            return None
        t = sorted(((x["tx_hash"], x["block_height"], x.get("block_time")) for x in r), key=lambda x: x[1], reverse=newest)
        return t[:n]

    def tx_utxos(self, hashes):
        """{tx_hash: {'inputs': [(addr, lovelace, {unit: qty})], 'outputs': [...]}}. Koios /tx_info (re-review 8: not the
        deprecated /tx_utxos; spend inputs only, collateral/reference excluded; 404s logged), batches under 1 KB.
        Blockfrost per-tx fallback skips collateral / reference inputs and collateral outputs too."""
        out = {}
        for body in KT.batches(hashes, "_tx_hashes", 25, KT.TX_INFO_FLAGS):
            chunk = body["_tx_hashes"]
            got = KT.tx_info(self.h, chunk, len(chunk))
            if not got and self.bf_key:  # per-tx Blockfrost fallback
                for hsh in chunk:
                    x = self.h.req("blockfrost", f"{BF}/txs/{hsh}/utxos", headers={"project_id": self.bf_key}, quiet404=False)
                    if x:
                        out[hsh] = {s: [(u["address"], int(next((a["quantity"] for a in u["amount"] if a["unit"] == "lovelace"), 0)),
                                         {a["unit"]: int(a["quantity"]) for a in u["amount"] if a["unit"] != "lovelace"})
                                        for u in x.get(s, []) if not u.get("collateral") and not u.get("reference")]
                                    for s in ("inputs", "outputs")}
                continue
            for h, tx in got.items():
                out[h] = {s: [(x["addr"], x["lovelace"], x["assets"]) for x in tx[s]] for s in ("inputs", "outputs")}
        return out

    def first_tx_height(self, stake):
        """Block height of the stake key's first tx (wallet age). 1 Koios call, cached forever by caller."""
        r = self.h.req("koios", f"{KOIOS}/account_txs?_stake_address={stake}&order=block_height.asc&limit=1")
        if r is None:
            return None
        return r[0]["block_height"] if r else 0

    def holdings(self, stakes, units):
        """{stake: {unit: qty}} for the given units. 1 Koios call per 25 stakes."""
        out = {}
        for i in range(0, len(stakes), 25):
            r = self.h.req("koios", f"{KOIOS}/account_assets", method="POST", body={"_stake_addresses": stakes[i:i + 25]})
            if r is None:
                return None
            for s in stakes[i:i + 25]:
                out[s] = {u: 0 for u in units}
            for a in r:
                u = a["policy_id"] + a["asset_name"]
                if u in units:
                    out[a["stake_address"]][u] = out[a["stake_address"]].get(u, 0) + int(a["quantity"])
        return out


# ---------------------------------------------------------------- pure analysis (fixture-testable)
def buyers_from_txs(unit, txs, utxos, decimals=None, price_ada=None, lp_pols=None):
    """Per tx, net token/ADA change per stake key. Script (pool/order) payment parts ignored.
    Buyer = token in, and either ADA out (the tx's lovelace leg is the size) or, for aggregator/batcher
    fills where the ADA left in an earlier order tx, sized by raw/10**decimals * pool price.
    LP tokens are never buys, and a wallet that receives LP tokens in the tx is adding liquidity, not buying.
    Returns [{'stake','addrs','tx','height','time','qty','ada','ada_basis'}] in chain order ('ada' may be None
    when it cannot be sized safely; raw units are never treated as ADA)."""
    pols = V.lp_policies() if lp_pols is None else lp_pols
    rows = []
    if V.lp_dex(unit, pols):
        return rows
    for hsh, height, btime in txs:
        u = utxos.get(hsh)
        if not u:
            continue
        net = {}
        for side, sign in (("inputs", -1), ("outputs", 1)):
            for addr, lov, assets in u[side]:
                stake, script = addr_info(addr)
                if script or not stake:
                    continue
                n = net.setdefault(stake, {"qty": 0, "lov": 0, "lp": 0, "addrs": set()})
                n["qty"] += sign * assets.get(unit, 0)
                n["lov"] += sign * lov
                n["lp"] += sign * sum(q for a, q in assets.items() if V.lp_dex(a, pols))
                n["addrs"].add(addr)
        for stake, n in net.items():
            if n["qty"] <= 0 or n["lp"] > 0:
                continue
            if n["lov"] < 0:
                ada, basis = round(-n["lov"] / 1e6, 2), "ada_leg"
            else:
                ada, basis = V.value_ada(n["qty"], None, decimals, price_ada)
                if ada is None:
                    continue  # token in with ADA in too and no safe price: not a sized buy
            rows.append({"stake": stake, "addrs": n["addrs"], "tx": hsh, "height": height, "time": btime,
                         "qty": n["qty"], "ada": ada, "ada_basis": basis})
    return rows


def excluded_tags(cfg):
    """Tags excluded from holder/cluster flags (config flags.exclude_tags, e.g. Solada's curve reserve). Matched on
    both the stake tag and the address tag, so a reserve without a stake key is covered too."""
    return {str(t).strip() for t in ((cfg or {}).get("exclude_tags") or {}) if str(t).strip()}


def match_tags(stake, watch):
    """Snek prefix tags that this stake key starts with. watch = {prefix: role}.

    Non-string keys are skipped. `"stake1" + p` would raise TypeError and the
    dev-checks run would be dropped for the whole payload.
    """
    if not isinstance(stake, str) or not stake or not watch:
        return []
    hits = []
    for p in watch:
        if not isinstance(p, str) or not p:
            continue
        if stake.startswith("stake1" + p):
            hits.append(p)
    return hits


def analyze_launch(ticker, unit, buyers, watch, cfg, live_at_s=None, first_heights=None, launcher_stake=None):
    """Return list of flags for one launch from its early buyers. No I/O."""
    f, first_heights = [], first_heights or {}
    ex = excluded_tags(cfg)
    if ex:  # a configured protocol wallet (curve reserve) is not a buyer cluster
        buyers = [b for b in buyers if tag(b["stake"]) not in ex and not any(tag(a) in ex for a in b.get("addrs") or ())]
    early = buyers[: cfg["first_x_buyers"]]
    stakes = []
    for b in early:
        if b["stake"] not in stakes:
            stakes.append(b["stake"])
    first_h = min((b["height"] for b in buyers), default=None)

    def fl(kind, **kw):
        f.append({"kind": kind, "ticker": ticker, **kw})

    # (a) watch tags in first block / early buyers
    fb = {b["stake"] for b in buyers if b["height"] == first_h}
    fb_tags = sorted({p for s in fb for p in match_tags(s, watch)})
    if len(fb_tags) >= cfg["first_block_watch_tags_min"]:
        fl("watch_tags_first_block", tags=fb_tags, roles=sorted({watch[p] for p in fb_tags}))
    early_tags = sorted({p for s in stakes for p in match_tags(s, watch)} - set(fb_tags))
    if early_tags:
        fl("watch_tags_early_buyers", tags=early_tags, roles=sorted({watch[p] for p in early_tags}))
    pre = [p for p in fb_tags + early_tags if watch[p] == "pool_preseeder"]
    if pre:
        fl("pool_preseeder_early", tags=pre)
    # (g) known launcher deploying
    if launcher_stake:
        lt = [p for p in match_tags(launcher_stake, watch) if watch[p] in cfg["launcher_roles"]]
        if lt:
            fl("known_launcher_deploy", tags=lt, roles=sorted({watch[p] for p in lt}))
    # (1) one stake key buying through several addresses
    multi = [b for b in early if len(b["addrs"]) >= cfg["stake_cluster_min_addrs"]]
    per_stake = {}
    for b in early:
        per_stake.setdefault(b["stake"], set()).update(b["addrs"])
    multi = sorted(tag(s) for s, a in per_stake.items() if len(a) >= cfg["stake_cluster_min_addrs"])
    if multi:
        fl("stake_key_multi_address", wallets=multi)
    # (3) coordinated timing right after launch
    times = sorted((b["time"], b["stake"]) for b in early if b.get("time"))
    w = cfg["coordinated_window_s"]
    best = []
    for i, (t0, _) in enumerate(times):
        grp = {s for t, s in times[i:] if t - t0 <= w}
        if len(grp) > len(best):
            best = grp
    if len(best) >= cfg["coordinated_min_wallets"] and (live_at_s is None or times[0][0] - live_at_s <= cfg["coordinated_after_launch_s"]):
        fl("coordinated_buys", wallets=sorted(tag(s) for s in best), window_s=w)
    # (b) identical-size buys from 3+ wallets within 10 min
    tol, win = cfg["same_size_tolerance_pct"] / 100, cfg["same_size_window_s"]
    seen_groups = set()
    for i, a in enumerate(early):
        grp = {a["stake"]}
        for b in early[i + 1:]:
            if not (a.get("time") and b.get("time")) or abs(b["time"] - a["time"]) > win:
                continue
            # same size = same ADA value (lovelace leg or decimals x price, never raw units read as ADA),
            # or the same token amount within a relative tolerance (scale-free: same unit, same decimals)
            same_ada = a.get("ada") is not None and b.get("ada") is not None and abs(b["ada"] - a["ada"]) <= max(tol * a["ada"], 0.5)
            if same_ada or abs(b["qty"] - a["qty"]) <= tol * a["qty"]:
                grp.add(b["stake"])
        if len(grp) >= cfg["same_size_min_wallets"]:
            key = tuple(sorted(tag(s) for s in grp))
            if key not in seen_groups and not any(set(key) <= set(k) for k in seen_groups):
                seen_groups.add(key)
                fl("identical_size_buys", wallets=list(key), ada_each=a["ada"], window_s=win)
    # (6) fresh-wallet swarm
    if first_heights and first_h is not None:
        fresh = sorted(tag(s) for s in stakes if first_heights.get(s) is not None
                       and first_h - first_heights[s] <= cfg["fresh_wallet_max_age_blocks"])
        if len(fresh) >= cfg["fresh_swarm_min_wallets"]:
            fl("fresh_wallet_swarm", wallets=fresh, max_age_blocks=cfg["fresh_wallet_max_age_blocks"])
    return f


def holder_concentration(dist, cfg):
    """dist = [(addr, qty)]. Top holder and top stake-cluster share, script (pool) addresses excluded, and so are
    holders whose stake tag or address tag is in flags.exclude_tags (e.g. the Solada curve reserve w-f46e5a31):
    they are left out of the total too, so the shares are of the circulating (non-reserve) supply."""
    by_stake, tot = {}, 0
    top_addr = 0
    ex = excluded_tags(cfg)
    for addr, q in dist:
        stake, script = addr_info(addr)
        if script or q <= 0:
            continue
        if ex and (tag(addr) in ex or (stake and tag(stake) in ex)):
            continue
        tot += q
        top_addr = max(top_addr, q)
        by_stake[stake or addr] = by_stake.get(stake or addr, 0) + q
    if not tot:
        return None
    top = max(by_stake.items(), key=lambda x: x[1])
    return {"top_addr_pct": round(100 * top_addr / tot, 1), "top_cluster_pct": round(100 * top[1] / tot, 1),
            "top_cluster": tag(top[0]), "basis": conc_basis(ex)}


def conc_basis(ex):
    """Identifies the exclusion set a concentration share was computed on, so a jump is only measured
    between snapshots on the same basis (adding a reserve to exclude_tags must not read as a jump)."""
    return "all" if not ex else "ex-" + hashlib.sha256(",".join(sorted(ex)).encode()).hexdigest()[:6]


def _same_metric_basis(prev, cur, key):
    """True when both sides are untagged, or both carry the same basis tag.

    A tagged reading is not compared with an untagged or differently tagged one.
    """
    b0, b1 = prev.get(key), cur.get(key)
    if not b0 and not b1:
        return True
    return bool(b0) and b0 == b1


def metric_flags(ticker, cur, prev, cfg):
    """Flags from public per-name metric history (ada_side, holders, mcap_ada, vol_h24_ada, conc). No I/O.

    ADA side and mcap are compared across runs only when both readings share a basis.
    """
    f = []
    if not prev:
        return f
    a0, a1 = prev.get("ada_side"), cur.get("ada_side")
    h0, h1 = prev.get("holders"), cur.get("holders")
    m0, m1 = prev.get("mcap_ada"), cur.get("mcap_ada")
    ada_ok = _same_metric_basis(prev, cur, "ada_basis")
    mcap_ok = _same_metric_basis(prev, cur, "mcap_basis")
    if ada_ok and a0 and a1 is not None and 100 * (a0 - a1) / a0 >= cfg["ada_side_drop_pct"]:
        f.append({"kind": "ada_side_drop", "ticker": ticker, "from": a0, "to": a1})
    if mcap_ok and h0 and h1 is not None and m0 and m1 and h1 < h0 and m1 > m0:
        f.append({"kind": "holders_down_mcap_up", "ticker": ticker, "holders": [h0, h1], "mcap_ada": [m0, m1]})
    if ada_ok and h0 and h1 and a0 and a1 and h1 >= cfg["holder_doubling_ratio"] * h0 and a1 > a0:
        f.append({"kind": "holders_doubled_ada_up", "ticker": ticker, "positive": True, "holders": [h0, h1], "ada_side": [a0, a1]})
    v = cur.get("vol_h24_ada")
    if v and a1 and v / a1 >= cfg["wash_vol_to_ada_side"] and h0 is not None and h1 is not None and h1 <= h0:
        f.append({"kind": "volume_without_holders", "ticker": ticker, "vol_h24_ada": v, "ada_side": a1, "holders": [h0, h1]})
    c0, c1 = (prev.get("conc") or {}), (cur.get("conc") or {})
    if c1.get("top_cluster_pct", 0) >= cfg["top_cluster_pct"]:
        f.append({"kind": "holder_concentration", "ticker": ticker, **c1})
    same_basis = c0.get("basis", "all") == c1.get("basis", "all")
    if c0 and c1 and same_basis and c1["top_cluster_pct"] - c0.get("top_cluster_pct", 0) >= cfg["concentration_jump_pts"]:
        f.append({"kind": "concentration_jump", "ticker": ticker, "from": c0.get("top_cluster_pct"), "to": c1["top_cluster_pct"]})
    return f


def whale_flags(whale_summary, cfg):
    """Founder-whale moves as flags, COCK/SOLADA team-support activity dropped as noise."""
    out = []
    for mv in (whale_summary or {}).get("moves") or []:
        nm = str(mv.get("asset", "")).upper()
        if any(n in nm for n in cfg["whale_noise_tickers"]):
            continue
        out.append({"kind": "founder_whale_move", "ticker": nm, "tag": whale_summary.get("tag"), **{k: mv[k] for k in mv if k != "asset"}})
    return out


def repeat_buyers(appear, cfg):
    """appear = {stake: [tickers]} (private cache). Wallets in the first-X of N+ launches."""
    return sorted((tag(s), sorted(set(t))[-10:]) for s, t in appear.items() if len(set(t)) >= cfg["repeat_min_launches"])


# ---------------------------------------------------------------- orchestration
def watch_map(cfg, log_keys):
    w = {}
    for role, tags in (cfg.get("snek_prefix_tags") or {}).items():
        if role.startswith("_"):
            continue
        for t in tags:
            w[t] = role
    for k in log_keys:
        w.setdefault(k, "log_whale_watch")
    return w


def _key(fl):
    body = {k: v for k, v in fl.items() if k not in ("from", "to", "holders", "mcap_ada", "ada_side", "vol_h24_ada", "ada_each", "delta", "pct", "ada_equiv")}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:12]


def run_flags(http, bf_key, cfg, launches, new_ids, rows, dists, watch, whale_summary, state, cache_dir, baseline, dry_run, now_iso,
              unit_value=None, lp_pols=None):
    """Compute all flags for this run. Returns (new_flags, flags_doc). Never touches triggers."""
    fc = cfg["flags"]
    calls0 = dict(http.calls)
    chain = FlagChain(http, bf_key)
    cache_path = os.path.join(cache_dir, "flags-cache.json")
    cache = json.load(open(cache_path)) if os.path.exists(cache_path) else {}
    cache.setdefault("first_height", {})
    cache.setdefault("appear", {})
    cache.setdefault("snipers", {})
    cache.setdefault("scanned", [])
    flags = []
    by_id = {l["id"]: l for l in launches}
    unit_of = lambda l: (l.get("policyId") or "") + (l.get("assetName") or "")

    # ---- early-buyer scan of new launches (2 calls each + capped wallet-age lookups)
    todo = [i for i in new_ids if i not in cache["scanned"] and unit_of(by_id[i])][: fc["max_launch_scans"]]
    if baseline:
        todo = []
    age_budget = fc["max_wallet_age_lookups"]
    for lid in todo:
        l = by_id[lid]
        u = unit_of(l)
        txs = chain.asset_txs(u, fc["scan_txs"])
        if txs is None:
            continue
        cache["scanned"].append(lid)
        ut = chain.tx_utxos([t[0] for t in txs])
        uv = (unit_value or {}).get(u) or {}
        buyers = buyers_from_txs(u, txs, ut, uv.get("decimals", 6 if l.get("curve") else None), uv.get("price_ada"), lp_pols)
        early = []
        for b in buyers:
            if b["stake"] not in early:
                early.append(b["stake"])
        early = early[: fc["first_x_buyers"]]
        for s in early:
            if s not in cache["first_height"] and age_budget > 0:
                age_budget -= 1
                h = chain.first_tx_height(s)
                if h is not None:
                    cache["first_height"][s] = h
            cache["appear"].setdefault(s, [])
            if l["ticker"] not in cache["appear"][s]:
                cache["appear"][s] = (cache["appear"][s] + [l["ticker"]])[-20:]
        first_h = min((b["height"] for b in buyers), default=None)
        for b in buyers:
            if b["height"] == first_h:
                cache["snipers"].setdefault(b["stake"], {})[u] = {"ticker": l["ticker"], "qty": b["qty"]}
        launcher = addr_info(l.get("launcherAddress") or "")[0]
        live_s = (l.get("liveAtMs") or 0) / 1000 or None
        flags += analyze_launch(l["ticker"], u, buyers, watch, fc, live_s, cache["first_height"], launcher)
        if l.get("pool") and not l.get("curve"):
            flags.append({"kind": "user_created_pool", "ticker": l["ticker"], "note": "pool without Solada curve (SER-style)"})
    # known launcher check is free for every new launch, even ones not scanned
    for lid in ([] if baseline else new_ids):
        if lid in todo:
            continue
        l = by_id[lid]
        launcher = addr_info(l.get("launcherAddress") or "")[0]
        lt = [p for p in match_tags(launcher, watch) if watch[p] in fc["launcher_roles"]] if launcher else []
        if lt:
            flags.append({"kind": "known_launcher_deploy", "ticker": l["ticker"], "tags": lt, "roles": sorted({watch[p] for p in lt})})
    cache["scanned"] = cache["scanned"][-500:]

    # ---- repeat early buyers across launches
    reps = repeat_buyers(cache["appear"], fc)
    for t, tickers in reps:
        flags.append({"kind": "repeat_early_buyer", "wallet": t, "launches": tickers})

    # ---- King-of-the-Hill / graduation pile-in (latest txs of near-graduation names)
    hot = [l for l in launches if (l.get("curve") or {}).get("progressBps") and
           int(l["curve"]["progressBps"]) >= fc["koth_progress_bps"] and not l["curve"].get("graduated")]
    for l in sorted(hot, key=lambda x: -int(x["curve"]["progressBps"]))[: fc["max_koth_scans"]]:
        if baseline:
            break
        u = unit_of(l)
        txs = chain.asset_txs(u, fc["koth_txs"], newest=True)
        if not txs:
            continue
        uv = (unit_value or {}).get(u) or {}
        buyers = buyers_from_txs(u, txs, chain.tx_utxos([t[0] for t in txs]), uv.get("decimals", 6), uv.get("price_ada"), lp_pols)
        tg = sorted({p for b in buyers for p in match_tags(b["stake"], watch)})
        fresh = sorted({tag(b["stake"]) for b in buyers if b["stake"] in cache["first_height"]
                        and b["height"] - cache["first_height"][b["stake"]] <= fc["fresh_wallet_max_age_blocks"]})
        if tg or len(fresh) >= fc["fresh_swarm_min_wallets"]:
            flags.append({"kind": "koth_pile_in", "ticker": l["ticker"], "curve_pct": int(l["curve"]["progressBps"]) / 100,
                          "tags": tg, "fresh_wallets": fresh})

    # ---- first-block sniper full exits (1 call per 25 tracked snipers)
    sn = cache["snipers"]
    if len(sn) > fc["max_tracked_snipers"]:
        for s in list(sn)[: len(sn) - fc["max_tracked_snipers"]]:
            sn.pop(s)
    if sn and not baseline:
        units = {u for v in sn.values() for u in v}
        hold = chain.holdings(list(sn), units)
        for s, pos in (hold or {}).items():
            for u, info in list(sn[s].items()):
                if pos.get(u, 0) == 0:
                    tags = match_tags(s, watch)
                    flags.append({"kind": "sniper_full_exit", "ticker": info["ticker"], "wallet": tag(s), "tags": tags})
                    sn[s].pop(u)
            if not sn[s]:
                sn.pop(s)

    # ---- metric-history flags (zero extra calls)
    hist = state.setdefault("flag_metrics", {})
    for r in rows:
        tk = r["ticker"]
        cur = {k: r.get(k) for k in ("ada_side", "holders", "mcap_ada", "vol_h24_ada", "ada_basis", "mcap_basis")}
        u = r.get("unit")
        if u and u in dists:
            cur["conc"] = holder_concentration(dists[u], fc)
        flags += metric_flags(tk, cur, hist.get(tk), fc)
        hist[tk] = {**{k: v for k, v in cur.items() if v is not None}, "at": now_iso()}
    if len(hist) > 300:
        for k in sorted(hist, key=lambda k: hist[k].get("at", ""))[: len(hist) - 300]:
            hist.pop(k)

    flags += whale_flags(whale_summary, fc)

    # ---- dedupe, publish
    seen = state.setdefault("flags_seen", {})
    new = []
    for fl in flags:
        k = _key(fl)
        if k not in seen:
            seen[k] = now_iso()
            new.append({**fl, "at": now_iso()})
    if len(seen) > 3000:
        state["flags_seen"] = dict(sorted(seen.items(), key=lambda x: x[1])[-3000:])
    calls = {k: http.calls.get(k, 0) - calls0.get(k, 0) for k in http.calls if http.calls.get(k, 0) - calls0.get(k, 0)}
    if not dry_run:
        os.makedirs(cache_dir, exist_ok=True)
        json.dump(cache, open(cache_path, "w"))
    return new, calls
