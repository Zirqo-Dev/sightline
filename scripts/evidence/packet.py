#!/usr/bin/env python3
"""Read-only evidence packet. No network, no wall clock, no pushes.

Same --repo, --data, --as-of, previous packet, and Actions snapshot produce
the same bytes. Checkout history depth is not an input. A file link uses the
pinned snapshot SHA when that commit contains the file bytes. A missing input
stays unknown. It is not treated as healthy.

  python3 scripts/evidence/packet.py --repo . --data /path/to/desk-data \\
      --as-of 2026-11-07T21:30:00Z --out /tmp/evidence
"""
import argparse
import ast
import calendar
import csv
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlparse

SCHEMA_VERSION = 1
KIND = "sightline_evidence_packet"
BUILDER = "scripts/evidence/packet.py"
BOOKS = ("F", "R", "S")
AS_OF_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
SHA_RE = re.compile(r"[0-9a-f]{40}")
SLUG_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DESK_RUN_RE = re.compile(r"\d{8}T\d{4}")
POS_ID_RE = re.compile(r"[FRS]-\d{8}T\d{4}-[1-9A-HJ-NP-Za-km-z]{8}")
CRON_RE = re.compile(r'^\s*-\s*cron:\s*"(\d{1,2})\s+(\d{1,2})\s+\*\s+\*\s+\*"\s*$', re.M)
PLACES = Decimal("0.000000001")
PNL_TOL = 1e-8
FRAC_DONE = 1 - 1e-4
SECRET_RE = re.compile(
    r"ghs_[A-Za-z0-9]|ghp_[A-Za-z0-9]|github_pat_|x-access-token|BEGIN [A-Z ]*PRIVATE KEY",
    re.I,
)
SCAN_FILES = (
    "solana-desk/health.json",
    "solana-desk/trades.csv",
    "solana-desk/tasks.csv",
    "solana-desk/positions.json",
)
SCAN_OPTIONAL = ("solana-desk/scan_timing.jsonl",)
PREFILTER_FILES = ("solada-prefilter/last-run.json",)
DAILY_FILES = ("reports/status/daily_status_latest.json",)
WORKFLOW_PATHS = (
    ".github/workflows/solana-desk-scan.yml",
    ".github/workflows/solada-prefilter.yml",
    ".github/workflows/daily-status.yml",
    ".github/workflows/desk-data-guard.yml",
    ".github/workflows/privacy-guard.yml",
    ".github/workflows/schema-check.yml",
    ".github/workflows/run-forensics.yml",
)
FAILED_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
HEALTH_REQUIRED = ("loop_ran", "void_day", "ai")
HEALTH_OPTIONAL = ("frozen",)
REQUIRED_LINES = (
    "f_closes_meet_minimum",
    "f_expectancy_below_stop",
    "f_rule_would_stop",
    "data_failure_rate_above_void",
)
TRADE_NEED = ("time", "book", "action", "mint", "frac", "pnl")
TASK_NEED = ("time", "run_id", "skip_type", "fail_reason")
PREDICATES = (
    "new_close_id",
    "new_fill_id",
    "health_flip",
    "new_failed_run_id",
    "board_threshold_crossing",
    "material_verdict_inputs",
)
NOT_IMPLEMENTED = (
    "prefilter_trading_verdict",
    "forensics_trading_verdict",
    "nft_workflow",
    "solada_desk_workflow",
    "actions_run_history_drops",
    "alert_replacement",
)
LIMITATIONS = (
    "coverage.full is false. A source or predicate that was not read is unknown or not_implemented, not healthy.",
    "Freshness limits scan_60min, prefilter_150min, and daily_2h_after_scheduled_slot are provisional pilot settings. They are not desk trading rules.",
    "Daily freshness uses the cron slot in .github/workflows/daily-status.yml plus that provisional grace. It does not use file modification time, and it is not the 19:00 America/Toronto rule in scripts/status/daily_status.py.",
    "Forensics detail and the Actions billing-account lookup are deferred. Their state is unknown, not healthy, and coverage.full stays false.",
    "Day-14 STOP and CONTINUE figures are recomputed inputs. This packet does not issue finality and it is not a trading instruction.",
    "A prefilter trading verdict is not implemented. Prefilter freshness still uses last-run.json at and dry_run.",
    "run-nft.yml and run-solada.yml are not queried.",
    "Run duration is copied from the timing API when a snapshot has it. The billing-account lookup is deferred. Duration is not an invoice and not remaining quota.",
    "This pilot does not estimate a token-saving percentage.",
    "Missing files, unreadable files, and a missing Actions snapshot are not treated as healthy.",
    "The packet does not read the wall clock. as-of is an input. The same inputs and as-of produce the same bytes.",
    "A file commit and its blob URL are the pinned checkout SHA when that snapshot contains the file bytes. They are not the last commit that edited the path, and they do not depend on fetch depth.",
    "Existing alerts are unchanged. This packet does not close or replace them.",
    "Workflow health uses the newest schedule, push, or workflow_dispatch run in a 20-run snapshot page. A skipped pull request does not replace that run. If the page has no such run, the state is unknown, not healthy.",
)


class PacketError(Exception):
    pass


def dumps(doc):
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise PacketError("cannot load " + name)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def status_module(repo):
    path = os.path.join(repo, "scripts", "status", "daily_status.py")
    cached = getattr(status_module, "_mod", None)
    if cached and cached[0] == path:
        return cached[1]
    mod = _load_module("daily_status_evidence", path)
    status_module._mod = (path, mod)
    return mod


def alert_module(repo):
    path = os.path.join(repo, "scripts", "data_health_alert.py")
    cached = getattr(alert_module, "_mod", None)
    if cached and cached[0] == path:
        return cached[1]
    # The alert module is imported by file path. Loading it does not post an issue.
    scripts = os.path.join(repo, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    mod = _load_module("data_health_alert_evidence", path)
    alert_module._mod = (path, mod)
    return mod


def day14_module(repo):
    desk_root = os.path.join(repo, "solana-desk")
    path = os.path.join(desk_root, "specs", "day14_verdict.py")
    cached = getattr(day14_module, "_mod", None)
    if cached and cached[0] == path:
        return cached[1]
    if desk_root not in sys.path:
        sys.path.insert(0, desk_root)
    mod = _load_module("day14_verdict_evidence", path)
    day14_module._mod = (path, mod)
    return mod


def text_fails(status, text):
    if not isinstance(text, str):
        return True
    if SECRET_RE.search(text):
        return True
    if status.BANNED.search(text):
        return True
    return False


def quant(value):
    return str(Decimal(str(value)).quantize(PLACES, rounding=ROUND_HALF_UP))


def rate_text(value):
    return format(Decimal(str(value)), ".10g")


def github_slug_from_url(url):
    """Repository slug only. Userinfo, including an access token, is ignored."""
    if not isinstance(url, str) or not url or len(url) > 500:
        return None
    parsed = urlparse(url)
    if parsed.hostname != "github.com":
        return None
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    if SLUG_RE.fullmatch(path):
        return path
    return None


def github_slug(value):
    if isinstance(value, str) and SLUG_RE.fullmatch(value.strip()):
        return value.strip()
    return None


def _git(repo, args):
    try:
        proc = subprocess.run(
            ["git", "-C", repo, *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or "").strip()
    return text or None


def git_head(repo):
    text = _git(repo, ["rev-parse", "HEAD"])
    if text and SHA_RE.fullmatch(text):
        return text
    return None


def git_file_commit(repo, rel):
    """Pinned snapshot SHA when that commit's blob matches the raw file bytes.

    ``git log -1`` is not used. A depth-1 checkout reports HEAD for every
    path, and a full history reports the last commit that touched the path.
    Those SHAs differ for one tree. The blob URL is the snapshot that was
    read, which is truthful only when the snapshot contains these bytes.
    ``git hash-object --no-filters`` is required. Without it, a clean or
    eol filter can hash a CRLF worktree as the committed LF blob.
    """
    if not rel or rel.startswith("-") or ".." in rel.split("/") or "\n" in rel or "\r" in rel:
        return None
    head = git_head(repo)
    if not head:
        return None
    listed = _git(repo, ["ls-tree", "-r", "HEAD", "--", rel])
    if not listed:
        return None
    lines = [line for line in listed.splitlines() if line]
    if len(lines) != 1 or "\t" not in lines[0]:
        return None
    meta, name = lines[0].split("\t", 1)
    if name != rel:
        return None
    parts = meta.split()
    if len(parts) != 3 or parts[1] != "blob" or not SHA_RE.fullmatch(parts[2]):
        return None
    work = _git(repo, ["hash-object", "--no-filters", "--", rel])
    if work != parts[2]:
        return None
    return head


def remote_slug(repo):
    url = _git(repo, ["remote", "get-url", "origin"])
    if not url:
        return None
    return github_slug_from_url(url)


def blob_url(slug, commit, rel):
    if not github_slug(slug or "") or not commit or not SHA_RE.fullmatch(commit):
        return None
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return None
    return "https://github.com/%s/blob/%s/%s" % (slug, commit, rel)


def run_url(slug, run_id):
    if not github_slug(slug or "") or not isinstance(run_id, int):
        return None
    if run_id < 1 or run_id > 10**15:
        return None
    return "https://github.com/%s/actions/runs/%s" % (slug, run_id)


def parse_as_of(value, status):
    if value is None:
        return None, None
    if not isinstance(value, str) or not AS_OF_RE.fullmatch(value):
        raise PacketError("--as-of must be YYYY-MM-DDTHH:MM:SSZ")
    epoch = status.parse_time(value)
    if epoch is None:
        raise PacketError("--as-of must be YYYY-MM-DDTHH:MM:SSZ")
    return value, int(epoch)


def stamp_from(status, value):
    epoch = status.parse_time(value)
    if epoch is None:
        return None, None
    return status.fmt_time(epoch), int(epoch)


def read_text(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read(), None
    except (OSError, UnicodeError) as exc:
        return None, type(exc).__name__


def load_json(path):
    text, err = read_text(path)
    if err:
        return None, err
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "JSONDecodeError"


def read_table(path, required):
    """CSV with a real header. A headerless file is not an empty ledger."""
    text, err = read_text(path)
    if err:
        return None, err
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return None, "header"
    try:
        reader = csv.DictReader(lines)
        fields = list(reader.fieldnames or [])
        if any(name not in fields for name in required):
            return None, "header"
        return list(reader), None
    except csv.Error:
        return None, "csv.Error"


def tasks_usable(rows):
    """False when a row would be dropped and the failure count would look complete."""
    for row in rows:
        if not isinstance(row, dict):
            return False
        rid = (row.get("run_id") or "").strip()
        if not DESK_RUN_RE.fullmatch(rid):
            return False
        try:
            float(row.get("time") or "")
        except (TypeError, ValueError):
            return False
    return True


def sha256_file(status, path):
    try:
        return status.sha256_file(path)
    except OSError:
        return None


def file_fact(status, root, rel, repo_for_git):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return {"path": rel, "present": False, "sha256": None, "commit": None, "error": "missing"}
    digest = sha256_file(status, path)
    commit = git_file_commit(repo_for_git, rel) if repo_for_git else None
    return {"path": rel, "present": True, "sha256": digest, "commit": commit, "error": None}


def load_freshness(repo, status):
    rel = "scripts/evidence/freshness.json"
    path = os.path.join(repo, rel)
    doc, err = load_json(path)
    if err or not isinstance(doc, dict):
        return None, err or "not an object"
    if doc.get("provisional") is not True or doc.get("approved_trading_rule") is not False:
        return None, "freshness file is not marked provisional"
    if doc.get("kind") != "sightline_evidence_freshness_provisional":
        return None, "freshness kind"
    scan = doc.get("solana_scan") if isinstance(doc.get("solana_scan"), dict) else {}
    pre = doc.get("solada_prefilter") if isinstance(doc.get("solada_prefilter"), dict) else {}
    daily = doc.get("daily_status") if isinstance(doc.get("daily_status"), dict) else {}
    scan_s = scan.get("max_age_seconds")
    pre_s = pre.get("max_age_seconds")
    grace = daily.get("grace_after_slot_seconds")
    schedule = daily.get("schedule_file")
    if not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in (scan_s, pre_s, grace)):
        return None, "freshness limits are not positive integers"
    if not isinstance(schedule, str) or schedule != ".github/workflows/daily-status.yml":
        return None, "daily schedule_file is not the daily-status workflow"
    for label, block, expect in (
        ("scan_60min", scan, "solana_scan"),
        ("prefilter_150min", pre, "solada_prefilter"),
        ("daily_2h_after_scheduled_slot", daily, "daily_status"),
    ):
        if block.get("label") != label:
            return None, "freshness label missing for " + expect
    return {
        "scan_seconds": scan_s,
        "prefilter_seconds": pre_s,
        "daily_grace_seconds": grace,
        "schedule_file": schedule,
        "sha256": sha256_file(status, path),
    }, None


def read_daily_cron(repo, rel):
    path = os.path.join(repo, rel)
    text, err = read_text(path)
    if err:
        return None
    found = CRON_RE.findall(text)
    if len(found) != 1:
        return None
    minute, hour = int(found[0][0]), int(found[0][1])
    if minute > 59 or hour > 23:
        return None
    return minute, hour


def _slot_epoch(year, month, day, hour, minute):
    return calendar.timegm((year, month, day, hour, minute, 0, 0, 0, 0))


def _utc_date(epoch):
    year, month, day = time.gmtime(int(epoch))[:3]
    return date(year, month, day)


def daily_required_dates(as_of_epoch, hour, minute, grace):
    """UTC dates of the slot that must already exist, and the latest due slot."""
    clock = _utc_date(as_of_epoch)
    today = _slot_epoch(clock.year, clock.month, clock.day, hour, minute)
    latest = today if as_of_epoch >= today else today - 86400
    required = latest if as_of_epoch >= latest + grace else latest - 86400
    return _utc_date(required).isoformat(), _utc_date(latest).isoformat()


def age_freshness(label, limit, source_epoch, as_of_epoch, source_field):
    body = {
        "status": "provisional",
        "label": label,
        "limit_seconds": limit,
        "provisional": True,
        "approved_trading_rule": False,
        "source_field": source_field,
        "age_seconds": None,
        "stale": None,
        "reason": None,
    }
    if as_of_epoch is None:
        body["status"] = "unknown"
        body["reason"] = "no as-of input; freshness was not judged"
        return body
    if source_epoch is None:
        body["status"] = "unknown"
        body["reason"] = "source timestamp was not read"
        return body
    age = as_of_epoch - source_epoch
    body["age_seconds"] = age
    if age < 0:
        body["status"] = "unknown"
        body["reason"] = "source timestamp is after as-of"
        return body
    stale = age > limit
    body["stale"] = stale
    if stale:
        body["reason"] = "older than the provisional limit"
    return body


def slot_freshness(label, grace, report_date, required_date, latest_date, as_of_epoch, cron_ok):
    body = {
        "status": "provisional",
        "label": label,
        "grace_after_slot_seconds": grace,
        "provisional": True,
        "approved_trading_rule": False,
        "required_date": required_date,
        "latest_due_date": latest_date,
        "report_date": report_date,
        "stale": None,
        "reason": None,
    }
    if as_of_epoch is None:
        body["status"] = "unknown"
        body["reason"] = "no as-of input; freshness was not judged"
        return body
    if not cron_ok:
        body["status"] = "unknown"
        body["reason"] = "daily-status cron was not read"
        return body
    if report_date is None or required_date is None:
        body["status"] = "unknown"
        body["reason"] = "daily status date was not read"
        return body
    report = _date(report_date)
    required = _date(required_date)
    latest = _date(latest_date)
    if report is None or required is None or latest is None:
        body["status"] = "unknown"
        body["reason"] = "daily status date was not read"
        return body
    if report > latest:
        body["status"] = "unknown"
        body["reason"] = "daily status date is after the latest due slot"
        return body
    fresh = required <= report <= latest
    body["stale"] = not fresh
    if not fresh:
        body["reason"] = "daily status date does not cover the slot required by the cron plus the provisional grace"
    return body


def _date(value):
    if not isinstance(value, str) or not DAY_RE.fullmatch(value):
        return None
    try:
        year, month, day = (int(part) for part in value.split("-"))
        return date(year, month, day)
    except ValueError:
        return None


def unsupported_freshness(reason):
    return {
        "status": "unsupported",
        "label": None,
        "provisional": False,
        "approved_trading_rule": False,
        "stale": None,
        "reason": reason,
    }


def not_applicable_freshness(reason):
    return {
        "status": "not_applicable",
        "label": None,
        "provisional": False,
        "approved_trading_rule": False,
        "stale": None,
        "reason": reason,
    }


def file_state(completeness, freshness, extra_unknown=None):
    missing = completeness["required_missing"]
    bad = completeness["unreadable"]
    present = completeness["required_present"]
    if not present and missing:
        return "missing"
    if bad:
        return "failed"
    if missing:
        return "partial"
    if extra_unknown:
        return "unknown"
    status = freshness.get("status")
    if status in ("unknown", "unsupported"):
        return "unknown"
    if freshness.get("stale") is True:
        return "stale"
    if freshness.get("stale") is False and status == "provisional":
        return "ok"
    return "unknown"


def safe_pos_id(value):
    return isinstance(value, str) and POS_ID_RE.fullmatch(value) is not None


def build_lots(rows, book):
    """Same lot walk as day14_verdict.build_closes, per book. refill is not a lot."""
    lots = defaultdict(list)
    built = []
    ok = True
    for row in rows:
        if not isinstance(row, dict):
            ok = False
            continue
        if row.get("book") != book or row.get("action") not in ("buy", "sell"):
            continue
        mint = row.get("mint") or ""
        try:
            ts = int(float(row.get("time") or ""))
        except (TypeError, ValueError):
            ok = False
            continue
        if row.get("action") == "buy":
            lots[mint].append({"mint": mint, "opened": ts, "closed": None, "pnl": 0.0, "frac": 0.0})
            continue
        if row.get("pnl") in ("", None):
            ok = False
            continue
        if not lots[mint]:
            ok = False
            continue
        lot = lots[mint][0]
        try:
            lot["pnl"] += float(row["pnl"])
            lot["frac"] += float(row.get("frac") or 0)
        except (TypeError, ValueError):
            ok = False
            continue
        lot["closed"] = ts
        if lot["frac"] >= FRAC_DONE:
            built.append(lots[mint].pop(0))
    still_open = []
    for mint in lots:
        still_open.extend(lots[mint])
    return built, still_open, ok


def match_closed(built, closed_rows):
    used = set()
    ids = []
    unmatched_ids = []
    withheld = 0
    ok = True
    rows = [row for row in closed_rows if isinstance(row, dict)]
    if len(rows) != len(closed_rows):
        ok = False
    for row in rows:
        hit = None
        for i, lot in enumerate(built):
            if i in used:
                continue
            try:
                same = (
                    lot["mint"] == row.get("mint")
                    and lot["opened"] == int(row["opened"])
                    and lot["closed"] == int(row["closed"])
                    and abs(lot["pnl"] - float(row["pnl"])) <= PNL_TOL
                )
            except (TypeError, ValueError, KeyError):
                same = False
            if same:
                hit = i
                break
        if hit is None:
            ok = False
            if safe_pos_id(row.get("id")):
                unmatched_ids.append(row["id"])
            elif row.get("id"):
                withheld += 1
            continue
        used.add(hit)
        if safe_pos_id(row.get("id")):
            ids.append(row["id"])
        else:
            ok = False
            withheld += 1
    if len(used) != len(built):
        ok = False
    return sorted(ids), sorted(unmatched_ids), withheld, ok


def match_open(still_open, open_map):
    ids = []
    withheld = 0
    ok = True
    if not isinstance(open_map, dict):
        return [], 0, False
    used = set()
    for pid in sorted(open_map, key=lambda item: str(item)):
        pos = open_map[pid]
        if not isinstance(pos, dict):
            ok = False
            continue
        hit = None
        for i, lot in enumerate(still_open):
            if i in used or lot.get("closed") is not None:
                continue
            try:
                same = lot["mint"] == pos.get("mint") and lot["opened"] == int(pos["opened"])
            except (TypeError, ValueError):
                same = False
            if same:
                hit = i
                break
        if hit is None:
            ok = False
            if safe_pos_id(pid):
                withheld += 0
            continue
        used.add(hit)
        if safe_pos_id(pid):
            ids.append(pid)
        else:
            ok = False
            withheld += 1
    if len(used) != len(still_open):
        ok = False
    return sorted(ids), withheld, ok


def book_map(positions):
    books = positions.get("books") if isinstance(positions, dict) else None
    if not isinstance(books, dict):
        return {}
    return books


def recompute_books(trade_rows, positions):
    """Full lot recompute. n is null when the trade file was not read."""
    if trade_rows is None:
        return {
            "ok": False,
            "n": None,
            "ids": [],
            "by_book": {book: None for book in BOOKS},
            "unmatched_lots": None,
            "unmatched_position_ids": [],
            "withheld_position_ids": None,
            "reason": "solana-desk/trades.csv is missing or unreadable",
        }, {
            "ok": False,
            "n": None,
            "ids": [],
            "reason": "solana-desk/trades.csv is missing or unreadable",
        }
    books = book_map(positions)
    close_ids = []
    fill_ids = []
    by_book = {}
    unmatched_ids = []
    withheld = 0
    unmatched_lots = 0
    ok = positions is not None
    reason = None if positions is not None else "solana-desk/positions.json is missing or unreadable"
    for book in BOOKS:
        built, still_open, lots_ok = build_lots(trade_rows, book)
        block = books.get(book) if isinstance(books.get(book), dict) else {}
        closed_rows = block.get("closed") if isinstance(block.get("closed"), list) else []
        if book not in books:
            ok = False
            reason = "positions.json has no " + book + " book"
        matched, unmatched, held, closed_ok = match_closed(built, closed_rows)
        open_ids, open_held, open_ok = match_open(still_open, block.get("open") if isinstance(block.get("open"), dict) else {})
        if not lots_ok or not closed_ok or not open_ok:
            ok = False
            if reason is None:
                reason = "book " + book + " trades and positions do not reconcile"
        close_ids.extend(matched)
        fill_ids.extend(matched)
        fill_ids.extend(open_ids)
        by_book[book] = len(matched)
        unmatched_ids.extend(unmatched)
        withheld += held + open_held
        unmatched_lots += max(0, len(built) - len(matched))
    duplicate = len(close_ids) != len(set(close_ids)) or len(fill_ids) != len(set(fill_ids))
    if duplicate:
        ok = False
        reason = "duplicate position id"
    n_close = None if duplicate else len(close_ids)
    n_fill = None if duplicate else len(set(fill_ids))
    closes = {
        "ok": ok,
        "n": n_close,
        "ids": sorted(set(close_ids)),
        "by_book": by_book,
        "unmatched_lots": unmatched_lots,
        "unmatched_position_ids": sorted(set(unmatched_ids)),
        "withheld_position_ids": withheld,
        "reason": None if ok else reason,
    }
    # n stays a count of matched ids even when reconciliation failed, and ok says
    # the count is not a complete ledger. Callers must not treat ok false as zero.
    fills = {
        "ok": ok,
        "n": n_fill,
        "ids": sorted(set(fill_ids)),
        "reason": None if ok else reason,
    }
    if not ok and not close_ids and positions is None:
        closes["n"] = None
        fills["n"] = None
    return closes, fills


def health_flags(doc, status):
    if not isinstance(doc, dict):
        return None
    flags = {}
    for key in HEALTH_REQUIRED:
        value = doc.get(key)
        if isinstance(value, bool):
            flags[key] = value
        else:
            return None
    for key in HEALTH_OPTIONAL:
        value = doc.get(key)
        if isinstance(value, bool):
            flags[key] = value
    mode = doc.get("mode")
    if isinstance(mode, str):
        code = status.public_code(mode)
        if code:
            flags["mode"] = code
    return flags


def _ast_value(node):
    """Literal or a leading minus. Rejects arithmetic so a formula is not a threshold."""
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        if isinstance(node.value, (int, float, str)):
            return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _ast_value(node.operand)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if isinstance(node.op, ast.UAdd) else -value
    return None


def config_constant(status, repo, name):
    """Read one config assignment. status is unused; the signature stays local to this pilot."""
    del status
    path = os.path.join(repo, "solana-desk", "desk", "config.py")
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, UnicodeError, SyntaxError):
        return None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == name:
                return _ast_value(node.value)
    return None


def verdict_inputs(repo, positions, trade_rows, status):
    """Day-14 inputs from the frozen module. finality is not issued here."""
    blank = {
        "ok": False,
        "reason": None,
        "inputs": None,
        "lines": {
            "f_closes_meet_minimum": None,
            "f_expectancy_below_stop": None,
            "f_rule_would_stop": None,
        },
    }
    if not isinstance(positions, dict) or trade_rows is None:
        blank["reason"] = "positions.json and trades.csv are both required for the day-14 inputs"
        return blank
    try:
        mod = day14_module(repo)
        mod.assert_registered()
    except (OSError, PacketError, RuntimeError, SyntaxError, ImportError, AttributeError) as exc:
        blank["reason"] = "day-14 module: " + type(exc).__name__
        return blank
    cfg_min = config_constant(status, repo, "MIN_F_CLOSES")
    cfg_stop = config_constant(status, repo, "EARLY_STOP_EXP")
    cfg_ticket = config_constant(status, repo, "TICKET_SOL")
    cfg_void = config_constant(status, repo, "DATA_FAIL_VOID")
    if cfg_min != mod.MIN_CLOSES or cfg_stop != mod.STOP_EXP or cfg_ticket != mod.TICKET:
        blank["reason"] = "day-14 constants do not match solana-desk/desk/config.py"
        return blank
    if not isinstance(cfg_void, (int, float)) or isinstance(cfg_void, bool):
        blank["reason"] = "DATA_FAIL_VOID was not a number"
        return blank
    try:
        closed, _opened = mod.f_records(positions)
        counted, voided, late = mod.select_closes(
            closed, mod.parse_utc(mod.CUTOFF_DEFAULT), mod.parse_utc(mod.VOID_START), mod.parse_utc(mod.VOID_END)
        )
        total = sum(float(row["pnl"]) for row in counted)
        rule, exp = mod.decide(len(counted), total, mod.TICKET, mod.MIN_CLOSES, mod.STOP_EXP)
        unique = mod.ids_unique(closed)
        same = mod.reconciles(closed, trade_rows)
    except (TypeError, ValueError, KeyError, ArithmeticError) as exc:
        blank["reason"] = "day-14 inputs: " + type(exc).__name__
        return blank
    if counted:
        total_dec = sum((Decimal(str(row["pnl"])) for row in counted), Decimal(0))
        exp_dec = total_dec / (Decimal(str(mod.TICKET)) * len(counted))
        below = exp_dec < Decimal(str(mod.STOP_EXP))
        exp_out = quant(exp_dec)
        total_out = quant(total_dec)
    else:
        below = False
        exp_out = None
        total_out = quant(0)
    float_below = exp is not None and exp < mod.STOP_EXP
    if below != float_below or (rule == "STOP") != (len(counted) >= mod.MIN_CLOSES and float_below):
        blank["reason"] = "day-14 decimal recompute disagreed with the frozen decide()"
        return blank
    last = positions.get("last_run_ts")
    try:
        last_ts = int(last) if last is not None and last != "" and not isinstance(last, bool) else None
    except (TypeError, ValueError):
        last_ts = None
    before = None if last_ts is None else last_ts < mod.parse_utc(mod.CUTOFF_DEFAULT)
    meet = len(counted) >= mod.MIN_CLOSES
    inputs = {
        "n_f_counted": len(counted),
        "total_pnl_sol": total_out,
        "expectancy_sol": exp_out,
        "min_closes": mod.MIN_CLOSES,
        "stop_exp": quant(mod.STOP_EXP),
        "ticket_sol": quant(mod.TICKET),
        "void_start": mod.VOID_START,
        "void_end": mod.VOID_END,
        "cutoff": mod.CUTOFF_DEFAULT,
        "closes_reconcile": bool(same),
        "close_ids_unique": bool(unique),
        "last_run_before_cutoff": before,
        "rule_result": rule,
        "excluded_void_entry": len(voided),
        "excluded_after_cutoff": len(late),
        "finality": "not_issued",
        "data_fail_void": quant(cfg_void),
    }
    return {
        "ok": True,
        "reason": None,
        "inputs": inputs,
        "lines": {
            "f_closes_meet_minimum": meet,
            "f_expectancy_below_stop": below,
            "f_rule_would_stop": rule == "STOP",
        },
        "data_fail_void": cfg_void,
    }


def rate_above_void(health, void_value):
    if not isinstance(void_value, (int, float)) or isinstance(void_value, bool):
        return None, "DATA_FAIL_VOID was not read"
    if not isinstance(health, dict):
        return None, "health.json was not read"
    rate = health.get("data_failure_rate")
    void_day = health.get("void_day")
    if rate is None:
        return None, "data_failure_rate is null"
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return None, "data_failure_rate is not a number"
    above = Decimal(str(rate)) > Decimal(str(void_value))
    if isinstance(void_day, bool) and void_day != above:
        return None, "void_day does not match data_failure_rate compared with DATA_FAIL_VOID"
    return above, None


def failed_runs(repo, task_rows, task_err=None):
    if task_rows is None:
        return {
            "ok": False,
            "desk_ids": [],
            "all_desk_run_n": None,
            "reason": task_err or "solana-desk/tasks.csv is missing or unreadable",
        }
    if not tasks_usable(task_rows):
        return {
            "ok": False,
            "desk_ids": [],
            "all_desk_run_n": None,
            "reason": "tasks.csv has a row without a run id or time; the failure count is unknown",
        }
    try:
        alert = alert_module(repo)
        runs = alert.collect(task_rows)
    except (OSError, PacketError, SyntaxError, ImportError, AttributeError, csv.Error) as exc:
        return {
            "ok": False,
            "desk_ids": [],
            "all_desk_run_n": None,
            "reason": "data_health_alert: " + type(exc).__name__,
        }
    ids = []
    for rid in sorted(runs):
        run = runs[rid]
        sources = run.get("sources") or {}
        if run.get("data_fails", 0) > 0 or any(sources.get(name, 0) > 0 for name in alert.SOURCES):
            ids.append(rid)
    return {
        "ok": True,
        "desk_ids": ids,
        "all_desk_run_ids": sorted(runs),
        "all_desk_run_n": len(runs),
        "reason": None,
    }


def scan_clock(status, health, timing_rows):
    """Newest scan timestamp that belongs to health.last_run_utc when that run is present."""
    field = None
    epoch = None
    desk_run = None
    if isinstance(health, dict):
        last = health.get("last_run_utc")
        if isinstance(last, str) and DESK_RUN_RE.fullmatch(last):
            desk_run = last
            text, got = stamp_from(status, last)
            if got is not None:
                field, epoch = "solana-desk/health.json last_run_utc", got
                _ = text
        times = health.get("run_times_utc")
        if isinstance(times, list):
            for value in times:
                _text, got = stamp_from(status, value)
                if got is not None and (epoch is None or got > epoch):
                    field, epoch = "solana-desk/health.json run_times_utc", got
    if timing_rows:
        for row in timing_rows:
            if not isinstance(row, dict):
                continue
            if desk_run and row.get("run_id") != desk_run:
                continue
            _text, got = stamp_from(status, row.get("started_utc"))
            if got is not None and (epoch is None or got >= epoch):
                field, epoch = "solana-desk/scan_timing.jsonl started_utc", got
    text = status.fmt_time(epoch) if epoch is not None else None
    return text, epoch, field, desk_run


def read_timing(path):
    text, err = read_text(path)
    if err:
        return None, err
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None, "JSONDecodeError"
        if not isinstance(row, dict):
            return None, "not an object"
        rows.append(row)
    return rows, None


def int_field(doc, key):
    value = doc.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def prefilter_fields(doc, status):
    if not isinstance(doc, dict):
        return None, "last-run.json is not an object"
    fields = {}
    absent = []
    for key in ("launches", "new_launches", "candidates", "flags_new"):
        value = int_field(doc, key)
        if value is None:
            absent.append(key)
        else:
            fields[key] = value
    if isinstance(doc.get("dry_run"), bool):
        fields["dry_run"] = doc["dry_run"]
    else:
        absent.append("dry_run")
    if isinstance(doc.get("baseline"), bool):
        fields["baseline"] = doc["baseline"]
    raw = doc.get("data_failures")
    if isinstance(raw, dict):
        failures = {}
        bad = False
        for key in sorted(raw):
            code = status.public_code(str(key))
            value = raw[key]
            if not code or isinstance(value, bool) or not isinstance(value, int):
                bad = True
                break
            failures[code] = value
        fields["data_failures"] = None if bad else failures
        if bad:
            fields["data_failures_reason"] = "data_failures has a key or count that is not a short code and an integer"
    else:
        absent.append("data_failures")
    if absent:
        fields["absent"] = absent
    return fields, None


def actions_snapshot(path):
    if not path:
        return None, "no actions snapshot"
    doc, err = load_json(path)
    if err or not isinstance(doc, dict):
        return None, err or "not an object"
    if doc.get("kind") != "sightline_actions_snapshot" or doc.get("schema_version") != 1:
        return None, "actions snapshot kind"
    if doc.get("fetched") is not True:
        return doc, doc.get("error") if isinstance(doc.get("error"), str) else "snapshot not fetched"
    return doc, None


def workflow_sources(snapshot, snap_err, slug):
    sources = []
    workflows = snapshot.get("workflows") if isinstance(snapshot, dict) else None
    if not isinstance(workflows, dict):
        workflows = {}
    known = snap_err is None and isinstance(snapshot, dict) and snapshot.get("fetched") is True
    for path in WORKFLOW_PATHS:
        row = workflows.get(path) if isinstance(workflows.get(path), dict) else None
        run = row.get("run") if isinstance(row, dict) else None
        run = run if isinstance(run, dict) else None
        latest = row.get("latest") if isinstance(row, dict) and isinstance(row.get("latest"), dict) else None
        if isinstance(row, dict) and "health_resolved" in row:
            health = run if row.get("health_resolved") is True else None
        else:
            # Older snapshots stored one run. A skipped pull request is not health.
            health = run
            if health and health.get("event") == "pull_request" and health.get("conclusion") == "skipped":
                health = None
            if latest is None:
                latest = health
        run_id = health.get("id") if isinstance(health, dict) and isinstance(health.get("id"), int) else None
        conclusion = health.get("conclusion") if isinstance(health, dict) else None
        status = health.get("status") if isinstance(health, dict) else None
        head = health.get("head_sha") if isinstance(health, dict) else None
        latest_id = latest.get("id") if isinstance(latest, dict) and isinstance(latest.get("id"), int) else None
        if not known:
            state = "unknown"
            reason = snap_err or "no actions snapshot"
            missing = ["actions_snapshot"]
            present = []
        elif row is None:
            state = "unknown"
            reason = "workflow was not in the snapshot"
            missing = [path]
            present = []
        elif row.get("error"):
            state = "unknown"
            reason = "workflow list was not read"
            missing = [path]
            present = []
        elif health is None:
            state = "unknown"
            reason = "no schedule, push, or workflow_dispatch run in the snapshot page; a skipped pull request is not healthy"
            missing = [path]
            present = []
        elif status != "completed":
            state = "unknown"
            reason = "relevant run is not completed"
            missing = []
            present = [path]
        elif conclusion in FAILED_CONCLUSIONS:
            state = "failed"
            reason = "relevant run conclusion is " + conclusion
            missing = []
            present = [path]
        elif conclusion == "success":
            state = "ok"
            reason = None
            missing = []
            present = [path]
        else:
            state = "unknown"
            reason = "relevant run conclusion is not success"
            missing = []
            present = [path]
        links = []
        for link_id in (run_id, latest_id):
            url = run_url(slug, link_id)
            if url and not any(item["url"] == url for item in links):
                links.append({"path": path, "url": url})
        sources.append({
            "id": "workflow:" + path,
            "state": state,
            "reason": reason,
            "as_of": health.get("run_started_at") if isinstance(health, dict) else None,
            "as_of_field": "actions run_started_at" if isinstance(run, dict) else None,
            "desk_run_id": None,
            "actions_run_id": run_id,
            "commit": head if isinstance(head, str) and SHA_RE.fullmatch(head) else None,
            "completeness": {
                "required_present": present,
                "required_missing": missing,
                "unreadable": [],
                "optional_missing": [],
            },
            "links": links,
            "freshness": not_applicable_freshness(
                "workflow health is the newest schedule, push, or workflow_dispatch run in the snapshot page, not an age limit"
            ),
            "files": [],
            "fields": {
                "conclusion": conclusion if isinstance(conclusion, str) else None,
                "run_status": status if isinstance(status, str) else None,
                "event": health.get("event") if isinstance(health, dict) else None,
                "run_duration_ms": health.get("run_duration_ms") if isinstance(health, dict) else None,
                "billable_ubuntu_total_ms": health.get("billable_ubuntu_total_ms") if isinstance(health, dict) else None,
                "latest_event": latest.get("event") if isinstance(latest, dict) else None,
                "latest_conclusion": latest.get("conclusion") if isinstance(latest, dict) else None,
                "latest_actions_run_id": latest_id,
            },
        })
    return sources


def actions_usage(snapshot, snap_err):
    """Timing only. The billing-account lookup is deferred and is not a quota."""
    deferred = "billing account lookup is deferred. Remaining quota is unknown. This is not a claim that usage is zero."
    base = {
        "source": "github_actions_timing_api" if snap_err is None else None,
        "equated_to_billed_minutes": False,
        "quota_remaining_known": False,
        "note": deferred if snap_err is None else (snap_err or "no actions snapshot"),
        "runs": [],
    }
    if not isinstance(snapshot, dict):
        return base
    runs = []
    workflows = snapshot.get("workflows") if isinstance(snapshot.get("workflows"), dict) else {}
    for path in WORKFLOW_PATHS:
        row = workflows.get(path)
        run = row.get("run") if isinstance(row, dict) else None
        if not isinstance(run, dict) or not isinstance(run.get("id"), int):
            continue
        runs.append({
            "workflow": path,
            "actions_run_id": run["id"],
            "run_duration_ms": run.get("run_duration_ms") if isinstance(run.get("run_duration_ms"), int) else None,
            "billable_ubuntu_total_ms": run.get("billable_ubuntu_total_ms") if isinstance(run.get("billable_ubuntu_total_ms"), int) else None,
        })
    base["runs"] = runs
    return base


def source_shell(source_id, state, reason, as_of, as_of_field, desk_run, actions_run, commit, completeness, links, freshness, files, fields):
    return {
        "id": source_id,
        "state": state,
        "reason": reason,
        "as_of": as_of,
        "as_of_field": as_of_field,
        "desk_run_id": desk_run,
        "actions_run_id": actions_run,
        "commit": commit,
        "completeness": completeness,
        "links": links,
        "freshness": freshness,
        "files": files,
        "fields": fields,
    }


def completeness_for(facts, required, optional):
    present, missing, bad = [], [], []
    opt_missing = []
    by_path = {fact["path"]: fact for fact in facts}
    for rel in required:
        fact = by_path.get(rel)
        if not fact or not fact["present"]:
            missing.append(rel)
        elif fact.get("error") and fact["error"] != "missing":
            bad.append(rel)
        else:
            present.append(rel)
    for rel in optional:
        fact = by_path.get(rel)
        if not fact or not fact["present"]:
            opt_missing.append(rel)
    return {
        "required_present": present,
        "required_missing": missing,
        "unreadable": bad,
        "optional_missing": opt_missing,
    }


def links_for(slug, facts):
    links = []
    for fact in facts:
        if not fact.get("present") or not fact.get("commit"):
            continue
        url = blob_url(slug, fact["commit"], fact["path"])
        if url:
            links.append({"path": fact["path"], "url": url})
    return links


def _id_list(value):
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if not safe_pos_id(item):
            return None
        out.append(item)
    return sorted(set(out))


def _desk_ids(value):
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if not isinstance(item, str) or not DESK_RUN_RE.fullmatch(item):
            return None
        out.append(item)
    return sorted(set(out))


def _action_ids(value):
    if not isinstance(value, list):
        return None
    out = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        if item < 1 or item > 10**15:
            return None
        out.append(item)
    return sorted(set(out))


def predicate(status_name, ids=None, fields=None, lines=None):
    body = {"status": status_name, "ids": ids or [], "fields": fields or [], "lines": lines or []}
    return body


def compare_ids(previous_ok, previous_ids, current_ok, current_ids):
    if not previous_ok or not current_ok or previous_ids is None or current_ids is None:
        return predicate("unknown"), None
    added = sorted(set(current_ids) - set(previous_ids))
    missing = sorted(set(previous_ids) - set(current_ids))
    status_name = "change" if added else "no_change"
    return predicate(status_name, ids=added), missing


def compare_flags(prev, curr):
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return predicate("unknown")
    for key in HEALTH_REQUIRED:
        if key not in prev or key not in curr:
            return predicate("unknown")
    changed = []
    keys = sorted(set(prev) | set(curr))
    for key in keys:
        if prev.get(key) != curr.get(key):
            changed.append(key)
    if changed:
        return predicate("change", fields=changed)
    return predicate("no_change")


def compare_lines(prev, curr):
    """A null required line makes the predicate unknown, even if other lines match."""
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return predicate("unknown")
    for key in REQUIRED_LINES:
        if not isinstance(prev.get(key), bool) or not isinstance(curr.get(key), bool):
            return predicate("unknown")
    changed = [key for key in REQUIRED_LINES if prev[key] != curr[key]]
    if changed:
        return predicate("change", lines=changed)
    return predicate("no_change")


def compare_inputs(prev, curr):
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return predicate("unknown")
    if prev.get("finality") != "not_issued" or curr.get("finality") != "not_issued":
        return predicate("unknown")
    changed = []
    for key in sorted(set(prev) | set(curr)):
        if key == "finality":
            continue
        if prev.get(key) != curr.get(key):
            changed.append(key)
    if changed:
        return predicate("change", fields=changed)
    return predicate("no_change")


def clock_changes(prev, curr):
    if not isinstance(prev, dict) or not isinstance(curr, dict):
        return []
    changed = []
    for key in sorted(set(prev) | set(curr)):
        if prev.get(key) != curr.get(key):
            changed.append(key)
    return changed


def load_previous(path, status):
    if not path:
        return None, "no previous packet"
    raw, err = read_text(path)
    if err or raw is None:
        return None, err or "unreadable"
    if text_fails(status, raw):
        return None, "previous packet failed the public-text check"
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return None, "JSONDecodeError"
    if not isinstance(doc, dict) or doc.get("schema_version") != 1 or doc.get("kind") != KIND:
        return None, "previous packet is not this schema"
    return doc, None


def section(doc, *keys):
    cur = doc
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def build_delta(previous, prev_err, prev_sha, closes, fills, failed, health, lines, inputs, clocks):
    not_impl = {name: predicate("not_implemented") for name in NOT_IMPLEMENTED}
    if previous is None:
        predicates = {name: predicate("unknown") for name in PREDICATES}
        predicates.update(not_impl)
        return {
            "previous_present": False,
            "previous_sha256": None,
            "previous_reason": prev_err,
            "material": None,
            "timestamp_only": False,
            "predicates": predicates,
            "dropped": {
                "status": "unknown",
                "close_ids": [],
                "fill_ids": [],
                "desk_failed_run_ids": [],
                "actions_note": "not_implemented: the snapshot stores the latest run, not the run history",
            },
        }
    prev_closes_ok = section(previous, "recompute", "closes", "ok") is True
    prev_fills_ok = section(previous, "recompute", "fills", "ok") is True
    prev_failed_ok = section(previous, "recompute", "failed_runs", "ok") is True
    close_pred, close_drop = compare_ids(
        prev_closes_ok, _id_list(section(previous, "recompute", "closes", "ids")),
        closes.get("ok") is True, closes.get("ids") if closes.get("ok") else None,
    )
    fill_pred, fill_drop = compare_ids(
        prev_fills_ok, _id_list(section(previous, "recompute", "fills", "ids")),
        fills.get("ok") is True, fills.get("ids") if fills.get("ok") else None,
    )
    prev_desk = _desk_ids(section(previous, "recompute", "failed_runs", "desk_ids"))
    curr_desk = failed.get("desk_ids") if failed.get("ok") else None
    prev_actions_known = section(previous, "recompute", "failed_runs", "actions_known") is True
    curr_actions_known = failed.get("actions_known") is True
    prev_actions = _action_ids(section(previous, "recompute", "failed_runs", "actions_ids"))
    curr_actions = failed.get("actions_ids") if curr_actions_known else None
    if not prev_failed_ok or not failed.get("ok") or prev_desk is None or curr_desk is None:
        fail_pred = predicate("unknown")
        desk_drop = None
    else:
        added = sorted(set(curr_desk) - set(prev_desk))
        all_ids = set(failed.get("all_desk_run_ids") or [])
        desk_drop = sorted(set(prev_desk) - all_ids)
        action_added = []
        actions_unknown = not (prev_actions_known and curr_actions_known and prev_actions is not None and curr_actions is not None)
        if not actions_unknown:
            action_added = sorted(set(curr_actions) - set(prev_actions))
        if added or action_added:
            fail_pred = predicate(
                "change",
                ids=[("desk:" + item) for item in added] + [("actions:" + str(item)) for item in action_added],
            )
        elif actions_unknown:
            fail_pred = predicate("unknown")
        else:
            fail_pred = predicate("no_change")
    health_pred = compare_flags(section(previous, "recompute", "health_flags"), health)
    line_pred = compare_lines(section(previous, "recompute", "threshold_lines"), lines)
    input_pred = compare_inputs(section(previous, "recompute", "verdict_inputs"), inputs)
    predicates = {
        "new_close_id": close_pred,
        "new_fill_id": fill_pred,
        "health_flip": health_pred,
        "new_failed_run_id": fail_pred,
        "board_threshold_crossing": line_pred,
        "material_verdict_inputs": input_pred,
    }
    predicates.update(not_impl)
    known_drops = [item for item in (close_drop, fill_drop, desk_drop) if item is not None]
    found_drop = any(known_drops)
    if found_drop:
        dropped_status = "found"
    elif close_drop is None or fill_drop is None or desk_drop is None:
        dropped_status = "unknown"
    else:
        dropped_status = "none"
    close_drop = close_drop or []
    fill_drop = fill_drop or []
    desk_drop = desk_drop or []
    changed = []
    unknown = False
    for name in PREDICATES:
        status_name = predicates[name]["status"]
        if status_name == "change":
            changed.append(name)
        elif status_name == "unknown":
            unknown = True
    if dropped_status == "found":
        material = True
    elif changed:
        material = True
    elif unknown or dropped_status == "unknown":
        material = None
    else:
        material = False
    clocks_changed = clock_changes(section(previous, "clocks"), clocks)
    timestamp_only = material is False and bool(clocks_changed)
    return {
        "previous_present": True,
        "previous_sha256": prev_sha,
        "previous_reason": None,
        "material": material,
        "timestamp_only": timestamp_only,
        "predicates": predicates,
        "dropped": {
            "status": dropped_status,
            "close_ids": close_drop,
            "fill_ids": fill_drop,
            "desk_failed_run_ids": desk_drop,
            "actions_note": "not_implemented: the snapshot stores the latest run, not the run history",
        },
    }


def build(repo, data, as_of=None, previous=None, actions_path=None, repo_slug=None):
    repo = os.path.abspath(repo)
    data = os.path.abspath(data) if data else repo
    status = status_module(repo)
    as_of_text, as_of_epoch = parse_as_of(as_of, status)
    freshness, fresh_err = load_freshness(repo, status)
    slug = github_slug(repo_slug) if repo_slug else None
    if slug is None and repo_slug:
        raise PacketError("--repo-slug is not owner/name")
    if slug is None:
        slug = remote_slug(repo)
    code_commit = git_head(repo)
    data_commit = git_head(data) if os.path.isdir(os.path.join(data, ".git")) or os.path.isfile(os.path.join(data, ".git")) else None
    # A worktree has a .git file. git_head already returns None when it is not a checkout.
    if data_commit is None:
        data_commit = git_head(data)

    def facts(rels):
        return [file_fact(status, data, rel, data) for rel in rels]

    scan_facts = facts(SCAN_FILES + SCAN_OPTIONAL)
    pre_facts = facts(PREFILTER_FILES)
    daily_facts = facts(DAILY_FILES)

    health_path = os.path.join(data, "solana-desk", "health.json")
    health_doc, health_err = (None, "missing")
    if os.path.isfile(health_path):
        health_doc, health_err = load_json(health_path)
        if health_err or not isinstance(health_doc, dict):
            health_doc, health_err = None, health_err or "not an object"
    trades, trade_err = (None, "missing")
    trade_path = os.path.join(data, "solana-desk", "trades.csv")
    if os.path.isfile(trade_path):
        trades, trade_err = read_table(trade_path, TRADE_NEED)
        if trade_err:
            trades = None
    tasks, task_err = (None, "missing")
    task_path = os.path.join(data, "solana-desk", "tasks.csv")
    if os.path.isfile(task_path):
        tasks, task_err = read_table(task_path, TASK_NEED)
        if task_err:
            tasks = None
    positions, pos_err = (None, "missing")
    pos_path = os.path.join(data, "solana-desk", "positions.json")
    if os.path.isfile(pos_path):
        positions, pos_err = load_json(pos_path)
        if pos_err or not isinstance(positions, dict):
            positions, pos_err = None, pos_err or "not an object"
    timing_rows = None
    timing_path = os.path.join(data, "solana-desk", "scan_timing.jsonl")
    if os.path.isfile(timing_path):
        timing_rows, timing_err = read_timing(timing_path)
        if timing_err:
            timing_rows = None
            for fact in scan_facts:
                if fact["path"] == "solana-desk/scan_timing.jsonl":
                    fact["error"] = timing_err

    scan_text, scan_epoch, scan_field, desk_run = scan_clock(status, health_doc, timing_rows)
    if freshness:
        scan_fresh = age_freshness("scan_60min", freshness["scan_seconds"], scan_epoch, as_of_epoch, scan_field)
    else:
        scan_fresh = unsupported_freshness(fresh_err or "freshness file was not read")
    scan_complete = completeness_for(scan_facts, SCAN_FILES, SCAN_OPTIONAL)
    if health_err and health_err != "missing":
        scan_complete["unreadable"].append("solana-desk/health.json")
        if "solana-desk/health.json" in scan_complete["required_present"]:
            scan_complete["required_present"].remove("solana-desk/health.json")
    if trade_err and trade_err != "missing":
        if "solana-desk/trades.csv" not in scan_complete["unreadable"]:
            scan_complete["unreadable"].append("solana-desk/trades.csv")
        if "solana-desk/trades.csv" in scan_complete["required_present"]:
            scan_complete["required_present"].remove("solana-desk/trades.csv")
    if task_err and task_err != "missing":
        if "solana-desk/tasks.csv" not in scan_complete["unreadable"]:
            scan_complete["unreadable"].append("solana-desk/tasks.csv")
        if "solana-desk/tasks.csv" in scan_complete["required_present"]:
            scan_complete["required_present"].remove("solana-desk/tasks.csv")
    if pos_err and pos_err != "missing":
        if "solana-desk/positions.json" not in scan_complete["unreadable"]:
            scan_complete["unreadable"].append("solana-desk/positions.json")
        if "solana-desk/positions.json" in scan_complete["required_present"]:
            scan_complete["required_present"].remove("solana-desk/positions.json")
    scan_complete["unreadable"] = sorted(set(scan_complete["unreadable"]))
    scan_state = file_state(scan_complete, scan_fresh)
    flags = health_flags(health_doc, status) if health_doc else None
    closes, fills = recompute_books(trades, positions)
    failed = failed_runs(repo, tasks, None if task_err == "missing" else task_err)
    verdict = verdict_inputs(repo, positions, trades, status)
    void_value = verdict.get("data_fail_void")
    above, above_reason = rate_above_void(health_doc, void_value)
    lines = dict(verdict["lines"])
    lines["data_failure_rate_above_void"] = above
    scan_fields = {
        "health_flags": flags,
        "data_failure_rate": rate_text(health_doc["data_failure_rate"]) if isinstance(health_doc, dict) and isinstance(health_doc.get("data_failure_rate"), (int, float)) and not isinstance(health_doc.get("data_failure_rate"), bool) else None,
        "data_failure_rate_reason": above_reason,
        "cfg_version": status.public_code(str(health_doc.get("cfg_version"))) if isinstance(health_doc, dict) and health_doc.get("cfg_version") else None,
        "day": health_doc.get("day") if isinstance(health_doc, dict) and isinstance(health_doc.get("day"), str) and DAY_RE.fullmatch(health_doc.get("day")) else None,
    }
    scan_source = source_shell(
        "solana_scan", scan_state, None if scan_state == "ok" else scan_fresh.get("reason"),
        scan_text, scan_field, desk_run, None, data_commit,
        scan_complete, links_for(slug, scan_facts), scan_fresh, scan_facts, scan_fields,
    )

    pre_path = os.path.join(data, "solada-prefilter", "last-run.json")
    pre_doc, pre_err = (None, "missing")
    if os.path.isfile(pre_path):
        pre_doc, pre_err = load_json(pre_path)
        if pre_err:
            pre_doc = None
    pre_text, pre_epoch = (None, None)
    if isinstance(pre_doc, dict):
        pre_text, pre_epoch = stamp_from(status, pre_doc.get("at"))
        if pre_epoch is not None:
            pre_text = status.fmt_time(pre_epoch)
    if freshness:
        pre_fresh = age_freshness("prefilter_150min", freshness["prefilter_seconds"], pre_epoch, as_of_epoch, "solada-prefilter/last-run.json at")
    else:
        pre_fresh = unsupported_freshness(fresh_err or "freshness file was not read")
    pre_complete = completeness_for(pre_facts, PREFILTER_FILES, ())
    if pre_err and pre_err != "missing":
        pre_complete["unreadable"] = ["solada-prefilter/last-run.json"]
        pre_complete["required_present"] = []
    pre_fields, _pre_field_err = prefilter_fields(pre_doc, status) if pre_doc else ({}, "missing")
    dry = pre_fields.get("dry_run") if isinstance(pre_fields, dict) else None
    extra = None
    if dry is True:
        extra = "last-run.json dry_run is true"
    elif dry is not False:
        extra = "dry_run was not false"
    pre_state = file_state(pre_complete, pre_fresh, extra)
    pre_source = source_shell(
        "solada_prefilter", pre_state, extra if pre_state == "unknown" and extra else pre_fresh.get("reason"),
        pre_text, "solada-prefilter/last-run.json at", None, None, data_commit,
        pre_complete, links_for(slug, pre_facts), pre_fresh, pre_facts, pre_fields or {},
    )

    daily_path = os.path.join(data, "reports", "status", "daily_status_latest.json")
    daily_doc, daily_err = (None, "missing")
    if os.path.isfile(daily_path):
        daily_doc, daily_err = load_json(daily_path)
        if daily_err:
            daily_doc = None
    report_date = None
    daily_kind_ok = False
    if isinstance(daily_doc, dict) and daily_doc.get("schema_version") == 1 and daily_doc.get("kind") == "sightline_daily_status":
        daily_kind_ok = True
        if isinstance(daily_doc.get("date"), str) and DAY_RE.fullmatch(daily_doc["date"]):
            report_date = daily_doc["date"]
    cron = None
    grace = None
    if freshness:
        cron = read_daily_cron(repo, freshness["schedule_file"])
        grace = freshness["daily_grace_seconds"]
    required_date = latest_date = None
    if cron and as_of_epoch is not None and grace:
        required_date, latest_date = daily_required_dates(as_of_epoch, cron[1], cron[0], grace)
    if freshness and grace:
        daily_fresh = slot_freshness(
            "daily_2h_after_scheduled_slot", grace, report_date, required_date, latest_date, as_of_epoch, cron is not None
        )
    else:
        daily_fresh = unsupported_freshness(fresh_err or "freshness file was not read")
    daily_complete = completeness_for(daily_facts, DAILY_FILES, ())
    if daily_err and daily_err != "missing":
        daily_complete["unreadable"] = ["reports/status/daily_status_latest.json"]
        daily_complete["required_present"] = []
    elif daily_doc is not None and not daily_kind_ok:
        daily_complete["unreadable"] = ["reports/status/daily_status_latest.json"]
        daily_complete["required_present"] = []
    daily_state = file_state(daily_complete, daily_fresh)
    daily_as_of = None
    if isinstance(daily_doc, dict) and isinstance(daily_doc.get("as_of"), dict):
        value = daily_doc["as_of"].get("value")
        if isinstance(value, str):
            daily_as_of, _epoch = stamp_from(status, value)
    daily_source = source_shell(
        "daily_status", daily_state, None if daily_state == "ok" else daily_fresh.get("reason"),
        report_date, "reports/status/daily_status_latest.json date", None, None, data_commit,
        daily_complete, links_for(slug, daily_facts), daily_fresh, daily_facts,
        {"schema_version": 1 if daily_kind_ok else None, "date": report_date, "evidence_as_of": daily_as_of},
    )

    fore_fresh = unsupported_freshness("forensics detail is deferred; no freshness limit is applied")
    fore_source = source_shell(
        "forensics", "unknown",
        "forensics detail is deferred in this pilot and is not treated as healthy",
        None, None, None, None, None,
        {
            "required_present": [],
            "required_missing": [],
            "unreadable": [],
            "optional_missing": [],
            "deferred": ["solada/forensics/index.jsonl"],
        },
        [], fore_fresh, [], {"deferred": True},
    )

    snapshot, snap_err = actions_snapshot(actions_path)
    # actions_snapshot returns (doc, err). err set when not fetched.
    if actions_path and snapshot is None:
        snap_state_err = snap_err
    elif isinstance(snapshot, dict) and snapshot.get("fetched") is not True:
        snap_state_err = snap_err or "snapshot not fetched"
    else:
        snap_state_err = None if actions_path else "no actions snapshot"
    workflow = workflow_sources(snapshot if snap_state_err is None else None, snap_state_err, slug)
    usage = actions_usage(snapshot if isinstance(snapshot, dict) else None, snap_state_err)

    action_failed = []
    # A fetched snapshot is not coverage. An errored workflow or a page with no
    # schedule, push, or workflow_dispatch run leaves the failure list unknown.
    action_known = (
        snap_state_err is None
        and len(workflow) == len(WORKFLOW_PATHS)
        and all(source["state"] in ("ok", "failed") for source in workflow)
    )
    if action_known:
        for source in workflow:
            if source["fields"].get("conclusion") in FAILED_CONCLUSIONS and isinstance(source["actions_run_id"], int):
                action_failed.append(source["actions_run_id"])
    failed = dict(failed)
    failed["actions_ids"] = sorted(set(action_failed))
    failed["actions_known"] = action_known

    clocks = {
        "solana_scan": scan_text,
        "solada_prefilter": pre_text,
        "daily_status_date": report_date,
        "forensics": None,
        "health_last_run_utc": desk_run,
    }
    prev_doc, prev_err = load_previous(previous, status)
    prev_sha = sha256_file(status, previous) if previous and os.path.isfile(previous) and prev_doc is not None else None
    delta = build_delta(prev_doc, prev_err, prev_sha, closes, fills, failed, flags, lines, verdict.get("inputs"), clocks)

    recompute = {
        "closes": closes,
        "fills": fills,
        "failed_runs": {
            "ok": failed.get("ok") is True,
            "desk_ids": failed.get("desk_ids") or [],
            "all_desk_run_n": failed.get("all_desk_run_n"),
            "actions_ids": failed.get("actions_ids") if action_known else [],
            "actions_known": action_known,
            "reason": None,
        },
        "health_flags": flags,
        "threshold_lines": lines,
        "threshold_reason": above_reason if above is None else verdict.get("reason"),
        "verdict_inputs": verdict.get("inputs"),
        "verdict_reason": verdict.get("reason"),
    }
    reasons = []
    if failed.get("ok") is not True and failed.get("reason"):
        reasons.append(failed["reason"])
    if not action_known:
        reasons.append(snap_state_err or "actions workflow coverage is unresolved")
    recompute["failed_runs"]["reason"] = "; ".join(reasons) if reasons else None

    sources = [scan_source, pre_source, daily_source, fore_source] + workflow
    doc = {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "ai": False,
        "coverage": {
            "full": False,
            "deferred": ["forensics_detail", "billing_account"],
            "predicates": {name: "implemented" for name in PREDICATES},
        },
        "provenance": {
            "builder": BUILDER,
            "schema_version": SCHEMA_VERSION,
            "code_commit": code_commit,
            "data_commit": data_commit,
            "repo": slug,
            "as_of_input": as_of_text,
            "freshness_file": "scripts/evidence/freshness.json",
            "freshness_sha256": None if not freshness else freshness.get("sha256"),
        },
        "as_of": {
            "value": as_of_text,
            "source": "cli --as-of" if as_of_text else None,
            "reason": None if as_of_text else "no as-of input; freshness was not judged",
        },
        "sources": sources,
        "recompute": recompute,
        "clocks": clocks,
        "delta": delta,
        "actions_usage": usage,
        "limitations": list(LIMITATIONS),
    }
    doc["coverage"]["predicates"].update({name: "not_implemented" for name in NOT_IMPLEMENTED})
    text = dumps(doc)
    if text_fails(status, text):
        raise PacketError("evidence packet failed the public-text check and was not written")
    return json.loads(text)


REPLAY_CONTRACT = "snapshot + manifest + pinned SHAs; replay requires checkouts at recorded SHAs"


def _required_sha(value, label):
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise PacketError(label + " is required")
    return value


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Files replay reads. Other generated files are not part of this check.
CODE_INPUTS = (
    ".github/workflows/daily-status.yml",
    "scripts/data_health_alert.py",
    "scripts/evidence/freshness.json",
    "scripts/evidence/packet.py",
    "scripts/evidence/requirements.txt",
    "scripts/status/daily_status.py",
    "solana-desk/desk/__init__.py",
    "solana-desk/desk/books.py",
    "solana-desk/desk/config.py",
    "solana-desk/desk/exits.py",
    "solana-desk/desk/fills.py",
    "solana-desk/desk/ledger.py",
    "solana-desk/desk/observe.py",
    "solana-desk/specs/day14_verdict.py",
)
DATA_INPUTS = (
    "reports/status/daily_status_latest.json",
    "solada-prefilter/last-run.json",
    "solana-desk/health.json",
    "solana-desk/positions.json",
    "solana-desk/scan_timing.jsonl",
    "solana-desk/tasks.csv",
    "solana-desk/trades.csv",
)


def _input_status(repo, paths):
    """Porcelain lines for replay inputs, including untracked and ignored files.

    A failed or missing git status is not a clean tree.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain=v1", "--ignored", "--untracked-files=all", "--", *paths],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PacketError("git status failed") from exc
    if proc.returncode != 0:
        raise PacketError("git status failed")
    return [line for line in (proc.stdout or "").splitlines() if line.strip()]


def retain_replay(out_dir, as_of, snapshot_path, code_commit, data_commit, repo, previous_path=None, code_repo=None):
    """Copy the snapshot and manifest. Desk-data bytes are not copied.

    Replay is not offline. It needs checkouts whose HEAD matches the recorded SHAs.
    """
    if not isinstance(as_of, str) or not AS_OF_RE.fullmatch(as_of):
        raise PacketError("as-of must be YYYY-MM-DDTHH:MM:SSZ")
    if not snapshot_path or not os.path.isfile(snapshot_path):
        raise PacketError("actions snapshot is missing")
    if not code_repo or not os.path.isdir(code_repo):
        raise PacketError("code checkout is missing")
    code_sha = _required_sha(code_commit, "code commit")
    data_sha = _required_sha(data_commit, "data commit")
    slug = github_slug(repo) if repo else None
    if not slug:
        raise PacketError("repo slug")
    req_path = os.path.join(code_repo, "scripts", "evidence", "requirements.txt")
    if not os.path.isfile(req_path):
        raise PacketError("requirements.txt is missing")
    replay_dir = os.path.join(out_dir, "replay")
    os.makedirs(replay_dir, exist_ok=True)
    snap_dest = os.path.join(replay_dir, "actions-snapshot.json")
    shutil.copyfile(snapshot_path, snap_dest)
    inputs = {"actions-snapshot.json": _file_sha256(snap_dest)}
    prev_rel = None
    prev_dest = None
    if previous_path:
        if not os.path.isfile(previous_path):
            raise PacketError("previous packet is missing")
        prev_rel = "previous-packet.json"
        prev_dest = os.path.join(replay_dir, prev_rel)
        shutil.copyfile(previous_path, prev_dest)
        inputs[prev_rel] = _file_sha256(prev_dest)
    doc = {
        "schema_version": 1,
        "kind": "sightline_evidence_replay",
        "contract": REPLAY_CONTRACT,
        "as_of": as_of,
        "code_commit": code_sha,
        "data_commit": data_sha,
        "repo": slug,
        "python_version": sys.version.split()[0],
        "requirements_sha256": _file_sha256(req_path),
        "inputs": inputs,
        "actions_snapshot": "actions-snapshot.json",
        "previous_packet": prev_rel,
    }
    text = dumps(doc)
    if SECRET_RE.search(text):
        raise PacketError("replay metadata failed the secret check and was not written")
    path = os.path.join(replay_dir, "replay.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return {"as_of": as_of, "snapshot": snap_dest, "previous": prev_dest, "replay": path}


def _load_replay(bundle_dir):
    path = os.path.join(bundle_dir, "replay", "replay.json")
    raw, err = read_text(path)
    if err or raw is None:
        raise PacketError("replay.json is missing")
    if SECRET_RE.search(raw):
        raise PacketError("replay metadata failed the secret check")
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PacketError("replay.json is not JSON") from exc
    if not isinstance(doc, dict) or doc.get("kind") != "sightline_evidence_replay":
        raise PacketError("replay.json is not a replay")
    if doc.get("contract") != REPLAY_CONTRACT:
        raise PacketError("replay contract")
    return doc


def _check_pinned(repo, expected, label, inputs):
    _required_sha(expected, label)
    head = git_head(repo)
    if head != expected:
        raise PacketError(label + " does not match checkout HEAD")
    if _input_status(repo, inputs):
        raise PacketError(label + " replay input is not a clean tracked file")


def _check_inputs(bundle_dir, doc, code_repo):
    inputs = doc.get("inputs")
    if not isinstance(inputs, dict) or "actions-snapshot.json" not in inputs:
        raise PacketError("replay inputs")
    req_path = os.path.join(code_repo, "scripts", "evidence", "requirements.txt")
    if not os.path.isfile(req_path) or _file_sha256(req_path) != doc.get("requirements_sha256"):
        raise PacketError("requirements sha256 does not match")
    if sys.version.split()[0] != doc.get("python_version"):
        raise PacketError("python version does not match")
    for name, digest in sorted(inputs.items()):
        if name not in ("actions-snapshot.json", "previous-packet.json"):
            raise PacketError("replay input name")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise PacketError("replay input sha256")
        path = os.path.join(bundle_dir, "replay", name)
        if not os.path.isfile(path) or _file_sha256(path) != digest:
            raise PacketError("retained input sha256 does not match")
    if doc.get("previous_packet") and "previous-packet.json" not in inputs:
        raise PacketError("replay previous hash")


def rebuild_from_replay(bundle_dir, repo, data, out_dir):
    """Rebuild only when checkouts match the recorded SHAs and retained hashes match.

    Desk-data is not inside the bundle. The data checkout must already be the recorded commit.
    """
    doc = _load_replay(bundle_dir)
    _check_inputs(bundle_dir, doc, repo)
    _check_pinned(repo, doc.get("code_commit"), "code commit", CODE_INPUTS)
    _check_pinned(data, doc.get("data_commit"), "data commit", DATA_INPUTS)
    snap = os.path.join(bundle_dir, "replay", "actions-snapshot.json")
    prev = None
    if doc.get("previous_packet"):
        prev = os.path.join(bundle_dir, "replay", "previous-packet.json")
    built = build(repo, data, doc.get("as_of"), prev, snap, doc.get("repo"))
    return write_packet(built, out_dir)


def upload_blocked(paths, code_repo):
    """True when any file that would be uploaded fails the public-text check."""
    status = status_module(code_repo)
    for path in paths:
        raw, err = read_text(path)
        if err or raw is None or text_fails(status, raw):
            return True
    return False


def write_packet(doc, out_dir):
    text = dumps(doc)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "evidence-packet.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build a read-only evidence packet from files already in a checkout.")
    parser.add_argument("--repo", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    parser.add_argument("--data", default=None, help="desk-data checkout. Defaults to --repo.")
    parser.add_argument("--as-of", dest="as_of", default=None, help="UTC timestamp YYYY-MM-DDTHH:MM:SSZ. Freshness uses this and nothing else.")
    parser.add_argument("--previous", default=None, help="Optional previous evidence packet.")
    parser.add_argument("--actions-snapshot", dest="actions", default=None)
    parser.add_argument("--repo-slug", dest="slug", default=None)
    parser.add_argument("--code-commit", dest="code_commit", default=None)
    parser.add_argument("--data-commit", dest="data_commit", default=None)
    parser.add_argument("--retain-replay", action="store_true")
    parser.add_argument("--check-upload", dest="check_upload", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    try:
        if args.check_upload:
            files = []
            for root, _dirs, names in os.walk(args.check_upload):
                for name in sorted(names):
                    files.append(os.path.join(root, name))
            if not files or upload_blocked(files, args.repo):
                raise PacketError("upload blocked by the public-text check")
            return 0
        if not args.out:
            raise PacketError("--out is required")
        as_of = args.as_of
        previous = args.previous
        actions = args.actions
        if args.retain_replay:
            kept = retain_replay(
                args.out, args.as_of, args.actions, args.code_commit, args.data_commit,
                args.slug, args.previous, args.repo,
            )
            as_of = kept["as_of"]
            previous = kept["previous"]
            actions = kept["snapshot"]
        doc = build(args.repo, args.data, as_of, previous, actions, args.slug)
        path = write_packet(doc, args.out)
    except PacketError as exc:
        print("evidence packet: " + str(exc), file=sys.stderr)
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
