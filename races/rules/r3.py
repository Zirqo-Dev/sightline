"""R3 basket. Names are proved at the first runs, then filled inside the 7-call cap.

Rank quotes happen before buys. A run that buys skips marks.
"""

import io
import zipfile
from datetime import datetime, timezone
from urllib.parse import urlencode

from races.fills import manage_positions, try_enter
from races.http import host_of
from races.impact import buy_impact_bps

ASOF = "2026-11-11T23:59:59Z"
LISTING_LIMIT = "2025-11-11T23:59:59Z"


def on_run(ctx):
    w = ctx.cfg["wallets"]["R3"]
    st = ctx.state.setdefault("r3", {
        "phase": "scan",
        "page": 1,
        "seen": [],
        "roster": [],
        "ranked": {},
        "selected": [],
        "filled": [],
        "rebalance_month": None,
        "rebalance_left": [],
    })
    if st["phase"] == "off":
        ctx.log("R3", "decision", {"action": "off"})
        return
    if st["phase"] == "scan":
        _scan(ctx, st, w)
        return
    if st["phase"] == "rank":
        ctx.skip_marks.add("R3")
        _rank(ctx, st, w)
        return
    if st["phase"] == "fill":
        ctx.skip_marks.add("R3")
        _fill(ctx, st)
        return
    if _rebalance_due(ctx, st, w):
        ctx.log("R3", "decision", {"action": "rebalance", "late": st.get("rebalance_month") != ctx.slot.strftime("%Y-%m")})
        st["phase"] = "rank"
        st["ranked"] = {}
        ctx.skip_marks.add("R3")
        _rank(ctx, st, w)
        return
    manage_positions(ctx, "R3", lambda pos, view: None, lambda pos: pos.get("reserve_sol"))


def _rebalance_due(ctx, st, w):
    if ctx.slot.day != int(w["rebalance_day"]):
        return False
    if ctx.slot.hour == 0 and ctx.slot.minute < 12:
        return False
    month = ctx.slot.strftime("%Y-%m")
    return st.get("rebalance_month") != month and st["phase"] == "live"


def _scan(ctx, st, w):
    page = int(st["page"])
    url = w["coingecko_markets"] + "?" + urlencode({
        "vs_currency": "usd", "category": "meme-token", "per_page": 250, "page": page,
    })
    result = _get(ctx, url, "api.coingecko.com")
    rows = result.body if result and result.status == 200 and isinstance(result.body, list) else None
    if rows is None:
        ctx.log("R3", "decision", {"action": "scan_wait", "reason": "coingecko"})
        return
    if not rows:
        _finish_scan(ctx, st)
        return
    details = 0
    for row in rows:
        cid = row.get("id")
        if not cid or cid in st["seen"]:
            continue
        if details >= int(w["details_per_run"]):
            break
        st["seen"].append(cid)
        details += 1
        proved = _prove(ctx, w, cid, row.get("symbol"))
        if proved:
            st["roster"].append(proved)
            ctx.ledger.allow_add(proved["mint"])
    if details < int(w["details_per_run"]):
        st["page"] = page + 1
        if not rows or len(rows) < 250:
            _finish_scan(ctx, st)
    ctx.log("R3", "decision", {"action": "scan", "roster": len(st["roster"]), "page": st["page"]})


def _finish_scan(ctx, st):
    if not st["roster"]:
        st["phase"] = "off"
        ctx.log("R3", "decision", {"action": "off", "reason": "empty_roster"})
        return
    st["phase"] = "rank"
    ctx.ledger.write_json("r3-roster.json", {
        "schema_version": 1,
        "asof_rule": ASOF,
        "roster": st["roster"],
    })
    ctx.log("R3", "decision", {"action": "roster", "n": len(st["roster"])})


def _prove(ctx, w, cid, symbol):
    detail = _get(ctx, w["coingecko_coin"] + cid, "api.coingecko.com")
    if not detail or detail.status != 200 or not isinstance(detail.body, dict):
        return None
    mint = ((detail.body.get("platforms") or {}).get("solana")) or ""
    if not mint:
        return None
    sym = _binance_symbol(detail.body, symbol)
    listed, source, why = _listing(ctx, w, sym)
    if listed is None:
        ctx.log("R3", "decision", {"action": "excluded", "reason": why or "listing_date", "symbol": sym, "mint": mint})
        return None
    limit = datetime.fromisoformat(LISTING_LIMIT.replace("Z", "+00:00")).timestamp() * 1000
    if listed > limit:
        ctx.log("R3", "decision", {"action": "excluded", "reason": "listing_too_new", "symbol": sym, "mint": mint})
        return None
    return {
        "mint": mint,
        "symbol": (detail.body.get("symbol") or symbol or "TOKEN").upper(),
        "listed_on": datetime.fromtimestamp(listed / 1000, timezone.utc).strftime("%Y-%m-%d"),
        "source": source,
        "binance_symbol": sym,
    }


def _binance_symbol(detail, fallback):
    for ticker in detail.get("tickers") or []:
        market = ticker.get("market") or {}
        name = market.get("name") if isinstance(market, dict) else market
        if name != "Binance":
            continue
        if ticker.get("target") != "USDT":
            continue
        base = ticker.get("base")
        if base:
            return str(base).upper() + "USDT"
    return (str(fallback or "").upper() or "UNKNOWN") + "USDT"


def _listing(ctx, w, symbol):
    """Return (open_time_ms, source, fail_reason). A 451 is not a pass."""
    url = w["binance_klines"] + "?" + urlencode({
        "symbol": symbol, "interval": "1d", "limit": 1, "startTime": 0,
    })
    result = _get(ctx, url, "api.binance.com")
    if result and result.status == 200 and isinstance(result.body, list) and result.body:
        try:
            return _as_ms(result.body[0][0]), "binance_klines", None
        except (TypeError, ValueError, IndexError):
            return None, None, "listing_date"
    if result is None or result.status in (451, 403) or result.temporary:
        return _vision(ctx, w, symbol)
    return None, None, "listing_date"


def _vision(ctx, w, symbol):
    prefix = f"data/spot/monthly/klines/{symbol}/1d/"
    list_base = w.get("binance_vision_list") or "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
    list_url = list_base.rstrip("/") + "?" + urlencode({"prefix": prefix})
    listed = _get(ctx, list_url, "s3-ap-northeast-1.amazonaws.com")
    if not listed or listed.status != 200:
        return None, None, "vision_list"
    text = listed.body if isinstance(listed.body, str) else ""
    if _is_html(text):
        return None, None, "vision_html"
    keys = []
    for part in text.split("<Key>")[1:]:
        key = part.split("</Key>", 1)[0].strip()
        if key.startswith(prefix) and key.endswith(".zip"):
            keys.append(key)
    if not keys:
        return None, None, "vision_empty"
    key = sorted(keys)[0]
    blob = _get(ctx, w["binance_vision"].rstrip("/") + "/" + key, "data.binance.vision")
    raw = blob.raw if blob is not None else b""
    if not blob or blob.status != 200 or not raw:
        return None, None, "vision_zip"
    opened = _zip_open_ms(raw)
    if opened is None:
        return None, None, "vision_zip"
    return opened, "binance_vision", None


def _is_html(text):
    head = text.lstrip()[:300].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or "<html" in head


def _zip_open_ms(raw):
    if not isinstance(raw, (bytes, bytearray)):
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = sorted(n for n in zf.namelist() if n.endswith(".csv"))
            if not names:
                return None
            line = zf.open(names[0]).readline().decode("utf-8", errors="replace").strip()
    except zipfile.BadZipFile:
        return None
    if not line:
        return None
    try:
        return _as_ms(line.split(",")[0])
    except ValueError:
        return None


def _as_ms(value):
    """Binance spot files switched to microseconds on 2025-01-01."""
    value = int(float(value))
    if value > 10**14:
        value //= 1000
    return value


def _rank(ctx, st, w):
    calls_left = ctx.cfg["jupiter"]["caps"]["R3"] - ctx.jup.budget.used.get("R3", 0)
    for row in st["roster"]:
        if calls_left < 2:
            break
        if row["mint"] in st["ranked"]:
            continue
        reserve = _reserve(ctx, row["mint"])
        quote = ctx.jup.quote("R3", ctx.cfg["wsol_mint"], row["mint"], int(ctx.cfg["ticket_lamports"]))
        calls_left = ctx.cfg["jupiter"]["caps"]["R3"] - ctx.jup.budget.used.get("R3", 0)
        if quote is None:
            st["ranked"][row["mint"]] = {"two_way": False, "impact": None, "source": "no_route"}
            continue
        sell = ctx.jup.quote("R3", row["mint"], ctx.cfg["wsol_mint"], quote.out_raw)
        calls_left = ctx.cfg["jupiter"]["caps"]["R3"] - ctx.jup.budget.used.get("R3", 0)
        impact = buy_impact_bps(float(ctx.cfg["ticket_sol"]), reserve)
        if impact is None and quote.impact_pct is not None:
            try:
                impact = float(quote.impact_pct) * 10000.0
                source = "jup_pct"
            except (TypeError, ValueError):
                impact = None
                source = "none"
        else:
            source = "cp" if impact is not None else "none"
        st["ranked"][row["mint"]] = {
            "two_way": sell is not None,
            "impact": impact,
            "source": source,
            "reserve_sol": reserve,
            "symbol": row["symbol"],
        }
        ctx.log("R3", "decision", {
            "action": "rank_quote", "mint": row["mint"], "two_way": sell is not None,
            "rank_impact_source": source, "impact_bps": impact,
        })
    if all(row["mint"] in st["ranked"] for row in st["roster"]):
        ranked = [
            (st["ranked"][row["mint"]].get("impact") if st["ranked"][row["mint"]].get("impact") is not None else 1e18,
             row["mint"], row)
            for row in st["roster"]
            if st["ranked"][row["mint"]].get("two_way")
        ]
        ranked.sort()
        st["selected"] = [row for _impact, _mint, row in ranked[:int(w["basket"])]]
        st["phase"] = "fill" if st["selected"] else "off"
        if not st["selected"]:
            ctx.log("R3", "decision", {"action": "off", "reason": "no_route"})
        else:
            ctx.log("R3", "decision", {"action": "selected", "n": len(st["selected"])})


def _fill(ctx, st):
    for row in list(st["selected"]):
        if row["mint"] in st["filled"]:
            continue
        if ctx.cfg["jupiter"]["caps"]["R3"] - ctx.jup.budget.used.get("R3", 0) < 2:
            ctx.log("R3", "decision", {"action": "fill_deferred", "mint": row["mint"]})
            return
        info = st["ranked"].get(row["mint"]) or {}
        ctx.log("R3", "decision", {"action": "first_run_spread", "mint": row["mint"]})
        ok = try_enter(ctx, "R3", row["mint"], row["symbol"], None, info.get("reserve_sol"))
        st["filled"].append(row["mint"])
        if not ok:
            ctx.log("R3", "decision", {"action": "fill_skip", "mint": row["mint"]})
    if all(row["mint"] in st["filled"] for row in st["selected"]):
        st["phase"] = "live"
        st["rebalance_month"] = ctx.slot.strftime("%Y-%m")
        ctx.log("R3", "decision", {"action": "live"})


def _reserve(ctx, mint):
    url = ctx.cfg["discovery"]["dex_token_url"] + mint
    result = _get(ctx, url, "api.dexscreener.com")
    pairs = []
    if result and result.status == 200 and isinstance(result.body, dict):
        pairs = result.body.get("pairs") or []
    wsol = ctx.cfg["wsol_mint"]
    best = None
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        quote = (pair.get("quoteToken") or {}).get("address")
        base = (pair.get("baseToken") or {}).get("address")
        liq = pair.get("liquidity") or {}
        sol = None
        if quote == wsol:
            sol = liq.get("quote")
        elif base == wsol:
            sol = liq.get("base")
        try:
            sol = float(sol)
        except (TypeError, ValueError):
            continue
        if best is None or sol > best:
            best = sol
    return best


def _get(ctx, url, host):
    if host_of(url) != host:
        return None
    return ctx.transport.request("GET", url, headers={"user-agent": "sightline-races", "accept": "application/json"})
