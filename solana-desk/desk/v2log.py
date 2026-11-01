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

Each JSONL rolls on the UTC day and again at 5 MB. A looked-up manifest row
stores the S2 inputs that are not derivable, with holder rows compacted.
A candidate that was not fetched omits the blank input object. Follow-ups
batch up to 30 mints per DexScreener call and stop once a death label is final.
Typical growth is about 1–2 MB/day. The 5 MB roll is the backstop.

S2 rules are spec section 4. They are recorded. This module does not enter,
does not draw a twin sample, and does not read or write config.py.
"""
import hashlib, json, os, re, sys, time, urllib.error
from . import config as C, observe, skips

MANIFEST = "manifest"
SNAPS = "pool-snaps"
DEATH = "death"
SEEN_NAME = "seen.json"
MAX_BYTES = 5 * 1024 * 1024
DEFAULT_EXTRA_CALLS = 24
EXTRA_CALL_HARD_MAX = 100
# DexScreener /tokens/v1/{chain}/{addresses} accepts up to 30 comma-separated mints.
# The cap above is HTTP calls, so one scan can cover 24*30 pools. 300 requests/minute
# on that route; this desk stays at or under the call cap and paces each call.
BATCH_SIZE = 30
BATCH_HARD_MAX = 30
DEFAULT_EXTRA_GAP_S = 0.3
DEFAULT_FOLLOW_BUDGET_S = 45
FOLLOW_BUDGET_HARD_MAX = 60
FOLLOW_CALL_TIMEOUT_S = 5
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


def follow_budget():
    """Seconds the follow-up reads may spend. Hard-capped so a scan stays far from the 420s job limit."""
    raw = os.environ.get("SOLANA_DESK_V2_FOLLOW_BUDGET_S", "").strip()
    if not raw:
        return DEFAULT_FOLLOW_BUDGET_S
    try:
        n = float(raw)
    except ValueError:
        return DEFAULT_FOLLOW_BUDGET_S
    if n < 0:
        return 0.0
    return min(n, FOLLOW_BUDGET_HARD_MAX)


def batch_size():
    """Mints per follow-up HTTP call. Hard-capped at the DexScreener batch limit."""
    raw = os.environ.get("SOLANA_DESK_V2_BATCH", "").strip()
    if not raw:
        return BATCH_SIZE
    try:
        n = int(raw)
    except ValueError:
        return BATCH_SIZE
    if n < 1:
        return 1
    return min(n, BATCH_HARD_MAX)


def mint_rank(mint):
    """Sort key for a capped follow set. Lower is kept. SHA-256 of the mint, not arrival order."""
    if not isinstance(mint, str):
        mint = ""
    return hashlib.sha256(mint.encode("utf-8")).hexdigest()


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


def _expand_holders(rows):
    """Holder rows as stored (objects or [pct, excluded, is_creator]) become objects."""
    if not isinstance(rows, list):
        return rows
    if not rows or isinstance(rows[0], dict):
        return rows
    out = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        out.append({"pct": row[0], "excluded": bool(row[1]), "is_creator": bool(row[2])})
    return out


def _compact_holders(rows):
    """[pct, 1 if excluded else 0, 1 if creator else 0]. No wallet, owner, or address."""
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        out.append([row.get("pct"), 1 if row.get("excluded") else 0, 1 if row.get("is_creator") else 0])
    return out


def hydrate_inputs(inputs, at_s=None):
    """Fill derivable S2 fields on a copy of a manifest input.

    A slim row omits pair_created_at_s, k, liquidity sides, top10, dev, and boost_window.
    Those are functions of pairCreatedAt, sol, token, holders, age_s, and graduated_pump.
    A stored value is left as it was. Holder rows may be objects or compact triples.
    """
    if not isinstance(inputs, dict):
        return inputs
    out = dict(inputs)
    holders = out.get("holders")
    if isinstance(holders, list):
        holders = _expand_holders(holders)
        out["holders"] = holders
        if out.get("top10_ex_pool") is None:
            out["top10_ex_pool"] = top10_from_rows(holders)
        if out.get("dev_pct") is None:
            out["dev_pct"] = dev_from_rows(holders)
    sol, token = _num(out.get("sol")), _num(out.get("token"))
    if out.get("k") is None and sol is not None and token is not None and sol >= 0 and token > 0:
        out["k"] = sol * token
    created = out.get("pair_created_at_s")
    if created is None and out.get("pairCreatedAt") is not None:
        created = observe.pair_created_s({"pairCreatedAt": out.get("pairCreatedAt")})
        out["pair_created_at_s"] = created
    age = _num(out.get("age_s"))
    entry = _num(at_s)
    if entry is None and created is not None and age is not None:
        entry = float(created) + float(age)
    if "boost_window" not in out:
        out["boost_window"] = observe.boost_window(created, entry, out.get("graduated_pump"))
    return out


_SLIM_INPUT_KEYS = (
    "dexId", "pairAddress", "pairCreatedAt", "age_s", "graduated_pump",
    "sol", "token", "txns_h1", "mint_authority", "freeze_authority", "score_normalised",
)


def _slim_inputs(inputs):
    """Drop fields a reader can recompute. Keep age_s: it is the scan-time snapshot."""
    if not isinstance(inputs, dict):
        return inputs
    out = {key: inputs.get(key) for key in _SLIM_INPUT_KEYS}
    err = inputs.get("reserve_error")
    if err:
        out["reserve_error"] = err
    holders = inputs.get("holders")
    if isinstance(holders, list):
        out["holders"] = _compact_holders(holders) if holders and isinstance(holders[0], dict) else list(holders)
    else:
        out["holders"] = None
    if inputs.get("holders_truncated"):
        out["holders_truncated"] = True
    return out


def authority_status(container, key):
    """revoked only when the key is present and null. unknown when the key is missing. set when it is truthy.

    The value itself is never returned. A missing RugCheck field is not treated as revoked.
    """
    if not isinstance(container, dict) or key not in container:
        return "unknown"
    value = container.get(key)
    if value is None:
        return "revoked"
    if value:
        return "set"
    return "unknown"


def _pool_same(a, b):
    return a == b


def k_dropped(series):
    """True when any comparable consecutive k is at least 50% below the earlier k.

    A missing k is skipped, so 100, None, 40 is a drop. A change of pool address
    breaks the chain: the new pool's k is not compared with the previous pool.
    """
    drop = k_stats(series)[2]
    return drop is not None and drop >= S2_MAX_K_DROP - 1e-12


def k_stats(series):
    """(n, last point, max consecutive fractional drop).

    n counts every point, including a missing k. The drop skips missing k and
    restarts when the pool address changes. None when no pair was compared.
    """
    prev = None
    prev_pool = None
    last = None
    max_drop = None
    n = 0
    for point in series or []:
        if not isinstance(point, dict):
            continue
        n += 1
        last = {"t_s": point.get("t_s"), "k": _num(point.get("k")), "pool": point.get("pool")}
        k = last["k"]
        pool = point.get("pool")
        if k is None:
            continue
        if prev is not None and not _pool_same(pool, prev_pool):
            prev = k
            prev_pool = pool
            continue
        if prev is not None and prev > 0:
            drop = (prev - k) / prev
            if max_drop is None or drop > max_drop:
                max_drop = drop
        prev = k
        prev_pool = pool
    return n, last, max_drop


def rules_from_logged(inputs, k_series, looked_up):
    """S2 pass/fail from manifest fields only. No pair and no RugCheck document."""
    unknown = {key: "unknown" for key in RULE_IDS}
    if not looked_up or not isinstance(inputs, dict):
        return unknown
    inputs = hydrate_inputs(inputs)
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


def _too_late(rows, now):
    """True when now is past t0+2h and an observation at now cannot cover the window.

    Coverage needs every consecutive pair, including that new point, to be at most 1h
    apart. A later read cannot see price inside the window or swaps the h1 field no
    longer reaches. The caller marks the label missed and does not fetch.
    """
    now_n = _num(now)
    if now_n is None or not rows:
        return False
    t0 = _num(rows[0].get("t_s"))
    if t0 is None or now_n <= t0 + DEATH_WINDOW_S:
        return False
    times = [t for t in (_num(row.get("t_s")) for row in rows) if t is not None]
    if not times or now_n - times[-1] > H1_S:
        return True
    seq = times + [now_n]
    return any(b - a > H1_S for a, b in zip(seq, seq[1:]))


def death_label(obs, *, now=None):
    """Spec section 4 death label, from raw observations only.

    Dead when reserve-implied price is at least 80% below the t0 price at any
    observation with t in [t0, t0+2h], or when h1 buys+sells stay 0 across a
    full 2h that the observations actually cover.

    Price uses sol/token at each row. A stored price field is ignored.
    The t0 row's txns.h1 looks back to t0-1h, so it is not part of the zero-swap
    test. A later row with an error, or with no buys/sells count, is missing
    data: it does not freeze the label as unknown. Consecutive timestamps still
    have to be within 1h or the gap rule applies. A positive h1 after the window
    is swaps_unattributed.

    dead is true, false, or null. null with pending means the 2h has not elapsed
    and price has not hit the line. null without pending means the swap coverage
    is not enough to decide and price did not hit the line. reason missed means
    now is past the last moment a new observation could still cover the window,
    so the label stays null and nothing is fetched to fill it in.
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
        if _too_late(rows, now):
            return {"dead": None, "pending": False, "reason": "missed", "price_drop": worst, "obs_n": len(rows)}
        return base
    close = closing[0]
    span = [row for row in rows if t0 <= _num(row.get("t_s")) <= _num(close.get("t_s"))]
    gaps = [_num(b.get("t_s")) - _num(a.get("t_s")) for a, b in zip(span, span[1:])]
    if any(gap > H1_S for gap in gaps):
        return {"dead": None, "pending": False, "reason": "swaps_gap", "price_drop": worst, "obs_n": len(rows)}

    def after_t0(row):
        return _num(row.get("t_s")) > t0

    def missing(row):
        return bool(row.get("error")) or _swaps(row.get("txns_h1")) is None

    counted = [row for row in span if after_t0(row) and not missing(row)]
    if any((_swaps(row.get("txns_h1")) or 0) > 0 for row in counted if _num(row.get("t_s")) <= end):
        return {"dead": False, "pending": False, "reason": None, "price_drop": worst, "obs_n": len(rows)}
    if _num(close.get("t_s")) > end and not missing(close) and (_swaps(close.get("txns_h1")) or 0) > 0:
        return {"dead": None, "pending": False, "reason": "swaps_unattributed", "price_drop": worst, "obs_n": len(rows)}
    if not counted or (missing(close) and _num(close.get("t_s")) >= end and not any(_num(row.get("t_s")) >= end and not missing(row) for row in span)):
        return {"dead": None, "pending": False, "reason": "swaps_gap", "price_drop": worst, "obs_n": len(rows)}
    if all((_swaps(row.get("txns_h1")) or 0) == 0 for row in counted):
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
    """(doc, corrupt). A missing file is a new index. A present file that does not parse is left untouched."""
    path = _seen_path(data_dir)
    if not os.path.exists(path):
        return {"v": V, "pools": {}}, False
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        if isinstance(doc, dict) and isinstance(doc.get("pools"), dict):
            return doc, False
    except (OSError, ValueError):
        pass
    return None, True


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
        mint_a = authority_status(rc, "mintAuthority")
        freeze_a = authority_status(rc, "freezeAuthority")
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


def _follow_plan(info, now):
    """('final', label) once the death label cannot change.

    ('missed', label) when the window can no longer be covered honestly.
    ('follow', 0) while t0+2h is still open. ('follow', 1) when one read at now
    can still close that window. Tier 0 is read before tier 1.
    """
    if not isinstance(info, dict) or info.get("final"):
        return "final", None
    obs = info.get("obs") if isinstance(info.get("obs"), list) else []
    if not obs:
        t0 = _num(info.get("t0_s"))
        if t0 is None or now <= t0 + DEATH_WINDOW_S:
            return "follow", 0
        return "missed", {"dead": None, "pending": False, "reason": "missed", "price_drop": None, "obs_n": 0}
    label = death_label(obs, now=now)
    if not label.get("pending"):
        if label.get("reason") == "missed":
            return "missed", label
        return "final", label
    t0 = _num(info.get("t0_s"))
    if t0 is None:
        t0 = _num(obs[0].get("t_s")) or 0
    if now <= t0 + DEATH_WINDOW_S:
        return "follow", 0
    return "follow", 1


def _seal(info, label):
    """Remember a finished death label and drop the observation copy.

    The death file keeps the rows. seen.json keeps t0, the k series, and this flag.
    """
    if not isinstance(info, dict):
        return
    info["final"] = True
    info["final_logged"] = True
    if isinstance(label, dict):
        info["death"] = {
            "dead": label.get("dead"),
            "pending": False,
            "reason": label.get("reason"),
        }
    info.pop("obs", None)


def _chunks(items, size):
    size = max(1, int(size))
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _obs(mint, pool, t0_s, at, sol, token, k, h1, scan_id, error):
    t_s = _t_s(at)
    return {
        "type": "obs", "v": V, "mint": mint, "pool": pool,
        "t0": _iso(t0_s), "t0_s": t0_s, "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "sol": sol, "token": token, "k": k, "price": reserve_price(sol, token),
        "txns_h1": h1, "error": error, "scan_id": scan_id,
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


def _skip_row(mint, now, scan_id, reason, tier):
    t_s = _t_s(now)
    return {
        "type": "skip", "v": V, "mint": mint,
        "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "scan_id": str(scan_id), "reason": reason, "tier": tier,
    }


def _emit_candidate(row):
    """Manifest line. Unfetched mints stay short. Looked-up inputs drop derivable fields."""
    lookup = row.get("pool_lookup")
    out = {
        "type": "candidate", "v": V, "scan_id": row.get("scan_id"),
        "t_s": row.get("t_s"), "mint": row.get("mint"), "source": row.get("source"),
        "seen_index": row.get("seen_index"), "selected": bool(row.get("selected")),
        "pool_lookup": lookup,
    }
    stems = list(row.get("snap_stems") or [])
    if lookup == "not_attempted":
        n = row.get("k_series_n") or 0
        if n:
            out["k_series_n"] = n
            out["k_last"] = row.get("k_last")
            out["k_max_drop"] = row.get("k_max_drop")
        if stems:
            out["snap_stems"] = stems
        return out
    if row.get("pair_at_s") is not None:
        out["pair_at_s"] = row.get("pair_at_s")
    if row.get("rug_at_s") is not None:
        out["rug_at_s"] = row.get("rug_at_s")
    out["rules"] = row.get("rules")
    out["k_series_n"] = row.get("k_series_n") or 0
    out["k_last"] = row.get("k_last")
    out["k_max_drop"] = row.get("k_max_drop")
    out["snap_stems"] = stems
    out["first_pick"] = bool(row.get("first_pick"))
    if lookup == "ok":
        out["inputs"] = _slim_inputs(row.get("inputs"))
    return out


def _snap_row(mint, pool, now, sol, token, k, h1, scan_id, origin, error):
    t_s = _t_s(now)
    return {
        "v": V, "scan_id": scan_id, "mint": mint, "pool": pool,
        "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "sol": sol, "token": token, "k": k, "txns_h1": h1,
        "origin": origin, "error": error,
    }


def _touch_pool(pools, mint, at, pool, sol, token, k, h1, scan_id, error=None):
    """First-seen stays. This observation is stamped with the fetch time. Returns the pool record."""
    t_s = _t_s(at)
    info = pools.get(mint)
    if not isinstance(info, dict):
        info = {
            "t0_s": t_s, "pool": pool, "first_scan": scan_id,
            "last_snap_s": None, "k_series": [], "obs": [], "snap_stems": [],
        }
        pools[mint] = info
    if info.get("t0_s") is None:
        info["t0_s"] = t_s
    point = _k_point(at, k, pool)
    if info.get("final"):
        series = info.get("k_series")
        if not isinstance(series, list):
            series = []
        series.append(point)
        info["k_series"] = series
        info["last_snap_s"] = t_s
        if pool:
            info["pool"] = pool
        return info
    series = info.get("k_series")
    if not isinstance(series, list):
        series = []
    series.append(point)
    info["k_series"] = series
    obs = info.get("obs")
    if not isinstance(obs, list):
        obs = []
    obs.append({
        "t_s": t_s, "sol": sol, "token": token, "k": k, "pool": pool,
        "txns_h1": h1, "error": error,
    })
    info["obs"] = obs
    info["last_snap_s"] = t_s
    if pool:
        info["pool"] = pool
    return info


def _note_stem(info, path, written):
    stem = os.path.basename(path)
    if stem.endswith(".jsonl"):
        stem = stem[:-len(".jsonl")]
    stems = info.setdefault("snap_stems", [])
    if stem not in stems:
        stems.append(stem)
    written.add(stem)
    return stem


def _k_public(series):
    n, last, drop = k_stats(series)
    return {
        "k_series_n": n,
        "k_last": last,
        "k_max_drop": drop,
    }


def record(data_dir, seen, fetched, now, run_id, *, sources, pre_filters, fetch_pair=None,
           fetch_pairs=None, sleep=None, leads_per_run=None, cap=None, batch=None,
           log_errors=0, max_bytes=MAX_BYTES, source_pages=None, budget_s=None, now_fn=None):
    """Write this scan's manifest, snapshots, and death rows. Never raises.

    seen: every candidate (mint, source) in scanner order, including those past the lead cap.
    fetched: one dict per lead the scan actually looked up (pair, rc, pool_lookup, timestamps).
    fetch_pairs(mints) reads one DexScreener page for up to batch mints and returns
    {mint: pair or None}. fetch_pair(mint) is the one-mint form; it is one HTTP call
    per mint. Either is used only for pools already seen that this scan did not look up.
    A finished death label is not read again. cap counts HTTP calls, not mints.
    """
    summary = {
        "enabled": True, "extra_calls": 0, "log_errors": log_errors,
        "pool_lookups_attempted": 0, "pool_lookups_failed": 0, "error": None,
    }
    try:
        return _record(
            data_dir, seen, fetched, now, run_id, sources=sources, pre_filters=pre_filters,
            fetch_pair=fetch_pair, fetch_pairs=fetch_pairs, sleep=sleep,
            leads_per_run=leads_per_run, cap=cap, batch=batch,
            log_errors=log_errors, max_bytes=max_bytes, summary=summary,
            source_pages=source_pages, budget_s=budget_s, now_fn=now_fn or time.time,
        )
    except Exception:
        print("v2 log: skipped", file=sys.stderr)
        summary["error"] = "log_failed"
        return summary


def _record(data_dir, seen, fetched, now, run_id, *, sources, pre_filters, fetch_pair,
            fetch_pairs, sleep, leads_per_run, cap, batch, log_errors, max_bytes, summary,
            source_pages, budget_s, now_fn):
    if leads_per_run is None:
        leads_per_run = C.LEADS_PER_RUN
    if cap is None:
        cap = extra_cap()
    if budget_s is None:
        budget_s = follow_budget()
    # One-mint callback: each call is one HTTP call, so the batch width is 1.
    if fetch_pairs is None:
        width = 1
    elif batch is None:
        width = batch_size()
    else:
        try:
            width = int(batch)
        except (TypeError, ValueError):
            width = batch_size()
        if width < 1:
            width = 1
        width = min(width, BATCH_HARD_MAX)
    gap = extra_gap()
    t_s = _t_s(now)
    by_mint = {}
    rug_attempted = 0
    rug_failed = 0
    for row in fetched or []:
        if isinstance(row, dict) and public_id(row.get("mint")):
            by_mint[row["mint"]] = row
            rug_state = row.get("rug_lookup")
            if rug_state in ("ok", "failed"):
                rug_attempted += 1
                if rug_state == "failed":
                    rug_failed += 1
    state, corrupt = load_seen(data_dir)
    if corrupt:
        summary["error"] = "seen_corrupt"
        summary["log_errors"] = log_errors + 1
        print("v2 log: skipped (seen index)", file=sys.stderr)
        return summary
    pools = state.setdefault("pools", {})
    written_stems = set()
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
    clock = t_s if t_s is not None else now
    pending = [
        mint for mint in pools
        if public_id(mint) and mint not in attempted_mints and isinstance(pools.get(mint), dict)
    ]
    ranked = []
    missed = []
    for mint in pending:
        info = pools.get(mint)
        kind, payload = _follow_plan(info, clock)
        if kind == "final":
            if isinstance(info, dict) and not info.get("final"):
                _seal(info, payload)
            continue
        if kind == "missed":
            missed.append((mint, payload))
            continue
        ranked.append((payload, mint_rank(mint), mint))
    ranked.sort()
    for mint, label in missed:
        info = pools.get(mint)
        if not isinstance(info, dict):
            continue
        try:
            if not info.get("final_logged"):
                append_jsonl(
                    data_dir, DEATH, _label_row(mint, _t_s(info.get("t0_s")), clock, str(run_id), label),
                    now, max_bytes=max_bytes,
                )
            _seal(info, label)
        except Exception:
            log_errors += 1
    have_fetch = fetch_pairs is not None or fetch_pair is not None
    pool_limit = (max(0, int(cap)) * width) if have_fetch else 0
    chosen = ranked[:pool_limit]
    cap_left = ranked[pool_limit:]
    skip_reasons = {"cap": 0, "budget": 0, "429": 0}

    def _log_skip(mint, tier, reason):
        nonlocal log_errors
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
        try:
            append_jsonl(
                data_dir, DEATH, _skip_row(mint, clock, run_id, reason, tier),
                now, max_bytes=max_bytes,
            )
        except Exception:
            log_errors += 1

    for tier, _rank, mint in cap_left:
        _log_skip(mint, tier, "cap")
    extra_calls = 0
    extra_attempted = 0
    extra_failed = 0
    follow_stop = None
    hit_budget = False
    saw_429 = False
    batches = list(_chunks([mint for _tier, _rank, mint in chosen], width))
    tier_of = {mint: tier for tier, _rank, mint in chosen}
    next_batch = 0
    started = time.monotonic()
    for index, batch_mints in enumerate(batches):
        if time.monotonic() - started >= budget_s:
            hit_budget = True
            next_batch = index
            break
        if sleep is not None and gap > 0:
            try:
                sleep(gap)
            except Exception:
                log_errors += 1
            if time.monotonic() - started >= budget_s:
                hit_budget = True
                next_batch = index
                break
        next_batch = index + 1
        extra_calls += 1
        http_err = None
        try:
            if fetch_pairs is not None:
                got = fetch_pairs(list(batch_mints))
            else:
                got = {batch_mints[0]: fetch_pair(batch_mints[0])}
            if not isinstance(got, dict):
                got = {}
            error = None
        except urllib.error.HTTPError as exc:
            got = {}
            error = "lookup_failed"
            http_err = exc
        except Exception:
            got = {}
            error = "lookup_failed"
        fetched_at = now_fn()
        for mint in batch_mints:
            extra_attempted += 1
            if error:
                pair = None
                err = error
                extra_failed += 1
            else:
                pair = got.get(mint)
                err = None
            try:
                _follow_snapshot(
                    pools, mint, pair, err, fetched_at, run_id, data_dir, max_bytes, written_stems,
                )
            except Exception:
                log_errors += 1
        if http_err is not None and getattr(http_err, "code", None) == 429:
            saw_429 = True
            break
    rest_reason = "429" if saw_429 else ("budget" if hit_budget else None)
    if rest_reason:
        for batch_mints in batches[next_batch:]:
            for mint in batch_mints:
                _log_skip(mint, tier_of.get(mint), rest_reason)
    if saw_429:
        follow_stop = "429"
    elif hit_budget:
        follow_stop = "budget"
    elif cap_left:
        follow_stop = "cap"
    follow_skipped = sum(skip_reasons.values())
    for row in candidates:
        if row["pool_lookup"] != "ok":
            continue
        try:
            _write_observation(
                data_dir, pools, row["mint"], row.get("_fetch_at", now), run_id, "scan",
                max_bytes, row["inputs"], written_stems,
            )
        except Exception:
            log_errors += 1
    header = {
        "type": "scan", "v": V, "scan_id": str(run_id), "t": _iso(t_s) if t_s is not None else None, "t_s": t_s,
        "sources": list(sources or []),
        "source_pages": list(source_pages or []),
        "pre_filters": list(pre_filters or []),
        "page_limits": {"leads_per_run": int(leads_per_run), "pages_per_source": 1},
        "candidates_seen": len(candidates),
        "candidates_selected": sum(1 for row in candidates if row["selected"]),
        "pool_lookups_attempted": scan_attempted,
        "pool_lookups_failed": scan_failed,
        "pool_lookups": {
            "scan_attempted": scan_attempted, "scan_failed": scan_failed,
            "extra_attempted": extra_attempted, "extra_failed": extra_failed,
        },
        "g5_rate": None if scan_attempted == 0 else round(scan_failed / scan_attempted, 6),
        "rug_lookups_attempted": rug_attempted,
        "rug_lookups_failed": rug_failed,
        "rug_fail_rate": None if rug_attempted == 0 else round(rug_failed / rug_attempted, 6),
        "extra_calls": extra_calls,
        "extra_call_cap": int(cap),
        "batch_size": width if have_fetch else 0,
        "follow_budget_s": budget_s,
        "follow_skipped_budget": follow_skipped,
        "follow_skip_reasons": skip_reasons,
        "follow_missed": len(missed),
        "follow_stop": follow_stop,
        "snap_files": sorted(written_stems),
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
    for row in candidates:
        info = pools.get(row["mint"])
        if isinstance(info, dict):
            row["snap_stems"] = list(info.get("snap_stems") or [])
    append_jsonl(data_dir, MANIFEST, header, now, max_bytes=max_bytes)
    for row in candidates:
        append_jsonl(data_dir, MANIFEST, _emit_candidate(row), now, max_bytes=max_bytes)
    state["v"] = V
    save_seen(data_dir, state)
    summary.update(
        extra_calls=extra_calls, log_errors=log_errors,
        pool_lookups_attempted=scan_attempted, pool_lookups_failed=scan_failed, error=None,
        follow_stop=follow_stop, follow_skipped_budget=follow_skipped,
    )
    return summary


def _candidate_row(pools, mint, source, index, selected, rec, pool_lookup, looked_up, now, run_id):
    pair = rec.get("pair") if isinstance(rec, dict) else None
    rc = rec.get("rc") if isinstance(rec, dict) else None
    pair_at = rec.get("pair_at") if isinstance(rec, dict) else None
    fetch_at = pair_at if pair_at is not None else now
    if pool_lookup == "ok" and isinstance(pair, dict):
        inputs = _inputs_from(pair, rc, fetch_at)
        info = _touch_pool(
            pools, mint, fetch_at, inputs.get("pairAddress"), inputs.get("sol"), inputs.get("token"),
            inputs.get("k"), inputs.get("txns_h1"), str(run_id), error=inputs.get("reserve_error"),
        )
        k_series = list(info.get("k_series") or [])
        reserve = _reserve_row(inputs.get("sol"), inputs.get("token"), inputs.get("k"), inputs.get("pairAddress"), fetch_at)
        snap_stems = list(info.get("snap_stems") or [])
    else:
        inputs = _blank_inputs()
        reserve = None
        info = pools.get(mint) if isinstance(pools.get(mint), dict) else None
        k_series = list((info or {}).get("k_series") or [])
        snap_stems = list((info or {}).get("snap_stems") or [])
        looked_up = False
    rules = rules_from_logged(inputs, k_series, looked_up and pool_lookup == "ok")
    picked = bool(isinstance(info, dict) and info.get("s2_picked"))
    first_pick = bool(s2_pick(rules, pool_lookup) and not picked)
    if first_pick and isinstance(info, dict):
        info["s2_picked"] = True
    pair_s = _t_s(pair_at) if isinstance(rec, dict) else None
    rug_at = _t_s(rec.get("rug_at")) if isinstance(rec, dict) else None
    row = {
        "type": "candidate", "v": V, "scan_id": str(run_id),
        "t": _iso(_t_s(now)), "t_s": _t_s(now),
        "mint": mint, "source": source, "seen_index": index, "selected": bool(selected),
        "pool_lookup": pool_lookup,
        "pair_at_s": pair_s, "rug_at_s": rug_at,
        "inputs": inputs, "reserve": reserve, "rules": rules,
        "snap_stems": snap_stems, "first_pick": first_pick,
        "_fetch_at": fetch_at,
    }
    row.update(_k_public(k_series))
    return row


def _follow_snapshot(pools, mint, pair, error, at, run_id, data_dir, max_bytes, written):
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
    info = _touch_pool(pools, mint, at, pool, sol, token, k, h1, str(run_id), error=err)
    path = append_jsonl(
        data_dir, SNAPS,
        _snap_row(mint, pool, at, sol, token, k, h1, str(run_id), "follow", err),
        at, max_bytes=max_bytes,
    )
    _note_stem(info, path, written)
    _write_death(data_dir, mint, info, at, run_id, max_bytes, sol, token, k, h1, pool, err)


def _write_observation(data_dir, pools, mint, at, run_id, origin, max_bytes, inputs, written):
    info = pools.get(mint) or {}
    sol, token, k = inputs.get("sol"), inputs.get("token"), inputs.get("k")
    pool = inputs.get("pairAddress")
    h1 = inputs.get("txns_h1")
    err = inputs.get("reserve_error")
    path = append_jsonl(
        data_dir, SNAPS,
        _snap_row(mint, pool, at, sol, token, k, h1, str(run_id), origin, err),
        at, max_bytes=max_bytes,
    )
    if isinstance(info, dict):
        _note_stem(info, path, written)
    if isinstance(info, dict) and info.get("final"):
        return
    _write_death(data_dir, mint, info, at, run_id, max_bytes, sol, token, k, h1, pool, err)


def _write_death(data_dir, mint, info, at, run_id, max_bytes, sol, token, k, h1, pool, error):
    t0_s = _t_s(info.get("t0_s")) if isinstance(info, dict) else _t_s(at)
    obs_line = _obs(mint, pool, t0_s, at, sol, token, k, h1, str(run_id), error)
    append_jsonl(data_dir, DEATH, obs_line, at, max_bytes=max_bytes)
    label = death_label(info.get("obs") if isinstance(info, dict) else [obs_line], now=at)
    append_jsonl(data_dir, DEATH, _label_row(mint, t0_s, at, str(run_id), label), at, max_bytes=max_bytes)
    if isinstance(info, dict) and not label.get("pending"):
        _seal(info, label)
