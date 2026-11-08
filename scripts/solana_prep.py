#!/usr/bin/env python3
"""Read-only Solana day slice for nightly evidence. Not the evidence packet.

Counts buys, sells, and sell reasons in one UTC day of trades.csv, and copies
the health.json scalars when that file's day is the same day. Staleness is
unknown. Equity, means, and SH-05 pulls are out of scope.

The public JSON has no mint. Row identity is sha256 of
time|book|action|why|mint|pnl|frac. The file stores one hash of the sorted
row ids.
"""
import argparse
import calendar
import csv
import hashlib
import json
import os
import re
import sys

SCHEMA_VERSION = 1
KIND = "sightline_solana_prep"
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
AUDIT_FIELDS = ("time", "book", "action", "why", "mint", "pnl", "frac")
HEALTH_FIELDS = (
    "day", "last_run_utc", "loop_ran", "runs", "expected_runs", "checks",
    "data_failure_rate", "void_day", "error_rate", "mode", "frozen", "cfg_version",
)
OUT_OF_SCOPE = (
    "equity",
    "cardano_vetoes",
    "cardano_liquidity",
    "whale_ledger",
    "dev_checks",
    "x_buzz",
    "spike_outcomes",
    "blacklist",
    "solada_log",
    "sh05_pool_age",
    "sh05_true_pull_sol_liquidity_series",
    "notion_hypothesis_tallies",
    "notion_decisions",
)


class PrepError(Exception):
    pass


def dumps(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha(value, label):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise PrepError(label + " must be 40 hex characters")
    return value


def _day_bounds(day):
    if not isinstance(day, str) or not DAY_RE.fullmatch(day):
        raise PrepError("day must be YYYY-MM-DD")
    try:
        parsed = calendar.timegm(__import__("time").strptime(day, "%Y-%m-%d"))
    except ValueError as exc:
        raise PrepError("day must be YYYY-MM-DD") from exc
    return parsed, parsed + 86400


def _row_id(row):
    parts = []
    for key in AUDIT_FIELDS:
        parts.append((row.get(key) or "").strip())
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _load_trades(path):
    if not os.path.isfile(path):
        raise PrepError("trades.csv is missing")
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            raw_lines = fh.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise PrepError("trades.csv is unreadable") from exc
    if not raw_lines:
        raise PrepError("trades.csv is empty")
    try:
        reader = csv.DictReader(raw_lines)
        fieldnames = list(reader.fieldnames or [])
        if "time" not in fieldnames or "action" not in fieldnames or "book" not in fieldnames:
            raise PrepError("trades.csv header is missing time, book, or action")
        rows = list(reader)
    except csv.Error as exc:
        raise PrepError("trades.csv is not CSV") from exc
    seen = set()
    kept = []
    for row in rows:
        if not isinstance(row, dict):
            raise PrepError("trades.csv has a row that is not a record")
        action = (row.get("action") or "").strip()
        book = (row.get("book") or "").strip()
        if not action or not book:
            raise PrepError("trades.csv has a row without action or book")
        try:
            ts = float(row.get("time") or "")
        except (TypeError, ValueError) as exc:
            raise PrepError("trades.csv has a row with a bad time") from exc
        if ts < 0:
            raise PrepError("trades.csv has a row with a bad time")
        rid = _row_id(row)
        if rid in seen:
            raise PrepError("trades.csv has a duplicate row")
        seen.add(rid)
        row["_ts"] = ts
        row["_id"] = rid
        kept.append(row)
    return kept


def _health(path, day):
    unknown = {
        "known": False,
        "reason": "health.json is missing or does not match the window",
    }
    for key in HEALTH_FIELDS:
        unknown[key] = None
    if not os.path.isfile(path):
        unknown["reason"] = "health.json is missing"
        return unknown
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.loads(fh.read())
    except (OSError, UnicodeError, json.JSONDecodeError):
        unknown["reason"] = "health.json is unreadable"
        return unknown
    if not isinstance(doc, dict):
        unknown["reason"] = "health.json is not an object"
        return unknown
    if doc.get("day") != day:
        unknown["reason"] = "health day does not match the window"
        return unknown
    out = {"known": True, "reason": None}
    for key in HEALTH_FIELDS:
        out[key] = doc.get(key)
    return out


def build(trades_path, health_path, day, code_commit, data_commit):
    code_commit = _require_sha(code_commit, "code commit")
    data_commit = _require_sha(data_commit, "data commit")
    start, end = _day_bounds(day)
    rows = _load_trades(trades_path)
    health = _health(health_path, day)
    selected = [row for row in rows if start <= row["_ts"] < end]
    opens = []
    closes = []
    marks = []
    other = []
    for row in selected:
        action = (row.get("action") or "").strip()
        if action == "buy":
            opens.append(row)
        elif action == "sell":
            closes.append(row)
        elif action == "mark":
            marks.append(row)
        else:
            other.append(row)
    open_reasons = {}
    unknown_open = 0
    for row in opens:
        why = (row.get("why") or "").strip()
        if not why:
            unknown_open += 1
            continue
        open_reasons[why] = open_reasons.get(why, 0) + 1
    exit_reasons = {}
    unknown_exit = 0
    for row in closes:
        why = (row.get("why") or "").strip()
        if not why:
            unknown_exit += 1
            continue
        exit_reasons[why] = exit_reasons.get(why, 0) + 1
    other_actions = {}
    for row in other:
        action = (row.get("action") or "").strip()
        other_actions[action] = other_actions.get(action, 0) + 1
    opens_ok = unknown_open == 0
    closes_ok = unknown_exit == 0 and sum(exit_reasons.values()) == len(closes)
    if not opens_ok or not closes_ok or not health["known"]:
        status = "unknown"
    else:
        status = "ok"
    id_set = hashlib.sha256("\n".join(sorted(row["_id"] for row in selected)).encode("utf-8")).hexdigest()
    if not selected:
        id_set = hashlib.sha256(b"").hexdigest()
    doc = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "ai_baseline": "unmeasured",
        "provenance": {
            "builder": "scripts/solana_prep.py",
            "code_commit": code_commit,
            "data_commit": data_commit,
            "inputs": {
                "solana-desk/trades.csv": _file_sha256(trades_path),
                "solana-desk/health.json": _file_sha256(health_path) if os.path.isfile(health_path) else None,
            },
            "window": {
                "day": day,
                "start": day + "T00:00:00Z",
                "end_exclusive": _next_day(day) + "T00:00:00Z",
                "clock": "UTC",
            },
            "as_of": {
                "semantics": "records inside the UTC day as stored in the snapshot",
                "staleness": "unknown",
            },
        },
        "coverage": {
            "trades_window": "complete",
            "health": "recorded" if health["known"] else "unknown",
            "out_of_scope": list(OUT_OF_SCOPE),
        },
        "reconciliation": {
            "status": status,
            "opens": {
                "n": len(opens),
                "ok": opens_ok,
                "reasons": open_reasons,
                "unknown_reason": unknown_open,
            },
            "closes": {
                "n": len(closes),
                "ok": closes_ok,
                "exit_reasons": exit_reasons,
                "unknown_exit_reason": unknown_exit,
            },
            "marks": {"n": len(marks)},
            "other_actions": other_actions,
            "window_row_n": len(selected),
            "row_id_set_sha256": id_set,
        },
        "health": health,
    }
    return doc


def _next_day(day):
    import datetime
    y, m, d = (int(part) for part in day.split("-"))
    return (datetime.date(y, m, d) + datetime.timedelta(days=1)).isoformat()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only Solana day slice. Does not write the snapshot.")
    parser.add_argument("--trades", required=True)
    parser.add_argument("--health", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--code-commit", required=True)
    parser.add_argument("--data-commit", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        doc = build(args.trades, args.health, args.day, args.code_commit, args.data_commit)
    except PrepError as exc:
        print("solana prep: " + str(exc), file=sys.stderr)
        return 2
    text = dumps(doc)
    parent = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(parent, exist_ok=True)
    trades_dir = os.path.dirname(os.path.abspath(args.trades))
    if os.path.abspath(parent) == trades_dir:
        print("solana prep: --out must not be the snapshot directory", file=sys.stderr)
        return 2
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
