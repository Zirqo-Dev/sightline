"""Log holder counts and kill lines for the EST-01 universe. Research only.

One row per universe token per call. Nothing here opens a ticket, posts a
webhook, or changes a kill line, a verdict, or a rule. The day-14 verdict
and the frozen Solana rules are not imported.

A token is in the universe when the desk already named it on
``log.est01_check.universe`` (or the latest EST-01 ``check_log``), or when
the registered gate holds on this log: launchpad established or minswap,
pool ADA side at least 300,000, and a numeric pool kill line
("ADA side under …" / "pool … under …"). A name the desk excluded stays out.
"""
import json
import os
import re

POOL_MIN = 300_000
HANDLE = re.compile(r"@[A-Za-z0-9_]{2,32}")
ADDR = re.compile(r"\b(?:(?:stake|addr)(?:_test)?1[0-9a-z]{8,}|0x[0-9a-f]{8,})\b", re.I)
POOL_KILL = re.compile(r"(?:ada\s+side|pool)[^.]{0,48}?under\s+~?\d", re.I)
KEEP_LINES = 5000


def scrub(text):
    s = HANDLE.sub("", ADDR.sub("", str(text or "")))
    return re.sub(r"\s+", " ", s).strip()


def _num(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def numeric_pool_kill(text):
    """True when the line is a pool ADA-side floor, not a clip or a holder floor."""
    return bool(POOL_KILL.search(str(text or "")))


def _kill_text(log, verdict, ticker):
    lines = (log or {}).get("kill_lines") or {}
    raw = lines.get(ticker)
    if raw is None and isinstance(verdict, dict):
        raw = verdict.get("kill_line")
    if isinstance(raw, dict):
        raw = raw.get("text") or ""
    return scrub(raw)


def universe_tickers(log, paper):
    """Tickers the EST-01 check covers. Sorted. Does not invent a name."""
    log = log if isinstance(log, dict) else {}
    paper = paper if isinstance(paper, dict) else {}
    named = []
    seen = set()

    def add(token):
        tk = str(token or "").strip().upper()
        if not tk or tk in seen:
            return
        seen.add(tk)
        named.append(tk)

    check = log.get("est01_check") if isinstance(log.get("est01_check"), dict) else {}
    for token in check.get("universe") or []:
        add(token)
    for test in paper.get("tests") or []:
        if not isinstance(test, dict) or str(test.get("id") or "").upper() != "EST-01":
            continue
        logs = test.get("check_log") or []
        last = logs[-1] if logs and isinstance(logs[-1], dict) else {}
        for token in last.get("checked") or []:
            add(token)
    verdicts = {}
    for row in log.get("verdicts") or []:
        if isinstance(row, dict) and row.get("ticker"):
            verdicts[str(row["ticker"]).upper()] = row
    for row in log.get("cardano_top10") or []:
        if isinstance(row, dict) and row.get("ticker"):
            tk = str(row["ticker"]).upper()
            verdicts.setdefault(tk, {})
            # Pool depth can fill a missing side. Launchpad is never invented:
            # a top-10 name without an established verdict stays out of the universe.
            if row.get("ada_side_depth") is not None and verdicts[tk].get("pool_ada") is None:
                verdicts[tk] = dict(verdicts[tk], pool_ada=row.get("ada_side_depth"))
    for tk, verdict in verdicts.items():
        pad = str(verdict.get("launchpad") or "").lower()
        pool = _num(verdict.get("pool_ada"))
        if pad not in ("established", "minswap"):
            continue
        if pool is None or pool < POOL_MIN:
            continue
        if not numeric_pool_kill(_kill_text(log, verdict, tk)):
            continue
        add(tk)
    return named


def rows_for(log, paper):
    """Holder count and kill line for each universe token. Missing stays null."""
    log = log if isinstance(log, dict) else {}
    top = {}
    for row in log.get("cardano_top10") or []:
        if isinstance(row, dict) and row.get("ticker"):
            top[str(row["ticker"]).upper()] = row
    verdicts = {}
    for row in log.get("verdicts") or []:
        if isinstance(row, dict) and row.get("ticker"):
            verdicts[str(row["ticker"]).upper()] = row
    out = []
    for tk in universe_tickers(log, paper):
        verdict = verdicts.get(tk) or {}
        board = top.get(tk) or {}
        holders = _num(board.get("holders"))
        source = "cardano_top10" if holders is not None else None
        if holders is None:
            holders = _num(verdict.get("holders"))
            source = "verdict" if holders is not None else None
        pool = _num(board.get("ada_side_depth"))
        if pool is None:
            pool = _num(verdict.get("pool_ada"))
        out.append({
            "ticker": tk,
            "holders": None if holders is None else int(holders) if holders == int(holders) else holders,
            "holders_source": source,
            "kill_line": _kill_text(log, verdict, tk),
            "pool_ada": None if pool is None else int(pool) if pool == int(pool) else pool,
            "buy_signal": False,
            "mode": "log_only",
        })
    return out


def _stamp(now_s):
    from datetime import datetime, timezone
    try:
        ts = float(now_s)
    except (TypeError, ValueError):
        ts = 0
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record(data_dir, log, paper, now_s):
    """Append one jsonl line per token and refresh the latest snapshot.

    A second call with the same timestamp does not duplicate a ticker.
    The log and paper objects are not modified.
    """
    rows = rows_for(log, paper)
    at = _stamp(now_s)
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, "est_universe_log.jsonl")
    prior = []
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            prior = [line for line in fh.read().splitlines() if line.strip()]
    have = set()
    for line in prior:
        try:
            old = json.loads(line)
        except ValueError:
            continue
        if old.get("at") == at and old.get("ticker"):
            have.add(str(old["ticker"]).upper())
    fresh = []
    for row in rows:
        if row["ticker"] in have:
            continue
        fresh.append(json.dumps({"at": at, **row}, separators=(",", ":"), sort_keys=True))
    kept = (prior + fresh)[-KEEP_LINES:]
    with open(path, "w", encoding="utf-8") as fh:
        if kept:
            fh.write("\n".join(kept) + "\n")
    latest = {
        "at": at,
        "buy_signal": False,
        "mode": "log_only",
        "note": "Holder counts and kill lines for the EST-01 universe. Not a ticket and not a verdict.",
        "rows": rows,
    }
    with open(os.path.join(data_dir, "est_universe_latest.json"), "w", encoding="utf-8") as fh:
        json.dump(latest, fh, indent=1)
        fh.write("\n")
    return {"at": at, "tickers": [r["ticker"] for r in rows], "appended": len(fresh)}
