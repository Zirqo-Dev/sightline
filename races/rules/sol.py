"""SOL hold. Official net % is 0. The USDC quote is 0.5 SOL, scaled by 10."""


def on_run(ctx):
    cfg = ctx.cfg
    quote = ctx.jup.quote("SOL", cfg["wsol_mint"], cfg["usdc_mint"], int(cfg["ticket_lamports"]))
    mark = ctx.state.setdefault("sol_usdc", {"usdc": None, "stale": False})
    if quote is None:
        mark["stale"] = True
        ctx.log("SOL", "decision", {"action": "usdc_mark", "stale": True})
        return
    usdc = quote.out_raw / (10 ** int(cfg["usdc_decimals"])) * float(cfg["sol_usdc_scale"])
    mark["usdc"] = usdc
    mark["stale"] = False
    ctx.log("SOL", "decision", {"action": "usdc_mark", "usdc": usdc, "stale": False, "scale": cfg["sol_usdc_scale"]})
