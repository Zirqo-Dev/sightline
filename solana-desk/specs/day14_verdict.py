"""Frozen day-14 early stop. Analysis only: this file does not enter, exit, or sample.

Rule, as registered in config v1-v6.5 (plan v6.2): STOP if book F has at least
20 realized closes and F expectancy after costs is worse than -0.15 per trade.
Otherwise CONTINUE. The script checks those constants against config at runtime
and stops if they have drifted.

Expectancy = total realized SOL pnl / (0.5 * number of F closes).
Raw mean. No winsorizing. No confidence interval in the decision.

Realized closes only. An open position does not count, including a partial
take already booked on it, and nothing is marked to market. A close at or
before the cutoff counts. A close after the cutoff does not exist for this run.
Closes land 22-37s after their 30-minute slot, so the 10:00 America/Toronto
run (slot 20261111T1500) closes after 15:00:00Z. The last slot whose closes
can count is 20261111T1430.

Costs: COST_VERSION is as_logged. Pre-v3 closes are counted. The figure is the
`pnl` cell on each sell row in trades.csv (the position total is that sum).
The dashboard's 7-day and 28-day stats count only cost_model v3-exit-aware.
That convention does not govern this verdict. Dropping a close because its
cost label is older is barred after the sample has been seen. trades.csv
`net_pnl` is incomplete on older rows and must not be used.

The output is VERDICT: NOT_FINAL unless all of these hold: the data's last
run timestamp is at or after the cutoff, the F closed list reconciles with
trades.csv (positions.json keeps only the last CLOSED_KEEP closes per book),
close ids are unique, and --cutoff is the default UTC instant with a trailing
Z. A non-default cutoff is computed and marked non-binding. The rule's
STOP or CONTINUE is still printed as rule_result.

Voids: a close counts only when its entry timestamp is outside the committed
window [2026-10-31T01:30:00Z, 2026-11-01T00:00:00Z). There is no single stored
RugCheck outage window. tasks.csv shows rugcheck missing_data from slot
20261031T0130 through 20261031T1530. positions.json voids are per-run
data_failures_over_10pct from 2026-10-31T02:00:29Z through
2026-10-31T23:30:26Z, which keeps going after RugCheck returned because the
UTC-day rate stayed above 10%. The frozen code applied that void
prospectively: a run after the rate crossed 10% opens nothing, and entries
already opened stay.

Rejected reading: the plan's phrase "that day's new entries voided" would
also drop entries opened on 31 Oct before the 01:30 slot. On desk-data
b92055f that is 7 F closes and about -1.40 SOL. It is rejected because the
RugCheck missing-data rows start at the 01:30 slot and the code did not
reach back and void the earlier entries.
"""
import argparse, calendar, hashlib, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.path.dirname(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desk import config as C
from desk import fills

# Registered application of the frozen rule. assert_registered() checks config.
COST_VERSION = "as_logged"
MIN_CLOSES = 20
STOP_EXP = -0.15
TICKET = 0.5
# 2026-11-11 10:00 America/Toronto. DST ended 2026-11-01, so that clock is EST (UTC-5).
CUTOFF_DEFAULT = "2026-11-11T15:00:00Z"
VOID_START = "2026-10-31T01:30:00Z"
VOID_END = "2026-11-01T00:00:00Z"
VOID_SOURCE = "committed_constant"
LAST_COUNTED_RUN = "20261111T1430"
PNL_TOL = 1e-8

VOID_NOTE = (
    "No single RugCheck outage window is stored. "
    "tasks.csv has rugcheck missing_data on slots 20261031T0130 through 20261031T1530 "
    "(fail_reason blank that day). "
    "positions.json voids are data_failures_over_10pct from 2026-10-31T02:00:29Z "
    "through 2026-10-31T23:30:26Z, including hours after RugCheck returned, "
    "because the UTC-day rate stayed above 10%. "
    "The frozen code applied that prospectively. Entries already opened stay."
)
WHOLE_DAY_NOTE = (
    "Rejected: the plan phrase 'that day's new entries voided' would also drop entries "
    "opened on 2026-10-31 before 01:30 UTC. RugCheck missing_data starts at slot "
    "20261031T0130, and the frozen code did not reclassify those earlier entries."
)
LAST_RUN_NOTE = (
    "Closes land 22-37s after their slot, so slot 20261111T1500 closes after 15:00:00Z. "
    "The last slot whose closes can fall at or before the default cutoff is 20261111T1430."
)
COST_NOTE = (
    "Pre-v3 closes are counted. The dashboard's v3-only 7-day and 28-day stats do not govern "
    "this verdict. Dropping closes because their cost label is older is barred post-peek. "
    "trades.csv net_pnl is incomplete and is not used; the sell row's pnl cell is the logged net."
)


def assert_registered():
    """Fail closed if the frozen constants this verdict applies are no longer the config."""
    bad = []
    if C.MIN_F_CLOSES != MIN_CLOSES:
        bad.append("MIN_F_CLOSES")
    if C.EARLY_STOP_EXP != STOP_EXP:
        bad.append("EARLY_STOP_EXP")
    if C.TICKET_SOL != TICKET:
        bad.append("TICKET_SOL")
    if fills.CLOSED_KEEP != 200:
        bad.append("CLOSED_KEEP")
    if COST_VERSION != "as_logged":
        bad.append("COST_VERSION")
    if CUTOFF_DEFAULT != "2026-11-11T15:00:00Z":
        bad.append("CUTOFF")
    if (VOID_START, VOID_END) != ("2026-10-31T01:30:00Z", "2026-11-01T00:00:00Z"):
        bad.append("VOID_WINDOW")
    if bad:
        raise RuntimeError("day-14 constants drifted: " + ",".join(bad))


def parse_utc(text):
    """Require a trailing Z. A naive timestamp or a numeric offset is rejected."""
    if not isinstance(text, str) or not text.endswith("Z"):
        raise ValueError("cutoff must be UTC and end with Z")
    body = text[:-1]
    if len(body) != 19:
        raise ValueError("cutoff must be YYYY-MM-DDTHH:MM:SSZ")
    return calendar.timegm(time.strptime(body, "%Y-%m-%dT%H:%M:%S"))


def fmt_sol(x):
    return f"{x:.9f}"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_blob_sha256(commit, relpath):
    """sha256 of one path at a commit, or None when that object is not in this repo."""
    if not isinstance(commit, str) or len(commit) != 40:
        return None
    if any(c not in "0123456789abcdef" for c in commit.lower()):
        return None
    try:
        data = subprocess.check_output(
            ["git", "-C", REPO, "show", f"{commit}:{relpath}"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    return hashlib.sha256(data).hexdigest()


def load_json(path):
    with open(path) as fh:
        return json.load(fh)


def load_trades(path):
    import csv
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def f_records(positions):
    book = (positions.get("books") or {}).get("F") or {}
    return list(book.get("closed") or []), list((book.get("open") or {}).values())


def select_closes(closed, cutoff_ts, void_start, void_end):
    """Realized F closes that exist at the cutoff and whose entry is outside the void window."""
    counted, voided, late = [], [], []
    for row in closed:
        opened = int(row["opened"])
        done = int(row["closed"])
        if void_start <= opened < void_end:
            voided.append(row)
            continue
        if done > cutoff_ts:
            late.append(row)
            continue
        counted.append(row)
    return counted, voided, late


def decide(n, total, ticket, min_closes, stop_exp):
    """STOP only when n is at least the minimum and expectancy is strictly worse than stop_exp."""
    exp = None if n <= 0 else total / (ticket * n)
    stop = n >= min_closes and exp is not None and exp < stop_exp
    return ("STOP" if stop else "CONTINUE"), exp


def build_closes(trades):
    """F closes from append-only trades.csv, using pnl (never net_pnl).

    A buy opens a lot. Sells for that mint fill the oldest open lot. The lot
    closes when the sold fraction reaches 1. Returns (closes, ok). ok is false
    when a sell has no lot or a blank pnl cell.
    """
    from collections import defaultdict
    lots = defaultdict(list)
    built = []
    ok = True
    for row in trades:
        if row.get("book") != "F" or row.get("action") not in ("buy", "sell"):
            continue
        mint = row.get("mint") or ""
        try:
            ts = int(float(row.get("time") or ""))
        except (TypeError, ValueError):
            ok = False
            continue
        if row.get("action") == "buy":
            lots[mint].append(dict(mint=mint, opened=ts, closed=None, pnl=0.0, frac=0.0))
            continue
        if row.get("pnl") in ("", None):
            ok = False
            continue
        if not lots[mint]:
            ok = False
            continue
        lot = lots[mint][0]
        lot["pnl"] += float(row["pnl"])
        try:
            lot["frac"] += float(row.get("frac") or 0)
        except (TypeError, ValueError):
            ok = False
            continue
        lot["closed"] = ts
        if lot["frac"] >= 1 - 1e-4:
            built.append(lots[mint].pop(0))
    return built, ok


def ids_unique(closed):
    ids = [row.get("id") for row in closed]
    return len(ids) == len(set(ids)) and all(ids)


def reconciles(closed, trades):
    """True when every positions.json F close is the same lot trades.csv still has."""
    built, ok = build_closes(trades)
    if not ok or not ids_unique(closed):
        return False
    if len(built) != len(closed):
        return False
    used = set()
    for row in closed:
        hit = None
        for i, lot in enumerate(built):
            if i in used:
                continue
            if lot["mint"] != row.get("mint"):
                continue
            if lot["opened"] != int(row["opened"]) or lot["closed"] != int(row["closed"]):
                continue
            if abs(lot["pnl"] - float(row["pnl"])) > PNL_TOL:
                continue
            hit = i
            break
        if hit is None:
            return False
        used.add(hit)
    return True


def whole_day_drop(closed, void_start):
    """F closes the rejected whole-UTC-day reading would remove and this script keeps."""
    day = time.strftime("%Y-%m-%d", time.gmtime(void_start))
    rows = []
    for row in closed:
        opened = int(row["opened"])
        if time.strftime("%Y-%m-%d", time.gmtime(opened)) == day and opened < void_start:
            rows.append(row)
    return rows


def render(positions, trades, cutoff_text, data_commit, positions_sha256, trades_sha256, commit_bytes="unchecked"):
    assert_registered()
    cutoff_ts = parse_utc(cutoff_text)
    void_start = parse_utc(VOID_START)
    void_end = parse_utc(VOID_END)
    closed, opened = f_records(positions)
    counted, voided, late = select_closes(closed, cutoff_ts, void_start, void_end)
    total = sum(float(row["pnl"]) for row in counted)
    rule, exp = decide(len(counted), total, TICKET, MIN_CLOSES, STOP_EXP)
    pre = sum(1 for row in counted if not str(row.get("cost_model") or "").strip())
    pre_all = 0
    for name in "FRS":
        for row in ((positions.get("books") or {}).get(name) or {}).get("closed") or []:
            if not str(row.get("cost_model") or "").strip():
                pre_all += 1
    v3 = [row for row in counted if str(row.get("cost_model") or "").strip() == "v3-exit-aware"]
    partial = sum(float(row.get("pnl") or 0) for row in opened)
    dropped = whole_day_drop(closed, void_start)
    drop_pnl = sum(float(row["pnl"]) for row in dropped)
    unique = ids_unique(closed)
    same = reconciles(closed, trades)
    binding = cutoff_text == CUTOFF_DEFAULT
    last = positions.get("last_run_ts")
    try:
        last_ts = int(last) if last is not None and last != "" else None
    except (TypeError, ValueError):
        last_ts = None
    reasons = []
    if not binding:
        reasons.append("cutoff_not_default")
    if last_ts is None:
        reasons.append("last_run_missing")
    elif last_ts < cutoff_ts:
        reasons.append("last_run_before_cutoff")
    if not unique:
        reasons.append("close_ids_not_unique")
    if not same:
        reasons.append("closes_do_not_reconcile")
    if commit_bytes == "mismatch":
        reasons.append("commit_bytes_mismatch")
    final = not reasons
    verdict = rule if final else "NOT_FINAL"
    if v3 and len(v3) != len(counted):
        v3_total = sum(float(row["pnl"]) for row in v3)
        v3_exp = v3_total / (TICKET * len(v3))
        v3_rule, _ = decide(len(v3), v3_total, TICKET, MIN_CLOSES, STOP_EXP)
        v3_line = (
            f"v3_only_not_used: n={len(v3)} expectancy={fmt_sol(v3_exp)} rule={v3_rule} "
            f"would_drop={len(counted) - len(v3)} of {len(counted)}"
        )
    else:
        v3_line = "v3_only_not_used: na"
    lines = [
        f"cutoff: {cutoff_text}",
        f"cutoff_binding: {'yes' if binding else 'no'}",
        f"cost_version: {COST_VERSION}",
        "pre_v3_counted: yes",
        "dashboard_v3_only_governs: no",
        f"cost_note: {COST_NOTE}",
        f"void_window_start: {VOID_START}",
        f"void_window_end: {VOID_END}",
        f"void_window_source: {VOID_SOURCE}",
        f"void_window_note: {VOID_NOTE}",
        "whole_day_void: rejected",
        f"whole_day_void_would_drop: {len(dropped)}",
        f"whole_day_void_pnl_sol: {fmt_sol(drop_pnl)}",
        f"whole_day_void_note: {WHOLE_DAY_NOTE}",
        f"last_counted_run: {LAST_COUNTED_RUN if binding else 'na'}",
        f"last_counted_run_note: {LAST_RUN_NOTE}",
        f"n_closes: {len(counted)}",
        f"excluded_void_entry: {len(voided)}",
        f"excluded_after_cutoff: {len(late)}",
        f"open_ignored: {len(opened)}",
        f"open_partial_pnl_excluded: {fmt_sol(partial)}",
        f"total_pnl_sol: {fmt_sol(total)}",
        f"expectancy: {'na' if exp is None else fmt_sol(exp)}",
        f"rule_result: {rule}",
        f"pre_v3_closes: {pre}",
        f"pre_v3_closed_positions_all_books: {pre_all}",
        v3_line,
        f"last_run_utc: {'missing' if last_ts is None else time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(last_ts))}",
        f"close_ids_unique: {'yes' if unique else 'no'}",
        f"closes_reconcile: {'yes' if same else 'no'}",
        f"finality: {'yes' if final else 'no'}",
        f"finality_reasons: {'none' if final else ','.join(reasons)}",
        f"positions_sha256: {positions_sha256}",
        f"trades_sha256: {trades_sha256}",
        f"commit_bytes: {commit_bytes}",
        f"data_commit: {data_commit}",
        f"VERDICT: {verdict}",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Frozen day-14 verdict. NOT_FINAL until the cutoff and the trade log agree.")
    p.add_argument("--data-dir", required=True, help="desk-data solana-desk directory")
    p.add_argument("--cutoff", default=CUTOFF_DEFAULT, help="UTC cutoff ending in Z; closes after it do not exist")
    p.add_argument("--data-commit", default="unknown", help="commit of the desk data that was read")
    o = p.parse_args(argv)
    pos_path = os.path.join(o.data_dir, "positions.json")
    tr_path = os.path.join(o.data_dir, "trades.csv")
    try:
        assert_registered()
        parse_utc(o.cutoff)
        if not os.path.isfile(pos_path) or not os.path.isfile(tr_path):
            raise OSError("positions.json and trades.csv are both required")
        pos_hash = sha256_file(pos_path)
        tr_hash = sha256_file(tr_path)
        commit_bytes = "unchecked"
        gpos = git_blob_sha256(o.data_commit, "solana-desk/positions.json")
        gtr = git_blob_sha256(o.data_commit, "solana-desk/trades.csv")
        if gpos is not None and gtr is not None:
            commit_bytes = "match" if (gpos == pos_hash and gtr == tr_hash) else "mismatch"
        text = render(
            load_json(pos_path), load_trades(tr_path), o.cutoff, o.data_commit,
            pos_hash, tr_hash, commit_bytes,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"day14_verdict: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
