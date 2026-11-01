#!/usr/bin/env python3
"""G8 report: mint and freeze authority on PumpSwap pools that pass every other S2 rule.

Report only. It does not change skips, entries, exits, or the authority rule.
One snapshot has no earlier k, so the k rule passes when reserves parse.
The script prints status revoked or set. It does not print authority pubkeys,
RPC URLs, or API keys.

Usage (from the repo root):
    python3 scripts/g8_authorities.py
    python3 scripts/g8_authorities.py --want 20 --inspect-cap 80
"""
import argparse, json, os, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "solana-desk"))
sys.path.insert(0, ROOT)

from desk import observe, run, v2log  # noqa: E402
from sol_forensics.rpc import PUBLIC_RPC_URL, RpcClient  # noqa: E402

DEX = "https://api.dexscreener.com"
RUG = "https://api.rugcheck.xyz/v1/tokens/{}/report"
SOURCES = (
    "/token-boosts/top/v1",
    "/token-boosts/latest/v1",
    "/token-profiles/latest/v1",
    "/latest/dex/search?q=pump",
)


def mints_in(payload):
    """Solana mints from a DexScreener list or a search result. Order kept."""
    if isinstance(payload, dict):
        items = payload.get("pairs") or payload.get("tokens") or []
    elif isinstance(payload, list):
        items = payload
    else:
        return []
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        chain = item.get("chainId")
        if chain not in (None, "solana"):
            continue
        if chain is None and "baseToken" in item:
            continue
        mint = item.get("tokenAddress")
        if not isinstance(mint, str) or not mint:
            base = item.get("baseToken")
            mint = base.get("address") if isinstance(base, dict) else None
        mint = v2log.public_id(mint)
        if mint:
            out.append(mint)
    return out


def pair_screen(pair, now):
    """Pair-only S2 checks. False skips the RugCheck call."""
    if not isinstance(pair, dict):
        return False
    if observe.graduated_pump(pair) is not True:
        return False
    created = observe.pair_created_s(pair)
    if created is None or float(now) - float(created) < v2log.S2_MIN_AGE_S:
        return False
    sol, _token, _k, _err = observe.snapshot_reserves(pair)
    if sol is None or sol < v2log.S2_MIN_SOL:
        return False
    swaps = v2log._swaps(v2log.txns_h1(pair))
    sells = None
    h1 = v2log.txns_h1(pair)
    if isinstance(h1, dict):
        sells = v2log._num(h1.get("sells"))
    return swaps is not None and swaps >= v2log.S2_MIN_TRADES and sells is not None and sells >= v2log.S2_MIN_SELLS


def authority_status(result):
    """revoked or set from a getAccountInfo jsonParsed mint. Unknown when the account does not parse.
    Pubkeys are not returned.
    """
    unknown = {"mint_authority": "unknown", "freeze_authority": "unknown"}
    if not isinstance(result, dict):
        return unknown
    value = result.get("value")
    if not isinstance(value, dict):
        return unknown
    data = value.get("data")
    info = None
    if isinstance(data, dict):
        parsed = data.get("parsed")
        if isinstance(parsed, dict) and isinstance(parsed.get("info"), dict):
            info = parsed["info"]
    if not isinstance(info, dict):
        return unknown
    return {
        "mint_authority": "revoked" if not info.get("mintAuthority") else "set",
        "freeze_authority": "revoked" if not info.get("freezeAuthority") else "set",
    }


def sample(get, rpc_call, now, *, want=20, inspect_cap=80, sleep=None):
    """Inspect up to inspect_cap mints. Stop once want pools pass every S2 rule except authority.

    get(url) is DexScreener or RugCheck. rpc_call(mint) is getAccountInfo's result, or raises.
    Returns a report. Does not write a file and does not change a rule.
    """
    if sleep is None:
        sleep = lambda _s: None
    want = max(0, int(want))
    inspect_cap = max(0, int(inspect_cap))
    seen, mints = set(), []
    source_calls = 0
    for path in SOURCES:
        source_calls += 1
        try:
            payload = get(DEX + path)
        except Exception:
            continue
        for mint in mints_in(payload):
            if mint in seen:
                continue
            seen.add(mint)
            mints.append(mint)
    inspected = 0
    pair_calls = 0
    rug_calls = 0
    rpc_calls = 0
    rows = []
    for mint in mints:
        if inspected >= inspect_cap or len(rows) >= want:
            break
        inspected += 1
        try:
            sleep(0.3)
            pair_calls += 1
            pair = run.best_pair(get, mint)
        except Exception:
            continue
        if not pair_screen(pair, now):
            continue
        try:
            sleep(1.0)
            rug_calls += 1
            rc = get(RUG.format(mint))
        except Exception:
            continue
        try:
            inputs = v2log._inputs_from(pair, rc if isinstance(rc, dict) else None, now)
            point = {"t_s": int(now), "k": inputs.get("k"), "pool": inputs.get("pairAddress")}
            ok, _rules = v2log.passes_other_s2(inputs, [point])
        except Exception:
            continue
        if not ok:
            continue
        status = {"mint_authority": "unknown", "freeze_authority": "unknown"}
        try:
            sleep(0.5)
            rpc_calls += 1
            status = authority_status(rpc_call(mint))
        except Exception:
            status = {"mint_authority": "unknown", "freeze_authority": "unknown"}
        rows.append({
            "mint": mint,
            "pool": inputs.get("pairAddress"),
            "mint_authority": status["mint_authority"],
            "freeze_authority": status["freeze_authority"],
        })
    revoked = sum(1 for row in rows if row["mint_authority"] == "revoked" and row["freeze_authority"] == "revoked")
    unsettled = sum(1 for row in rows if row["mint_authority"] == "unknown" or row["freeze_authority"] == "unknown")
    complete = len(rows) >= want and want > 0
    all_revoked = complete and revoked == len(rows) and unsettled == 0
    if not complete:
        recommendation = "incomplete"
    elif all_revoked:
        recommendation = "drop_authority_rule"
    else:
        recommendation = "keep_authority_rule"
    return {
        "want": want,
        "sampled": len(rows),
        "inspected": inspected,
        "mints_seen": len(mints),
        "source_calls": source_calls,
        "pair_calls": pair_calls,
        "rug_calls": rug_calls,
        "rpc_calls": rpc_calls,
        "revoked": revoked,
        "unknown": unsettled,
        "all_revoked": all_revoked,
        "k_history": "one_snapshot",
        "authority_rule_changed": False,
        "recommendation": recommendation,
        "rows": rows,
        "note": "Report only. The S2 mint and freeze rule is unchanged.",
    }


def _rpc_factory(url):
    client = RpcClient(url, budget=40, max_rps=2.0, max_retries=2, timeout=20)

    def call(mint):
        return client.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])

    return call


def main(argv=None):
    parser = argparse.ArgumentParser(description="G8 mint and freeze authority report. Does not change rules.")
    parser.add_argument("--want", type=int, default=20)
    parser.add_argument("--inspect-cap", type=int, default=80)
    args = parser.parse_args(argv)
    url = os.environ.get("SOLANA_RPC_URL") or PUBLIC_RPC_URL
    try:
        report = sample(run.http_json, _rpc_factory(url), time.time(), want=args.want, inspect_cap=args.inspect_cap)
    except Exception:
        print(json.dumps({"error": "lookup_failed", "authority_rule_changed": False}))
        return 1
    json.dump(report, sys.stdout, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
