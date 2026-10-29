"""Build the whale roster from desk-data and write the public summary.

The private roster and event log stay in the Actions cache. This step does not
post a webhook and does not change desk rules.

    python3 -m whale_roster.build --repo . --data-dir DIR --cache-dir DIR --public-dir DIR
"""
import argparse
import json
import os
import tempfile
import time

import whale_roster as WR


def _write(path, doc):
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".whale-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _load(path):
    doc = WR.load_json(path)
    return doc


def main(argv=None):
    parser = argparse.ArgumentParser(description="Whale roster research summary")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--public-dir", required=True)
    parser.add_argument("--network", action="store_true")
    parser.add_argument("--max-lookups", type=int, default=8)
    args = parser.parse_args(argv)
    import rugger_blacklist as RB
    if RB._inside(args.cache_dir, args.public_dir) or RB._inside(args.public_dir, args.cache_dir):
        print("::warning::whale roster: cache and public dir overlap; not writing")
        return 1
    log = _load(os.path.join(args.data_dir, "solada", "log.json"))
    ledger = _load(os.path.join(args.data_dir, "solada-prefilter", "whale_ledger.json"))
    liquidity = _load(os.path.join(args.data_dir, "solada-prefilter", "liquidity_moves.json"))
    config = _load(os.path.join(args.repo, "solada-prefilter", "config.json"))
    known = _load(os.path.join(args.repo, "solada-prefilter", "known_units.json"))
    previous = _load(os.path.join(args.public_dir, WR.PUBLIC_NAME))
    cache_roster = _load(os.path.join(args.cache_dir, WR.CACHE_ROSTER))
    events_doc = _load(os.path.join(args.cache_dir, WR.CACHE_EVENTS))
    events = events_doc.get("events") if isinstance(events_doc, dict) else []
    if not isinstance(events, list):
        events = []
    quote = None
    if args.network:
        import whale_roster.market as market
        left = {"n": max(0, args.max_lookups)}
        cache = {}

        def quote(unit):
            if not unit:
                return {"answered": False, "price": None, "liquidity": None, "reason": "full token id unavailable"}
            if unit in cache:
                return cache[unit]
            if left["n"] <= 0:
                return {"answered": False, "price": None, "liquidity": None, "reason": "lookup cap"}
            left["n"] -= 1
            time.sleep(3)
            cache[unit] = market.fetch_cardano(unit)
            return cache[unit]
    roster, events, public = WR.build(
        log, ledger, liquidity, config, known, cache_roster, events, time.time(), quote, WR.social_counts(previous),
    )
    _write(os.path.join(args.cache_dir, WR.CACHE_ROSTER), roster)
    _write(os.path.join(args.cache_dir, WR.CACHE_EVENTS), {"events": events})
    _write(os.path.join(args.public_dir, WR.PUBLIC_NAME), public)
    n_cand = sum(1 for w in roster.get("whales") or [] if w.get("status") == "candidate")
    print("whale roster: %d whales, %d candidates, %d events" % (len(roster.get("whales") or []), n_cand, len(events)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
