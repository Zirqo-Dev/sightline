#!/usr/bin/env python3
"""Build the desk pages from the desk files at build time.

Rows carry a display-only risk panel: kill-line distance, the swarm flag,
codenamed whale flow, and holders. The swarm flag is never a point. X buzz
is not a score and is not read into the panel. WHY and CHANGE MY MIND stay
factual and do not name a size.

Paper test status lists EST-01 and SW-01 near-misses with each registered
check passed or failed. A ticket is only an open row on those tests, and
that size is the registered 100 ADA paper. Nothing here opens a ticket.
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


SIZE_WORD = re.compile(
    r"\b(?:skip|small|standard|tiny)\s+lottery\b|\b(?:skip|small|standard)\s+ticket\b|\bbuy\s+\d+",
    re.I,
)
EST_LABELS = (
    ("kill", "pool at least 25% above the kill line"),
    ("whale", "codenamed whale net-buying at least 1,000 ADA over 72h"),
    ("holders", "holders flat or up over 7d"),
    ("liquidity", "liquidity down no more than 10%"),
)
EST_SHORT = {
    "kill": "kill-line distance",
    "whale": "whale net-buy",
    "holders": "holder trend",
    "liquidity": "liquidity",
}
SW_LABELS = (
    ("bonded", "bonded to a DEX pool"),
    ("rule_k", "120 holders, pool at least 25k ADA, 6h buys above sells"),
    ("whale", "codenamed whale still net-buying after 24h"),
)
WHALE_NET_MIN = 1000
ABOVE_LINE = 25
LIQ_FLOOR = -10


def no_size(text):
    s = SIZE_WORD.sub("", str(text or ""))
    return re.sub(r"\s+", " ", s).strip(" ;,")


def risk_panel(facts):
    """Display only. The swarm flag is separate and is not a point."""
    above = facts.get("above")
    dist = facts.get("dist") or ""
    if above is True:
        kill, kill_cls = (dist or "above the kill line"), "up"
    elif above is False:
        kill, kill_cls = (dist or "under the kill line"), "dn"
    else:
        kill, kill_cls = "kill distance not logged", "mu"
    name = facts.get("whale_name") or "Whale"
    whale = facts.get("whale")
    if whale == "buy":
        flow = name + " net-buying"
    elif whale == "trim":
        flow = name + " trimming"
    elif whale == "exit":
        flow = name + " exiting"
    else:
        flow = "no whale flow logged"
    holders_n = facts.get("holders_n")
    if holders_n is None:
        hold = "holders not logged"
    else:
        trend = ""
        if facts.get("holders") == "up":
            trend = " up"
        elif facts.get("holders") == "down":
            trend = " down"
        elif facts.get("holders_delta") == 0:
            trend = " flat"
        hold = f"holders {compact(float(holders_n))}{trend}"
    return {
        "kill": scrub(kill),
        "kill_cls": kill_cls,
        "swarm": bool(facts.get("swarm")),
        "whale": scrub(flow),
        "holders": scrub(hold),
    }


def change_line(facts):
    if facts.get("above") is False:
        text = "Side back above the kill line."
    elif facts.get("whale") == "trim":
        text = (facts.get("whale_name") or "The whale") + " flips to net-buy."
    elif facts.get("whale") == "exit":
        text = (facts.get("whale_name") or "The whale") + " stops selling."
    elif facts.get("swarm"):
        text = "Swarm exits and the side holds the 25k ₳ bar."
    elif facts.get("holders") != "up":
        text = "Holder trend is logged up on the next run."
    else:
        text = "A later run still clears the registered test."
    return no_size(text)


def why_line(facts):
    return no_size(scrub(facts.get("why") or "")) or "No reading in this feed."


def row(ticker, facts, right="–", right_cls="mu", pill="", pill_cls=""):
    return {
        "ticker": ticker,
        "logo": facts.get("logo") or ticker,
        "pill": pill,
        "pill_cls": pill_cls,
        "why": why_line(facts),
        "change": no_size(scrub(facts.get("change") or "")) or change_line(facts),
        "risk": risk_panel(facts),
        "right": right,
        "right_cls": right_cls,
    }


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check(cid, label, ok, detail, short):
    return {"id": cid, "label": label, "pass": bool(ok), "detail": scrub(detail), "short": short}


def _holder_7d(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value >= 0, signed(value, 1) + "% over 7d"
    text = str(value or "").strip()
    if not text:
        return False, "not logged"
    low = text.lower()
    if "unverifiable" in low or "no logged" in low or "missing" in low:
        return False, "unverifiable"
    if low in ("flat", "up") or low.startswith("+") or "flat or up" in low:
        return True, text
    return False, text


def est_checks(src):
    """The four EST-01 qualify checks. The 300k pool gate is the universe, not a fifth check."""
    src = src or {}
    pct = _num(src.get("pct_from_line"))
    net = _num(src.get("whale_net_72h_ada"))
    pool7 = _num(src.get("pool_7d_pct"))
    who = scrub(src.get("whale") or "")
    kill_ok = pct is not None and pct >= ABOVE_LINE
    whale_ok = net is not None and net >= WHALE_NET_MIN
    hold_ok, hold_detail = _holder_7d(src.get("holders_7d"))
    liq_ok = pool7 is not None and pool7 >= LIQ_FLOOR
    if net is None:
        whale_detail = "no 72h net-buy logged"
    else:
        whale_detail = ((who + " ") if who else "") + compact(net) + " ADA"
    if pct is None:
        kill_detail = "not logged"
    else:
        kill_detail = signed(pct, 1) + "% vs line"
    if pool7 is None:
        liq_detail = "not logged"
    else:
        liq_detail = signed(pool7, 1) + "% over 7d"
    return [
        _check("kill", EST_LABELS[0][1], kill_ok, kill_detail, EST_SHORT["kill"]),
        _check("whale", EST_LABELS[1][1], whale_ok, whale_detail, EST_SHORT["whale"]),
        _check("holders", EST_LABELS[2][1], hold_ok, hold_detail, EST_SHORT["holders"]),
        _check("liquidity", EST_LABELS[3][1], liq_ok, liq_detail, EST_SHORT["liquidity"]),
    ]


def sw_checks(src, launch=None):
    src = src or {}
    launch = launch or {}
    bonded = src.get("bonded") is True or launch.get("graduated") is True
    holders = _num(src.get("holders"))
    if holders is None:
        holders = _num(launch.get("holders"))
    pool = _num(src.get("pool_ada"))
    if pool is None:
        pool = _num(launch.get("pool_ada"))
    buys = _num(src.get("buys_6h"))
    sells = _num(src.get("sells_6h"))
    if holders is None and pool is None and buys is None:
        k_ok = False
        k_detail = "unverifiable, not counted"
    else:
        flow_ok = buys is not None and sells is not None and buys > sells
        k_ok = holders is not None and holders >= HOLDER_BAR and pool is not None and pool >= POOL_BAR and flow_ok
        bits = []
        bits.append("holders " + (compact(holders) if holders is not None else "not logged"))
        bits.append("side " + (compact(pool) + " ₳" if pool is not None else "not logged"))
        if buys is None or sells is None:
            bits.append("6h flow unverifiable")
        else:
            bits.append(f"6h {compact(buys)}/{compact(sells)}")
        k_detail = "; ".join(bits)
    whale = src.get("whale_accumulating")
    if whale is True:
        w_ok, w_detail = True, "net-buying after 24h"
    elif whale is False:
        w_ok, w_detail = False, "not net-buying after 24h"
    else:
        due = scrub(src.get("gate_check_due") or "")
        w_ok, w_detail = False, ("not checked yet" + (f" ({due})" if due else ""))
    return [
        _check("bonded", SW_LABELS[0][1], bonded, "bonded" if bonded else "not bonded", "bonded pool"),
        _check("rule_k", SW_LABELS[1][1], k_ok, k_detail, "rule k"),
        _check("whale", SW_LABELS[2][1], w_ok, w_detail, "whale after 24h"),
    ]


def _pack_near(ticker, checks):
    return {
        "ticker": ticker,
        "passed": sum(1 for c in checks if c["pass"]),
        "total": len(checks),
        "missing": [c.get("short") or c["id"] for c in checks if not c["pass"]],
        "checks": checks,
    }


def _ticket_open(ticket):
    if not isinstance(ticket, dict) or ticket.get("closed_at"):
        return False
    return str(ticket.get("status") or "open").lower() not in ("closed", "exited", "cancelled", "canceled")


def _test(paper, tid):
    for test in (paper or {}).get("tests") or []:
        if isinstance(test, dict) and str(test.get("id") or "").upper() == tid:
            return test
    return {}


def est_near_misses(log, paper):
    check = (log or {}).get("est01_check") if isinstance((log or {}).get("est01_check"), dict) else {}
    by = {}
    for row in check.get("rows") or []:
        if isinstance(row, dict) and row.get("ticker"):
            by[str(row["ticker"]).upper()] = row
    names = [str(t).upper() for t in (check.get("universe") or []) if t]
    if not names:
        last = ((_test(paper, "EST-01").get("check_log") or [{}])[-1])
        if isinstance(last, dict):
            names = [str(t).upper() for t in (last.get("checked") or []) if t]
    out = []
    for tk in names:
        checks = est_checks(by.get(tk) or {"ticker": tk})
        if all(c["pass"] for c in checks):
            continue
        out.append(_pack_near(tk, checks))
    return out


def sw_near_misses(log, paper):
    test = _test(paper, "SW-01")
    launches = {}
    for src in ((log or {}).get("cardano_v2") or {}).get("launches") or []:
        if isinstance(src, dict) and src.get("ticker"):
            launches[str(src["ticker"]).upper()] = src
    open_names = set()
    for ticket in test.get("tickets") or []:
        if _ticket_open(ticket) and ticket.get("ticker"):
            open_names.add(str(ticket["ticker"]).upper())
    out = []
    for src in test.get("comparison_group") or []:
        if not isinstance(src, dict) or not src.get("ticker"):
            continue
        tk = str(src["ticker"]).upper()
        if tk in open_names:
            continue
        checks = sw_checks(src, launches.get(tk))
        if all(c["pass"] for c in checks):
            continue
        out.append(_pack_near(tk, checks))
    return out


def ticket_line(paper, chain):
    if chain != "cardano":
        return "No EST-01 or SW-01 test on this chain. No sized ticket."
    bits = []
    for tid in ("EST-01", "SW-01"):
        for ticket in _test(paper, tid).get("tickets") or []:
            if not _ticket_open(ticket):
                continue
            name = scrub(ticket.get("ticker") or "")
            if name:
                bits.append(f"{name} {tid} fixed 100 ADA paper")
    if not bits:
        return "No open ticket. A ticket is only a registered test's fixed 100 ADA paper."
    return "Open: " + "; ".join(bits) + "."


def paper_status(log, paper, chain):
    cardano = chain == "cardano"
    return {
        "label": "Not a pick, not a ranking",
        "ticket": ticket_line(paper, chain),
        "universe": "Pool at least 300k ADA, established, with an owner-approved kill line." if cardano else "",
        "est01": est_near_misses(log, paper) if cardano else [],
        "sw01": sw_near_misses(log, paper) if cardano else [],
    }


def _pct_24h(text):
    m = re.search(r"([+\-−]?\d+(?:\.\d+)?)\s*%\s*(?:in\s*)?24h", text or "", re.I)
    if not m:
        m = re.search(r"price\s*([+\-−]?\d+(?:\.\d+)?)\s*%", text or "", re.I)
    if not m:
        return None
    return float(m.group(1).replace("−", "-").replace("+", ""))


def move_24h(text):
    """Numeric 24h move printed on a row, or None when that cell has no reading."""
    if text in (None, "", "–"):
        return None
    raw = str(text).strip().replace("−", "-").replace("%", "").replace("+", "").replace(",", "")
    if raw in ("", "-", "."):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def runner_sort_key(row):
    """Display order only: a numeric 24h move first (biggest first), then watch pills, then no reading."""
    move = move_24h((row or {}).get("right"))
    ticker = str((row or {}).get("ticker") or "")
    if move is not None:
        return (0, -move, ticker)
    if str((row or {}).get("pill") or "") == "watch":
        return (1, 0, ticker)
    return (2, 0, ticker)


def solana_run_asof(health):
    """Read-only stamp from health.json last_run_utc (YYYYMMDDTHHMM). Not a verdict input."""
    raw = str((health or {}).get("last_run_utc") or "") if isinstance(health, dict) else ""
    match = re.match(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})", raw)
    if not match:
        return None
    try:
        datetime(
            int(match.group(1)), int(match.group(2)), int(match.group(3)),
            int(match.group(4)), int(match.group(5)), tzinfo=timezone.utc,
        )
    except ValueError:
        return None
    return f"{match.group(1)}-{match.group(2)}-{match.group(3)}T{match.group(4)}:{match.group(5)}:00Z"


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
    swarm_names = set()
    v2 = log.get("cardano_v2") or {}
    for src in v2.get("launches") or []:
        if isinstance(src, dict) and src.get("swarm") and src.get("ticker"):
            swarm_names.add(str(src["ticker"]).upper())
    for group in v2.get("swarms") or []:
        if not isinstance(group, dict):
            continue
        for name in group.get("last_seen") or []:
            swarm_names.add(str(name).upper())
        for item in group.get("launches") or []:
            if isinstance(item, dict) and item.get("ticker"):
                swarm_names.add(str(item["ticker"]).upper())

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
        pct_line = _num(v.get("pct_from_line"))
        if above is None and pct_line is not None:
            above = pct_line > 0
            dist = signed(pct_line, 1) + "% vs line"
        swarm = tk in swarm_names or bool((seed or {}).get("swarm"))
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
        try:
            holders_num = None if holders_n is None else float(holders_n)
        except (TypeError, ValueError):
            holders_num = None
        try:
            delta_num = None if delta is None else float(delta)
        except (TypeError, ValueError):
            delta_num = None
        return {
            "whale": whale, "whale_name": who, "holders": holders, "holders_n": holders_num,
            "holders_delta": delta_num, "above": above, "dist": dist, "swarm": swarm,
            "why": "; ".join(bits), "logo": tk, "pct": pct,
        }

    def pack(tk, seed=None, pill="", pill_cls=""):
        facts = facts_for(tk, seed)
        pct = facts.get("pct")
        right = "–" if pct is None else signed(pct, 1) + "%"
        cls = "mu" if pct is None else "up" if pct > 0 else "dn" if pct < 0 else "mu"
        return row(tk, facts, right, cls, pill, pill_cls)

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
    # Established stays in top-10 order. Runners are display order only.
    rank = {tk: i for i, tk in enumerate(top)}
    established.sort(key=lambda r: (0 if r["pill"] == "watch" else 1, rank.get(r["ticker"], 99)))
    runners.sort(key=runner_sort_key)
    runners = runners[:12]
    established = established[:10]

    launches = []
    for src in ((log.get("cardano_v2") or {}).get("launches") or []):
        tk = str(src.get("ticker") or "").upper()
        if not tk:
            continue
        facts = facts_for(tk, src)
        facts["why"] = " · ".join(x for x in [src.get("age"), f"{src.get('holders')} holders" if src.get("holders") is not None else "", "swarm" if src.get("swarm") else "", "sniper" if src.get("sniper") else ""] if x)
        facts["change"] = "Gate check: 120 holders and side at the 25k ₳ bar."
        if src.get("swarm"):
            facts["swarm"] = True
        launches.append(row(tk, facts, "–", "mu", "pass", ""))
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
        "paper_status": paper_status(log, paper, "cardano"),
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


SOLANA_BOOK_RANK = {"F": 0, "R": 1, "S": 2}


def _mark_pct(entry, mark):
    try:
        if entry and float(entry) > 0 and mark is not None:
            return (float(mark) / float(entry) - 1) * 100
    except (TypeError, ValueError):
        return None
    return None


def solana_open_rows(positions):
    """One display row per token. The pill lists the books, for example F · R."""
    grouped = {}
    order = []
    for book, blob in ((positions or {}).get("books") or {}).items():
        op = (blob or {}).get("open") or {}
        if not isinstance(op, dict):
            continue
        for _id, p in op.items():
            if not isinstance(p, dict):
                continue
            symbol = scrub(p.get("symbol") or "")
            if not symbol:
                continue
            key = symbol.upper()
            if key not in grouped:
                grouped[key] = []
                order.append(key)
            grouped[key].append({
                "book": str(book),
                "symbol": symbol,
                "pct": _mark_pct(p.get("entry"), p.get("last_mark")),
                "why": scrub(str(p.get("why") or "open").replace("_", " ")),
            })
    opens = []
    for key in order:
        parts = sorted(grouped[key], key=lambda p: (SOLANA_BOOK_RANK.get(p["book"], 99), p["book"]))
        books = []
        for part in parts:
            if part["book"] not in books:
                books.append(part["book"])
        books.sort(key=lambda b: (SOLANA_BOOK_RANK.get(b, 99), b))
        pill = " · ".join(books)
        pcts = [part["pct"] for part in parts if part["pct"] is not None]
        pct = max(pcts) if pcts else None
        why = parts[0]["why"] or "open"
        if len(parts) == 1:
            if pct is not None:
                why = why + f"; mark {signed(pct, 1)}%"
        else:
            bits = []
            for part in parts:
                bits.append(part["book"] + (" no mark" if part["pct"] is None else f" {signed(part['pct'], 1)}%"))
            why = why + "; " + " · ".join(bits)
        symbol = parts[0]["symbol"]
        facts = {
            "whale": None, "holders": None, "holders_n": None, "above": None, "swarm": False,
            "why": why,
            "change": "A logged whale, holder, or kill-line print shows up. The day-14 line is not a ticket.",
            "logo": symbol,
        }
        item = row(
            symbol, facts,
            "–" if pct is None else signed(pct, 1) + "%",
            "up" if pct and pct > 0 else "dn" if pct and pct < 0 else "mu",
            pill, "",
        )
        item["book"] = pill
        opens.append(item)
    opens.sort(key=lambda r: -(move_24h(r["right"]) if move_24h(r["right"]) is not None else -1e9))
    return opens


def solana_section(data_dir, buzz, buzz_live):
    positions = load_json(os.path.join(data_dir, "solana-desk", "positions.json")) or {}
    health = load_json(os.path.join(data_dir, "solana-desk", "health.json")) or {}
    books, verdict, gap = solana_books(positions)
    opens = solana_open_rows(positions)
    sells = []
    path = os.path.join(data_dir, "solana-desk", "trades.csv")
    net = gross = wins = nsell = 0.0
    nwin = 0
    if os.path.isfile(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                if rec.get("action") != "sell":
                    continue  # refill is a cash top-up; its sol is not P&L
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
        "asof": solana_run_asof(health),
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
        "paper_status": paper_status({}, {}, "solana"),
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
            "whale": whale, "holders": None, "holders_n": None, "above": None, "swarm": False,
            "why": f"{w.get('chain') or ''} · " + (f"{floor} {unit} floor" if floor is not None else "floor not in the pulse") + ". Holders and the kill line are not in this pulse.",
            "change": "A fresh KING pulse with the floor up and 3 or more distinct buyers.",
            "logo": name,
        }
        item = row(name, facts, "floor n/a" if usd is None else ("$" + f"{usd:,.0f}" if usd >= 100 else f"${usd:,.2f}"), "" if usd is None else "", "watch" if status.lower() == "watch" else "pass", "a" if status.lower() == "watch" else "")
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
    return {
        "rows": rows,
        "open": len(paper.get("open") or []),
        "closed": len(closed),
        "pnl": round(pnl, 2),
        "asof": asof,
        "age_days": age_days,
        "prices": {"source": (prices or {}).get("source"), "asof": (prices or {}).get("asof"), "ETH": px.get("ETH"), "SOL": px.get("SOL")},
        "paper_status": paper_status({}, {}, "nft"),
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
        "legend": "Risk panel is display only: kill-line distance, swarm (red, never a point), codenamed whale flow, holders. "
        "X buzz is not a score. Paper test status is not a pick and not a ranking. "
        "A ticket is only a registered test's fixed 100 ADA paper. Research and paper only.",
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
