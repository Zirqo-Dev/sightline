"""Logging-only records for the v2 pre-registration gaps.

Nothing here is an input to skips, book selection, exits, sizing, cost, or the
scan-timing line. Callers already hold the DexScreener page and the per-lead
pair and RugCheck report. A follow-up pair read, when a pool has left the lead
page, is capped and counted. Any error is counted and swallowed.

Files (under data_dir/v2/, the desk-data solana-desk folder):
  manifest-YYYYMMDD.jsonl     one scan header plus one line per candidate mint
  pool-snaps-YYYYMMDD.jsonl   one reserve row per pool per observation, uncapped
  death-YYYYMMDD.jsonl        raw observations and a recomputable death label
  seen.json                   mint index (first-seen, k series). Not a decision input.

Each JSONL rolls on the UTC day and again at 5 MB. Typical growth is about
1–2 MB/day. If the same pools are snapshotted every 30 minutes for two weeks,
the manifest can reach about 5–12 MB/day because each row copies its k series;
the 5 MB roll keeps a single file at that size.

S2 rules are spec section 4. They are recorded. This module does not enter,
does not draw a twin sample, and does not read or write config.py.
"""
import json, os, re, sys, time
from . import config as C, observe, skips

MANIFEST = "manifest"
SNAPS = "pool-snaps"
DEATH = "death"
SEEN_NAME = "seen.json"
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_EXTRA_CALLS = 24
EXTRA_CALL_HARD_MAX = 100
DEFAULT_EXTRA_GAP_S = 0.3
DEATH_WINDOW_S = 2 * 3600
H1_S = 3600
PRICE_DROP = 0.80

# Spec section 4. Not the frozen v6.5 skip thresholds.
S2_MIN_AGE_S = 6 * 3600
S2_MIN_SOL = 50.0
S2_MIN_TRADES = 1
S2_MIN_SELLS = 1
S2_MAX_TOP10 = 30.0          # pass only when top10_ex_pool is strictly under 30
S2_MAX_DEV = 10.0            # pass when dev pct is at most 10
S2_MAX_K_DROP = 0.50         # a fall of this fraction or more fails k_stable
RULE_IDS = ("graduated_age", "sol_reserve", "activity_h1", "holders", "k_stable", "authorities")
V = 1
_B58 = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")
_BANNED_KEYS = {
    "wallet", "owner", "address", "creator", "funder", "funded", "stake",
    "mintAuthority", "freezeAuthority", "launcherAddress", "secret", "api_key", "apiKey",
    "private_key", "privateKey",
}
_PRIVACY_RE = re.compile(r"stake1[0-9a-z]{20,}|addr1[q-z][0-9a-z]{20,}", re.I)
_HOLDER_CAP = 100


def enabled(flag=None):
    """Default on. SOLANA_DESK_V2_LOG=0/false/off/no switches the records off."""
    if flag is False:
        return False
    if flag is True:
        return True
    raw = os.environ.get("SOLANA_DESK_V2_LOG", "1").strip().lower()
    return raw not in {"0", "false", "off", "no", ""}


def extra_cap():
    raw = os.environ.get("SOLANA_DESK_V2_EXTRA_CALLS", "").strip()
    if not raw:
        return DEFAULT_EXTRA_CALLS
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_EXTRA_CALLS
    if n < 0:
        return 0
    return min(n, EXTRA_CALL_HARD_MAX)


def extra_gap():
    raw = os.environ.get("SOLANA_DESK_V2_EXTRA_GAP_S", "").strip()
    if not raw:
        return DEFAULT_EXTRA_GAP_S
    try:
        n = float(raw)
    except ValueError:
        return DEFAULT_EXTRA_GAP_S
    if n < 0:
        return 0.0
    return min(n, 5.0)


def _num(v):
    """A finite number. Integers stay integers so a fetched count is not rewritten as a float."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v
    return None


def _iso(ts):
    try:
        return observe.iso(float(ts))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _t_s(ts):
    n = _num(ts)
    if n is None:
        return None
    try:
        return int(n)
    except (TypeError, ValueError, OverflowError):
        return None


def public_id(value):
    """A Solana address we are willing to publish: mint or pool, base58, 32–44 chars."""
    if not isinstance(value, str) or not 32 <= len(value) <= 44:
        return None
    if any(c not in _B58 for c in value):
        return None
    return value


def _swaps(h1):
    """buys + sells when both were present as numbers. None when the input is incomplete."""
    if not isinstance(h1, dict) or "buys" not in h1 or "sells" not in h1:
        return None
    buys, sells = _num(h1.get("buys")), _num(h1.get("sells"))
    if buys is None or sells is None:
        return None
    return buys + sells


def reserve_price(sol, token):
    """Reserve-implied token price in SOL: sol reserve / token reserve."""
    sol, token = _num(sol), _num(token)
    if sol is None or token is None or token <= 0 or sol < 0:
        return None
    return sol / token


def txns_h1(pair):
    """Numeric txns.h1 fields as fetched. Nested values and non-numbers are dropped."""
    if not isinstance(pair, dict):
        return None
    tx = pair.get("txns")
    if not isinstance(tx, dict):
        return None
    h1 = tx.get("h1")
    if not isinstance(h1, dict):
        return None
    out = {}
    for key, val in h1.items():
        if not isinstance(key, str) or not key or len(key) > 16:
            continue
        if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in key):
            continue
        out[key] = _num(val)
    return out


def holder_view(rc):
    """Holder percents with exclusion and creator flags. No wallet, owner, or creator address.

    top10 and dev match skips.top10_ex_pool and the dev sum in safety_check, including
    a missing pct counting as 0. Returns None when the RugCheck shape cannot be read.
    """
    if not isinstance(rc, dict):
        return None
    try:
        excluded = skips.excluded_accounts(rc)
    except Exception:
        return None
    creator = rc.get("creator")
    rows = []
    holders = rc.get("topHolders") or []
    if not isinstance(holders, list):
        return None
    truncated = len(holders) > _HOLDER_CAP
    for h in holders[:_HOLDER_CAP]:
        if not isinstance(h, dict):
            continue
        try:
            owner = h.get("owner")
            addr = h.get("address")
            is_ex = owner in excluded or addr in excluded
        except Exception:
            return None
        pct = h.get("pct", 0)
        pct_n = _num(pct)
        if pct_n is None:
            pct_n = 0.0
        rows.append({
            "pct": pct_n,
            "excluded": bool(is_ex),
            "is_creator": owner == creator,
        })
    return {"rows": rows, "truncated": truncated}


def top10_from_rows(rows):
    kept = []
    for row in rows or []:
        if not isinstance(row, dict) or row.get("excluded"):
            continue
        pct = _num(row.get("pct"))
        kept.append(0.0 if pct is None else pct)
    return sum(kept[:10])


def dev_from_rows(rows):
    total = 0.0
    for row in rows or []:
        if isinstance(row, dict) and row.get("is_creator"):
            pct = _num(row.get("pct"))
            total += 0.0 if pct is None else pct
    return total


def authority_status(value):
    """revoked when the RugCheck field is missing or falsy. set when it is truthy. The value is not returned."""
    return "set" if value else "revoked"


def k_dropped(series):
    """True when any consecutive numeric k is at least 50% below the earlier k.

    A missing k breaks the chain. It is not treated as a fall. The points stay in the series.
    """
    prev = None
    for point in series or []:
        if not isinstance(point, dict):
            prev = None
            continue
        k = _num(point.get("k"))
        if k is None:
            prev = None
            continue
        if prev is not None and prev > 0 and (prev - k) / prev >= S2_MAX_K_DROP - 1e-12:
            return True
        prev = k
    return False


def rules_from_logged(inputs, k_series, looked_up):
    """S2 pass/fail from manifest fields only. No pair and no RugCheck document."""
    unknown = {key: "unknown" for key in RULE_IDS}
    if not looked_up or not isinstance(inputs, dict):
        return unknown
    out = {}
    grad = inputs.get("graduated_pump")
    age = _num(inputs.get("age_s"))
    if grad is None or age is None:
        out["graduated_age"] = "unknown"
    else:
        out["graduated_age"] = "pass" if grad is True and age >= S2_MIN_AGE_S else "fail"
    sol = _num(inputs.get("sol"))
    out["sol_reserve"] = "unknown" if sol is None else ("pass" if sol >= S2_MIN_SOL else "fail")
    swaps = _swaps(inputs.get("txns_h1"))
    if swaps is None:
        out["activity_h1"] = "unknown"
    else:
        sells = _num(inputs["txns_h1"].get("sells"))
        out["activity_h1"] = "pass" if swaps >= S2_MIN_TRADES and sells is not None and sells >= S2_MIN_SELLS else "fail"
    top = _num(inputs.get("top10_ex_pool"))
    dev = _num(inputs.get("dev_pct"))
    if top is None or dev is None:
        out["holders"] = "unknown"
    else:
        out["holders"] = "pass" if top < S2_MAX_TOP10 - 1e-12 and dev <= S2_MAX_DEV + 1e-12 else "fail"
    out["k_stable"] = "fail" if k_dropped(k_series) else "pass"
    mint_a = inputs.get("mint_authority")
    freeze_a = inputs.get("freeze_authority")
    if mint_a not in ("revoked", "set") or freeze_a not in ("revoked", "set"):
        out["authorities"] = "unknown"
    else:
        out["authorities"] = "pass" if mint_a == "revoked" and freeze_a == "revoked" else "fail"
    return out


def passes_other_s2(inputs, k_series):
    """True when every S2 rule except mint and freeze authority passes.

    A single k point has not fallen, so the k rule passes. The authority rule is
    not part of this check. G8 uses it and does not change the rule.
    """
    rules = rules_from_logged(inputs, k_series, True)
    others = tuple(key for key in RULE_IDS if key != "authorities")
    return all(rules.get(key) == "pass" for key in others), rules


def s2_pick(rules, pool_lookup):
    return pool_lookup == "ok" and isinstance(rules, dict) and all(rules.get(key) == "pass" for key in RULE_IDS)


def twin_candidate(inputs, rules, pool_lookup):
    """Spec section 5: graduated PumpSwap, age at least 6h, at least one h1 trade, and not an S2 pick."""
    if pool_lookup != "ok" or not isinstance(inputs, dict):
        return False
    if s2_pick(rules, pool_lookup):
        return False
    if inputs.get("graduated_pump") is not True:
        return False
    age = _num(inputs.get("age_s"))
    if age is None or age < S2_MIN_AGE_S:
        return False
    swaps = _swaps(inputs.get("txns_h1"))
    return swaps is not None and swaps >= 1


def death_label(obs):
    """Spec section 4 death label, from raw observations only.

    Dead when reserve-implied price is at least 80% below the t0 price at any
    observation with t in [t0, t0+2h], or when h1 buys+sells stay 0 across a
    full 2h that the observations actually cover.

    Price uses sol/token at each row. A stored price field is ignored.
    Zero-swap needs an observation at or after t0+2h, every buys and sells count
    from t0 through that observation, and no gap over 1h (h1 would not cover it).
    A positive h1 on an observation after the window is not counted as an
    in-window swap and is not counted as zero either (swaps_unattributed).

    dead is true, false, or null. null with pending means the 2h has not elapsed
    and price has not hit the line. null without pending means the swap coverage
    is not enough to decide and price did not hit the line.
    """
    rows = []
    for row in obs or []:
        if not isinstance(row, dict):
            continue
        t = _num(row.get("t_s"))
        if t is None:
            continue
        rows.append(row)
    rows.sort(key=lambda row: (_num(row.get("t_s")),))
    empty = {"dead": None, "pending": True, "reason": None, "price_drop": None, "obs_n": 0}
    if not rows:
        return empty
    t0 = _num(rows[0].get("t_s"))
    end = t0 + DEATH_WINDOW_S
    p0 = reserve_price(rows[0].get("sol"), rows[0].get("token"))
    in_window = [row for row in rows if t0 <= _num(row.get("t_s")) <= end]
    worst = None
    for row in in_window:
        price = reserve_price(row.get("sol"), row.get("token"))
        if p0 is None or p0 <= 0 or price is None:
            continue
        drop = (p0 - price) / p0
        if worst is None or drop > worst:
            worst = drop
        if drop >= PRICE_DROP - 1e-12:
            return {"dead": True, "pending": False, "reason": "price", "price_drop": drop, "obs_n": len(rows)}
    closing = [row for row in rows if _num(row.get("t_s")) >= end]
    base = {"dead": None, "pending": True, "reason": None, "price_drop": worst, "obs_n": len(rows)}
    if not closing:
        return base
    close = closing[0]
    span = [row for row in rows if t0 <= _num(row.get("t_s")) <= _num(close.get("t_s"))]
    counts = [_swaps(row.get("txns_h1")) for row in span]
    if any(count is None for count in counts):
        return {"dead": None, "pending": False, "reason": "swaps_unknown", "price_drop": worst, "obs_n": len(rows)}
    gaps = [_num(b.get("t_s")) - _num(a.get("t_s")) for a, b in zip(span, span[1:])]
    if any(gap > H1_S for gap in gaps):
        return {"dead": None, "pending": False, "reason": "swaps_gap", "price_drop": worst, "obs_n": len(rows)}
    if any((_swaps(row.get("txns_h1")) or 0) > 0 for row in in_window):
        return {"dead": False, "pending": False, "reason": None, "price_drop": worst, "obs_n": len(rows)}
    if _num(close.get("t_s")) > end and (_swaps(close.get("txns_h1")) or 0) > 0:
        return {"dead": None, "pending": False, "reason": "swaps_unattributed", "price_drop": worst, "obs_n": len(rows)}
    if all(count == 0 for count in counts):
        return {"dead": True, "pending": False, "reason": "swaps", "price_drop": worst, "obs_n": len(rows)}
    return {"dead": False, "pending": False, "reason": None, "price_drop": worst, "obs_n": len(rows)}


def _plain(node):
    if node is None or isinstance(node, (bool, str)):
        return node
    if isinstance(node, int) and not isinstance(node, bool):
        return node
    if isinstance(node, float):
        return None if node != node or node in (float("inf"), float("-inf")) else node
    if isinstance(node, dict):
        out = {}
        for key, val in node.items():
            if not isinstance(key, str) or key in _BANNED_KEYS:
                raise ValueError("banned")
            out[key] = _plain(val)
        return out
    if isinstance(node, list):
        return [_plain(val) for val in node]
    raise ValueError("type")


def _line(row):
    plain = _plain(row)
    text = json.dumps(plain, separators=(",", ":"), sort_keys=True)
    if _PRIVACY_RE.search(text):
        raise ValueError("privacy")
    return text


def _part_path(folder, stem, day, part):
    if part == 1:
        return os.path.join(folder, f"{stem}-{day}.jsonl")
    return os.path.join(folder, f"{stem}-{day}-{part}.jsonl")


def append_jsonl(data_dir, stem, row, now, max_bytes=MAX_BYTES):
    """Append one JSON line. Rolls on the UTC day and when the file reaches max_bytes."""
    day = time.strftime("%Y%m%d", time.gmtime(now))
    folder = os.path.join(data_dir, "v2")
    os.makedirs(folder, exist_ok=True)
    text = _line(row)
    part = 1
    while part < 100:
        path = _part_path(folder, stem, day, part)
        size = os.path.getsize(path) if os.path.exists(path) else 0
        if size == 0 or size < max_bytes:
            break
        part += 1
    path = _part_path(folder, stem, day, part)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
    return path


def _seen_path(data_dir):
    return os.path.join(data_dir, "v2", SEEN_NAME)


def load_seen(data_dir):
    path = _seen_path(data_dir)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("pools"), dict):
            return doc
    except (OSError, ValueError):
        pass
    return {"v": V, "pools": {}}


def save_seen(data_dir, doc):
    folder = os.path.join(data_dir, "v2")
    os.makedirs(folder, exist_ok=True)
    path = _seen_path(data_dir)
    tmp = path + ".tmp"
    plain = _plain(doc)
    text = json.dumps(plain, separators=(",", ":"), sort_keys=True)
    if _PRIVACY_RE.search(text):
        raise ValueError("privacy")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.write("\n")
    os.replace(tmp, path)


def _inputs_from(pair, rc, now):
    """S2 inputs exactly as fetched, plus the parsed numbers the rules use. No addresses except pool and dex fields."""
    created_raw = pair.get("pairCreatedAt") if isinstance(pair, dict) else None
    created_s = observe.pair_created_s(pair)
    grad = observe.graduated_pump(pair)
    sol, token, k, err = observe.snapshot_reserves(pair)
    h1 = txns_h1(pair)
    holders = holder_view(rc) if isinstance(rc, dict) else None
    if holders is None:
        top = dev = None
        holder_rows = None
        truncated = False
    else:
        holder_rows = holders["rows"]
        truncated = holders["truncated"]
        top = top10_from_rows(holder_rows)
        dev = dev_from_rows(holder_rows)
    age = None if created_s is None else float(now) - float(created_s)
    dex = pair.get("dexId") if isinstance(pair, dict) and isinstance(pair.get("dexId"), str) else None
    if isinstance(dex, str):
        dex = dex.strip()[:32] or None
    pool = public_id(observe.pool_addr(pair))
    quote = _num((pair.get("liquidity") or {}).get("quote")) if isinstance(pair, dict) and isinstance(pair.get("liquidity"), dict) else None
    base = _num((pair.get("liquidity") or {}).get("base")) if isinstance(pair, dict) and isinstance(pair.get("liquidity"), dict) else None
    score = _num(rc.get("score_normalised")) if isinstance(rc, dict) else None
    if isinstance(rc, dict):
        mint_a = authority_status(rc.get("mintAuthority"))
        freeze_a = authority_status(rc.get("freezeAuthority"))
    else:
        mint_a = freeze_a = None
    return {
        "dexId": dex,
        "pairAddress": pool,
        "pairCreatedAt": _num(created_raw),
        "pair_created_at_s": created_s,
        "age_s": age,
        "graduated_pump": grad,
        "liquidity_quote": quote,
        "liquidity_base": base,
        "sol": sol,
        "token": token,
        "k": k,
        "reserve_error": err,
        "txns_h1": h1,
        "holders": holder_rows,
        "holders_truncated": truncated,
        "top10_ex_pool": top,
        "dev_pct": dev,
        "mint_authority": mint_a,
        "freeze_authority": freeze_a,
        "score_normalised": score,
        "boost_window": observe.boost_window(created_s, now, grad),
    }


def _blank_inputs():
    return {
        "dexId": None, "pairAddress": None, "pairCreatedAt": None, "pair_created_at_s": None,
        "age_s": None, "graduated_pump": None, "liquidity_quote": None, "liquidity_base": None,
        "sol": None, "token": None, "k": None, "reserve_error": None, "txns_h1": None,
        "holders": None, "holders_truncated": False, "top10_ex_pool": None, "dev_pct": None,
        "mint_authority": None, "freeze_authority": None, "score_normalised": None,
        "boost_window": None,
    }


def _reserve_row(sol, token, k, pool, now):
    t_s = _t_s(now)
    return {"sol": sol, "token": token, "k": k, "pool": pool, "t": _iso(t_s) if t_s is not None else None, "t_s": t_s}


def _k_point(now, k, pool):
    return {"t_s": _t_s(now), "k": _num(k) if k is not None else None, "pool": pool}


def _follow_key(info, mint, now):
    t0 = _num(info.get("t0_s")) or 0
    last = _num(info.get("last_snap_s")) or t0
    open_window = 0 if now <= t0 + DEATH_WINDOW_S else 1
    # Death window first, oldest t0 first. Then the stalest snapshot, mint as a tie break.
    if open_window == 0:
        return (0, t0, mint)
    return (1, last, mint)


def _obs(mint, pool, t0_s, now, sol, token, k, h1, scan_id):
    t_s = _t_s(now)
    return {
        "type": "obs", "v": V, "mint": mint, "pool": pool,
        "t0": _iso(t0_s), "t0_s": t0_s, "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "sol": sol, "token": token, "k": k, "price": reserve_price(sol, token),
        "txns_h1": h1, "scan_id": scan_id,
    }


def _label_row(mint, t0_s, now, scan_id, label):
    t_s = _t_s(now)
    return {
        "type": "label", "v": V, "mint": mint, "t0_s": t0_s,
        "t_s": t_s, "scan_id": scan_id,
        "dead": label.get("dead"), "pending": bool(label.get("pending")),
        "reason": label.get("reason"), "price_drop": label.get("price_drop"),
        "obs_n": label.get("obs_n"),
    }


def _snap_row(mint, pool, now, sol, token, k, h1, scan_id, origin, error):
    t_s = _t_s(now)
    return {
        "v": V, "scan_id": scan_id, "mint": mint, "pool": pool,
        "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "sol": sol, "token": token, "k": k, "txns_h1": h1,
        "origin": origin, "error": error,
    }


def _touch_pool(pools, mint, now, pool, sol, token, k, h1, scan_id):
    """First-seen stays. This observation is appended. Returns the pool record."""
    t_s = _t_s(now)
    info = pools.get(mint)
    if not isinstance(info, dict):
        info = {
            "t0_s": t_s, "pool": pool, "first_scan": scan_id,
            "last_snap_s": None, "k_series": [], "obs": [],
        }
        pools[mint] = info
    if info.get("t0_s") is None:
        info["t0_s"] = t_s
    point = _k_point(now, k, pool)
    series = info.get("k_series")
    if not isinstance(series, list):
        series = []
    series.append(point)
    info["k_series"] = series
    obs = info.get("obs")
    if not isinstance(obs, list):
        obs = []
    obs.append({
        "t_s": t_s, "sol": sol, "token": token, "k": k, "pool": pool, "txns_h1": h1,
    })
    info["obs"] = obs
    info["last_snap_s"] = t_s
    if pool:
        info["pool"] = pool
    return info


def record(data_dir, seen, fetched, now, run_id, *, sources, pre_filters, fetch_pair=None,
           sleep=None, leads_per_run=None, cap=None, log_errors=0, max_bytes=MAX_BYTES):
    """Write this scan's manifest, snapshots, and death rows. Never raises.

    seen: every candidate (mint, source) in scanner order, including those past the lead cap.
    fetched: one dict per lead the scan actually looked up (pair, rc, pool_lookup, timestamps).
    fetch_pair(mint) reads one DexScreener token page. It is used only for pools already
    seen that this scan did not look up. Each call counts as an extra pool lookup.
    """
    summary = {
        "enabled": True, "extra_calls": 0, "log_errors": log_errors,
        "pool_lookups_attempted": 0, "pool_lookups_failed": 0, "error": None,
    }
    try:
        return _record(
            data_dir, seen, fetched, now, run_id, sources=sources, pre_filters=pre_filters,
            fetch_pair=fetch_pair, sleep=sleep, leads_per_run=leads_per_run, cap=cap,
            log_errors=log_errors, max_bytes=max_bytes, summary=summary,
        )
    except Exception:
        print("v2 log: skipped", file=sys.stderr)
        summary["error"] = "log_failed"
        return summary


def _record(data_dir, seen, fetched, now, run_id, *, sources, pre_filters, fetch_pair,
            sleep, leads_per_run, cap, log_errors, max_bytes, summary):
    if leads_per_run is None:
        leads_per_run = C.LEADS_PER_RUN
    if cap is None:
        cap = extra_cap()
    gap = extra_gap()
    t_s = _t_s(now)
    by_mint = {}
    for row in fetched or []:
        if isinstance(row, dict) and public_id(row.get("mint")):
            by_mint[row["mint"]] = row
    state = load_seen(data_dir)
    pools = state.setdefault("pools", {})
    candidates = []
    scan_attempted = 0
    scan_failed = 0
    seen_rows = list(seen or [])
    for index, item in enumerate(seen_rows):
        try:
            mint, source = item
        except (TypeError, ValueError):
            log_errors += 1
            continue
        mint = public_id(mint)
        if not mint:
            log_errors += 1
            continue
        if not isinstance(source, str) or source not in ("token-profiles", "token-boosts"):
            source = "token-profiles"
        selected = index < int(leads_per_run)
        rec = by_mint.get(mint)
        looked_up = rec is not None
        pool_lookup = rec.get("pool_lookup") if looked_up else "not_attempted"
        if pool_lookup not in ("ok", "failed", "no_pair", "not_attempted"):
            pool_lookup = "failed"
        if looked_up:
            scan_attempted += 1
            if pool_lookup == "failed":
                scan_failed += 1
        try:
            row = _candidate_row(
                pools, mint, source, index, selected, rec, pool_lookup, looked_up, now, run_id,
            )
        except Exception:
            log_errors += 1
            continue
        candidates.append(row)
    picks = sorted(row["mint"] for row in candidates if s2_pick(row["rules"], row["pool_lookup"]))
    pick_set = set(picks)
    twins = sorted(
        row["mint"] for row in candidates
        if row["mint"] not in pick_set and twin_candidate(row["inputs"], row["rules"], row["pool_lookup"])
    )
    attempted_mints = set(by_mint)
    pending = [mint for mint in pools if public_id(mint) and mint not in attempted_mints and isinstance(pools.get(mint), dict)]
    pending.sort(key=lambda mint: _follow_key(pools[mint], mint, now if t_s is None else t_s))
    extra_attempted = 0
    extra_failed = 0
    follow_skipped = 0
    if fetch_pair is None or cap <= 0:
        follow_skipped = len(pending)
        pending_run = []
    else:
        pending_run = pending[:cap]
        follow_skipped = len(pending) - len(pending_run)
    for mint in pending_run:
        if sleep is not None and gap > 0:
            try:
                sleep(gap)
            except Exception:
                log_errors += 1
        extra_attempted += 1
        try:
            pair = fetch_pair(mint)
            error = None
        except Exception:
            pair = None
            error = "lookup_failed"
            extra_failed += 1
        try:
            _follow_snapshot(pools, mint, pair, error, now, run_id, data_dir, max_bytes)
        except Exception:
            log_errors += 1
    for row in candidates:
        if row["pool_lookup"] != "ok":
            continue
        try:
            _write_observation(data_dir, pools, row["mint"], now, run_id, "scan", max_bytes, row["inputs"])
        except Exception:
            log_errors += 1
    attempted = scan_attempted + extra_attempted
    failed = scan_failed + extra_failed
    header = {
        "type": "scan", "v": V, "scan_id": str(run_id), "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "sources": list(sources or []),
        "pre_filters": list(pre_filters or []),
        "page_limits": {"leads_per_run": int(leads_per_run), "pages_per_source": 1},
        "candidates_seen": len(candidates),
        "candidates_selected": sum(1 for row in candidates if row["selected"]),
        "pool_lookups_attempted": attempted,
        "pool_lookups_failed": failed,
        "pool_lookups": {
            "attempted": attempted, "failed": failed,
            "scan_attempted": scan_attempted, "scan_failed": scan_failed,
            "extra_attempted": extra_attempted, "extra_failed": extra_failed,
        },
        "g5_rate": None if attempted == 0 else round(failed / attempted, 6),
        "extra_calls": extra_attempted,
        "extra_call_cap": int(cap),
        "follow_skipped_budget": follow_skipped,
        "s2_picks": picks,
        "twin_candidates": twins,
        "twin_draws": None,
        "log_errors": log_errors,
        "s2_thresholds": {
            "min_age_s": S2_MIN_AGE_S, "min_sol": S2_MIN_SOL,
            "min_trades_h1": S2_MIN_TRADES, "min_sells_h1": S2_MIN_SELLS,
            "max_top10_pct": S2_MAX_TOP10, "max_dev_pct": S2_MAX_DEV,
            "max_k_drop": S2_MAX_K_DROP, "authority_rule": True,
        },
    }
    append_jsonl(data_dir, MANIFEST, header, now, max_bytes=max_bytes)
    for row in candidates:
        append_jsonl(data_dir, MANIFEST, row, now, max_bytes=max_bytes)
    state["v"] = V
    save_seen(data_dir, state)
    summary.update(
        extra_calls=extra_attempted, log_errors=log_errors,
        pool_lookups_attempted=attempted, pool_lookups_failed=failed, error=None,
    )
    return summary


def _candidate_row(pools, mint, source, index, selected, rec, pool_lookup, looked_up, now, run_id):
    pair = rec.get("pair") if isinstance(rec, dict) else None
    rc = rec.get("rc") if isinstance(rec, dict) else None
    if pool_lookup == "ok" and isinstance(pair, dict):
        inputs = _inputs_from(pair, rc, now)
        info = _touch_pool(
            pools, mint, now, inputs.get("pairAddress"), inputs.get("sol"), inputs.get("token"),
            inputs.get("k"), inputs.get("txns_h1"), str(run_id),
        )
        k_series = list(info.get("k_series") or [])
        reserve = _reserve_row(inputs.get("sol"), inputs.get("token"), inputs.get("k"), inputs.get("pairAddress"), now)
    else:
        inputs = _blank_inputs()
        k_series = []
        reserve = None
        looked_up = looked_up and pool_lookup == "ok"
    rules = rules_from_logged(inputs, k_series, looked_up and pool_lookup == "ok")
    pair_at = _t_s(rec.get("pair_at")) if isinstance(rec, dict) else None
    rug_at = _t_s(rec.get("rug_at")) if isinstance(rec, dict) else None
    return {
        "type": "candidate", "v": V, "scan_id": str(run_id),
        "t": _iso(_t_s(now)), "t_s": _t_s(now),
        "mint": mint, "source": source, "seen_index": index, "selected": bool(selected),
        "pool_lookup": pool_lookup,
        "pair_fetched_at": _iso(pair_at) if pair_at is not None else None,
        "rug_fetched_at": _iso(rug_at) if rug_at is not None else None,
        "inputs": inputs, "reserve": reserve, "k_series": k_series, "rules": rules,
    }


def _follow_snapshot(pools, mint, pair, error, now, run_id, data_dir, max_bytes):
    if error or not isinstance(pair, dict):
        sol = token = k = None
        h1 = None
        pool = public_id(pools.get(mint, {}).get("pool")) if isinstance(pools.get(mint), dict) else None
        err = error or "no_pool"
    else:
        sol, token, k, err = observe.snapshot_reserves(pair)
        h1 = txns_h1(pair)
        pool = public_id(observe.pool_addr(pair)) or (
            public_id(pools.get(mint, {}).get("pool")) if isinstance(pools.get(mint), dict) else None
        )
    info = _touch_pool(pools, mint, now, pool, sol, token, k, h1, str(run_id))
    append_jsonl(
        data_dir, SNAPS,
        _snap_row(mint, pool, now, sol, token, k, h1, str(run_id), "follow", err),
        now, max_bytes=max_bytes,
    )
    _write_death(data_dir, mint, info, now, run_id, max_bytes, sol, token, k, h1, pool)


def _write_observation(data_dir, pools, mint, now, run_id, origin, max_bytes, inputs):
    info = pools.get(mint) or {}
    sol, token, k = inputs.get("sol"), inputs.get("token"), inputs.get("k")
    pool = inputs.get("pairAddress")
    h1 = inputs.get("txns_h1")
    append_jsonl(
        data_dir, SNAPS,
        _snap_row(mint, pool, now, sol, token, k, h1, str(run_id), origin, inputs.get("reserve_error")),
        now, max_bytes=max_bytes,
    )
    _write_death(data_dir, mint, info, now, run_id, max_bytes, sol, token, k, h1, pool)


def _write_death(data_dir, mint, info, now, run_id, max_bytes, sol, token, k, h1, pool):
    t0_s = _t_s(info.get("t0_s")) if isinstance(info, dict) else _t_s(now)
    obs_line = _obs(mint, pool, t0_s, now, sol, token, k, h1, str(run_id))
    append_jsonl(data_dir, DEATH, obs_line, now, max_bytes=max_bytes)
    label = death_label(info.get("obs") if isinstance(info, dict) else [obs_line])
    append_jsonl(data_dir, DEATH, _label_row(mint, t0_s, now, str(run_id), label), now, max_bytes=max_bytes)
