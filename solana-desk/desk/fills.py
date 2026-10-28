"""Paper fill step (plan v6.5). Zero AI, paper only: no wallet, no keys, no signing, no real orders.

Each run (after the scan):
  1. mark every open position in books F, R, S with a Jupiter SELL quote for the tokens it holds,
     and apply exits.check (hard stop, +40/+100 ladder, 25% trail, 120-min time stop, liquidity pull,
     stale on temporary errors, -100% after 3 no-route checks in a row);
  2. unless the run is voided (data failures > 10% today, or the loop was out > 2h), open new fixed
     0.5 SOL paper entries chosen by books.select_fills, priced from the scan's Jupiter BUY quote;
  3. persist positions per book in positions.json, append trades.csv rows, log would_halt, write paper.json.
Prices are lamports per raw token unit, so returns are ratios and need no token decimals.
Costs: Jupiter quote impact is inside both quotes; plus haircut_per_side on each side and network_fee_sol per tx.
"""
import json, os, time
from . import config as C, books, exits, ledger

STATE = "positions.json"
LAMPORTS = 1e9
EXCLUDED_SAFETY = {"not_graduated", "missing_data"}  # out of scope / no execution check ran: never fill (S or R)
CLOSED_KEEP = 200

def iso(ts): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def empty_book():
    return dict(cash=C.START_BALANCE_SOL, open={}, fills_day="", fills_today=0, realized_pnl=0.0,
                closed=[], would_halt=[], peak_equity=C.START_BALANCE_SOL)

def load(d):
    try: s = json.load(open(os.path.join(d, STATE)))
    except Exception: s = {}
    s.setdefault("cfg_version", C.CFG_VERSION); s.setdefault("last_run_ts", None); s.setdefault("voids", [])
    s.setdefault("books", {}); [s["books"].setdefault(b, empty_book()) for b in "FRS"]
    return s

def save(d, s):
    p = os.path.join(d, STATE); tmp = p + ".tmp"
    with open(tmp, "w") as f: json.dump(s, f, indent=1)
    os.replace(tmp, p)  # atomic: a killed run never leaves a half-written book

def basis(): h = C.COST["haircut_per_side"]; return C.TICKET_SOL * (1 + h) + C.COST["network_fee_sol"]

def proceeds(pos, frac, px):
    h = C.COST["haircut_per_side"]
    gross = C.TICKET_SOL * frac * (px / pos["entry"]) if pos["entry"] else 0.0
    return max(0.0, gross * (1 - h) - C.COST["network_fee_sol"]) if px > 0 else 0.0

def equity(b):
    return b["cash"] + sum(proceeds(p, p["frac"], p["last_mark"]) for p in b["open"].values())

def _trade(d, now, book, pos, action, frac, sol, px, why, pnl="", delay=0.0, stale=0, impact=""):
    ledger.trade(d, dict(time=int(now), book=book, agent="EXECUTE" if action == "buy" else "EXIT", mint=pos["mint"],
                         symbol=pos.get("symbol", ""), action=action, frac=round(frac, 6), sol=round(sol, 9),
                         quote_px=px, impact_pct=impact, fee_sol=C.COST["network_fee_sol"],
                         haircut_pct=C.COST["haircut_per_side"] * 100, why=why,
                         pnl=pnl if pnl == "" else round(pnl, 9), check_delay_s=round(delay, 2), stale=stale))

def mark_status(sell_quote, pos):
    """-> (status, mark_px). Jupiter {} = no route; exception = temporary error (stale, retry next run)."""
    amt = int(pos["tokens"] * pos["frac"])
    if amt <= 0: return "ok", pos["last_mark"]
    try: q = sell_quote(pos["mint"], amt)
    except Exception: return "temp_error", None
    if not q or not q.get("routePlan") or not q.get("outAmount"): return "no_route", None
    return "ok", int(q["outAmount"]) / amt

def close(b, pid, pos, now, why, pnl_total):
    b["open"].pop(pid, None)
    b["closed"].append(dict(id=pid, mint=pos["mint"], symbol=pos.get("symbol", ""), opened=pos["opened"], closed=int(now),
                            entry=pos["entry"], exit_px=pos["last_mark"], why=why, pnl=round(pnl_total, 9),
                            pct=round(pnl_total / basis() * 100, 2), why_entered=pos.get("why", "")))
    b["closed"] = b["closed"][-CLOSED_KEEP:]

def manage(d, s, now, sell_quote, liquidity, sleep):
    n = 0
    for name in "FRS":
        b = s["books"][name]
        for pid, pos in list(b["open"].items()):
            if n: sleep(1.2)
            n += 1; t0 = time.time()
            status, px = mark_status(sell_quote, pos)
            liq = None
            if status == "ok":
                try: liq = liquidity(pos["mint"])
                except Exception: liq = None
            acts = exits.check(pos, px, liq, now, status)
            delay = time.time() - t0
            for action, frac, why, apx in acts:
                if action == "mark":
                    _trade(d, now, name, pos, "mark", 0, 0, pos["last_mark"], why, delay=delay, stale=1); continue
                pro = proceeds(pos, frac, apx); pnl = pro - basis() * frac
                b["cash"] += pro; b["realized_pnl"] += pnl; pos["pnl"] = pos.get("pnl", 0.0) + pnl
                if apx == 0.0: pos["last_mark"] = 0.0
                _trade(d, now, name, pos, "sell", frac, pro, apx, why, pnl=pnl, delay=delay, stale=pos.get("stale", 0))
                full = not why.startswith("take_profit")
                if full: pos["frac"] = 0.0
            if pos["frac"] <= 1e-9:
                close(b, pid, pos, now, acts[-1][2] if acts else "closed", pos.get("pnl", 0.0))

def void_reasons(s, st, now):
    out = []
    if s["last_run_ts"] and now - s["last_run_ts"] > C.OUTAGE_VOID_H * 3600: out.append("outage_over_2h")
    if st.get("checks") and st.get("data_failures", 0) / st["checks"] > C.DATA_FAIL_VOID: out.append("data_failures_over_10pct")
    return out

def enter(d, s, now, run_id, seed, leads, day):
    """leads: list of dicts mint, stype, reason, quote, pair (from this run's scan, in scan order)."""
    shim = {}
    for name in "FRS":
        b = s["books"][name]
        if b["fills_day"] != day: b["fills_day"], b["fills_today"] = day, 0
        k = books.Book(name); k.open = dict(b["open"]); k.fills_today = b["fills_today"]; shim[name] = k
    usable = [l for l in leads if l["stype"] is None or (l["stype"] == "safety" and l["reason"] not in EXCLUDED_SAFETY)]
    usable = [l for l in usable if (l.get("quote") or {}).get("outAmount")]
    by = {l["mint"]: l for l in usable}
    picks = books.select_fills([(l["mint"], l["stype"], l["reason"]) for l in usable], shim, seed)
    opened = 0
    for name in "FRS":
        b = s["books"][name]; held = {p["mint"] for p in b["open"].values()}
        for m in picks[name]:
            if m in held or b["cash"] < basis(): continue
            l = by[m]; tokens = int(l["quote"]["outAmount"])
            if tokens <= 0: continue
            px = C.TICKET_SOL * LAMPORTS / tokens
            liq0 = ((l.get("pair") or {}).get("liquidity") or {}).get("quote")
            pos = exits.new_position(px, liq0, int(now))
            why = {"F": "pass", "R": "random_match", "S": "safety:" + (l["reason"] or "")}[name]
            pos.update(mint=m, symbol=((l.get("pair") or {}).get("baseToken") or {}).get("symbol", ""), tokens=tokens,
                       run_id=run_id, why=why, book=name, pnl=0.0)
            pid = f"{name}-{run_id}-{m[:4]}{m[-4:]}"
            b["open"][pid] = pos; b["cash"] -= basis(); b["fills_today"] += 1; held.add(m); opened += 1
            imp = round(float(l["quote"].get("priceImpactPct", 0) or 0) * 100, 4)
            _trade(d, now, name, pos, "buy", 1, C.TICKET_SOL, px, why, impact=imp)
    return opened

def would_halt(s, now, day):
    for name in "FRS":
        b = s["books"][name]; eq = equity(b); dd = 1 - eq / C.START_BALANCE_SOL
        b["peak_equity"] = max(b["peak_equity"], eq)
        if dd >= C.SESSION_LOSS_HALT_PCT and not any(w["day"] == day for w in b["would_halt"]):
            b["would_halt"].append(dict(day=day, ts=iso(now), book=name, drawdown=round(dd, 4)))  # logged only, never blocks

def ticket(pid, p, closed=False):
    t = dict(id=pid, ticker=p.get("symbol") or (p["mint"][:4] + "…" + p["mint"][-4:]), mint=p["mint"], book=p.get("book", pid[0]),
             status="closed" if closed else ("stale" if p.get("stale") else "open"), size=C.TICKET_SOL)
    if closed:
        t.update(opened_at=iso(p["opened"]), closed_at=iso(p["closed"]), entry=dict(px=p["entry"]), exit=dict(px=p["exit_px"]),
                 exit_reason=p["why"], result=dict(pnl=p["pnl"], pct=p["pct"], unit="SOL"), thesis=p.get("why_entered", ""))
    else:
        r = p["last_mark"] / p["entry"] - 1 if p["entry"] else 0
        t.update(opened_at=iso(p["opened"]), window_ends_at=iso(p["opened"] + C.EXIT["time_stop_min"] * 60),
                 entry=dict(px=p["entry"]), frac=round(p["frac"], 6), last_mark=p["last_mark"], thesis=p.get("why", ""),
                 result=dict(pnl=round(p.get("pnl", 0.0), 9), pct=round(r * 100, 2), unit="SOL"))
    return t

def paper_json(d, s, now):
    opn = [ticket(pid, p) for n in "FRS" for pid, p in s["books"][n]["open"].items()]
    cls = [ticket(c["id"], dict(c, book=n), closed=True) for n in "FRS" for c in s["books"][n]["closed"]]
    E = C.EXIT
    out = dict(schema_version=2, desk="solana-desk", book="F,R,S", chain="solana", unit="SOL",
               disclaimer="Paper trading only. No real orders. Research only, not advice.",
               ticket_size=C.TICKET_SOL, default_window_hours=E["time_stop_min"] / 60, generated_at=iso(now),
               cfg_version=C.CFG_VERSION, open=opn, closed=cls,
               books={n: dict(cash=round(b["cash"], 9), equity=round(equity(b), 9), realized_pnl=round(b["realized_pnl"], 9),
                              open=len(b["open"]), closed=len(b["closed"]), fills_today=b["fills_today"], would_halt=b["would_halt"][-5:])
                      for n, b in s["books"].items()},
               rules=[dict(id="entry", rule=f"Fixed {C.TICKET_SOL} SOL paper entry from a Jupiter quote; F max {C.MAX_OPEN_F} open, {C.MAX_NEW_F_PER_DAY} new/day; R and S capped at {C.R_MATCH_CAP}x F fills"),
                      dict(id="exit", rule=f"Stop {int(E['hard_stop']*100)}%, take +40%/+100% (1/3 each), trail {int(E['trail']*100)}% after first take, time stop {E['time_stop_min']} min unless up {int(E['time_stop_keep_if_up']*100)}%, liquidity pull {int(E['liq_drop']*100)}%, -100% after {E['no_route_kill_checks']} no-route checks"),
                      dict(id="void", rule=f"No new entries when data failures > {int(C.DATA_FAIL_VOID*100)}% today or after a loop outage > {C.OUTAGE_VOID_H}h")],
               summaries=[dict(at=v["ts"], type="void", note=", ".join(v["why"])) for v in s["voids"][-10:]] +
                         [dict(at=w["ts"], type="would_halt", note=f"{w['book']} drawdown {w['drawdown']*100:.1f}%") for n in "FRS" for w in s["books"][n]["would_halt"][-3:]])
    with open(os.path.join(d, "paper.json"), "w") as f: json.dump(out, f, indent=1)
    return out

def step(d, now, run_id, seed, leads, st, sell_quote, liquidity, sleep=lambda s: None):
    """One fill-mode run. Returns a small summary for health.json."""
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    s = load(d)
    manage(d, s, now, sell_quote, liquidity, sleep)
    v = void_reasons(s, st, now)
    opened = 0
    if v: s["voids"].append(dict(ts=iso(now), run_id=run_id, why=v)); s["voids"] = s["voids"][-100:]
    else: opened = enter(d, s, now, run_id, seed, leads, day)
    would_halt(s, now, day)
    s["last_run_ts"] = int(now); s["cfg_version"] = C.CFG_VERSION
    save(d, s); paper_json(d, s, now)
    return dict(opened=opened, void=v, open={n: len(s["books"][n]["open"]) for n in "FRS"},
                equity={n: round(equity(s["books"][n]), 4) for n in "FRS"})
