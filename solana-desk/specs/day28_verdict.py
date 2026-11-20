"""Day-28 table and headline. Analysis only: this file does not enter, exit, or sample.

Report rules are SPD-5 DRAFT v2.1 (not a change to frozen v6.5). The owner
approved writing this script on 2026-11-20. It is run by hand at the cutoff.
It is not on a workflow.

The day-28 cutoff is the day-14 cutoff plus 14 * 24 h. If that constant is
not 2026-11-11T15:00:00Z, the constant wins and this cutoff is recomputed
from it. This module does not call day14_verdict.assert_registered, because
that assert treats a different cutoff string as drift.

Before the cutoff, main() does not open the data directory unless --synthetic
or --test is set. Those flags are for fixtures only.

Gate: F closes at or before the cutoff. Open positions do not count toward 20.
Expectancy: mean per-trade return on the 0.5 SOL ticket, all-in (closes, open
positions at the last mark at or before the cutoff, confirmed rugs at -100%).
action=refill rows are skipped. trades.csv net_pnl is not read. Book equity
is not the measure. A trimmed mean, a bootstrap interval, a segment, a twin
line, and the R-overlap check never change the table label. Branch 1 has no
GO headline.
"""
import argparse, calendar, os, random, sys, time

SPEC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SPEC_DIR)
REPO = os.path.dirname(ROOT)
ANALYSIS = os.path.join(REPO, "scripts", "analysis")
for path in (ROOT, SPEC_DIR, ANALYSIS):
    if path not in sys.path:
        sys.path.insert(0, path)

from desk import config as C
from desk.fills import exit_haircut_of, slice_pnl
from desk.report import trimmed
import day14_verdict as D14
import random_twin_check as TWIN
import r_overlap_check as OVERLAP

SPEC = "SPD-5 DRAFT v2.1"
BOOKS = ("F", "R", "S")
RUG_WHYS = ("pool_gone", "no_route_3x")
SEED = 20261125
HOLM_M = 6
ALPHA = 0.05
HOLM_FOOTNOTE = "awaiting full-family Holm (SH-06/07)"
FAMILY = ("SH-03", "SH-06", "SH-07", "SH-05m-A", "SH-05m-B", "SH-09")
NOT_JUDGED_YET = ("SH-06", "SH-07")
DRAIN_BASE = 0.146  # SH-05m-B history rate, 14.6%
SH02_MIN = 30
SH05A_MIN = 60
SH05B_MIN = 125
SH09_MIN_CLASS = 30
SH09_MIN_DRAINS = 20
UNKNOWN_MEASURE = "measurement inputs are not in positions.json, trades.csv, or tasks.csv"
LEAD_SENTENCE = (
    "Every lead came from the DexScreener paid feed (token-profiles/latest and token-boosts/latest). "
    "Results do not carry over to unpaid launches."
)
DAY14_DISCREPANCY = (
    "The day-14 script counts realized closes only and marks nothing to market. "
    "The frozen day-28 measure is all-in. Both are printed. If they disagree, all-in governs."
)
GATE_BUG = "Known R/F gate bug (SPD-105), deferred to v6.6 and unchanged inside v6.5. It does not change this label."
OPTIMISM = "Unmodeled optimism: paper results are a ceiling on live results."
JUPITER_400 = "Three unexplained Jupiter HTTP 400s (SPD-3). PR107 is a diagnostic boundary, not a strategy repair."
PASS_TEXT = (
    "v6.5 F: day-28 PASS. Paper-only continuation to 200 F closes or 12 weeks after day 1, "
    "whichever is first, then the same rule once. Never live."
)
STOP_TEXT = "v6.5 F: day-28 STOP, filters too strict (fewer than 20 F closes)"
LABEL_STOP = 'STOP "filters too strict"'
LABEL_KILL = 'KILL "edge not shown"'
LABEL_LOSE = 'KILL "beats random but loses money"'
LABEL_PASS = "PASS"


def holm_alpha():
    return ALPHA / HOLM_M


def holm_level():
    return 1.0 - ALPHA / HOLM_M


def holm_p_passes(p):
    """Most conservative Holm step on the family of 6: Bonferroni p <= 0.05/6."""
    return p is not None and p <= holm_alpha()


def assert_shared():
    """Fail closed if the gate numbers this report applies are no longer the config.

    The cutoff is not in this list. A different day-14 cutoff string is recomputed.
    """
    bad = []
    if C.MIN_F_CLOSES != 20 or D14.MIN_CLOSES != C.MIN_F_CLOSES:
        bad.append("MIN_F_CLOSES")
    if C.EARLY_STOP_EXP != -0.15 or D14.STOP_EXP != C.EARLY_STOP_EXP:
        bad.append("EARLY_STOP_EXP")
    if C.TICKET_SOL != 0.5 or D14.TICKET != C.TICKET_SOL:
        bad.append("TICKET_SOL")
    if C.TRIM_PCT != 0.05:
        bad.append("TRIM_PCT")
    if C.EXIT.get("no_route_kill_checks") != 3:
        bad.append("no_route_kill_checks")
    if bad:
        raise RuntimeError("day-28 constants drifted: " + ",".join(bad))


def derived_cutoff_text():
    """Day-14 constant plus exactly 14 * 24 h. The constant wins over the registered text."""
    ts = D14.parse_utc(D14.CUTOFF_DEFAULT) + 14 * 24 * 3600
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def derived_last_run():
    raw = D14.LAST_COUNTED_RUN
    if not isinstance(raw, str) or len(raw) != 13 or raw[8] != "T":
        return None
    try:
        ts = calendar.timegm(time.strptime(raw, "%Y%m%dT%H%M"))
    except ValueError:
        return None
    return time.strftime("%Y%m%dT%H%M", time.gmtime(ts + 14 * 24 * 3600))


def fmt(x):
    return "na" if x is None else f"{x:.9f}"


def mean(xs):
    return None if not xs else sum(xs) / float(len(xs))


def _num(text):
    if text is None or text == "":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _stale(row):
    return str((row or {}).get("stale") or "") in ("1", "1.0", "true")


def _ts(text):
    return _num(text)


def build_lots(trades, book):
    """Lots for one book. Sells fill the oldest open buy. Refill and mark rows are not lots.

    A blank pnl on a sell fails the build (it is not treated as zero). net_pnl is not read.
    Returns (closed_lots, open_lots, ok, refill_rows).
    """
    from collections import defaultdict
    lots = defaultdict(list)
    closed, still_open = [], []
    ok = True
    refills = 0
    for row in trades:
        action = row.get("action")
        if action == "refill":
            if row.get("book") == book:
                refills += 1
            continue
        if row.get("book") != book or action not in ("buy", "sell", "mark"):
            continue
        mint = row.get("mint") or ""
        ts = _ts(row.get("time"))
        if ts is None:
            ok = False
            continue
        ts = int(ts)
        if action == "mark":
            px = _num(row.get("quote_px"))
            target = lots[mint][0] if lots[mint] else None
            if target is None:
                for lot in reversed(closed):
                    if lot["mint"] == mint and lot["opened"] <= ts and (lot["closed"] is None or ts <= lot["closed"]):
                        target = lot
                        break
            if target is not None and px is not None:
                target["marks"].append((ts, px, _stale(row)))
            continue
        if action == "buy":
            lots[mint].append(dict(
                mint=mint, opened=ts, closed=None, pnl=0.0, frac=0.0,
                models=[], whys=[], sells=[], marks=[], entry=_num(row.get("quote_px")),
            ))
            continue
        if row.get("pnl") in ("", None) or not lots[mint]:
            ok = False
            continue
        lot = lots[mint][0]
        try:
            pnl = float(row["pnl"])
            frac = float(row.get("frac") or 0)
        except (TypeError, ValueError):
            ok = False
            continue
        lot["pnl"] += pnl
        lot["frac"] += frac
        lot["closed"] = ts
        why = row.get("why") or ""
        model = (row.get("cost_model") or "").strip()
        lot["models"].append(model)
        lot["whys"].append(why)
        lot["sells"].append(dict(time=ts, pnl=pnl, frac=frac, why=why, quote=_num(row.get("quote_px")), stale=_stale(row)))
        if lot["frac"] >= 1 - 1e-4:
            closed.append(lots[mint].pop(0))
    for mint in lots:
        for lot in lots[mint]:
            still_open.append(lot)
    for lot in closed:
        labels = set(lot["models"])
        lot["post_v3"] = bool(labels) and "" not in labels
        lot["rug"] = any(w in RUG_WHYS for w in lot["whys"])
    return closed, still_open, ok, refills


def reconciles_book(closed, trades, book):
    """True when every positions.json close is the same lot trades.csv still has."""
    built, _open, ok, _refills = build_lots(trades, book)
    if not ok or not D14.ids_unique(closed):
        return False
    if len(built) != len(closed):
        return False
    used = set()
    for row in closed:
        hit = None
        for i, lot in enumerate(built):
            if i in used or lot["mint"] != row.get("mint"):
                continue
            if lot["opened"] != int(row["opened"]) or lot["closed"] != int(row["closed"]):
                continue
            if abs(lot["pnl"] - float(row["pnl"])) > D14.PNL_TOL:
                continue
            hit = i
            break
        if hit is None:
            return False
        used.add(hit)
    return True


def in_committed_void(opened):
    return D14.parse_utc(D14.VOID_START) <= int(opened) < D14.parse_utc(D14.VOID_END)


def is_rug_open(pos):
    why = pos.get("why") or ""
    if why in RUG_WHYS:
        return True
    try:
        n = int(pos.get("no_route_count") or 0)
    except (TypeError, ValueError):
        n = 0
    return n >= int(C.EXIT["no_route_kill_checks"])


def last_mark(trades, book, mint, opened, cutoff_ts):
    """Last mark or sell quote at or before the cutoff, at or after the entry. None if there is none."""
    best = None
    for row in trades:
        if row.get("book") != book or (row.get("mint") or "") != mint:
            continue
        if row.get("action") not in ("mark", "sell"):
            continue
        ts = _ts(row.get("time"))
        px = _num(row.get("quote_px"))
        if ts is None or px is None:
            continue
        ts = int(ts)
        if ts < int(opened) or ts > cutoff_ts:
            continue
        stale = str(row.get("stale") or "") in ("1", "1.0", "true")
        if best is None or ts >= best[0]:
            best = (ts, px, stale)
    return best


def mark_return(entry, frac, entry_cost, exit_haircut, mark, realized):
    """After-cost return of the remaining fraction, plus pnl already booked, over the ticket."""
    pos = dict(entry=float(entry), frac=float(frac), entry_cost=float(entry_cost))
    _gross, net = slice_pnl(pos, float(frac), float(mark), float(exit_haircut))
    return (float(realized) + net) / C.TICKET_SOL


def day14_outcome(positions, trades, commit_bytes="unchecked"):
    """What the frozen day-14 script would print at its own default cutoff.

    NOT_FINAL is not a non-confirmation. STOP (final) is branch 1. CONTINUE (final) is branch 2.
    """
    cutoff_ts = D14.parse_utc(D14.CUTOFF_DEFAULT)
    closed, _opened = D14.f_records(positions)
    counted, _voided, _late = D14.select_closes(
        closed, cutoff_ts, D14.parse_utc(D14.VOID_START), D14.parse_utc(D14.VOID_END),
    )
    total = sum(float(row["pnl"]) for row in counted)
    rule, exp = D14.decide(len(counted), total, D14.TICKET, D14.MIN_CLOSES, D14.STOP_EXP)
    last = positions.get("last_run_ts")
    try:
        last_ts = int(last) if last not in (None, "") else None
    except (TypeError, ValueError):
        last_ts = None
    reasons = []
    if last_ts is None:
        reasons.append("day14_last_run_missing")
    elif last_ts < cutoff_ts:
        reasons.append("day14_last_run_before_cutoff")
    if not D14.ids_unique(closed):
        reasons.append("day14_close_ids_not_unique")
    if not D14.reconciles(closed, trades):
        reasons.append("day14_closes_do_not_reconcile")
    if commit_bytes == "mismatch":
        reasons.append("day14_commit_bytes_mismatch")
    verdict = rule if not reasons else "NOT_FINAL"
    return dict(verdict=verdict, rule=rule, expectancy=exp, n=len(counted), reasons=reasons)


def table_label(n_closes, f_exp, r_exp):
    """Frozen day-28 table. The close count is the gate. The means are all-in.

    Under 20 closes the comparison is not reached. A missing mean above that
    gate is not a label (the caller prints NOT_FINAL).
    """
    if n_closes < C.MIN_F_CLOSES:
        return LABEL_STOP
    if f_exp is None or r_exp is None:
        return None
    if f_exp <= r_exp:
        return LABEL_KILL
    if f_exp > 0:
        return LABEL_PASS
    return LABEL_LOSE


def headline_for(branch, label, not_final, unfixable, reason):
    """Registered headline. Branch 1 never returns GO. None means rerun, no headline."""
    if not not_final:
        if branch == 1:
            text = (
                "v6.5 F: edge not shown. The day-14 early STOP (Nov 3, confirmed Nov 11) stands. "
                f"Day-28 table (post-STOP, descriptive): {label}."
            )
            return "NO-GO", text
        if label == LABEL_STOP:
            return "INCONCLUSIVE", STOP_TEXT
        if label == LABEL_KILL:
            return "NO-GO", "v6.5 F: day-28 KILL (edge not shown)."
        if label == LABEL_LOSE:
            return "NO-GO", "v6.5 F: day-28 KILL (beats random but loses money)."
        if label == LABEL_PASS:
            return "PASS", PASS_TEXT
        raise RuntimeError("day-28 table has no label")
    if unfixable and branch == 1:
        text = (
            f"v6.5 F day-28 numbers can't be computed as registered ({reason}). "
            "The day-14 early STOP stands."
        )
        return "INCONCLUSIVE", text
    if unfixable and branch == 2:
        # The draft names this outcome and does not quote a sentence for it.
        return "INCONCLUSIVE", ""
    return "none", ""


def _bar(meets_95, meets_holm, futility, below_min):
    if below_min:
        return "INCONCLUSIVE", ""
    if futility:
        return "INVALIDATED", ""
    if meets_holm:
        return "VALIDATED", ""
    if meets_95:
        return "INCONCLUSIVE", HOLM_FOOTNOTE
    return "neither_bar_nor_futility", ""


def judge_sh02(n_reentries, gap, fisher_p):
    """Exploratory. gap is a fraction (0.20 = 20 percentage points). Never Holm-adjusted."""
    if n_reentries is None or gap is None or fisher_p is None:
        return "UNKNOWN", ""
    if n_reentries < SH02_MIN:
        return "INCONCLUSIVE", ""
    if gap <= 0.05:
        return "INVALIDATED", ""
    if gap >= 0.20 and fisher_p < 0.05:
        return "VALIDATED", ""
    return "neither_bar_nor_futility", ""


def judge_sh03(n_closes, f_beats_r, lb95, lb_holm, computable):
    """Register bar only. Does not change the frozen table label."""
    if not computable or f_beats_r is None:
        return "UNKNOWN", ""
    below = n_closes < C.MIN_F_CLOSES
    futility = (not below) and (not f_beats_r)
    meets_95 = (not below) and f_beats_r and lb95 is not None and lb95 > 0
    meets_holm = (not below) and f_beats_r and lb_holm is not None and lb_holm > 0
    return _bar(meets_95, meets_holm, futility, below)


def judge_sh05m_a(n_ge_1h, rr, lb95, lb_holm, ub95):
    if None in (n_ge_1h, rr, lb95, lb_holm, ub95):
        return "UNKNOWN", ""
    below = n_ge_1h < SH05A_MIN
    return _bar(rr >= 2 and lb95 > 1, rr >= 2 and lb_holm > 1, ub95 < 1.5, below)


def judge_sh05m_b(n_lt_1h, wilson95, wilson_holm):
    """No invalidation rule is written on test B. A low bound is not turned into INVALIDATED."""
    if None in (n_lt_1h, wilson95, wilson_holm):
        return "UNKNOWN", ""
    below = n_lt_1h < SH05B_MIN
    return _bar(wilson95 > DRAIN_BASE, wilson_holm > DRAIN_BASE, False, below)


def judge_sh08():
    """Diagnostic. The return value never changes a verdict."""
    return "DIAGNOSTIC", "never changes a verdict"


def judge_sh09(min_class, n_new_drains, rr, lb95, lb_holm, ub95):
    if None in (min_class, n_new_drains, rr, lb95, lb_holm, ub95):
        return "UNKNOWN", ""
    below = min_class < SH09_MIN_CLASS or n_new_drains < SH09_MIN_DRAINS
    return _bar(rr >= 3 and lb95 > 1, rr >= 3 and lb_holm > 1, ub95 < 1.5, below)


def bootstrap_bounds(f_returns, r_returns, n_boot, seed):
    """Percentile bootstrap, same resampler as random_twin_check.bootstrap_ci.

    F's interval is the two-sided 95% interval of F's mean. The F-minus-R bounds
    resample the two books independently. The design is not named in the draft.
    """
    if not f_returns:
        return None, None, None, None
    f_lo, f_hi = TWIN.bootstrap_ci(list(f_returns), n_boot, random.Random(seed))
    if not r_returns:
        return f_lo, f_hi, None, None
    rng = random.Random(seed + 1)
    nf, nr = len(f_returns), len(r_returns)
    diffs = []
    for _ in range(n_boot):
        mf = sum(f_returns[rng.randrange(nf)] for _k in range(nf)) / nf
        mr = sum(r_returns[rng.randrange(nr)] for _k in range(nr)) / nr
        diffs.append(mf - mr)
    diffs.sort()
    return f_lo, f_hi, TWIN.percentile(diffs, ALPHA), TWIN.percentile(diffs, holm_alpha())


def _closed_list(branch, label, computable):
    """Section 6 mapping. Printed beside the Holm line. Does not change the table."""
    if not computable or label is None:
        return "UNKNOWN"
    if label == LABEL_STOP or (branch == 1 and label == LABEL_PASS) or (branch == 2 and label == LABEL_PASS):
        return "INCONCLUSIVE"
    if branch == 1 or (isinstance(label, str) and label.startswith("KILL")):
        return "INVALIDATED"
    return "UNKNOWN"


def _sources(tasks, cutoff_ts):
    counts = {"profile": 0, "boost": 0, "manual": 0, "other": 0}
    bad = 0
    invalid = 0
    if not tasks:
        return counts, bad, invalid, False
    for row in tasks:
        ts = _ts(row.get("time"))
        if ts is None:
            bad += 1
            continue
        if int(ts) > cutoff_ts:
            continue
        if (row.get("reason") or "") == "invalid_mint":
            invalid += 1
        src = (row.get("source") or "").strip().lower()
        if src in counts:
            counts[src] += 1
        elif src:
            counts["other"] += 1
    return counts, bad, invalid, True


def entry_cost_of_lot(pos, trades, book):
    """Logged entry cost. The position field wins; otherwise the buy row's haircut. Never a guessed model."""
    raw = pos.get("entry_cost")
    if raw not in (None, ""):
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    opened = pos.get("opened")
    mint = pos.get("mint") or ""
    for row in trades:
        if row.get("book") != book or row.get("action") != "buy" or (row.get("mint") or "") != mint:
            continue
        if opened is not None and _ts(row.get("time")) is not None and int(float(row["time"])) != int(opened):
            continue
        haircut = _num(row.get("entry_haircut_pct"))
        if haircut is None:
            return None
        return C.TICKET_SOL * (1.0 + haircut / 100.0) + C.COST["network_fee_sol"]
    return None


def _quote_from_lot(lot, cutoff_ts, entry):
    """Last quote on this lot at or before the cutoff. The entry is that quote when nothing later was logged."""
    best = None
    points = list(lot.get("marks") or [])
    for sell in lot.get("sells") or []:
        if sell.get("quote") is not None and sell["time"] <= cutoff_ts:
            points.append((sell["time"], sell["quote"], sell["stale"]))
    for ts, px, stale in points:
        if ts <= cutoff_ts and (best is None or ts >= best[0]):
            best = (ts, px, stale)
    if best is None:
        return entry, 0
    return best[1], 1 if best[2] else 0


def _value_open(pos, book, trades, cutoff_ts, lot=None):
    """Return (return_or_None, stale, unknown_reason). A confirmed rug is exactly -1."""
    if is_rug_open(pos):
        return -1.0, 0, None
    entry = _num(pos.get("entry"))
    frac = _num(pos.get("frac"))
    cost = entry_cost_of_lot(pos, trades, book)
    if entry is None or entry <= 0 or frac is None or cost is None:
        return None, 0, "open_mark_unknown"
    if lot is not None:
        px, stale = _quote_from_lot(lot, cutoff_ts, entry)
    else:
        mark = last_mark(trades, book, pos.get("mint") or "", pos.get("opened"), cutoff_ts)
        if mark is None:
            px = entry
            stale = 1 if str(pos.get("stale") or "") in ("1", "1.0", "true") else 0
        else:
            px = mark[1]
            stale = 1 if mark[2] else 0
    realized = _num(pos.get("pnl"))
    if realized is None:
        realized = 0.0
    try:
        ret = mark_return(entry, frac, cost, exit_haircut_of(pos), px, realized)
    except (TypeError, ValueError, ZeroDivisionError):
        return None, 0, "open_mark_unknown"
    return ret, stale, None


def _book_sample(name, positions, trades, cutoff_ts):
    book = ((positions.get("books") or {}).get(name) or {})
    closed = list(book.get("closed") or [])
    opens = book.get("open") or {}
    voided = late = rugs = stale = post_v3 = 0
    close_returns = []
    realized_returns = []
    all_in = []
    unknown = None
    built, still_open, built_ok, _refills = build_lots(trades, name)
    if not built_ok:
        unknown = "trade_log_unreadable"
    by_close = {}
    for lot in built:
        by_close[(lot["mint"], lot["opened"], lot["closed"])] = lot
    by_open = {(lot["mint"], lot["opened"]): lot for lot in still_open}
    for row in closed:
        opened = int(row["opened"])
        if in_committed_void(opened):
            voided += 1
            continue
        done = int(row["closed"])
        try:
            logged = float(row["pnl"]) / C.TICKET_SOL
        except (TypeError, ValueError, KeyError):
            unknown = "close_pnl_unknown"
            continue
        if done > cutoff_ts:
            # That close does not exist for this run, so its price is not used.
            late += 1
            continue
        rug = (row.get("why") or "") in RUG_WHYS
        ret = -1.0 if rug else logged
        if rug:
            rugs += 1
        close_returns.append(ret)
        realized_returns.append(logged)
        all_in.append(ret)
        matched = by_close.get((row.get("mint") or "", opened, done))
        if matched is not None and matched.get("post_v3"):
            post_v3 += 1
    for pid, pos in opens.items():
        opened = pos.get("opened")
        if opened is None:
            unknown = "open_mark_unknown"
            continue
        if int(opened) > cutoff_ts:
            continue
        if in_committed_void(int(opened)):
            voided += 1
            continue
        pos = dict(pos)
        pos.setdefault("mint", pid)
        lot = by_open.get((pos.get("mint") or "", int(opened)))
        ret, st, why = _value_open(pos, name, trades, cutoff_ts, lot)
        stale += st
        if why:
            unknown = why
            continue
        if ret == -1.0 and is_rug_open(pos):
            rugs += 1
        all_in.append(ret)
    return dict(
        closes=len(close_returns), voided=voided, late=late, rugs=rugs, stale=stale,
        post_v3=post_v3, close_returns=close_returns, realized_returns=realized_returns,
        all_in=all_in, unknown=unknown, n_closed_rows=len(closed),
    )


def _overlap_line(tasks, positions):
    try:
        got = OVERLAP.analyze(tasks or [], positions)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return "overlap_status: not_computed", "overlap_changes_verdict: no"
    return (
        f"overlap_flag: {got.get('flag')}",
        "overlap_changes_verdict: no",
    )


def _twin_lines(samples, data_dir):
    """Post-v3 twin line. Below 20 lots it is not evaluable. It never changes the label."""
    need = any(samples[name]["post_v3"] >= 20 for name in BOOKS)
    got = None
    v2 = os.path.join(data_dir, "v2") if data_dir else ""
    if need and data_dir and os.path.isdir(v2):
        try:
            got = TWIN.analyze(data_dir, n_draws=TWIN.DRAWS, n_boot=TWIN.BOOT, seed=TWIN.SEED)
        except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
            got = None
    lines = []
    for name in BOOKS:
        n = samples[name]["post_v3"]
        if n < 20:
            lines.append(f"post_v3_twin_{name}: not evaluable (n = {n})")
            continue
        tw = None if got is None else ((got.get("books") or {}).get(name) or {}).get("twin")
        if not tw:
            lines.append(f"post_v3_twin_{name}: not evaluable (n = {n}; no universe log)")
        else:
            lines.append(f"post_v3_twin_{name}: n = {tw.get('n')} edge = {tw.get('edge')}")
    return lines


def render(positions, trades, cutoff_text, data_commit, positions_sha256, trades_sha256,
           tasks=None, commit_bytes="unchecked", unfixable=False, n_boot=None,
           tasks_sha256="unchecked", data_dir=None):
    assert_shared()
    n_boot = TWIN.BOOT if n_boot is None else int(n_boot)
    if n_boot < 1:
        raise ValueError("bootstrap must be positive")
    registered = derived_cutoff_text()
    cutoff_ts = D14.parse_utc(cutoff_text)
    binding = cutoff_text == registered
    early = day14_outcome(positions, trades, commit_bytes)
    if early["verdict"] == "STOP":
        branch = 1
    elif early["verdict"] == "CONTINUE":
        branch = 2
    else:
        branch = None
    samples = {name: _book_sample(name, positions, trades, cutoff_ts) for name in BOOKS}
    reasons = []
    if not binding:
        reasons.append("cutoff_not_default")
    last = positions.get("last_run_ts")
    try:
        last_ts = int(last) if last not in (None, "") else None
    except (TypeError, ValueError):
        last_ts = None
    if last_ts is None:
        reasons.append("last_run_missing")
    elif last_ts < cutoff_ts:
        reasons.append("last_run_before_cutoff")
    for name in BOOKS:
        book = ((positions.get("books") or {}).get(name) or {})
        closed = list(book.get("closed") or [])
        if not D14.ids_unique(closed):
            reasons.append(f"{name}_close_ids_not_unique")
        if not reconciles_book(closed, trades, name):
            reasons.append(f"{name}_closes_do_not_reconcile")
        if samples[name]["unknown"]:
            reasons.append(f"{name}_{samples[name]['unknown']}")
    if commit_bytes == "mismatch":
        reasons.append("commit_bytes_mismatch")
    if early["verdict"] == "NOT_FINAL":
        reasons.append("day14_not_final")
    # Unique, stable order.
    reasons = list(dict.fromkeys(reasons))
    final = not reasons
    f_exp = mean(samples["F"]["all_in"])
    r_exp = mean(samples["R"]["all_in"])
    s_exp = mean(samples["S"]["all_in"])
    f_real = mean(samples["F"]["realized_returns"])
    r_real = mean(samples["R"]["realized_returns"])
    n_f = samples["F"]["closes"]
    arithmetic = None
    compare_blocked = n_f >= C.MIN_F_CLOSES and (f_exp is None or r_exp is None)
    if compare_blocked and final:
        reasons.append("r_or_f_expectancy_undefined")
        final = False
    if not compare_blocked and all(samples[name]["unknown"] is None for name in BOOKS):
        arithmetic = table_label(n_f, f_exp, r_exp)
    realized_label = None
    if n_f < C.MIN_F_CLOSES:
        realized_label = LABEL_STOP
    elif f_real is not None and r_real is not None:
        realized_label = table_label(n_f, f_real, r_real)
    label = arithmetic if final else None
    shown = f"post-STOP, descriptive: {label}" if branch == 1 and label else label
    reason_text = "none" if not reasons else ",".join(reasons)
    flag_applies = bool(unfixable) and not final and branch in (1, 2)
    head, head_text = headline_for(branch, label, not final, flag_applies, reason_text)
    if head == "GO" or (branch == 1 and head not in ("NO-GO", "INCONCLUSIVE", "none")):
        raise RuntimeError("branch 1 cannot headline GO")
    if head == "none" and not final:
        verdict = "NOT_FINAL"
    else:
        verdict = head
    f_lo, f_hi, lb95, lb_holm = bootstrap_bounds(
        samples["F"]["all_in"], samples["R"]["all_in"], n_boot, SEED,
    )
    beats = None if f_exp is None or r_exp is None else f_exp > r_exp
    sh03, sh03_note = judge_sh03(n_f, beats, lb95, lb_holm, final and label is not None)
    closed_list = _closed_list(branch, label, final and label is not None)
    trim_f = trimmed(samples["F"]["all_in"])
    trim_r = trimmed(samples["R"]["all_in"])
    trim_warn = bool(samples["F"]["all_in"] and samples["R"]["all_in"] and mean(trim_f) <= mean(trim_r))
    differ = (
        final and realized_label is not None and label is not None and realized_label != label
    )
    small = [name for name in ("R", "S") if samples[name]["closes"] < C.MIN_F_CLOSES]
    refills = sum(1 for row in trades if row.get("action") == "refill")
    voids = positions.get("voids") or []
    src, src_bad, invalid_mints, had_tasks = _sources(tasks, cutoff_ts)
    last_run = derived_last_run()
    overlap_a, overlap_b = _overlap_line(tasks, positions)
    twin_lines = _twin_lines(samples, data_dir)
    lines = [
        f"spec: {SPEC}",
        "analysis: day28_verdict",
        "rules_changed: no",
        "live_step: no",
        "book_equity_used: no",
        "net_pnl_used: no",
        "gate_counts_open_positions: no",
        f"cutoff: {cutoff_text}",
        f"cutoff_registered: {registered}",
        f"cutoff_recomputed: {'no' if registered == '2026-11-25T15:00:00Z' else 'yes'}",
        f"cutoff_binding: {'yes' if binding else 'no'}",
        f"last_counted_run: {last_run if binding and last_run else 'na'}",
        "cost_version: as_logged",
        "pre_v3_counted: yes",
        f"ticket_sol: {fmt(C.TICKET_SOL)}",
        f"min_f_closes: {C.MIN_F_CLOSES}",
        f"refill_rows_skipped: {refills}",
        f"void_window_start: {D14.VOID_START}",
        f"void_window_end: {D14.VOID_END}",
        f"later_void_records: {len(voids) if isinstance(voids, list) else 0}",
        "later_voids_applied: no",
        f"day14_cutoff: {D14.CUTOFF_DEFAULT}",
        f"day14_verdict: {early['verdict']}",
        f"day14_rule_result: {early['rule']}",
        f"day14_n_closes: {early['n']}",
        f"branch: {branch if branch else 'na'}",
    ]
    for name in BOOKS:
        s = samples[name]
        lines.extend([
            f"{name}_closes: {s['closes']}",
            f"{name}_open_at_cutoff: {len(s['all_in']) - s['closes']}",
            f"{name}_voided: {s['voided']}",
            f"{name}_after_cutoff: {s['late']}",
            f"{name}_rugs: {s['rugs']}",
            f"{name}_stale_marks: {s['stale']}",
            f"{name}_all_in_n: {len(s['all_in'])}",
            f"{name}_all_in_expectancy: {fmt(mean(s['all_in']))}",
            f"{name}_realized_expectancy: {fmt(mean(s['realized_returns']))}",
            f"{name}_post_v3_lots: {s['post_v3']}",
        ])
    lines.extend([
        f"small_control_warning: {'none' if not small else ','.join(small)}",
        f"compare_F_vs_R: {fmt(None if f_exp is None or r_exp is None else f_exp - r_exp)}",
        f"compare_S_vs_R: {fmt(None if s_exp is None or r_exp is None else s_exp - r_exp)}",
        f"compare_F_vs_S: {fmt(None if f_exp is None or s_exp is None else f_exp - s_exp)}",
        f"trimmed_F: {fmt(mean(trim_f) if samples['F']['all_in'] else None)}",
        f"trimmed_R: {fmt(mean(trim_r) if samples['R']['all_in'] else None)}",
        f"trimmed_warning: {'yes' if trim_warn else 'no'}",
        "trimmed_changes_label: no",
        f"bootstrap_95_F: {'na' if f_lo is None else fmt(f_lo) + ' ' + fmt(f_hi)}",
        "bootstrap_changes_label: no",
        f"bootstrap_seed: {SEED}",
        f"bootstrap_draws: {n_boot}",
    ])
    lines.extend(twin_lines)
    lines.extend([
        "twin_changes_label: no",
        "headline_cites_twin: no",
        "headline_cites_segment: no",
        f"segment_pr106_run: 42430039958",
        "segment_pr106_boundary: uncertain",
        "segments_change_label: no",
        overlap_a,
        overlap_b,
        f"realized_only_label: {realized_label if realized_label else 'na'}",
        f"label_warning: {'all-in governs; realized-only label differs' if differ else 'none'}",
        f"table_label: {shown if shown else 'na'}",
        f"rule_result: {arithmetic if arithmetic else 'na'}",
        f"finality: {'yes' if final else 'no'}",
        f"finality_reasons: {reason_text}",
        f"unfixable_flag: {'applied' if flag_applies else 'ignored' if unfixable else 'no'}",
        f"headline: {head}",
        f"headline_text: {head_text if head_text else 'na'}",
        "go_available_in_branch_1: no",
        f"closed_list: {closed_list}",
        f"holm_family: {','.join(FAMILY)}",
        f"holm_m: {HOLM_M}",
        f"holm_alpha: {holm_alpha():.10f}",
        f"holm_level: {holm_level():.10f}",
        f"SH-03: {sh03}",
        f"SH-03_note: {sh03_note if sh03_note else 'none'}",
        "SH-03_changes_table: no",
        "SH-02: UNKNOWN",
        f"SH-02_reason: {UNKNOWN_MEASURE}",
        "SH-02_holm: no",
        "SH-05m-A: UNKNOWN",
        f"SH-05m-A_reason: {UNKNOWN_MEASURE}",
        "SH-05m-B: UNKNOWN",
        f"SH-05m-B_reason: {UNKNOWN_MEASURE}",
        "SH-08: DIAGNOSTIC",
        "SH-08_note: never changes a verdict",
        "SH-08_holm: no",
        "SH-09: UNKNOWN",
        f"SH-09_reason: {UNKNOWN_MEASURE}",
        *[f"{name}: not judged Nov 25" for name in NOT_JUDGED_YET],
        f"lead_source: {LEAD_SENTENCE}",
        f"lead_source_rows: {'missing' if not had_tasks else 'present'}",
        f"lead_source_profile: {src['profile']}",
        f"lead_source_boost: {src['boost']}",
        f"lead_source_manual: {src['manual']}",
        f"lead_source_other: {src['other']}",
        f"lead_rows_unreadable: {src_bad}",
        f"malformed_mint_skips: {invalid_mints}",
        "malformed_mint_counterfactual: unknown",
        f"day14_discrepancy: {DAY14_DISCREPANCY}",
        f"gate_bug: {GATE_BUG}",
        f"paper_ceiling: {OPTIMISM}",
        f"jupiter_http_400: {JUPITER_400}",
        "gaps: not_inferred",
        f"positions_sha256: {positions_sha256}",
        f"trades_sha256: {trades_sha256}",
        f"tasks_sha256: {tasks_sha256}",
        f"commit_bytes: {commit_bytes}",
        f"data_commit: {data_commit}",
        f"VERDICT: {verdict}",
    ])
    return "\n".join(lines) + "\n"


def refused_before_cutoff(now_ts, synthetic):
    """True when a real-data run must stop before any file is opened."""
    if synthetic:
        return False
    return float(now_ts) < D14.parse_utc(derived_cutoff_text())


def main(argv=None, *, now=None):
    p = argparse.ArgumentParser(description="Day-28 table and headline. Analysis only. Refuses real data before the cutoff.")
    p.add_argument("--data-dir", required=True, help="desk-data solana-desk directory")
    p.add_argument("--cutoff", default=None, help="UTC cutoff ending in Z; default is the derived day-28 instant")
    p.add_argument("--data-commit", default="unknown", help="commit of the desk data that was read")
    p.add_argument("--synthetic", action="store_true", help="fixture only; required to run before the cutoff")
    p.add_argument("--test", action="store_true", help="same as --synthetic; fixture only")
    p.add_argument("--not-final-unfixable", action="store_true",
                   help="the owner has decided a NOT_FINAL result cannot be repaired")
    p.add_argument("--bootstrap", type=int, default=TWIN.BOOT)
    o = p.parse_args(argv)
    synthetic = bool(o.synthetic or o.test)
    try:
        assert_shared()
        registered = derived_cutoff_text()
        cutoff = o.cutoff or registered
        D14.parse_utc(cutoff)
        now_ts = time.time() if now is None else now
        if refused_before_cutoff(now_ts, synthetic):
            print(
                "day28_verdict: refused before the cutoff. Real desk data is not read. "
                "Pass --synthetic or --test only for a fixture.",
                file=sys.stderr,
            )
            return 2
        if o.bootstrap < 1:
            raise ValueError("bootstrap must be positive")
        pos_path = os.path.join(o.data_dir, "positions.json")
        tr_path = os.path.join(o.data_dir, "trades.csv")
        task_path = os.path.join(o.data_dir, "tasks.csv")
        if not os.path.isfile(pos_path) or not os.path.isfile(tr_path):
            raise OSError("positions.json and trades.csv are both required")
        pos_hash = D14.sha256_file(pos_path)
        tr_hash = D14.sha256_file(tr_path)
        tasks = None
        task_hash = "missing"
        if os.path.isfile(task_path):
            tasks = D14.load_trades(task_path)
            task_hash = D14.sha256_file(task_path)
        commit_bytes = "unchecked"
        gpos = D14.git_blob_sha256(o.data_commit, "solana-desk/positions.json")
        gtr = D14.git_blob_sha256(o.data_commit, "solana-desk/trades.csv")
        if gpos is not None and gtr is not None:
            commit_bytes = "match" if (gpos == pos_hash and gtr == tr_hash) else "mismatch"
        text = render(
            D14.load_json(pos_path), D14.load_trades(tr_path), cutoff, o.data_commit,
            pos_hash, tr_hash, tasks, commit_bytes, o.not_final_unfixable, o.bootstrap,
            task_hash, o.data_dir,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"day28_verdict: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
