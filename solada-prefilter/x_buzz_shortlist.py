"""Name the next X buzz shortlist. This file does not call X.

The external job reads ``solada-prefilter/x_buzz_shortlist.json`` and queries
``this_run`` only. The read budget is the live file on 2026-11-02: 10 Cardano
rows and 3 Solana rows, 24 calls in total. This module does not raise that
budget and does not change a verdict, a kill line, or a ticket.

Cardano rows, in order, until the 10-row budget:
  1. Every EST-01 universe ticker (pinned every run).
  2. Up to half of the remaining slots: whale-ledger tickers already on the
     previous ``x_buzz.json`` Cardano list, so a series already being logged
     is not dropped in one run.
  3. The rest rotate through every other whale-ledger ticker. The cursor is
     stored here. A later run continues where this one stopped, so the ledger
     is covered without a larger read.

A ticker is one word of letters and digits, 2 to 15 characters. Policy
fragments, spaced names, and ``ADA`` are listed under ``skipped`` and are not
queried. No wallet, stake key, or handle is written.
"""
import json
import os
import re

import est_log

CARDANO_BUDGET = 10
SOLANA_BUDGET = 3
TICKER = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,14}$")
SKIP = {"ADA", "UNKNOWN"}
BUDGET_BASIS = (
    "x_buzz.json on 2026-11-02 queried 10 Cardano rows and 3 Solana rows "
    "(13) with x_calls.total 24. This list stays inside that row budget."
)


def _load(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def clean_ticker(value):
    text = str(value or "").strip()
    if not TICKER.fullmatch(text):
        return ""
    name = text.upper()
    if name in SKIP or name.startswith("LP"):
        return ""
    return name


def whale_tickers(ledger):
    """Clean tickers from positions and row tokens. Order is stable."""
    if not isinstance(ledger, dict):
        return [], []
    seen = []
    known = set()
    skipped = []

    def consider(raw, where):
        text = str(raw or "").strip()
        if not text or text.upper() in SKIP or text.upper().startswith("LP"):
            return
        name = clean_ticker(text)
        if not name:
            if text not in {s["token"] for s in skipped}:
                skipped.append({"token": text[:40], "reason": "not a single ticker", "where": where})
            return
        if name in known:
            return
        known.add(name)
        seen.append(name)

    positions = ledger.get("positions")
    if isinstance(positions, dict):
        for key in positions:
            consider(key, "positions")
    for row in ledger.get("rows") or []:
        if isinstance(row, dict):
            consider(row.get("token"), "rows")
    return seen, skipped


def _previous_cardano(buzz):
    out = []
    for row in (buzz or {}).get("tokens") or []:
        if not isinstance(row, dict) or str(row.get("chain") or "") != "cardano":
            continue
        name = clean_ticker(row.get("ticker"))
        if name and name not in out:
            out.append(name)
    return out


def _previous_solana(buzz):
    out = []
    for row in (buzz or {}).get("tokens") or []:
        if not isinstance(row, dict) or str(row.get("chain") or "") != "solana":
            continue
        name = clean_ticker(row.get("ticker"))
        if name and name not in out:
            out.append(name)
        if len(out) >= SOLANA_BUDGET:
            break
    return out


def _take(pool, cursor, slots):
    if slots <= 0 or not pool:
        return [], cursor if pool else 0
    start = cursor % len(pool)
    picked = []
    idx = start
    while len(picked) < slots and len(picked) < len(pool):
        name = pool[idx % len(pool)]
        if name not in picked:
            picked.append(name)
        idx += 1
        if idx % len(pool) == start and len(picked) >= min(slots, len(pool)):
            break
    nxt = (start + len(picked)) % len(pool)
    return picked, nxt


def select(universe, whale, previous_cardano, cursor):
    """Return this_run rows, the next cursor, and whether the pin was trimmed."""
    pinned = []
    for name in universe or []:
        name = clean_ticker(name)
        if name and name not in pinned:
            pinned.append(name)
    whale_set = set(whale or [])
    over = False
    deferred = []
    if len(pinned) > CARDANO_BUDGET:
        deferred = pinned[CARDANO_BUDGET:]
        pinned = pinned[:CARDANO_BUDGET]
        over = True
        return pinned, cursor, over, deferred, []
    remain = CARDANO_BUDGET - len(pinned)
    continuity = []
    for name in previous_cardano or []:
        if name in whale_set and name not in pinned and name not in continuity:
            continuity.append(name)
    cont_cap = min(len(continuity), remain // 2)
    continuity = continuity[:cont_cap]
    rotate_pool = [name for name in whale or [] if name not in pinned and name not in continuity]
    slots = remain - len(continuity)
    rotated, nxt = _take(rotate_pool, int(cursor or 0), slots)
    this_run = pinned + continuity + rotated
    return this_run, nxt, over, deferred, continuity


def _cursor_of(previous):
    if not isinstance(previous, dict):
        return 0
    try:
        return int(previous.get("cursor") or 0)
    except (TypeError, ValueError):
        return 0


def build(log, paper, ledger, buzz, previous, now_s):
    universe = est_log.universe_tickers(log, paper)
    whale, skipped = whale_tickers(ledger)
    prev_cardano = _previous_cardano(buzz)
    this_run, cursor, over, deferred, continuity = select(universe, whale, prev_cardano, _cursor_of(previous))
    pinned = set(clean_ticker(n) for n in universe)
    cont = set(continuity)
    rows = []
    for name in this_run:
        if name in pinned:
            reason = "est_universe"
        elif name in cont:
            reason = "whale_ledger_continuity"
        else:
            reason = "whale_ledger"
        rows.append({"ticker": name, "chain": "cardano", "reason": reason})
    solana = [{"ticker": name, "chain": "solana", "reason": "previous_shortlist"} for name in _previous_solana(buzz)]
    candidates = []
    seen = set()
    for name in list(universe) + list(whale):
        name = clean_ticker(name)
        if name and name not in seen:
            seen.add(name)
            candidates.append(name)
    at = est_log._stamp(now_s)
    return {
        "schema_version": 1,
        "kind": "x_buzz_shortlist",
        "buy_signal": False,
        "mode": "log_only",
        "at": at,
        "budget": {"cardano_rows": CARDANO_BUDGET, "solana_rows": SOLANA_BUDGET, "basis": BUDGET_BASIS},
        "over_budget": over,
        "deferred": deferred,
        "cursor": cursor,
        "candidates": candidates,
        "skipped": skipped,
        "this_run": rows,
        "solana_this_run": solana,
        "note": (
            "Query this_run, then solana_this_run. Do not add rows and do not raise the call count. "
            "Cardano pins the EST-01 universe, keeps a few whale-ledger names already on the previous "
            "shortlist, and rotates the rest of the whale ledger through the remaining slots. "
            + BUDGET_BASIS
            + " Not a buy signal and not a verdict."
        ),
    }


def record(data_dir, log, paper, now_s, buzz_path=None, ledger_path=None):
    """Write the shortlist next to the other prefilter research files."""
    ledger = _load(ledger_path or os.path.join(data_dir, "whale_ledger.json"))
    buzz = _load(buzz_path) if buzz_path else None
    previous = _load(os.path.join(data_dir, "x_buzz_shortlist.json"))
    doc = build(log, paper, ledger, buzz, previous, now_s)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "x_buzz_shortlist.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    return {
        "at": doc["at"],
        "this_run": [r["ticker"] for r in doc["this_run"]],
        "solana": [r["ticker"] for r in doc["solana_this_run"]],
        "candidates": len(doc["candidates"]),
        "over_budget": doc["over_budget"],
        "cursor": doc["cursor"],
    }
