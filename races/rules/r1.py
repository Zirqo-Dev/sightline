"""R1 random graduate. Same draw as R2. Exit only at 4h or at 0."""

from races.fills import hours_between, manage_positions, try_enter


def on_run(ctx):
    def decide(pos, view):
        limit = float(ctx.cfg["wallets"]["R1"]["time_stop_hours"])
        if hours_between(pos["entry_slot"], ctx.slot) >= limit:
            return "time_stop", False
        return None

    def reserve_of(pos):
        return pos.get("reserve_sol")

    manage_positions(ctx, "R1", decide, reserve_of)
    if not ctx.draw or not ctx.draw.get("mint"):
        return
    row = ctx.by_mint.get(ctx.draw["mint"]) or {}
    try_enter(
        ctx, "R1", ctx.draw["mint"], row.get("symbol"), row.get("pool_id"),
        row.get("reserve_sol"),
    )
