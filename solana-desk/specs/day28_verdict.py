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
import argparse, calendar, json, os, random, re, sys, time

SPEC_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SPEC_DIR)
REPO = os.path.dirname(ROOT)
ANALYSIS = os.path.join(REPO, "scripts", "analysis")
for path in (ROOT, SPEC_DIR, ANALYSIS):
    if path not in sys.path:
        sys.path.insert(0, path)

from desk import config as C
from desk.fills import CLOSED_KEEP, exit_haircut_of, slice_pnl
from desk.report import trimmed
import day14_verdict as D14
import random_twin_check as TWIN
import r_overlap_check as OVERLAP

SPEC = "SPD-5 DRAFT v2.1"
BOOKS = ("F", "R", "S")
REGISTERED_FLOOR = "2026-11-25T15:00:00Z"
SENTINEL_NAME = "DAY28_SYNTHETIC_FIXTURE"
SNAPSHOT_NAME = "open-snapshot.json"
_HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
NEITHER_NOTE = "neither the 95% bar nor the futility rule; filed as INCONCLUSIVE"
PASS_REGISTER_NOTE = "section 6 keeps a branch-2 PASS at INCONCLUSIVE; VALIDATED would need an owner addendum"
UNMODELED_COSTS = "The paper desk has no Jito, priority-fee, rent, or failed-tx cost model."


class NonFinite(ValueError):
    """A NaN or infinity in a numeric field. It is not a zero and not a mark."""
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
    if CLOSED_KEEP != 200:
        bad.append("CLOSED_KEEP")
    if bad:
        raise RuntimeError("day-28 constants drifted: " + ",".join(bad))


def derived_cutoff_text():
    """Day-14 constant plus exactly 14 * 24 h. The constant wins over the registered text."""
    ts = D14.parse_utc(D14.CUTOFF_DEFAULT) + 14 * 24 * 3600
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def refusal_instant():
    """Real data stays closed until this instant.

    The floor is the later of the derived day-28 cutoff and 2026-11-25T15:00:00Z.
    Editing the day-14 constant cannot open the gate earlier than that date.
    """
    return max(D14.parse_utc(derived_cutoff_text()), D14.parse_utc(REGISTERED_FLOOR))


def is_git_sha(text):
    return bool(_HEX40.fullmatch(text or ""))


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


def _finite(x):
    return x == x and x not in (float("inf"), float("-inf"))


def _num(text):
    """A blank field is None. NaN and inf raise. A non-numeric token is None (malformed)."""
    if isinstance(text, bool) or text is None or text == "":
        return None
    try:
        x = float(text)
    except (TypeError, ValueError):
        return None
    if not _finite(x):
        raise NonFinite()
    return x


def _stale(row):
    return str((row or {}).get("stale") or "") in ("1", "1.0", "true")


def _ts(text):
    return _num(text)


def build_lots(trades, book):
    """Lots for one book. Sells fill the oldest open buy. Refill and mark rows are not lots.

    A blank pnl on a sell fails the build (it is not treated as zero). net_pnl is not read.
    NaN and inf fail the build as non_finite. Returns
    (closed_lots, open_lots, ok, refill_rows, reason).
    """
    from collections import defaultdict
    lots = defaultdict(list)
    closed, still_open = [], []
    ok = True
    reason = None
    refills = 0
    try:
        return _build_lots(trades, book, lots, closed, still_open, refills)
    except NonFinite:
        return closed, still_open, False, refills, "non_finite"


def _build_lots(trades, book, lots, closed, still_open, refills):
    ok = True
    reason = None
    for index, row in enumerate(trades):
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
            reason = reason or "malformed_row"
            continue
        lot = lots[mint][0]
        pnl = _num(row.get("pnl"))
        frac = _num(row.get("frac"))
        if pnl is None or frac is None:
            ok = False
            reason = reason or "malformed_row"
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
            lot["sell_index"] = index
            closed.append(lots[mint].pop(0))
    for mint in lots:
        for lot in lots[mint]:
            still_open.append(lot)
    for lot in closed:
        labels = set(lot["models"])
        lot["post_v3"] = bool(labels) and "" not in labels
        lot["rug"] = any(w in RUG_WHYS for w in lot["whys"])
    if not ok and reason is None:
        reason = "malformed_row"
    return closed, still_open, ok, refills, reason


def reconciles_book(closed, trades, book):
    """positions.json keeps the last CLOSED_KEEP closes. The log keeps every lot.

    The tail is the last CLOSED_KEEP lots by the trades.csv row of the final
    sell, not by close time. Same-run closes share a timestamp. Older lots
    stay in the sample. A non-finite pnl does not reconcile.
    """
    built, _open, ok, _refills, _reason = build_lots(trades, book)
    if not ok or not D14.ids_unique(closed):
        return False
    ordered = sorted(built, key=lambda lot: lot["sell_index"])
    tail = ordered[-CLOSED_KEEP:] if len(ordered) > CLOSED_KEEP else ordered
    if len(closed) != len(tail):
        return False
    used = set()
    for row in closed:
        hit = None
        try:
            opened = int(row["opened"])
            done = int(row["closed"])
            pnl = float(row["pnl"])
        except (TypeError, ValueError, KeyError):
            return False
        if not _finite(pnl):
            return False
        for i, lot in enumerate(tail):
            if i in used or lot["mint"] != row.get("mint"):
                continue
            if lot["opened"] != opened or lot["closed"] != done:
                continue
            if abs(lot["pnl"] - pnl) > D14.PNL_TOL:
                continue
            hit = i
            break
        if hit is None:
            return False
        used.add(hit)
    return len(used) == len(tail)


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


def _trades_for_closed(trades, closed, book):
    """Buy and sell rows for the positions.json tail, so the day-14 check can see that tail."""
    spans = []
    for row in closed:
        try:
            spans.append((row.get("mint") or "", int(row["opened"]), int(row["closed"])))
        except (TypeError, ValueError, KeyError):
            continue
    out = []
    for row in trades:
        if row.get("book") != book or row.get("action") not in ("buy", "sell"):
            continue
        ts = _num(row.get("time"))
        if ts is None:
            continue
        ts = int(ts)
        mint = row.get("mint") or ""
        for span_mint, opened, done in spans:
            if mint == span_mint and opened <= ts <= done:
                out.append(row)
                break
    return out


def day14_outcome(positions, trades, commit_bytes="unchecked"):
    """What the frozen day-14 script would print at its own default cutoff.

    The recorded flag is the branch. This recompute is only the parity check.
    The positions.json tail is what that script can see; older lots are not fed to it.
    NOT_FINAL is not a non-confirmation.
    """
    try:
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
        if not D14.reconciles(closed, _trades_for_closed(trades, closed, "F")):
            reasons.append("day14_closes_do_not_reconcile")
        if commit_bytes == "mismatch":
            reasons.append("day14_commit_bytes_mismatch")
        verdict = rule if not reasons else "NOT_FINAL"
        return dict(verdict=verdict, rule=rule, expectancy=exp, n=len(counted), reasons=reasons)
    except NonFinite:
        return dict(verdict="NOT_FINAL", rule="CONTINUE", expectancy=None, n=0, reasons=["non_finite"])
    except (TypeError, ValueError):
        return dict(verdict="NOT_FINAL", rule="CONTINUE", expectancy=None, n=0, reasons=["malformed_row"])


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
    return "INCONCLUSIVE", NEITHER_NOTE


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
    return "INCONCLUSIVE", NEITHER_NOTE


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


def register_label(branch, label, computable):
    """Section 6, the conservative reading. Never VALIDATED.

    Branch 1 or any KILL is INVALIDATED. STOP is INCONCLUSIVE.
    A branch-2 PASS stays INCONCLUSIVE. VALIDATED would need an owner addendum.
    """
    if not computable or label is None or branch not in (1, 2):
        return "UNKNOWN", ""
    if branch == 1 or str(label).startswith("KILL"):
        return "INVALIDATED", ""
    if label == LABEL_STOP:
        return "INCONCLUSIVE", ""
    if branch == 2 and label == LABEL_PASS:
        return "INCONCLUSIVE", PASS_REGISTER_NOTE
    return "INCONCLUSIVE", NEITHER_NOTE


def _closed_list(branch, label, computable):
    """Section 6 closed-list mapping. A branch-1 descriptive PASS is INCONCLUSIVE.

    This is not the SH-03 line. register_label owns that line.
    """
    if not computable or label is None:
        return "UNKNOWN"
    if label == LABEL_STOP or (branch == 1 and label == LABEL_PASS) or (branch == 2 and label == LABEL_PASS):
        return "INCONCLUSIVE"
    if branch == 1 or str(label).startswith("KILL"):
        return "INVALIDATED"
    return "UNKNOWN"


def _snapshot_reasons(trades, cutoff_ts, snapshot):
    """The snapshot is the book of lots still open at the cutoff."""
    reasons = []
    last = snapshot.get("last_run_ts") if isinstance(snapshot, dict) else None
    try:
        last_ts = int(last) if last not in (None, "") else None
    except (TypeError, ValueError):
        last_ts = None
    if last_ts is None or last_ts > cutoff_ts:
        reasons.append("open_snapshot_after_cutoff")
    for name in BOOKS:
        built, still_open, ok, _refills, reason = build_lots(trades, name)
        if not ok:
            continue
        trade_keys = set()
        expected = set()
        for lot in list(built) + list(still_open):
            key = (lot["mint"], int(lot["opened"]))
            trade_keys.add(key)
            if lot["opened"] > cutoff_ts or in_committed_void(lot["opened"]):
                continue
            if lot.get("closed") is None or lot["closed"] > cutoff_ts:
                expected.add(key)
        present = set()
        opens = (((snapshot.get("books") or {}).get(name) or {}).get("open") or {})
        for pid, pos in opens.items():
            pos = pos or {}
            mint = pos.get("mint") or pid
            try:
                opened = int(pos["opened"])
            except (TypeError, ValueError, KeyError):
                reasons.append(f"{name}_open_snapshot_mismatch")
                continue
            key = (mint, opened)
            present.add(key)
            if key not in trade_keys:
                reasons.append(f"{name}_open_snapshot_mismatch")
        if expected - present:
            reasons.append(f"{name}_open_snapshot_incomplete")
    return list(dict.fromkeys(reasons))


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


def _latest_quote(lot, cutoff_ts):
    """Latest mark or sell quote on this lot at or before the cutoff. Never the entry."""
    if not lot:
        return None
    best = None
    points = list(lot.get("marks") or [])
    for sell in lot.get("sells") or []:
        if sell.get("quote") is None or sell["time"] > cutoff_ts:
            continue
        points.append((sell["time"], sell["quote"], sell["stale"]))
    for ts, px, stale in points:
        if not _finite(px) or ts > cutoff_ts:
            continue
        if best is None or ts >= best[0]:
            best = (ts, px, 1 if stale else 0)
    if best is None:
        return None
    return best[1], best[2]


def _pos_stale(pos):
    return str((pos or {}).get("stale") or "") in ("1", "1.0", "true")


def _value_open(pos, book, trades, cutoff_ts, lot=None):
    """Return (return_or_None, stale, unknown_reason).

    The mark is pos['last_mark'] with pos['stale'], or a quote at or before the
    cutoff when last_mark is absent. The entry price is never the mark.
    A confirmed open rug is -100%.
    """
    if is_rug_open(pos):
        return -1.0, 0, None
    try:
        entry = _num(pos.get("entry"))
        frac = _num(pos.get("frac"))
        if pos.get("frac") in (None, ""):
            return None, 0, "open_frac_missing"
        cost = entry_cost_of_lot(pos, trades, book)
        raw_last = pos.get("last_mark")
        last = None if raw_last in (None, "") else _num(raw_last)
        quote = _latest_quote(lot, cutoff_ts)
    except NonFinite:
        return None, 0, "non_finite"
    if entry is None or entry <= 0 or frac is None or cost is None:
        return None, 0, "open_mark_unknown"
    if last is not None:
        px, stale = last, (1 if _pos_stale(pos) else 0)
    elif quote is not None:
        px, stale = quote
    else:
        return None, 0, "open_mark_unknown"
    if not _finite(px):
        return None, 0, "open_mark_unknown"
    realized = 0.0
    try:
        if pos.get("pnl") not in (None, ""):
            realized = _num(pos.get("pnl"))
            if realized is None:
                realized = 0.0
        ret = mark_return(entry, frac, cost, exit_haircut_of(pos), px, realized)
    except NonFinite:
        return None, 0, "non_finite"
    except (TypeError, ValueError, ZeroDivisionError):
        return None, 0, "open_mark_unknown"
    if not _finite(ret):
        return None, 0, "non_finite"
    return ret, stale, None


def _book_sample(name, trades, cutoff_ts, snapshot):
    """Closes come from the append-only lots. Opens come from the cutoff snapshot."""
    voided = late = rugs = stale = post_v3 = 0
    close_returns = []
    realized_returns = []
    all_in = []
    unknown = None
    built, still_open, ok, _refills, reason = build_lots(trades, name)
    if reason == "non_finite":
        unknown = "non_finite"
    elif not ok:
        unknown = reason or "malformed_row"
    by_open = {}
    for lot in list(built) + list(still_open):
        by_open[(lot["mint"], lot["opened"])] = lot
    counted = set()
    for lot in built:
        if unknown in ("non_finite", "malformed_row"):
            break
        if in_committed_void(lot["opened"]):
            voided += 1
            continue
        if lot["closed"] is None or lot["closed"] > cutoff_ts:
            late += 1
            continue
        logged = lot["pnl"] / C.TICKET_SOL
        if not _finite(logged):
            unknown = "non_finite"
            continue
        rug = bool(lot.get("rug"))
        ret = min(logged, -1.0) if rug else logged
        if rug:
            rugs += 1
        close_returns.append(ret)
        realized_returns.append(logged)
        all_in.append(ret)
        counted.add((lot["mint"], lot["opened"]))
        if lot.get("post_v3"):
            post_v3 += 1
    snap = (((snapshot or {}).get("books") or {}).get(name) or {}).get("open") or {}
    for pid, pos in snap.items():
        if unknown in ("non_finite", "malformed_row"):
            break
        pos = dict(pos)
        pos.setdefault("mint", pid)
        opened = pos.get("opened")
        if opened is None:
            unknown = "open_mark_unknown"
            continue
        try:
            opened_i = int(opened)
        except (TypeError, ValueError):
            unknown = "malformed_row"
            continue
        if opened_i > cutoff_ts:
            continue
        if in_committed_void(opened_i):
            voided += 1
            continue
        if (pos.get("mint") or "", opened_i) in counted:
            continue
        lot = by_open.get((pos.get("mint") or "", opened_i))
        ret, st, why = _value_open(pos, name, trades, cutoff_ts, lot)
        stale += st
        if why:
            unknown = why
            continue
        if is_rug_open(pos):
            rugs += 1
        all_in.append(ret)
    return dict(
        closes=len(close_returns), voided=voided, late=late, rugs=rugs, stale=stale,
        post_v3=post_v3, close_returns=close_returns, realized_returns=realized_returns,
        all_in=all_in, unknown=unknown,
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
           tasks_sha256="unchecked", data_dir=None, day14_recorded=None,
           open_snapshot=None, open_snapshot_commit=None, synthetic=False):
    assert_shared()
    n_boot = TWIN.BOOT if n_boot is None else int(n_boot)
    if n_boot < 1:
        raise ValueError("bootstrap must be positive")
    registered = derived_cutoff_text()
    cutoff_ts = D14.parse_utc(cutoff_text)
    binding = cutoff_text == registered
    early = day14_outcome(positions, trades, commit_bytes)
    reasons = []
    if day14_recorded not in ("STOP", "CONTINUE"):
        reasons.append("day14_recorded_missing")
        branch = None
    elif early["verdict"] in ("STOP", "CONTINUE") and early["verdict"] != day14_recorded:
        reasons.append("day14_branch_mismatch")
        branch = None
    else:
        branch = 1 if day14_recorded == "STOP" else 2
    if not open_snapshot_commit or open_snapshot is None:
        reasons.append("open_snapshot_missing")
    else:
        reasons.extend(_snapshot_reasons(trades, cutoff_ts, open_snapshot))
    samples = {name: _book_sample(name, trades, cutoff_ts, open_snapshot) for name in BOOKS}
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
    if synthetic:
        verdict = "SYNTHETIC (not a verdict)"
    f_lo, f_hi, lb95, lb_holm = bootstrap_bounds(
        samples["F"]["all_in"], samples["R"]["all_in"], n_boot, SEED,
    )
    sh03, sh03_note = register_label(branch, label, final and label is not None)
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
        f"day14_recorded: {day14_recorded if day14_recorded else 'missing'}",
        f"day14_verdict: {early['verdict']}",
        f"day14_rule_result: {early['rule']}",
        f"day14_n_closes: {early['n']}",
        f"day14_recompute_reasons: {'none' if not early['reasons'] else ','.join(early['reasons'])}",
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
        f"unmodeled_costs: {UNMODELED_COSTS}",
        f"jupiter_http_400: {JUPITER_400}",
        "gaps: not logged by v6.5, not imputed",
        f"positions_sha256: {positions_sha256}",
        f"trades_sha256: {trades_sha256}",
        f"tasks_sha256: {tasks_sha256}",
        f"commit_bytes: {commit_bytes}",
        f"data_commit: {data_commit}",
        f"VERDICT: {verdict}",
    ])
    return "\n".join(lines) + "\n"


def refused_before_cutoff(now_ts, synthetic):
    """True when a real-data run must stop before any file is opened.

    Fixture mode does not skip the sentinel check in main(). It only skips this clock.
    """
    if synthetic:
        return False
    return float(now_ts) < refusal_instant()


def _load_snapshot(commit, data_dir, synthetic):
    """The positions file from the last desk run at or before the cutoff.

    Fixture mode reads open-snapshot.json and does not call git.
    """
    if not commit:
        return None
    if synthetic:
        path = os.path.join(data_dir, SNAPSHOT_NAME)
        if not os.path.isfile(path):
            return None
        return D14.load_json(path)
    raw = None
    try:
        import subprocess
        raw = subprocess.check_output(
            ["git", "-C", REPO, "show", f"{commit}:solana-desk/positions.json"],
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, OSError):
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None


def main(argv=None, *, now=None):
    p = argparse.ArgumentParser(description="Day-28 table and headline. Analysis only. Refuses real data before the cutoff.")
    p.add_argument("--data-dir", required=True, help="desk-data solana-desk directory")
    p.add_argument("--cutoff", default=None, help="UTC cutoff ending in Z; default is the derived day-28 instant")
    p.add_argument("--data-commit", default="unknown", help="commit of the desk data that was read")
    p.add_argument("--synthetic", action="store_true", help="fixture only; requires DAY28_SYNTHETIC_FIXTURE")
    p.add_argument("--test", action="store_true", help="same as --synthetic; fixture only")
    p.add_argument("--not-final-unfixable", action="store_true",
                   help="the owner has decided a NOT_FINAL result cannot be repaired")
    p.add_argument("--bootstrap", type=int, default=TWIN.BOOT)
    p.add_argument("--day14-recorded", required=True, choices=("STOP", "CONTINUE"),
                   help="the recorded day-14 final verdict")
    p.add_argument("--open-snapshot-commit", required=True,
                   help="commit of the last desk run at or before the cutoff")
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
                "day28_verdict: refused before the cutoff. Real desk data is not read.",
                file=sys.stderr,
            )
            return 2
        if not synthetic and o.bootstrap != TWIN.BOOT:
            print("day28_verdict: a real run uses bootstrap 10000", file=sys.stderr)
            return 2
        if o.bootstrap < 1:
            raise ValueError("bootstrap must be positive")
        if synthetic:
            # The sentinel is the first read. A 40-hex commit is refused before any other file.
            sentinel = os.path.join(o.data_dir, SENTINEL_NAME)
            if not os.path.isfile(sentinel):
                print(
                    "day28_verdict: fixture mode requires a DAY28_SYNTHETIC_FIXTURE file "
                    "in the data directory. No other file was read.",
                    file=sys.stderr,
                )
                return 2
            git_mark = os.path.join(o.data_dir, ".git")
            if os.path.isdir(git_mark) or os.path.isfile(git_mark):
                print(
                    "day28_verdict: fixture mode refuses a data directory that is a git checkout.",
                    file=sys.stderr,
                )
                return 2
            if is_git_sha(o.data_commit) or is_git_sha(o.open_snapshot_commit):
                print("day28_verdict: fixture mode refuses a 40-hex commit.", file=sys.stderr)
                return 2
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
        snapshot = _load_snapshot(o.open_snapshot_commit, o.data_dir, synthetic)
        commit_bytes = "unchecked"
        if not synthetic:
            gpos = D14.git_blob_sha256(o.data_commit, "solana-desk/positions.json")
            gtr = D14.git_blob_sha256(o.data_commit, "solana-desk/trades.csv")
            if gpos is not None and gtr is not None:
                commit_bytes = "match" if (gpos == pos_hash and gtr == tr_hash) else "mismatch"
        text = render(
            D14.load_json(pos_path), D14.load_trades(tr_path), cutoff, o.data_commit,
            pos_hash, tr_hash, tasks, commit_bytes, o.not_final_unfixable, o.bootstrap,
            task_hash, o.data_dir, o.day14_recorded, snapshot, o.open_snapshot_commit, synthetic,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"day28_verdict: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
