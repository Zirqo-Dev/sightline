"""Phase 1 dev checks (zero AI). One row per tracked Solada launch.

Writes desk-data:solada-prefilter/dev_checks.json. Chain data only (Koios, then Blockfrost
when BLOCKFROST_PROJECT_ID is set). No external score API.

The dev is the launch's creator stake: Solada launcherAddress, or, when that has no stake
key, the largest non-excluded key that received the token in the mint tx. Output carries a
short dev_tag only (a flags/watch prefix of at most 12 characters when one matches, else
the same w-xxxxxxxx hash flags.py uses). Never a full address or stake key.

Phase 1 is dev sells / late rugs, serial-launcher counts, and drawdown / inactivity.
dev_buy_ada, dev_locked_pct and unlock_date are always null (phase 2). No dev-lock check,
no dev-buy class, no social checks.

A sell is one transaction whose creator key UTxOs lose this token and gain ADA. A two-step
DEX order (tokens parked at a script, ADA paid back later) is not a sell here. late_rug is
true only when a complete read shows the dev sold at least rug_bag_pct of the tokens that
stake received, at least late_min_minutes after launch, while another non-excluded wallet
still holds. "The supply" is the creator stake's own received supply: the curve reserve is
excluded and usually holds the rest of the mint.

The first time a ticker is measured, changed_since_last is false (baseline). Later, it is
true only when dev_sold_ada rises by at least min_sell_ada, or late_rug flips to true.
That is the only thing that may add a dev_sell trigger. Drawdown, serial counts and
inactivity update the row without re-firing.
"""
import os, sys
from datetime import datetime, timezone

import flags as F
import koios_tx as KT

KOIOS = "https://api.koios.rest/api/v1"
BF = "https://cardano-mainnet.blockfrost.io/api/v0"
DEFAULTS = {
    "enabled": True, "budget_s": 45, "max_calls": 36, "max_bf_calls": 50,
    "max_txs_per_unit": 25, "max_pages_per_unit": 2, "utxo_batch": 13,
    "max_launches": 8, "max_prior": 3,
    "rug_bag_pct": 80, "late_min_minutes": 30, "rug_min_other_holders": 1,
    "min_sell_ada": 1.0, "inactive_h": 24, "serial_3h_h": 3, "serial_7d_h": 168,
    "serial_caution_3h": 2, "serial_caution_7d": 3, "drawdown_caution_pct": 80,
}
PHASE2 = ("dev_buy_ada", "dev_locked_pct", "unlock_date")
NOTE = (
    "Phase 1 only: creator-stake sells and late rugs, serial launch counts, drawdown from the "
    "peak this job has seen, and inactivity. dev_buy_ada, dev_locked_pct and unlock_date are "
    "phase 2 and always null. No dev lock, dev buy class, or social checks. dev_tag is a watch "
    "prefix or a 10-character hash, never an address or stake key. ATH is the highest mcap this "
    "job has observed (the run is every 2h), not a tick peak. prior_rugs counts earlier launches "
    "this job has already marked late_rug for the same creator stake. A sell is token out and ADA "
    "in on the creator's own key UTxOs in one tx."
)


def dc_cfg(cfg=None):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("dev_checks") or {}).items() if not str(k).startswith("_")})
    return out


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def short_tag(stake, watch):
    """Watch prefix when one matches and is <= 12 chars (most specific wins), else flags.tag (10 chars)."""
    if not stake:
        return None
    hits = [p for p in F.match_tags(stake, watch or {}) if isinstance(p, str) and 0 < len(p) <= 12]
    if hits:
        return max(hits, key=len)
    return F.tag(stake)


def _hit(stake, addr, ex):
    if stake and F.tag(stake) in ex:
        return True
    if addr and F.tag(addr) in ex:
        return True
    return False


def _who(u):
    addr = u.get("addr") or ""
    script = F.addr_info(addr)[1] if addr else False
    stake = u.get("stake") or (F.addr_info(addr)[0] if addr else None)
    return stake, bool(script), addr


def creator_from_mint(unit, tx, ex):
    """Largest key recipient of `unit` in the mint tx, skipping excluded tags and scripts."""
    best, qty = None, 0
    for u in tx.get("outputs") or []:
        st, script, addr = _who(u)
        if script or not st or _hit(st, addr, ex):
            continue
        q = int((u.get("assets") or {}).get(unit, 0) or 0)
        if q > qty:
            best, qty = st, q
    return best


def fold(unit, ordered, dev, ex, launch_s, dc):
    """Replay txs in chain order. Pure. Returns sell stats for `dev` (or zeros if dev is None).

    Excluded tags (the Solada curve reserve) are neither holders nor sellers. Script payment
    addresses are not holders. Peak is the dev's balance before each sell, so a same-tx dump
    still counts against the amount they received.
    """
    bal, peak = {}, 0
    sold_ada, sold_qty = 0.0, 0
    first_sell_s, holders_at, late = None, None, False
    for tx in ordered:
        deltas, dev_qty, dev_lov = {}, 0, 0
        for side, sign in (("inputs", -1), ("outputs", 1)):
            for u in tx.get(side) or []:
                st, script, addr = _who(u)
                if _hit(st, addr, ex) or script or not st:
                    continue
                q = int((u.get("assets") or {}).get(unit, 0) or 0)
                if q:
                    deltas[st] = deltas.get(st, 0) + sign * q
                if dev and st == dev:
                    dev_lov += sign * int(u.get("lovelace") or 0)
                    dev_qty += sign * q
        if dev:
            peak = max(peak, bal.get(dev, 0))
        for st, dq in deltas.items():
            bal[st] = bal.get(st, 0) + dq
            if bal[st] <= 0:
                bal.pop(st, None)
        if dev and dev_qty > 0:
            peak = max(peak, bal.get(dev, 0))
        if not (dev and dev_qty < 0 and dev_lov > 0):
            continue
        sold_ada += dev_lov / 1e6
        sold_qty += -dev_qty
        holders = sum(1 for q in bal.values() if q > 0)
        others = sum(1 for s, q in bal.items() if s != dev and q > 0)
        if first_sell_s is None:
            first_sell_s = tx.get("time")
            holders_at = holders
        after = (tx["time"] - launch_s) / 60.0 if launch_s and tx.get("time") else None
        bag = (sold_qty / peak * 100.0) if peak else 0.0
        if (after is not None and after >= float(dc["late_min_minutes"]) and bag >= float(dc["rug_bag_pct"])
                and others >= int(dc["rug_min_other_holders"])):
            late = True
            holders_at = holders
    return {"peak": peak, "sold_ada": round(sold_ada, 2), "sold_qty": sold_qty, "first_sell_s": first_sell_s,
            "holders_at_sell": holders_at, "late_rug": late}


def serial_counts(launches, stake, now_ms, dc):
    """How many Solada launches in this payload share `stake` and went live in the last 3h / 7d.
    Includes this launch. Launches with no creator stake or no liveAtMs are not counted."""
    if not stake:
        return None, None
    n3 = n7 = 0
    h3, h7 = float(dc["serial_3h_h"]) * 3_600_000, float(dc["serial_7d_h"]) * 3_600_000
    for l in launches or []:
        if F.addr_info(l.get("launcherAddress") or "")[0] != stake:
            continue
        live = l.get("liveAtMs")
        if not live:
            continue
        age = now_ms - float(live)
        if age < 0:
            continue
        if age <= h7:
            n7 += 1
        if age <= h3:
            n3 += 1
    return n3, n7


def prior_rug_count(cache, stake, unit, live_ms):
    """Other cached launches by this stake already marked late_rug, earlier than this one when both times exist."""
    if not stake:
        return None
    n = 0
    for u, row in (cache.get("units") or {}).items():
        if u == unit or not isinstance(row, dict) or row.get("stake") != stake or row.get("late_rug") is not True:
            continue
        other = row.get("live_at_ms")
        if live_ms and other and float(other) >= float(live_ms):
            continue
        n += 1
    return n


def risk_of(sold, late, prior, s3, s7, drawdown, inactive, complete, dc):
    """low | caution | high, or None when a partial read is not enough to call it low."""
    if late is True or (prior or 0) >= 1:
        return "high"
    caution = ((sold or 0) >= float(dc["min_sell_ada"]) or (s3 or 0) >= int(dc["serial_caution_3h"])
               or (s7 or 0) >= int(dc["serial_caution_7d"])
               or (drawdown is not None and drawdown >= float(dc["drawdown_caution_pct"]))
               or inactive is True)
    if caution:
        return "caution"
    if late is None or not complete:
        return None
    return "low"


def sell_changed(prev, sold, late, min_ada):
    """True only when a previous row exists and the sell picture moved. The first row for a ticker is a baseline."""
    if not isinstance(prev, dict):
        return False
    prev_sold = float(prev.get("dev_sold_ada") or 0)
    grew = sold is not None and round(float(sold) - prev_sold, 2) >= float(min_ada)
    return grew or (late is True and prev.get("late_rug") is not True)


def inactive_of(last_tx_s, saw_newest, vol, now_s, launch_s, hours):
    """True when nothing has traded for about `hours` (default 24). Newest chain tx wins; else 24h pool volume."""
    window = float(hours) * 3600.0
    if saw_newest and last_tx_s:
        return (now_s - last_tx_s) >= window
    if saw_newest and not last_tx_s:
        if launch_s:
            return (now_s - launch_s) >= window
        if vol is not None:
            return float(vol) <= 0
        return None
    if vol is not None:
        return float(vol) <= 0
    return None


def ath_drawdown(prev, mcap):
    """Peak mcap this job has stored, and percent down from it. Unknown current mcap leaves drawdown null."""
    prev_ath = (prev or {}).get("ath_mcap") if isinstance(prev, dict) else None
    ath = float(prev_ath) if isinstance(prev_ath, (int, float)) else None
    cur = float(mcap) if isinstance(mcap, (int, float)) else None
    if cur is not None and cur >= 0:
        ath = cur if ath is None else max(ath, cur)
    dd = None
    if ath and cur is not None and ath > 0:
        dd = round(max(0.0, (ath - cur) / ath * 100.0), 1)
    if ath is None:
        return None, dd
    return (int(ath) if ath == int(ath) else round(ath, 2)), dd


def _blank(ticker, policy_short, asof):
    row = {
        "ticker": ticker, "policy_short": policy_short, "dev_tag": None,
        "dev_sold_ada": None, "dev_sold_after_min": None, "holders_at_sell": None, "late_rug": None,
        "serial_3h": None, "serial_7d": None, "prior_rugs": None,
        "ath_mcap": None, "drawdown_pct": None, "inactive": None, "risk": None,
        "changed_since_last": False, "asof": asof,
    }
    for k in PHASE2:
        row[k] = None
    row["coverage"] = {"txs_read": 0, "complete": False, "skipped": None, "unread_txs": 0}
    return row


def _launch_s(launch):
    ms = (launch or {}).get("liveAtMs")
    return float(ms) / 1000.0 if ms else None


def _policy_short(unit):
    return unit[:12] if unit else None


class DevChain:
    """Koios first. A failed Koios read is retried on Blockfrost when the project id is set, under its own cap.
    The key is sent only as a request header. This class does not set Http.deadline, so a 429 here cannot trip
    the research circuit breaker or close whale_ledger's providers."""

    def __init__(self, http, dc, bf_key, deadline):
        self.h, self.dc, self.deadline = http, dc, deadline
        self.bf_key = (bf_key or "").strip()
        self.start = http.calls.get("koios", 0)
        self.bf_start = http.calls.get("blockfrost", 0)
        self.fallbacks = 0

    def late(self):
        return __import__("time").time() >= self.deadline

    def koios_left(self):
        return int(self.dc["max_calls"]) - (self.h.calls.get("koios", 0) - self.start)

    def bf_left(self):
        return int(self.dc["max_bf_calls"]) - (self.h.calls.get("blockfrost", 0) - self.bf_start)

    def stop_reason(self):
        if self.late():
            return "deadline"
        if self.koios_left() <= 0:
            return "budget"
        return None

    def bf(self, path):
        if not self.bf_key or self.bf_left() <= 0 or self.late():
            return None
        return self.h.req("blockfrost", BF + path, headers={"project_id": self.bf_key}, quiet404=False)

    def _fallback(self):
        if self.bf_key and self.bf_left() > 0 and not self.late():
            self.fallbacks += 1
            return True
        return False

    def _bf_utxo(self, x):
        def amt(a):
            lov = int(next((v["quantity"] for v in a if v["unit"] == "lovelace"), 0))
            return lov, {v["unit"]: int(v["quantity"]) for v in a if v["unit"] != "lovelace"}
        out = {}
        for s in ("inputs", "outputs"):
            rows = []
            for u in x.get(s) or []:
                if u.get("collateral") or u.get("reference"):
                    continue
                lov, assets = amt(u.get("amount") or [])
                ref = f"{u.get('tx_hash', '')}#{u.get('output_index', 0)}" if s == "inputs" else f"{x.get('hash', '')}#{u.get('output_index', 0)}"
                rows.append({"ref": ref, "addr": u.get("address", ""), "stake": F.addr_info(u.get("address", ""))[0],
                             "lovelace": lov, "assets": assets})
            out[s] = rows
        return out

    def asset_txs(self, unit, desc=False):
        if self.stop_reason():
            return None
        lim = int(self.dc["max_txs_per_unit"])
        order = "desc" if desc else "asc"
        url = (f"{KOIOS}/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}"
               f"&_history=true&order=block_height.{order}&limit={lim}")
        r = self.h.req("koios", url, quiet404=False)
        if r is None and self._fallback():
            r = self.bf(f"/assets/{unit}/transactions?order={order}&count={min(lim, 100)}")
        if not isinstance(r, list):
            return None
        rows = [(t.get("tx_hash"), t.get("block_height") or 0, t.get("block_time") or 0)
                for t in r if t.get("tx_hash")]
        rows.sort(key=lambda t: (t[1], t[0]), reverse=desc)
        return rows[:lim]

    def utxos(self, hashes):
        out = {}
        for body in KT.batches(hashes, "_tx_hashes", int(self.dc["utxo_batch"]), KT.TX_INFO_FLAGS):
            if self.stop_reason():
                break
            chunk = body["_tx_hashes"]
            got = KT.tx_info(self.h, chunk, len(chunk), lambda: self.stop_reason() is None)
            if not got and self._fallback():
                for h in chunk:
                    x = self.bf(f"/txs/{h}/utxos")
                    if x:
                        out[h] = self._bf_utxo(x)
                continue
            out.update(got or {})
        return out


def _private_cache(cache_dir, data_dir):
    """Actions-cache path for stake keys. Never a file inside the public desk-data directory."""
    if not cache_dir or not data_dir:
        return None
    cpath = os.path.abspath(os.path.join(cache_dir, "dev-checks-cache.json"))
    data = os.path.abspath(data_dir)
    if cpath == data or cpath.startswith(data + os.sep):
        print("dev_checks: cache dir is inside the public data dir; private cache not written", file=sys.stderr)
        return None
    return cpath


def _launcher_stake(launch, ex):
    addr = (launch or {}).get("launcherAddress") or ""
    stake = F.addr_info(addr)[0]
    if stake and _hit(stake, addr, ex):
        return None
    return stake


def _remember(cache, unit, stake, live_ms, late, complete):
    if not stake:
        return
    prev = (cache.get("units") or {}).get(unit) or {}
    cache.setdefault("units", {})[unit] = {
        "stake": stake, "live_at_ms": live_ms if live_ms is not None else prev.get("live_at_ms"),
        "late_rug": late if late is not None else prev.get("late_rug"),
        "complete": bool(complete) or bool(prev.get("complete")),
    }


def _scan(chain, item, ex, dc):
    """Read one unit. Stake keys stay in the returned dict for the caller; they are not a public field."""
    launch, unit = item.get("launch") or {}, item.get("unit") or ""
    stake = _launcher_stake(launch, ex)
    base = {"stake": stake, "skipped": None, "complete": False, "txs_read": 0, "unread_txs": 0,
            "saw_newest": False, "last_tx_s": None, "stats": None}
    if not unit or len(unit) < 56:
        base["skipped"] = "no_unit"
        return base
    if chain.stop_reason():
        base["skipped"] = chain.stop_reason()
        return base
    asc = chain.asset_txs(unit, desc=False)
    if asc is None:
        base["skipped"] = "koios"
        return base
    lim = int(dc["max_txs_per_unit"])
    desc_rows = None
    if len(asc) >= lim and int(dc["max_pages_per_unit"]) >= 2 and chain.stop_reason() is None:
        desc_rows = chain.asset_txs(unit, desc=True)
    merged = {}
    for row in asc + (desc_rows or []):
        merged[row[0]] = row
    txs = sorted(merged.values(), key=lambda t: (t[1], t[0]))
    ut = chain.utxos([t[0] for t in txs]) if txs else {}
    ordered, unread = [], 0
    for h, hg, bt in txs:
        if h not in ut:
            unread += 1
            continue
        ordered.append({"hash": h, "height": hg, "time": bt, **ut[h]})
    if not stake and ordered:
        stake = creator_from_mint(unit, ordered[0], ex)
        base["stake"] = stake
    complete = len(asc) < lim and unread == 0 and (desc_rows is None or isinstance(desc_rows, list) and len(asc) < lim)
    # a full first page means the middle of the history was not read
    if len(asc) >= lim:
        complete = False
    saw_newest = len(asc) < lim or isinstance(desc_rows, list)
    stats = fold(unit, ordered, stake, ex, _launch_s(launch), dc)
    if not complete:
        stats = dict(stats, late_rug=None, holders_at_sell=None)
        if stats["sold_ada"] == 0:
            stats["sold_ada"] = None
    elif stats["sold_ada"] == 0:
        stats["holders_at_sell"] = None
    base.update(complete=complete, txs_read=len(ordered), unread_txs=unread, saw_newest=saw_newest,
                last_tx_s=max((t["time"] for t in ordered), default=None), stats=stats, stake=stake)
    return base


def _no_stake(row, stake, keep=False):
    """Without a creator stake this run's sell fields are not the dev's. Do not call that low, and do not fire.
    keep=True leaves a previous row's sell picture in place (a skipped read must not look like a new sell later)."""
    if stake:
        return row
    row["changed_since_last"] = False
    if row.get("risk") == "low":
        row["risk"] = None
    if not keep:
        row["dev_tag"] = None
        row["dev_sold_ada"] = None
        row["dev_sold_after_min"] = None
        row["holders_at_sell"] = None
        row["late_rug"] = None
    cov = row.get("coverage") or {}
    if not cov.get("skipped"):
        cov["skipped"] = "no_stake"
    cov["complete"] = False
    row["coverage"] = cov
    return row


def _entry(item, scan, prev, cache, launches, dc, now_s, now_ms, baseline):
    launch = item.get("launch") or {}
    unit = item.get("unit") or ""
    row = _blank(item.get("ticker"), _policy_short(unit), iso(now_s))
    stake = (scan or {}).get("stake")
    row["dev_tag"] = short_tag(stake, item.get("watch"))
    s3, s7 = serial_counts(launches, stake, now_ms, dc)
    row["serial_3h"], row["serial_7d"] = s3, s7
    live_ms = launch.get("liveAtMs")
    row["prior_rugs"] = prior_rug_count(cache, stake, unit, live_ms)
    ath, dd = ath_drawdown(prev, item.get("mcap_ada"))
    row["ath_mcap"], row["drawdown_pct"] = ath, dd
    skipped = (scan or {}).get("skipped")
    stats = (scan or {}).get("stats")
    if skipped or not stats:
        if isinstance(prev, dict):
            for k in ("dev_tag", "dev_sold_ada", "dev_sold_after_min", "holders_at_sell", "late_rug"):
                if prev.get(k) is not None and (k != "dev_tag" or row["dev_tag"] is None):
                    row[k] = prev.get(k)
            if row["ath_mcap"] is None and prev.get("ath_mcap") is not None:
                row["ath_mcap"] = prev.get("ath_mcap")
        row["inactive"] = inactive_of(None, False, item.get("vol_h24_ada"), now_s, _launch_s(launch), dc["inactive_h"])
        risk = risk_of(row["dev_sold_ada"], row["late_rug"], row["prior_rugs"], s3, s7, row["drawdown_pct"],
                       row["inactive"], False, dc)
        if risk is None and isinstance(prev, dict) and prev.get("risk") in ("low", "caution", "high"):
            risk = prev.get("risk")
        row["risk"] = risk
        row["changed_since_last"] = False
        row["coverage"] = {"txs_read": 0, "complete": False, "skipped": skipped or "budget", "unread_txs": 0}
        return _no_stake(row, stake, keep=True)
    complete = bool(scan.get("complete"))
    sold = stats["sold_ada"]
    late = stats["late_rug"] if complete else None
    after = None
    if stats.get("first_sell_s") and _launch_s(launch):
        after = round((stats["first_sell_s"] - _launch_s(launch)) / 60.0, 1)
    row["dev_sold_ada"] = sold
    row["dev_sold_after_min"] = after
    row["holders_at_sell"] = stats["holders_at_sell"] if complete else None
    row["late_rug"] = True if late else (False if complete else None)
    row["inactive"] = inactive_of(scan.get("last_tx_s"), scan.get("saw_newest"), item.get("vol_h24_ada"),
                                  now_s, _launch_s(launch), dc["inactive_h"])
    risk = risk_of(sold, row["late_rug"], row["prior_rugs"], s3, s7, dd, row["inactive"], complete, dc)
    if risk is None and isinstance(prev, dict) and prev.get("risk") in ("low", "caution", "high"):
        risk = prev.get("risk")
    row["risk"] = risk
    allow = not baseline
    measured = sold if (complete or sold) else None
    row["changed_since_last"] = bool(allow and sell_changed(prev, measured, row["late_rug"], dc["min_sell_ada"]))
    if not complete and sold is None:
        row["changed_since_last"] = False
    row["coverage"] = {"txs_read": scan.get("txs_read") or 0, "complete": complete, "skipped": None,
                       "unread_txs": scan.get("unread_txs") or 0}
    return _no_stake(row, stake)


def trigger_for(row):
    """One dev_sell trigger when this row's sell picture changed. None otherwise. Metrics are short tags and numbers."""
    if not row.get("changed_since_last"):
        return None
    sold = float(row.get("dev_sold_ada") or 0)
    late = row.get("late_rug") is True
    if sold < 0 and not late:
        return None
    if sold == 0 and not late:
        return None
    metrics = {k: row.get(k) for k in
               ("dev_tag", "dev_sold_ada", "dev_sold_after_min", "holders_at_sell", "late_rug", "risk", "changed_since_last")}
    F.assert_public(metrics)
    tag = metrics.get("dev_tag")
    if tag is not None and len(tag) > 12:
        raise SystemExit("dev_checks: refusing a dev_tag that is not a short tag")
    return {"key": f"devsell:{row['ticker']}:{sold:.2f}:{1 if late else 0}", "kind": "dev_sell",
            "name": row["ticker"], "metrics": metrics}


def _guard(doc):
    for e in doc.get("entries") or []:
        tag = e.get("dev_tag")
        if tag is not None and (not isinstance(tag, str) or len(tag) > 12 or tag.startswith("stake") or tag.startswith("addr")):
            raise SystemExit("dev_checks: refusing a dev_tag that is not a short tag")
        for k in PHASE2:
            if e.get(k) is not None:
                raise SystemExit("dev_checks: phase 2 field must be null")
    return F.assert_public(doc)


def _basis(koios_calls, bf_calls):
    if bf_calls and koios_calls:
        return "mixed"
    if bf_calls:
        return "blockfrost"
    return "koios"


def run(http, cfg, tracked, launches, watch, data_dir, cache_dir, dry_run, now_s, bf_key=None, baseline=False):
    """Write dev_checks.json (unless dry_run). Returns (summary, triggers).

    `tracked` is a list of {ticker, unit, launch, mcap_ada, vol_h24_ada} for tracked Solada launches.
    `launches` is the Solada payload already fetched this run (serial counts). Full stake keys are
    written only to the private cache, and only when that directory is not the public data dir.
    Does not set http.deadline. Raises nothing for a data gap: skipped names are listed in coverage.
    """
    dc = dc_cfg(cfg)
    if not dc.get("enabled", True):
        return {"enabled": False}, []
    if bf_key is None:
        bf_key = os.environ.get("BLOCKFROST_PROJECT_ID", "")
    ex = F.excluded_tags((cfg or {}).get("flags"))
    path = os.path.join(data_dir, "dev_checks.json")
    prev_doc = KT.read_json(path, {})
    prev_rows = {r.get("ticker"): r for r in (prev_doc.get("entries") or []) if isinstance(r, dict) and r.get("ticker")}
    if not launches:
        # an empty payload is a fetch failure (prefilter already logged it); do not wipe a good file or seed a baseline
        return {"skipped": "launches unavailable", "wrote": False, "entries": len(prev_rows)}, []
    cpath = _private_cache(cache_dir, data_dir)
    cache = KT.read_json(cpath, {}) if cpath else {}
    if not isinstance(cache, dict):
        cache = {}
    cache.setdefault("units", {})
    if not isinstance(cache["units"], dict):
        cache["units"] = {}
    rows_in = [r for r in (tracked or []) if isinstance(r, dict) and r.get("ticker") and r.get("unit")]
    rows_in.sort(key=lambda r: r["ticker"])
    if len(rows_in) > 1:
        k = int(cache.get("rot") or 0) % len(rows_in)
        rows_in = rows_in[k:] + rows_in[:k]
        cache["rot"] = int(cache.get("rot") or 0) + 1
    # Wall clock, not the asof timestamp: a historical now_s must not make the budget look already spent.
    deadline = __import__("time").time() + float(dc["budget_s"])
    chain = DevChain(http, dc, bf_key, deadline)
    limit = max(0, int(dc["max_launches"]))
    scans, stop = {}, None
    for item in rows_in[:limit]:
        stop = chain.stop_reason()
        if stop:
            break
        item = dict(item, watch=watch)
        try:
            scans[item["unit"]] = _scan(chain, item, ex, dc)
        except Exception as e:  # one bad launch must not drop the rest or the webhook
            print(f"dev_checks: {item.get('ticker')} skipped ({type(e).__name__})", file=sys.stderr)
            scans[item["unit"]] = {"stake": _launcher_stake(item.get("launch"), ex), "skipped": "error",
                                   "complete": False, "txs_read": 0, "unread_txs": 0, "saw_newest": False,
                                   "last_tx_s": None, "stats": None}
        sc = scans[item["unit"]]
        _remember(cache, item["unit"], sc.get("stake"), (item.get("launch") or {}).get("liveAtMs"),
                  (sc.get("stats") or {}).get("late_rug") if sc.get("complete") else None, sc.get("complete"))
    # priors: other recent launches by a dev we already identified, only with calls left
    scanned_units = set(scans)
    stakes = {sc.get("stake") for sc in scans.values() if sc.get("stake")}
    priors = []
    for l in launches:
        st = _launcher_stake(l, ex)
        unit = (l.get("policyId") or "") + (l.get("assetName") or "")
        if not st or st not in stakes or not unit or unit in scanned_units:
            continue
        live = l.get("liveAtMs")
        if not live or (float(now_s) * 1000 - float(live)) > float(dc["serial_7d_h"]) * 3_600_000:
            continue
        known = cache["units"].get(unit) or {}
        if known.get("complete") and known.get("stake") == st and known.get("late_rug") is not None:
            continue
        priors.append({"ticker": l.get("ticker") or unit[:8], "unit": unit, "launch": l, "watch": watch,
                       "mcap_ada": None, "vol_h24_ada": None})
    prior_done = 0
    if chain.stop_reason() is None:
        for item in priors[:int(dc["max_prior"])]:
            if chain.stop_reason():
                break
            try:
                sc = _scan(chain, item, ex, dc)
            except Exception as e:
                print(f"dev_checks: prior {item.get('ticker')} skipped ({type(e).__name__})", file=sys.stderr)
                continue
            prior_done += 1
            scanned_units.add(item["unit"])
            _remember(cache, item["unit"], sc.get("stake"), (item.get("launch") or {}).get("liveAtMs"),
                      (sc.get("stats") or {}).get("late_rug") if sc.get("complete") else None, sc.get("complete"))
    prior_unchecked = max(0, len(priors) - prior_done)
    now_ms = float(now_s) * 1000
    # entries follow tracker order (ticker), not the rotated scan order
    by_unit = {r["unit"]: r for r in (tracked or []) if isinstance(r, dict) and r.get("unit")}
    entries, triggers = [], []
    for item in sorted(by_unit.values(), key=lambda r: r.get("ticker") or ""):
        item = dict(item, watch=watch)
        sc = scans.get(item["unit"])
        if sc is None:
            why = stop or ("budget" if item["unit"] not in {r["unit"] for r in rows_in[:limit]} else chain.stop_reason() or "budget")
            sc = {"stake": _launcher_stake(item.get("launch"), ex), "skipped": why, "stats": None}
        prev = prev_rows.get(item.get("ticker"))
        row = _entry(item, sc, prev, cache, launches, dc, now_s, now_ms, baseline)
        entries.append(row)
        trig = trigger_for(row)
        if trig is not None:
            triggers.append(trig)
    koios_calls = http.calls.get("koios", 0) - chain.start
    bf_calls = http.calls.get("blockfrost", 0) - chain.bf_start
    skipped = [{"ticker": e["ticker"], "reason": e["coverage"]["skipped"]} for e in entries if e["coverage"].get("skipped")]
    coverage = {
        "tracked": len(entries), "scanned": len(scans), "complete": sum(1 for e in entries if e["coverage"].get("complete")),
        "skipped": skipped, "prior_unchecked": prior_unchecked,
        "koios_calls": koios_calls, "blockfrost_calls": bf_calls, "blockfrost_fallbacks": chain.fallbacks,
        "blockfrost_available": bool(chain.bf_key), "basis": _basis(koios_calls, bf_calls),
        "budget_s": dc["budget_s"], "max_calls": dc["max_calls"],
    }
    doc = _guard({
        "at": iso(now_s), "phase": 1, "note": NOTE,
        "thresholds": {k: dc[k] for k in ("rug_bag_pct", "late_min_minutes", "rug_min_other_holders", "min_sell_ada",
                                          "inactive_h", "serial_caution_3h", "serial_caution_7d", "drawdown_caution_pct")},
        "entries": entries, "coverage": coverage,
    })
    if not dry_run:
        if cpath:
            KT.write_json(cpath, cache)
        KT.write_json(path, doc, indent=1)
    summary = {"entries": len(entries), "triggers": len(triggers), "skipped": len(skipped),
               "complete": coverage["complete"], "prior_unchecked": prior_unchecked,
               "koios_calls": koios_calls, "blockfrost_calls": bf_calls, "blockfrost_fallbacks": chain.fallbacks,
               "basis": coverage["basis"], "wrote": not dry_run}
    return summary, triggers
