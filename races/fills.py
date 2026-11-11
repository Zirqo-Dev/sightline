"""Paper fills at the logged lite-api outAmount. Never a mid price."""

from datetime import datetime, timezone

from races.impact import buy_impact_bps, sell_impact_bps, side_cost_bps

LAMPORTS = 1_000_000_000


def slot_date(slot):
    return slot.strftime("%Y-%m-%d")


def parse_slot(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def book_of(state, wallet, starting):
    books = state.setdefault("wallets", {})
    if wallet not in books:
        books[wallet] = {
            "cash_sol": float(starting),
            "positions": [],
            "entered": [],
            "fills_by_day": {},
            "cooldowns": {},
        }
    return books[wallet]


def fills_today(book, day):
    return int((book.get("fills_by_day") or {}).get(day) or 0)


def _out_sol(quote):
    return quote.out_raw / LAMPORTS


def manage_positions(ctx, wallet, decide, reserve_of, liq_of=None):
    """Mark or exit open positions, oldest first. Returns True if entries are blocked."""
    if wallet in ctx.skip_marks:
        return wallet in ctx.jup.block_entries
    book = book_of(ctx.state, wallet, ctx.cfg["starting_sol"])
    dead_after = int(ctx.cfg["death"]["failed_sell_quotes"])
    still = []
    for pos in list(book["positions"]):
        if liq_of is not None:
            liq = liq_of(pos)
            if liq is not None:
                pos["liquidity_usd"] = liq
                floor = ctx.cfg["wallets"]["R5"]["liq_floor_usd"] if wallet == "R5" else None
                if floor is not None and liq < float(floor):
                    _close_dead(ctx, wallet, book, pos, "liq_floor")
                    continue
        status = ctx.pool_status(pos.get("pool_id"))
        if status == "gone":
            _close_dead(ctx, wallet, book, pos, "pool_gone")
            continue
        quote = ctx.jup.quote(wallet, pos["mint"], ctx.cfg["wsol_mint"], int(pos["tokens_raw"]), "exit_quote")
        if quote is None:
            if wallet in ctx.jup.block_entries:
                pos["mark_stale"] = True
                still.append(pos)
                continue
            pos["failed_sell_quotes"] = int(pos.get("failed_sell_quotes") or 0) + 1
            pos["mark_stale"] = True
            if pos["failed_sell_quotes"] >= dead_after:
                _close_dead(ctx, wallet, book, pos, "no_route")
                continue
            still.append(pos)
            continue
        pos["failed_sell_quotes"] = 0
        pos["mark_stale"] = False
        reserve = reserve_of(pos)
        view = _view(pos, quote, reserve, ctx.cfg)
        pos["mark_price_sol"] = view["price"]
        pos["mark_sol"] = view["mark"]
        if view["price"] > float(pos.get("peak_price_sol") or 0):
            pos["peak_price_sol"] = view["price"]
        choice = decide(pos, view)
        if choice is None:
            still.append(pos)
            continue
        reason, stop = choice
        _close_quote(ctx, wallet, book, pos, quote, reserve, reason, stop)
    book["positions"] = still
    return wallet in ctx.jup.block_entries


def try_enter(ctx, wallet, mint, symbol, pool_id, reserve, meme=None, liquidity_usd=None, bucket=None):
    book = book_of(ctx.state, wallet, ctx.cfg["starting_sol"])
    ticket = float(ctx.cfg["ticket_sol"])
    if wallet in ctx.jup.block_entries:
        ctx.log(wallet, "decision", {"action": "skip", "reason": "cap", "mint": mint})
        return False
    if book["cash_sol"] < ticket:
        ctx.log(wallet, "decision", {"action": "skip", "reason": "cash_short", "mint": mint})
        return False
    if wallet != "R5" and mint in book["entered"]:
        ctx.log(wallet, "decision", {"action": "skip", "reason": "no_reentry", "mint": mint})
        return False
    wcfg = ctx.cfg["wallets"][wallet]
    cap = wcfg.get("max_fills_per_day")
    if cap is not None and fills_today(book, slot_date(ctx.slot)) >= int(cap):
        ctx.log(wallet, "decision", {"action": "skip", "reason": "daily_cap", "mint": mint})
        return False
    auth = ctx.chain.mint_auth(mint)
    if not auth or not auth.get("ok"):
        ctx.log(wallet, "decision", {"action": "skip", "reason": "authority_unknown", "mint": mint})
        return False
    if not auth.get("revoked"):
        ctx.log(wallet, "decision", {"action": "skip", "reason": "authority", "mint": mint})
        return False
    quote = ctx.jup.quote(wallet, ctx.cfg["wsol_mint"], mint, int(ctx.cfg["ticket_lamports"]), "entry_quote")
    if quote is None:
        ctx.log(wallet, "decision", {"action": "skip", "reason": "entry_quote", "mint": mint})
        return False
    sell = ctx.jup.quote(wallet, mint, ctx.cfg["wsol_mint"], quote.out_raw, "entry_quote")
    if sell is None:
        ctx.log(wallet, "decision", {"action": "skip", "reason": "sell_quote", "mint": mint})
        return False
    impact = buy_impact_bps(ticket, reserve)
    cost, source = side_cost_bps(impact, False, ctx.cfg)
    outlay = ticket * (1.0 + cost / 10000.0)
    if book["cash_sol"] < outlay:
        ctx.log(wallet, "decision", {"action": "skip", "reason": "cash_short", "mint": mint, "cost_bps": cost})
        return False
    decimals = int(auth.get("decimals") or 0)
    tokens = quote.out_raw / (10 ** decimals) if decimals else float(quote.out_raw)
    price = ticket / tokens if tokens else 0.0
    view = _view(
        {"tokens_raw": quote.out_raw, "decimals": decimals, "entry_outlay_sol": outlay, "entry_price_sol": price},
        sell, reserve, ctx.cfg,
    )
    pos = {
        "symbol": symbol or "TOKEN",
        "mint": mint,
        "pool_id": pool_id,
        "opened_at": ctx.slot_iso,
        "entry_slot": ctx.slot_iso,
        "tokens_raw": str(quote.out_raw),
        "decimals": decimals,
        "size_sol": ticket,
        "entry_price_sol": price,
        "entry_cost_bps": cost,
        "entry_cost_source": source,
        "entry_outlay_sol": outlay,
        "mark_price_sol": view["price"],
        "mark_sol": view["mark"],
        "failed_sell_quotes": 0,
        "mark_stale": False,
        "peak_price_sol": max(price, view["price"]),
        "meme": meme,
        "bucket": bucket,
        "liquidity_usd": liquidity_usd,
        "reserve_sol": reserve,
    }
    book["cash_sol"] -= outlay
    book["positions"].append(pos)
    book["entered"].append(mint)
    day = slot_date(ctx.slot)
    book["fills_by_day"][day] = fills_today(book, day) + 1
    ctx.ledger.allow_add(mint, pool_id)
    ctx.ledger.append("trades.jsonl", {
        "ts": ctx.slot_iso,
        "wallet": wallet,
        "action": "buy",
        "symbol": pos["symbol"],
        "mint": mint,
        "fill_price_sol": price,
        "size_sol": ticket,
        "cost_bps": cost,
        "pnl_sol": None,
        "exit_reason": None,
        "meme": meme,
        "bucket": bucket,
    })
    ctx.log(wallet, "decision", {
        "action": "buy", "mint": mint, "pool_id": pool_id, "cost_bps": cost, "cost_source": source,
    })
    return True


def _view(pos, quote, reserve, cfg):
    out_sol = _out_sol(quote)
    decimals = int(pos.get("decimals") or 0)
    raw = int(pos.get("tokens_raw") or 0)
    tokens = raw / (10 ** decimals) if decimals else float(raw)
    price = out_sol / tokens if tokens else 0.0
    impact = sell_impact_bps(out_sol, reserve)
    cost, source = side_cost_bps(impact, False, cfg)
    cost_stop, source_stop = side_cost_bps(impact, True, cfg)
    return {
        "price": price,
        "out_sol": out_sol,
        "impact_bps": impact,
        "mark": out_sol * (1.0 - cost / 10000.0),
        "mark_stop": out_sol * (1.0 - cost_stop / 10000.0),
        "cost": cost,
        "cost_stop": cost_stop,
        "cost_source": source,
        "cost_source_stop": source_stop,
    }


def _close_quote(ctx, wallet, book, pos, quote, reserve, reason, stop):
    view = _view(pos, quote, reserve, ctx.cfg)
    cost = view["cost_stop"] if stop else view["cost"]
    source = view["cost_source_stop"] if stop else view["cost_source"]
    proceeds = view["out_sol"] * (1.0 - cost / 10000.0)
    pnl = proceeds - float(pos["entry_outlay_sol"])
    book["cash_sol"] += proceeds
    _trade_sell(ctx, wallet, pos, view["price"], view["out_sol"], cost, pnl, reason)
    ctx.log(wallet, "decision", {"action": "sell", "reason": reason, "mint": pos["mint"], "cost_bps": cost, "cost_source": source, "stop": bool(stop)})
    if wallet == "R5":
        book.setdefault("cooldowns", {})[pos["mint"]] = ctx.slot_iso


def _close_dead(ctx, wallet, book, pos, reason):
    pnl = -float(pos["entry_outlay_sol"])
    _trade_sell(ctx, wallet, pos, 0.0, 0.0, 0.0, pnl, reason)
    ctx.log(wallet, "decision", {"action": "sell", "reason": reason, "mint": pos["mint"], "mark": 0})
    if wallet == "R5":
        book.setdefault("cooldowns", {})[pos["mint"]] = ctx.slot_iso


def _trade_sell(ctx, wallet, pos, price, size_sol, cost, pnl, reason):
    ctx.ledger.allow_add(pos.get("mint"), pos.get("pool_id"))
    ctx.ledger.append("trades.jsonl", {
        "ts": ctx.slot_iso,
        "wallet": wallet,
        "action": "sell",
        "symbol": pos.get("symbol") or "TOKEN",
        "mint": pos.get("mint"),
        "fill_price_sol": price,
        "size_sol": size_sol,
        "cost_bps": cost,
        "pnl_sol": pnl,
        "exit_reason": reason,
        "meme": pos.get("meme"),
        "bucket": pos.get("bucket"),
    })


def hours_between(entry_iso, slot):
    return (slot - parse_slot(entry_iso)).total_seconds() / 3600.0


def net_of(mark_sol, entry_outlay):
    if not entry_outlay:
        return 0.0
    return float(mark_sol) / float(entry_outlay) - 1.0
