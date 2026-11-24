#!/usr/bin/env python3
"""Slim Radar snapshot for /radar.html. Stdlib only.

The phone reads radar.json. This step runs inside the existing Pages build.
It does not change a schedule, a secret, or a scan log.

Solana scan lines have no ticker. Rows are named SOL #1, SOL #2, … from
the raw pool snaps. Mint and pool addresses are dropped before the file is
written. Ages are absolute timestamps; the page computes the labels.

Fetches, each with a 12 second timeout:
health-data/health.json, desk-data x_buzz.json, solada-prefilter flags,
kill status, last-run, and whale_roster, lst-count-data/status.json,
nft-pilot status.json and units.jsonl, HoodScout daily and NFT files,
and the Solana pool-snaps plus manifest for the latest day.

LST input is status.json only. The snapshot keeps written_at, runs_ok, and
chain_ok for the health dot. It does not keep coverage, freeze logs, or any
experiment result.

An NFT row whose raw name or slug credits a creator is left out. ETH floors
are grouped by the raw slug. The file records only how many collections were
left out.
"""
import datetime
import json
import math
import pathlib
import re
import sys
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
DESK = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/desk-data/"
HEALTH_URL = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/health-data/health.json"
LST_URL = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/lst-count-data/status.json"
NFT_STATUS_URL = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/nft-data/nft-pilot/status.json"
UNITS_URL = "https://raw.githubusercontent.com/Zirqo-Dev/sightline/nft-data/nft-pilot/units.jsonl"
HOOD = "https://raw.githubusercontent.com/Zirqo-Dev/hoodscout-data/main/data/"
# Every live fetch uses this socket timeout. A slow source is skipped.
# The Pages build still publishes if the snapshot step fails.
FETCH_TIMEOUT_S = 12
UA = {"user-agent": "sightline-radar"}

_MINT = re.compile(r"[1-9A-HJ-NP-Za-km-z]{32,44}")
_ETH = re.compile(r"0x[0-9a-fA-F]{40}")
_STAKE = re.compile(r"stake1[0-9a-z]{20,}|addr1[0-9a-z]{20,}", re.I)
_BANNED_KEYS = {
    "mint", "pool", "ca", "contract", "asset_id", "unit", "wallet", "address",
    "onset", "d_bps", "d_bps_num", "d_bps_den", "verdict", "nh04_verdict",
    "captures", "tag", "stake", "secret", "api_key", "private_key",
    "freeze_log", "coverage", "coverage_per_lst", "pnl", "positions",
}
_SYMBOL = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._$-]{0,19}")
_FULL_ADDR = re.compile(r"^0x[0-9a-fA-F]{40}$")


def parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc)


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return float(value)


def _clip(value, lo, hi):
    return max(lo, min(hi, value))


def raw_score(move, size, interest):
    """Source-local ranking input. Not a buy signal and not comparable across units."""
    if move is None:
        move_s = 0.35
    else:
        move_s = (_clip(move, -50.0, 300.0) + 50.0) / 350.0
    size_s = 0.0 if not size or size <= 0 else _clip(math.log10(size) / 6.0, 0.0, 1.0)
    interest_s = 0.0 if not interest or interest <= 0 else _clip(math.log10(interest + 1.0) / 3.0, 0.0, 1.0)
    return 0.5 * move_s + 0.3 * size_s + 0.2 * interest_s


def assign_heat(rows):
    """Map the row's own ranking input onto 0–100. Not a buy signal."""
    for row in rows:
        row["heat"] = round(100.0 * max(0.0, min(1.0, row["_raw"])), 1)


def buy_streak(snaps):
    """True when every snap has buys > sells, and there are at least two snaps."""
    if len(snaps) < 2:
        return False
    for snap in snaps:
        buys = snap.get("buys")
        sells = snap.get("sells")
        if not isinstance(buys, (int, float)) or not isinstance(sells, (int, float)):
            return False
        if isinstance(buys, bool) or isinstance(sells, bool) or not buys > sells:
            return False
    return True


def second_leg(prices):
    """Spike, then a dip of at least 12%, then a second push of at least 12%."""
    if len(prices) < 4:
        return False
    start = prices[0]
    if not start or start <= 0:
        return False
    for peak_at, peak in enumerate(prices):
        if peak_at == 0 or peak_at > len(prices) - 3:
            continue
        prior = min(prices[:peak_at])
        if peak < start * 1.15 or peak < prior * 1.15:
            continue
        for dip_at in range(peak_at + 1, len(prices) - 1):
            dip = prices[dip_at]
            if dip > peak * 0.88:
                continue
            for later in prices[dip_at + 1:]:
                if later >= dip * 1.12 and later > dip:
                    return True
    return False


def top10_pct(holders):
    kept = []
    for row in holders or []:
        if isinstance(row, dict):
            if row.get("excluded"):
                continue
            pct = _num(row.get("pct"))
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            if row[1]:
                continue
            pct = _num(row[0])
        else:
            continue
        if pct is not None:
            kept.append(pct)
    if not kept:
        return None
    return sum(kept[:10])


def _https(value):
    if not isinstance(value, str) or not value.startswith("https://"):
        return None
    if _MINT.search(value) or _ETH.search(value) or _STAKE.search(value):
        return None
    return value


def _symbol(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not _SYMBOL.fullmatch(text):
        return None
    if _MINT.fullmatch(text) or _ETH.fullmatch(text):
        return None
    return text


# Creator credits drop the whole NFT row. Nothing is rewritten.
_AT_WORD = re.compile(r"@[^\s@]+")
_PAREN_BY = re.compile(r"\(\s*by\s+[^)]+\)", re.I)
_SLUG_MARK = re.compile(r"[-_]by[-_]", re.I)
_NAME_BY = re.compile(r"\bby\s+\S+", re.I)


def _creator_credit(name, slug):
    """True when a raw name or slug credits a creator. The row is left out."""
    name = name.strip() if isinstance(name, str) else ""
    slug = slug.strip() if isinstance(slug, str) else ""
    for text in (name, slug):
        if text and (_AT_WORD.search(text) or _PAREN_BY.search(text)):
            return True
    for text in (slug, name):
        if text and _SLUG_MARK.search(text):
            return True
    return bool(name and _NAME_BY.search(name))


def _image_path_credit(url):
    """True when the image path, not the query, carries a creator credit."""
    if not isinstance(url, str) or not url.strip():
        return False
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path).casefold()
    return "-by-" in path or "_by_" in path or "@" in path


def _raw_label(name, slug):
    """The source name, or the source slug when the name is blank. Not rewritten."""
    if isinstance(name, str) and name.strip():
        text = name.strip()
    elif isinstance(slug, str) and slug.strip():
        text = slug.strip()
    else:
        return None
    if _MINT.search(text) or _ETH.search(text) or _STAKE.search(text):
        return None
    return text


def _public_id(prefix, slug, name):
    source = slug.strip() if isinstance(slug, str) and slug.strip() else (name or "")
    token = re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")
    if not token:
        return None
    return prefix + token


def _walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _BANNED_KEYS or "d_bps" in lowered or "onset" in lowered or "verdict" in lowered:
                raise ValueError("banned key")
            _walk(item)
    elif isinstance(value, list):
        for item in value:
            _walk(item)
    elif isinstance(value, str):
        if _MINT.search(value) or _ETH.search(value) or _STAKE.search(value):
            raise ValueError("banned string")
        lowered = value.lower()
        if "nh04" in lowered or "captures.jsonl" in lowered:
            raise ValueError("banned text")


def _public_row(row):
    image = row.get("image")
    if image:
        trial = dict(row)
        try:
            _walk(trial)
            return trial
        except ValueError:
            row = dict(row)
            row["image"] = None
    _walk(row)
    return row


def _txns(raw):
    if not isinstance(raw, dict):
        return None, None
    return _num(raw.get("buys")), _num(raw.get("sells"))


def _sample(values, limit=8):
    clean = [value for value in values if isinstance(value, (int, float)) and math.isfinite(value)]
    if len(clean) <= limit:
        return [round(value, 8) for value in clean]
    if limit < 2:
        return [round(clean[-1], 8)]
    out = []
    last = len(clean) - 1
    for index in range(limit):
        at = int(round(index * last / (limit - 1)))
        out.append(round(clean[at], 8))
    return out


def _lst_health(doc):
    """Allowlist only. Anything else in status.json stays out of the snapshot."""
    if not isinstance(doc, dict):
        return {}
    out = {}
    written = doc.get("written_at")
    if isinstance(written, str):
        out["written_at"] = written
    if isinstance(doc.get("runs_ok"), bool):
        out["runs_ok"] = doc["runs_ok"]
    if isinstance(doc.get("chain_ok"), bool):
        out["chain_ok"] = doc["chain_ok"]
    return out


def _workflow(doc, filename, title):
    if not isinstance(doc, dict):
        return None
    for row in doc.get("workflows") or []:
        if not isinstance(row, dict):
            continue
        file_name = str(row.get("file") or "")
        if file_name.endswith(filename) or row.get("name") == title:
            status = row.get("status")
            if status not in ("green", "amber", "red"):
                status = "unknown"
            return {"status": status, "at": row.get("last_run_at") if isinstance(row.get("last_run_at"), str) else None,
                    "name": title}
    return None


def _feed(feed_id, label, status, at, now, detail):
    """Age is not baked. The page computes it from `at`."""
    del now
    return {
        "id": feed_id,
        "label": label,
        "status": status,
        "at": at,
        "detail": detail,
    }


def _iso(when):
    if isinstance(when, (int, float)) and not isinstance(when, bool):
        when = datetime.datetime.fromtimestamp(when, datetime.timezone.utc)
    if not isinstance(when, datetime.datetime):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seen_from_days(generated_at, days):
    base = parse_time(generated_at)
    if base is None or not isinstance(days, (int, float)) or isinstance(days, bool) or days < 0:
        return None
    return _iso(base - datetime.timedelta(days=float(days)))


def _short_addr(value):
    """First and last four characters of a full address. The full value is not kept."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _FULL_ADDR.fullmatch(text):
        return None
    return text[:4] + "…" + text[-4:]


def roster_names(doc):
    """Exact codenames from the public roster. No pattern match."""
    names = set()
    if not isinstance(doc, dict):
        return names
    for whale in doc.get("whales") or []:
        if not isinstance(whale, dict):
            continue
        name = whale.get("codename")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def _disambiguate(rows):
    """Unique ids. Same-ticker rows get a short contract or an age hint."""
    groups = {}
    for row in rows:
        key = (row.get("chain"), str(row.get("name") or "").casefold())
        groups.setdefault(key, []).append(row)
    for group in groups.values():
        if len(group) < 2:
            for row in group:
                row.pop("_hint", None)
            continue
        used = set()
        for index, row in enumerate(group, start=1):
            hint = row.pop("_hint", None)
            if hint and hint not in used:
                row["hint"] = hint
                used.add(hint)
            else:
                row["show_age"] = True
            suffix = re.sub(r"[^a-z0-9]+", "-", (hint or str(index)).lower()).strip("-") or str(index)
            row["id"] = "%s-%s" % (row.get("id") or "row", suffix)
    seen = {}
    for row in rows:
        base = row.get("id") or "row"
        if base in seen:
            seen[base] += 1
            row["id"] = "%s-%d" % (base, seen[base])
        else:
            seen[base] = 1
            row["id"] = base


def _hood_status(at, now):
    parsed = parse_time(at)
    if parsed is None:
        return "unknown"
    hours = (now - parsed).total_seconds() / 3600.0
    if hours <= 26:
        return "green"
    if hours <= 48:
        return "amber"
    return "red"


def _is_rwa(name, symbol):
    blob = "%s %s" % (name or "", symbol or "")
    if re.search(r"courtyard|yield[-\s]?field|real[-\s]?world|\brwa\b", blob, re.I):
        return True
    return str(symbol or "").upper() in ("USDG", "USDC", "USD")


def _flag_index(doc, now):
    """Ticker -> fresh flag kinds. Wallet tags are not copied."""
    found = {}
    if not isinstance(doc, dict):
        return found

    def take(item, force):
        if not isinstance(item, dict):
            return
        ticker = _symbol(item.get("ticker"))
        kind = item.get("kind")
        if not ticker or not isinstance(kind, str):
            return
        when = item.get("at")
        if not force and not (isinstance(when, str) and _fresh(when, now, 48)):
            return
        found.setdefault(ticker, set()).add(kind)

    for item in doc.get("new") or []:
        take(item, True)
    for item in doc.get("recent") or []:
        take(item, False)
    return found


def _fresh(value, now, hours):
    parsed = parse_time(value)
    if parsed is None:
        return False
    return (now - parsed).total_seconds() <= hours * 3600


def _whale_name(doc, allowed):
    """Use a trigger name only when it is exactly one roster codename."""
    allowed = set(allowed or ())
    names = []
    if not isinstance(doc, dict):
        return None
    for item in doc.get("triggers") or []:
        if not isinstance(item, dict) or item.get("kind") != "whale_move":
            continue
        name = item.get("name")
        if isinstance(name, str) and name.strip() in allowed:
            names.append(name.strip())
    unique = sorted(set(names))
    if len(unique) == 1:
        return unique[0]
    return None


def _chips(ids, labels):
    return [{"id": chip_id, "label": labels[chip_id]} for chip_id in ids if chip_id in labels]


def _finish(rows, limit):
    assign_heat(rows)
    ranked = sorted(rows, key=lambda row: (-row["heat"], row["name"]))
    out = []
    for row in ranked[:limit]:
        row.pop("_raw", None)
        try:
            out.append(_public_row(row))
        except ValueError:
            continue
    return out


def _solana_rows(manifest_lines, snap_lines, death_lines=None):
    """Rows from pool snaps only. Death labels and S2 picks are not read."""
    del death_lines
    first = {}
    for line in manifest_lines or []:
        if not isinstance(line, dict) or line.get("type") != "candidate":
            continue
        mint = line.get("mint")
        if not isinstance(mint, str):
            continue
        slot = first.setdefault(mint, {"age_s": None, "t": None, "holders": None})
        inputs = line.get("inputs") if isinstance(line.get("inputs"), dict) else {}
        age = _num(inputs.get("age_s"))
        when = _num(line.get("t_s"))
        if age is not None and (slot["t"] is None or (when is not None and when < slot["t"])):
            slot["age_s"] = age
            slot["t"] = when
        elif slot["age_s"] is None and age is not None:
            slot["age_s"] = age
        holders = inputs.get("holders")
        if isinstance(holders, list):
            slot["holders"] = holders
    seen_mints = set()
    series = {}
    for line in snap_lines or []:
        if not isinstance(line, dict):
            continue
        mint = line.get("mint")
        sol = _num(line.get("sol"))
        token = _num(line.get("token"))
        when = _num(line.get("t_s"))
        # Dust reserves are bad parses (a token amount of 1e-6) and blow up sol/token.
        if not isinstance(mint, str) or sol is None or token is None or when is None:
            continue
        seen_mints.add(mint)
        if sol < 0.05 or token < 1:
            continue
        buys, sells = _txns(line.get("txns_h1"))
        series.setdefault(mint, []).append({
            "t": when, "price": sol / token, "sol": sol, "buys": buys, "sells": sells,
        })
    rows = []
    rejected = set(seen_mints - set(series))
    for mint, points in series.items():
        points.sort(key=lambda item: item["t"])
        prices = [item["price"] for item in points if item["price"] > 0]
        if len(prices) < 2 or max(prices) / min(prices) > 25:
            rejected.add(mint)
            continue
        points = [item for item in points if item["price"] > 0]
        move = (prices[-1] / prices[0] - 1.0) * 100.0
        meta = first.get(mint) or {}
        birth = None
        if meta.get("t") is not None and isinstance(meta.get("age_s"), (int, float)):
            birth = meta["t"] - meta["age_s"]
        elif points:
            birth = points[0]["t"]
        seen_at = _iso(birth) if birth is not None else None
        share = top10_pct(meta.get("holders"))
        tx_snaps = [{"buys": item["buys"], "sells": item["sells"]} for item in points]
        labels = {}
        chip_ids = []
        if seen_at:
            labels["seen"] = "seen"
            chip_ids.append("seen")
        if isinstance(meta.get("age_s"), (int, float)) and meta["age_s"] < 3600:
            labels["pool"] = "new pool"
            chip_ids.append("pool")
        if share is not None and share >= 30:
            labels["heavy"] = "top-heavy"
            chip_ids.append("heavy")
        if points[0]["sol"] < 50:
            labels["thin"] = "thin start"
            chip_ids.append("thin")
        if buy_streak(tx_snaps):
            labels["streak"] = "buy streak"
            chip_ids.append("streak")
        if second_leg(prices):
            labels["leg"] = "second leg"
            chip_ids.append("leg")
        last = points[-1]
        buy_pct = None
        if last["buys"] is not None and last["sells"] is not None and last["buys"] + last["sells"] > 0:
            buy_pct = last["buys"] / (last["buys"] + last["sells"])
        facts = []
        if "pool" in labels:
            facts.append("Pool age at the first log was under 1 hour.")
        if share is not None and share >= 30:
            facts.append("Top 10 holders, excluding the pool, are %s%%." % round(share, 1))
        if "thin" in labels:
            facts.append("Starting pool was under 50 SOL.")
        if "streak" in labels:
            facts.append("Buys beat sells on every 30-minute snap in this window.")
        if "leg" in labels:
            facts.append("Price spiked, dipped, then pushed again.")
        facts.append("Pool reserve moved from %s SOL to %s SOL." % (round(points[0]["sol"], 1), round(last["sol"], 1)))
        interest = last["buys"] if last["buys"] is not None else None
        rows.append({
            "id": "",
            "name": "",
            "seen_at": seen_at,
            "kind": "coin",
            "chain": "SOL",
            "image": None,
            "price": round(prices[-1], 8),
            "price_unit": "SOL",
            "change": round(move, 4),
            "size": round(last["sol"], 4),
            "size_unit": "SOL",
            "size_kind": "liq",
            "buyers": None,
            "buy_pct": None if buy_pct is None else round(buy_pct, 4),
            "stat": "h1 buys %s%%" % round(buy_pct * 100) if buy_pct is not None else "30-minute snaps",
            "spark": _sample(prices),
            "chips": _chips(chip_ids, labels),
            "facts": facts,
            "why": "Solana 30-minute snaps.",
            "source": "Solana 30-minute snaps",
            "_raw": raw_score(move, last["sol"], interest),
            "_mint": mint,
        })
    assign_heat(rows)
    ranked = sorted(rows, key=lambda row: (-row["heat"], -row["_raw"]))[:8]
    out = []
    for index, row in enumerate(ranked, start=1):
        row.pop("_mint", None)
        row.pop("_raw", None)
        row["name"] = "SOL #%d" % index
        row["id"] = "sol-%d" % index
        row["why"] = row["name"]
        try:
            out.append(_public_row(row))
        except ValueError:
            continue
    return out, len(rejected)


def _hood_coins(doc):
    tokens = doc.get("tokens") if isinstance(doc, dict) else None
    rows = []
    for token in tokens or []:
        if not isinstance(token, dict):
            continue
        if token.get("tier") not in ("BEST", "WATCH"):
            continue
        name = _symbol(token.get("symbol") or "")
        if not name:
            continue
        price = _num(token.get("price_usd"))
        change = _num(token.get("chg_h24"))
        if change is None:
            change = _num(token.get("chg_h6"))
        vol = _num(token.get("vol24_usd"))
        liq = _num(token.get("liq_usd"))
        buyers = _num(token.get("buyers24"))
        buys = _num(token.get("buys24"))
        sells = _num(token.get("sells24"))
        days = _num(token.get("age_days"))
        seen_at = _seen_from_days(doc.get("generated_at"), days)
        labels = {}
        chip_ids = []
        if seen_at:
            labels["seen"] = "seen"
            chip_ids.append("seen")
        if days is not None and days < 1:
            labels["pool"] = "new pool"
            chip_ids.append("pool")
        if token.get("watch_reason") == "thin_depth":
            labels["thin"] = "thin start"
            chip_ids.append("thin")
        buy_pct = None
        if buys is not None and sells is not None and buys + sells > 0:
            buy_pct = buys / (buys + sells)
        facts = []
        if "thin" in labels:
            facts.append("HoodScout watch reason is thin depth.")
        if change is not None:
            facts.append("24h move %s%%." % round(change, 2))
        if vol is not None:
            facts.append("24h volume $%s." % format(round(vol), ","))
        spark = token.get("last5_byr24")
        spark_vals = spark if isinstance(spark, list) else []
        rows.append({
            "id": "rh-" + re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
            "name": name,
            "seen_at": seen_at,
            "kind": "coin",
            "chain": "RH",
            "_hint": _short_addr(token.get("ca")),
            "image": _https(token.get("image_url")),
            "price": None if price is None else round(price, 8),
            "price_unit": "USD",
            "change": None if change is None else round(change, 4),
            "size": None if vol is None else round(vol, 2),
            "size_unit": "USD",
            "size_kind": "vol",
            "liq": None if liq is None else round(liq, 2),
            "buyers": None if buyers is None else round(buyers, 2),
            "buy_pct": None if buy_pct is None else round(buy_pct, 4),
            "stat": "%s buyers" % format(int(buyers), ",") if buyers is not None else "Robinhood coin",
            "spark": _sample([item for item in spark_vals if isinstance(item, (int, float))]),
            "chips": _chips(chip_ids, labels),
            "facts": facts or ["HoodScout daily file."],
            "why": "%s · %s" % (name, token.get("tier")),
            "source": "HoodScout daily",
            "_raw": raw_score(change, vol or liq, buyers),
        })
    return _finish(rows, 10)


def _hood_nfts(doc):
    """Kept HoodScout NFT rows, and how many collections were left out."""
    collections = doc.get("collections") if isinstance(doc, dict) else None
    rows = []
    hidden = 0
    for item in collections or []:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name") if isinstance(item.get("name"), str) else ""
        raw_slug = item.get("slug") if isinstance(item.get("slug"), str) else ""
        if _creator_credit(raw_name, raw_slug) or _image_path_credit(item.get("image_url")):
            hidden += 1
            continue
        name = _raw_label(raw_name, raw_slug)
        row_id = _public_id("nft-", raw_slug, name)
        if not name or not row_id or _is_rwa(name, item.get("floor_symbol")):
            continue
        change = _num(item.get("floor_chg_pct"))
        floor = _num(item.get("floor_price"))
        vol = _num(item.get("volume_24h"))
        sales = _num(item.get("sales_24h"))
        symbol = item.get("floor_symbol") if isinstance(item.get("floor_symbol"), str) else "ETH"
        if _ETH.fullmatch(symbol) or len(symbol) > 8:
            symbol = "ETH"
        seen_at = _seen_from_days(doc.get("generated_at"), _num(item.get("age_days")))
        labels = {}
        chip_ids = []
        if seen_at:
            labels["seen"] = "seen"
            chip_ids.append("seen")
        if item.get("flag_new") is True:
            labels["pool"] = "new"
            chip_ids.append("pool")
        facts = ["HoodScout NFT row."]
        if change is None:
            facts.append("Floor change is blank, so the pill is an em dash.")
        else:
            facts.append("Floor change %s%%." % round(change, 2))
        if floor is not None:
            facts.append("Floor %s %s." % (round(floor, 4), symbol))
        rows.append({
            "id": row_id,
            "name": name,
            "seen_at": seen_at,
            "kind": "nft",
            "chain": "RH",
            "image": _https(item.get("image_url")),
            "price": None if floor is None else round(floor, 8),
            "price_unit": symbol,
            "change": None if change is None else round(change, 4),
            "size": None if vol is None else round(vol, 4),
            "size_unit": symbol,
            "size_kind": "vol",
            "buyers": None,
            "buy_pct": None,
            "stat": "%s sales" % format(int(sales), ",") if sales is not None else "floor",
            "spark": [],
            "chips": _chips(chip_ids, labels),
            "facts": facts,
            "why": name,
            "source": "HoodScout NFT",
            "_raw": raw_score(change, vol, sales),
        })
    return _finish(rows, 6), hidden


def _cardano_rows(last_run, flags, kill, whale, now):
    results = last_run.get("results") if isinstance(last_run, dict) else None
    passed = set()
    for ticker in (last_run or {}).get("passed_k_y") or []:
        safe = _symbol(ticker)
        if safe:
            passed.add(safe)
    flagged = _flag_index(flags, now)
    rows = []
    for item in results or []:
        if not isinstance(item, dict):
            continue
        name = _symbol(item.get("ticker"))
        if not name:
            continue
        price = _num(item.get("price_ada"))
        vol = _num(item.get("vol_h24_ada"))
        holders = _num(item.get("native_holders"))
        side = _num(item.get("ada_side"))
        kinds = flagged.get(name) or set()
        labels = {}
        chip_ids = []
        if name in passed:
            labels["desk"] = "desk"
            chip_ids.append("desk")
        if "whale_move" in kinds:
            labels["whale"] = "whale"
            chip_ids.append("whale")
        if "sniper_full_exit" in kinds:
            labels["snipers"] = "snipers out"
            chip_ids.append("snipers")
        if "coordinated_buys" in kinds:
            labels["coord"] = "coordinated"
            chip_ids.append("coord")
        if "holder_concentration" in kinds or "concentration_jump" in kinds:
            labels["heavy"] = "top-heavy"
            chip_ids.append("heavy")
        kill_row = kill.get(name) if isinstance(kill, dict) else None
        if isinstance(kill_row, dict) and kill_row.get("hit") is True:
            labels["skull"] = "kill line"
            chip_ids.append("skull")
        facts = ["Cardano last-run row."]
        if vol is not None:
            facts.append("24h volume %s ADA." % format(int(vol), ","))
        if holders is not None:
            facts.append("%s holders." % format(int(holders), ","))
        if side is not None:
            facts.append("ADA side %s." % format(int(side), ","))
        if "whale" in labels:
            facts.append("Whale move%s." % (": %s" % whale if whale else ""))
        if "snipers" in labels:
            facts.append("A tracked sniper exit is on the flag file.")
        if "coord" in labels:
            facts.append("Coordinated buys are on the flag file.")
        if "heavy" in labels:
            facts.append("Holder concentration is on the flag file.")
        if "skull" in labels:
            metric = kill_row.get("metric") if isinstance(kill_row.get("metric"), str) else ""
            if re.fullmatch(r"[a-z_]{3,24}", metric or ""):
                facts.append("Kill line crossed on %s." % metric.replace("_", " "))
            else:
                facts.append("Kill line is crossed.")
        if "desk" in labels:
            facts.append("Passed the desk screen this run.")
        rows.append({
            "id": "ada-" + name.lower(),
            "name": name,
            "age": None,
            "kind": "coin",
            "chain": "ADA",
            "image": None,
            "price": None if price is None else round(price, 8),
            "price_unit": "ADA",
            "change": None,
            "size": None if vol is None else round(vol, 2),
            "size_unit": "ADA",
            "size_kind": "vol",
            "liq": None if side is None else round(side, 2),
            "buyers": None,
            "buy_pct": None,
            "stat": "%s holders" % format(int(holders), ",") if holders is not None else "Cardano",
            "spark": [],
            "chips": _chips(chip_ids, labels),
            "facts": facts,
            "why": name,
            "source": "Cardano last-run",
            "_raw": raw_score(None, vol or side, holders),
        })
    return _finish(rows, 8)


def _eth_rows(units):
    """One row per raw slug. A credited slug is counted and never grouped."""
    series = {}
    hidden = set()
    loose = 0
    for line in units or []:
        if not isinstance(line, dict) or line.get("chain") != "eip155:1":
            continue
        raw_name = line.get("name") if isinstance(line.get("name"), str) else ""
        raw_slug = line.get("slug") if isinstance(line.get("slug"), str) else ""
        slug = raw_slug.strip()
        if _creator_credit(raw_name, raw_slug):
            if slug:
                hidden.add(slug)
            else:
                loose += 1
            continue
        if not slug:
            continue
        floor = _num(line.get("floor_eth"))
        when = parse_time(line.get("snapshot_t"))
        if floor is None or when is None:
            continue
        series.setdefault(slug, []).append((when, floor, raw_name.strip()))
    rows = []
    for slug, points in series.items():
        points.sort(key=lambda item: item[0])
        last_at, last_floor, last_name = points[-1]
        label = ""
        for _when, _floor, raw_name in points:
            if raw_name:
                label = raw_name
        if not label:
            label = slug
        name = _raw_label(label, slug)
        row_id = _public_id("eth-", slug, name)
        if not name or not row_id:
            continue
        prior = None
        for when, floor, _raw_name in points:
            if (last_at - when).total_seconds() >= 20 * 3600:
                prior = floor
        change = None if not prior else (last_floor / prior - 1.0) * 100.0
        seen_at = _iso(points[0][0])
        labels = {}
        chip_ids = []
        if seen_at and seen_at != _iso(last_at):
            labels["seen"] = "seen"
            chip_ids.append("seen")
        facts = ["Floor from ETH collection snaps."]
        if change is None:
            facts.append("No snap about a day earlier, so the pill is an em dash.")
        else:
            facts.append("Floor change about 24h: %s%%." % round(change, 2))
        facts.append("Latest floor %s ETH." % round(last_floor, 4))
        rows.append({
            "id": row_id,
            "name": name,
            "seen_at": seen_at,
            "kind": "nft",
            "chain": "ETH",
            "image": None,
            "price": round(last_floor, 8),
            "price_unit": "ETH",
            "change": None if change is None else round(change, 4),
            "size": None,
            "size_unit": "ETH",
            "size_kind": "floor",
            "buyers": None,
            "buy_pct": None,
            "stat": "floor",
            "spark": _sample([floor for _when, floor, _raw_name in points]),
            "chips": _chips(chip_ids, labels),
            "facts": facts,
            "why": name,
            "source": "ETH floor snaps",
            "_raw": raw_score(change, last_floor, None),
        })
    return _finish(rows, 6), len(hidden) + loose


def _stamp(values):
    parsed = [parse_time(value) for value in values if isinstance(value, str)]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return None
    return max(parsed).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_from(payloads, now=None):
    """Build the public snapshot from already-parsed sources. Unknown keys are dropped."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    payloads = payloads or {}
    gaps = []
    health = payloads.get("health") if isinstance(payloads.get("health"), dict) else {}
    feeds = []
    wanted = (
        ("SOL", "SOL", "solana-desk-scan.yml", "Solana desk scan"),
        ("WAL", "WAL", "races.yml", "wallet races"),
        ("FOR", "FOR", "run-forensics.yml", "Solana forensics"),
        ("NFT", "NFT", "nft-snapshot.yml", "NFT snapshot"),
        ("ADA", "ADA", "solada-prefilter.yml", "Solada prefilter"),
    )
    scan_times = []
    for feed_id, label, filename, title in wanted:
        row = _workflow(health, filename, title)
        if row is None:
            feeds.append(_feed(feed_id, label, "unknown", None, now, "%s is missing from health.json" % title))
            gaps.append("%s health row is missing." % label)
        else:
            feeds.append(_feed(feed_id, label, row["status"], row["at"], now, "%s · %s" % (title, row["status"])))
            if row["at"]:
                scan_times.append(row["at"])
    hood = payloads.get("hood") if isinstance(payloads.get("hood"), dict) else {}
    hood_at = hood.get("generated_at") if isinstance(hood.get("generated_at"), str) else None
    discover = payloads.get("nft_discover") if isinstance(payloads.get("nft_discover"), dict) else {}
    discover_at = discover.get("generated_at") if isinstance(discover.get("generated_at"), str) else None
    hood_stamp = hood_at or discover_at
    feeds.append(_feed("HS", "HS", _hood_status(hood_stamp, now), hood_stamp, now, "HoodScout daily"))
    if hood_stamp:
        scan_times.append(hood_stamp)
    else:
        gaps.append("HoodScout daily file was not readable.")
    lst = _lst_health(payloads.get("lst"))
    lst_at = lst.get("written_at")
    lst_row = _workflow(health, "lst-count.yml", "lst count")
    if lst_row:
        lst_status = lst_row["status"]
    elif lst.get("runs_ok") is True and lst.get("chain_ok") is True:
        lst_status = "green"
    else:
        lst_status = "unknown"
    if lst.get("runs_ok") is False or lst.get("chain_ok") is False:
        lst_status = "red"
    feeds.append(_feed("LST", "LST", lst_status, lst_at or (lst_row or {}).get("at"), now, "LST status"))
    buzz_at = payloads.get("buzz_asof")
    if not isinstance(buzz_at, str):
        buzz_doc = payloads.get("buzz") if isinstance(payloads.get("buzz"), dict) else {}
        buzz_at = buzz_doc.get("asof") if isinstance(buzz_doc.get("asof"), str) else None
    buzz_stale = True
    if buzz_at and _fresh(buzz_at, now, 48):
        buzz_stale = False
    feeds.append(_feed("X", "X", "hollow" if buzz_stale else "green", buzz_at, now,
                       "X buzz stale" if buzz_stale else "X buzz"))
    if buzz_stale:
        gaps.append("X buzz is stale, so the header mark stays hollow and no row shows fresh buzz.")
    last_run = payloads.get("last_run") if isinstance(payloads.get("last_run"), dict) else {}
    if isinstance(last_run.get("at"), str):
        scan_times.append(last_run["at"])
    nft_at = payloads.get("nft_ran_at")
    if isinstance(nft_at, str):
        scan_times.append(nft_at)
    rows = []
    sol_rows, sol_dropped = _solana_rows(payloads.get("manifest"), payloads.get("snaps"))
    rows.extend(sol_rows)
    if sol_dropped:
        gaps.append("Solana reserve filter dropped %d series (dust or a move over 25×)." % sol_dropped)
    if not sol_rows:
        gaps.append("No Solana 30-minute snaps were readable for this snapshot.")
    else:
        gaps.append("Solana scan lines have no ticker. Names are SOL #1, SOL #2, and so on.")
    if hood:
        rows.extend(_hood_coins(hood))
        gaps.append("HoodScout speculative and dead tiers are not on this list.")
    nft_doc = discover or (payloads.get("nft_latest") if isinstance(payloads.get("nft_latest"), dict) else {})
    nft_rows, nft_hidden = _hood_nfts(nft_doc) if nft_doc else ([], 0)
    rows.extend(nft_rows)
    if last_run:
        allowed = roster_names(payloads.get("whale_roster"))
        rows.extend(_cardano_rows(last_run, payloads.get("flags"), payloads.get("kill"), _whale_name(last_run, allowed), now))
    else:
        gaps.append("Cardano last-run was not readable.")
    eth_rows, eth_hidden = _eth_rows(payloads.get("units"))
    rows.extend(eth_rows)
    hidden = nft_hidden + eth_hidden
    gaps.append("Base has no coin feed. The BASE cut stays empty.")
    _disambiguate(rows)
    rows.sort(key=lambda row: (-row["heat"], row["chain"], row["name"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    last = _stamp(scan_times)
    doc = {
        "schema": 1,
        "built_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "last_scan": last,
        "lead_count": len(rows),
        "x": {"asof": buzz_at},
        "feeds": feeds,
        "rows": rows,
        "gaps": gaps,
        "heat_note": "Heat is a ranking, not a buy signal.",
    }
    if hidden:
        doc["hidden_note"] = "%d collections hidden for creator attribution" % hidden
    _walk(doc)
    return doc


def _fetch(url, timeout=FETCH_TIMEOUT_S):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_json(url):
    return json.loads(_fetch(url))


def _fetch_jsonl(url, timeout=FETCH_TIMEOUT_S):
    raw = _fetch(url, timeout=timeout)
    rows = []
    for line in raw.splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _first_json(urls):
    last = None
    for url in urls:
        try:
            return _fetch_json(url), None
        except Exception as exc:
            last = exc
    return None, last


def load_live(now=None):
    """Fetch the public files this preview is allowed to read."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    payloads = {}
    errors = []

    def take(key, url, kind="json"):
        try:
            payloads[key] = _fetch_jsonl(url) if kind == "jsonl" else _fetch_json(url)
        except Exception as exc:
            errors.append("%s (%s)" % (key, type(exc).__name__))

    take("health", HEALTH_URL)
    take("buzz", DESK + "x_buzz.json")
    take("flags", DESK + "solada-prefilter/flags.json")
    take("kill", DESK + "solada-prefilter/kill_status.json")
    take("last_run", DESK + "solada-prefilter/last-run.json")
    take("whale_roster", DESK + "solada-prefilter/whale_roster.json")
    take("lst", LST_URL)
    try:
        status = _fetch_json(NFT_STATUS_URL)
        if isinstance(status, dict) and isinstance(status.get("ran_at"), str):
            payloads["nft_ran_at"] = status["ran_at"]
    except Exception as exc:
        errors.append("nft status (%s)" % type(exc).__name__)
    day = now.date()
    days = [day, day - datetime.timedelta(days=1)]
    hood, hood_err = _first_json([HOOD + "daily/%s.json" % item.isoformat() for item in days])
    if hood is not None:
        payloads["hood"] = hood
    elif hood_err is not None:
        errors.append("hood (%s)" % type(hood_err).__name__)
    take("nft_discover", HOOD + "nft_discover.json")
    take("nft_latest", HOOD + "nft_latest.json")
    stems = [item.strftime("%Y%m%d") for item in days]
    for stem in stems:
        try:
            payloads["snaps"] = _fetch_jsonl(DESK + "solana-desk/v2/pool-snaps-%s.jsonl" % stem)
            payloads["manifest"] = _fetch_jsonl(DESK + "solana-desk/v2/manifest-%s.jsonl" % stem)
            break
        except Exception as exc:
            errors.append("solana %s (%s)" % (stem, type(exc).__name__))
    try:
        payloads["units"] = _fetch_jsonl(UNITS_URL)
    except Exception as exc:
        errors.append("units (%s)" % type(exc).__name__)
    payloads["_errors"] = errors
    return payloads


def write_snapshot(dest, now=None):
    dest = pathlib.Path(dest)
    payloads = load_live(now)
    errors = payloads.pop("_errors", [])
    doc = build_from(payloads, now)
    if errors:
        doc["gaps"] = list(doc["gaps"]) + ["Unreadable source: %s." % item for item in errors]
        _walk(doc)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print("  radar.json rows %d (%d bytes)" % (doc["lead_count"], dest.stat().st_size))
    return doc


def main():
    dest = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "radar.json"
    write_snapshot(dest)


if __name__ == "__main__":
    main()
