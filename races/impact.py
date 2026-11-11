"""Cost rule (d). Buy impact is constant-product. Sell impact is out/reserve.

Missing SOL reserve charges the 600 bps cap on that side and is logged.
Only a stop exit adds 150 bps. No network or priority fee.
"""


def buy_impact_bps(dx_sol, reserve_sol):
    if reserve_sol is None or reserve_sol <= 0 or dx_sol is None or dx_sol < 0:
        return None
    return 10000.0 * float(dx_sol) / (float(reserve_sol) + float(dx_sol))


def sell_impact_bps(out_sol, reserve_sol):
    if reserve_sol is None or reserve_sol <= 0 or out_sol is None or out_sol < 0:
        return None
    return 10000.0 * float(out_sol) / float(reserve_sol)


def side_cost_bps(impact_bps, stop, cfg):
    """Return (cost_bps, source). source is 'cap_missing_reserve' or 'formula'."""
    costs = cfg["costs"]
    cap = float(costs["cap_bps"])
    if impact_bps is None:
        return cap, "cap_missing_reserve"
    extra = float(costs["stop_extra_bps"]) if stop else 0.0
    raw = float(costs["base_bps"]) + float(costs["impact_weight"]) * float(impact_bps) + extra
    return min(cap, raw), "formula"
