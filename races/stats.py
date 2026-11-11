"""Official rank in SOL, with no scaling before 30 days.

The 95% interval is a day-clustered bootstrap of mean per-trade net %.
"""

import random


def since_start_pct(equity, starting):
    if not starting:
        return 0.0
    return (float(equity) / float(starting) - 1.0) * 100.0


def official_pct(equity_now, history, slot_ts, period_days):
    """history is a list of (ts_epoch, equity) oldest first.

    Before `period_days` of history, return the since-start figure's inputs
    as ('since_start', None). After that, equity_now / equity_at_or_before
    slot-period - 1.
    """
    cutoff = slot_ts - period_days * 86400
    prior = [eq for ts, eq in history if ts <= cutoff]
    if not prior:
        return "since_start", None
    base = prior[-1]
    if not base:
        return "since_start", None
    return "trailing_30d", (float(equity_now) / float(base) - 1.0) * 100.0


def day_count(started_ts, slot_ts):
    if started_ts is None:
        return 0
    return max(0, int((slot_ts - started_ts) // 86400))


def bootstrap_ci(trades, draws, seed, min_n):
    """trades: list of {day, net_pct}. Percentile interval, or None."""
    if len(trades) < min_n:
        return None
    clusters = {}
    for row in trades:
        clusters.setdefault(row["day"], []).append(float(row["net_pct"]))
    keys = sorted(clusters)
    rng = random.Random(int(seed))
    means = []
    n_keys = len(keys)
    for _ in range(int(draws)):
        vals = []
        for _k in range(n_keys):
            vals.extend(clusters[keys[rng.randrange(n_keys)]])
        means.append(sum(vals) / len(vals))
    means.sort()
    n = len(means)
    lo = means[int(0.025 * n)]
    hi = means[min(n - 1, int(0.975 * n))]
    return lo, hi


def deployed_pct(equity, deployed_sol):
    if not equity:
        return 0.0
    return 100.0 * float(deployed_sol) / float(equity)


def average(values):
    if not values:
        return 0.0
    return sum(values) / len(values)
