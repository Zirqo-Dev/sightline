#!/usr/bin/env python3
"""Slim Radar snapshot for /radar.html. Stdlib only.

The phone reads radar.json. This step runs inside the existing Pages build.
It does not change a schedule, a secret, or a scan log.

Solana snap lines may carry a token name and symbol recorded by the scan.
A row uses that symbol, then that name. A row that still has neither after
one public DexScreener token lookup (at most 10 calls) is left off the
ladder. The file records only how many were left off. A DexScreener link is
the pair or mint already on the snap. Ages are absolute timestamps; the page
computes the labels.

Degen and the smart-layer inputs are baked here from fields already on the
row. The page applies the HOTTER score, the 48h fold, and the SMART badge.

A coin with no picture uses a committed dashboard icon when the ticker and
the chain both match. SOL #N rows stay picture-less. NFT rows publish no image.

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
import hashlib
import json
import math
import pathlib
import re
import sys
import unicodedata
import urllib.error
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
# Solana links are base58. A link that would carry 0x or a 64-hex asset id is not published.
_SOL_DEX = re.compile(r"^https://dexscreener\.com/solana/[1-9A-HJ-NP-Za-km-z]{32,44}$")


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


# Degen meter. Start at 1, add only for an input that is present, cap at 5.
# Fresh is seen_at under 24h. Thin is an OR of the three size checks.
# Parabolic uses the price multiple (1 + change/100): 10x through 100x is +1,
# and a multiple over 100x is +2. A stock-ticker clone is an exact ticker.
DEGEN_CAP = 5
FRESH_H = 24
THIN_SOL = 150.0
THIN_USD = 500_000.0
THIN_HOLDERS = 500.0
CLONE_TICKERS = frozenset({"NVDA", "MSTR"})
DEGEN_LABELS = {1: "MID", 2: "SPICY", 3: "DEGEN", 4: "FULL SEND", 5: "CASINO"}
# Smart layer. These knobs are the whole rule. The page uses the same numbers.
OLD_GUARD_H = 48
THIN_LIQ_TO_VOL = 0.10
KILL_DROP = 0.80
NEAR_DROP = 0.60
NEAR_LINE = 1.25
# HOTTER score, higher first. The page is the copy that ages with the clock.
# score = heat + FRESH_BONUS * freshness + marker - FLAG_PENALTY * flags
# freshness = 0 when seen_at is missing, else max(0, (48 - age_hours) / 48)
# marker = 20 for ▲▲, 10 for ▲, 0 otherwise
# A missing heat sorts last and does not use the score.
FRESH_BONUS = 8.0
MARKER_HOTTER = 20.0
MARKER_HEATING = 10.0
FLAG_PENALTY = 25.0
DEX_TOKENS_URL = "https://api.dexscreener.com/latest/dex/tokens/"
MAX_DEX_CALLS = 10


def degen_score(name, seen_at, now, sol_reserve=None, usd_volume=None, holders=None,
                crowd=False, multiple=None):
    """Return (score, why). Missing numbers add nothing. The floor is 1."""
    score = 1
    why = []
    seen = parse_time(seen_at) if isinstance(seen_at, str) else None
    if seen is not None and now is not None:
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)
        if (now - seen).total_seconds() < FRESH_H * 3600:
            score += 1
            why.append("fresh")
    thin = False
    if sol_reserve is not None and sol_reserve < THIN_SOL:
        thin = True
    if usd_volume is not None and usd_volume < THIN_USD:
        thin = True
    if holders is not None and holders < THIN_HOLDERS:
        thin = True
    if thin:
        score += 1
        why.append("thin")
    if crowd:
        score += 1
        why.append("crowd")
    if multiple is not None and multiple > 0:
        if multiple > 100:
            score += 2
            why.append("parabolic_100x")
        elif multiple >= 10:
            score += 1
            why.append("parabolic")
    ticker = name.strip().upper() if isinstance(name, str) else ""
    if not ticker:
        score += 1
        why.append("unknown_name")
    elif ticker in CLONE_TICKERS:
        score += 1
        why.append("unknown_clone")
    return min(DEGEN_CAP, score), why


def heat_mark(streak, leg, buy_pct):
    """▲▲ when both legs are on. ▲ when either is on, or buy_pct is at least 0.80."""
    if streak and leg:
        return "hotter"
    if streak or leg or (buy_pct is not None and buy_pct >= 0.80):
        return "heating"
    return None


def price_multiple(change):
    """Price multiple from a percent change. None when the change was not measured."""
    if change is None:
        return None
    return 1.0 + (change / 100.0)


def price_line(first, last):
    """Solana kill line from the snap prices already on the row.

    Crossed at an 80% drop (the desk's reserve-price death line). Near at a
    60% drop. A missing price is unknown, not a flag.
    """
    if first is None or last is None or first <= 0:
        return "unknown"
    drop = 1.0 - (last / first)
    if drop >= KILL_DROP:
        return "crossed"
    if drop >= NEAR_DROP:
        return "near"
    return "clear"


def kill_line_state(row):
    """Cardano kill_status row. Crossed, near, clear, or unknown.

    Near means the value is still above the line and within 25% of it.
    A row that does not carry both numbers cannot clear the near check.
    """
    if not isinstance(row, dict):
        return "clear"
    value = _num(row.get("value"))
    line = _num(row.get("line"))
    if row.get("hit") is True:
        return "crossed"
    if value is None or line is None:
        return "unknown"
    if value <= line:
        return "crossed"
    if line > 0 and value <= line * NEAR_LINE:
        return "near"
    return "clear"


def thin_vs_volume(liq, vol):
    """True when liquidity is under 10% of volume. None when either input is missing."""
    if liq is None or vol is None or vol <= 0:
        return None
    return (liq / vol) < THIN_LIQ_TO_VOL


def buys_falling(values):
    """True when the last buy count is under the first. None without two counts."""
    nums = []
    for value in values or []:
        number = _num(value)
        if number is not None:
            nums.append(number)
    if len(nums) < 2:
        return None
    return nums[-1] < nums[0]


def _age_hours(seen_at, now):
    seen = parse_time(seen_at) if isinstance(seen_at, str) else None
    if seen is None or now is None:
        return None
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return (now - seen).total_seconds() / 3600.0


def fading_flag(buys_drop, seen_at, now):
    """True, False, or None.

    Fading is a buy-count drop or an age over 48h. Prior heat is not stored,
    so a heat drop is not an input. None means the row cannot be cleared.
    """
    if buys_drop is True:
        return True
    age = _age_hours(seen_at, now)
    if age is None:
        return None
    if age > OLD_GUARD_H:
        return True
    if buys_drop is False:
        return False
    return None


def red_flag_ids(row, now):
    """Ids only. A missing input is not an id."""
    flags = []
    if row.get("thin_book") is True:
        flags.append("thin_book")
    if row.get("crowd_flag") is True:
        flags.append("crowd")
    if row.get("line_state") == "crossed":
        flags.append("line_crossed")
    elif row.get("line_state") == "near":
        flags.append("line_near")
    fade = fading_flag(row.get("buys_drop"), row.get("seen_at"), now)
    if fade is True:
        flags.append("fade")
    return flags


def row_is_smart(row, now):
    """Hot or heating, every smart check decided, and no red flag."""
    heat = _num(row.get("heat"))
    if heat is None:
        return False
    mark = row.get("heat_mark")
    if not (heat >= 55 or mark in ("heating", "hotter")):
        return False
    if row.get("thin_book") is None or row.get("crowd_flag") is None:
        return False
    if row.get("line_state") not in ("clear", "near", "crossed"):
        return False
    if fading_flag(row.get("buys_drop"), row.get("seen_at"), now) is None:
        return False
    return not red_flag_ids(row, now)


def _sync_line_chip(row):
    """A crossed line keeps the skull chip. Near does not."""
    if row.get("line_state") != "crossed":
        return
    chips = row.setdefault("chips", [])
    if any(isinstance(chip, dict) and chip.get("id") == "skull" for chip in chips):
        return
    chips.append({"id": "skull", "label": "kill line"})


def _stamp_meter(row, now, name, sol_reserve=None, usd_volume=None, holders=None,
                 crowd=False, multiple=None, streak=False, leg=False, buy_pct=None,
                 thin_book=False, crowd_flag=False, line_state="clear", buys_drop=False):
    """Bake degen and the smart inputs. None on a check means it was not decided."""
    score, why = degen_score(
        name, row.get("seen_at"), now,
        sol_reserve=sol_reserve, usd_volume=usd_volume, holders=holders,
        crowd=bool(crowd), multiple=multiple,
    )
    row["degen"] = score
    row["degen_label"] = DEGEN_LABELS[score]
    row["degen_why"] = why
    row["heat_mark"] = heat_mark(streak, leg, buy_pct)
    row["thin_book"] = thin_book
    row["crowd_flag"] = crowd_flag
    row["line_state"] = line_state
    row["buys_drop"] = buys_drop
    _sync_line_chip(row)
    row["red_flags"] = red_flag_ids(row, now)
    row["smart"] = row_is_smart(row, now)
    return row


def _dex_names(payload, mint):
    """Symbol, name, and a safe image from one token payload. No address is returned."""
    pairs = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(pairs, list):
        return None
    for pair in pairs:
        if not isinstance(pair, dict) or str(pair.get("chainId") or "") != "solana":
            continue
        base = pair.get("baseToken") if isinstance(pair.get("baseToken"), dict) else {}
        if base.get("address") != mint:
            continue
        symbol = _token_label(base.get("symbol"), 20)
        name = _token_label(base.get("name"), 64)
        info = pair.get("info") if isinstance(pair.get("info"), dict) else {}
        image = _https(info.get("imageUrl"))
        if image and _image_hides(image):
            image = None
        if not symbol and not name:
            continue
        return {"symbol": symbol, "name": name, "image": image}
    return None


class _DexStop(Exception):
    """The public token endpoint asked us to stop. The build continues."""


def _dex_lookup_live(mint):
    """One keyless token call. None on any failure. Raises _DexStop on HTTP 429."""
    if not isinstance(mint, str) or not _MINT.fullmatch(mint):
        return None
    url = DEX_TOKENS_URL + urllib.parse.quote(mint)
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) == 429:
            raise _DexStop()
        return None
    except Exception:
        return None
    try:
        return _dex_names(payload, mint)
    except Exception:
        return None


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


# 'by' with a separator on both sides, or '@' after NFKC (full-width at becomes @).
_BY_MARK = re.compile(r"[-_.\u2010-\u2015\u2212\ufe63\uff0d]by[-_.\u2010-\u2015\u2212\ufe63\uff0d]")


def _norm_piece(text):
    """Percent-decode until stable, then NFKC and lowercase."""
    current = text or ""
    for _ in range(3):
        nxt = urllib.parse.unquote(current)
        if nxt == current:
            break
        current = nxt
    return unicodedata.normalize("NFKC", current).lower()


def _has_by_mark(text):
    folded = _norm_piece(text)
    return "@" in folded or _BY_MARK.search(folded) is not None


def _image_hides(url):
    """True when the source image URL credits a creator. The URL is not published."""
    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urllib.parse.urlsplit(url)
    pieces = (url, parsed.hostname or "", parsed.path or "", parsed.query or "", parsed.fragment or "")
    return any(_has_by_mark(piece) for piece in pieces)


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


def _sol_dex(ident):
    """A Solana DexScreener link. Base58 pair or mint only. No 0x and no long hex."""
    if not isinstance(ident, str) or not _MINT.fullmatch(ident):
        return None
    url = "https://dexscreener.com/solana/" + ident
    if not _SOL_DEX.fullmatch(url) or _ETH.search(url) or _STAKE.search(url):
        return None
    return url


def _sol_row_id(ident):
    """Stable id from the pool, or the mint when no pool was recorded.

    The address is not the id. Ten hex characters of SHA-256 keep two pools
    with the same symbol apart without a base58 exemption on the row id.
    """
    if not isinstance(ident, str) or not ident:
        return None
    return "sol-" + hashlib.sha256(ident.encode("utf-8")).hexdigest()[:10]


def _token_label(value, limit):
    """A recorded token name or symbol. Addresses and handles are not labels."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text or len(text) > limit:
        return None
    if _MINT.search(text) or _ETH.search(text) or _STAKE.search(text):
        return None
    if "@" in text or "/" in text or "\\" in text:
        return None
    lowered = text.lower()
    if "http:" in lowered or "https:" in lowered:
        return None
    return text


def _walk(value, key=None):
    if isinstance(value, dict):
        for item_key, item in value.items():
            lowered = str(item_key).lower()
            if lowered in _BANNED_KEYS or "d_bps" in lowered or "onset" in lowered or "verdict" in lowered:
                raise ValueError("banned key")
            _walk(item, item_key)
    elif isinstance(value, list):
        for item in value:
            _walk(item, key)
    elif isinstance(value, str):
        if key == "dex_url" and _SOL_DEX.fullmatch(value) and not _ETH.search(value) and not _STAKE.search(value):
            return
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


def _feed(feed_id, label, status, at, now, detail, name=None):
    """Age is not baked. The page computes it from `at`."""
    del now
    return {
        "id": feed_id,
        "label": label,
        "name": name or label,
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
        original = [row.get("id") for row in group]
        shared = {item for item in original if original.count(item) > 1}
        for index, row in enumerate(group, start=1):
            hint = row.pop("_hint", None)
            if hint and hint not in used:
                row["hint"] = hint
                used.add(hint)
            else:
                row["show_age"] = True
            if row.get("id") not in shared:
                continue
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


# Radar chain label -> icons.json chain. A named SOL ticker may use a local file.
_CHAIN_FOR_ICON = {"ADA": "cardano", "ETH": "ethereum", "BASE": "base", "SOL": "solana"}
_SOL_ANON = re.compile(r"SOL #\d+")
_SLASH_CREDIT = re.compile(r"\([^)]*/[^)]*\)")
_SEEN_KEYS = ("first_seen", "seen_at", "first_logged_at", "first_seen_at")


def _icons_doc():
    cached = getattr(_icons_doc, "_cache", None)
    if cached is not None:
        return cached
    path = ROOT / "dashboard" / "icons" / "icons.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    _icons_doc._cache = data
    return data


def _credits_a_creator(text):
    if not isinstance(text, str) or not text.strip():
        return False
    if _creator_credit(text, text):
        return True
    return _SLASH_CREDIT.search(text) is not None


def _nft_name_is_credited(icons, name):
    """True when this name is a creator-credited icons.json nft_names key."""
    if _credits_a_creator(name):
        return True
    if not isinstance(icons, dict) or not isinstance(name, str):
        return False
    names = icons.get("nft_names")
    if not isinstance(names, dict):
        return False
    folded = name.strip().lower()
    nfts = icons.get("nfts") if isinstance(icons.get("nfts"), dict) else {}
    for key, slug in names.items():
        if not isinstance(key, str) or key.strip().lower() != folded:
            continue
        if _credits_a_creator(key):
            return True
        entry = nfts.get(slug) if isinstance(slug, str) else None
        label = entry.get("name") if isinstance(entry, dict) else ""
        if _credits_a_creator(label):
            return True
    return False


def _local_coin_icon(icons, ticker, chain):
    """Committed coin icon when the ticker and the chain both match."""
    if not isinstance(icons, dict) or not isinstance(ticker, str) or not ticker.strip():
        return None
    wanted = _CHAIN_FOR_ICON.get(chain)
    if not wanted or _nft_name_is_credited(icons, ticker):
        return None
    coins = icons.get("coins") if isinstance(icons.get("coins"), dict) else {}
    entry = coins.get(ticker)
    if not isinstance(entry, dict):
        entry = coins.get(ticker.upper())
    if not isinstance(entry, dict):
        return None
    if str(entry.get("chain") or "").lower() != wanted:
        return None
    src = entry.get("src")
    if not isinstance(src, str) or not src.startswith("dashboard/icons/") or ".." in src:
        return None
    if not (ROOT / src).is_file():
        return None
    return src


def _apply_coin_icon(row, icons=None):
    """Prefer a local coin file. A SOL #N name stays picture-less. NFTs do too."""
    if not isinstance(row, dict) or row.get("kind") != "coin":
        return row
    name = row.get("name") if isinstance(row.get("name"), str) else ""
    if _SOL_ANON.fullmatch(name or ""):
        row["image"] = None
        return row
    icons = _icons_doc() if icons is None else icons
    local = _local_coin_icon(icons, name, row.get("chain"))
    if local:
        row["image"] = local
        return row
    image = row.get("image")
    if isinstance(image, str) and image.startswith("dashboard/"):
        row["image"] = None
        return row
    if image and not _https(image):
        row["image"] = None
    return row


def _cardano_seen(item):
    """A real first-seen timestamp on the row. The run clock is not one."""
    if not isinstance(item, dict):
        return None
    for key in _SEEN_KEYS:
        if key not in item:
            continue
        stamp = _iso(parse_time(item.get(key)))
        if stamp:
            return stamp
    return None


def _cardano_stat(holders, vol, side):
    if holders is not None:
        return "%s holders" % format(int(holders), ",")
    if vol is not None:
        return "%s ADA vol" % format(int(vol), ",")
    if side is not None:
        return "ADA side %s" % format(int(side), ",")
    return "last-run"


def _finish(rows, limit, now=None):
    assign_heat(rows)
    ranked = sorted(rows, key=lambda row: (-row["heat"], row["name"]))
    icons = _icons_doc()
    out = []
    for row in ranked[:limit]:
        row.pop("_raw", None)
        if not row.get("dex_url"):
            row.pop("dex_url", None)
        # Heat is known only after assign_heat, so the badge is decided here.
        row["red_flags"] = red_flag_ids(row, now)
        row["smart"] = row_is_smart(row, now)
        _apply_coin_icon(row, icons)
        try:
            out.append(_public_row(row))
        except ValueError:
            continue
    return out


def _solana_rows(manifest_lines, snap_lines, death_lines=None, now=None, dex_lookup=None):
    """Rows from pool snaps only. Death labels and S2 picks are not read.

    A series with no ticker after the scan-time name and the optional
    DexScreener lookup is not returned. The third value is how many.
    """
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
    recorded = {}
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
        slot = recorded.setdefault(mint, {"t": None, "name": None, "symbol": None, "pool": None})
        pool = line.get("pool") if isinstance(line.get("pool"), str) and _MINT.fullmatch(line.get("pool")) else None
        recorded_name = _token_label(line.get("name"), 64)
        recorded_symbol = _token_label(line.get("symbol"), 20)
        newer = slot["t"] is None or when >= slot["t"]
        if newer:
            slot["t"] = when
        if pool and (newer or not slot["pool"]):
            slot["pool"] = pool
        if recorded_name and (newer or not slot["name"]):
            slot["name"] = recorded_name
        if recorded_symbol and (newer or not slot["symbol"]):
            slot["symbol"] = recorded_symbol
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
            "_labels": recorded.get(mint) or {},
            "_sol": last["sol"],
            "_multiple": (prices[-1] / prices[0]) if prices and prices[0] else None,
            "_crowd": None if share is None else share >= 30,
            "_line": price_line(prices[0], prices[-1]) if prices else "unknown",
            "_buys": buys_falling([item.get("buys") for item in points]),
            "_streak": "streak" in labels,
            "_leg": "leg" in labels,
            "_buy_pct": buy_pct,
        })
    assign_heat(rows)
    ranked = sorted(rows, key=lambda row: (-row["heat"], -row["_raw"]))[:8]
    named = []
    pending = []
    for row in ranked:
        mint = row.pop("_mint", None)
        meta = row.pop("_labels", None) or {}
        row.pop("_raw", None)
        symbol = meta.get("symbol")
        token_name = meta.get("name")
        if symbol and token_name and token_name.casefold() == symbol.casefold():
            token_name = None
        row["name"] = ""
        if symbol:
            row["name"] = symbol
        elif token_name:
            row["name"] = token_name
            token_name = None
        if token_name:
            row["token_name"] = token_name
        ident = meta.get("pool") or (mint if isinstance(mint, str) and _MINT.fullmatch(mint) else None)
        row["_ident"] = ident
        row["_mint_lookup"] = mint if isinstance(mint, str) else None
        if row["name"]:
            named.append(row)
        else:
            pending.append(row)
    hidden = 0
    calls = 0
    stop = False
    for row in pending:
        found = None
        mint = row.get("_mint_lookup")
        if dex_lookup is not None and not stop and calls < MAX_DEX_CALLS and mint:
            calls += 1
            try:
                found = dex_lookup(mint)
            except _DexStop:
                stop = True
                found = None
            except Exception:
                found = None
        if isinstance(found, dict):
            symbol = _token_label(found.get("symbol"), 20)
            token_name = _token_label(found.get("name"), 64)
            if symbol and token_name and token_name.casefold() == symbol.casefold():
                token_name = None
            if symbol:
                row["name"] = symbol
                if token_name:
                    row["token_name"] = token_name
            elif token_name:
                row["name"] = token_name
            image = found.get("image")
            if isinstance(image, str) and _https(image) and not _image_hides(image):
                row["image"] = image
        if not row.get("name"):
            hidden += 1
            continue
        named.append(row)
    out = []
    for index, row in enumerate(named, start=1):
        ident = row.pop("_ident", None)
        row.pop("_mint_lookup", None)
        sol_reserve = row.pop("_sol", None)
        multiple = row.pop("_multiple", None)
        crowd = row.pop("_crowd", None)
        line_state = row.pop("_line", "unknown")
        buys_drop = row.pop("_buys", None)
        streak = row.pop("_streak", False)
        leg = row.pop("_leg", False)
        buy_pct = row.pop("_buy_pct", None)
        row_id = _sol_row_id(ident)
        if row_id:
            row["id"] = row_id
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", str(row["name"]).lower()).strip("-") or str(index)
            row["id"] = "sol-" + slug
        row["why"] = row["name"]
        url = _sol_dex(ident)
        if url:
            row["dex_url"] = url
        _stamp_meter(
            row, now, row["name"],
            sol_reserve=sol_reserve, multiple=multiple, crowd=bool(crowd),
            streak=streak, leg=leg, buy_pct=buy_pct,
            # Snaps have a SOL reserve and no USD volume, so the liq/vol
            # comparison is not an input on this chain.
            thin_book=False, crowd_flag=crowd, line_state=line_state, buys_drop=buys_drop,
        )
        _apply_coin_icon(row)
        try:
            out.append(_public_row(row))
        except ValueError:
            continue
    return out, len(rejected), hidden


def _hood_coins(doc, now=None):
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
        if days is not None and days * 24 < 1:
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
        _stamp_meter(
            rows[-1], now, name,
            usd_volume=vol, multiple=price_multiple(change), buy_pct=buy_pct,
            # No holder-concentration field and no kill line are published here.
            thin_book=thin_vs_volume(liq, vol), crowd_flag=False, line_state="clear",
            buys_drop=buys_falling(spark_vals),
        )
    return _finish(rows, 10, now)


def _hood_nfts(doc, now=None):
    """Kept HoodScout NFT rows, and how many collections were left out."""
    collections = doc.get("collections") if isinstance(doc, dict) else None
    rows = []
    hidden = 0
    for item in collections or []:
        if not isinstance(item, dict):
            continue
        raw_name = item.get("name") if isinstance(item.get("name"), str) else ""
        raw_slug = item.get("slug") if isinstance(item.get("slug"), str) else ""
        if _creator_credit(raw_name, raw_slug) or _image_hides(item.get("image_url")):
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
        days = _num(item.get("age_days"))
        seen_at = _seen_from_days(doc.get("generated_at"), days)
        labels = {}
        chip_ids = []
        if seen_at:
            labels["seen"] = "seen"
            chip_ids.append("seen")
        if item.get("flag_new") is True and days is not None and days * 24 < 1:
            labels["pool"] = "new pool"
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
            "image": None,
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
        _stamp_meter(
            rows[-1], now, name, multiple=price_multiple(change),
            thin_book=False, crowd_flag=False, line_state="clear", buys_drop=False,
        )
    return _finish(rows, 6, now), hidden


def _cardano_rows(last_run, flags, kill, whale, now, flags_loaded=False, kill_loaded=False):
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
        if holders is None:
            holders = _num(item.get("holders"))
        side = _num(item.get("ada_side"))
        seen_at = _cardano_seen(item)
        kinds = flagged.get(name) or set()
        labels = {}
        chip_ids = []
        if seen_at:
            labels["seen"] = "seen"
            chip_ids.append("seen")
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
            "seen_at": seen_at,
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
            "stat": _cardano_stat(holders, vol, side),
            "spark": [],
            "chips": _chips(chip_ids, labels),
            "facts": facts,
            "why": name,
            "source": "Cardano last-run",
            "_raw": raw_score(None, vol or side, holders),
        })
        crowd_on = bool({"sniper_full_exit", "coordinated_buys", "holder_concentration", "concentration_jump"} & set(kinds))
        if not kill_loaded:
            line_state = "unknown"
        elif isinstance(kill_row, dict):
            line_state = kill_line_state(kill_row)
        else:
            line_state = "clear"
        _stamp_meter(
            rows[-1], now, name,
            holders=holders, crowd=crowd_on, multiple=None,
            thin_book=thin_vs_volume(side, vol),
            crowd_flag=crowd_on if flags_loaded else None,
            line_state=line_state,
            # Cardano rows have no buy series. Age is the fading input.
            buys_drop=False,
        )
    return _finish(rows, 8, now)


def _eth_rows(units, now=None):
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
        series.setdefault(slug, []).append((when, floor, raw_name.strip(), None))
    rows = []
    for slug, points in series.items():
        points.sort(key=lambda item: item[0])
        last_at, last_floor, last_name, _last_contract = points[-1]
        label = ""
        for _when, _floor, raw_name, _contract in points:
            if raw_name:
                label = raw_name
        if not label:
            label = slug
        name = _raw_label(label, slug)
        row_id = _public_id("eth-", slug, name)
        if not name or not row_id:
            continue
        prior = None
        for when, floor, _raw_name, _contract in points:
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
            "spark": _sample([floor for _when, floor, _raw_name, _contract in points]),
            "chips": _chips(chip_ids, labels),
            "facts": facts,
            "why": name,
            "source": "ETH floor snaps",
            "_raw": raw_score(change, last_floor, None),
        })
        _stamp_meter(
            rows[-1], now, name, multiple=price_multiple(change),
            thin_book=False, crowd_flag=False, line_state="clear", buys_drop=False,
        )
    return _finish(rows, 6, now), len(hidden) + loose


def _stamp(values):
    parsed = [parse_time(value) for value in values if isinstance(value, str)]
    parsed = [item for item in parsed if item is not None]
    if not parsed:
        return None
    return max(parsed).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_from(payloads, now=None, dex_lookup=None):
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
        ("WAL", "WAL", "races.yml", "Wallet races"),
        ("FOR", "FOR", "run-forensics.yml", "Solana forensics"),
        ("NFT", "NFT", "nft-snapshot.yml", "NFT snapshot"),
        ("ADA", "ADA", "solada-prefilter.yml", "Solada prefilter"),
    )
    scan_times = []
    for feed_id, label, filename, title in wanted:
        row = _workflow(health, filename, title)
        if row is None:
            feeds.append(_feed(feed_id, label, "unknown", None, now, "%s is missing from health.json" % title, title))
            gaps.append("%s health row is missing." % label)
        else:
            if feed_id == "WAL" and row["status"] == "red":
                detail = "Wallet races · last recorded run failed"
            else:
                detail = "%s · %s" % (title, row["status"])
            feeds.append(_feed(feed_id, label, row["status"], row["at"], now, detail, title))
            if row["at"]:
                scan_times.append(row["at"])
    hood = payloads.get("hood") if isinstance(payloads.get("hood"), dict) else {}
    hood_at = hood.get("generated_at") if isinstance(hood.get("generated_at"), str) else None
    discover = payloads.get("nft_discover") if isinstance(payloads.get("nft_discover"), dict) else {}
    discover_at = discover.get("generated_at") if isinstance(discover.get("generated_at"), str) else None
    hood_stamp = hood_at or discover_at
    feeds.append(_feed("HS", "HS", _hood_status(hood_stamp, now), hood_stamp, now, "HoodScout daily", "HoodScout"))
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
    feeds.append(_feed("LST", "LST", lst_status, lst_at or (lst_row or {}).get("at"), now, "LST count", "LST count"))
    buzz_at = payloads.get("buzz_asof")
    if not isinstance(buzz_at, str):
        buzz_doc = payloads.get("buzz") if isinstance(payloads.get("buzz"), dict) else {}
        buzz_at = buzz_doc.get("asof") if isinstance(buzz_doc.get("asof"), str) else None
    buzz_stale = True
    if buzz_at and _fresh(buzz_at, now, 48):
        buzz_stale = False
    feeds.append(_feed("X", "X", "hollow" if buzz_stale else "green", buzz_at, now,
                       "X buzz stale" if buzz_stale else "X buzz", "X buzz"))
    if buzz_stale:
        gaps.append("X buzz is stale, so the header mark stays hollow and no row shows fresh buzz.")
    last_run = payloads.get("last_run") if isinstance(payloads.get("last_run"), dict) else {}
    if isinstance(last_run.get("at"), str):
        scan_times.append(last_run["at"])
    nft_at = payloads.get("nft_ran_at")
    if isinstance(nft_at, str):
        scan_times.append(nft_at)
    rows = []
    sol_rows, sol_dropped, sol_unnamed = _solana_rows(
        payloads.get("manifest"), payloads.get("snaps"), now=now, dex_lookup=dex_lookup,
    )
    rows.extend(sol_rows)
    if sol_dropped:
        gaps.append("Solana reserve filter dropped %d series (dust or a move over 25×)." % sol_dropped)
    if not sol_rows and not sol_unnamed:
        gaps.append("No Solana 30-minute snaps were readable for this snapshot.")
    if hood:
        rows.extend(_hood_coins(hood, now))
        gaps.append("HoodScout speculative and dead tiers are not on this list.")
    nft_doc = discover or (payloads.get("nft_latest") if isinstance(payloads.get("nft_latest"), dict) else {})
    nft_rows, nft_hidden = _hood_nfts(nft_doc, now) if nft_doc else ([], 0)
    rows.extend(nft_rows)
    if last_run:
        allowed = roster_names(payloads.get("whale_roster"))
        rows.extend(_cardano_rows(
            last_run, payloads.get("flags"), payloads.get("kill"), _whale_name(last_run, allowed), now,
            flags_loaded=isinstance(payloads.get("flags"), dict),
            kill_loaded=isinstance(payloads.get("kill"), dict),
        ))
    else:
        gaps.append("Cardano last-run was not readable.")
    eth_rows, eth_hidden = _eth_rows(payloads.get("units"), now)
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
    if sol_unnamed:
        doc["unnamed_hidden"] = sol_unnamed
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
    doc = build_from(payloads, now, dex_lookup=_dex_lookup_live)
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
