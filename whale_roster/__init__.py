"""Cardano whale roster. Research only: never a buy signal.

Private file (sightline-private whales/roster.json): codename, raw wallets,
role, added date, source, evidence, status, and a social-account slot.
Public desk-data file solada-prefilter/whale_roster.json: codename, role,
status, scores, and eligible_for_active with its reason. No wallets.

Readers of the run log accept whales[] and the previous single-object key.
Writers emit whales[] only.

SPD-81 scores every roster whale with the lead's +1h/+6h/+24h hit rule.
Candidate and active rows are built from tagged big_moves (and tagged
liquidity moves), deduped across desk-data runs. A checkpoint is a hit, a
wrong-way miss, or unobserved. Rates and the eligibility count use observed
checkpoints only. Past checkpoints use the nearest desk-data ADA/qty print.
eligible_for_active is a research flag. Nothing in this module promotes a
whale. Promotion stays a manual owner decision. Whales remain
confirmation-only flags. Candidates that fall off the bar become dormant.
"""
import hashlib
import json
import os
import re

SCHEMA = 1
PUBLIC_KIND = "whale_roster_public"
PRIVATE_KIND = "whale_roster"
PRIVATE_REL = os.path.join("whales", "roster.json")
PUBLIC_NAME = "whale_roster.json"
CACHE_ROSTER = "whale-roster.json"
CACHE_EVENTS = "whale-events.json"
LEAD_CODENAME = "Grumpy Walrus"
LEAD_ROLE = "lead"
LEAD_ADDED = "2026-10-26"
MIN_SAMPLE = 8
MIN_TOKENS = 2
MIN_ADA = 2000
# SPD-81 router / market-maker exclusion. A tag with at least this many
# moves, whose buy ADA and sell ADA are within MM_BALANCE of each other
# (|buy-sell| / max(buy, sell) <= MM_BALANCE, both sides above zero), is
# not proposed as a candidate. Box research flagged Brisk Stoat (387 moves,
# 131k ADA, buy and sell within 10%, hit rate about 50% at 1h, 6h, and 24h)
# as one of these. The filter does not change a desk rule and does not drop
# a whale the owner already kept.
MM_MIN_MOVES = 100
MM_BALANCE = 0.10
# SPD-81 pre-registered promotion bar. eligible_for_active uses the LIVE
# tally only: each of 6h and 24h has at least PROMOTE_MIN_EVENTS observed
# checkpoints and a hit rate strictly above PROMOTE_MIN_HIT_RATE. Observed
# means a hit or a wrong-way miss. Unobserved checkpoints are not in that
# count and are not misses. The desk-data backfill is a separate,
# approximate tally and never fills the live one. promising_backfill is
# true when that approximate tally alone would clear the same bar. Rates
# stay blank under MIN_SAMPLE observed checkpoints on that horizon and in
# that tally. Nothing here sets status to active. Promotion stays a manual
# owner decision.
PROMOTE_MIN_EVENTS = 25
PROMOTE_MIN_HIT_RATE = 0.55
# SPD-81 candidacy floors. The first cut (two tokens, or 2,000 ADA, no
# recency) made 48 names, most with 2-10 moves. A candidate now needs every
# one of these: MIN_CANDIDATE_MOVES deduped moves, MIN_ADA across at least
# MIN_TOKENS watchlist tokens, and a move inside CANDIDATE_ACTIVE_S. The
# list is then the top MAX_CANDIDATES by total ADA (a tie goes to more
# moves). Research filter only. It does not change a desk rule.
MIN_CANDIDATE_MOVES = 10
CANDIDATE_ACTIVE_S = 14 * 24 * 3600
MAX_CANDIDATES = 12
# Nearest desk-data print used as the price (ADA/qty) or the pool size.
# A horizon with no print inside its tolerance is unobserved, never a miss.
# The baseline print at the move uses the 1h tolerance.
OBS_TOL_S = {"at_move": 20 * 60, "h1": 20 * 60, "h6": 60 * 60, "h24": 3 * 3600}
H1_S = 3600
H6_S = 6 * 3600
H24_S = 24 * 3600
H24_SLACK_S = 6 * 3600
# Same windows as scripts/buzz_outcomes.py: a run prices the one open window.
WINDOWS = (
    ("at_move", 0, H1_S),
    ("h1", H1_S, H6_S),
    ("h6", H6_S, H24_S),
    ("h24", H24_S, H24_S + H24_SLACK_S),
)
HORIZONS = ("h1", "h6", "h24")
# Previous public names. Readers accept them. Writers do not emit them.
OLD_LOG_KEY = "founder_whale"
OLD_FLAG = "founder_whale_move"
OLD_ROLE = "founder_whale"
NEW_FLAG = "whale_move"
NEW_ROLE = "lead_whale"
# w- is the flags/ledger hash. m- is the big_moves hash of the same owner
# string. The 8 hex digits match when both hashed the same stake key.
_TAG = re.compile(r"^[wm]-[0-9a-f]{8}$")
_STAKE = re.compile(r"^stake1[02-9ac-hj-np-z]{20,}$")
_ADDR = re.compile(r"^addr1[02-9ac-hj-np-z]{20,}$")
_LEAK = re.compile(r"stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,}")
_TICKER = re.compile(r"[A-Z][A-Z0-9-]{0,15}")

ADJECTIVES = (
    "Sleepy", "Bold", "Quiet", "Swift", "Rusty", "Lucky", "Calm", "Dapper",
    "Mossy", "Brisk", "Jolly", "Wry", "Dusty", "Nimble", "Cosy", "Plucky",
    "Sandy", "Icy", "Merry", "Keen",
)
ANIMALS = (
    "Otter", "Badger", "Heron", "Newt", "Puffin", "Moose", "Corgi", "Yak",
    "Ibis", "Ferret", "Gecko", "Marlin", "Osprey", "Quail", "Robin", "Stoat",
    "Tapir", "Urchin", "Vole", "Lynx",
)


def parse_time(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 1_000_000_000 else None
    if not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def iso(ts):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def change_pct(new, old):
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def clean_num(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return num


def is_tag(value):
    return isinstance(value, str) and bool(_TAG.fullmatch(value.strip().lower()))


def tag_body(value):
    """8 hex digits shared by a w- tag, an m- tag, and the hash of that owner."""
    if not is_tag(value):
        return None
    return value.strip().lower().split("-", 1)[1]


def _watch_ticker(value):
    if not isinstance(value, str):
        return None
    name = value.strip().upper()
    if not _TICKER.fullmatch(name) or name in ("ADA", "LP"):
        return None
    return name


def is_wallet(value):
    if not isinstance(value, str):
        return False
    text = value.strip()
    return bool(_STAKE.fullmatch(text) or _ADDR.fullmatch(text))


def codename_for(key, taken=()):
    """Adjective Animal, stable for the same wallet id. The lead name is reserved."""
    taken = set(taken)
    digest = hashlib.sha256(str(key).encode()).digest()
    base = int.from_bytes(digest[:4], "big")
    span = len(ADJECTIVES) * len(ANIMALS)
    for bump in range(span):
        n = base + bump
        name = ADJECTIVES[n % len(ADJECTIVES)] + " " + ANIMALS[(n // len(ADJECTIVES)) % len(ANIMALS)]
        if name == LEAD_CODENAME or name in taken:
            continue
        return name
    return "Keen Lynx"


def _blank(due):
    return {
        "status": "pending",
        "due_at": iso(due) if isinstance(due, (int, float)) else due,
        "observed_at": None,
        "price": None,
        "liquidity": None,
        "price_change_pct": None,
        "liquidity_change_pct": None,
        "reason": "not due",
    }


def classify_row(row):
    """accumulate, trim, exit, or None. Ledger rows use signed qty and kind."""
    if not isinstance(row, dict):
        return None
    kind = str(row.get("kind") or "")
    direction = str(row.get("direction") or "").lower()
    token = str(row.get("token") or row.get("ticker") or "")
    if not token or token.upper() == "ADA":
        return None
    qty = row.get("qty")
    if isinstance(qty, bool):
        qty = None
    if kind == "lp_remove" or direction == "exit" or row.get("closed") is True:
        return "exit"
    inbound = direction in ("in", "buy", "add") or kind in ("transfer_in", "lp_add")
    outbound = direction in ("out", "sell", "remove") or kind == "transfer_out"
    if isinstance(qty, (int, float)):
        if qty > 0:
            inbound = True
        elif qty < 0:
            outbound = True
    if kind in ("order_open", "order_cancel"):
        return None
    if outbound and (row.get("position_after") == 0):
        return "exit"
    if inbound and not outbound:
        return "accumulate"
    if outbound and not inbound:
        return "trim"
    return None


def _cp(event, name):
    if name == "at_move":
        return event["at_move"]
    return event["followups"][name]


def new_event(move, codename, unit):
    at = move["at"]
    ident = "|".join((codename, move.get("ticker") or "", str(at), move["action"], move.get("tx") or ""))
    event = {
        "id": hashlib.sha256(ident.encode()).hexdigest()[:16],
        "codename": codename,
        "ticker": move.get("ticker") or move.get("token"),
        "action": move["action"],
        "at": at,
        "unit": unit,
        "tx": move.get("tx") or "",
        "at_move": _blank(at),
        "followups": {name: _blank(at + offset) for name, offset in (("h1", H1_S), ("h6", H6_S), ("h24", H24_S))},
    }
    return event


def _fill(event, name, cp, now, reading):
    if not reading or not reading.get("answered"):
        cp["status"] = "pending"
        cp["reason"] = (reading or {}).get("reason") or "market unread"
        return
    cp["status"] = "filled"
    cp["observed_at"] = iso(now)
    cp["price"] = clean_num(reading.get("price"))
    cp["liquidity"] = clean_num(reading.get("liquidity"))
    cp["reason"] = None if cp["price"] is not None else (reading.get("reason") or "no ada price")
    if name == "at_move":
        cp["price_change_pct"] = None
        cp["liquidity_change_pct"] = None
        return
    base = event["at_move"]
    if base.get("status") != "filled":
        cp["price_change_pct"] = None
        cp["liquidity_change_pct"] = None
        cp["reason"] = "no price at move"
        return
    cp["price_change_pct"] = change_pct(cp["price"], base.get("price"))
    cp["liquidity_change_pct"] = change_pct(cp["liquidity"], base.get("liquidity"))
    if cp["price_change_pct"] is None and cp["liquidity_change_pct"] is None and not cp["reason"]:
        cp["reason"] = "no price at move"


def _mark_outcome(event, name):
    """hit, miss, or unobserved. A flat or missing series is unobserved."""
    if name == "at_move":
        return
    cp = _cp(event, name)
    if cp.get("status") != "filled":
        cp["outcome"] = "unobserved"
        return
    vote = favorable(event.get("action"), cp.get("price_change_pct"), cp.get("liquidity_change_pct"))
    if vote is True:
        cp["outcome"] = "hit"
    elif vote is False:
        cp["outcome"] = "miss"
    else:
        cp["outcome"] = "unobserved"


def advance_event(event, now, quote, one_per_run=True):
    """Fill the one open window. A closed window with no reading is unobserved.

    quote(unit) -> {answered, price, liquidity, reason}. One open window at a
    time, matching the buzz outcome tracker. Unobserved is not a miss: it
    stays out of hit rates and out of the eligibility count.
    """
    filled = False
    at = event["at"]
    for name, start, end in WINDOWS:
        cp = _cp(event, name)
        if cp.get("outcome") in ("hit", "miss"):
            continue
        if cp["status"] in ("filled", "unobserved", "missed"):
            if cp["status"] == "missed":
                cp["status"] = "unobserved"
                cp["outcome"] = "unobserved"
                cp["reason"] = "no observation in the window"
            continue
        if now < at + start:
            cp["status"] = "pending"
            cp["reason"] = "not due"
            cp["outcome"] = "unobserved"
            continue
        if now >= at + end:
            cp["status"] = "unobserved"
            cp["outcome"] = "unobserved"
            cp["observed_at"] = None
            cp["reason"] = "no observation in the window"
            continue
        if one_per_run and filled:
            cp["status"] = "pending"
            cp["reason"] = "deferred; one checkpoint per run"
            cp["outcome"] = "unobserved"
            continue
        filled = True
        reading = quote(event.get("unit")) if event.get("unit") else {"answered": False, "reason": "full token id unavailable"}
        _fill(event, name, cp, now, reading)
        _mark_outcome(event, name)
        if cp.get("outcome") in ("hit", "miss") and cp.get("source") != "desk-data":
            cp["source"] = "live"


def favorable(action, price_pct, liq_pct):
    """True when every measured series moves with the whale. None when unpriced."""
    direction = 1 if action == "accumulate" else -1
    votes = []
    for pct in (price_pct, liq_pct):
        if pct is None or pct == 0:
            continue
        votes.append((pct > 0) == (direction > 0))
    if not votes:
        return None
    return all(votes)


def checkpoint_outcome(action, cp):
    """hit, miss, or unobserved. Legacy filled rows with no outcome still score."""
    if not isinstance(cp, dict):
        return "unobserved"
    outcome = cp.get("outcome")
    if outcome in ("hit", "miss", "unobserved"):
        return outcome
    if cp.get("status") != "filled":
        return "unobserved"
    vote = favorable(action, cp.get("price_change_pct"), cp.get("liquidity_change_pct"))
    if vote is True:
        return "hit"
    if vote is False:
        return "miss"
    return "unobserved"


def _empty_band():
    return {name: {"n_observed": 0, "hits": 0, "hit_rate": None} for name in HORIZONS}


def _finish_band(band, approximate=False):
    for name in HORIZONS:
        row = band[name]
        observed = row["n_observed"]
        row["hit_rate"] = round(row["hits"] / observed, 3) if observed >= MIN_SAMPLE else None
    if approximate:
        band["approximate"] = True
        band["note"] = "Approximate backfill from desk-data prints (ADA/qty and pool ADA), not a live quote."
    return band


def _mirror_live(scores):
    """Flat horizon fields stay equal to the live tally for older readers."""
    live = scores.get("live") or _empty_band()
    for name in HORIZONS:
        row = live.get(name) or {}
        observed = int(row.get("n_observed") or 0)
        hits = int(row.get("hits") or 0)
        scores["n_observed_" + name] = observed
        scores["priced_" + name] = observed
        scores["hits_" + name] = hits
        scores["hit_rate_" + name] = row.get("hit_rate")
    scores["small_sample"] = all(scores["n_observed_" + name] < MIN_SAMPLE for name in HORIZONS)
    return scores


def empty_scores(social=None):
    scores = {
        "events": 0,
        "accumulate": 0,
        "trim": 0,
        "exit": 0,
        "live": _empty_band(),
        "backfill": _finish_band(_empty_band(), approximate=True),
    }
    _mirror_live(scores)
    scores["social_moves_matched"] = social if isinstance(social, int) and not isinstance(social, bool) else None
    return scores


def summarize(events, social_by_name=None):
    """Per-codename live and backfill tallies.

    Live checkpoints are the forward quote path (source live, or a filled
    row with no source from the private cache). Backfill checkpoints are
    desk-data prints (source desk-data) and are approximate. Unobserved
    checkpoints are in neither tally. Flat horizon fields mirror live.
    A rate stays null under MIN_SAMPLE observed checkpoints in that tally
    on that horizon.
    """
    social_by_name = social_by_name or {}
    grouped = {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        name = event.get("codename")
        if not name:
            continue
        grouped.setdefault(name, []).append(event)
    out = {}
    for name, rows in grouped.items():
        scores = empty_scores(social_by_name.get(name))
        scores["events"] = len(rows)
        for event in rows:
            action = event.get("action")
            if action in ("accumulate", "trim", "exit"):
                scores[action] += 1
            for horizon in HORIZONS:
                cp = (event.get("followups") or {}).get(horizon) or {}
                outcome = checkpoint_outcome(action, cp)
                if outcome not in ("hit", "miss"):
                    continue
                band = scores["backfill"] if cp.get("source") == "desk-data" else scores["live"]
                band[horizon]["n_observed"] += 1
                if outcome == "hit":
                    band[horizon]["hits"] += 1
        _finish_band(scores["live"])
        _finish_band(scores["backfill"], approximate=True)
        _mirror_live(scores)
        out[name] = scores
    return out


def published_live_band(previous, codename=None):
    """Live horizon tally already published for one whale, or None.

    The lead's published file is the private-cache tally. Old files store
    it in the flat horizon fields. Newer files store it under scores.live.
    """
    codename = codename or LEAD_CODENAME
    if not isinstance(previous, dict):
        return None
    whale = None
    for row in previous.get("whales") or []:
        if isinstance(row, dict) and row.get("codename") == codename:
            whale = row
            break
    if not whale:
        return None
    scores = whale.get("scores") if isinstance(whale.get("scores"), dict) else {}
    band = _empty_band()
    live = scores.get("live") if isinstance(scores.get("live"), dict) else None
    found = False
    for name in HORIZONS:
        if live and isinstance(live.get(name), dict):
            raw_n = live[name].get("n_observed")
            raw_hits = live[name].get("hits")
        else:
            raw_n = scores.get("n_observed_" + name)
            if isinstance(raw_n, bool) or not isinstance(raw_n, (int, float)):
                raw_n = scores.get("priced_" + name)
            raw_hits = scores.get("hits_" + name)
        if isinstance(raw_n, bool) or not isinstance(raw_n, (int, float)):
            raw_n = 0
        if isinstance(raw_hits, bool) or not isinstance(raw_hits, (int, float)):
            raw_hits = 0
        band[name]["n_observed"] = int(raw_n)
        band[name]["hits"] = int(raw_hits)
        if band[name]["n_observed"] or band[name]["hits"]:
            found = True
    if not found:
        return None
    return _finish_band(band)


def _lead_has_live_checkpoints(events):
    for event in events or []:
        if not isinstance(event, dict) or event.get("codename") != LEAD_CODENAME:
            continue
        for name in HORIZONS:
            cp = (event.get("followups") or {}).get(name) or {}
            if cp.get("source") == "desk-data":
                continue
            if checkpoint_outcome(event.get("action"), cp) in ("hit", "miss"):
                return True
    return False


def restore_published_live(scores_by_name, events, previous):
    """Keep the lead's published live tally. Backfill must not replace it.

    Used when this run has no live checkpoints in the event cache. A cache
    that already holds live fills is left as counted.
    """
    if _lead_has_live_checkpoints(events):
        return scores_by_name
    preserved = published_live_band(previous, LEAD_CODENAME)
    if not preserved:
        return scores_by_name
    lead = (scores_by_name or {}).get(LEAD_CODENAME)
    if not isinstance(lead, dict):
        return scores_by_name
    lead["live"] = preserved
    _mirror_live(lead)
    return scores_by_name


def moves_from_ledger(doc):
    moves = []
    if not isinstance(doc, dict):
        return moves
    for row in doc.get("rows") or []:
        action = classify_row(row)
        if not action:
            continue
        at = parse_time(row.get("time") or row.get("at"))
        token = row.get("token") or row.get("ticker")
        if at is None or not token:
            continue
        moves.append({
            "at": at,
            "ticker": str(token),
            "action": action,
            "tx": row.get("tx_short") or "",
            "tag": row.get("tag"),
        })
    return moves


def _add(bucket_tokens, bucket_ada, tag, ticker, ada):
    if not is_tag(tag) or not ticker:
        return
    name = _watch_ticker(ticker)
    if not name:
        return
    bucket_tokens.setdefault(tag.strip().lower(), set()).add(name)
    if isinstance(ada, (int, float)) and not isinstance(ada, bool) and ada > 0:
        key = tag.strip().lower()
        bucket_ada[key] = bucket_ada.get(key, 0) + float(ada)


def market_maker(moves_n, buy_ada, sell_ada):
    """True for a router-like tag. See MM_MIN_MOVES and MM_BALANCE."""
    if isinstance(moves_n, bool) or not isinstance(moves_n, (int, float)):
        return False
    if moves_n < MM_MIN_MOVES:
        return False
    buy = buy_ada if isinstance(buy_ada, (int, float)) and not isinstance(buy_ada, bool) else 0
    sell = sell_ada if isinstance(sell_ada, (int, float)) and not isinstance(sell_ada, bool) else 0
    if buy <= 0 or sell <= 0:
        return False
    return abs(buy - sell) / max(buy, sell) <= MM_BALANCE


def _tally(stats, tag, direction, ada):
    if not is_tag(tag):
        return
    key = tag.strip().lower()
    row = stats.setdefault(key, {"n": 0, "buy": 0.0, "sell": 0.0})
    row["n"] += 1
    amount = float(ada) if isinstance(ada, (int, float)) and not isinstance(ada, bool) and ada > 0 else 0.0
    side = str(direction or "").lower()
    if side in ("in", "buy", "add"):
        row["buy"] += amount
    elif side in ("out", "sell", "remove"):
        row["sell"] += amount


def router_tags(doc):
    """Tags in one big_moves document that look like routers. Sorted."""
    stats = {}
    for row in (doc or {}).get("moves") or []:
        if isinstance(row, dict):
            _tally(stats, row.get("tag") or row.get("actor_tag"), row.get("direction"), row.get("ada"))
    return sorted(tag for tag, row in stats.items() if market_maker(row["n"], row["buy"], row["sell"]))


def _big_move_docs(log, big_moves):
    """Explicit big_moves docs win. Otherwise read big_moves off the log.

    Passing a document (even an empty one) does not also read the log summary.
    Those summaries repeat the same trade across history rows and would
    double-count ADA next to the desk-data file.
    """
    if isinstance(big_moves, list):
        return [d for d in big_moves if isinstance(d, dict)]
    if isinstance(big_moves, dict):
        return [big_moves]
    if not isinstance(log, dict):
        return []
    docs = []

    def take(snap):
        if not isinstance(snap, dict):
            return
        block = snap.get("big_moves")
        if isinstance(block, dict):
            docs.append(block)
        elif isinstance(block, list):
            docs.append({"moves": block})
        flows = snap.get("liquidity_flows")
        if isinstance(flows, dict) and flows.get("big_moves"):
            docs.append({"moves": flows.get("big_moves")})

    take(log)
    for snap in log.get("history") or []:
        take(snap)
    return docs


def discover(log, liquidity, lead_tags, shortlist, big_moves=None, now=None):
    """Candidate tags from clusters, heat, liquidity, and big moves. Never active.

    A tag qualifies only when it has MIN_CANDIDATE_MOVES deduped moves and
    MIN_ADA across at least MIN_TOKENS watchlist tokens, and (when now is
    set) a move inside CANDIDATE_ACTIVE_S. Watchlist tokens are the shortlist
    plus tickers big_moves was asked to scan. The result is the top
    MAX_CANDIDATES by total ADA, ties broken by more moves. The lead tag is
    skipped. A router-like tag is skipped. Nothing returned here is active.
    """
    tokens, ada, stats = {}, {}, {}
    v2 = (log or {}).get("cardano_v2") if isinstance(log, dict) else None
    v2 = v2 if isinstance(v2, dict) else {}
    for cluster in v2.get("clusters") or []:
        if not isinstance(cluster, dict):
            continue
        for tag in cluster.get("wallets") or []:
            _add(tokens, ada, tag, cluster.get("ticker"), 0)
    heat = v2.get("heat") if isinstance(v2.get("heat"), dict) else {}
    for row in heat.get("wallets") or heat.get("rows") or []:
        if not isinstance(row, dict):
            continue
        cells = row.get("cells") if isinstance(row.get("cells"), dict) else {}
        for ticker, cell in cells.items():
            amount = cell.get("ada") if isinstance(cell, dict) else None
            _add(tokens, ada, row.get("tag"), ticker, amount)
    short = {str(t).upper() for t in (shortlist or []) if _watch_ticker(str(t))}
    tagged = []
    for move in (liquidity or {}).get("moves") or []:
        if isinstance(move, dict):
            tagged.append(move)
    for doc in _big_move_docs(log, big_moves):
        cov = doc.get("coverage")
        if isinstance(cov, dict):
            for name in cov:
                ticker = _watch_ticker(name)
                if ticker:
                    short.add(ticker)
        tagged.extend(doc.get("moves") or [])
    last_at = {}
    watch_n = {}
    watch_ada = {}
    for move in dedupe_rows(tagged):
        tag = move.get("tag") or move.get("actor_tag")
        _add(tokens, ada, tag, move.get("ticker"), move.get("ada"))
        _tally(stats, tag, move.get("direction") or move.get("kind"), move.get("ada"))
        if not is_tag(tag):
            continue
        key = tag.strip().lower()
        at = parse_time(move.get("time") or move.get("at"))
        if isinstance(at, (int, float)) and at > last_at.get(key, 0):
            last_at[key] = at
        ticker = _watch_ticker(move.get("ticker") or "")
        amount = move.get("ada")
        amount = float(amount) if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0 else 0.0
        if ticker and (not short or ticker in short):
            watch_n[key] = watch_n.get(key, 0) + 1
            watch_ada[key] = watch_ada.get(key, 0.0) + amount
    routers = set()
    for tag, row in stats.items():
        if market_maker(row["n"], row["buy"], row["sell"]):
            body = tag_body(tag)
            if body:
                routers.add(body)
    lead = set()
    for tag in lead_tags or []:
        if not is_tag(tag):
            continue
        body = tag_body(tag)
        lead.add(tag.strip().lower())
        if body:
            lead.add("w-" + body)
            lead.add("m-" + body)
    ranked = []
    cutoff = None if now is None else now - CANDIDATE_ACTIVE_S
    for tag, names in tokens.items():
        if tag in lead or tag_body(tag) in routers:
            continue
        on_list = [n for n in names if not short or n in short]
        if len(on_list) < MIN_TOKENS:
            continue
        if watch_n.get(tag, 0) < MIN_CANDIDATE_MOVES:
            continue
        if watch_ada.get(tag, 0) < MIN_ADA:
            continue
        if cutoff is not None and last_at.get(tag, 0) < cutoff:
            continue
        ranked.append((ada.get(tag, 0), watch_n.get(tag, 0), tag))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [tag for _ada, _count, tag in ranked[:MAX_CANDIDATES]]


def _whale(codename, role, status, added, source, evidence, wallets=None, tags=None, social=None):
    return {
        "codename": codename,
        "wallets": list(wallets or []),
        "tags": list(tags or []),
        "role": role,
        "added": added,
        "source": source,
        "evidence": list(evidence or []),
        "status": status,
        "social": social if isinstance(social, dict) else {"accounts": []},
    }


def empty_roster():
    return {"schema": SCHEMA, "private": True, "kind": PRIVATE_KIND, "whales": []}


def _ids(whale):
    found = set()
    for wallet in whale.get("wallets") or []:
        if is_wallet(wallet):
            text = wallet.strip()
            found.add("wallet:" + text)
            # Same 8 hex as a w- or m- tag of this owner. Used only to merge
            # rows in memory. The wallet itself is not copied onto public output.
            found.add("id:" + hashlib.sha256(text.encode()).hexdigest()[:8])
    for tag in whale.get("tags") or []:
        if is_tag(tag):
            found.add("tag:" + tag.strip().lower())
            body = tag_body(tag)
            if body:
                found.add("id:" + body)
    name = whale.get("codename")
    if isinstance(name, str) and name.strip():
        found.add("name:" + name.strip())
    return found


def _find(roster, whale):
    want = _ids(whale)
    for row in roster.get("whales") or []:
        if _ids(row) & want:
            return row
    return None


def _union_list(left, right):
    out = []
    for item in list(left or []) + list(right or []):
        if item not in out:
            out.append(item)
    return out


def _merge_status(left, right):
    states = {left, right}
    if "dropped" in states:
        return "dropped"
    if "active" in states:
        return "active"
    if "dormant" in states:
        return "dormant"
    return "candidate"


def _merge_social(left, right):
    accounts = []
    seen = set()
    for block in (left, right):
        rows = (block or {}).get("accounts") if isinstance(block, dict) else None
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            key = (str(row.get("network") or ""), str(row.get("handle") or ""))
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            accounts.append({"network": key[0], "handle": key[1]})
    return {"accounts": accounts}


def merge_whale(left, right):
    """Union one whale. Dropped stays dropped. Codenames already on the left win."""
    if left is None:
        return json.loads(json.dumps(right))
    if right is None:
        return json.loads(json.dumps(left))
    name = left.get("codename") or right.get("codename")
    role = "lead" if "lead" in (left.get("role"), right.get("role")) else (left.get("role") or right.get("role") or "watch")
    added = left.get("added") or right.get("added")
    return _whale(
        name, role, _merge_status(left.get("status"), right.get("status")),
        added, left.get("source") or right.get("source") or "",
        _union_list(left.get("evidence"), right.get("evidence")),
        _union_list(left.get("wallets"), right.get("wallets")),
        _union_list(left.get("tags"), right.get("tags")),
        _merge_social(left.get("social"), right.get("social")),
    )


def union_roster(local, remote):
    """Union two private rosters. Remote-only whales are kept. Never auto-activates."""
    base = empty_roster()
    rows = []
    for src in (local, remote):
        if not isinstance(src, dict):
            continue
        for whale in src.get("whales") or []:
            if not isinstance(whale, dict) or not whale.get("codename"):
                continue
            hit = None
            want = _ids(whale)
            for i, row in enumerate(rows):
                if _ids(row) & want:
                    hit = i
                    break
            if hit is None:
                rows.append(json.loads(json.dumps(whale)))
            else:
                rows[hit] = merge_whale(rows[hit], whale)
    base["whales"] = rows
    return base


def ensure_lead(roster, wallet, tag):
    """Seed Grumpy Walrus once. An existing row keeps its status."""
    lead = _whale(
        LEAD_CODENAME, "lead", "active", LEAD_ADDED, "desk config", ["whale ledger"],
        [wallet] if is_wallet(wallet) else [],
        [tag.lower()] if is_tag(tag) else [],
    )
    found = _find(roster, lead)
    if found is None:
        roster.setdefault("whales", []).append(lead)
        return roster
    found["wallets"] = _union_list(found.get("wallets"), lead["wallets"])
    found["tags"] = _union_list(found.get("tags"), lead["tags"])
    if not found.get("codename"):
        found["codename"] = LEAD_CODENAME
    if found.get("role") not in ("lead", "watch"):
        found["role"] = "lead"
    if "whale ledger" not in (found.get("evidence") or []):
        found.setdefault("evidence", []).append("whale ledger")
    return roster


def settle_candidates(roster, qualified_tags):
    """Park candidates that missed the bar. Do not delete them and do not activate them.

    A candidate whose tag is outside qualified_tags becomes dormant. A dormant
    whale that is back inside the list returns to candidate. That is a return
    to the watch list, not a promotion to active. Lead, active, and dropped
    rows are left as they are.
    """
    qualified = {tag_body(tag) for tag in qualified_tags or [] if tag_body(tag)}
    for whale in (roster or {}).get("whales") or []:
        if not isinstance(whale, dict):
            continue
        if whale.get("role") == "lead" or whale.get("codename") == LEAD_CODENAME:
            continue
        if whale.get("status") in ("active", "dropped"):
            continue
        bodies = set()
        for ident in _ids(whale):
            if ident.startswith("id:"):
                bodies.add(ident[3:])
        inside = bool(bodies & qualified)
        if whale.get("status") == "candidate" and not inside:
            whale["status"] = "dormant"
        elif whale.get("status") == "dormant" and inside:
            whale["status"] = "candidate"
    return roster


def add_candidates(roster, tags, evidence, reserved=()):
    """Insert candidates. Never changes an existing whale to active.

    reserved codenames (router-like tags) stay unused so a later tag does
    not inherit a label research already set aside. Status is never set
    to active here.
    """
    taken = [w.get("codename") for w in roster.get("whales") or []]
    for name in reserved or ():
        if isinstance(name, str) and name and name not in taken:
            taken.append(name)
    for tag in tags:
        if not is_tag(tag):
            continue
        probe = _whale("placeholder", "watch", "candidate", "", "", [], tags=[tag.lower()])
        if _find(roster, probe):
            continue
        name = codename_for(tag.lower(), taken)
        taken.append(name)
        roster.setdefault("whales", []).append(_whale(
            name, "watch", "candidate", "", "desk-data clusters", evidence, tags=[tag.lower()],
        ))
    return roster


def read_log_whales(log):
    """Public log whales. whales[] wins. The previous single object is the fallback."""
    if not isinstance(log, dict):
        return []
    rows = log.get("whales")
    if isinstance(rows, list) and rows:
        out = []
        for row in rows:
            if isinstance(row, dict):
                out.append(_public_row(row))
        return [r for r in out if r]
    old = log.get(OLD_LOG_KEY)
    if isinstance(old, dict):
        row = _public_row(old)
        return [row] if row else []
    return []


def _public_row(row):
    name = row.get("codename") or row.get("handle") or ""
    if not isinstance(name, str):
        name = ""
    name = name.strip()
    if name.startswith("$") or name.startswith("@") or "founder" in name.lower():
        name = ""
    tag = row.get("tag") or row.get("address") or ""
    tag = tag.strip().lower() if isinstance(tag, str) and is_tag(tag) else ""
    net = row.get("net_flow_ada")
    net = net if isinstance(net, (int, float)) and not isinstance(net, bool) else None
    role = row.get("role")
    if role == OLD_ROLE or role == NEW_ROLE:
        role = "lead"
    if role not in ("lead", "watch"):
        role = "lead" if name == LEAD_CODENAME else "watch"
    status = row.get("status") if row.get("status") in ("candidate", "active", "dropped") else "active"
    if not name and net is None and not tag:
        return None
    return {"codename": name, "role": role, "status": status, "net_flow_ada": net, "tag": tag}


def emit_log_whales(rows):
    """Shape writers should put on the run log. No previous key."""
    whales = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("codename"):
            continue
        item = {
            "codename": row["codename"],
            "role": row.get("role") if row.get("role") in ("lead", "watch") else "watch",
            "status": row.get("status") if row.get("status") in ("candidate", "active", "dropped") else "candidate",
        }
        if isinstance(row.get("net_flow_ada"), (int, float)) and not isinstance(row.get("net_flow_ada"), bool):
            item["net_flow_ada"] = row["net_flow_ada"]
        whales.append(item)
    return {"whales": whales}


def flag_kind(kind):
    """True when a flag kind is the whale-move flag, old or new."""
    return kind in (NEW_FLAG, OLD_FLAG)


def _band_numbers(scores, which, horizon):
    """(n_observed, hit_rate) from live or backfill, else the flat live fields."""
    band = (scores or {}).get(which)
    if isinstance(band, dict) and isinstance(band.get(horizon), dict):
        row = band[horizon]
        observed = row.get("n_observed")
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            observed = 0
        return int(observed), row.get("hit_rate")
    if which == "live":
        return _observed_count(scores, horizon), (scores or {}).get("hit_rate_" + horizon)
    return 0, None


def _observed_count(scores, horizon):
    value = (scores or {}).get("n_observed_" + horizon)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return int(value)


def _bar_result(scores, which):
    """True when one tally clears the pre-registered bar on its own."""
    n6, r6 = _band_numbers(scores, which, "h6")
    n24, r24 = _band_numbers(scores, which, "h24")

    def rate_ok(rate):
        return isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > PROMOTE_MIN_HIT_RATE

    return n6 >= PROMOTE_MIN_EVENTS and n24 >= PROMOTE_MIN_EVENTS and rate_ok(r6) and rate_ok(r24), n6, r6, n24, r24


def promotion_eligibility(scores):
    """(eligible_for_active, reason). The flag does not change status.

    The bar is applied to the LIVE tally only. Backfill never makes a whale
    eligible. A blank live rate does not pass. Promotion stays a manual
    owner decision.
    """
    scores = scores or {}
    ok, n6, r6, n24, r24 = _bar_result(scores, "live")

    def rate_ok(rate):
        return isinstance(rate, (int, float)) and not isinstance(rate, bool) and rate > PROMOTE_MIN_HIT_RATE

    ok_6n = n6 >= PROMOTE_MIN_EVENTS
    ok_24n = n24 >= PROMOTE_MIN_EVENTS
    ok_6 = rate_ok(r6)
    ok_24 = rate_ok(r24)

    def shown(rate):
        return "blank" if rate is None else str(rate)

    def horizon_text(label, count, rate, count_ok, rate_ok_flag):
        text = "%s observed %d" % (label, count)
        if not count_ok:
            text += " (need %d)" % PROMOTE_MIN_EVENTS
        text += ", rate " + shown(rate)
        if not rate_ok_flag:
            text += " (need > %s)" % PROMOTE_MIN_HIT_RATE
        return text

    if ok:
        reason = "eligible: live 6h observed %d rate %s, live 24h observed %d rate %s; promotion is a manual owner decision" % (n6, r6, n24, r24)
        return True, reason
    reason = "not eligible: %s; %s; promotion is a manual owner decision" % (
        horizon_text("live 6h", n6, r6, ok_6n, ok_6),
        horizon_text("live 24h", n24, r24, ok_24n, ok_24),
    )
    return False, reason


def promising_backfill(scores):
    """True when the approximate backfill alone would clear the live bar.

    This is not eligibility and not a promotion.
    """
    ok, _n6, _r6, _n24, _r24 = _bar_result(scores, "backfill")
    return ok


def public_summary(roster, events, asof, social_by_name=None, previous=None):
    scores = summarize(events, social_by_name)
    restore_published_live(scores, events, previous)
    whales = []
    for whale in (roster or {}).get("whales") or []:
        if not isinstance(whale, dict) or not whale.get("codename"):
            continue
        if whale.get("status") == "dormant":
            continue
        name = whale["codename"]
        role = "lead" if whale.get("role") == "lead" else "watch"
        status = whale.get("status") if whale.get("status") in ("candidate", "active", "dropped") else "candidate"
        row_scores = scores.get(name) or empty_scores((social_by_name or {}).get(name))
        eligible, reason = promotion_eligibility(row_scores)
        whales.append({
            "codename": name,
            "role": role,
            "status": status,
            "eligible_for_active": eligible,
            "eligible_reason": reason,
            "promising_backfill": promising_backfill(row_scores),
            "scores": row_scores,
        })
    dormant = sum(1 for whale in (roster or {}).get("whales") or [] if isinstance(whale, dict) and whale.get("status") == "dormant")
    return {
        "schema": SCHEMA,
        "kind": PUBLIC_KIND,
        "buy_signal": False,
        "measured_factor": True,
        "min_sample": MIN_SAMPLE,
        "dormant_count": dormant,
        "asof": asof,
        "note": (
            "Research tally. A hit is an observed checkpoint where every measured series "
            "(price and liquidity) moves the whale's way at +1h, +6h, or +24h. A wrong-way "
            "observation is a miss. No observation is unobserved and is not a miss. "
            "n_observed is hits plus misses. Rates stay blank under 8 observed checkpoints "
            "on that horizon in that tally. scores.live is the forward quote path, or the "
            "lead's published private-cache tally. scores.backfill is approximate: nearest "
            "desk-data ADA/qty within 20 minutes at 1h, 60 minutes at 6h, and 3 hours at 24h, "
            "plus pool ADA when a snapshot is in that window. Backfill never replaces live. "
            "eligible_for_active needs at least 25 live observed checkpoints and a live hit "
            "rate above 55% at both 6h and 24h. promising_backfill means the approximate "
            "tally alone would clear that bar. Nothing is promoted automatically. Not a buy signal."
        ),
        "whales": whales,
    }


def public_text_ok(doc):
    """False when a public document carries a wallet, a handle, or the old wording."""
    if not isinstance(doc, dict):
        return False
    if doc.get("buy_signal") is not False or doc.get("kind") != PUBLIC_KIND:
        return False
    if doc.get("private") is True:
        return False
    blob = json.dumps(doc)
    if _LEAK.search(blob) or re.search(r"founder", blob, re.I):
        return False
    if re.search(r"\b[wm]-[0-9a-f]{8}\b", blob):
        return False
    if re.search(r"@[A-Za-z0-9_]{2,}", blob) or re.search(r"\$[A-Za-z]", blob):
        return False
    allowed = {"codename", "role", "status", "scores", "eligible_for_active", "eligible_reason", "promising_backfill"}
    for whale in doc.get("whales") or []:
        if not isinstance(whale, dict) or set(whale) - allowed:
            return False
        if "wallets" in whale or "handle" in whale or "stake" in whale or "tag" in whale:
            return False
        if "eligible_for_active" in whale and whale["eligible_for_active"] not in (True, False):
            return False
        if "promising_backfill" in whale and whale["promising_backfill"] not in (True, False):
            return False
        reason = whale.get("eligible_reason")
        if reason is not None and not isinstance(reason, str):
            return False
    return True


def load_json(path):
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def shortlist_of(log, liquidity):
    names = set()
    if isinstance(log, dict):
        for row in log.get("cardano_top10") or []:
            if isinstance(row, dict) and row.get("ticker"):
                names.add(str(row["ticker"]).upper())
        for row in log.get("verdicts") or []:
            if isinstance(row, dict) and row.get("ticker"):
                names.add(str(row["ticker"]).upper())
    for row in (liquidity or {}).get("tokens") or []:
        if isinstance(row, dict) and row.get("ticker"):
            names.add(str(row["ticker"]).upper())
    return names


def unit_for(ticker, config, known):
    if not ticker:
        return None
    units = (config or {}).get("units") or {}
    if isinstance(units, dict) and isinstance(units.get(ticker), str):
        return units[ticker]
    block = (known or {}).get("units") if isinstance(known, dict) else None
    if isinstance(block, dict):
        row = block.get(ticker) or block.get(str(ticker).upper())
        if isinstance(row, dict) and isinstance(row.get("unit"), str):
            return row["unit"]
        if isinstance(row, str):
            return row
    return None


def social_counts(previous):
    found = {}
    if not isinstance(previous, dict):
        return found
    for whale in previous.get("whales") or []:
        if not isinstance(whale, dict):
            continue
        scores = whale.get("scores") if isinstance(whale.get("scores"), dict) else {}
        value = scores.get("social_moves_matched")
        if isinstance(value, int) and not isinstance(value, bool):
            found[whale.get("codename")] = value
    return found


def dedupe_rows(rows):
    """One row per trade. The same tx in several desk-data runs counts once.

    A row with no tx hash is kept apart by its time so two untagged-time
    rows are not collapsed into each other.
    """
    seen = set()
    out = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tag = str(row.get("tag") or row.get("actor_tag") or "").strip().lower()
        tx = str(row.get("tx_short") or "")
        ticker = str(row.get("ticker") or row.get("token") or "").upper()
        direction = str(row.get("direction") or row.get("kind") or "").lower()
        if tx:
            key = (tag, tx, ticker, direction)
        else:
            key = (tag, "", ticker, direction, str(row.get("time") or row.get("at") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def combine_big_moves(docs):
    """Merge big_moves documents. Coverage is the union of scanned tickers."""
    rows = []
    coverage = {}
    for doc in docs or []:
        if not isinstance(doc, dict):
            continue
        rows.extend(doc.get("moves") or [])
        cov = doc.get("coverage")
        if isinstance(cov, dict):
            for name, value in cov.items():
                if name not in coverage:
                    coverage[name] = value
    return {"moves": dedupe_rows(rows), "coverage": coverage}


def action_from_move(row):
    """accumulate, trim, exit, or None. big_moves and liquidity rows share this."""
    if not isinstance(row, dict):
        return None
    direction = str(row.get("direction") or "").lower()
    kind = str(row.get("kind") or "")
    ticker = str(row.get("ticker") or row.get("token") or "").upper()
    lp = row.get("lp") is True or ticker == "LP"
    if kind == "lp_remove" or (lp and direction in ("out", "remove", "sell")):
        return "exit"
    if kind == "lp_add" or direction in ("in", "buy", "add"):
        return "accumulate"
    if direction in ("out", "sell", "remove"):
        return "trim"
    return None


def moves_from_tagged(rows):
    """Moves for any w- or m- tag. Ledger rows stay on moves_from_ledger."""
    moves = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        tag = row.get("tag") or row.get("actor_tag")
        if not is_tag(tag):
            continue
        action = action_from_move(row)
        if not action:
            continue
        at = parse_time(row.get("time") or row.get("at"))
        token = row.get("ticker") or row.get("token")
        if at is None or not token or not _watch_ticker(str(token)):
            continue
        moves.append({
            "at": at,
            "ticker": str(token),
            "action": action,
            "tx": row.get("tx_short") or "",
            "tag": tag.strip().lower(),
        })
    return moves


def _whale_by_body(roster):
    found = {}
    for whale in (roster or {}).get("whales") or []:
        if not isinstance(whale, dict):
            continue
        for ident in _ids(whale):
            if ident.startswith("id:"):
                found.setdefault(ident[3:], whale)
    return found


def _nearest_point(points, target, tol, skip_tx=None):
    best = None
    for point in points or []:
        if skip_tx and point.get("tx") and point.get("tx") == skip_tx:
            continue
        at = point.get("t")
        if not isinstance(at, (int, float)):
            continue
        gap = abs(at - target)
        if gap <= tol and (best is None or gap < best[0]):
            best = (gap, point)
    return None if best is None else best[1]


def price_series(rows):
    """ticker -> sorted prints {t, price, tx}.

    Price is ADA per token (ada/qty) from big_moves. Whole-token rows win
    when a ticker has any; otherwise raw rows are used. The two scales are
    never mixed on one ticker. ada_basis tx or price only.
    """
    parsed = []
    whole = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("ada_basis") not in ("tx", "price"):
            continue
        ticker = _watch_ticker(row.get("ticker") or "")
        at = parse_time(row.get("time") or row.get("at"))
        ada = row.get("ada")
        qty = row.get("qty")
        basis = row.get("qty_basis")
        if not ticker or at is None or basis not in ("whole", "raw"):
            continue
        if isinstance(ada, bool) or isinstance(qty, bool):
            continue
        if not isinstance(ada, (int, float)) or not isinstance(qty, (int, float)):
            continue
        if ada <= 0 or qty == 0:
            continue
        if basis == "whole":
            whole.add(ticker)
        parsed.append({
            "ticker": ticker,
            "t": at,
            "price": abs(float(ada)) / abs(float(qty)),
            "tx": str(row.get("tx_short") or ""),
            "basis": basis,
        })
    series = {}
    for point in parsed:
        if point["ticker"] in whole and point["basis"] != "whole":
            continue
        if point["ticker"] not in whole and point["basis"] != "raw":
            continue
        series.setdefault(point["ticker"], []).append(point)
    for ticker in series:
        series[ticker].sort(key=lambda point: point["t"])
    return series


def liquidity_series(log, rows):
    """ticker -> sorted {t, liquidity} pool ADA from the log and liquidity rows.

    Log snapshots contribute cardano_top10 ada_side_depth, verdict pool_ada,
    and liquidity_flows pool depth. liquidity_moves contribute
    reserve_ada_after. Same timestamp keeps the latest value seen.
    """
    found = {}

    def put(ticker, at, liquidity):
        name = _watch_ticker(ticker or "")
        if not name or not isinstance(at, (int, float)):
            return
        if isinstance(liquidity, bool) or not isinstance(liquidity, (int, float)) or liquidity <= 0:
            return
        found.setdefault(name, {})[at] = float(liquidity)

    def take_snap(snap):
        if not isinstance(snap, dict):
            return
        at = parse_time(snap.get("asof"))
        for row in (snap.get("cardano_top10") or []):
            if isinstance(row, dict):
                put(row.get("ticker"), at, row.get("ada_side_depth"))
        for row in (snap.get("verdicts") or []):
            if isinstance(row, dict):
                put(row.get("ticker"), at, row.get("pool_ada"))
        flows = snap.get("liquidity_flows")
        if isinstance(flows, dict):
            for row in (flows.get("pools") or []):
                if isinstance(row, dict):
                    put(row.get("ticker"), at, row.get("ada_side_depth"))

    if isinstance(log, dict):
        take_snap(log)
        for snap in log.get("history") or []:
            take_snap(snap)
    for row in rows or []:
        if isinstance(row, dict):
            put(row.get("ticker"), parse_time(row.get("time") or row.get("at")), row.get("reserve_ada_after"))
    series = {}
    for ticker, points in found.items():
        series[ticker] = [{"t": at, "liquidity": liquidity} for at, liquidity in sorted(points.items())]
    return series


def _horizon_offset(name):
    for label, start, _end in WINDOWS:
        if label == name:
            return start
    return 0


def _horizon_closed(event, name, now):
    at = event.get("at")
    if not isinstance(at, (int, float)) or not isinstance(now, (int, float)):
        return False
    for label, _start, end in WINDOWS:
        if label == name:
            return now >= at + end
    return False


def apply_desk_observations(events, prices, liquidity, now):
    """Fill checkpoints from desk-data prints. Unobserved is not a miss.

    Price is the nearest big_moves ADA/qty inside OBS_TOL_S of the horizon.
    Liquidity is the nearest pool ADA snapshot inside the same tolerance,
    when one exists. A live filled checkpoint (outcome hit or miss, or
    status filled) is left alone. A closed horizon with no print is
    unobserved and stays out of n_observed.
    """
    prices = prices or {}
    liquidity = liquidity or {}
    for event in events or []:
        if not isinstance(event, dict):
            continue
        ticker = _watch_ticker(event.get("ticker") or "")
        price_points = prices.get(ticker) or []
        liq_points = liquidity.get(ticker) or []
        tx = str(event.get("tx") or "")
        at = event.get("at")
        if not isinstance(at, (int, float)):
            continue
        base_cp = event.get("at_move") if isinstance(event.get("at_move"), dict) else None
        base_price = None
        base_liq = None
        if base_cp and base_cp.get("status") == "filled" and base_cp.get("price"):
            base_price = base_cp.get("price")
            base_liq = base_cp.get("liquidity")
        else:
            base = _nearest_point(price_points, at, OBS_TOL_S["at_move"], skip_tx=None)
            if base:
                base_price = base.get("price")
                if base_cp is not None and base_cp.get("status") != "filled":
                    base_cp["status"] = "filled"
                    base_cp["price"] = base_price
                    base_cp["observed_at"] = iso(base["t"])
                    base_cp["reason"] = None
                    base_cp["source"] = "desk-data"
            liq = _nearest_point(liq_points, at, OBS_TOL_S["at_move"])
            if liq:
                base_liq = liq.get("liquidity")
                if base_cp is not None and base_cp.get("liquidity") is None:
                    base_cp["liquidity"] = base_liq
        for name in HORIZONS:
            cp = (event.get("followups") or {}).get(name)
            if not isinstance(cp, dict):
                continue
            if cp.get("outcome") in ("hit", "miss") or cp.get("status") == "filled":
                continue
            if cp.get("status") == "missed":
                cp["status"] = "unobserved"
                cp["outcome"] = "unobserved"
            target = at + _horizon_offset(name)
            obs = _nearest_point(price_points, target, OBS_TOL_S[name], skip_tx=tx or None)
            if base_price and obs and obs.get("price"):
                cp["status"] = "filled"
                cp["price"] = obs["price"]
                cp["observed_at"] = iso(obs["t"])
                cp["reason"] = None
                cp["source"] = "desk-data"
                cp["price_change_pct"] = change_pct(obs["price"], base_price)
                liq = _nearest_point(liq_points, target, OBS_TOL_S[name])
                if base_liq and liq and liq.get("liquidity"):
                    cp["liquidity"] = liq["liquidity"]
                    cp["liquidity_change_pct"] = change_pct(liq["liquidity"], base_liq)
                else:
                    cp["liquidity_change_pct"] = None
                _mark_outcome(event, name)
                if cp.get("outcome") == "unobserved":
                    # Flat series: keep it out of the rate. Status stays filled
                    # only when a direction was measured; otherwise unobserved.
                    cp["status"] = "unobserved"
                continue
            if _horizon_closed(event, name, now):
                cp["status"] = "unobserved"
                cp["outcome"] = "unobserved"
                cp["reason"] = "no observation in the window"
                cp["price_change_pct"] = None
                cp["liquidity_change_pct"] = None


def _append_event(events, known_ids, move, codename, config, known):
    unit = unit_for(move.get("ticker"), config, known)
    event = new_event(move, codename, unit)
    if event["id"] in known_ids:
        return
    events.append(event)
    known_ids.add(event["id"])


def build(log, ledger, liquidity, config, known, cache_roster, cache_events, now, quote=None, social_by_name=None, big_moves=None, previous=None):
    """Update the private roster and events. Returns (roster, events, public_doc).

    quote is optional. Without it, open windows stay pending. Candidates stay
    candidates. The lead row is seeded once and is not reactivated if dropped.
    big_moves is one document or a list of them (current file plus older
    desk-data revisions). Rows are deduped by trade. The lead is scored from
    the whale ledger. Every other roster whale is scored from its tagged
    big_moves and liquidity rows, with the same horizons. Wallets are read
    only to match a tag and are not written to the public document.
    eligible_for_active is recorded. Status is not changed to active.
    """
    roster = union_roster(cache_roster, None)
    tag = (ledger or {}).get("whale_tag") if isinstance(ledger, dict) else None
    wallet = ((config or {}).get("whale") or {}).get("stake") if isinstance(config, dict) else None
    ensure_lead(roster, wallet, tag)
    lead_tags = [tag] if is_tag(tag or "") else []
    for whale in roster.get("whales") or []:
        if whale.get("role") == "lead" or whale.get("codename") == LEAD_CODENAME:
            lead_tags.extend(whale.get("tags") or [])
    combined = combine_big_moves(big_moves if isinstance(big_moves, list) else ([big_moves] if isinstance(big_moves, dict) else []))
    router_rows = list(combined.get("moves") or [])
    if isinstance(liquidity, dict):
        router_rows.extend(liquidity.get("moves") or [])
    routers = router_tags({"moves": dedupe_rows(router_rows)})
    reserved = [codename_for(item) for item in routers]
    router_bodies = {tag_body(item) for item in routers if tag_body(item)}
    short = shortlist_of(log, liquidity)
    # One pass. The tightened bar already includes cluster tags that also
    # have enough big-move history. An empty big_moves document is not passed,
    # so the log's repeated summary rows are not mixed with the file history.
    qualified = [
        item for item in discover(log, liquidity, lead_tags, short, combined, now)
        if tag_body(item) not in router_bodies
    ]
    add_candidates(roster, qualified, ["big-move overlap"], reserved)
    settle_candidates(roster, qualified)
    events = list(cache_events or [])
    known_ids = {e.get("id") for e in events if isinstance(e, dict)}
    lead_name = LEAD_CODENAME
    for move in moves_from_ledger(ledger):
        _append_event(events, known_ids, move, lead_name, config, known)
    by_body = _whale_by_body(roster)
    lead_bodies = {tag_body(item) for item in lead_tags if tag_body(item)}
    tagged_rows = list(combined.get("moves") or [])
    if isinstance(liquidity, dict):
        tagged_rows.extend(liquidity.get("moves") or [])
    for move in moves_from_tagged(dedupe_rows(tagged_rows)):
        body = tag_body(move.get("tag"))
        whale = by_body.get(body) if body else None
        if whale is None:
            continue
        if whale.get("role") == "lead" or whale.get("codename") == LEAD_CODENAME:
            continue
        if body in lead_bodies:
            continue
        _append_event(events, known_ids, move, whale.get("codename"), config, known)
    apply_desk_observations(
        events,
        price_series(combined.get("moves") or []),
        liquidity_series(log, (liquidity or {}).get("moves") if isinstance(liquidity, dict) else []),
        now,
    )
    if quote is not None:
        for event in events:
            advance_event(event, now, quote, one_per_run=True)
    public = public_summary(roster, events, iso(now), social_by_name, previous)
    if not public_text_ok(public):
        raise RuntimeError("public whale roster rejected")
    return roster, events, public
