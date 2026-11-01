"""Frozen day-14 early stop. Analysis only: this file does not enter, exit, or sample.

Rule, as registered in config v1-v6.5 (plan v6.2): STOP if book F has at least
MIN_F_CLOSES realized closes and F expectancy after costs is worse than
EARLY_STOP_EXP per trade. Otherwise CONTINUE.

Expectancy = total realized SOL pnl / (TICKET_SOL * number of F closes).
Raw mean. No winsorizing. No confidence interval in the decision.

Realized closes only. An open position does not count, including a partial
take already booked on it, and nothing is marked to market. A close at or
before the cutoff counts. A close after the cutoff does not exist for this run.

Costs: COST_VERSION is as_logged. The figure is the net SOL pnl stored on the
closed position, the sum of the sell-slice nets written when that position
closed. The haircut changed to v3-exit-aware at 2026-10-29T00:30:25Z. Earlier
closes kept the haircut they were charged and were not rewritten. This verdict
does not restate them. The owner confirms this constant in
review. A v3 recompute is printed only when every counted close has the logged
quote and pool reserve the formula needs, and it never changes the verdict.

Voids: a close counts only when its entry timestamp is outside the committed
window. There is no single stored RugCheck outage window. tasks.csv shows
rugcheck missing_data on slots 20261031T0130 through 20261031T1530, and
fail_reason is blank that day. positions.json voids are per-run
data_failures_over_10pct from 2026-10-31T02:00:29Z through
2026-10-31T23:30:26Z, which keeps going after RugCheck returned because the
UTC-day rate stayed above 10%. Those records are not one (start, end), so the
window below is the committed constant. No manual reclassification.
"""
import argparse, calendar, csv, json, os, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from desk import config as C
from desk import fills

# Owner confirms in review. Do not restate pre-v3 rows onto v3-exit-aware.
COST_VERSION = "as_logged"

# 2026-11-11 10:00 America/Toronto. DST ended 2026-11-01, so that clock is EST (UTC-5).
CUTOFF_DEFAULT = "2026-11-11T15:00:00Z"

# Inclusive start, exclusive end. See the module docstring.
VOID_START = "2026-10-31T01:30:00Z"
VOID_END = "2026-11-01T00:00:00Z"
VOID_SOURCE = "committed_constant"
VOID_NOTE = (
    "No single RugCheck outage window is stored. "
    "tasks.csv has rugcheck missing_data on slots 20261031T0130 through 20261031T1530 "
    "(fail_reason blank that day). "
    "positions.json voids are data_failures_over_10pct from 2026-10-31T02:00:29Z "
    "through 2026-10-31T23:30:26Z, including hours after RugCheck returned, "
    "because the UTC-day rate stayed above 10%. "
    "Those are per-run records, so this run uses the committed window."
)


def parse_utc(text):
    s = text[:-1] if text.endswith("Z") else text
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))


def fmt_sol(x):
    return f"{x:.9f}"


def load_positions(data_dir):
    path = os.path.join(data_dir, "positions.json")
    with open(path) as fh:
        return json.load(fh)


def load_trades(data_dir):
    path = os.path.join(data_dir, "trades.csv")
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def f_records(positions):
    book = (positions.get("books") or {}).get("F") or {}
    closed = list(book.get("closed") or [])
    opened = list((book.get("open") or {}).values())
    return closed, opened


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


def _sells_for(close, trades):
    out = []
    for row in trades:
        if row.get("book") != "F" or row.get("mint") != close.get("mint"):
            continue
        if row.get("action") != "sell":
            continue
        try:
            ts = int(float(row.get("time") or ""))
        except (TypeError, ValueError):
            continue
        if int(close["opened"]) < ts <= int(close["closed"]):
            out.append(row)
    return out


def _recompute_one(close, sell):
    """v3 net SOL for one full exit, from the logged entry reserve and exit quote. None if an input is missing."""
    pool_in = close.get("entry_sol_reserve")
    px = close.get("exit_px")
    if pool_in in (None, "") or px in (None, ""):
        return None
    try:
        pool_in = float(pool_in)
        px = float(px)
        frac = float(sell.get("frac") or "")
    except (TypeError, ValueError):
        return None
    if abs(frac - 1.0) > 1e-4 or pool_in <= 0:
        return None
    if px > 0 and close.get("exit_sol_reserve") in (None, ""):
        return None
    pool_out = None if px <= 0 else float(close.get("exit_sol_reserve"))
    h_entry = fills.haircut_frac(pool_in, fills.verified_impact_bps(pool_in, C.TICKET_SOL))
    entry_cost = C.TICKET_SOL * (1 + h_entry) + C.COST["network_fee_sol"]
    pos = {"entry": float(close["entry"]), "entry_cost": entry_cost}
    if px <= 0:
        _gross, net = fills.slice_pnl(pos, 1.0, 0.0, 0.0)
        return net
    dx = fills.gross_proceeds(pos, 1.0, px)
    verified = fills.verified_impact_bps(pool_out, dx)
    h_exit = fills.haircut_frac(pool_out, 0.0 if verified is None else verified, close.get("why"))
    _gross, net = fills.slice_pnl(pos, 1.0, px, h_exit)
    return net


def v3_recompute(closes, trades):
    """Book-level v3 figure only when every counted close can be rebuilt from its logged quote."""
    rebuilt, matched, missing = [], 0, 0
    for close in closes:
        sells = _sells_for(close, trades)
        one = _recompute_one(close, sells[0]) if len(sells) == 1 else None
        if one is None:
            missing += 1
            continue
        rebuilt.append(one)
        if abs(one - float(close["pnl"])) <= 1e-8:
            matched += 1
    if not closes:
        return "not_possible", None, "no counted closes"
    if missing:
        note = (
            f"{missing} of {len(closes)} F closes lack a single logged exit quote with an entry pool reserve, "
            f"so the v3 formula cannot be applied to the whole book. "
            f"{len(rebuilt)} closes have those fields and {matched} of them recompute to the logged net. "
            "Informational only. The verdict uses as_logged."
        )
        return "not_possible", None, note
    total = sum(rebuilt)
    return "informational", total, "every counted close was rebuilt from its logged quote and entry reserve. Not used for the verdict."


def render(positions, trades, cutoff_text, data_commit, void_start_text=VOID_START, void_end_text=VOID_END):
    closed, opened = f_records(positions)
    cutoff_ts = parse_utc(cutoff_text)
    counted, voided, late = select_closes(closed, cutoff_ts, parse_utc(void_start_text), parse_utc(void_end_text))
    total = sum(float(row["pnl"]) for row in counted)
    verdict, exp = decide(len(counted), total, C.TICKET_SOL, C.MIN_F_CLOSES, C.EARLY_STOP_EXP)
    pre = sum(1 for row in counted if not str(row.get("cost_model") or "").strip())
    pre_all = 0
    for name in "FRS":
        for row in ((positions.get("books") or {}).get(name) or {}).get("closed") or []:
            if not str(row.get("cost_model") or "").strip():
                pre_all += 1
    partial = sum(float(row.get("pnl") or 0) for row in opened)
    status, rebuilt, note = v3_recompute(counted, trades)
    lines = [
        f"cutoff: {cutoff_text}",
        f"cost_version: {COST_VERSION}",
        f"void_window_start: {void_start_text}",
        f"void_window_end: {void_end_text}",
        f"void_window_source: {VOID_SOURCE}",
        f"void_window_note: {VOID_NOTE}",
        f"n_closes: {len(counted)}",
        f"excluded_void_entry: {len(voided)}",
        f"excluded_after_cutoff: {len(late)}",
        f"open_ignored: {len(opened)}",
        f"open_partial_pnl_excluded: {fmt_sol(partial)}",
        f"total_pnl_sol: {fmt_sol(total)}",
        f"expectancy: {'na' if exp is None else fmt_sol(exp)}",
        f"data_commit: {data_commit}",
        f"pre_v3_closes: {pre}",
        f"pre_v3_closed_positions_all_books: {pre_all}",
        f"v3_recompute: {status}",
    ]
    if rebuilt is not None and len(counted):
        _v, rebuilt_exp = decide(len(counted), rebuilt, C.TICKET_SOL, C.MIN_F_CLOSES, C.EARLY_STOP_EXP)
        lines.append(f"v3_recompute_pnl_sol: {fmt_sol(rebuilt)}")
        lines.append(f"v3_recompute_expectancy: {fmt_sol(rebuilt_exp)}")
    lines.append(f"v3_recompute_note: {note}")
    lines.append(f"VERDICT: {verdict}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Frozen day-14 verdict from desk-data positions.json")
    p.add_argument("--data-dir", required=True, help="desk-data solana-desk directory")
    p.add_argument("--cutoff", default=CUTOFF_DEFAULT, help="UTC cutoff, closes after this do not exist")
    p.add_argument("--data-commit", default="unknown", help="commit of the desk data that was read")
    o = p.parse_args(argv)
    try:
        text = render(load_positions(o.data_dir), load_trades(o.data_dir), o.cutoff, o.data_commit)
    except OSError as exc:
        print(f"day14_verdict: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
