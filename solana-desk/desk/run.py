"""Scanner entry point (no AI, paper only). Usage:
    python -m desk.run --mode scan --data-dir PATH
Mode "scan" (default) only checks leads and logs tasks; it never fills.
Mode "fill" is refused until config.FROZEN is True (config freeze, end of day 3).
Free public APIs only: DexScreener, RugCheck, Jupiter quote (optional free key in JUPITER_API_KEY).
"""
import argparse, json, os, sys, time, urllib.request, urllib.error
from . import config as C, skips, ledger

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

def quote(get, mint, key, sleep=time.sleep):
    """Returns quote dict; {} if Jupiter says there is no route; raises on other errors.
    One retry on HTTP 429/5xx."""
    q = f"?inputMint={SOL}&outputMint={mint}&amount={int(C.TICKET_SOL * 1e9)}&slippageBps=300"
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

def mode_allowed(mode):
    return mode == "scan" or (mode == "fill" and C.FROZEN)

def load_state(d, day):
    p = os.path.join(d, "state.json")
    try: s = json.load(open(p))
    except Exception: s = {}
    if s.get("day") != day: s = dict(day=day, runs=0, checks=0, errors=0, data_failures=0)
    return s

def scan(data_dir, get=http_json, key=None, now=None, mode="scan", sleep=None):
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
    st.setdefault("eligibility_skips", 0)
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
        ledger.task(data_dir, dict(time=int(now), run_id=run_id, agent="scanner", task="scan", mint=mint, book="",
                    result="pass" if stype is None else "skip", skip_type=stype or "", reason=reason or "",
                    source=src, check_delay_s=round(time.time() - t0, 2), cfg_version=C.CFG_VERSION, seed=seed))
    st["runs"] += 1; st["slots"].append(run_id)
    st["run_times"].append(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)))
    with open(os.path.join(data_dir, "state.json"), "w") as f: json.dump(st, f, indent=1)
    h = ledger.health(data_dir, st["runs"], RUNS_PER_DAY, st["checks"], st["errors"], st["data_failures"], day)
    h.update(eligibility_skips=st["eligibility_skips"], mode=mode, cfg_version=C.CFG_VERSION, frozen=C.FROZEN, last_run_utc=run_id, leads_this_run=len(ls),
             runner="github-actions" if os.environ.get("GITHUB_ACTIONS") else "local", notes=notes,
             run_times_utc=st["run_times"], max_gap_min=max_gap_min(st["run_times"]),
             trigger=os.environ.get("GITHUB_EVENT_NAME", "local"))
    with open(os.path.join(data_dir, "health.json"), "w") as f: json.dump(h, f, indent=1)
    return h

def main(argv=None):
    a = argparse.ArgumentParser(); a.add_argument("--mode", choices=["scan", "fill"], default="scan")
    a.add_argument("--data-dir", default="desk-data-out"); o = a.parse_args(argv)
    if not mode_allowed(o.mode):
        print("fill mode refused: config not frozen yet (scan-only days)"); return 2
    if o.mode == "fill":
        print("fill mode not implemented in this runner yet; scanning only")
    h = scan(o.data_dir, key=os.environ.get("JUPITER_API_KEY") or None, mode="scan")
    if h.get("duplicate"): print("slot already scanned; nothing to do"); return 0
    print(json.dumps({k: h.get(k) for k in ("day", "runs", "checks", "error_rate", "leads_this_run", "max_gap_min", "notes")}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
