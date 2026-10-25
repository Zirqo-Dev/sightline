"""Hard skips: data, safety, execution. Missing data is a skip. Same execution checks for every book."""
import time
from . import config as C

def _get(d, *path):
    for p in path:
        if d is None: return None
        d = d.get(p) if isinstance(d, dict) else None
    return d

def data_check(pair, rc, quote):
    """Return reason or None."""
    if pair is None or rc is None: return "missing_data"
    if quote is None: return "no_route"
    if _get(pair, "liquidity") is None or pair.get("pairCreatedAt") is None: return "missing_data"
    return None

def excluded_accounts(rc):
    known = rc.get("knownAccounts") or {}
    return {k for k, v in known.items() if v.get("type") in ("AMM", "LOCKER")}

def top10_ex_pool(rc):
    ex = excluded_accounts(rc)
    hs = [h for h in rc.get("topHolders") or [] if h.get("owner") not in ex and h.get("address") not in ex]
    return sum(h.get("pct", 0) for h in hs[:10])

def lp_check(rc, pair_address):
    """Pool-type-aware LP lock. Returns reason or None."""
    m = next((m for m in rc.get("markets") or [] if m.get("pubkey") == pair_address), None)
    if m is None: return "missing_data"
    rule = C.LP_LOCK_RULE.get(m.get("marketType"), "check")
    if rule == "bonding_curve": return "not_graduated"
    if rule in ("structural", "not_applicable"): return None
    pct = _get(m, "lp", "lpLockedPct")
    if pct is None: return "missing_data"
    return "lp_unlocked" if pct < C.SKIP_IF["min_lp_locked_pct"] else None

def safety_check(pair, rc, now=None):
    s = C.SKIP_IF; now = now or time.time()
    if rc.get("rugged"): return "rugged"
    if pair.get("dexId") in C.BONDING_DEX_IDS: return "not_graduated"
    if rc.get("mintAuthority") or rc.get("freezeAuthority"): return "authority_open"
    if any(r.get("level") == "danger" and "liquidity" not in r.get("name", "").lower() for r in rc.get("risks") or []):
        pass  # danger flags are scored via score_normalised; kept explicit below
    age_min = (now * 1000 - pair["pairCreatedAt"]) / 60000
    if age_min < s["min_age_min"]: return "too_fresh"
    if age_min > s["max_age_h"] * 60: return "too_old"
    u = s.get("min_liquidity_usd")
    if u is not None and (pair.get("liquidity") or {}).get("usd", 0) < u: return "thin_pool"
    q = s.get("min_quote_liquidity_sol")
    if q is not None and (pair.get("liquidity") or {}).get("quote", 0) < q: return "thin_pool"
    r = lp_check(rc, pair.get("pairAddress"))
    if r: return r
    creator = rc.get("creator")
    dev = sum(h.get("pct", 0) for h in rc.get("topHolders") or [] if h.get("owner") == creator)
    if dev > s["max_dev_pct"]: return "dev_holds"
    if top10_ex_pool(rc) > s["max_top10_pct"]: return "top10_heavy"
    sn = rc.get("score_normalised")
    if sn is None: return "missing_data"
    if sn > s["max_risk_score_norm"]: return "risk_score"
    return None

def impact_pct(quote):
    """Jupiter priceImpactPct is a fraction-as-string (0.049 = 4.9%)."""
    return float(quote.get("priceImpactPct", "nan")) * 100

def execution_check(pair, quote, ticket_sol=None):
    e = C.EXEC; ticket = ticket_sol or C.TICKET_SOL
    if not quote or not quote.get("routePlan"): return "no_route"
    if impact_pct(quote) > e["max_impact_pct"]: return "impact"
    qliq = (pair.get("liquidity") or {}).get("quote")
    if not qliq or ticket > e["max_ticket_share"] * qliq + 1e-12: return "ticket_share"  # share of SOL side
    if (_get(pair, "txns", "h1", "sells") or 0) < e["min_sells_1h"]: return "nobody_can_sell"
    return None

def classify(pair, rc, quote, now=None):
    """-> (skip_type, reason) ; skip_type in data/safety/execution/None"""
    r = data_check(pair, rc, quote)
    if r: return ("data", r)
    r = safety_check(pair, rc, now)
    x = execution_check(pair, quote)
    if x: return ("execution", x)
    if r: return ("safety", r)
    return (None, None)
