"""Cardano whale roster. Research only: never a buy signal.

Private file (sightline-private whales/roster.json): codename, raw wallets,
role, added date, source, evidence, status, and a social-account slot.
Public desk-data file solada-prefilter/whale_roster.json: codename, role,
status, and scores. No wallets.

Readers of the run log accept whales[] and the previous single-object key.
Writers emit whales[] only.
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
_W = re.compile(r"^w-[0-9a-f]{8}$")
_STAKE = re.compile(r"^stake1[02-9ac-hj-np-z]{20,}$")
_ADDR = re.compile(r"^addr1[02-9ac-hj-np-z]{20,}$")
_LEAK = re.compile(r"stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,}")

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
    return isinstance(value, str) and bool(_W.fullmatch(value.strip().lower()))


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


def advance_event(event, now, quote, one_per_run=True):
    """Fill the open window. Later windows that already ended are missed.

    quote(unit) -> {answered, price, liquidity, reason}. One open window at a
    time, matching the buzz outcome tracker.
    """
    filled = False
    at = event["at"]
    for name, start, end in WINDOWS:
        cp = _cp(event, name)
        if cp["status"] in ("filled", "missed"):
            continue
        if now < at + start:
            cp["status"] = "pending"
            cp["reason"] = "not due"
            continue
        if now >= at + end:
            cp["status"] = "missed"
            cp["observed_at"] = None
            cp["reason"] = "window passed before a run observed it"
            continue
        if one_per_run and filled:
            cp["status"] = "pending"
            cp["reason"] = "deferred; one checkpoint per run"
            continue
        filled = True
        reading = quote(event.get("unit")) if event.get("unit") else {"answered": False, "reason": "full token id unavailable"}
        _fill(event, name, cp, now, reading)


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


def empty_scores(social=None):
    scores = {
        "events": 0,
        "accumulate": 0,
        "trim": 0,
        "exit": 0,
    }
    for name in HORIZONS:
        scores["priced_" + name] = 0
        scores["hits_" + name] = 0
        scores["hit_rate_" + name] = None
    scores["small_sample"] = True
    scores["social_moves_matched"] = social if isinstance(social, int) and not isinstance(social, bool) else None
    return scores


def summarize(events, social_by_name=None):
    """Per-codename counts and hit rates. Rates stay null under 8 events."""
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
                if cp.get("status") != "filled":
                    continue
                vote = favorable(action, cp.get("price_change_pct"), cp.get("liquidity_change_pct"))
                if vote is None:
                    continue
                scores["priced_" + horizon] += 1
                if vote:
                    scores["hits_" + horizon] += 1
        scores["small_sample"] = scores["events"] < MIN_SAMPLE
        if not scores["small_sample"]:
            for horizon in HORIZONS:
                priced = scores["priced_" + horizon]
                scores["hit_rate_" + horizon] = round(scores["hits_" + horizon] / priced, 3) if priced else None
        out[name] = scores
    return out


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
    name = str(ticker).upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9-]{0,15}", name) or name == "ADA":
        return
    bucket_tokens.setdefault(tag.lower(), set()).add(name)
    if isinstance(ada, (int, float)) and not isinstance(ada, bool) and ada > 0:
        bucket_ada[tag.lower()] = bucket_ada.get(tag.lower(), 0) + float(ada)


def discover(log, liquidity, lead_tags, shortlist):
    """Candidate tags from clusters, heat, and liquidity moves. Never active.

    A tag qualifies when it shows up on at least two shortlist tokens with
    enough ADA, or on at least two shortlist tokens alongside another tag
    (they move together). The lead tag is skipped.
    """
    tokens, ada = {}, {}
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
    for move in (liquidity or {}).get("moves") or []:
        if isinstance(move, dict):
            _add(tokens, ada, move.get("actor_tag"), move.get("ticker"), move.get("ada"))
    short = {str(t).upper() for t in (shortlist or [])}
    lead = {t.lower() for t in (lead_tags or []) if is_tag(t)}
    found = []
    for tag, names in tokens.items():
        if tag in lead:
            continue
        on_list = sorted(n for n in names if not short or n in short)
        if len(on_list) < MIN_TOKENS:
            continue
        others = False
        for other, other_names in tokens.items():
            if other == tag:
                continue
            if len(set(on_list) & set(other_names)) >= MIN_TOKENS:
                others = True
                break
        large = ada.get(tag, 0) >= MIN_ADA
        if large or others:
            found.append(tag)
    return sorted(found)


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
            found.add("wallet:" + wallet.strip())
    for tag in whale.get("tags") or []:
        if is_tag(tag):
            found.add("tag:" + tag.strip().lower())
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


def add_candidates(roster, tags, evidence):
    """Insert candidates. Never changes an existing whale to active."""
    taken = [w.get("codename") for w in roster.get("whales") or []]
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


def public_summary(roster, events, asof, social_by_name=None):
    scores = summarize(events, social_by_name)
    whales = []
    for whale in (roster or {}).get("whales") or []:
        if not isinstance(whale, dict) or not whale.get("codename"):
            continue
        name = whale["codename"]
        role = "lead" if whale.get("role") == "lead" else "watch"
        status = whale.get("status") if whale.get("status") in ("candidate", "active", "dropped") else "candidate"
        whales.append({
            "codename": name,
            "role": role,
            "status": status,
            "scores": scores.get(name) or empty_scores((social_by_name or {}).get(name)),
        })
    return {
        "schema": SCHEMA,
        "kind": PUBLIC_KIND,
        "buy_signal": False,
        "measured_factor": True,
        "min_sample": MIN_SAMPLE,
        "asof": asof,
        "note": "Research tally. A hit is a favorable price or liquidity change at +1h, +6h, or +24h. Rates stay blank under 8 events. Not a buy signal.",
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
    if re.search(r"@[A-Za-z0-9_]{2,}", blob) or re.search(r"\$[A-Za-z]", blob):
        return False
    allowed = {"codename", "role", "status", "scores"}
    for whale in doc.get("whales") or []:
        if not isinstance(whale, dict) or set(whale) - allowed:
            return False
        if "wallets" in whale or "handle" in whale or "stake" in whale:
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


def build(log, ledger, liquidity, config, known, cache_roster, cache_events, now, quote=None, social_by_name=None):
    """Update the private roster and events. Returns (roster, events, public_doc).

    quote is optional. Without it, open windows stay pending. Candidates stay
    candidates. The lead row is seeded once and is not reactivated if dropped.
    """
    roster = union_roster(cache_roster, None)
    tag = (ledger or {}).get("whale_tag") if isinstance(ledger, dict) else None
    wallet = ((config or {}).get("whale") or {}).get("stake") if isinstance(config, dict) else None
    ensure_lead(roster, wallet, tag)
    lead_tags = [tag] if is_tag(tag or "") else []
    for whale in roster.get("whales") or []:
        if whale.get("codename") == LEAD_CODENAME:
            lead_tags.extend(whale.get("tags") or [])
    add_candidates(roster, discover(log, liquidity, lead_tags, shortlist_of(log, liquidity)), ["cluster or liquidity overlap"])
    events = list(cache_events or [])
    known_ids = {e.get("id") for e in events if isinstance(e, dict)}
    lead_name = LEAD_CODENAME
    for move in moves_from_ledger(ledger):
        unit = unit_for(move["ticker"], config, known)
        event = new_event(move, lead_name, unit)
        if event["id"] in known_ids:
            continue
        events.append(event)
        known_ids.add(event["id"])
    if quote is not None:
        for event in events:
            advance_event(event, now, quote, one_per_run=True)
    public = public_summary(roster, events, iso(now), social_by_name)
    if not public_text_ok(public):
        raise RuntimeError("public whale roster rejected")
    return roster, events, public
