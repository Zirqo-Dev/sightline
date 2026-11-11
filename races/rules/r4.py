"""R4 Upbit listing bounce. Off after a failed reachability check in the first two runs."""

import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from races.fills import hours_between, manage_positions, try_enter
from races.http import host_of
from races.scrub import is_b58

_MINT = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_SYMBOL = re.compile(r"\(([A-Za-z0-9]{1,12})\)")


def on_run(ctx):
    w = ctx.cfg["wallets"]["R4"]
    st = ctx.state.setdefault("r4", {
        "probes": 0, "off": False, "open_gate": False, "queue": [], "seen": [],
    })
    manage_positions(ctx, "R4", lambda pos, view: _exit(ctx, w, pos), lambda pos: pos.get("reserve_sol"))
    if st["off"]:
        ctx.log("R4", "decision", {"action": "off"})
        return
    if not st["open_gate"]:
        ok = _probe(ctx, w)
        st["probes"] += 1
        if not ok:
            st["off"] = True
            ctx.log("R4", "decision", {"action": "off", "reason": "unreachable", "probes": st["probes"]})
            return
        ctx.log("R4", "decision", {"action": "probe_ok", "probes": st["probes"]})
        if st["probes"] < 2:
            return
        st["open_gate"] = True
    _collect(ctx, w, st)
    _maybe_enter(ctx, st)


def _exit(ctx, w, pos):
    if hours_between(pos["entry_slot"], ctx.slot) >= float(w["time_stop_hours"]):
        return "time_stop", False
    return None


def _probe(ctx, w):
    url = w["list_url"] + "?" + urlencode({"os": "web", "page": 1, "per_page": 20, "category": "trade"})
    result = _get(ctx, url)
    if not result or result.status != 200 or not isinstance(result.body, dict):
        return False
    if result.body.get("success") is not True:
        return False
    notices = (result.body.get("data") or {}).get("notices")
    return isinstance(notices, list)


def _collect(ctx, w, st):
    notices = []
    for page in range(1, int(w["history_pages"]) + 1):
        url = w["list_url"] + "?" + urlencode({"os": "web", "page": page, "per_page": 20, "category": "trade"})
        result = _get(ctx, url)
        if not result or result.status != 200 or not isinstance(result.body, dict):
            ctx.log("R4", "decision", {"action": "skip", "reason": "list", "page": page})
            return
        batch = (result.body.get("data") or {}).get("notices") or []
        if not batch:
            break
        notices.extend(batch)
    markets = _markets(ctx, w)
    for notice in notices:
        nid = str(notice.get("id"))
        if nid in st["seen"]:
            continue
        title = str(notice.get("title") or "")
        if any(bad in title for bad in w["exclude_titles"]):
            continue
        if not any(good in title for good in w["include_titles"]):
            continue
        symbol = _symbol(title)
        if not symbol:
            ctx.log("R4", "decision", {"action": "skip", "reason": "symbol", "id": nid})
            st["seen"].append(nid)
            continue
        when = _announced(notice)
        if when is None:
            ctx.log("R4", "decision", {"action": "skip", "reason": "clock", "id": nid})
            st["seen"].append(nid)
            continue
        if _already_listed(notices, symbol, when, markets):
            ctx.log("R4", "decision", {"action": "skip", "reason": "not_first", "id": nid, "symbol": symbol})
            st["seen"].append(nid)
            continue
        mint = _mint(ctx, w, notice, symbol)
        if not mint:
            ctx.log("R4", "decision", {"action": "skip", "reason": "mint", "id": nid, "symbol": symbol})
            st["seen"].append(nid)
            continue
        st["seen"].append(nid)
        st["queue"].append({"id": nid, "symbol": symbol, "mint": mint, "t_ann": when.strftime("%Y-%m-%dT%H:%M:%SZ")})
        ctx.ledger.allow_add(mint)
    st["queue"].sort(key=lambda row: (row["t_ann"], row["id"]))


def _maybe_enter(ctx, st):
    book = ctx.state["wallets"].get("R4") or {}
    if book.get("positions"):
        return
    delay = timedelta(seconds=int(ctx.cfg["wallets"]["R4"]["entry_delay_seconds"]))
    for event in list(st["queue"]):
        announced = datetime.fromisoformat(event["t_ann"].replace("Z", "+00:00"))
        if ctx.slot < announced + delay:
            continue
        if event["mint"] in (book.get("entered") or []):
            st["queue"].remove(event)
            ctx.log("R4", "decision", {"action": "skip", "reason": "no_reentry", "mint": event["mint"]})
            continue
        reserve = None
        ok = try_enter(ctx, "R4", event["mint"], event["symbol"], None, reserve)
        if ok or event["mint"] in (ctx.state["wallets"]["R4"].get("entered") or []):
            st["queue"].remove(event)
        return


def _symbol(title):
    found = _SYMBOL.findall(title)
    return found[-1].upper() if found else None


def _announced(notice):
    raw = notice.get("first_listed_at") or ""
    if not raw:
        return None
    if raw.endswith("Z") or "+" in raw[10:] or "-" in raw[10:]:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            return None
    try:
        local = datetime.fromisoformat(raw).replace(tzinfo=timezone(timedelta(hours=9)))
    except ValueError:
        return None
    return local.astimezone(timezone.utc)


def _already_listed(notices, symbol, when, markets):
    for notice in notices:
        title = str(notice.get("title") or "")
        if symbol not in title.upper() and f"({symbol})" not in title:
            continue
        earlier = _announced(notice)
        if earlier and earlier < when:
            return True
    if markets is None:
        return True
    for prefix in ("KRW-", "BTC-", "USDT-"):
        row = markets.get(prefix + symbol)
        if row and row < when:
            return True
    return False


def _markets(ctx, w):
    result = _get(ctx, w["markets_url"])
    if not result or result.status != 200 or not isinstance(result.body, list):
        return None
    out = {}
    for row in result.body:
        if not isinstance(row, dict):
            continue
        market = row.get("market")
        listed = row.get("listed_at") or row.get("first_listed_at")
        if market and listed:
            try:
                out[market] = datetime.fromisoformat(str(listed).replace("Z", "+00:00"))
            except ValueError:
                continue
    return out


def _mint(ctx, w, notice, symbol):
    detail = _get(ctx, w["detail_url"] + str(notice.get("id")) + "?os=web")
    body = ""
    network = ""
    if detail and detail.status == 200 and isinstance(detail.body, dict):
        data = detail.body.get("data") or detail.body
        body = str(data.get("body") or data.get("content") or "")
        network = str(data.get("network") or "")
    wallet = _get(ctx, w["wallet_url"])
    solana = False
    if "solana" in network.lower() or "솔라나" in network.lower() or network.upper() == "SOL":
        solana = True
    if wallet and wallet.status == 200 and isinstance(wallet.body, list):
        for row in wallet.body:
            if not isinstance(row, dict):
                continue
            if str(row.get("currency") or "").upper() != symbol:
                continue
            net = str(row.get("net_type") or "")
            if "SOL" in net.upper() or "SOLANA" in net.upper():
                solana = True
    if not solana and "solana" not in body.lower() and "솔라나" not in body.lower():
        return None
    found = [m for m in _MINT.findall(body) if is_b58(m)]
    unique = sorted(set(found))
    if len(unique) == 1:
        return unique[0]
    return None


def _get(ctx, url):
    host = host_of(url)
    if host not in ("api-manager.upbit.com", "api.upbit.com"):
        return None
    return ctx.transport.request("GET", url, headers={"user-agent": "sightline-races", "accept": "application/json"})
