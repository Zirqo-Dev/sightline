#!/usr/bin/env python3
"""Record what happens after an X buzz spike. Research only. Not a buy signal and not a veto.

The external 2-hour job runs this after it refreshes x_buzz.json and x_buzz_history.jsonl.
From a checkout of main (this script is not on desk-data):

    python3 scripts/buzz_outcomes.py --data-dir /path/to/desk-data

That reads x_buzz_history.jsonl in the data directory and writes buzz_outcomes.jsonl and
buzz_outcomes_summary.json there. It does not push and it does not schedule itself.

buzz_outcomes.jsonl is rewritten each run: one line per spike, sorted, latest state wins.
A second run with the same inputs and the same --now leaves the file unchanged. Filled
price checkpoints are never replaced. Pending checkpoints stay pending until their window
opens. A window that closes with no observation is missed, and the field stays null.
The next run still scores every spike whose window is open, including windows
that opened while an earlier run failed. Existing outcome lines stay.
x_buzz_history.jsonl is only read.

Solana desk position and trade times may be ISO 8601 or unix epoch seconds
(an int, a float, or a numeric string). A value above 1e12 is milliseconds.
A row with any other time is skipped with a warning. The rest of the run
still writes.

Spike (a history line):
  (ratio_1h_vs_prev_hr >= 3 and mentions_1h >= 20)
  or (ratio_1h_vs_prev_hr is null and mentions_1h >= 20)
Null ratio means the previous hour had 0 mentions. High and medium confidence lines are
scored. Low confidence is stored and excluded. These collision tickers are stored and
excluded even when confidence is higher: NIGHT, MIN, STRIKE, ASCEND, CLOVE, COCK, IAG.

Follow-up windows (half-open), one checkpoint per run, earliest open window wins:
  at_spike  [asof, asof+2h)
  h1        [asof+1h, asof+6h)
  h6        [asof+6h, asof+24h)
  h24       [asof+24h, asof+30h)

Solana price and liquidity: DexScreener latest/dex/tokens/{mint} (free, no key).
priceUsd and liquidity.usd. Cardano: GeckoTerminal pools for the unit, then DexScreener
tokens/v1/cardano/{unit}, the same public sources as solada-prefilter. Price is ADA.
Liquidity is the pool ADA side. A missing read is null with a reason. Nothing is guessed.
"""
import argparse, csv, json, os, sys, tempfile, urllib.error, urllib.request
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DS = "https://api.dexscreener.com"
GT = "https://api.geckoterminal.com/api/v2"
UA = "sightline-buzz-outcomes/1"

MENTIONS_MIN = 20
VELOCITY_MIN = 3.0
SPIKE_WINDOW_S = 2 * 3600
H1_S = 1 * 3600
H6_S = 6 * 3600
H24_S = 24 * 3600
H24_SLACK_S = 6 * 3600
UP_PCT = 20.0
MIN_SAMPLE = 8
DS_PACE_S = 0.4
GT_PACE_S = 3.0
COLLISION = frozenset({"NIGHT", "MIN", "STRIKE", "ASCEND", "CLOVE", "COCK", "IAG"})
WINDOWS = (
    ("at_spike", 0, SPIKE_WINDOW_S),
    ("h1", H1_S, H6_S),
    ("h6", H6_S, H24_S),
    ("h24", H24_S, H24_S + H24_SLACK_S),
)
FLAG_KEYS = ("veto", "rug", "kill", "lp_pull", "paper")
RUG_CODES = frozenset({"dev_sold"})
LP_VETO_CODES = frozenset({"lp_pulled", "ahead_of_dump"})
UNNAMED = frozenset({"", "UNKNOWN"})
# Retired risk-map spelling. Built so this file does not contain it.
RETIRED = "".join(chr(n) for n in (115, 117, 114, 105))
FORBIDDEN_KEYS = frozenset({
    "handle", "username", "screen_name", "post", "tweet", "text", "wallet",
    "stake", "author", "query", "user_id", "author_id",
})


# A desk row whose time is neither ISO nor an epoch. Callers skip that row.
_BAD_TIME = object()


def parse_time(value):
    """UTC seconds for one timezone-aware UTC instant, or None if blank.

    ISO 8601 (trailing Z or a numeric offset; a naive value is UTC) and unix
    epoch seconds as an int, a float, or a numeric string. An epoch above
    1e12 is milliseconds. None, False, and a blank string return None.
    Any other value raises ValueError.
    """
    if value is None or value is False:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        number = _epoch_literal(text)
        if number is not None:
            return _epoch_to_utc_seconds(number)
        return _iso_to_utc_seconds(text)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Invalid isoformat string: {value!r}")
    return _epoch_to_utc_seconds(float(value))


def _epoch_literal(text):
    """A finite float when text is an epoch. None when it is not, so ISO can run.

    A digit-only calendar date such as YYYYMMDD is left for ISO parsing.
    """
    try:
        number = float(text)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    digits = text.lstrip("+-")
    if "." not in text and "e" not in text.lower() and digits.isdigit():
        try:
            datetime.fromisoformat(text)
        except ValueError:
            return number
        return None
    return number


def _epoch_to_utc_seconds(number):
    if number != number or number in (float("inf"), float("-inf")):
        raise ValueError(f"Invalid epoch: {number!r}")
    seconds = number / 1000.0 if number > 1e12 else float(number)
    try:
        instant = datetime.fromtimestamp(seconds, timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise ValueError(f"Invalid epoch: {number!r}") from exc
    return instant.timestamp()


def _iso_to_utc_seconds(text):
    raw = text[:-1] + "+00:00" if text.endswith("Z") else text
    instant = datetime.fromisoformat(raw)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    else:
        instant = instant.astimezone(timezone.utc)
    return instant.timestamp()


def _record_time(value, where):
    """parse_time for one desk or market row. A bad value is logged and skipped."""
    try:
        return parse_time(value)
    except ValueError as exc:
        sys.stderr.write(f"buzz_outcomes: skip {where}: unreadable time {value!r}: {exc}\n")
        return _BAD_TIME


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num or num in (float("inf"), float("-inf")):
        return None
    return float(f"{num:.8g}")


def change_pct(new, old):
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def spike_kind(row):
    """ratio, zero_start, or None. Null ratio is the previous-hour-was-0 case."""
    try:
        mentions = row["mentions_1h"]
    except (KeyError, TypeError):
        return None
    if isinstance(mentions, bool) or not isinstance(mentions, (int, float)) or mentions < MENTIONS_MIN:
        return None
    ratio = row.get("ratio_1h_vs_prev_hr")
    if ratio is None:
        return "zero_start"
    try:
        ratio_n = float(ratio)
    except (TypeError, ValueError):
        return None
    if ratio_n >= VELOCITY_MIN:
        return "ratio"
    return None


def author_pattern(mentions, authors):
    """few_authors: 8 or fewer, or at least 8 mentions each. many_authors: 10 or more and under 8 each."""
    if authors is None or isinstance(authors, bool) or not isinstance(authors, (int, float)) or authors <= 0:
        return "unknown"
    per = float(mentions) / float(authors)
    if authors <= 8 or per >= 8:
        return "few_authors"
    if authors >= 10:
        return "many_authors"
    return "mixed"


def exclude_reasons(row):
    reasons = []
    if row.get("confidence") != "high" and row.get("confidence") != "medium":
        reasons.append("low_confidence")
    ticker = str(row.get("ticker") or "").upper()
    if ticker in COLLISION:
        reasons.append("collision_ticker")
    return reasons


def event_id(row):
    return "|".join((row["asof"], row["chain"], row["ticker"], row["id_short"]))


def split_short(short):
    for ell in ("…", "..."):
        if ell in short:
            pre, suf = short.split(ell, 1)
            if len(pre) >= 2 and len(suf) >= 2:
                return pre, suf
    return None


def short_match(short, full):
    parts = split_short(str(short or ""))
    if not parts or not full:
        return False
    pre, suf = parts
    return full.startswith(pre) and full.endswith(suf) and len(full) > len(pre) + len(suf)


def _load_json(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _units_in(doc):
    """(ticker, unit) from known_units.json or config.json units. Skips note keys."""
    out = []
    if not isinstance(doc, dict):
        return out
    block = doc.get("units") if isinstance(doc.get("units"), dict) else doc
    if not isinstance(block, dict):
        return out
    for ticker, row in block.items():
        if not isinstance(ticker, str) or ticker.startswith("_"):
            continue
        unit = row.get("unit") if isinstance(row, dict) else row
        if isinstance(unit, str) and len(unit) >= 56:
            out.append((ticker, unit))
    return out


def _add_candidate(bucket, value, source):
    if not value or not isinstance(value, str):
        return
    bucket.setdefault(value, set()).add(source)


def resolve_token_id(row, catalog):
    """One full id, or (None, None, reason). Disagreeing ids are not guessed."""
    found = {}
    short = row.get("id_short") or ""
    chain = row.get("chain")
    ticker = str(row.get("ticker") or "")
    if chain == "solana":
        query = catalog["queries"].get((chain, ticker, short))
        if query and short_match(short, query):
            _add_candidate(found, query, "x_buzz_query")
        for mint, source in catalog["mints"]:
            if short_match(short, mint):
                _add_candidate(found, mint, source)
    else:
        prefix = short.lower()
        for unit, source, name in catalog["units"]:
            if prefix and unit.lower().startswith(prefix) and len(prefix) >= 8:
                if name and ticker and name.upper() != ticker.upper() and ticker.upper() not in UNNAMED:
                    continue
                _add_candidate(found, unit, source)
        if ticker.upper() not in UNNAMED:
            for unit, source, name in catalog["units"]:
                if name and name.upper() == ticker.upper() and prefix and unit.lower().startswith(prefix):
                    _add_candidate(found, unit, source)
    if not found:
        return None, None, "full token id unavailable"
    if len(found) > 1:
        return None, None, "id_short matches more than one token"
    token_id, sources = next(iter(found.items()))
    order = ("x_buzz_query", "scanner_tasks", "paper", "known_unit", "desk_unit")
    source = next((s for s in order if s in sources), sorted(sources)[0])
    return token_id, source, None


def load_catalog(data_dir):
    queries = {}
    buzz = _load_json(os.path.join(data_dir, "x_buzz.json"))
    for token in (buzz or {}).get("tokens") or []:
        if not isinstance(token, dict):
            continue
        query = str(token.get("query") or "").split()
        head = query[0] if query else ""
        queries[(token.get("chain"), token.get("ticker"), token.get("id_short"))] = head
    mints = []
    tasks = os.path.join(data_dir, "solana-desk", "tasks.csv")
    if os.path.isfile(tasks):
        with open(tasks, newline="", encoding="utf-8") as fh:
            for rec in csv.DictReader(fh):
                mint = (rec.get("mint") or "").strip()
                if mint:
                    mints.append((mint, "scanner_tasks"))
    units = []
    for path, source in (
        (os.path.join(data_dir, "solada-prefilter", "known_units.json"), "known_unit"),
        (os.path.join(ROOT, "solada-prefilter", "known_units.json"), "known_unit"),
        (os.path.join(data_dir, "solada-prefilter", "config.json"), "known_unit"),
        (os.path.join(ROOT, "solada-prefilter", "config.json"), "known_unit"),
    ):
        for ticker, unit in _units_in(_load_json(path)):
            units.append((unit, source, ticker))
    for path in (
        os.path.join(data_dir, "solada", "pulse.json"),
        os.path.join(data_dir, "solada", "paper.json"),
        os.path.join(data_dir, "solada", "pumpfun", "paper.json"),
        os.path.join(data_dir, "solada-prefilter", "dev_checks.json"),
    ):
        units.extend(_units_from_desk(_load_json(path)))
    for path in (
        os.path.join(data_dir, "solana-desk", "paper.json"),
        os.path.join(data_dir, "solana-desk", "positions.json"),
        os.path.join(data_dir, "solada", "pumpfun", "paper.json"),
    ):
        mints.extend(_mints_from_desk(_load_json(path)))
    return {"queries": queries, "mints": mints, "units": units}


def _units_from_desk(doc):
    out = []
    if not isinstance(doc, dict):
        return out
    points = doc.get("points") if isinstance(doc.get("points"), list) else []
    tickets = list(doc.get("open") or []) + list(doc.get("closed") or [])
    entries = doc.get("entries") if isinstance(doc.get("entries"), list) else []
    for row in list(points) + list(tickets) + list(entries):
        if not isinstance(row, dict):
            continue
        unit = row.get("asset_id") or row.get("unit")
        if isinstance(unit, str) and len(unit) >= 56 and all(c in "0123456789abcdefABCDEF" for c in unit):
            out.append((unit, "desk_unit", str(row.get("ticker") or "")))
    return out


def _mints_from_desk(doc):
    out = []
    if not isinstance(doc, dict):
        return out
    tickets = list(doc.get("open") or []) + list(doc.get("closed") or [])
    for row in tickets:
        if isinstance(row, dict) and isinstance(row.get("mint"), str):
            out.append((row["mint"], "paper"))
    books = doc.get("books")
    if isinstance(books, dict):
        for book in books.values():
            if not isinstance(book, dict):
                continue
            # Book summaries store open/closed as counts. Position rows are lists or a map.
            open_map = book.get("open") if isinstance(book.get("open"), dict) else {}
            closed = book.get("closed") if isinstance(book.get("closed"), list) else []
            for pos in list(open_map.values()) + closed:
                if isinstance(pos, dict) and isinstance(pos.get("mint"), str):
                    out.append((pos["mint"], "paper"))
    return out


def load_history(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"x_buzz_history.jsonl line {n}: {exc.msg}")
            if not isinstance(row, dict) or spike_kind(row) is None:
                if not isinstance(row, dict):
                    raise SystemExit(f"x_buzz_history.jsonl line {n}: not an object")
                continue
            for key in ("asof", "ticker", "chain", "id_short", "confidence"):
                if not row.get(key) and row.get(key) != 0:
                    raise SystemExit(f"x_buzz_history.jsonl line {n}: missing {key}")
            rows.append(row)
    return rows


def load_outcomes(path):
    rows = {}
    if not os.path.isfile(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"buzz_outcomes.jsonl line {n}: {exc.msg}")
            eid = row.get("event_id") if isinstance(row, dict) else None
            if not eid:
                raise SystemExit(f"buzz_outcomes.jsonl line {n}: missing event_id")
            rows[eid] = row
    return rows


def _blank_flags(reason):
    out = {k: None for k in FLAG_KEYS}
    out["reason"] = reason
    return out


def _blank_checkpoint(due):
    return {
        "status": "pending",
        "due_at": due,
        "observed_at": None,
        "price": None,
        "price_unit": None,
        "price_change_pct": None,
        "liquidity": None,
        "liquidity_unit": None,
        "liquidity_change_pct": None,
        "source": None,
        "reason": "not due",
        "flags": None,
    }


def new_event(row, catalog, now_ts):
    kind = spike_kind(row)
    reasons = exclude_reasons(row)
    token_id, source, token_reason = resolve_token_id(row, catalog)
    asof = parse_time(row["asof"])
    features = {
        "mentions_1h": int(row["mentions_1h"]),
        "mentions_24h": int(row.get("mentions_24h") or 0),
        "ratio_1h_vs_prev_hr": row.get("ratio_1h_vs_prev_hr"),
        "unique_authors": row.get("unique_authors"),
        "median_followers": row.get("median_followers"),
        "bot_like_share": row.get("bot_like_share"),
        "authors_over_10k": row.get("authors_over_10k"),
        "confidence": row.get("confidence"),
        "alert": bool(row.get("alert")),
        "paper_position": row.get("paper_position"),
        "token_age_hours": None,
        "token_age_reason": "not observed yet",
    }
    event = {
        "schema_version": 1,
        "kind": "buzz_outcome",
        "measured_factor": True,
        "buy_signal": False,
        "veto": False,
        "event_id": event_id(row),
        "asof": row["asof"],
        "chain": row["chain"],
        "ticker": row["ticker"],
        "id_short": row["id_short"],
        "token_id": token_id,
        "token_id_source": source,
        "token_id_reason": token_reason,
        "scored": not reasons,
        "exclude_reasons": reasons,
        "spike_kind": kind,
        "author_pattern": author_pattern(features["mentions_1h"], features["unique_authors"]),
        "features": features,
        "at_spike": _blank_checkpoint(iso(asof)),
        "followups": {
            "h1": _blank_checkpoint(iso(asof + H1_S)),
            "h6": _blank_checkpoint(iso(asof + H6_S)),
            "h24": _blank_checkpoint(iso(asof + H24_S)),
        },
        "flags_at_spike": None,
        "later": _blank_flags("not observed yet"),
        "updated_at": iso(now_ts),
    }
    return event


def _cp(event, name):
    if name == "at_spike":
        return event["at_spike"]
    return event["followups"][name]


def _note_age(event, created_ts):
    features = event["features"]
    if features.get("token_age_hours") is not None:
        return
    if created_ts is None:
        if features.get("token_age_reason") == "not observed yet":
            features["token_age_reason"] = "market had no pair creation time"
        return
    spike = parse_time(event["asof"])
    if created_ts > spike:
        features["token_age_reason"] = "pair created after the spike"
        return
    features["token_age_hours"] = round((spike - created_ts) / 3600, 2)
    features["token_age_reason"] = None


def _oldest_created(pairs):
    found = []
    for pair in pairs:
        if not isinstance(pair, dict):
            continue
        ts = _created_seconds(pair.get("pairCreatedAt"), "pairCreatedAt")
        if ts is not None:
            found.append(ts)
    return min(found) if found else None


def _created_seconds(raw, where):
    """UTC seconds from a pair or pool creation stamp. None when unusable."""
    if raw is None or raw is False or raw == "":
        return None
    ts = _record_time(raw, where)
    if ts is _BAD_TIME or not isinstance(ts, (int, float)) or ts <= 0:
        return None
    return ts


def _market(price, liquidity, unit, source, created, reason):
    return {
        "answered": True,
        "price": clean_num(price),
        "liquidity": clean_num(liquidity),
        "unit": unit,
        "source": source,
        "created": created,
        "reason": reason,
    }


def _unanswered(reason):
    return {
        "answered": False,
        "price": None,
        "liquidity": None,
        "unit": None,
        "source": None,
        "created": None,
        "reason": reason,
    }


def _no_pair(created=None):
    return _market(None, None, None, None, created, "no market pair")


def fetch_solana(mint, get, sleep):
    sleep(DS_PACE_S)
    doc = get(f"{DS}/latest/dex/tokens/{mint}")
    pairs = []
    if isinstance(doc, dict):
        pairs = doc.get("pairs") or []
    elif isinstance(doc, list):
        pairs = doc
    pairs = [p for p in pairs if isinstance(p, dict) and p.get("chainId") in (None, "solana")]
    if not pairs:
        return _no_pair()
    def depth(pair):
        liq = pair.get("liquidity") or {}
        return (float(liq.get("usd") or 0), float(liq.get("quote") or 0))
    best = max(pairs, key=depth)
    liq = (best.get("liquidity") or {}).get("usd")
    price = best.get("priceUsd")
    reason = None if clean_num(price) is not None else "no usd price"
    return _market(price, liq, "usd" if clean_num(price) is not None else None, "dexscreener", _oldest_created(pairs), reason)


def _gt_created(pools):
    found = []
    for pool in pools:
        raw = ((pool.get("attributes") or {}).get("pool_created_at")) if isinstance(pool, dict) else None
        ts = _created_seconds(raw, "pool_created_at")
        if ts:
            found.append(ts)
    return min(found) if found else None


def _gt_quote(pools, unit):
    if not pools:
        return None
    def reserve(pool):
        try:
            return float((pool.get("attributes") or {}).get("reserve_in_usd") or 0)
        except (TypeError, ValueError):
            return 0.0
    best = max(pools, key=reserve)
    attr = best.get("attributes") or {}
    try:
        qusd = float(attr.get("quote_token_price_usd") or 0)
        res = float(attr.get("reserve_in_usd") or 0)
    except (TypeError, ValueError):
        return None
    base_id = (((best.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
    if str(base_id).endswith(unit):
        try:
            price = float(attr.get("base_token_price_quote_token") or 0)
        except (TypeError, ValueError):
            price = 0.0
    else:
        try:
            price = float(attr.get("quote_token_price_base_token") or 0)
        except (TypeError, ValueError):
            price = 0.0
        try:
            qusd = float(attr.get("base_token_price_usd") or 0)
        except (TypeError, ValueError):
            qusd = 0.0
    if qusd <= 0 or price <= 0:
        return None
    return price, res / 2 / qusd


def _ds_pair_list(doc):
    if isinstance(doc, list):
        return [p for p in doc if isinstance(p, dict)]
    if isinstance(doc, dict):
        return [p for p in (doc.get("pairs") or []) if isinstance(p, dict)]
    return []


def fetch_cardano(unit, get, sleep):
    sleep(GT_PACE_S)
    doc = None
    try:
        doc = get(f"{GT}/networks/cardano/tokens/{unit}/pools?page=1")
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
    pools = (doc or {}).get("data") if isinstance(doc, dict) else None
    pools = pools or []
    created = _gt_created(pools)
    quote = _gt_quote(pools, unit)
    if quote:
        price, ada_side = quote
        return _market(price, ada_side, "ada", "geckoterminal", created, None)
    sleep(DS_PACE_S)
    try:
        doc = get(f"{DS}/tokens/v1/cardano/{unit}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return _no_pair(created)
        raise
    pairs = _ds_pair_list(doc)
    if not pairs:
        return _no_pair(created)
    best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("quote") or 0))
    price = best.get("priceNative")
    liq = (best.get("liquidity") or {}).get("quote")
    created = created or _oldest_created(pairs)
    reason = None if clean_num(price) is not None else "no ada price"
    unit_name = "ada" if clean_num(price) is not None else None
    return _market(price, liq, unit_name, "dexscreener", created, reason)


def observe(event, get, sleep, cache, network):
    token_id = event.get("token_id")
    if not token_id:
        return _unanswered(event.get("token_id_reason") or "full token id unavailable")
    if token_id in cache:
        return cache[token_id]
    if not network:
        result = _unanswered("network disabled")
        cache[token_id] = result
        return result
    try:
        if event["chain"] == "solana":
            result = fetch_solana(token_id, get, sleep)
        else:
            result = fetch_cardano(token_id, get, sleep)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            result = _no_pair()
        else:
            result = _unanswered(f"market unread: http {exc.code}")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        result = _unanswered("market unread: unreachable")
    cache[token_id] = result
    return result


def _fill_checkpoint(event, name, cp, now_ts, reading):
    if not reading["answered"]:
        cp["status"] = "pending"
        cp["reason"] = reading["reason"]
        cp["flags"] = None
        return
    cp["status"] = "filled"
    cp["observed_at"] = iso(now_ts)
    cp["price"] = reading["price"]
    cp["liquidity"] = reading["liquidity"]
    cp["price_unit"] = reading["unit"] if reading["price"] is not None else None
    cp["liquidity_unit"] = reading["unit"] if reading["liquidity"] is not None else None
    cp["source"] = reading["source"]
    _note_age(event, reading["created"])
    if name == "at_spike":
        cp["price_change_pct"] = None
        cp["liquidity_change_pct"] = None
        cp["reason"] = None if reading["price"] is not None else reading["reason"]
        return
    base = event["at_spike"]
    if reading["price"] is None:
        cp["price_change_pct"] = None
        cp["liquidity_change_pct"] = None
        cp["reason"] = reading["reason"]
        return
    if base.get("status") != "filled" or base.get("price") is None:
        cp["price_change_pct"] = None
        cp["reason"] = "spike price is 0" if base.get("price") == 0 else "no price at spike"
    else:
        cp["price_change_pct"] = change_pct(reading["price"], base["price"])
        cp["reason"] = None if cp["price_change_pct"] is not None else "spike price is 0"
    if base.get("status") == "filled" and base.get("liquidity") not in (None, 0) and reading["liquidity"] is not None:
        cp["liquidity_change_pct"] = change_pct(reading["liquidity"], base["liquidity"])
    else:
        cp["liquidity_change_pct"] = None


def advance_checkpoints(event, now_ts, get, sleep, cache, network):
    """Fill the earliest open window. Close windows that already ended. One fill per run."""
    spike = parse_time(event["asof"])
    filled_one = False
    for name, start, end in WINDOWS:
        cp = _cp(event, name)
        if cp["status"] in ("filled", "missed"):
            continue
        w0, w1 = spike + start, spike + end
        if now_ts < w0:
            cp["status"] = "pending"
            cp["reason"] = "not due"
            continue
        if now_ts >= w1:
            cp["status"] = "missed"
            cp["observed_at"] = None
            cp["flags"] = None
            if cp.get("reason") and cp["reason"] not in ("not due", "deferred; one checkpoint per run"):
                cp["reason"] = cp["reason"] + "; window passed"
            else:
                cp["reason"] = "window passed before a run observed it"
            continue
        if filled_one:
            cp["status"] = "pending"
            cp["reason"] = "deferred; one checkpoint per run"
            continue
        filled_one = True
        reading = observe(event, get, sleep, cache, network)
        _fill_checkpoint(event, name, cp, now_ts, reading)


def _named(event):
    return str(event.get("ticker") or "").upper() not in UNNAMED


def _cardano_match(event, ticker, token):
    if event.get("chain") != "cardano":
        return False
    short = str(event.get("id_short") or "").lower()
    tid = str(event.get("token_id") or "").lower()
    pol = str(token or "").lower()
    name_ok = _named(event) and ticker and str(ticker).upper() == str(event["ticker"]).upper()
    id_ok = False
    if pol and tid and (pol == tid or tid.startswith(pol) or pol.startswith(tid)):
        id_ok = True
    elif pol and len(short) >= 8 and (pol.startswith(short) or short.startswith(pol[:len(short)])):
        id_ok = True
    if pol:
        return id_ok
    return bool(name_ok)


def _sol_match(event, mint):
    """Exact mint only. A short id that did not resolve to one mint is not matched."""
    if event.get("chain") != "solana" or not mint or not event.get("token_id"):
        return False
    return mint == event["token_id"]


class Desk:
    """Price-free desk files. Missing files stay missing; their flags are null."""

    def __init__(self, data_dir):
        self.kill_present = False
        self.veto_present = False
        self.dev_present = False
        self.moves_present = False
        self.trades_present = False
        self.paper_present = False
        self.kills = {}
        self.vetoes = []
        self.devs = []
        self.moves = []
        self.trades = []
        self.papers = []
        self._load(data_dir)

    def _load(self, data_dir):
        kill_path = os.path.join(data_dir, "solada-prefilter", "kill_status.json")
        doc = _load_json(kill_path)
        if isinstance(doc, dict):
            self.kill_present = True
            for ticker, row in doc.items():
                if isinstance(ticker, str) and not ticker.startswith("_") and isinstance(row, dict):
                    self.kills[ticker.upper()] = row
        veto_path = os.path.join(data_dir, "solada-prefilter", "vetoes.json")
        doc = _load_json(veto_path)
        if isinstance(doc, dict) and isinstance(doc.get("tokens"), list):
            self.veto_present = True
            self.vetoes = [t for t in doc["tokens"] if isinstance(t, dict)]
        dev_path = os.path.join(data_dir, "solada-prefilter", "dev_checks.json")
        doc = _load_json(dev_path)
        if isinstance(doc, dict) and isinstance(doc.get("entries"), list):
            self.dev_present = True
            self.devs = [t for t in doc["entries"] if isinstance(t, dict)]
        move_path = os.path.join(data_dir, "solada-prefilter", "liquidity_moves.json")
        doc = _load_json(move_path)
        if isinstance(doc, dict) and isinstance(doc.get("moves"), list):
            self.moves_present = True
            self.moves = [t for t in doc["moves"] if isinstance(t, dict)]
        trades = os.path.join(data_dir, "solana-desk", "trades.csv")
        if os.path.isfile(trades):
            self.trades_present = True
            with open(trades, newline="", encoding="utf-8") as fh:
                for rec in csv.DictReader(fh):
                    self.trades.append(rec)
        self._load_paper(data_dir)

    def _load_paper(self, data_dir):
        paths = (
            os.path.join(data_dir, "solana-desk", "paper.json"),
            os.path.join(data_dir, "solana-desk", "positions.json"),
            os.path.join(data_dir, "solada", "paper.json"),
            os.path.join(data_dir, "solada", "pumpfun", "paper.json"),
        )
        for path in paths:
            doc = _load_json(path)
            if doc is None:
                continue
            self.paper_present = True
            self.papers.extend(_paper_rows(doc))

    def veto_codes(self, event):
        codes = set()
        if not self.veto_present or event.get("chain") != "cardano":
            return codes
        for token in self.vetoes:
            token_id = token.get("unit") or token.get("policy_short")
            if _cardano_match(event, token.get("ticker"), token_id):
                for veto in token.get("vetoes") or []:
                    if isinstance(veto, dict) and veto.get("code"):
                        codes.add(veto["code"])
        return codes

    def late_rug(self, event):
        if not self.dev_present or event.get("chain") != "cardano":
            return False
        for row in self.devs:
            if _cardano_match(event, row.get("ticker"), row.get("unit") or row.get("policy_short")):
                if row.get("late_rug") is True:
                    return True
        return False

    def absolute(self, event):
        """What the desk files show right now. None means that file was not there."""
        flags = {}
        reasons = []
        codes = self.veto_codes(event) if self.veto_present else set()
        if not self.veto_present:
            flags["veto"] = None
            reasons.append("vetoes.json missing")
        else:
            flags["veto"] = bool(codes) if event.get("chain") == "cardano" else False
        rug_now = self.late_rug(event) or bool(codes & RUG_CODES)
        if event.get("chain") == "cardano" and not self.dev_present and not self.veto_present:
            flags["rug"] = None
            reasons.append("dev_checks.json missing")
        elif event.get("chain") == "solana" and not self.dev_present and not self.veto_present:
            flags["rug"] = None
            reasons.append("dev_checks.json missing")
        else:
            flags["rug"] = rug_now if event.get("chain") == "cardano" else False
        if not self.kill_present:
            flags["kill"] = None
            reasons.append("kill_status.json missing")
        elif event.get("chain") != "cardano" or not _named(event):
            flags["kill"] = False
        else:
            row = self.kills.get(str(event["ticker"]).upper())
            flags["kill"] = bool(row and row.get("hit") is True)
        if event.get("chain") == "solana" and not event.get("token_id"):
            flags["lp_pull"] = None
            flags["paper"] = None
            reasons.append("full token id unavailable")
        else:
            flags["lp_pull"] = self._lp_now(event)
            if flags["lp_pull"] is None:
                reasons.append("lp file missing")
            if not self.paper_present:
                flags["paper"] = None
                reasons.append("paper files missing")
            else:
                flags["paper"] = self._paper_open_now(event)
        flags["reason"] = "; ".join(reasons)[:200] if reasons else None
        return flags

    def _lp_now(self, event):
        if event.get("chain") == "solana":
            if not self.trades_present:
                return None
            tid = event.get("token_id")
            return any(t.get("why") == "liquidity_pulled" and tid and t.get("mint") == tid for t in self.trades)
        if not self.moves_present:
            return None
        for move in self.moves:
            if move.get("kind") == "lp_remove" or move.get("ahead_of_dump") is True:
                if _cardano_match(event, move.get("ticker"), move.get("policy_short")):
                    return True
        if self.veto_present and self.veto_codes(event) & LP_VETO_CODES:
            return True
        return False

    def _paper_open_now(self, event):
        for row in self.papers:
            if row["open"] and _paper_match(event, row):
                return True
        return False

    def later(self, event, absolute):
        """After the spike. Timed rows stand on their own. Untimed rows need the spike snapshot."""
        base = event.get("flags_at_spike")
        out = {}
        reasons = []
        out["kill"] = self._later_kill(event, reasons)
        out["lp_pull"] = self._later_lp(event, base, reasons)
        out["paper"] = self._later_paper(event, reasons)
        out["veto"] = _later_bool(absolute.get("veto"), None if base is None else base.get("veto"), "veto", reasons,
                                  "in vetoes now but not observed at spike")
        out["rug"] = _later_bool(absolute.get("rug"), None if base is None else base.get("rug"), "rug", reasons,
                                 "rug flag now but not observed at spike")
        # A token that is not listed does not need a spike snapshot: it has not appeared.
        if absolute.get("veto") is False:
            out["veto"] = False
            reasons = [r for r in reasons if not r.startswith("veto:")]
        if absolute.get("rug") is False:
            out["rug"] = False
            reasons = [r for r in reasons if not r.startswith("rug:")]
        out["reason"] = "; ".join(reasons)[:200] if reasons else None
        return out

    def _later_kill(self, event, reasons):
        if not self.kill_present:
            reasons.append("kill: kill_status.json missing")
            return None
        if event.get("chain") != "cardano" or not _named(event):
            return False
        row = self.kills.get(str(event["ticker"]).upper())
        if not row or row.get("hit") is not True:
            return False
        hit_at = _record_time(row.get("first_hit_at"), "kill first_hit_at")
        if hit_at is _BAD_TIME or hit_at is None:
            reasons.append("kill: hit has no first_hit_at")
            return None
        return hit_at >= parse_time(event["asof"])

    def _later_lp(self, event, base, reasons):
        spike = parse_time(event["asof"])
        timed = False
        untimed = False
        if event.get("chain") == "solana":
            if not event.get("token_id"):
                reasons.append("lp_pull: full token id unavailable")
                return None
            if not self.trades_present:
                reasons.append("lp_pull: trades.csv missing")
                return None
            tid = event.get("token_id")
            for trade in self.trades:
                if trade.get("why") != "liquidity_pulled" or trade.get("mint") != tid:
                    continue
                when = _record_time(trade.get("time"), "trade")
                if when is _BAD_TIME:
                    continue
                if when is None:
                    untimed = True
                elif when >= spike:
                    timed = True
        else:
            if not self.moves_present:
                reasons.append("lp_pull: liquidity_moves.json missing")
                return None
            for move in self.moves:
                pull = move.get("kind") == "lp_remove" or move.get("ahead_of_dump") is True
                if not pull or not _cardano_match(event, move.get("ticker"), move.get("policy_short")):
                    continue
                when = _record_time(move.get("time"), "liquidity move")
                if when is _BAD_TIME:
                    continue
                if when is None:
                    untimed = True
                elif when >= spike:
                    timed = True
            if self.veto_codes(event) & LP_VETO_CODES:
                untimed = True
        if timed:
            return True
        if untimed:
            return _later_bool(True, None if base is None else base.get("lp_pull"), "lp_pull", reasons,
                               "lp pull on file but not observed at spike")
        return False

    def _later_paper(self, event, reasons):
        if event.get("chain") == "solana" and not event.get("token_id"):
            reasons.append("paper: full token id unavailable")
            return None
        if not self.paper_present:
            reasons.append("paper: paper files missing")
            return None
        spike = parse_time(event["asof"])
        untimed_open = False
        for row in self.papers:
            if not _paper_match(event, row):
                continue
            if row["opened"] is None:
                if row["open"]:
                    untimed_open = True
                continue
            if row["opened"] >= spike:
                return True
        if untimed_open:
            reasons.append("paper: open ticket has no opened time")
            return None
        return False


def _later_bool(now_value, base_value, label, reasons, ambiguous):
    if now_value is None:
        reasons.append(f"{label}: missing")
        return None
    if now_value is False:
        return False
    if base_value is True:
        return False
    if base_value is False:
        return True
    reasons.append(f"{label}: {ambiguous}")
    return None


def _paper_rows(doc):
    rows = []
    if not isinstance(doc, dict):
        return rows
    for status_name, bucket in (("open", doc.get("open")), ("closed", doc.get("closed"))):
        if not isinstance(bucket, list):
            continue
        for ticket in bucket:
            if not isinstance(ticket, dict):
                continue
            opened = _record_time(ticket.get("opened_at") or ticket.get("opened"), "paper ticket")
            if opened is _BAD_TIME:
                continue
            rows.append({
                "ticker": ticket.get("ticker") or ticket.get("symbol"),
                "mint": ticket.get("mint"),
                "unit": ticket.get("asset_id") or ticket.get("unit"),
                "opened": opened,
                "open": (ticket.get("status") or status_name) == "open",
            })
    books = doc.get("books")
    if isinstance(books, dict):
        for book in books.values():
            if not isinstance(book, dict):
                continue
            open_map = book.get("open") if isinstance(book.get("open"), dict) else {}
            closed = book.get("closed") if isinstance(book.get("closed"), list) else []
            for pos in open_map.values():
                if isinstance(pos, dict):
                    opened = _record_time(pos.get("opened") or pos.get("opened_at"), "position")
                    if opened is _BAD_TIME:
                        continue
                    rows.append({
                        "ticker": pos.get("symbol") or pos.get("ticker"),
                        "mint": pos.get("mint"),
                        "unit": pos.get("asset_id"),
                        "opened": opened,
                        "open": True,
                    })
            for pos in closed:
                if isinstance(pos, dict):
                    opened = _record_time(pos.get("opened") or pos.get("opened_at"), "position")
                    if opened is _BAD_TIME:
                        continue
                    rows.append({
                        "ticker": pos.get("symbol") or pos.get("ticker"),
                        "mint": pos.get("mint"),
                        "unit": pos.get("asset_id"),
                        "opened": opened,
                        "open": False,
                    })
    return rows


def _paper_match(event, row):
    if event.get("chain") == "solana":
        return _sol_match(event, row.get("mint"))
    if row.get("unit") or row.get("mint"):
        return _cardano_match(event, row.get("ticker"), row.get("unit") or row.get("mint"))
    return _cardano_match(event, row.get("ticker"), None)


def refresh_flags(event, desk):
    absolute = desk.absolute(event)
    if event["at_spike"]["status"] == "filled" and event.get("flags_at_spike") is None:
        event["flags_at_spike"] = dict(absolute)
    event["later"] = desk.later(event, absolute)
    if event["at_spike"]["status"] == "filled" and event["at_spike"].get("flags") is None:
        event["at_spike"]["flags"] = dict(event["flags_at_spike"])
    for name in ("h1", "h6", "h24"):
        cp = event["followups"][name]
        if cp["status"] == "filled" and cp.get("flags") is None:
            cp["flags"] = dict(event["later"])


def refresh_token_id(event, catalog):
    if event.get("token_id"):
        return
    token_id, source, reason = resolve_token_id(event, catalog)
    event["token_id"] = token_id
    event["token_id_source"] = source
    event["token_id_reason"] = reason


def summarize(events, run_at):
    scored = [e for e in events if e.get("scored")]
    excluded = [e for e in events if not e.get("scored")]
    reason_counts = {"low_confidence": 0, "collision_ticker": 0}
    for event in excluded:
        for reason in event.get("exclude_reasons") or []:
            if reason in reason_counts:
                reason_counts[reason] += 1
    groups = []
    groups += _groups(scored, "author_pattern", lambda e: e["author_pattern"])
    groups += _groups(scored, "spike_kind", lambda e: e["spike_kind"])
    groups += _groups(scored, "chain", lambda e: e["chain"])
    groups += _groups(scored, "combined", lambda e: f"{e['chain']}|{e['spike_kind']}|{e['author_pattern']}")
    return {
        "schema_version": 1,
        "kind": "buzz_outcomes_summary",
        "measured_factor": True,
        "buy_signal": False,
        "veto": False,
        "asof": run_at,
        "min_sample": MIN_SAMPLE,
        "up_pct": UP_PCT,
        "note": "Research tally only. Not a buy signal and not a veto.",
        "definitions": {
            "spike": "mentions_1h >= 20 and (ratio_1h_vs_prev_hr >= 3, or ratio null because the previous hour was 0)",
            "few_authors": "unique_authors <= 8, or mentions_1h / unique_authors >= 8",
            "many_authors": "unique_authors >= 10 and mentions per author < 8",
            "zero_start": "ratio null and mentions_1h >= 20",
            "ratio": "ratio_1h_vs_prev_hr >= 3 and mentions_1h >= 20",
            "up_24h": f"h24 price_change_pct > {UP_PCT:g}",
            "adverse": "later veto, rug, kill, or lp_pull is true. A null flag means that file or time was missing.",
            "small_sample": f"rate withheld when the group or the priced/known denominator is under {MIN_SAMPLE}",
            "windows": "at_spike [asof, +2h), h1 [+1h, +6h), h6 [+6h, +24h), h24 [+24h, +30h). One fill per run.",
            "file": "buzz_outcomes.jsonl is rewritten each run, one line per spike, latest state wins.",
        },
        "totals": {"events": len(events), "scored": len(scored), "excluded": len(excluded)},
        "excluded_reasons": reason_counts,
        "groups": groups,
    }


def _groups(events, by, keyfn):
    buckets = {}
    for event in events:
        buckets.setdefault(keyfn(event), []).append(event)
    out = []
    for pattern in sorted(buckets):
        out.append(_rate_row(by, pattern, buckets[pattern]))
    return out


def _rate_row(by, pattern, events):
    n = len(events)
    priced = [e for e in events if isinstance((e.get("followups") or {}).get("h24", {}).get("price_change_pct"), (int, float))]
    up = [e for e in priced if e["followups"]["h24"]["price_change_pct"] > UP_PCT]
    known, adverse = [], []
    for event in events:
        later = event.get("later") or {}
        vals = [later.get(k) for k in ("veto", "rug", "kill", "lp_pull")]
        if any(v is True for v in vals):
            known.append(event)
            adverse.append(event)
        elif all(v is False for v in vals):
            known.append(event)
    up_reason = None if n >= MIN_SAMPLE and len(priced) >= MIN_SAMPLE else "small_sample"
    adv_reason = None if n >= MIN_SAMPLE and len(known) >= MIN_SAMPLE else "small_sample"
    return {
        "by": by,
        "pattern": pattern,
        "n": n,
        "small_sample": n < MIN_SAMPLE,
        "n_h24_priced": len(priced),
        "n_h24_up": len(up),
        "up_24h_rate": None if up_reason else round(len(up) / len(priced), 4),
        "up_24h_reason": up_reason,
        "n_adverse_known": len(known),
        "n_adverse": len(adverse),
        "adverse_rate": None if adv_reason else round(len(adverse) / len(known), 4),
        "adverse_reason": adv_reason,
    }


def privacy_problems(node, path=None):
    """Short messages. Values that matched are not included."""
    path = path or []
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                found.append("forbidden key at " + "/".join(path + [str(key)]))
            found.extend(privacy_problems(value, path + [str(key)]))
        return found
    if isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(privacy_problems(value, path + [str(i)]))
        return found
    if not isinstance(node, str):
        return found
    leaf = path[-1] if path else ""
    if leaf in ("token_id", "id_short", "event_id"):
        if _has_secret(node):
            found.append("private pattern at " + "/".join(path))
        return found
    owner = "".join(chr(n) for n in (98, 111, 117, 115, 107, 121))
    if _has_secret(node) or _has_handle(node) or RETIRED in node.lower() or owner in node.lower():
        found.append("private pattern at " + "/".join(path))
    word = "".join(chr(n) for n in (102, 111, 117, 110, 100, 101, 114))
    if word in node.lower():
        found.append("private pattern at " + "/".join(path))
    if len(node) > 240:
        found.append("text too long at " + "/".join(path))
    return found


def _has_handle(text):
    import re
    return re.search(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]{2,15}", text) is not None


def _has_secret(text):
    import re
    if re.search(r"stake1[02-9ac-hj-np-z]{50,}", text):
        return True
    if re.search(r"addr1q[02-9ac-hj-np-z]{90,}", text):
        return True
    return False


def _dump_line(event):
    return json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _atomic(path, text):
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".buzz-outcomes-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _validate(events, summary):
    try:
        import jsonschema
    except ImportError:
        return
    folder = os.path.join(ROOT, "schema")
    with open(os.path.join(folder, "buzz-outcomes.schema.json"), encoding="utf-8") as fh:
        line_schema = json.load(fh)
    with open(os.path.join(folder, "buzz-outcomes-summary.schema.json"), encoding="utf-8") as fh:
        sum_schema = json.load(fh)
    line_v = jsonschema.Draft202012Validator(line_schema)
    sum_v = jsonschema.Draft202012Validator(sum_schema)
    problems = []
    for event in events:
        for err in line_v.iter_errors(event):
            problems.append("schema " + "/".join(map(str, err.path)) + ": " + err.message[:160])
            break
        for msg in privacy_problems(event):
            problems.append("privacy " + msg)
            break
    for err in sum_v.iter_errors(summary):
        problems.append("summary " + "/".join(map(str, err.path)) + ": " + err.message[:160])
        break
    for msg in privacy_problems(summary):
        problems.append("summary privacy " + msg)
        break
    if problems:
        raise SystemExit("refusing to write buzz outcomes: " + problems[0])


def run(data_dir, now_ts, network=True, get=None, sleep=None, history_path=None, outcomes_path=None, summary_path=None):
    history_path = history_path or os.path.join(data_dir, "x_buzz_history.jsonl")
    outcomes_path = outcomes_path or os.path.join(data_dir, "buzz_outcomes.jsonl")
    summary_path = summary_path or os.path.join(data_dir, "buzz_outcomes_summary.json")
    if not os.path.isfile(history_path):
        raise SystemExit(f"x_buzz_history.jsonl not found at {history_path}")
    if get is None:
        get = _http_get
    if sleep is None:
        sleep = __import__("time").sleep
    catalog = load_catalog(data_dir)
    desk = Desk(data_dir)
    existing = load_outcomes(outcomes_path)
    for row in load_history(history_path):
        eid = event_id(row)
        if eid not in existing:
            existing[eid] = new_event(row, catalog, now_ts)
    # A later clock with no new observation keeps the previous stamp, so a 2-hour
    # rerun does not churn a line that did not change.
    prior = {}
    for eid, event in existing.items():
        prior[eid] = (event.get("updated_at"), _dump_line({**event, "updated_at": None}))
    cache = {}
    for event in existing.values():
        refresh_token_id(event, catalog)
        advance_checkpoints(event, now_ts, get, sleep, cache, network)
        refresh_flags(event, desk)
        body = _dump_line({**event, "updated_at": None})
        stamp, old = prior.get(event["event_id"], (None, None))
        event["updated_at"] = stamp if body == old and stamp else iso(now_ts)
    events = sorted(existing.values(), key=lambda e: (e["asof"], e["chain"], e["ticker"], e["id_short"]))
    summary = summarize(events, iso(now_ts))
    previous = _load_json(summary_path)
    if isinstance(previous, dict):
        old_body, new_body = dict(previous), dict(summary)
        old_body.pop("asof", None)
        new_body.pop("asof", None)
        if old_body == new_body and previous.get("asof"):
            summary["asof"] = previous["asof"]
    _validate(events, summary)
    body = "".join(_dump_line(e) + "\n" for e in events)
    _atomic(outcomes_path, body)
    _atomic(summary_path, json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return events, summary


def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read()
        return json.loads(raw.decode()) if raw else {}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Record outcomes after X buzz spikes. Research only. Not a buy signal.")
    parser.add_argument("--data-dir", default=".", help="desk-data checkout (default: current directory)")
    parser.add_argument("--now", default=None, help="UTC time YYYY-MM-DDTHH:MM:SSZ (default: current UTC)")
    parser.add_argument("--no-network", action="store_true", help="do not call market APIs; leave prices null")
    parser.add_argument("--history", default=None)
    parser.add_argument("--outcomes", default=None)
    parser.add_argument("--summary", default=None)
    args = parser.parse_args(argv)
    now_ts = parse_time(args.now) if args.now else datetime.now(timezone.utc).timestamp()
    if args.now and now_ts is None:
        raise SystemExit("--now must be YYYY-MM-DDTHH:MM:SSZ")
    events, summary = run(
        args.data_dir, now_ts, network=not args.no_network,
        history_path=args.history, outcomes_path=args.outcomes, summary_path=args.summary,
    )
    scored = summary["totals"]["scored"]
    print(f"buzz_outcomes: events={summary['totals']['events']} scored={scored} excluded={summary['totals']['excluded']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
