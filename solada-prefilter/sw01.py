"""SW-01 candidate detector for the Solada pre-filter. Zero AI.

A heads-up for the owner when a Solada launch qualifies for the pre-registered
paper test. The receiving agent re-verifies every figure. This is not the test,
and it does not change rules k, y, aa, kill lines, verdicts, or other webhook
reasons. Any error is logged by the caller and skipped.

Gates, all required:

1. Swarm-flagged at launch (`coordinated_buys` or `fresh_wallet_swarm`). The
   earliest of that flag's time and the token's first sighting is after
   2026-11-01T19:40:00Z (14:40 America/Toronto that day; EST, UTC-5).
2. A DEX pool (bonded off the curve). Quote basis `geckoterminal` or
   `dexscreener`. Curve ADA, the coin board, and the platform market are not.
3. Pool ADA >= 25000 and holders >= 120. Holders are the lower of the on-chain
   circulating count and Solada's count when both exist. Circulating leaves out
   script (pool) addresses and `flags.exclude_tags` (the curve reserve). When
   only one count exists, that count is used. The numeric floors are not loosened.
4. Age >= 24h from the launch time.
5. A codenamed whale-watch wallet bought after launch+24h. Rough check of rows
   already written this run: lead-whale ledger buys, snapshot moves with a
   positive delta, and a liquidity swap whose actor is a role label. A hash tag
   is not a codename. An untimed position does not count.

Once per policy id. A measured miss waits 6h (`rearm_h`) before another look.
A missing pool, holder, or launch time is not a miss, so the next run retries.
`sw01_exit` posts once, on the first run at or after the alert time plus 48h.
State is the `sw01` object in the existing `state.json`. No new data file.
"""
import hashlib
import json
import os
from datetime import datetime, timezone

import flags as F
import dev_checks as DC

CUTOFF = "2026-11-01T19:40:00Z"
SWARM_KINDS = ("coordinated_buys", "fresh_wallet_swarm")
DEX_BASES = ("geckoterminal", "dexscreener")
METRIC_KEYS = ("policy", "pool_ada", "holders", "buys_6h", "sells_6h", "age_h", "whale_tags")
_CAP = 500


def sw_cfg(cfg):
    """Knobs that are not desk rules. Missing keys keep the registered floors."""
    raw = (cfg or {}).get("sw01") if isinstance(cfg, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    def num(key, default):
        try:
            v = raw.get(key, default)
            if isinstance(v, bool) or v is None:
                return default
            return float(v)
        except (TypeError, ValueError):
            return default
    return {
        "enabled": raw.get("enabled", True) is not False,
        "min_pool_ada": num("min_pool_ada", 25000),
        "min_holders": num("min_holders", 120),
        "min_age_h": num("min_age_h", 24),
        "rearm_h": num("rearm_h", 6),
        "exit_h": num("exit_h", 48),
        "cutoff": raw.get("cutoff") if isinstance(raw.get("cutoff"), str) and raw.get("cutoff") else CUTOFF,
        "codename": ((cfg or {}).get("whale") or {}).get("codename") if isinstance(cfg, dict) else None,
    }


def parse_time(value):
    """Unix seconds from an ISO-8601 Z stamp or a number. None when unreadable."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def policy_of(value):
    """First 56 hex chars of a policy id or unit. None when shorter."""
    if not isinstance(value, str):
        return None
    hexed = "".join(c for c in value.lower() if c in "0123456789abcdef")
    if len(hexed) < 56:
        return None
    return hexed[:56]


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _whole(value):
    n = _num(value)
    if n is None:
        return None
    if n == int(n):
        return int(n)
    return n


def circulating_holders(dist, exclude_tags):
    """Addresses with a balance, excluding script (pool) addresses and exclude_tags.

    exclude_tags is matched on the address tag and the stake tag, same as the
    concentration flags. None when dist was not fetched. An empty page is 0.
    """
    if dist is None:
        return None
    if not isinstance(dist, (list, tuple)):
        return None
    excluded = set()
    if isinstance(exclude_tags, dict):
        excluded = {str(k) for k in exclude_tags if str(k).strip()}
    elif isinstance(exclude_tags, (list, tuple, set)):
        excluded = {str(k) for k in exclude_tags if str(k).strip()}
    n = 0
    for item in dist:
        try:
            addr, qty = item[0], item[1]
        except (TypeError, IndexError, KeyError):
            continue
        q = _num(qty)
        if not isinstance(addr, str) or q is None or q <= 0:
            continue
        try:
            stake, script = F.addr_info(addr)
        except Exception:
            continue
        if script:
            continue
        if excluded and (F.tag(addr) in excluded or (stake and F.tag(stake) in excluded)):
            continue
        n += 1
    return n


def holder_count(on_chain, solada):
    """Lower of the two counts when both exist; the one that exists otherwise."""
    a, b = _num(on_chain), _num(solada)
    if a is not None and b is not None:
        return _whole(min(a, b))
    if a is not None:
        return _whole(a)
    if b is not None:
        return _whole(b)
    return None


def _ticker(value):
    if value is None:
        return ""
    text = str(value).strip()
    return text


def _ticker_key(value):
    return _ticker(value).upper()


def allowed_labels(cfg):
    """Role labels plus the lead whale's public codename. No wallet ids."""
    roles = set()
    try:
        roles = {r for r in DC.known_roles(cfg) if isinstance(r, str)}
    except Exception:
        roles = set()
    roles.add("log_whale_watch")
    name = sw_cfg(cfg).get("codename")
    name = name.strip() if isinstance(name, str) else ""
    if name and not F.FULL_ID_RE.search(name):
        roles.add(name)
    codename = name or "lead_whale"
    if codename not in roles and not F.FULL_ID_RE.search(codename):
        roles.add(codename)
    return roles, codename


def _publish_label(label, allowed):
    if not isinstance(label, str) or label not in allowed:
        return None
    if label.startswith("w-") or label.startswith("m-") or F.FULL_ID_RE.search(label):
        return None
    return label


def _token_match(row_token, ticker, policy, index):
    """True when a ledger token key is this ticker, and a policy suffix agrees."""
    if not isinstance(row_token, str) or not ticker:
        return False
    if row_token == "ADA" or row_token.startswith("LP"):
        return False
    base, sep, suf = row_token.partition("~")
    if base.upper() != ticker.upper():
        return False
    pref = ""
    if sep and suf:
        pref = suf.split("+", 1)[0].lower()
    elif isinstance(index, dict):
        indexed = index.get(row_token)
        if isinstance(indexed, str):
            pref = "".join(c for c in indexed.lower() if c in "0123456789abcdef")
    if not pref:
        return True
    return isinstance(policy, str) and policy.lower().startswith(pref)


def _row_is_buy(row):
    kind = row.get("kind") if isinstance(row, dict) else None
    if kind == "swap":
        return row.get("direction") == "buy"
    if kind == "transfer_in":
        tok = row.get("token")
        return isinstance(tok, str) and tok not in ("ADA",) and not tok.startswith("LP")
    if kind == "order_fill":
        tok = row.get("token")
        return isinstance(tok, str) and tok not in ("ADA",) and not tok.startswith("LP")
    return False


def whale_tags(token, ctx, now_s, knobs):
    """Codenames that bought after launch+24h. Empty is a measured miss, not unknown.

    None only when the launch time is missing, because the 24h mark is then unknown.
    """
    live = token.get("live_s")
    if live is None:
        return None
    after = live + knobs["min_age_h"] * 3600
    allowed = ctx.get("allowed") or set()
    codename = _publish_label(ctx.get("codename"), allowed)
    found = []
    index = ctx.get("ledger_index") or {}
    ticker, policy = token.get("ticker"), token.get("policy")
    for row in ctx.get("ledger_rows") or []:
        if not isinstance(row, dict) or not _row_is_buy(row):
            continue
        ts = parse_time(row.get("time"))
        if ts is None or not (after < ts <= now_s):
            continue
        if _token_match(row.get("token"), ticker, policy, index) and codename:
            found.append(codename)
    for mv in ctx.get("whale_moves") or []:
        if not isinstance(mv, dict):
            continue
        delta = _num(mv.get("delta"))
        if delta is None or delta <= 0:
            continue
        ts = parse_time(mv.get("at") if mv.get("at") is not None else mv.get("time"))
        if ts is None or not (after < ts <= now_s):
            continue
        asset = mv.get("asset") if mv.get("asset") is not None else mv.get("token")
        if _token_match(str(asset), ticker, policy, {}) and codename:
            found.append(codename)
    for mv in ctx.get("liquidity") or []:
        if not isinstance(mv, dict):
            continue
        if mv.get("kind") not in (None, "swap") or mv.get("direction") != "buy":
            continue
        label = _publish_label(mv.get("actor_tag"), allowed)
        if not label:
            continue
        ts = parse_time(mv.get("time") if mv.get("time") is not None else mv.get("at"))
        if ts is None or not (after < ts <= now_s):
            continue
        if _ticker_key(mv.get("ticker")) != _ticker_key(ticker):
            continue
        short = mv.get("policy_short")
        if isinstance(short, str) and short and isinstance(policy, str):
            hexed = "".join(c for c in short.lower() if c in "0123456789abcdef")
            if hexed and not policy.lower().startswith(hexed):
                continue
        found.append(label)
    return sorted(set(found))


def _live_s(launch):
    if not isinstance(launch, dict):
        return None
    ms = _num(launch.get("liveAtMs"))
    if ms is None:
        ms = _num(launch.get("live_at_ms"))
    if ms is None:
        return None
    if ms > 10_000_000_000:  # board stamps are milliseconds
        return ms / 1000.0
    return ms


def _solada_holders(launch, row):
    for src in (launch, row):
        if not isinstance(src, dict):
            continue
        if "holders" in src and src.get("holders") is not None and src is launch:
            return _num(src.get("holders"))
        if src is row and src.get("native_holders") is not None:
            return _num(src.get("native_holders"))
    if isinstance(launch, dict) and launch.get("holders") is not None:
        return _num(launch.get("holders"))
    return None


def _dex_ada(row):
    """(basis_ok, pool_ada or None). A curve quote is a measured non-pool, not unknown."""
    if not isinstance(row, dict):
        return None, None
    basis = row.get("pool_basis") or row.get("ada_basis")
    ada = _num(row.get("ada_side"))
    if basis in DEX_BASES:
        return True, ada
    if basis in ("solada-curve", "solada-market", "solada-coins"):
        return False, ada
    if row.get("graduated") and isinstance(row.get("pool"), dict):
        # Board says bonded, but this run has no DEX quote. ADA side is unknown.
        return None, None
    return None, None


def gates(token, ctx, now_s, knobs):
    """Return ok True, False (measured miss), or None (retry next run).

    `failed` lists gates that missed. `unknown` lists gates this run could not read.
    A measured failure wins over an unknown one.
    """
    failed, unknown = [], []
    origin_times = [t for t in (token.get("origin") or []) if isinstance(t, (int, float))]
    cutoff = parse_time(knobs["cutoff"])
    if not token.get("swarm"):
        failed.append("swarm")
    elif not origin_times or cutoff is None:
        unknown.append("cutoff")
    elif min(origin_times) <= cutoff:
        failed.append("cutoff")
    dex_ok, pool_ada = _dex_ada(token.get("row") or {})
    if dex_ok is False:
        failed.append("pool")
        pool_ada = None
    elif dex_ok is not True or pool_ada is None:
        unknown.append("pool")
        pool_ada = None
    elif pool_ada < knobs["min_pool_ada"]:
        failed.append("pool_ada")
    holders = holder_count(token.get("on_chain"), token.get("solada"))
    if holders is None:
        unknown.append("holders")
    elif holders < knobs["min_holders"]:
        failed.append("holders")
    live = token.get("live_s")
    age_h = None
    if live is None:
        unknown.append("age")
    else:
        age_h = (now_s - live) / 3600.0
        if age_h < knobs["min_age_h"]:
            failed.append("age")
    tags = whale_tags(token, ctx, now_s, knobs)
    if tags is None:
        unknown.append("whale")
    elif not tags:
        failed.append("whale")
    buys = _whole((token.get("flow") or {}).get("buys_6h"))
    sells = _whole((token.get("flow") or {}).get("sells_6h"))
    if failed:
        ok = False
    elif unknown:
        ok = None
    else:
        ok = True
    metrics = None
    if ok is True:
        metrics = {
            "policy": token["policy"],
            "pool_ada": _whole(pool_ada),
            "holders": holders,
            "buys_6h": buys,
            "sells_6h": sells,
            "age_h": round(age_h, 2),
            "whale_tags": tags,
        }
    return {"ok": ok, "failed": failed, "unknown": unknown, "metrics": metrics, "whale_tags": tags or []}


def _load_json(path):
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def context_from_run(launches, rows, dists, flags, data_dir, state, cfg, pools=None):
    """Assemble the rough inputs. Missing files are empty, not errors."""
    allowed, codename = allowed_labels(cfg)
    ledger = _load_json(os.path.join(data_dir, "whale_ledger.json")) if data_dir else {}
    liq = _load_json(os.path.join(data_dir, "liquidity_moves.json")) if data_dir else {}
    index = {}
    for tok in ledger.get("tokens") or []:
        if isinstance(tok, dict) and isinstance(tok.get("token"), str) and isinstance(tok.get("policy"), str):
            index[tok["token"]] = tok["policy"]
    moves = []
    whale = state.get("whale") if isinstance(state, dict) else None
    if isinstance(whale, dict):
        moves = [m for m in (whale.get("moves") or []) if isinstance(m, dict)]
    return {
        "launches": launches or [],
        "rows": rows or [],
        "dists": dists if isinstance(dists, dict) else {},
        "flags": [f for f in (flags or []) if isinstance(f, dict)],
        "exclude_tags": ((cfg or {}).get("flags") or {}).get("exclude_tags") if isinstance(cfg, dict) else {},
        "ledger_rows": [r for r in (ledger.get("rows") or []) if isinstance(r, dict)],
        "ledger_index": index,
        "whale_moves": moves,
        "liquidity": [m for m in (liq.get("moves") or []) if isinstance(m, dict)],
        "pools": pools if isinstance(pools, dict) else {},
        "allowed": allowed,
        "codename": codename,
        "cfg": cfg if isinstance(cfg, dict) else {},
    }


def _swarm_index(flags, launches):
    """policy -> earliest flag time (or None) for swarm flags that join one launch."""
    by_ticker = {}
    for launch in launches or []:
        if not isinstance(launch, dict):
            continue
        key = _ticker_key(launch.get("ticker"))
        if key:
            by_ticker.setdefault(key, []).append(launch)
    out = {}
    for flag in flags or []:
        if not isinstance(flag, dict) or flag.get("kind") not in SWARM_KINDS:
            continue
        cands = by_ticker.get(_ticker_key(flag.get("ticker"))) or []
        chosen = []
        if len(cands) == 1:
            chosen = cands
        elif len(cands) > 1:
            flag_s = parse_time(flag.get("at"))
            close = []
            for launch in cands:
                live = _live_s(launch)
                if flag_s is not None and live is not None and abs(flag_s - live) <= 3 * 3600:
                    close.append(launch)
            if len(close) == 1:
                chosen = close
        for launch in chosen:
            pol = policy_of(launch.get("policyId") or launch.get("policy") or "")
            if not pol:
                continue
            ts = parse_time(flag.get("at"))
            prev = out.get(pol)
            if prev is None:
                out[pol] = ts
            elif ts is not None and (prev is None or ts < prev):
                out[pol] = ts
            elif pol not in out:
                out[pol] = ts
    return out


def _row_by_policy(rows):
    out = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pol = policy_of(row.get("unit") or row.get("policy") or "")
        if pol and pol not in out:
            out[pol] = row
    return out


def _flow(pools, unit):
    pool = (pools or {}).get(unit) if isinstance(pools, dict) else None
    if not isinstance(pool, dict):
        return {}
    return {"buys_6h": pool.get("buys_h6", pool.get("buys_6h")), "sells_6h": pool.get("sells_h6", pool.get("sells_6h"))}


def _cap(mapping, protect):
    if not isinstance(mapping, dict) or len(mapping) <= _CAP:
        return
    def stamp(value):
        if isinstance(value, dict):
            return str(value.get("at") or "")
        return str(value or "")
    overflow = len(mapping) - _CAP
    for key in sorted(mapping, key=lambda k: stamp(mapping[k])):
        if overflow <= 0:
            break
        if key in protect:
            continue
        mapping.pop(key, None)
        overflow -= 1


def _bucket_from(state):
    if not isinstance(state, dict):
        return {}
    raw = state.get("sw01")
    return dict(raw) if isinstance(raw, dict) else {}


def decide(state, ctx, now_s, persist=True):
    """Plan candidate and exit posts. Mutates state['sw01'] only when persist is true.

    Alerts are not marked sent here. The caller marks them after the webhook
    accepts the post, so a failed post is retried next run and does not wait 6h.
    """
    knobs = sw_cfg((ctx or {}).get("cfg"))
    alerts, held = [], []
    if not knobs["enabled"]:
        return {"alerts": [], "held": [], "now": now_s}
    ctx = ctx or {}
    bucket = _bucket_from(state)
    fired = bucket.get("fired") if isinstance(bucket.get("fired"), dict) else {}
    exited = bucket.get("exit") if isinstance(bucket.get("exit"), dict) else {}
    miss = bucket.get("miss") if isinstance(bucket.get("miss"), dict) else {}
    sighted = bucket.get("sighted") if isinstance(bucket.get("sighted"), dict) else {}
    swarm = bucket.get("swarm") if isinstance(bucket.get("swarm"), dict) else {}
    bucket = {"fired": dict(fired), "exit": dict(exited), "miss": dict(miss),
              "sighted": dict(sighted), "swarm": dict(swarm)}
    launches = [l for l in (ctx.get("launches") or []) if isinstance(l, dict)]
    fresh_swarm = _swarm_index(ctx.get("flags"), launches)
    for pol, ts in fresh_swarm.items():
        prev = parse_time((bucket["swarm"].get(pol) or {}).get("at") if isinstance(bucket["swarm"].get(pol), dict) else None)
        if ts is None and pol in bucket["swarm"]:
            continue
        if prev is None or (ts is not None and ts < prev):
            bucket["swarm"][pol] = {"at": iso(ts) if ts is not None else None}
    rows = _row_by_policy(ctx.get("rows"))
    seen = set()
    rearm_s = knobs["rearm_h"] * 3600
    exit_s = knobs["exit_h"] * 3600

    def consider(launch, row):
        pol = policy_of((launch or {}).get("policyId") or (launch or {}).get("policy") or (row or {}).get("unit") or "")
        if not pol or pol in seen:
            return
        seen.add(pol)
        ticker = _ticker((launch or {}).get("ticker") or (row or {}).get("ticker"))
        if not ticker:
            stored = bucket["fired"].get(pol)
            ticker = stored.get("ticker") if isinstance(stored, dict) else ""
        if not ticker:
            return
        live = _live_s(launch) if launch else None
        swarm_at = None
        remembered = bucket["swarm"].get(pol)
        if isinstance(remembered, dict):
            swarm_at = parse_time(remembered.get("at"))
        is_swarm = pol in fresh_swarm or pol in bucket["swarm"]
        fired_row = bucket["fired"].get(pol)
        if isinstance(fired_row, dict) and pol not in bucket["exit"]:
            alert_at = parse_time(fired_row.get("at"))
            if alert_at is not None and now_s >= alert_at + exit_s:
                name = fired_row.get("ticker") or ticker
                alerts.append({
                    "kind": "sw01_exit", "policy": pol, "ticker": name,
                    "payload": {"reason": "sw01_exit", "names": [name]},
                })
        if isinstance(fired_row, dict):
            return
        if not is_swarm:
            return
        if live is not None:
            prev_s = parse_time(bucket["sighted"].get(pol))
            if prev_s is None or live < prev_s:
                bucket["sighted"][pol] = iso(live)
        origin = []
        if live is not None:
            origin.append(live)
        if swarm_at is not None:
            origin.append(swarm_at)
        sight = parse_time(bucket["sighted"].get(pol))
        if sight is not None:
            origin.append(sight)
        miss_at = parse_time(bucket["miss"].get(pol))
        if miss_at is not None and now_s < miss_at + rearm_s:
            return
        unit = (row or {}).get("unit") or ((launch or {}).get("policyId") or "") + ((launch or {}).get("assetName") or "")
        dist = (ctx.get("dists") or {}).get(unit) if unit else None
        if dist is None and isinstance(row, dict):
            dist = (ctx.get("dists") or {}).get(row.get("unit"))
        token = {
            "policy": pol, "ticker": ticker, "live_s": live, "swarm": True, "origin": origin,
            "row": row or {}, "on_chain": circulating_holders(dist, ctx.get("exclude_tags")),
            "solada": _solada_holders(launch, row), "flow": _flow(ctx.get("pools"), unit),
        }
        try:
            result = gates(token, ctx, now_s, knobs)
        except Exception:
            held.append({"policy": pol, "ticker": ticker, "failed": ["error"], "unknown": []})
            return
        if result["ok"] is True:
            alerts.append({
                "kind": "sw01_candidate", "policy": pol, "ticker": ticker,
                "payload": {"reason": "sw01_candidate", "names": [ticker], "metrics": result["metrics"]},
            })
            bucket["miss"].pop(pol, None)
        elif result["ok"] is False:
            bucket["miss"][pol] = iso(now_s)
            held.append({"policy": pol, "ticker": ticker, "failed": result["failed"], "unknown": result["unknown"]})
        else:
            held.append({"policy": pol, "ticker": ticker, "failed": [], "unknown": result["unknown"]})

    try:
        for launch in launches:
            row = rows.get(policy_of(launch.get("policyId") or ""))
            consider(launch, row)
        for pol, row in rows.items():
            if pol not in seen:
                consider(None, row)
        for pol, fired_row in list(bucket["fired"].items()):
            if pol in seen or not isinstance(fired_row, dict):
                continue
            if pol in bucket["exit"]:
                continue
            alert_at = parse_time(fired_row.get("at"))
            name = fired_row.get("ticker")
            if alert_at is not None and isinstance(name, str) and name and now_s >= alert_at + exit_s:
                alerts.append({"kind": "sw01_exit", "policy": pol, "ticker": name,
                               "payload": {"reason": "sw01_exit", "names": [name]}})
    except Exception:
        held.append({"policy": "", "ticker": "", "failed": ["error"], "unknown": []})
    protect = set(bucket["fired"])
    for key in ("miss", "sighted", "swarm"):
        _cap(bucket[key], protect)
    if persist and isinstance(state, dict):
        if any(bucket.values()):
            state["sw01"] = bucket
        else:
            state.pop("sw01", None)
    alerts.sort(key=lambda a: (a["kind"], a["policy"]))
    return {"alerts": alerts, "held": held, "now": now_s}


def mark_sent(state, alert, now_s):
    """Remember a post the webhook accepted. Not called when the post failed."""
    if not isinstance(state, dict) or not isinstance(alert, dict):
        return
    bucket = state.get("sw01")
    if not isinstance(bucket, dict):
        bucket = {}
        state["sw01"] = bucket
    pol = alert.get("policy")
    if not isinstance(pol, str) or not pol:
        return
    stamp = iso(now_s)
    if alert.get("kind") == "sw01_exit":
        exited = bucket.get("exit") if isinstance(bucket.get("exit"), dict) else {}
        exited[pol] = stamp
        bucket["exit"] = exited
        return
    fired = bucket.get("fired") if isinstance(bucket.get("fired"), dict) else {}
    fired[pol] = {"ticker": alert.get("ticker") or (alert.get("payload") or {}).get("names", [""])[0], "at": stamp}
    bucket["fired"] = fired
    miss = bucket.get("miss")
    if isinstance(miss, dict):
        miss.pop(pol, None)


def deliver(http, cfg, state, decision, url, key, now_s=None):
    """POST each alert. A failed post is left unmarked so the next run retries it.

    An empty URL is recorded and not sent, matching the other pre-filter triggers.
    One bad payload is skipped. The return value is tickers only.
    """
    sent_c, sent_e, failed = [], [], 0
    url = (url or "").strip()
    key = (key or "").strip()
    when = decision.get("now") if isinstance(decision, dict) and now_s is None else now_s
    if when is None:
        when = parse_time(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")) or 0
    for alert in (decision or {}).get("alerts") or []:
        body = alert.get("payload") if isinstance(alert, dict) else None
        if not isinstance(body, dict):
            failed += 1
            continue
        try:
            F.assert_public(body)
            if body.get("reason") == "sw01_candidate":
                metrics = body.get("metrics") or {}
                if set(metrics) != set(METRIC_KEYS):
                    failed += 1
                    continue
        except Exception:
            failed += 1
            continue
        try:
            if not url:
                ok = True
            else:
                hdr = {"Authorization": f"Bearer {key}"} if key else {}
                slot = int((cfg.get("webhook") or {}).get("idempotency_slot_minutes", 120) or 120)
                slot_id = int(when // (slot * 60))
                ident = json.dumps([slot_id, [alert.get("kind"), alert.get("policy")]])
                hdr["Idempotency-Key"] = hashlib.sha256(ident.encode()).hexdigest()[:32]
                ok = bool(http.post_webhook(url, body, hdr))
        except Exception:
            failed += 1
            continue
        if not ok:
            failed += 1
            continue
        mark_sent(state, alert, when)
        name = alert.get("ticker")
        if alert.get("kind") == "sw01_exit":
            sent_e.append(name)
        else:
            sent_c.append(name)
    return {"candidates": sent_c, "exits": sent_e, "failed": failed, "pending": failed}
