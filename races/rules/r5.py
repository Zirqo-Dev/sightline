"""R5 established breakout. Memes stay in. Monday 00:12 is 2 + 1 + at most 2."""

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from races.fills import _close_dead, _close_quote, book_of, hours_between, try_enter

DAY = 86400
WSOL = "So11111111111111111111111111111111111111112"


def gate(token, cfg, now):
    """Return (ok, reason). `now` is an aware datetime."""
    w = cfg["wallets"]["R5"]
    if token.get("isVerified") is not True:
        return False, "unverified"
    symbol = str(token.get("symbol") or "")
    if _denied_symbol(symbol, w):
        return False, "denylist"
    tags = {str(tag).lower() for tag in (token.get("tags") or [])}
    banned = {str(tag).lower() for tag in w["reject_tags"]}
    if tags & banned:
        return False, "tag"
    try:
        mcap = float(token.get("mcap"))
        fdv = float(token.get("fdv"))
        liq = float(token.get("liquidity"))
        score = float(token.get("organicScore"))
    except (TypeError, ValueError):
        return False, "fields"
    if mcap < float(w["min_mcap_usd"]):
        return False, "mcap"
    if fdv <= 0 or mcap / fdv < float(w["min_mcap_fdv"]):
        return False, "mcap_fdv"
    if liq < float(w["min_liquidity_usd"]):
        return False, "liquidity"
    if score < float(w["min_organic_score"]):
        return False, "organic_score"
    created = ((token.get("firstPool") or {}).get("createdAt"))
    age = _age_days(created, now)
    if age is None or age < float(w["min_age_days"]):
        return False, "age"
    return True, "ok"


def signal(token, cfg):
    """24h blow-off cap is inclusive. +65 does not enter."""
    w = cfg["wallets"]["R5"]
    s24 = token.get("stats24h") or {}
    s6 = token.get("stats6h") or {}
    try:
        p24 = float(s24.get("priceChange"))
        p6 = float(s6.get("priceChange"))
        vol = float(s24.get("volumeChange"))
        buy = float(s24.get("buyVolume"))
        organic = float(s24.get("buyOrganicVolume"))
    except (TypeError, ValueError):
        return False, "signal_fields"
    if p24 < float(w["price_24h_min"]) or p24 > float(w["price_24h_max"]):
        return False, "price_24h"
    if p6 <= float(w["price_6h_gt"]):
        return False, "price_6h"
    if vol < float(w["volume_change_min"]):
        return False, "volume"
    if buy <= 0 or organic / buy < float(w["min_organic_buy_ratio"]):
        return False, "organic_buy"
    return True, "ok"


def bucket(token, cfg):
    """Choice 30: meme tag, else a meme launchpad, else untagged, else non-meme."""
    w = cfg["wallets"]["R5"]
    tags = {str(tag).lower() for tag in (token.get("tags") or [])}
    if "meme" in tags:
        return "meme"
    launch = str(token.get("launchpad") or "").strip().lower()
    meme_lp = {str(item).lower() for item in w.get("meme_launchpads") or []}
    if launch and launch in meme_lp:
        return "meme"
    if not launch:
        return "untagged"
    return "non_meme"


def on_run(ctx):
    w = ctx.cfg["wallets"]["R5"]
    st = ctx.state.setdefault("r5", {
        "week": None, "tokens": [], "below_min": True, "freeze_slot": None, "rr": 0,
    })
    book = book_of(ctx.state, "R5", ctx.cfg["starting_sol"])
    monday = ctx.slot.weekday() == 0 and ctx.slot.hour == 0 and ctx.slot.minute == 12
    if monday:
        _universe(ctx, st, w)
        fresh = _search(ctx, st, book)
        if fresh is None:
            ctx.log("R5", "decision", {"action": "search_failed", "entries": "deferred"})
        else:
            _apply_marks(book, fresh)
            _exits(ctx, st, book, fresh, w, quote_limit=2)
        ctx.log("R5", "decision", {"action": "entry_deferred", "until": "00:42"})
        _calls(ctx)
        return
    if not st.get("freeze_slot"):
        _search(ctx, st, book)
        ctx.log("R5", "decision", {"action": "cash", "reason": "no_freeze"})
        _calls(ctx)
        return
    fresh = _search(ctx, st, book)
    if fresh is None:
        for pos in book["positions"]:
            pos["mark_stale"] = True
        ctx.log("R5", "decision", {"action": "search_failed"})
        _calls(ctx)
        return
    _apply_marks(book, fresh)
    used = _exits(ctx, st, book, fresh, w, quote_limit=4)
    if st.get("below_min"):
        ctx.log("R5", "decision", {"action": "cash", "reason": "universe_below_min"})
    elif used <= 2:
        _maybe_enter(ctx, st, book, fresh, w)
    else:
        ctx.log("R5", "decision", {"action": "skip", "reason": "quote_cap"})
    _calls(ctx)


def _universe(ctx, st, w):
    rows = []
    ok = False
    for key in ("toptraded_url", "organic_url"):
        url = w[key] + "?limit=" + str(int(w["list_limit"]))
        got = ctx.jup.token_list("R5", url)
        if got:
            rows.extend(got)
            ok = True
    if not ok:
        ctx.log("R5", "decision", {"action": "freeze_kept", "reason": "universe_calls"})
        return
    by_mint = {}
    for token in rows:
        mint = token.get("id")
        if isinstance(mint, str):
            by_mint.setdefault(mint, token)
    accepted = []
    for token in by_mint.values():
        passed, _reason = gate(token, ctx.cfg, ctx.slot)
        if not passed:
            continue
        mint = token["id"]
        ctx.ledger.allow_add(mint)
        accepted.append({
            "mint": mint,
            "symbol": str(token.get("symbol") or "TOKEN")[:16],
            "bucket": bucket(token, ctx.cfg),
            "meme": bucket(token, ctx.cfg) == "meme",
            "mcap": token.get("mcap"),
            "fdv": token.get("fdv"),
            "liquidity_usd": token.get("liquidity"),
            "organic_score": token.get("organicScore"),
            "first_pool_at": (token.get("firstPool") or {}).get("createdAt"),
            "launchpad": token.get("launchpad") or "",
            "tags": [str(tag) for tag in (token.get("tags") or [])],
        })
    accepted.sort(key=lambda row: row["mint"])
    st["week"] = ctx.slot.strftime("%G-W%V")
    st["tokens"] = accepted
    st["below_min"] = len(accepted) < int(w["min_universe"])
    st["freeze_slot"] = ctx.slot_iso
    ctx.ledger.write_json("r5-universe.json", {
        "schema_version": 1,
        "frozen_at": ctx.slot_iso,
        "week": st["week"],
        "count": len(accepted),
        "below_min": st["below_min"],
        "min_tokens": int(w["min_universe"]),
        "tokens": accepted,
    })
    _crosscheck(ctx, w, accepted)
    ctx.log("R5", "decision", {"action": "universe", "n": len(accepted), "below_min": st["below_min"]})


def _search(ctx, st, book):
    mints = [row["mint"] for row in st.get("tokens") or []]
    for pos in book["positions"]:
        if pos.get("mint") not in mints:
            mints.append(pos["mint"])
    if WSOL not in mints:
        mints.append(WSOL)
    query = ",".join(mints[:100])
    url = ctx.cfg["wallets"]["R5"]["search_url"] + "?query=" + quote(query, safe=",")
    rows = ctx.jup.token_list("R5", url)
    if not rows:
        return None
    return {row.get("id"): row for row in rows if isinstance(row, dict) and row.get("id")}


def _apply_marks(book, fresh):
    sol = _sol_px(fresh)
    if sol is None:
        return
    for pos in book["positions"]:
        token = fresh.get(pos["mint"])
        if not token or token.get("usdPrice") is None:
            continue
        price = float(token["usdPrice"]) / sol
        tokens = _tokens(pos)
        pos["mark_price_sol"] = price
        pos["mark_sol"] = tokens * price
        pos["mark_stale"] = False
        if token.get("liquidity") is not None:
            pos["liquidity_usd"] = token.get("liquidity")


def _exits(ctx, st, book, fresh, w, quote_limit):
    quoted = set()
    n = 0
    for pos in list(book["positions"]):
        if n >= quote_limit:
            break
        reason = _trigger(pos, fresh, w, ctx.slot)
        if reason is None:
            continue
        if reason == "liq_floor":
            _close_dead(ctx, "R5", book, pos, "liq_floor")
            book["positions"] = [row for row in book["positions"] if row is not pos]
            continue
        stop = reason in ("stop", "trail")
        if _sell(ctx, book, pos, reason, stop):
            quoted.add(id(pos))
            n += 1
    still = [row for row in book["positions"] if id(row) not in quoted]
    if n < quote_limit and still:
        idx = int(st.get("rr") or 0) % len(still)
        pos = still[idx]
        st["rr"] = idx + 1
        _simulate(ctx, book, pos)
        n += 1
    elif still:
        st["rr"] = int(st.get("rr") or 0) + 1
    return n


def _trigger(pos, fresh, w, slot):
    token = fresh.get(pos["mint"]) if fresh else None
    liq = pos.get("liquidity_usd")
    if token and token.get("liquidity") is not None:
        liq = float(token["liquidity"])
    sol = _sol_px(fresh)
    net = None
    mark = None
    if token and token.get("usdPrice") is not None and sol:
        price = float(token["usdPrice"]) / sol
        mark = _tokens(pos) * price
        outlay = float(pos.get("entry_outlay_sol") or 0)
        if outlay:
            net = mark / outlay - 1.0
    if net is not None and net <= float(w["hard_stop"]):
        return "stop"
    if net is not None:
        peak = pos.get("peak_net")
        if peak is None or net > peak:
            pos["peak_net"] = net
            pos["peak_mark_sol"] = mark
        if pos.get("peak_net", -1) >= float(w["trail_arm"]):
            pos["trail_armed"] = True
        peak_mark = float(pos.get("peak_mark_sol") or 0)
        if pos.get("trail_armed") and peak_mark and mark <= peak_mark * (1.0 - float(w["trail_giveback"])):
            return "trail"
    if hours_between(pos["entry_slot"], slot) >= float(w["time_stop_days"]) * 24.0:
        return "time_stop"
    if liq is not None and float(liq) < float(w["liq_floor_usd"]):
        return "liq_floor"
    return None


def _simulate(ctx, book, pos):
    quote = ctx.jup.quote("R5", pos["mint"], ctx.cfg["wsol_mint"], int(pos["tokens_raw"]), "exit_quote")
    if quote is None:
        if ctx.jup.last in ("429", "timeout", "http_5xx", "cap"):
            pos["mark_stale"] = True
            return
        pos["failed_sell_quotes"] = int(pos.get("failed_sell_quotes") or 0) + 1
        pos["mark_stale"] = True
        if pos["failed_sell_quotes"] >= int(ctx.cfg["death"]["failed_sell_quotes"]):
            _close_dead(ctx, "R5", book, pos, "no_route")
            book["positions"] = [row for row in book["positions"] if row is not pos]
        return
    pos["failed_sell_quotes"] = 0
    pos["mark_stale"] = False


def _sell(ctx, book, pos, reason, stop):
    quote = ctx.jup.quote("R5", pos["mint"], ctx.cfg["wsol_mint"], int(pos["tokens_raw"]), "exit_quote")
    if quote is None:
        if ctx.jup.last in ("429", "timeout", "http_5xx", "cap"):
            pos["mark_stale"] = True
        return False
    reserve = pos.get("reserve_sol")
    _close_quote(ctx, "R5", book, pos, quote, reserve, reason, stop)
    book["positions"] = [row for row in book["positions"] if row is not pos]
    return True


def _maybe_enter(ctx, st, book, fresh, w):
    if len(book["positions"]) >= int(w["max_open"]):
        ctx.log("R5", "decision", {"action": "skip", "reason": "max_open"})
        return
    day = ctx.slot.strftime("%Y-%m-%d")
    if int((book.get("fills_by_day") or {}).get(day) or 0) >= int(w["max_entries_per_day"]):
        ctx.log("R5", "decision", {"action": "skip", "reason": "daily_cap"})
        return
    ranked = []
    for row in st.get("tokens") or []:
        token = fresh.get(row["mint"])
        if not token:
            continue
        ok, _reason = signal(token, ctx.cfg)
        if not ok:
            continue
        if _cooling(book, row["mint"], ctx.slot, w):
            continue
        if any(pos["mint"] == row["mint"] for pos in book["positions"]):
            continue
        ranked.append((float((token.get("stats24h") or {}).get("priceChange") or 0), row["mint"], row, token))
    if not ranked:
        ctx.log("R5", "decision", {"action": "no_signal"})
        return
    ranked.sort(key=lambda item: (-item[0], item[1]))
    _change, mint, row, token = ranked[0]
    try_enter(
        ctx, "R5", mint, row["symbol"], None, _reserve(ctx, mint),
        meme=(row.get("bucket") == "meme"), liquidity_usd=token.get("liquidity"),
        bucket=row.get("bucket"),
    )


def _reserve(ctx, mint):
    from races.rules.r3 import _reserve as dex_reserve
    return dex_reserve(ctx, mint)


def _calls(ctx):
    ctx.ledger.append("r5-calls.jsonl", {
        "ts": ctx.slot_iso,
        "calls": ctx.jup.budget.used.get("R5", 0),
        "cap": ctx.cfg["jupiter"]["caps"]["R5"],
        "order": [row["kind"] for row in ctx.jup.calls if row.get("wallet") == "R5"],
    })


def _crosscheck(ctx, w, accepted):
    for row in accepted[:8]:
        url = w["coingecko_contract"] + row["mint"]
        result = ctx.transport.request("GET", url, headers={"user-agent": "sightline-races", "accept": "application/json"})
        ctx.log("R5", "decision", {"action": "crosscheck", "mint": row["mint"], "ok": bool(result and result.status == 200)})


def _sol_px(fresh):
    token = (fresh or {}).get(WSOL) or {}
    try:
        px = float(token.get("usdPrice"))
    except (TypeError, ValueError):
        return None
    return px or None


def _tokens(pos):
    raw = int(pos.get("tokens_raw") or 0)
    decimals = int(pos.get("decimals") or 0)
    return raw / (10 ** decimals) if decimals else float(raw)


def _cooling(book, mint, slot, w):
    raw = (book.get("cooldowns") or {}).get(mint)
    if not raw:
        return False
    then = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    return slot < then + timedelta(days=float(w["cooldown_days"]))


def _denied_symbol(symbol, w):
    upper = symbol.upper()
    banned = {item.upper() for item in w["denylist_symbols"]}
    if upper in banned:
        return True
    return any(part.upper() in upper for part in w["symbol_substrings"])


def _age_days(created, now):
    if not created:
        return None
    try:
        ts = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (now - ts).total_seconds() / DAY
