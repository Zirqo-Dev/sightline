"""Discount, onset, missed hours, and the one end-of-window verdict.

No returns are computed. The gate is a count of onsets.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_EVEN
from fractions import Fraction

WINDOW_HOURS = 336
ONSET_BPS = 30
REARM_BPS = 15
REARM_HOURS = 72
COVERAGE_MIN = Fraction(7, 10)
MIN_ONSETS = 4
MIN_DAYS = 3
MISSING_REASONS = ("no_run", "rpc_fail", "quote_fail", "rate_stale")


def discount_bps(price, rate):
    """D = (1 - P / R) * 10000."""
    if rate <= 0:
        raise ValueError("rate")
    return (1 - (price / rate)) * 10000


def fee_fraction(numerator, denominator):
    if numerator is None or denominator is None:
        return Fraction(0)
    if numerator <= 0 or denominator <= 0:
        return Fraction(0)
    return Fraction(numerator, denominator)


def adjusted_rate(rate, fraction):
    """R x (1 - withdrawal fee). None when the fee would wipe out the rate."""
    if fraction >= 1:
        return None
    adjusted = rate * (1 - fraction)
    if adjusted <= 0:
        return None
    return adjusted


def parse_iso(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def iso(when):
    when = when.astimezone(timezone.utc).replace(microsecond=0)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_seconds(when):
    return when.astimezone(timezone.utc).replace(microsecond=0)


def floor_hour(when):
    """UTC hour that contains `when`. T0 is this value at the freeze."""
    return when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def slot_index(t0, now):
    """Whole UTC hours from the floored T0 hour to the floored hour of `now`."""
    return int((floor_hour(now) - floor_hour(t0)).total_seconds() // 3600)


def hour_start(t0, slot):
    return floor_hour(t0) + timedelta(hours=slot)


class Arm:
    def __init__(self):
        self.armed = True
        self.onset_hour = None


def consider(arm, d_bps, hour):
    """Re-arm, then maybe onset. Returns True when this hour is an onset.

    The spec does not say whether the hour that first reaches 72h can itself
    onset. This re-arms first, then applies D >= 30, so it can.
    Exactly 72h qualifies (`hour >= onset + 72h`). Exactly 30 bps onsets.
    Exactly 15 bps re-arms.
    """
    if not arm.armed and arm.onset_hour is not None:
        due = arm.onset_hour + timedelta(hours=REARM_HOURS)
        if d_bps <= REARM_BPS or hour >= due:
            arm.armed = True
            arm.onset_hour = None
    if arm.armed and d_bps >= ONSET_BPS:
        arm.armed = False
        arm.onset_hour = hour
        return True
    return False


def _captures(records, symbol):
    rows = [
        rec for rec in records
        if rec.get("symbol") == symbol and rec.get("type") == "capture"
    ]
    rows.sort(key=lambda rec: (rec.get("slot", 0), rec.get("hour_start", "")))
    return rows


def arm_after(records, symbol):
    arm = Arm()
    for rec in _captures(records, symbol):
        d_bps = Fraction(int(rec["d_bps_num"]), int(rec["d_bps_den"]))
        consider(arm, d_bps, parse_iso(rec["hour_start"]))
    return arm


def _d_of_raw(rec):
    return Fraction(int(rec["d_bps_num"]), int(rec["d_bps_den"]))


def _d_of_fee(which):
    def d_of(rec):
        price = Fraction(int(rec["price_num"]), int(rec["price_den"]))
        rate = Fraction(int(rec["rate_num"]), int(rec["rate_den"]))
        fees = rec.get("withdrawal_fees") or {}
        field = fees.get(which) or {}
        fraction = fee_fraction(field.get("numerator"), field.get("denominator"))
        rate = adjusted_rate(rate, fraction)
        if rate is None:
            return None
        return discount_bps(price, rate)
    return d_of


def replay_onsets(records, symbol, d_of):
    arm = Arm()
    found = []
    for rec in _captures(records, symbol):
        d_bps = d_of(rec)
        if d_bps is None:
            continue
        if consider(arm, d_bps, parse_iso(rec["hour_start"])):
            found.append(rec)
    return found


def previous_missing(records, symbol, slot):
    if slot <= 0:
        return False
    prev = [
        rec for rec in records
        if rec.get("symbol") == symbol and rec.get("slot") == slot - 1
    ]
    if not prev:
        return True
    return prev[-1].get("type") == "missing"


def has_record(records, symbol, slot):
    return any(rec.get("symbol") == symbol and rec.get("slot") == slot for rec in records)


def fmt_bps(value):
    if value is None:
        return None
    dec = Decimal(value.numerator) / Decimal(value.denominator)
    return format(dec.quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN), "f")


def fmt_ratio(value):
    if value is None:
        return None
    dec = Decimal(value.numerator) / Decimal(value.denominator)
    return format(dec.quantize(Decimal("0.000000000001"), rounding=ROUND_HALF_EVEN), "f")


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    count = len(ordered)
    mid = count // 2
    if count % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def coverage_of(records, symbols):
    expected = WINDOW_HOURS * len(symbols)
    captured = [
        rec for rec in records
        if rec.get("type") == "capture" and rec.get("symbol") in symbols and 0 <= rec.get("slot", -1) < WINDOW_HOURS
    ]
    ratio = Fraction(len(captured), expected) if expected else Fraction(0)
    per = {}
    for symbol in symbols:
        got = sum(1 for rec in captured if rec.get("symbol") == symbol)
        per[symbol] = {"captured": got, "expected": WINDOW_HOURS}
    return {
        "captured": len(captured),
        "expected": expected,
        "ratio": ratio,
        "per_lst": per,
    }


def build_verdict(records, symbols):
    """One read at T0+336h. Coverage under 70% is BLOCKED, not KILL.

    Fee-adjusted counts are display only. Raw D decides KILL and PASS.
    Both current withdrawal fees are reported because the spec does not
    say which field is "the withdrawal fee".
    "Resting" D is not defined. median_resting_d_bps is the median D on
    captured hours with D < 30 bps.
    An onset's UTC day is the date of its slot hour (T0 + slot hours).
    """
    cov = coverage_of(records, symbols)
    raw = []
    per_lst = {symbol: 0 for symbol in symbols}
    for symbol in symbols:
        found = replay_onsets(records, symbol, _d_of_raw)
        raw.extend(found)
        per_lst[symbol] = len(found)
    days = {}
    gap = 0
    for rec in raw:
        day = rec["hour_start"][:10]
        days[day] = days.get(day, 0) + 1
        if rec.get("onset_after_gap"):
            gap += 1
    fee_counts = {}
    for which in ("sol_withdrawal_fee", "stake_withdrawal_fee"):
        count = 0
        for symbol in symbols:
            count += len(replay_onsets(records, symbol, _d_of_fee(which)))
        fee_counts[which] = count
    captured = [
        rec for rec in records
        if rec.get("type") == "capture" and rec.get("symbol") in symbols
    ]
    ds = [_d_of_raw(rec) for rec in captured]
    resting = [value for value in ds if value < ONSET_BPS]
    if cov["ratio"] < COVERAGE_MIN:
        result = "BLOCKED"
        reason = "coverage < 70%"
    elif len(raw) < MIN_ONSETS:
        result = "KILL"
        reason = "onsets < 4"
    elif len(days) < MIN_DAYS:
        result = "KILL"
        reason = "onsets on fewer than 3 distinct UTC days"
    else:
        result = "PASS"
        reason = "onsets >= 4 on >= 3 distinct UTC days and coverage >= 70%"
    return {
        "verdict": result,
        "reason": reason,
        "evidence": "NONE",
        "tally_note": "A count gate tests no returns, so it adds nothing to the false-pass tally.",
        "pass_note": "PASS means only worth pitching a returns book after Nov 25.",
        "onsets": len(raw),
        "onset_days": sorted(days),
        "onsets_per_day": days,
        "onsets_per_lst": per_lst,
        "onset_after_gap": gap,
        "max_d_bps": fmt_bps(max(ds) if ds else None),
        "median_resting_d_bps": fmt_bps(_median(resting)),
        "median_resting_definition": "median D over captured hours with D < 30 bps",
        "onset_counts": {
            "raw": len(raw),
            "sol_withdrawal_fee": fee_counts["sol_withdrawal_fee"],
            "stake_withdrawal_fee": fee_counts["stake_withdrawal_fee"],
        },
        "coverage": {
            "captured": cov["captured"],
            "expected": cov["expected"],
            "ratio": fmt_ratio(cov["ratio"]),
        },
        "coverage_per_lst": cov["per_lst"],
    }
