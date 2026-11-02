#!/usr/bin/env python3
"""Write a daily brief from the desk log when the external bot did not.

Root cause: the desk bot's daily run commits solada/log.json and solada/paper.json
on desk-data and stopped adding solada/briefs/YYYY-MM-DD.json after 2026-10-19
(last brief commit f6d95cd1, "Snek Sentinel daily brief 2026-10-19"). Later runs
such as "desk: daily 7 PM run 2026-11-01" touch the log and the paper book only.
This repo never wrote the brief file. scripts/build-site.py indexed only the
files already on main, and the page treats any index.json as config, so it
never listed a brief that existed only on desk-data. The published index froze
at 2026-10-18 and 2026-10-19.

This script fills a missing day from the log headline and learned_or_changed.
It does not replace a brief a person wrote (one without "generated": true).
It also writes briefs/manifest.json, which the page is allowed to read from
desk-data. Hand-written days stay as they are.
"""
import argparse
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/Toronto")
DATE_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
HANDLE = re.compile(r"@[A-Za-z0-9_]{2,32}")
ADDR = re.compile(r"\b(?:(?:stake|addr)(?:_test)?1[0-9a-z]{8,}|0x[0-9a-f]{8,})\b", re.I)
FOUNDER = re.compile(r"\bfounder(?:_whale(?:_move)?|_pct)?\b", re.I)
WTAG = re.compile(r"\bw-[0-9a-f]{6,}\b", re.I)
BOT = re.compile(r"Snek Sentinel")
GENERATED_BY = "sightline-publish-briefs"


def scrub(text):
    s = str(text or "")
    s = BOT.sub("Sentinel", s)
    s = HANDLE.sub("", s)
    s = ADDR.sub("", s)
    s = FOUNDER.sub("", s)
    s = WTAG.sub("", s)
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\s+([,.;])", r"\1", s)
    return s.strip()


def parse_asof(value):
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def et_date(value):
    dt = parse_asof(value)
    return dt.date().isoformat() if dt else None


def scoreboard(paper):
    if not isinstance(paper, dict):
        return None
    closed = [t for t in (paper.get("closed") or []) if isinstance(t, dict)]
    opened = [t for t in (paper.get("open") or []) if isinstance(t, dict)]
    pnl = 0.0
    wins = 0
    for ticket in closed:
        result = ticket.get("result") if isinstance(ticket.get("result"), dict) else {}
        pnl += float(result.get("pnl") or 0)
        if float(result.get("pct") or 0) > 0:
            wins += 1
    losses = max(0, len(closed) - wins)
    return {
        "open": len(opened),
        "closed": len(closed),
        "wins": wins,
        "losses": losses,
        "pnl": round(pnl, 1),
        "unit": paper.get("unit") or "",
    }


def brief_from_log(log, paper, desk, pump=None):
    if not isinstance(log, dict):
        return None
    day = et_date(log.get("asof"))
    headline = scrub(log.get("headline"))
    if not day or not headline:
        return None
    learned = []
    for line in log.get("learned_or_changed") or []:
        if isinstance(line, str):
            clean = scrub(line)
            if clean:
                learned.append(clean)
    doc = {
        "schema_version": 2,
        "desk": desk,
        "date": day,
        "headline": headline[:2000],
        "learned": learned[:8],
        "generated": True,
        "generated_by": GENERATED_BY,
        "source_asof": log.get("asof"),
    }
    board = scoreboard(paper)
    if board:
        doc["scoreboard"] = board
    pump_board = scoreboard(pump)
    if pump_board:
        doc["pumpfun"] = pump_board
    return doc


def snapshots(log):
    """Newest snapshot per America/Toronto date. The current log wins over history."""
    if not isinstance(log, dict):
        return []
    ordered = [log]
    hist = log.get("history") or []
    if isinstance(hist, list):
        for item in reversed(hist):
            if isinstance(item, dict):
                ordered.append(item)
    chosen = {}
    for snap in ordered:
        day = et_date(snap.get("asof"))
        if day and day not in chosen and scrub(snap.get("headline")):
            chosen[day] = snap
    return [chosen[day] for day in sorted(chosen)]


def _load(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _hand_written(path):
    if not os.path.isfile(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return True
    return not (isinstance(doc, dict) and doc.get("generated") is True)


def publish_desk(data_dir, desk_id, log_rel, paper_rel, pump_rel=None):
    log = _load(os.path.join(data_dir, log_rel))
    paper = _load(os.path.join(data_dir, paper_rel))
    pump = _load(os.path.join(data_dir, pump_rel)) if pump_rel else None
    bdir = os.path.join(data_dir, desk_id, "briefs")
    os.makedirs(bdir, exist_ok=True)
    written = []
    for snap in snapshots(log):
        doc = brief_from_log(snap, paper, desk_id, pump if desk_id == "solada" else None)
        if not doc:
            continue
        path = os.path.join(bdir, doc["date"] + ".json")
        if _hand_written(path):
            continue
        # A generated file is replaced when the source log moved.
        if os.path.isfile(path):
            prev = _load(path) or {}
            if prev.get("source_asof") == doc.get("source_asof") and prev.get("headline") == doc.get("headline"):
                continue
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        written.append(doc["date"])
    dates = sorted(p[:-5] for p in os.listdir(bdir) if DATE_FILE.match(p))
    # generated_at is UTC with a Z so the page can compare it to other stamps.
    manifest = {
        "generated_at": datetime.now(tz=ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dates": dates,
    }
    with open(os.path.join(bdir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    return written, dates


def publish(data_dir):
    jobs = [
        ("solada", "solada/log.json", "solada/paper.json", "solada/pumpfun/paper.json"),
        ("nft", "nft/log.json", "nft/paper.json", None),
    ]
    found = {}
    for desk_id, log_rel, paper_rel, pump_rel in jobs:
        if not os.path.isfile(os.path.join(data_dir, log_rel)):
            continue
        written, dates = publish_desk(data_dir, desk_id, log_rel, paper_rel, pump_rel)
        found[desk_id] = {"written": written, "dates": dates}
    return found


def main():
    parser = argparse.ArgumentParser(description="Publish daily briefs from desk logs")
    parser.add_argument("--data-dir", required=True, help="Directory with solada/ and nft/ desk files")
    args = parser.parse_args()
    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(data_dir):
        raise SystemExit("publish-briefs: data dir missing: " + data_dir)
    found = publish(data_dir)
    if not found:
        print("publish-briefs: no desk logs")
        return
    for desk_id, info in found.items():
        print(f"publish-briefs {desk_id}: wrote {info['written'] or '(none)'} · index {info['dates']}")


if __name__ == "__main__":
    main()
