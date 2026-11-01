"""Build the whale roster from desk-data and write the public summary.

The private roster and event log stay in the Actions cache. This step does not
post a webhook and does not change desk rules. It does not promote a whale.

Candidate events come from tagged big_moves rows and tagged liquidity rows.
When data-dir is a git checkout, older revisions of those files are read too,
and a trade that shows up in several runs is kept once. Wallets stay in the
cache. The public file gets codename, role, status, scores, and the
eligible_for_active flag.

    python3 -m whale_roster.build --repo . --data-dir DIR --cache-dir DIR --public-dir DIR
"""
import argparse
import json
import os
import subprocess
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


def _git_docs(data_dir, rel):
    """Older revisions of one desk-data file. Empty when this dir is not a checkout."""
    try:
        listed = subprocess.run(
            ["git", "-C", data_dir, "rev-list", "HEAD", "--", rel],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if listed.returncode != 0:
        return []
    docs = []
    for commit in listed.stdout.split():
        try:
            shown = subprocess.run(
                ["git", "-C", data_dir, "show", "%s:%s" % (commit, rel)],
                capture_output=True, timeout=60, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if shown.returncode != 0 or not shown.stdout:
            continue
        try:
            doc = json.loads(shown.stdout.decode())
        except (UnicodeError, ValueError):
            continue
        if isinstance(doc, dict):
            docs.append(doc)
    return docs


def _history(data_dir, rel):
    """Working copy first, then git history. Callers dedupe trades."""
    docs = []
    current = _load(os.path.join(data_dir, rel))
    if isinstance(current, dict):
        docs.append(current)
    for doc in _git_docs(data_dir, rel):
        docs.append(doc)
    return docs


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
    liquidity = _load(os.path.join(args.data_dir, "solada-prefilter", "liquidity_moves.json")) or {}
    liq_rows = list(liquidity.get("moves") or [])
    for old in _history(args.data_dir, "solada-prefilter/liquidity_moves.json"):
        liq_rows.extend(old.get("moves") or [])
    liquidity = dict(liquidity)
    liquidity["moves"] = WR.dedupe_rows(liq_rows)
    big_moves = _history(args.data_dir, "solada-prefilter/big_moves.json")
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
        big_moves,
    )
    _write(os.path.join(args.cache_dir, WR.CACHE_ROSTER), roster)
    _write(os.path.join(args.cache_dir, WR.CACHE_EVENTS), {"events": events})
    _write(os.path.join(args.public_dir, WR.PUBLIC_NAME), public)
    n_cand = sum(1 for w in roster.get("whales") or [] if w.get("status") == "candidate")
    print("whale roster: %d whales, %d candidates, %d events" % (len(roster.get("whales") or []), n_cand, len(events)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
