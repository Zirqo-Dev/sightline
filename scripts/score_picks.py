#!/usr/bin/env python3
"""Score Sightline rows from the desk files at build time.

Five signals, each 1 or 0: whale net-buy, holder trend, distance above the
kill line, X buzz, liquidity. A missing feed counts as 0 and is named in the
line. X buzz is left out of the tier while its file is missing. Nothing here
invents a position the feed did not name.

Paper ticket: Skip at 0–2 or a failed rule, Small at 3 without a rule pass,
Standard at 4 or more with a rule pass.
"""
import argparse
import csv
import json
import os
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/Toronto")
CUTOFF = 1790000000  # replaced below from the verdict clock
# 2026-11-11T15:00:00Z
CUTOFF_TS = 1794438000
VOID_START = 1761874200  # 2026-10-31T01:30:00Z
VOID_END = 1761955200    # 2026-11-01T00:00:00Z
TICKET = 0.5
STOP_EXP = -0.15
MIN_CLOSES = 20
POOL_BAR = 25000
HOLDER_BAR = 120

HANDLE = re.compile(r"@[A-Za-z0-9_]{2,32}")
ADDR = re.compile(r"\b(?:(?:stake|addr)(?:_test)?1[0-9a-z]{8,}|0x[0-9a-f]{8,})\b", re.I)


def scrub(text):
    s = HANDLE.sub("", ADDR.sub("", str(text or "")))
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_json(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def compact(n):
    if n is None:
        return ""
    a = abs(n)
    if a >= 1e6:
        return f"{a / 1e6:.3f}".rstrip("0").rstrip(".") + "M"
    if a >= 1e3:
        return f"{a / 1e3:.1f}".rstrip("0").rstrip(".") + "k"
    return str(int(round(a)))


def signed(n, digits=1):
    if n is None:
        return "–"
    body = f"{abs(n):.{digits}f}".rstrip("0").rstrip(".") if digits else str(int(round(abs(n))))
    if digits:
        body = f"{abs(n):.{digits}f}"
    return ("−" if n < 0 else "+" if n > 0 else "") + body


def tier_of(counted, rule_pass):
    if rule_pass and counted >= 4:
        return "Standard"
    if counted <= 2 or not rule_pass:
        if counted >= 3 and not rule_pass:
            return "Small" if counted == 3 else "Skip"
        return "Skip"
    return "Small"


def score_row(facts, buzz_live):
    """facts: whale buy|trim|exit|None, holders up|down|None, above True|False|None,
    buzz True|False|None (None = token not in a live file), liquid True|False|None.
    """
    notes = []
    whale = facts.get("whale")
    if whale == "buy":
        w = 1
    else:
        w = 0
        if whale is None:
            notes.append("no whale flow logged (0)")
    holders = facts.get("holders")
    if holders == "up":
        h = 1
    else:
        h = 0
        if holders is None:
            notes.append("holder trend not logged (0)")
    above = facts.get("above")
    if above is True:
        k = 1
    else:
        k = 0
        if above is None:
            notes.append("kill distance not logged (0)")
    buzz = facts.get("buzz")
    excluded = not buzz_live
    if excluded:
        b = 0
        notes.append("X buzz feed missing (0, not in the tier)")
    elif buzz is None:
        b = 0
        notes.append("no X buzz row (0)")
    else:
        b = 1 if buzz else 0
    liquid = facts.get("liquid")
    if liquid is True:
        liq = 1
    else:
        liq = 0
        if liquid is None:
            notes.append("liquidity not logged (0)")
    bits = [w, h, k, 0 if excluded else b, liq]
    counted = w + h + k + (0 if excluded else b) + liq
    rule = bool(facts.get("rule_pass"))
    # A failed rule cannot be Standard. Score 3 without a pass is Small.
    # Score 4+ with a pass is Standard. Anything else at 0–2, or a fail
    # that is not the Small case, is Skip.
    if rule and counted >= 4:
        tier = "Standard"
    elif counted == 3 and not rule:
        tier = "Small"
    elif counted <= 2 or not rule:
        tier = "Skip"
    else:
        tier = "Small"
    return {"score": bits, "counted": counted, "tier": tier, "buzz_excluded": excluded, "gaps": notes}


def change_line(facts):
    if facts.get("above") is False:
        return "Side back above the kill line."
    if facts.get("whale") == "trim":
        return (facts.get("whale_name") or "The whale") + " flips to net-buy."
    if facts.get("whale") == "exit":
        return (facts.get("whale_name") or "The whale") + " stops selling."
    if facts.get("swarm_fail"):
        return "Swarm exits and the side holds the 25k ₳ bar."
    if facts.get("holders") != "up":
        return "Holder trend is logged up on the next run."
    if not facts.get("buzz"):
        return "A live X buzz print shows up."
    return "A later run still clears the desk rule."


def why_line(facts, score):
    parts = []
    if facts.get("why"):
        parts.append(facts["why"])
    extra = [g for g in score["gaps"] if g not in (facts.get("why") or "")]
    if extra:
        parts.append("; ".join(extra))
    return scrub(" ".join(parts)) or "No reading in this feed (signals 0)."


def row(ticker, facts, buzz_live, right="–", right_cls="mu", pill="", pill_cls=""):
    scored = score_row(facts, buzz_live)
    return {
        "ticker": ticker,
        "logo": facts.get("logo") or ticker,
        "pill": pill,
        "pill_cls": pill_cls,
        "why": why_line(facts, scored),
        "change": scrub(facts.get("change") or change_line(facts)),
        "score": scored["score"],
        "counted": scored["counted"],
        "tier": scored["tier"],
        "buzz_excluded": scored["buzz_excluded"],
        "right": right,
        "right_cls": right_cls,
    }


def _pct_24h(text):
    m = re.search(r"([+\-−]?\d+(?:\.\d+)?)\s*%\s*(?:in\s*)?24h", text or "", re.I)
    if not m:
        m = re.search(r"price\s*([+\-−]?\d+(?:\.\d+)?)\s*%", text or "", re.I)
    if not m:
        return None
    return float(m.group(1).replace("−", "-").replace("+", ""))


def _distance(text):
    blob = text or ""
    above = re.search(r"(\d+(?:\.\d+)?)%\s+above\s+(?:the\s+)?(\d[\d,.]*k?)", blob, re.I)
    under = re.search(r"(\d+(?:\.\d+)?)%\s+under\s+(?:the\s+)?(\d[\d,.]*k?)", blob, re.I)
    if above:
        return True, f"{above.group(1)}% above {above.group(2)} line"
    if under:
        return False, f"{under.group(1)}% under {under.group(2)}"
    return None, ""


def cardano_section(data_dir, buzz, buzz_live):
    log = load_json(os.path.join(data_dir, "solada", "log.json")) or {}
    paper = load_json(os.path.join(data_dir, "solada", "paper.json")) or {}
    pump = load_json(os.path.join(data_dir, "solada", "pumpfun", "paper.json")) or {}
    devs = load_json(os.path.join(data_dir, "solada-prefilter", "dev_checks.json")) or {}
    verdicts = {str(v.get("ticker") or "").upper(): v for v in (log.get("verdicts") or []) if v.get("ticker")}
    top = []
    for r in log.get("cardano_top10") or []:
        if r.get("ticker"):
            top.append(str(r["ticker"]).upper())
    top_set = set(top)
    trends = {}
    rotation = ((log.get("cardano_v2") or {}).get("rotation") or {})
    who = scrub(rotation.get("who") or "")
    for t in rotation.get("trends") or []:
        tk = str(t.get("token") or "").upper()
        if tk:
            trends[tk] = str(t.get("trend") or "")
    levels = log.get("levels") or {}
    buzz_by = {}
    for tok in (buzz or {}).get("tokens") or []:
        if str(tok.get("chain") or "") == "cardano":
            buzz_by[str(tok.get("ticker") or "").upper()] = tok

    def facts_for(tk, seed=None):
        v = verdicts.get(tk) or {}
        seed = seed or {}
        reasons = " ".join(v.get("reasons") or [])
        blob = " ".join([reasons, str(levels.get(tk) or ""), str(seed.get("note") or ""), str(v.get("whale_activity") or "")])
        holders_n = v.get("holders")
        if holders_n is None:
            holders_n = seed.get("holders")
        pool = v.get("pool_ada")
        if pool is None:
            pool = seed.get("ada_side_depth")
        delta = v.get("holders_delta")
        if delta is None:
            delta = seed.get("holders_delta")
        trend = trends.get(tk)
        whale = None
        if trend in ("accumulating", "re-entering"):
            whale = "buy"
        elif trend == "trimming":
            whale = "trim"
        elif trend == "exiting":
            whale = "exit"
        if delta is None:
            holders = None
        elif float(delta) > 0:
            holders = "up"
        elif float(delta) < 0:
            holders = "down"
        else:
            holders = None
        above, dist = _distance(reasons or blob)
        if pool is None:
            liquid = None
        else:
            liquid = float(pool) >= POOL_BAR
        b = buzz_by.get(tk)
        if not buzz_live:
            buzz_hit = None
        elif b is None:
            buzz_hit = None
        else:
            ratio = (b.get("velocity") or {}).get("ratio_1h_vs_prev_hr")
            buzz_hit = bool(ratio is not None and float(ratio) >= 1)
        swarm_fail = bool(re.search(r"rule y|swarm", blob, re.I) and re.search(r"fail", blob, re.I))
        rule = (
            holders_n is not None and float(holders_n) >= HOLDER_BAR
            and pool is not None and float(pool) >= POOL_BAR
            and above is True and not swarm_fail
        )
        bits = []
        if whale == "buy":
            bits.append((who or "Whale") + " net-buying")
        elif whale == "trim":
            bits.append((who or "Whale") + " trimming")
        elif whale == "exit":
            bits.append((who or "Whale") + " exiting")
        if holders_n is not None:
            bits.append(f"holders {compact(float(holders_n))}")
        if pool is not None:
            bits.append(f"side {compact(float(pool))} ₳")
        if dist:
            bits.append(dist)
        pct = _pct_24h(blob)
        return {
            "whale": whale, "whale_name": who, "holders": holders, "above": above,
            "buzz": buzz_hit, "liquid": liquid, "rule_pass": rule, "swarm_fail": swarm_fail,
            "why": "; ".join(bits), "logo": tk, "pct": pct,
        }

    def pack(tk, seed=None, pill="", pill_cls=""):
        facts = facts_for(tk, seed)
        pct = facts.get("pct")
        right = "–" if pct is None else signed(pct, 1) + "%"
        cls = "mu" if pct is None else "up" if pct > 0 else "dn" if pct < 0 else "mu"
        return row(tk, facts, buzz_live, right, cls, pill, pill_cls)

    runners = []
    established = []
    for tk, v in verdicts.items():
        pad = str(v.get("launchpad") or "").lower()
        pill = str(v.get("verdict") or "pass").lower()
        pill_cls = "a" if pill == "watch" else "d" if "kill" in pill else ""
        label = "watch" if pill == "watch" else "near kill" if pill_cls == "d" else "passed · moving" if (_pct_24h(" ".join(v.get("reasons") or [])) or 0) > 0 and pill == "pass" else "pass"
        if label == "passed · moving":
            pill_cls = "a"
        item = pack(tk, None, label, pill_cls)
        if tk in top_set or pad in ("established", "minswap"):
            if tk in top_set:
                established.append(item)
        else:
            runners.append(item)
    # Keep top-10 order for established, runners by whether they are moving.
    rank = {tk: i for i, tk in enumerate(top)}
    established.sort(key=lambda r: (0 if r["pill"] == "watch" else 1, rank.get(r["ticker"], 99)))
    runners.sort(key=lambda r: (0 if r["right_cls"] == "up" else 1, -r["counted"], r["ticker"]))
    runners = runners[:12]
    established = established[:10]

    launches = []
    for src in ((log.get("cardano_v2") or {}).get("launches") or []):
        tk = str(src.get("ticker") or "").upper()
        if not tk:
            continue
        facts = facts_for(tk)
        facts["why"] = " · ".join(x for x in [src.get("age"), f"{src.get('holders')} holders" if src.get("holders") is not None else "", "swarm" if src.get("swarm") else "", "sniper" if src.get("sniper") else ""] if x)
        facts["change"] = "Gate check: 120 holders and side at the 25k ₳ bar."
        launches.append(row(tk, facts, buzz_live, "–", "mu", "pass", ""))
    launches = launches[:8]

    whales = []
    for w in log.get("whales") or []:
        name = scrub(w.get("codename") or "")
        if not name:
            continue
        net = w.get("net_flow_ada")
        whales.append({
            "name": name,
            "line": scrub(w.get("line") or ""),
            "right": "net ~0 ₳" if net is None or abs(float(net)) < 1 else signed(float(net) if abs(float(net)) < 1000 else float(net) / 1000, 1) + (" ₳" if abs(float(net)) < 1000 else "k ₳"),
            "cls": "up" if net and float(net) > 1 else "mu",
        })
    swarm = ((log.get("cardano_v2") or {}).get("swarms") or [None])[0]
    if isinstance(swarm, dict) and swarm.get("name"):
        whales.append({
            "name": scrub(swarm.get("name")),
            "line": scrub(" · ".join(x for x in [f"{swarm.get('wallets')} wallets" if swarm.get("wallets") else "", " ".join((swarm.get("last_seen") or [])[:4])] if x)),
            "right": "early buyers",
            "cls": "mu",
        })
    flow = []
    buckets = {"accumulating": [], "trimming": [], "exiting": [], "re-entering": []}
    for t in rotation.get("trends") or []:
        tk = str(t.get("token") or "").upper()
        if tk and t.get("trend") in buckets:
            buckets[t["trend"]].append(tk)
    if buckets["accumulating"]:
        flow.append("▲ " + " ".join(buckets["accumulating"]) + " accumulating")
    if buckets["re-entering"]:
        flow.append("▲ " + " ".join(buckets["re-entering"]) + " re-entering")
    if buckets["trimming"]:
        flow.append("▼ " + " ".join(buckets["trimming"]) + " trimming")
    if buckets["exiting"]:
        flow.append("✖ " + " ".join(buckets["exiting"]) + " exiting")

    def book_side(doc):
        closed = doc.get("closed") or []
        pnl = 0.0
        pcts = []
        wins = 0
        for t in closed:
            res = t.get("result") or {}
            pnl += float(res.get("pnl") or 0)
            if res.get("pct") is not None:
                pcts.append(float(res["pct"]))
                if float(res["pct"]) > 0:
                    wins += 1
        avg = sum(pcts) / len(pcts) if pcts else None
        return {"open": len(doc.get("open") or []), "closed": len(closed), "pnl": round(pnl, 1), "avg": avg, "wins": wins}

    tests = {str(t.get("id") or "").upper(): t for t in (paper.get("tests") or [])}
    sw = tests.get("SW-01") or {}
    est = tests.get("EST-01") or {}
    pending = [g.get("ticker") for g in (sw.get("comparison_group") or []) if g.get("ticker")]
    check = (est.get("check_log") or [{}])[-1] if est.get("check_log") else {}
    learned = [scrub(x) for x in (log.get("learned_or_changed") or []) if isinstance(x, str)]
    cov = (devs.get("coverage") or {})
    tracked = cov.get("tracked") or 0
    skipped = cov.get("skipped")
    nskip = len(skipped) if isinstance(skipped, list) else (skipped or 0)
    failure = (nskip / tracked) if tracked else None
    best = None
    pool = runners + established
    if pool:
        best = sorted(pool, key=lambda r: (-r["counted"], 0 if r["tier"] != "Skip" else 1))[0]
    buzz_rows = []
    for tok in sorted(buzz_by.values(), key=lambda t: -(t.get("mentions_1h") or 0))[:5]:
        ratio = (tok.get("velocity") or {}).get("ratio_1h_vs_prev_hr")
        buzz_rows.append({
            "ticker": tok.get("ticker"),
            "sub": f"{tok.get('mentions_1h')} posts / 1h" + (f" · {ratio}× prior hour" if ratio is not None else ""),
        })
    return {
        "asof": log.get("asof"),
        "failure": failure,
        "failure_skipped": nskip,
        "failure_tracked": tracked,
        "sentinel": "none",
        "runners": runners,
        "established": established,
        "radar": launches,
        "whales": whales,
        "flow": " · ".join(flow),
        "changed": scrub(" ".join(learned) or log.get("headline") or ""),
        "sw01": {"tickets": len(sw.get("tickets") or []), "names": pending[:4]},
        "est01": {"names": (check.get("checked") or [])[:4], "note": scrub(check.get("note") or "")},
        "books": {"solada": book_side(paper), "pump": book_side(pump)},
        "best": best,
        "buzz": buzz_rows,
    }


def solana_books(positions):
    out = []
    for book in ("F", "R", "S"):
        closed = (((positions or {}).get("books") or {}).get(book) or {}).get("closed") or []
        counted = []
        for row in closed:
            try:
                opened = int(row["opened"])
                done = int(row["closed"])
            except (KeyError, TypeError, ValueError):
                continue
            if VOID_START <= opened < VOID_END:
                continue
            if done > CUTOFF_TS:
                continue
            counted.append(row)
        n = len(counted)
        total = sum(float(r.get("pnl") or 0) for r in counted)
        exp = None if n == 0 else total / (TICKET * n)
        rule = "STOP" if n >= MIN_CLOSES and exp is not None and exp < STOP_EXP else "CONTINUE"
        out.append({"id": book, "n": n, "exp": exp, "exp_pct": None if exp is None else round(exp * 100, 1), "rule": rule, "total": total})
    last = (positions or {}).get("last_run_ts")
    try:
        last_ts = int(last) if last not in (None, "") else None
    except (TypeError, ValueError):
        last_ts = None
    verdict = "NOT_FINAL" if last_ts is None or last_ts < CUTOFF_TS else out[0]["rule"]
    f, r = out[0], out[1]
    gap = None
    if f["exp"] is not None and r["exp"] is not None:
        gap = round((f["exp"] - r["exp"]) * 100, 1)
    return out, verdict, gap


def solana_section(data_dir, buzz, buzz_live):
    positions = load_json(os.path.join(data_dir, "solana-desk", "positions.json")) or {}
    books, verdict, gap = solana_books(positions)
    opens = []
    for book, blob in ((positions.get("books") or {}).items()):
        op = (blob or {}).get("open") or {}
        if not isinstance(op, dict):
            continue
        for _id, p in op.items():
            symbol = scrub(p.get("symbol") or "")
            if not symbol:
                continue
            entry = p.get("entry")
            mark = p.get("last_mark")
            pct = None
            try:
                if entry and float(entry) > 0 and mark is not None:
                    pct = (float(mark) / float(entry) - 1) * 100
            except (TypeError, ValueError):
                pct = None
            buzz_hit = None
            if buzz_live:
                hit = next((t for t in (buzz or {}).get("tokens") or [] if str(t.get("chain")) == "solana" and str(t.get("ticker") or "").upper() == symbol.upper()), None)
                buzz_hit = None if hit is None else bool(((hit.get("velocity") or {}).get("ratio_1h_vs_prev_hr") or 0) >= 1)
            facts = {
                "whale": None, "holders": None, "above": None, "buzz": buzz_hit, "liquid": None,
                "rule_pass": False,
                "why": f"{scrub(str(p.get('why') or 'open').replace('_', ' '))}" + (f"; mark {signed(pct, 1)}%" if pct is not None else ""),
                "change": "F book back above −15% per trade by Nov 11, or a logged whale, holder, or liquidity print shows up.",
                "logo": symbol,
            }
            item = row(symbol, facts, buzz_live, "–" if pct is None else signed(pct, 1) + "%", "up" if pct and pct > 0 else "dn" if pct and pct < 0 else "mu", book, "")
            item["book"] = book
            opens.append(item)
    opens.sort(key=lambda r: -(float(r["right"].replace("−", "-").replace("%", "").replace("+", "") or 0) if r["right"] not in ("–",) else -1e9))
    sells = []
    path = os.path.join(data_dir, "solana-desk", "trades.csv")
    net = gross = wins = nsell = 0.0
    nwin = 0
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                if rec.get("action") != "sell":
                    continue
                nsell += 1
                try:
                    pnl = float(rec["pnl"]) if rec.get("pnl") not in ("", None) else None
                except ValueError:
                    pnl = None
                try:
                    g = float(rec["gross_pnl"]) if rec.get("gross_pnl") not in ("", None) else None
                except ValueError:
                    g = None
                if pnl is not None:
                    net += pnl
                    if pnl > 0:
                        nwin += 1
                if g is not None:
                    gross += g
                sells.append(rec)
    trades = []
    for rec in sells[-8:]:
        try:
            ts = int(float(rec.get("time") or 0))
            when = datetime.fromtimestamp(ts, ET).strftime("%-I:%M %p")
        except (TypeError, ValueError, OSError):
            when = ""
        try:
            pnl = float(rec["pnl"]) if rec.get("pnl") not in ("", None) else None
        except ValueError:
            pnl = None
        trades.append({
            "time": when,
            "book": rec.get("book") or "",
            "ticker": scrub(rec.get("symbol") or ""),
            "reason": scrub(str(rec.get("why") or "").replace("_", " ")) or "no reason logged",
            "pnl": None if pnl is None else round(pnl, 3),
        })
    trades.reverse()
    f = books[0] if books else {}
    closest = opens[0] if opens else None
    best = {
        "name": "Skip today",
        "why": (
            f"Closest is {closest['ticker']} ({closest['right']})" if closest else "No open position in the feed"
        ) + (
            f", and the F book is {f.get('rule')} at {signed(f.get('exp_pct'), 1)}% per trade over {f.get('n')} closes. "
            "Whale, holder, kill line and liquidity are not in the Solana feed (0)."
        ),
        "change": "F book back above −15% per trade by Nov 11, or a fresh pass with a live X buzz row.",
        "score": closest["score"] if closest else [0, 0, 0, 0, 0],
        "tier": "Skip",
        "buzz_excluded": not buzz_live,
        "logo": closest["logo"] if closest else "",
    }
    buzz_rows = []
    for tok in (buzz or {}).get("tokens") or []:
        if str(tok.get("chain")) != "solana":
            continue
        tk = str(tok.get("ticker") or "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,14}", tk) or tk.lower() == "unknown":
            continue
        ratio = (tok.get("velocity") or {}).get("ratio_1h_vs_prev_hr")
        buzz_rows.append({"ticker": tk, "sub": f"{tok.get('mentions_1h')} posts / 1h" + (f" · {ratio}× prior hour" if ratio is not None else "")})
    costs = round(gross - net, 2) if nsell else None
    return {
        "books": books,
        "verdict": verdict,
        "gap_pp": gap,
        "opens": opens,
        "trades": trades,
        "net": round(net, 3),
        "gross": round(gross, 3),
        "costs": costs,
        "win_pct": round(100 * nwin / nsell, 1) if nsell else None,
        "fills": int(nsell),
        "best": best,
        "buzz": buzz_rows[:5],
        "buzz_live": buzz_live,
    }


def nft_section(data_dir, prices):
    log = load_json(os.path.join(data_dir, "nft", "log.json")) or {}
    pulse = load_json(os.path.join(data_dir, "nft", "pulse.json")) or {}
    paper = load_json(os.path.join(data_dir, "nft", "paper.json")) or {}
    latest = {}
    for r in pulse.get("latest") or []:
        if r.get("ticker"):
            latest[re.sub(r"[^a-z0-9]", "", str(r["ticker"]).lower())] = r
    watch = []
    runs = log.get("runs") or []
    if runs and isinstance(runs[0], dict):
        watch = runs[0].get("watchlist") or []
    px = (prices or {}).get("prices") or {}
    rows = []
    for w in watch:
        name = scrub(w.get("name") or w.get("slug") or "")
        if not name:
            continue
        key = re.sub(r"[^a-z0-9]", "", str(w.get("slug") or name).lower())
        live = latest.get(key)
        if live is None:
            live = next((v for k, v in latest.items() if k and (key in k or k in key)), None)
        floor = None if not live else live.get("floor")
        unit = (live or {}).get("unit") or ""
        usd = None
        if floor is not None:
            u = str(unit).upper()
            if u in ("USD", "USDC", "USDT"):
                usd = float(floor)
            elif u in px:
                usd = float(floor) * float(px[u])
        text = scrub(" ".join(str(w.get(k) or "") for k in ("today", "kill")))
        whale = "buy" if re.search(r"one whale|whale sweep|whale", text, re.I) else None
        status = str(w.get("status") or "pass")
        facts = {
            "whale": whale, "holders": None, "above": None, "buzz": None, "liquid": None,
            "rule_pass": False,
            "why": f"{w.get('chain') or ''} · " + (f"{floor} {unit} floor" if floor is not None else "floor not in the pulse") + ". Holder trend, kill line, X buzz and liquidity are not in this pulse (0).",
            "change": "A fresh KING pulse with the floor up and 3 or more distinct buyers.",
            "logo": name,
        }
        item = row(name, facts, False, "floor n/a" if usd is None else ("$" + f"{usd:,.0f}" if usd >= 100 else f"${usd:,.2f}"), "" if usd is None else "", "watch" if status.lower() == "watch" else "pass", "a" if status.lower() == "watch" else "")
        item["chain"] = w.get("chain") or ""
        item["floor"] = floor
        item["unit"] = unit
        rows.append(item)
    closed = paper.get("closed") or []
    pnl = sum(float((t.get("result") or {}).get("pnl") or 0) for t in closed)
    asof = pulse.get("updated_at") or log.get("asof")
    age_days = None
    if asof:
        try:
            dt = datetime.fromisoformat(str(asof).replace("Z", "+00:00"))
            age_days = int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() // 86400)
        except ValueError:
            age_days = None
    best_row = next((r for r in rows if r["pill"] == "watch"), rows[0] if rows else None)
    best = None
    if best_row:
        best = {
            "name": "Skip today",
            "logo": best_row["logo"],
            "why": f"Closest is {best_row['ticker']}. " + best_row["why"],
            "change": best_row["change"],
            "score": best_row["score"],
            "tier": "Skip",
            "buzz_excluded": True,
        }
    return {
        "rows": rows,
        "open": len(paper.get("open") or []),
        "closed": len(closed),
        "pnl": round(pnl, 2),
        "asof": asof,
        "age_days": age_days,
        "prices": {"source": (prices or {}).get("source"), "asof": (prices or {}).get("asof"), "ETH": px.get("ETH"), "SOL": px.get("SOL")},
        "best": best,
    }


def briefs_section(data_dir):
    out = []
    for desk, label in (("solada", "Cardano"), ("nft", "NFT")):
        folder = os.path.join(data_dir, desk, "briefs")
        if not os.path.isdir(folder):
            continue
        names = sorted(n for n in os.listdir(folder) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", n))
        for name in names[-3:]:
            doc = load_json(os.path.join(folder, name)) or {}
            if doc.get("headline"):
                out.append({"desk": label, "date": doc.get("date") or name[:10], "headline": scrub(doc["headline"])})
    out.sort(key=lambda r: r["date"], reverse=True)
    return out[:3]


def build(data_dir):
    buzz = load_json(os.path.join(data_dir, "x_buzz.json"))
    buzz_live = bool(buzz and buzz.get("tokens"))
    prices = load_json(os.path.join(data_dir, "dashboard", "prices.json"))
    cardano = cardano_section(data_dir, buzz, buzz_live)
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "buzz_live": buzz_live,
        "cardano": cardano,
        "solana": solana_section(data_dir, buzz, buzz_live),
        "nft": nft_section(data_dir, prices),
        "briefs": briefs_section(data_dir),
        "legend": "Score = 5 signals, 1 bar each: whale net-buy · holder trend · above kill line · X buzz · liquidity. A missing signal is 0. "
        + ("X buzz is in the file and counts when the token has a row. " if buzz_live else "X buzz feed is missing, shown dashed, and not counted toward a tier. ")
        + "Paper ticket: Skip (0–2 or a failed rule) · Small (3, no rule pass) · Standard (4 or more and a rule pass). Research and paper only.",
    }


def write(data_dir, dest):
    doc = build(data_dir)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        json.dump(doc, fh)
        fh.write("\n")
    return doc


def main():
    parser = argparse.ArgumentParser(description="Score picks from desk files")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    doc = write(os.path.abspath(args.data_dir), os.path.abspath(args.out))
    print(f"score-picks: buzz_live={doc['buzz_live']} runners={len(doc['cardano']['runners'])} opens={len(doc['solana']['opens'])} nft={len(doc['nft']['rows'])}")


if __name__ == "__main__":
    main()
