"""Scanner entry point (no AI, paper only). Usage:
    python -m desk.run --mode scan --data-dir PATH
Mode "scan" (default) only checks leads and logs tasks; it never fills.
Mode "fill" is refused until config.FROZEN is True (config freeze, end of day 3).
Free public APIs only: DexScreener, RugCheck, Jupiter quote (optional free key in JUPITER_API_KEY).
"""
import argparse, json, os, sys, time, urllib.request
from . import config as C, skips, ledger

SOL = "So11111111111111111111111111111111111111112"
DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report"
JUP_KEYED, JUP_LITE = "https://api.jup.ag/swap/v1/quote", "https://lite-api.jup.ag/swap/v1/quote"
RUNS_PER_DAY = 48  # every 30 min on GitHub Actions

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

def quote(get, mint, key):
    q = f"?inputMint={SOL}&outputMint={mint}&amount={int(C.TICKET_SOL * 1e9)}&slippageBps=300"
    if key: return get(JUP_KEYED + q, {"x-api-key": key})
    return get(JUP_LITE + q)

def mode_allowed(mode):
    return mode == "scan" or (mode == "fill" and C.FROZEN)

def load_state(d, day):
    p = os.path.join(d, "state.json")
    try: s = json.load(open(p))
    except Exception: s = {}
    if s.get("day") != day: s = dict(day=day, runs=0, checks=0, errors=0, data_failures=0)
    return s

def scan(data_dir, get=http_json, key=None, now=None, mode="scan"):
    now = now or time.time(); day = time.strftime("%Y-%m-%d", time.gmtime(now))
    os.makedirs(data_dir, exist_ok=True)
    run_id = time.strftime("%Y%m%dT%H%M", time.gmtime(now)); seed = int(now) // 60
    st = load_state(data_dir, day); notes = []
    if not key: notes.append("JUPITER_API_KEY missing: using keyless lite-api")
    ls = leads(get, C.LEADS_PER_RUN)
    if not ls: notes.append("no leads (DexScreener empty or unreachable)")
    for mint, src in ls:
        t0 = time.time(); err = False
        try: pair = best_pair(get, mint)
        except Exception: pair, err = None, True
        try: rc = get(RUG.format(mint))
        except Exception: rc, err = None, True
        try: q = quote(get, mint, key)
        except Exception: q, err = None, True
        stype, reason = skips.classify(pair, rc, q, now)
        st["checks"] += 1; st["errors"] += int(err); st["data_failures"] += int(stype == "data")
        ledger.task(data_dir, dict(time=int(now), run_id=run_id, agent="scanner", task="scan", mint=mint, book="",
                    result="pass" if stype is None else "skip", skip_type=stype or "", reason=reason or "",
                    source=src, check_delay_s=round(time.time() - t0, 2), cfg_version=C.CFG_VERSION, seed=seed))
    st["runs"] += 1
    with open(os.path.join(data_dir, "state.json"), "w") as f: json.dump(st, f, indent=1)
    h = ledger.health(data_dir, st["runs"], RUNS_PER_DAY, st["checks"], st["errors"], st["data_failures"], day)
    h.update(mode=mode, cfg_version=C.CFG_VERSION, frozen=C.FROZEN, last_run_utc=run_id, leads_this_run=len(ls),
             runner="github-actions" if os.environ.get("GITHUB_ACTIONS") else "local", notes=notes)
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
    print(json.dumps({k: h[k] for k in ("day", "runs", "checks", "error_rate", "leads_this_run", "notes")}))
    return 0

if __name__ == "__main__":
    sys.exit(main())
