"""R2 uses R1's draw, then holder, dev, sell, and authority filters."""

from races.chain import top10_and_dev
from races.fills import hours_between, manage_positions, net_of, try_enter


def on_run(ctx):
    w = ctx.cfg["wallets"]["R2"]

    def decide(pos, view):
        if net_of(view["mark"], pos["entry_outlay_sol"]) >= float(w["take_profit"]):
            return "take_profit", False
        if net_of(view["mark_stop"], pos["entry_outlay_sol"]) <= float(w["hard_stop"]):
            return "stop", True
        if hours_between(pos["entry_slot"], ctx.slot) >= float(w["time_stop_hours"]):
            return "time_stop", False
        return None

    manage_positions(ctx, "R2", decide, lambda pos: pos.get("reserve_sol"))
    if not ctx.draw or not ctx.draw.get("mint"):
        return
    row = ctx.by_mint.get(ctx.draw["mint"]) or {}
    if not _filters(ctx, row):
        return
    try_enter(ctx, "R2", row["mint"], row.get("symbol"), row.get("pool_id"), row.get("reserve_sol"))


def _filters(ctx, row):
    w = ctx.cfg["wallets"]["R2"]
    sells = row.get("sells_h1")
    if sells is None or int(sells) < int(w["min_sells_h1"]):
        ctx.log("R2", "decision", {"action": "skip", "reason": "sells", "mint": row.get("mint")})
        return False
    creator = row.get("creator")
    if not creator:
        creator = ctx.chain.metadata_update_authority(row["mint"])
    pool_owner = ctx.chain.account_owner_program(row["pool_id"]) if row.get("pool_id") else None
    checked = top10_and_dev(ctx.chain, row["mint"], row.get("pool_id"), creator, pool_owner)
    if checked.get("skip"):
        ctx.log("R2", "decision", {"action": "skip", "reason": checked["skip"], "mint": row.get("mint")})
        return False
    if checked["top10_pct"] >= float(w["max_top10_pct"]):
        ctx.log("R2", "decision", {"action": "skip", "reason": "top10", "mint": row.get("mint"), "top10_pct": checked["top10_pct"]})
        return False
    if checked["dev_pct"] > float(w["max_dev_pct"]):
        ctx.log("R2", "decision", {"action": "skip", "reason": "dev", "mint": row.get("mint"), "dev_pct": checked["dev_pct"]})
        return False
    ctx.log("R2", "decision", {
        "action": "filters_pass", "mint": row.get("mint"),
        "top10_pct": checked["top10_pct"], "dev_pct": checked["dev_pct"], "sells_h1": sells,
    })
    return True
