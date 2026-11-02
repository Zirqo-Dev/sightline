#!/usr/bin/env python3
"""Publish a daily brief when the desk log moved and the brief file did not.

Root cause of the stall (briefs frozen on 2026-10-18 and 2026-10-19):

The desk bot still commits solada/log.json (and nft/log.json, when it runs)
onto the desk-data branch, but it stopped writing briefs/YYYY-MM-DD.json
after the 2026-10-19 brief. That brief parked the nightly write for a lean
10 AM / 2 PM / 7 PM schedule and the Oct 24 restore never produced a file.
Separately, scripts/build-site.py built briefs/index.json only from the
copies on main, and the dashboard treated every index.json as config that
is never read from desk-data. Main does not take direct bot pushes, so the
published index could not grow even if a new file landed on desk-data.

This script fills a missing day from the log headline, learned lines, and
the paper book. It does not replace a brief the bot wrote itself. A file
marked generator "publish_briefs" is refreshed when that day's headline
changes. Stake keys, addresses, and the word "founder" are stripped first.
"""
import argparse
import json
import os
import re

ADDR = re.compile(
    r"\b(?:(?:stake|addr)(?:_test)?1[0-9a-z]+|0x[0-9a-fA-F]{8,}|[1-9A-HJ-NP-Za-km-z]{32,44})\b",
    re.I,
)
DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
GENERATOR = "publish_briefs"


def clean(text):
    s = ADDR.sub("", str(text or ""))
    s = re.sub(r"\bfounder\b", "", s, flags=re.I)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;:])", r"\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    return s.strip()


def brief_date(asof):
    m = DATE.match(str(asof or ""))
    return m.group(1) if m else ""


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def scoreboard(paper):
    if not isinstance(paper, dict):
        return None
    closed = paper.get("closed") if isinstance(paper.get("closed"), list) else []
    opened = paper.get("open") if isinstance(paper.get("open"), list) else []
    wins = losses = 0
    pnl = 0.0
    for row in closed:
        res = row.get("result") if isinstance(row, dict) else None
        res = res if isinstance(res, dict) else {}
        pct = res.get("pct")
        got = res.get("pnl")
        metric = pct if isinstance(pct, (int, float)) else got
        if isinstance(metric, (int, float)) and metric > 0:
            wins += 1
        else:
            losses += 1
        if isinstance(got, (int, float)):
            pnl += got
    return {
        "open": len(opened),
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "pnl": round(pnl, 4),
        "unit": clean(paper.get("unit") or ""),
    }


def highlights(log):
    raw = log.get("learned_or_changed") if isinstance(log, dict) else None
    if not isinstance(raw, list):
        raw = []
    out = []
    for item in raw:
        text = item if isinstance(item, str) else ""
        if isinstance(item, dict):
            text = item.get("text") or item.get("line") or ""
        line = clean(text)
        if line:
            out.append(line[:500])
        if len(out) >= 6:
            break
    return out


def build_doc(desk_id, log, paper):
    headline = clean(log.get("headline") if isinstance(log, dict) else "")
    day = brief_date(log.get("asof") if isinstance(log, dict) else "")
    if not headline or not day:
        return None
    doc = {
        "schema_version": 2,
        "desk": desk_id,
        "date": day,
        "headline": headline[:2000],
        "highlights": highlights(log),
        "rules_learned": [],
        "next_watch": [],
        "generator": GENERATOR,
        "source_asof": str(log.get("asof") or ""),
        "disclaimer": "Paper trades and public research only. Not financial advice.",
    }
    board = scoreboard(paper)
    if board:
        doc["scoreboard"] = board
    return doc


def write_index(folder):
    dates = sorted(
        name[:-5] for name in os.listdir(folder)
        if name.endswith(".json") and name != "index.json" and DATE.match(name)
    )
    path = os.path.join(folder, "index.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(dates, fh, indent=2)
        fh.write("\n")
    return dates


def publish_desk(data_dir, desk):
    """Write one brief if this desk's log is ahead of its brief files.

    Returns "wrote", "refreshed", "kept", or "skipped".
    """
    rel = desk.get("briefs")
    log_rel = desk.get("log")
    if not rel or not log_rel:
        return "skipped"
    log = load_json(os.path.join(data_dir, log_rel))
    paper = None
    for book in desk.get("books") or []:
        if book.get("id") == desk.get("id") and book.get("paper"):
            paper = load_json(os.path.join(data_dir, book["paper"]))
            break
    if paper is None:
        for book in desk.get("books") or []:
            if book.get("paper"):
                paper = load_json(os.path.join(data_dir, book["paper"]))
                break
    doc = build_doc(desk.get("id") or "", log, paper)
    if not doc:
        return "skipped"
    folder = os.path.join(data_dir, rel)
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, doc["date"] + ".json")
    action = "wrote"
    if os.path.exists(path):
        prev = load_json(path) or {}
        if prev.get("generator") != GENERATOR:
            write_index(folder)
            return "kept"
        if prev.get("headline") == doc["headline"] and prev.get("source_asof") == doc["source_asof"]:
            write_index(folder)
            return "kept"
        action = "refreshed"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")
    write_index(folder)
    return action


def desks_from(data_dir, desks_path):
    path = desks_path or os.path.join(data_dir, "desks.json")
    if not os.path.isfile(path):
        here = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "desks.json"))
        path = here
    doc = load_json(path) or {}
    return doc.get("desks") or []


def publish(data_dir, desks_path=None):
    results = {}
    for desk in desks_from(data_dir, desks_path):
        desk_id = desk.get("id") or "?"
        results[desk_id] = publish_desk(data_dir, desk)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish missing daily briefs from desk logs")
    parser.add_argument("--data-dir", default=".", help="desk-data checkout (default: current directory)")
    parser.add_argument("--desks", default="", help="desks.json path (default: data-dir, then this repo)")
    args = parser.parse_args(argv)
    results = publish(args.data_dir, args.desks or None)
    for desk_id, action in results.items():
        print(f"brief {desk_id}: {action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
