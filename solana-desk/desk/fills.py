"""Paper fill step (plan v6.5). Zero AI, paper only: no wallet, no keys, no signing, no real orders.

Each run (after the scan):
  1. mark every open position in books F, R, S with a Jupiter SELL quote for the tokens it holds,
     and apply exits.check (hard stop, +40/+100 ladder, 25% trail, 120-min time stop, liquidity pull,
     stale on temporary errors, -100% after 3 no-route checks in a row);
  2. unless the run is voided (data failures > 10% today, or the loop was out > 2h), open new fixed
     0.5 SOL paper entries chosen by books.select_fills, priced from the scan's Jupiter BUY quote;
  3. persist positions per book in positions.json, append trades.csv rows, log would_halt, write paper.json.
Prices are lamports per raw token unit, so returns are ratios and need no token decimals.
Costs: the Jupiter outAmount already includes the pool's curve move. On top of that, each side
pays a slippage+MEV haircut (config.COST, an assumption; see COST_NOTE): base_bps plus
impact_weight times the verified constant-product impact of this fill's SOL into the pool,
plus stress_exit_bps on hard_stop, trailing_stop and liquidity_pulled, capped at max_bps.
Jupiter priceImpactPct is logged and is not an input. Exits still use the raw quote price.
Gross and net PnL are both recorded; pnl stays the net figure. New rows carry cost_model
v3-exit-aware and the time that model started. Older rows are not rewritten.
desk.observe adds reserve, migration, quote, and held-price fields. Those fields are
logs. They are not read by exits, books, or the fill price. log=False skips them.
"""
import csv, json, os, time
from . import config as C, books, exits, ledger, observe

STATE = "positions.json"
LAMPORTS = 1e9
EXCLUDED_SAFETY = {"not_graduated", "missing_data"}  # out of scope / no execution check ran: never fill (S or R)
CLOSED_KEEP = 200

def iso(ts): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

def empty_book():
    return dict(cash=C.START_BALANCE_SOL, open={}, fills_day="", fills_today=0, realized_pnl=0.0,
                realized_gross=0.0, closed=[], would_halt=[], peak_equity=C.START_BALANCE_SOL)

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

# Position keys the fill and the exit rule own. A log dict that repeats one of these is dropped.
_DECISION = {
    "entry", "peak", "steps", "liq0", "opened", "frac", "last_mark", "stale", "no_route_count",
    "mint", "symbol", "tokens", "run_id", "why", "book", "pnl", "gross_pnl",
    "entry_haircut", "entry_cost", "cost_model", "cost_model_started_at", "exit_haircut",
}

def sol_liq(snap):
    """SOL-side number exits.check already uses.
    A non-dict is the historical liquidity callback (a float or None) and is returned unchanged.
    A pair uses ((pair or {}).get("liquidity") or {}).get("quote"), including a falsy liquidity object.
    """
    if not isinstance(snap, dict):
        return snap
    return ((snap or {}).get("liquidity") or {}).get("quote")

def _apply_log(pos, fields):
    """Write logging fields. Decision keys already on the position stay as they are."""
    if not isinstance(fields, dict):
        return
    for k, v in fields.items():
        if k in _DECISION:
            continue
        pos[k] = v

def _f(x):
    try:
        if x is None or x == "": return None
        return float(x)
    except (TypeError, ValueError):
        return None

def impact_bps(quote):
    """Jupiter priceImpactPct is a fraction (0.01 = 1% = 100 bps). Logged only. Not the haircut input."""
    if not isinstance(quote, dict): return None
    v = _f(quote.get("priceImpactPct")) if "priceImpactPct" in quote else None
    if v is None or v != v: return None  # missing or NaN: do not invent an impact term
    return abs(v) * 10000.0

def verified_impact_bps(pool_sol, dx_sol=None):
    """Constant-product impact of selling or buying dx_sol into a pool with pool_sol on the SOL side.
    dx/(pool+dx), in bps. None when either side is missing or not positive. This is the figure the
    haircut trusts: quoted outAmount fits this curve, and Jupiter priceImpactPct does not."""
    x = _f(pool_sol)
    dx = C.TICKET_SOL if dx_sol is None else _f(dx_sol)
    if x is None or x != x or x <= 0 or dx is None or dx != dx or dx <= 0: return None
    return 10000.0 * dx / (x + dx)

def haircut_frac(pool_sol=None, impact_bps_v=None, exit_reason=None, dx_sol=None):
    """One side's slippage+MEV fraction. See config.COST_NOTE.
    impact_bps_v is the verified curve impact. None derives it from pool_sol and dx_sol.
    stress_exit_bps is added only for the named exit reasons. Never above max_bps or below min_bps."""
    c = C.COST
    if impact_bps_v is None: impact_bps_v = verified_impact_bps(pool_sol, dx_sol)
    raw = float(c["base_bps"])
    if impact_bps_v is not None: raw += float(c["impact_weight"]) * float(impact_bps_v)
    if exit_reason in c["stress_exit_reasons"]: raw += float(c["stress_exit_bps"])
    bps = min(float(c["max_bps"]), max(float(c["min_bps"]), raw))
    return bps / 10000.0

def _pct(frac):
    return "" if frac == "" or frac is None else round(float(frac) * 100, 4)

def entry_cost(pool_sol=None, quote=None):
    """SOL reserved at entry: ticket worsened by the entry haircut, plus one network fee.
    quote is ignored for the haircut. priceImpactPct stays on the trade row, not in this cost."""
    h = haircut_frac(pool_sol, verified_impact_bps(pool_sol, C.TICKET_SOL))
    return C.TICKET_SOL * (1 + h) + C.COST["network_fee_sol"], h

def legacy_entry_cost():
    h = C.COST["legacy_haircut_per_side"]
    return C.TICKET_SOL * (1 + h) + C.COST["network_fee_sol"]

def entry_cost_of(pos):
    """What this position actually reserved. Legacy rows (no entry_cost) keep the old 0.5% entry."""
    v = pos.get("entry_cost")
    return legacy_entry_cost() if v is None else float(v)

def gross_proceeds(pos, frac, px):
    if not pos.get("entry") or px <= 0 or frac <= 0: return 0.0
    return C.TICKET_SOL * frac * (px / pos["entry"])

def net_proceeds(pos, frac, px, h_exit):
    if px <= 0 or frac <= 0: return 0.0
    return max(0.0, gross_proceeds(pos, frac, px) * (1 - h_exit) - C.COST["network_fee_sol"])

def slice_pnl(pos, frac, px, h_exit):
    """(gross, net) SOL for selling `frac` at quote px. Gross is the price move; net subtracts both haircuts and fees."""
    gross = gross_proceeds(pos, frac, px) - C.TICKET_SOL * frac
    net = net_proceeds(pos, frac, px, h_exit) - entry_cost_of(pos) * frac
    return gross, net

def exit_haircut_of(pos):
    if pos.get("exit_haircut") is not None: return float(pos["exit_haircut"])
    return haircut_frac(pos.get("liq0"), verified_impact_bps(pos.get("liq0"), C.TICKET_SOL))

def equity(b):
    return b["cash"] + sum(net_proceeds(p, p["frac"], p["last_mark"], exit_haircut_of(p)) for p in b["open"].values())

def ensure_model(s, now):
    """First new fill fixes the model's start time. Later fills copy it. Past rows are not touched."""
    if not s.get("cost_model_started_at"): s["cost_model_started_at"] = iso(now)
    s["cost_model"] = C.COST_MODEL
    return C.COST_MODEL, s["cost_model_started_at"]

def _bps(v):
    return "" if v is None or v == "" else round(float(v), 4)

def _trade(d, now, book, pos, action, frac, sol, px, why, pnl="", delay=0.0, stale=0, impact="",
           gross="", net="", entry_h="", exit_h="", model=("", ""), verified=""):
    side = exit_h if action == "sell" else entry_h
    name, started = model if model else ("", "")
    ledger.trade(d, dict(time=int(now), book=book, agent="EXECUTE" if action == "buy" else "EXIT", mint=pos["mint"],
                         symbol=pos.get("symbol", ""), action=action, frac=round(frac, 6), sol=round(sol, 9),
                         quote_px=px, impact_pct=impact, fee_sol=C.COST["network_fee_sol"],
                         haircut_pct=_pct(side), why=why,
                         pnl=pnl if pnl == "" else round(pnl, 9), check_delay_s=round(delay, 2), stale=stale,
                         gross_pnl="" if gross == "" else round(gross, 9),
                         net_pnl="" if net == "" else round(net, 9),
                         entry_haircut_pct=_pct(entry_h), exit_haircut_pct=_pct(exit_h),
                         verified_impact_bps=_bps(verified), cost_model=name, cost_model_started_at=started))

def mark_status(sell_quote, pos):
    """-> (status, mark_px, quote|None). Jupiter {} = no route; exception = temporary error (stale, retry next run)."""
    amt = int(pos["tokens"] * pos["frac"])
    if amt <= 0: return "ok", pos["last_mark"], None
    try: q = sell_quote(pos["mint"], amt)
    except Exception: return "temp_error", None, None
    if not q or not q.get("routePlan") or not q.get("outAmount"): return "no_route", None, None
    return "ok", int(q["outAmount"]) / amt, q

def close(b, pid, pos, now, why, pnl_total, gross_total):
    b["open"].pop(pid, None)
    cost = entry_cost_of(pos)
    rec = dict(id=pid, mint=pos["mint"], symbol=pos.get("symbol", ""), opened=pos["opened"], closed=int(now),
               entry=pos["entry"], exit_px=pos["last_mark"], why=why, pnl=round(pnl_total, 9),
               gross_pnl=round(gross_total, 9),
               pct=round(pnl_total / cost * 100, 2) if cost else 0.0,
               gross_pct=round(gross_total / C.TICKET_SOL * 100, 2) if C.TICKET_SOL else 0.0,
               why_entered=pos.get("why", ""))
    if pos.get("cost_model"):
        rec["cost_model"] = pos["cost_model"]
        rec["cost_model_started_at"] = pos.get("cost_model_started_at") or ""
    for k in observe.PUBLIC_KEYS:
        if k in pos:
            rec[k] = pos[k]
    b["closed"].append(rec)
    b["closed"] = b["closed"][-CLOSED_KEEP:]

def manage(d, s, now, sell_quote, liquidity, sleep, log=True):
    n = 0
    for name in "FRS":
        b = s["books"][name]
        for pid, pos in list(b["open"].items()):
            if n: sleep(1.2)
            n += 1; t0 = time.time()
            try: held_amt = int(pos["tokens"] * pos["frac"])
            except (TypeError, ValueError, KeyError): held_amt = 0
            status, px, q = mark_status(sell_quote, pos)
            liq = None
            raw = None
            fetched = False
            if status == "ok":
                try:
                    raw = liquidity(pos["mint"])
                    liq = sol_liq(raw)
                    fetched = True
                except Exception:
                    # The exit rule already treats a failed read as no liquidity. The log records null.
                    liq = None
                    raw = None
                    fetched = True
            acts = exits.check(pos, px, liq, now, status)  # raw quote price; the haircut is not an exit input
            if log:
                try:
                    if fetched:
                        _apply_log(pos, observe.note_held(pos, raw, now))
                    if status == "ok" and isinstance(px, (int, float)) and not isinstance(px, bool) and px > 0:
                        _apply_log(pos, observe.note_price(pos, px))
                except Exception:
                    pass
            delay = time.time() - t0
            pool = liq if liq is not None else pos.get("liq0")
            if status == "ok" and px is not None and px > 0:
                # Open mark is not an exit, so it does not take the stress add-on.
                pos["exit_haircut"] = haircut_frac(pool, verified_impact_bps(pool, gross_proceeds(pos, pos["frac"], px)))
            entry_h = pos.get("entry_haircut")
            if entry_h is None: entry_h = C.COST["legacy_haircut_per_side"]
            stamped = None
            def model():
                nonlocal stamped
                if stamped is None: stamped = ensure_model(s, now)
                return stamped
            for action, frac, why, apx in acts:
                if action == "mark":
                    _trade(d, now, name, pos, "mark", 0, 0, pos["last_mark"], why, delay=delay, stale=1,
                           entry_h=entry_h, model=model()); continue
                dx = gross_proceeds(pos, frac, apx) if apx > 0 else 0.0
                v = verified_impact_bps(pool, dx) if dx > 0 else None
                h = 0.0 if dx <= 0 else haircut_frac(pool, 0.0 if v is None else v, why)
                pro = net_proceeds(pos, frac, apx, h); gross, pnl = slice_pnl(pos, frac, apx, h)
                b["cash"] += pro; b["realized_pnl"] += pnl
                b["realized_gross"] = b.get("realized_gross", 0.0) + gross
                pos["pnl"] = pos.get("pnl", 0.0) + pnl
                pos["gross_pnl"] = pos.get("gross_pnl", 0.0) + gross
                if apx == 0.0: pos["last_mark"] = 0.0
                imp = ""
                if q and "priceImpactPct" in q and q.get("priceImpactPct") not in (None, ""):
                    imp = round(float(q.get("priceImpactPct") or 0) * 100, 4)
                _trade(d, now, name, pos, "sell", frac, pro, apx, why, pnl=pnl, delay=delay, stale=pos.get("stale", 0),
                       gross=gross, net=pnl, entry_h=entry_h, exit_h=h, impact=imp, model=model(), verified=v)
                full = not why.startswith("take_profit")
                if full: pos["frac"] = 0.0
            if pos["frac"] <= 1e-9:
                if log:
                    try:
                        _apply_log(pos, observe.stamp_exit(pos, raw if fetched else None, q, held_amt, fetched))
                    except Exception:
                        _apply_log(pos, {"exit_reserve_error": "log_failed", "exit_quote_error": "log_failed"})
                close(b, pid, pos, now, acts[-1][2] if acts else "closed", pos.get("pnl", 0.0), pos.get("gross_pnl", 0.0))

def void_reasons(s, st, now):
    out = []
    if s["last_run_ts"] and now - s["last_run_ts"] > C.OUTAGE_VOID_H * 3600: out.append("outage_over_2h")
    if st.get("checks") and st.get("data_failures", 0) / st["checks"] > C.DATA_FAIL_VOID: out.append("data_failures_over_10pct")
    return out

def enter(d, s, now, run_id, seed, leads, day, log=True):
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
            l = by[m]; tokens = int(l["quote"]["outAmount"])
            if tokens <= 0: continue
            liq0 = ((l.get("pair") or {}).get("liquidity") or {}).get("quote")
            cost, h_entry = entry_cost(liq0, l.get("quote"))
            if m in held or b["cash"] < cost: continue
            px = C.TICKET_SOL * LAMPORTS / tokens
            pos = exits.new_position(px, liq0, int(now))
            why = {"F": "pass", "R": "random_match", "S": "safety:" + (l["reason"] or "")}[name]
            model = ensure_model(s, now)
            v_entry = verified_impact_bps(liq0, C.TICKET_SOL)
            pos.update(mint=m, symbol=((l.get("pair") or {}).get("baseToken") or {}).get("symbol", ""), tokens=tokens,
                       run_id=run_id, why=why, book=name, pnl=0.0, gross_pnl=0.0,
                       entry_haircut=h_entry, entry_cost=cost,
                       cost_model=model[0], cost_model_started_at=model[1])
            if log:
                try:
                    _apply_log(pos, observe.entry_log(l, px, int(now)))
                except Exception:
                    _apply_log(pos, {"entry_reserve_error": "log_failed", "entry_quote_error": "log_failed"})
            pid = f"{name}-{run_id}-{m[:4]}{m[-4:]}"
            b["open"][pid] = pos; b["cash"] -= cost; b["fills_today"] += 1; held.add(m); opened += 1
            imp = round(float(l["quote"].get("priceImpactPct", 0) or 0) * 100, 4)
            _trade(d, now, name, pos, "buy", 1, C.TICKET_SOL, px, why, impact=imp, entry_h=h_entry,
                   model=model, verified=v_entry)
    return opened

def would_halt(s, now, day):
    for name in "FRS":
        b = s["books"][name]; eq = equity(b); dd = 1 - eq / C.START_BALANCE_SOL
        b["peak_equity"] = max(b["peak_equity"], eq)
        if dd >= C.SESSION_LOSS_HALT_PCT and not any(w["day"] == day for w in b["would_halt"]):
            b["would_halt"].append(dict(day=day, ts=iso(now), book=name, drawdown=round(dd, 4)))  # logged only, never blocks

PRE_MODEL = "pre-v3"

def model_label(row):
    """Blank cost_model is an older fill. The cell is not rewritten; the label is applied when it is read."""
    m = str((row or {}).get("cost_model") or "").strip()
    return m or PRE_MODEL

def read_trades(d):
    p = os.path.join(d, "trades.csv")
    if not os.path.exists(p): return []
    with open(p, newline="") as f: return list(csv.DictReader(f))

def window_stats(rows, now, started_at=""):
    """7-day and 28-day close stats. Counted pnl is only cost_model v3-exit-aware.
    Older closes in the same window stay in older_closes / older_labels and are not added in."""
    windows = {}
    for days in (7, 28):
        cut = float(now) - days * 86400
        sells = []
        for r in rows or []:
            if str(r.get("action") or "") != "sell": continue
            if r.get("pnl") in ("", None): continue
            try: ts = float(r.get("time") or "")
            except (TypeError, ValueError): continue
            if ts < cut: continue
            try: pnl = float(r["pnl"])
            except (TypeError, ValueError): continue
            sells.append((r, pnl))
        counted = [(r, p) for r, p in sells if model_label(r) == C.COST_MODEL]
        older = [(r, p) for r, p in sells if model_label(r) != C.COST_MODEL]
        labels = {}
        for r, _ in older:
            k = model_label(r); labels[k] = labels.get(k, 0) + 1
        windows[str(days)] = dict(counted_closes=len(counted), net_pnl=round(sum(p for _, p in counted), 9),
                                  wins=sum(1 for _, p in counted if p > 0),
                                  older_closes=len(older), older_net_pnl=round(sum(p for _, p in older), 9),
                                  older_labels=labels)
    return dict(model=C.COST_MODEL, started_at=started_at or "", windows=windows)

def ticket(pid, p, closed=False):
    t = dict(id=pid, ticker=p.get("symbol") or (p["mint"][:4] + "…" + p["mint"][-4:]), mint=p["mint"], book=p.get("book", pid[0]),
             status="closed" if closed else ("stale" if p.get("stale") else "open"), size=C.TICKET_SOL,
             cost_model=model_label(p))
    if p.get("cost_model_started_at"): t["cost_model_started_at"] = p["cost_model_started_at"]
    if closed:
        net = p["pnl"]
        res = dict(pnl=net, pct=p["pct"], unit="SOL", net_pnl=net, net_pct=p["pct"])
        if p.get("gross_pnl") is not None:
            res["gross_pnl"] = p["gross_pnl"]
            res["gross_pct"] = p.get("gross_pct", round(p["gross_pnl"] / C.TICKET_SOL * 100, 2) if C.TICKET_SOL else 0.0)
        t.update(opened_at=iso(p["opened"]), closed_at=iso(p["closed"]), entry=dict(px=p["entry"]), exit=dict(px=p["exit_px"]),
                 exit_reason=p["why"], result=res, thesis=p.get("why_entered", ""))
    else:
        r = p["last_mark"] / p["entry"] - 1 if p["entry"] else 0
        net = round(p.get("pnl", 0.0), 9)
        res = dict(pnl=net, pct=round(r * 100, 2), unit="SOL", net_pnl=net)
        if p.get("gross_pnl") is not None: res["gross_pnl"] = round(p["gross_pnl"], 9)
        t.update(opened_at=iso(p["opened"]), window_ends_at=iso(p["opened"] + C.EXIT["time_stop_min"] * 60),
                 entry=dict(px=p["entry"]), frac=round(p["frac"], 6), last_mark=p["last_mark"], thesis=p.get("why", ""),
                 result=res)
    for k in observe.PUBLIC_KEYS:
        if k in p:
            t[k] = p[k]
    return t

def _book_public(b):
    out = dict(cash=round(b["cash"], 9), equity=round(equity(b), 9), realized_pnl=round(b["realized_pnl"], 9),
               open=len(b["open"]), closed=len(b["closed"]), fills_today=b["fills_today"], would_halt=b["would_halt"][-5:])
    if "realized_gross" in b: out["realized_gross_pnl"] = round(b["realized_gross"], 9)
    return out

def paper_json(d, s, now):
    opn = [ticket(pid, p) for n in "FRS" for pid, p in s["books"][n]["open"].items()]
    cls = [ticket(c["id"], dict(c, book=n), closed=True) for n in "FRS" for c in s["books"][n]["closed"]]
    E = C.EXIT
    out = dict(schema_version=2, desk="solana-desk", book="F,R,S", chain="solana", unit="SOL",
               disclaimer="Paper trading only. No real orders. Research only, not advice.",
               ticket_size=C.TICKET_SOL, default_window_hours=E["time_stop_min"] / 60, generated_at=iso(now),
               cfg_version=C.CFG_VERSION, open=opn, closed=cls,
               books={n: _book_public(b) for n, b in s["books"].items()},
               costs=dict(model=C.COST_MODEL, started_at=s.get("cost_model_started_at") or "", note=C.COST_NOTE,
                          base_bps=C.COST["base_bps"], min_bps=C.COST["min_bps"], max_bps=C.COST["max_bps"],
                          impact_weight=C.COST["impact_weight"], stress_exit_bps=C.COST["stress_exit_bps"],
                          stress_exit_reasons=list(C.COST["stress_exit_reasons"]),
                          network_fee_sol=C.COST["network_fee_sol"], haircut_per_side=C.COST["haircut_per_side"],
                          verified_impact="constant_product_dx_over_pool_plus_dx", applied="entry_and_exit"),
               stats=window_stats(read_trades(d), now, s.get("cost_model_started_at") or ""),
               rules=[dict(id="entry", rule=f"Fixed {C.TICKET_SOL} SOL paper entry from a Jupiter quote; F max {C.MAX_OPEN_F} open, {C.MAX_NEW_F_PER_DAY} new/day; R and S capped at {C.R_MATCH_CAP}x F fills"),
                      dict(id="exit", rule=f"Stop {int(E['hard_stop']*100)}%, take +40%/+100% (1/3 each), trail {int(E['trail']*100)}% after first take, time stop {E['time_stop_min']} min unless up {int(E['time_stop_keep_if_up']*100)}%, liquidity pull {int(E['liq_drop']*100)}%, -100% after {E['no_route_kill_checks']} no-route checks"),
                      dict(id="void", rule=f"No new entries when data failures > {int(C.DATA_FAIL_VOID*100)}% today or after a loop outage > {C.OUTAGE_VOID_H}h"),
                      dict(id="costs", rule=C.COST_NOTE)],
               summaries=[dict(at=v["ts"], type="void", note=", ".join(v["why"])) for v in s["voids"][-10:]] +
                         [dict(at=w["ts"], type="would_halt", note=f"{w['book']} drawdown {w['drawdown']*100:.1f}%") for n in "FRS" for w in s["books"][n]["would_halt"][-3:]])
    with open(os.path.join(d, "paper.json"), "w") as f: json.dump(out, f, indent=1)
    return out

def step(d, now, run_id, seed, leads, st, sell_quote, liquidity, sleep=lambda s: None, *, log=True):
    """One fill-mode run. Returns a small summary for health.json.
    log=False skips the additive reserve, quote, and held-price fields. Decisions stay on the same path.
    """
    day = time.strftime("%Y-%m-%d", time.gmtime(now))
    s = load(d)
    manage(d, s, now, sell_quote, liquidity, sleep, log=log)
    v = void_reasons(s, st, now)
    opened = 0
    if v: s["voids"].append(dict(ts=iso(now), run_id=run_id, why=v)); s["voids"] = s["voids"][-100:]
    else: opened = enter(d, s, now, run_id, seed, leads, day, log=log)
    would_halt(s, now, day)
    s["last_run_ts"] = int(now); s["cfg_version"] = C.CFG_VERSION
    save(d, s); paper_json(d, s, now)
    return dict(opened=opened, void=v, open={n: len(s["books"][n]["open"]) for n in "FRS"},
                equity={n: round(equity(s["books"][n]), 4) for n in "FRS"})
