"""Scanner entry point (no AI, paper only). Usage:
    python -m desk.run --mode scan --data-dir PATH
Mode "scan" (default) only checks leads and logs tasks; it never fills.
Mode "fill" = scan + paper fill step (desk/fills.py); allowed only when config.FROZEN and FILL_READY.
Mode "auto" (workflow default) = fill when allowed, else scan.
Free public APIs only: DexScreener, RugCheck, Jupiter quote (optional free key in JUPITER_API_KEY).
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from . import config as C, skips, ledger, fills

SOL = "So11111111111111111111111111111111111111112"
DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report"
JUP_KEYED, JUP_LITE = "https://api.jup.ag/swap/v1/quote", "https://lite-api.jup.ag/swap/v1/quote"
RUNS_PER_DAY = 48  # every 30 min on GitHub Actions
SLOT_S = 1800

def max_gap_min(times):
    ts = [time.mktime(time.strptime(t, "%Y-%m-%dT%H:%M:%SZ")) for t in times]
    return round(max((b - a for a, b in zip(ts, ts[1:])), default=0) / 60, 1)

def http_json(url, headers=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "solana-paper-desk/1", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def leads(get, n):
    out, seen = [], set()
    for path in ("/token-profiles/latest/v1", "/token-boosts/latest/v1"):
        try: items = get(DEX + path)
        except Exception: continue
        for it in items or []:
            m = it.get("tokenAddress")
            if it.get("chainId") == "solana" and m and m not in seen:
                seen.add(m); out.append((m, path.split("/")[1]))
    return out[:n]

def best_pair(get, mint):
    d = get(f"{DEX}/latest/dex/tokens/{mint}") or {}
    ps = [p for p in d.get("pairs") or [] if p.get("chainId") == "solana" and (p.get("quoteToken") or {}).get("address") == SOL]
    return max(ps, key=lambda p: (p.get("liquidity") or {}).get("quote", 0), default=None)

QUOTE_GAP_S = 1.2   # keyed free tier 429s after ~11 back-to-back calls (measured 2026-10-25)
RETRY_WAIT_S = 2.0

def quote(get, mint, key, sleep=time.sleep, input_mint=SOL, amount=None):
    """Returns quote dict; {} if Jupiter says there is no route; raises on other errors.
    One retry on HTTP 429/5xx. Default: buy quote for the ticket; fills use input_mint=mint to price a sell."""
    out = SOL if input_mint != SOL else mint
    amt = int(C.TICKET_SOL * 1e9) if amount is None else int(amount)
    q = f"?inputMint={input_mint}&outputMint={out}&amount={amt}&slippageBps=300"
    url, hdr = (JUP_KEYED + q, {"x-api-key": key}) if key else (JUP_LITE + q, None)
    for attempt in (0, 1):
        try:
            return get(url, hdr) if hdr else get(url)
        except urllib.error.HTTPError as e:
            body = ""
            try: body = e.read().decode(errors="ignore")
            except Exception: pass
            if e.code == 400 and ("NO_ROUTE" in body.upper() or "COULD_NOT_FIND" in body.upper()):
                return {}  # real "no route" -> execution skip, not a data failure
            if attempt == 0 and (e.code == 429 or e.code >= 500):
                sleep(RETRY_WAIT_S); continue
            raise

ERR_NAMES = {"dex": "dexscreener", "rug": "rugcheck", "jup": "jupiter"}
WOULD_NAME = "rugger_would_veto.json"
WOULD_NOTE = (
    "Log only. A known rugger or a wallet they funded would have been vetoed. "
    "This file does not change entries, exits, skips, or books. Wallet ids are w- hashes. "
    "buy_signal is false. The Solana desk has no LP-pull or dump event; the feed is RugCheck rugged."
)


def _rugger():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if root not in sys.path:
        sys.path.insert(0, root)
    import rugger_blacklist as RB
    return RB


def rugger_book(cache_dir, data_dir):
    """Private book. No cache dir keeps this run in memory and writes no wallet file."""
    RB = _rugger()
    if not cache_dir:
        return RB.Book(RB.MemoryStore(None), RB.settings_from(None))
    return RB.open_book(cache_dir, data_dir)


def observe_rugger(book, rc, mint, now):
    """Record RugCheck rugged, then judge. Returns public verdicts only. Does not skip."""
    if book is None or not isinstance(rc, dict):
        return []
    RB = _rugger()
    creator = rc.get("creator")
    at = RB.iso(now)
    if rc.get("rugged") is True and isinstance(mint, str) and mint:
        book.note_evidence(creator, "solana", "rugcheck_rugged", mint, at)
    if not isinstance(creator, str) or not creator or not isinstance(mint, str) or not mint:
        return []
    return book.judge(creator, mint, now)


def _public_would(mint, verdict):
    row = {
        "mint": mint,
        "code": verdict.get("code"),
        "rugger_id": verdict.get("rugger_id"),
        "evidence_tags": list(verdict.get("tags") or []),
    }
    if verdict.get("code") == "known_rugger_funded":
        row["wallet_id"] = verdict.get("wallet_id")
        row["hop"] = verdict.get("hop")
    return row


def _would_ok(doc):
    """True when the public log has no raw wallet field. Does not print values."""
    if not isinstance(doc, dict) or doc.get("buy_signal") is not False or doc.get("mode") != "log_only":
        return False
    if doc.get("desk") != "solana" or doc.get("schema_version") != 1:
        return False
    rows = doc.get("rows")
    if not isinstance(rows, list):
        return False
    banned = {"wallet", "stake", "address", "creator", "funder", "funded", "launcherAddress"}
    id_re = _rugger()._ID_RE
    tags = set(_rugger().TAGS)

    def walk(node):
        if isinstance(node, dict):
            if banned & set(node):
                return False
            return all(walk(v) for v in node.values())
        if isinstance(node, list):
            return all(walk(v) for v in node)
        if isinstance(node, str) and (_rugger()._STAKE_RE.search(node) or _rugger()._ADDR_RE.search(node)):
            return False
        return True

    if not walk(doc):
        return False
    for row in rows:
        if not isinstance(row, dict):
            return False
        code = row.get("code")
        keys = set(row)
        if code == "known_rugger":
            if keys != {"mint", "code", "rugger_id", "evidence_tags"}:
                return False
        elif code == "known_rugger_funded":
            if keys != {"mint", "code", "rugger_id", "wallet_id", "evidence_tags", "hop"}:
                return False
            hop = row.get("hop")
            if isinstance(hop, bool) or not isinstance(hop, int) or not 1 <= hop <= 8:
                return False
            if not id_re.fullmatch(row.get("wallet_id") or ""):
                return False
        else:
            return False
        if not isinstance(row.get("mint"), str) or not row["mint"] or len(row["mint"]) > 64:
            return False
        if not id_re.fullmatch(row.get("rugger_id") or ""):
            return False
        ev = row.get("evidence_tags")
        if not isinstance(ev, list) or not ev or any(t not in tags for t in ev):
            return False
    return True


def write_would(data_dir, fresh, now):
    """Append this run's would-veto rows. Hashes only. A bad document is not written."""
    if not fresh:
        return False
    RB = _rugger()
    path = os.path.join(data_dir, WOULD_NAME)
    prev = []
    if os.path.exists(path):
        try:
            old = json.load(open(path))
            if isinstance(old, dict) and isinstance(old.get("rows"), list):
                prev = [r for r in old["rows"] if isinstance(r, dict)]
        except (OSError, ValueError):
            prev = []
    seen = set()
    rows = []
    for row in prev + fresh:
        key = (row.get("mint"), row.get("code"), row.get("rugger_id"), row.get("wallet_id"), row.get("hop"))
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    doc = {
        "schema_version": 1, "desk": "solana", "mode": "log_only", "buy_signal": False,
        "note": WOULD_NOTE, "at": RB.iso(now), "rows": rows[-200:],
    }
    if not _would_ok(doc):
        print("rugger log: not written (public check)", file=sys.stderr)
        return False
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1)
        fh.write("\n")
    return True

FILL_READY = True  # fill step built (desk/fills.py); paper only

def mode_allowed(mode):
    return mode == "scan" or (mode == "fill" and C.FROZEN and FILL_READY)

def load_state(d, day):
    p = os.path.join(d, "state.json")
    try: s = json.load(open(p))
    except Exception: s = {}
    if s.get("day") != day: s = dict(day=day, runs=0, checks=0, errors=0, data_failures=0)
    return s

def _touch_cache(cache_dir, data_dir):
    """Make sure the private directory has a file so the Actions cache save has something to store.

    A duplicate slot returns before the scan. An empty directory makes that save fail.
    The file is the blacklist document, never copied into the public data directory.
    """
    if not cache_dir:
        return
    try:
        os.makedirs(cache_dir, exist_ok=True)
        book = rugger_book(cache_dir, data_dir)
        if book is not None and book.writable:
            path = os.path.join(cache_dir, _rugger().PRIVATE_NAME)
            if not os.path.exists(path):
                book.save()
    except Exception as e:
        print(f"rugger log: skipped ({type(e).__name__})", file=sys.stderr)


def scan(data_dir, get=http_json, key=None, now=None, mode="scan", sleep=None, cache_dir=None):
    _touch_cache(cache_dir, data_dir)
    now = now or time.time(); day = time.strftime("%Y-%m-%d", time.gmtime(now))
    os.makedirs(data_dir, exist_ok=True)
    slot = int(now) // SLOT_S * SLOT_S  # idempotency: one scan per 30-min slot
    run_id = time.strftime("%Y%m%dT%H%M", time.gmtime(slot)); seed = slot // 60
    st = load_state(data_dir, day); notes = []
    st.setdefault("run_times", [])
    if run_id in st.setdefault("slots", []):
        h = json.load(open(os.path.join(data_dir, "health.json")))
        h["last_skipped_duplicate_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))
        with open(os.path.join(data_dir, "health.json"), "w") as f: json.dump(h, f, indent=1)
        h["duplicate"] = True; return h
    if not key: notes.append("JUPITER_API_KEY missing: using keyless lite-api")
    ls = leads(get, C.LEADS_PER_RUN)
    if not ls: notes.append("no leads (DexScreener empty or unreachable)")
    if sleep is None: sleep = time.sleep if get is http_json else (lambda s: None)
    st.setdefault("eligibility_skips", 0); run_leads = []
    try:
        book = rugger_book(cache_dir, data_dir)
    except Exception as e:
        print(f"rugger log: skipped ({type(e).__name__})", file=sys.stderr)
        book = None
    would = []
    for i, mint in enumerate(m for m, _ in ls):
        src = ls[i][1]
        if i: sleep(QUOTE_GAP_S)  # pace Jupiter (and RugCheck) instead of bursting 12 calls
        t0 = time.time(); errs = set()
        try: pair = best_pair(get, mint)
        except Exception: pair = None; errs.add("dex")
        try: rc = get(RUG.format(mint))
        except Exception: rc = None; errs.add("rug")
        try: q = quote(get, mint, key, sleep)
        except Exception: q = None; errs.add("jup")
        stype, reason = skips.classify(pair, rc, q, now, errs)
        err = bool(errs)
        st["checks"] += 1; st["errors"] += int(err); st["data_failures"] += int(stype == "data")
        st["eligibility_skips"] += int(stype == "eligibility")
        run_leads.append(dict(mint=mint, stype=stype, reason=reason, quote=q, pair=pair))
        ledger.task(data_dir, dict(time=int(now), run_id=run_id, agent="scanner", task="scan", mint=mint, book="",
                    result="pass" if stype is None else "skip", skip_type=stype or "", reason=reason or "",
                    source=src, check_delay_s=round(time.time() - t0, 2), cfg_version=C.CFG_VERSION, seed=seed,
                    error_src=";".join(sorted(ERR_NAMES[e] for e in errs))))
        # Log-only. stype, reason, and run_leads are already fixed. Fills do not read this.
        try:
            if book is not None:
                for verdict in observe_rugger(book, rc, mint, now):
                    would.append(_public_would(mint, verdict))
        except Exception as e:
            print(f"rugger log: skipped ({type(e).__name__})", file=sys.stderr)
    if book is not None:
        try:
            book.save()
        except Exception as e:
            print(f"rugger log: skipped ({type(e).__name__})", file=sys.stderr)
    try:
        write_would(data_dir, would, now)
    except Exception as e:
        print(f"rugger log: skipped ({type(e).__name__})", file=sys.stderr)
    st["runs"] += 1; st["slots"].append(run_id)
    st["run_times"].append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
    with open(os.path.join(data_dir, "state.json"), "w") as f: json.dump(st, f, indent=1)
    fill = None
    if mode == "fill":
        def sell_quote(m, amount): return quote(get, m, key, sleep, input_mint=m, amount=amount)
        def liquidity(m):
            p = best_pair(get, m); return ((p or {}).get("liquidity") or {}).get("quote")
        fill = fills.step(data_dir, now, run_id, seed, run_leads, st, sell_quote, liquidity, sleep)
        if fill["void"]: notes.append("new entries voided: " + ", ".join(fill["void"]))
    h = ledger.health(data_dir, st["runs"], RUNS_PER_DAY, st["checks"], st["errors"], st["data_failures"], day)
    h.update(eligibility_skips=st["eligibility_skips"], mode=mode, cfg_version=C.CFG_VERSION, frozen=C.FROZEN, last_run_utc=run_id, leads_this_run=len(ls),
             runner="github-actions" if os.environ.get("GITHUB_ACTIONS") else "local", notes=notes,
             run_times_utc=st["run_times"], max_gap_min=max_gap_min(st["run_times"]),
             trigger=os.environ.get("GITHUB_EVENT_NAME", "local"))
    if fill: h.update(fill=fill)
    with open(os.path.join(data_dir, "health.json"), "w") as f: json.dump(h, f, indent=1)
    return h

def main(argv=None):
    a = argparse.ArgumentParser(); a.add_argument("--mode", choices=["scan", "fill", "auto"], default="scan")
    a.add_argument("--data-dir", default="desk-data-out")
    a.add_argument("--cache-dir", default=os.environ.get("SOLANA_DESK_CACHE_DIR") or None)
    o = a.parse_args(argv)
    mode = o.mode
    if mode == "auto": mode = "fill" if mode_allowed("fill") else "scan"
    if not mode_allowed(mode):
        print("fill mode refused: config not frozen or fill step not built yet"); return 2
    h = scan(o.data_dir, key=os.environ.get("JUPITER_API_KEY") or None, mode=mode, cache_dir=o.cache_dir)
    print(f"mode={mode}")
    if h.get("duplicate"): print("slot already scanned; nothing to do"); return 0
    print(json.dumps({k: h.get(k) for k in ("day", "runs", "checks", "error_rate", "leads_this_run", "max_gap_min", "notes")}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
