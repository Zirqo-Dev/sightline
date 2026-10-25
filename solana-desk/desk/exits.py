"""Exit logic. Stale-aware; -100% only if pool confirmed gone or no route for N consecutive checks."""
from . import config as C

def new_position(entry_px, liq0, opened_ts):
    return dict(entry=entry_px, peak=entry_px, steps=0, liq0=liq0, opened=opened_ts, frac=1.0,
                last_mark=entry_px, stale=0, no_route_count=0)

def check(pos, mark, liq, now_ts, status="ok"):
    """status: ok | temp_error | no_route | pool_gone. Returns list of (action, frac, why, px)."""
    E = C.EXIT; acts = []
    if status == "pool_gone":
        return [("sell", pos["frac"], "pool_gone", 0.0)]
    if status == "temp_error":
        pos["stale"] = 1; return [("mark", 0, "stale", pos["last_mark"])]
    if status == "no_route":
        pos["no_route_count"] += 1; pos["stale"] = 1
        if pos["no_route_count"] >= E["no_route_kill_checks"]:
            return [("sell", pos["frac"], "no_route_3x", 0.0)]
        return [("mark", 0, "stale", pos["last_mark"])]
    pos["no_route_count"] = 0; pos["stale"] = 0; pos["last_mark"] = mark
    pos["peak"] = max(pos["peak"], mark)
    r = mark / pos["entry"] - 1
    if liq is not None and pos["liq0"] and liq < pos["liq0"] * (1 - E["liq_drop"]):
        return [("sell", pos["frac"], "liquidity_pulled", mark)]
    if r <= E["hard_stop"]:
        return [("sell", pos["frac"], "hard_stop", mark)]
    for i, (lvl, f) in enumerate(E["ladder"]):
        if pos["steps"] == i and r >= lvl:
            acts.append(("sell", f, f"take_profit_{int(lvl*100)}", mark)); pos["steps"] += 1; pos["frac"] -= f
    if pos["steps"] >= 1 and mark <= pos["peak"] * (1 - E["trail"]):
        acts.append(("sell", pos["frac"], "trailing_stop", mark)); return acts
    if (now_ts - pos["opened"]) / 60 >= E["time_stop_min"] and r < E["time_stop_keep_if_up"] and pos["frac"] > 0:
        acts.append(("sell", pos["frac"], "time_stop", mark))
    return acts
