"""CLI: python3 -m sol_forensics --mint <MINT> [--holders] [--hops 3]

The RPC URL is taken from SOLANA_RPC_URL when that variable is set, and otherwise
the public mainnet endpoint. The URL is never printed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

from sol_forensics.engine import (
    analyse,
    load_config,
    make_client,
    recently_reported,
    rpc_url_from_env,
)
from sol_forensics.rpc import PUBLIC_RPC_URL


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="sol_forensics",
        description="Experimental Solana early-buyer forensics. Research only: no trading and no keys.",
    )
    parser.add_argument("--mint", help="Token mint to analyse")
    parser.add_argument("--watchlist", action="store_true", help="Scan mints in the config watchlist")
    parser.add_argument("--config", help="Path to config.json (default: solada/forensics/config.json)")
    parser.add_argument("--holders", action="store_true", help="Also trace owners from getTokenLargestAccounts")
    parser.add_argument("--hops", type=int, help="Funding hops (default from config, usually 3)")
    parser.add_argument("--window-buys", type=int, help="First N buys in the early window")
    parser.add_argument("--window-seconds", type=int, help="Seconds after creation that still count as early")
    parser.add_argument("--budget", type=int, help="Max JSON-RPC calls for this process")
    parser.add_argument("--max-pages", type=int, help="Signature pages when searching for the create transaction")
    parser.add_argument("--wallet-pages", type=int, help="Signature pages per wallet before marking it unresolved_busy")
    parser.add_argument("--top-buyers", type=int, help="Window buyers to trace, after every slot-0 buyer")
    parser.add_argument("--force", action="store_true", help="Run even if a complete report is under 24 hours old")
    parser.add_argument("--skip-recent", action="store_true", help="Skip a mint reported completely in the last 24h")
    args = parser.parse_args(argv)
    if not args.mint and not args.watchlist:
        parser.error("pass --mint or --watchlist")

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = args.config or os.path.join(here, "solada", "forensics", "config.json")
    if not os.path.isfile(config_path):
        print("config not found", file=sys.stderr)
        return 1
    cfg = load_config(config_path)
    url = rpc_url_from_env()
    # Say which source was used, never the URL itself.
    print("rpc:", "SOLANA_RPC_URL" if os.environ.get("SOLANA_RPC_URL") else "public", flush=True)
    if url == PUBLIC_RPC_URL:
        pass
    client = make_client(cfg, url, budget=args.budget)
    # One budget covers every mint in this process. Watchlist runs share it.
    mints = _mints(cfg, args)
    if not mints:
        print("no mints to scan")
        return 0
    index_path = os.path.join(cfg["_base"], (cfg.get("paths") or {}).get("index", "index.jsonl"))
    now = time.time()
    skip_recent = (args.watchlist or args.skip_recent) and not args.force
    exit_code = 0
    for row in mints:
        mint = row["mint"]
        if skip_recent and recently_reported(pathlib.Path(index_path), mint, now=now):
            print(f"skip {mint} (reported in the last 24h)")
            continue
        print(f"analyse {row.get('symbol') or mint}", flush=True)
        report = analyse(
            mint,
            config=cfg,
            rpc=client,
            holders=args.holders,
            progress=True,
            hops=args.hops,
            window_buys=args.window_buys,
            window_seconds=args.window_seconds,
            max_pages=args.max_pages,
            wallet_pages=args.wallet_pages,
            top_buyers=args.top_buyers,
        )
        quality = report["data_quality"]
        summary = report["summary"]
        print(
            f"done {report.get('symbol') or mint} partial={quality['partial']} "
            f"history_too_deep={quality['history_too_deep']} buyers={len(report['buyers'])} "
            f"calls={quality['calls_total']} flags={','.join(summary['flags']) or '-'}",
            flush=True,
        )
    return exit_code


def _mints(cfg, args):
    if args.mint:
        return [{"mint": args.mint.strip(), "symbol": None}]
    rows = []
    for row in cfg.get("watchlist") or []:
        if not row.get("mint"):
            continue
        if row.get("schedule", True):
            rows.append(row)
    return rows


if __name__ == "__main__":
    sys.exit(main())
