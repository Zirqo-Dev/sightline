"""Phase 1 dev checks (zero AI). One row per tracked Solada launch.

Writes desk-data:solada-prefilter/dev_checks.json. Chain data only (Koios, then Blockfrost
when BLOCKFROST_PROJECT_ID is set). No external score API.

The dev is the launch's creator stake: Solada launcherAddress, or, when that has no stake
key, the non-script stake that funded a verified mint tx. Output carries a short dev_tag
only (a flags/watch prefix of at most 12 characters when one matches, else the same
w-xxxxxxxx hash flags.py uses). Never a full address or stake key. Rows and trigger keys
are the token (policy + asset name): a ticker that changes token is a new baseline.

Phase 1 is dev sells / late rugs, serial-launcher counts, and drawdown / inactivity.
dev_buy_ada, dev_locked_pct and unlock_date are always null. LP pulls are phase 2, with
dev lock, dev buy class, and social checks.

A sell is one transaction whose creator key UTxOs lose this token and gain ADA, or a
later tx that pays the dev ADA for tokens they had parked at a script without returning
those tokens (a cancel is not a sell; a partial fill counts only the filled quantity).
Staking-reward withdrawals in that tx are not part of the ADA gain. late_rug needs both
the net drop from the dev's peak and tokens actually sold (including batcher fills) of
at least rug_bag_pct. A lock or vesting transfer is not a sell, and a round trip is not a rug.

The first row for a token is a baseline (no webhook). Later, a dev_sell fires only for a
sell tx hash the private cache has not seen, and only when the new sell is at least
min_sell_ada or late_rug flips true. A public row with no sell-cache entry is
re-baselined (hashes recorded, no fire). An incomplete read never lowers stored sell
facts; the row is marked partial. Drawdown, serial counts and inactivity do not re-fire.
"""
import hashlib, os, re, sys, time
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
    "min_sell_ada": 25.0, "inactive_h": 24, "serial_3h_h": 3, "serial_7d_h": 168,
    "serial_caution_3h": 2, "serial_caution_7d": 3, "drawdown_caution_pct": 80,
}
PHASE2 = ("dev_buy_ada", "dev_locked_pct", "unlock_date")
NOTE = (
    "Phase 1 only: creator-stake sells and late rugs, serial launch counts, drawdown from the "
    "peak this job has seen, and inactivity. dev_buy_ada, dev_locked_pct, unlock_date and LP pulls "
    "are phase 2 (the three fields are always null). No dev lock, dev buy class, or social checks. "
    "dev_tag is a watch prefix or a 10-character hash, never an address or stake key. Rows are keyed "
    "by policy + asset name. ATH is the highest mcap this job has observed (the run is every 2h), not "
    "a tick peak. prior_rugs counts earlier launches this job has already marked late_rug for the same "
    "creator stake. A sell is token out and ADA in on the creator's own key in one tx, or ADA paid "
    "later for tokens parked at a script (a cancel is not a sell). late_rug needs the net drop and "
    "tokens actually sold, both at least rug_bag_pct of that stake's peak; a lock is not a sell. "
    "min_sell_ada is a dust floor on the ADA of sell txs not seen before. A partial read adds that "
    "fresh ADA to the stored total and does not lower it. An incomplete read is marked partial."
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


def _enterprise_key(addr):
    """True for a stake-less key address (addr1v / enterprise). Script enterprise (addr1w) is not a holder."""
    hrp, raw = F.b32_decode(addr or "")
    if not raw or hrp not in ("addr", "addr_test"):
        return False
    return (raw[0] >> 4) == 6


def _holder_key(u, ex):
    """Stake key, else an enterprise key address. Scripts and excluded tags are neither holders nor sellers."""
    st, script, addr = _who(u)
    if script or _hit(st, addr, ex):
        return None
    if st:
        return st
    if addr and _enterprise_key(addr):
        return "e:" + addr
    return None


def _qty(u, unit):
    try:
        return int((u.get("assets") or {}).get(unit, 0) or 0)
    except (TypeError, ValueError):
        return 0


def creator_from_mint(unit, tx, ex):
    """Stake that funded a verified mint. None when this tx is not the mint, or the funder is a script or excluded.

    Verified mint: the token is on outputs and on no input (it is created here). The dev is the non-script,
    non-excluded stake that paid the largest ADA into that tx, not the largest recipient.
    """
    inputs, outputs = tx.get("inputs") or [], tx.get("outputs") or []
    if sum(_qty(u, unit) for u in inputs) > 0 or sum(_qty(u, unit) for u in outputs) <= 0:
        return None
    best, lov = None, -1
    for u in inputs:
        st, script, addr = _who(u)
        if script or not st or _hit(st, addr, ex):
            continue
        n = int(u.get("lovelace") or 0)
        if n > lov:
            best, lov = st, n
    return best


def fold(unit, ordered, dev, ex, launch_s, dc):
    """Replay txs in chain order. Pure. Returns sell stats for `dev` (or zeros if dev is None).

    Excluded tags (the Solada curve reserve) are neither holders nor sellers. Script payment addresses
    are not holders; stake-less enterprise key addresses are. Peak is the dev's balance before each sell.
    late_rug needs a late sell, a net drop of rug_bag_pct, and tokens actually sold (same-tx or batcher
    fills) of at least rug_bag_pct of peak. Selling and buying back is churn. Tokens sent to a lock or
    vesting script are not a sell. ADA gained is net of staking withdrawals.
    Tokens the dev parks at a script stay pending until a later tx pays the dev ADA for the filled part.
    A cancel returns the tokens (balance goes up) plus the deposit and is not a sell.
    """
    bal, peak = {}, 0
    sold_ada, sold_qty = 0.0, 0
    first_sell_s, holders_at, late_sell = None, None, False
    sell_hashes, sell_ada, pending = [], {}, {}
    for tx in ordered:
        deltas, dev_qty, dev_lov = {}, 0, 0
        for side, sign in (("inputs", -1), ("outputs", 1)):
            for u in tx.get(side) or []:
                key = _holder_key(u, ex)
                q = _qty(u, unit)
                if key and q:
                    deltas[key] = deltas.get(key, 0) + sign * q
                if dev and key == dev:
                    dev_lov += sign * int(u.get("lovelace") or 0)
                    dev_qty += sign * q
        if dev:
            wd = (tx.get("withdrawals") or {}).get(dev) or 0
            try:
                dev_lov -= int(wd)
            except (TypeError, ValueError):
                pass
            peak = max(peak, bal.get(dev, 0))
        for st, dq in deltas.items():
            bal[st] = bal.get(st, 0) + dq
            if bal[st] <= 0:
                bal.pop(st, None)
        if dev and dev_qty > 0:
            peak = max(peak, bal.get(dev, 0))
        script_outs = []
        for u in tx.get("outputs") or []:
            st, script, addr = _who(u)
            ref, q = u.get("ref") or "", _qty(u, unit)
            if script and q and ref and not _hit(st, addr, ex):
                script_outs.append((ref, q))
        spent = [u.get("ref") for u in (tx.get("inputs") or []) if u.get("ref") in pending]
        is_sell, sell_q, sell_lov = False, 0, 0.0
        if dev and dev_qty < 0 and dev_lov > 0:
            is_sell, sell_q, sell_lov = True, -dev_qty, dev_lov / 1e6
            sold_ada += sell_lov
        elif dev and dev_qty < 0 and script_outs:
            left = -dev_qty
            for ref, q in script_outs:
                take = min(left, q)
                if take > 0:
                    pending[ref] = pending.get(ref, 0) + take
                    left -= take
                if left <= 0:
                    break
        elif dev and dev_lov > 0 and spent:
            parked = sum(pending.pop(ref, 0) for ref in spent)
            returned = dev_qty if dev_qty > 0 else 0  # cancel, or change paid back to the dev
            order_addrs = set()
            for u in tx.get("inputs") or []:
                if u.get("ref") in spent:
                    _st, script, addr = _who(u)
                    if script and addr:
                        order_addrs.add(addr)
            relisted = 0
            for u in tx.get("outputs") or []:
                st, script, addr = _who(u)
                ref, q = u.get("ref") or "", _qty(u, unit)
                # unfilled tokens that go straight back into an order at the same script are not sold
                if script and q and ref and addr in order_addrs and not _hit(st, addr, ex):
                    pending[ref] = pending.get(ref, 0) + q
                    relisted += q
            filled = parked - returned - relisted
            if filled > 0:
                is_sell, sell_q, sell_lov = True, filled, dev_lov / 1e6
                sold_ada += sell_lov
        elif spent:
            for ref in spent:
                pending.pop(ref, None)
        if not is_sell:
            continue
        sold_qty += sell_q
        if tx.get("hash"):
            sell_hashes.append(tx["hash"])
            sell_ada[tx["hash"]] = round(sell_ada.get(tx["hash"], 0.0) + sell_lov, 2)
        holders = sum(1 for q in bal.values() if q > 0)
        if first_sell_s is None:
            first_sell_s = tx.get("time")
            holders_at = holders
        after = (tx["time"] - launch_s) / 60.0 if launch_s and tx.get("time") else None
        if after is not None and after >= float(dc["late_min_minutes"]):
            late_sell = True
    cur = bal.get(dev, 0) if dev else 0
    net = ((peak - cur) / peak * 100.0) if peak else 0.0
    sold_pct = (sold_qty / peak * 100.0) if peak else 0.0
    others = sum(1 for s, q in bal.items() if s != dev and q > 0)
    pending_qty = sum(pending.values())
    # net drop alone is not a rug: a lock can empty the wallet without a sale
    bag = float(dc["rug_bag_pct"])
    late = bool(late_sell and net >= bag and sold_pct >= bag and others >= int(dc["rug_min_other_holders"]))
    # no launch time: a sell cannot be called late or not-late. None keeps a stored True.
    if not launch_s and (sell_hashes or sold_qty):
        late = None
    sharp = bool(peak and (net >= float(dc["rug_bag_pct"]) or pending_qty / peak * 100.0 >= float(dc["rug_bag_pct"])))
    return {"peak": peak, "sold_ada": round(sold_ada, 2), "sold_qty": sold_qty, "first_sell_s": first_sell_s,
            "holders_at_sell": holders_at, "late_rug": late, "sell_hashes": sell_hashes, "sell_ada": sell_ada,
            "net_drop_pct": round(net, 2), "pending_qty": pending_qty, "sharp": sharp}


def serial_counts(launches, stake, now_ms, dc, this_unit=None):
    """How many Solada launches in this payload share `stake` and went live in the last 3h / 7d.

    Includes this launch. A mint-found dev (launcherAddress has no stake) still counts `this_unit`.
    Launches with no liveAtMs are not counted. Other launches with no creator stake are not counted.
    """
    if not stake or launches is None:
        return None, None  # no board: do not report serial 0 (that would read as "this dev launched nothing else")
    n3 = n7 = 0
    h3, h7 = float(dc["serial_3h_h"]) * 3_600_000, float(dc["serial_7d_h"]) * 3_600_000
    for l in launches or []:
        st = F.addr_info(l.get("launcherAddress") or "")[0]
        unit = (l.get("policyId") or "") + (l.get("assetName") or "")
        if st == stake or (this_unit and unit == this_unit and not st):
            pass
        else:
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


def risk_of(sold, late, prior, s3, s7, drawdown, inactive, complete, dc, sharp=False):
    """low | caution | high, or None when a partial read is not enough to call it low.

    sharp: the dev's balance fell by rug_bag_pct, or that much is still parked at a script.
    That is never "low" (a batcher order is not a same-tx sell, and a round trip is not a rug).
    """
    if late is True or (prior or 0) >= 1:
        return "high"
    caution = ((sold or 0) >= float(dc["min_sell_ada"]) or (s3 or 0) >= int(dc["serial_caution_3h"])
               or (s7 or 0) >= int(dc["serial_caution_7d"])
               or (drawdown is not None and drawdown >= float(dc["drawdown_caution_pct"]))
               or inactive is True or sharp)
    if caution:
        return "caution"
    if late is None or not complete or sharp:
        return None
    return "low"


def sell_changed(prev, sold, late, min_ada):
    """True only when a previous row exists and the sell picture moved. The first row for a token is a baseline."""
    if not isinstance(prev, dict):
        return False
    prev_sold = float(prev.get("dev_sold_ada") or 0)
    grew = sold is not None and round(float(sold) - prev_sold, 2) >= float(min_ada)
    return grew or (late is True and prev.get("late_rug") is not True)


def _may_fire(prev, baseline, item=None):
    """Both baseline guards. The first row for a token, and a --baseline run, never fire.

    A top-10 row with no launcher address stays baseline-only: the mint funder of an older
    token is not a Solada dev, so a later sell does not fire.
    """
    if not isinstance(prev, dict):
        return False
    if baseline:
        return False
    if isinstance(item, dict) and item.get("baseline_only"):
        if not ((item.get("launch") or {}).get("launcherAddress")):
            return False
    return True


def _worth_firing(fresh_ada, late, prev, dc):
    """Fire when the ADA on unseen sell txs is at least min_sell_ada, or late_rug flipped.

    `fresh_ada` is the sum for hashes the private cache has not seen. It is not this read's total
    and not that total minus the stored total: a partial window often misses the older sells.
    """
    if fresh_ada is not None and float(fresh_ada) >= float(dc["min_sell_ada"]):
        return True
    return late is True and (not isinstance(prev, dict) or prev.get("late_rug") is not True)


def _keep_sell(row, prev, complete):
    """Never lower stored sell facts on an incomplete read. Mark the row partial instead."""
    if complete or not isinstance(prev, dict):
        return row
    old = prev.get("dev_sold_ada")
    new = row.get("dev_sold_ada")
    if old is not None and (new is None or float(new) < float(old)):
        row["dev_sold_ada"] = old
        if prev.get("dev_sold_after_min") is not None:
            row["dev_sold_after_min"] = prev.get("dev_sold_after_min")
    if prev.get("late_rug") is True and row.get("late_rug") is not True:
        row["late_rug"] = True
    if row.get("holders_at_sell") is None and prev.get("holders_at_sell") is not None and prev.get("late_rug") is True:
        row["holders_at_sell"] = prev.get("holders_at_sell")
    if row.get("dev_tag") is None and prev.get("dev_tag"):
        row["dev_tag"] = prev.get("dev_tag")
    row["coverage"]["partial"] = True
    return row


def inactive_of(last_tx_s, saw_newest, vol, now_s, launch_s, hours, volume_counts=True):
    """True when nothing has traded for about `hours` (default 24). Newest chain tx wins; else 24h pool volume.

    volume_counts=False when the scan was skipped: a volume of 0 from a board we did not
    confirm is not inactivity.
    """
    window = float(hours) * 3600.0
    if saw_newest and last_tx_s:
        return (now_s - last_tx_s) >= window
    if not volume_counts:
        if saw_newest and not last_tx_s and launch_s:
            return (now_s - launch_s) >= window
        return None
    if saw_newest and not last_tx_s:
        if launch_s:
            return (now_s - launch_s) >= window
        if vol is not None:
            return float(vol) <= 0
        return None
    if vol is not None:
        return float(vol) <= 0
    return None


def ath_drawdown(prev, mcap, basis=None):
    """Peak mcap this job has stored, percent down from it, and the basis of that peak.

    Unknown current mcap leaves drawdown null and keeps the stored peak. A reading on a
    different basis starts a new peak and does not report a drop against the old one.
    Returns (ath, drawdown, basis).
    """
    prev = prev if isinstance(prev, dict) else {}
    raw_ath = prev.get("ath_mcap")
    ath = float(raw_ath) if isinstance(raw_ath, (int, float)) and not isinstance(raw_ath, bool) else None
    prev_basis = prev.get("mcap_basis") if isinstance(prev.get("mcap_basis"), str) and prev.get("mcap_basis") else None
    cur = float(mcap) if isinstance(mcap, (int, float)) and not isinstance(mcap, bool) else None
    cur_basis = basis if isinstance(basis, str) and basis else None
    tagged = bool(cur_basis or prev_basis)
    same = (cur_basis == prev_basis) if tagged else True
    if cur is not None and cur >= 0 and ath is not None and not same:
        shown = int(cur) if cur == int(cur) else round(cur, 2)
        return shown, None, cur_basis
    if cur is not None and cur >= 0 and (same or ath is None):
        ath = cur if ath is None else max(ath, cur)
    dd = None
    if ath and cur is not None and ath > 0 and (same or raw_ath is None):
        dd = round(max(0.0, (ath - cur) / ath * 100.0), 1)
    out_basis = prev_basis
    if cur is not None and (same or raw_ath is None):
        out_basis = cur_basis or prev_basis
    if ath is None:
        return None, dd, out_basis
    shown = int(ath) if ath == int(ath) else round(ath, 2)
    return shown, dd, out_basis


def _blank(ticker, policy_short, asof, unit=None):
    row = {
        "ticker": ticker, "unit": unit, "policy_short": policy_short, "dev_tag": None,
        "dev_sold_ada": None, "dev_sold_after_min": None, "holders_at_sell": None, "late_rug": None,
        "serial_3h": None, "serial_7d": None, "prior_rugs": None,
        "ath_mcap": None, "drawdown_pct": None, "inactive": None, "risk": None,
        "changed_since_last": False, "asof": asof,
    }
    for k in PHASE2:
        row[k] = None
    row["coverage"] = {"txs_read": 0, "complete": False, "partial": False, "skipped": None, "unread_txs": 0}
    return row


def _launch_s(launch):
    ms = (launch or {}).get("liveAtMs")
    return float(ms) / 1000.0 if ms else None


def _known_launch_s(launch, scan):
    """Board launch time, else the verified mint tx time the scan found."""
    s = _launch_s(launch)
    if s is not None:
        return s
    alt = (scan or {}).get("launch_s")
    if isinstance(alt, (int, float)) and not isinstance(alt, bool):
        return float(alt)
    return None


def _verified_mint_s(unit, ordered):
    """Block time of a tx that creates `unit` (token on outputs, on no input)."""
    for tx in ordered or []:
        inputs, outputs = tx.get("inputs") or [], tx.get("outputs") or []
        if sum(_qty(u, unit) for u in inputs) > 0:
            continue
        if sum(_qty(u, unit) for u in outputs) > 0 and tx.get("time"):
            return float(tx["time"])
    return None


def _policy_short(unit):
    return unit[:12] if unit else None


class DevChain:
    """Koios first. A failed Koios read is retried on Blockfrost when the project id is set, under its own cap.
    The key is sent only as a request header. This class does not set Http.deadline, so a 429 here cannot trip
    the research circuit breaker. The first 429 stops further Koios calls and sets koios_pause_until instead."""

    def __init__(self, http, dc, bf_key, deadline):
        self.h, self.dc, self.deadline = http, dc, deadline
        self.bf_key = (bf_key or "").strip()
        self.start = http.calls.get("koios", 0)
        self.bf_start = http.calls.get("blockfrost", 0)
        self.fallbacks = 0
        self.koios_paused = False

    def late(self):
        return __import__("time").time() >= self.deadline

    def koios_left(self):
        return int(self.dc["max_calls"]) - (self.h.calls.get("koios", 0) - self.start)

    def bf_left(self):
        return int(self.dc["max_bf_calls"]) - (self.h.calls.get("blockfrost", 0) - self.bf_start)

    def stop_reason(self):
        if self.late():
            return "deadline"
        if not self.koios_open():
            # a 429 already stopped Koios; Blockfrost may finish the run inside its own cap
            if self.bf_key and self.bf_left() > 0:
                return None
            return "budget"
        if self.koios_left() <= 0:
            return "budget"
        return None

    def koios_open(self):
        """False after a 429: no more Koios calls this run. Blockfrost may still be used."""
        if self.koios_paused:
            return False
        if getattr(self.h, "dev_checks", False) and (getattr(self.h, "koios_pause_until", 0) or 0) > time.time():
            self.koios_paused = True
            return False
        return True

    def note_koios(self):
        if getattr(self.h, "dev_checks", False) and (getattr(self.h, "koios_pause_until", 0) or 0) > time.time():
            self.koios_paused = True

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

    def _ada_up(self, tx, dev):
        """True when `dev`'s lovelace rose in this tx, before withdrawals are subtracted."""
        if not dev:
            return False
        net = 0
        for side, sign in (("inputs", -1), ("outputs", 1)):
            for u in tx.get(side) or []:
                if (u.get("stake") or "") != dev:
                    continue
                try:
                    net += sign * int(u.get("lovelace") or 0)
                except (TypeError, ValueError):
                    pass
        return net > 0

    def _pull_withdrawals(self, h):
        """Blockfrost /txs/{h}/withdrawals. None when the call budget or the deadline says not to call."""
        if not self.bf_key or self.bf_left() <= 0 or self.late():
            return None
        rows = self.bf(f"/txs/{h}/withdrawals")
        out = {}
        if not isinstance(rows, list):
            return {}
        for w in rows:
            st = w.get("address") or w.get("stake_address") or ""
            try:
                amt = int(w.get("amount") or 0)
            except (TypeError, ValueError):
                amt = 0
            if st and amt > 0:
                out[st] = out.get(st, 0) + amt
        return out

    def _maybe_withdrawals(self, parsed, h, dev):
        if not dev or not self._ada_up(parsed, dev) or "withdrawals" in parsed:
            return
        got = self._pull_withdrawals(h)
        if got is not None:
            parsed["withdrawals"] = got

    def attach_withdrawals(self, ordered, dev):
        """For a dev learned after the utxo read (mint fallback): fetch withdrawals only where ADA rose."""
        for tx in ordered or []:
            if not tx.get("hash"):
                continue
            self._maybe_withdrawals(tx, tx["hash"], dev)
            if self.bf_left() <= 0 or self.late():
                break

    def asset_txs(self, unit, desc=False):
        if self.stop_reason() and self.koios_open():
            return None
        if self.late():
            return None
        lim = int(self.dc["max_txs_per_unit"])
        order = "desc" if desc else "asc"
        url = (f"{KOIOS}/asset_txs?_asset_policy={unit[:56]}&_asset_name={unit[56:]}"
               f"&_history=true&order=block_height.{order}&limit={lim}")
        r, allow_bf = None, not self.koios_open()
        if self.koios_open() and self.koios_left() > 0:
            r = self.h.req("koios", url, quiet404=False)
            self.note_koios()
            allow_bf = r is None
        elif self.koios_left() <= 0 and self.koios_open():
            return None
        if r is None and allow_bf and self._fallback():
            r = self.bf(f"/assets/{unit}/transactions?order={order}&count={min(lim, 100)}")
        if not isinstance(r, list):
            return None
        rows = [(t.get("tx_hash"), t.get("block_height") or 0, t.get("block_time") or 0)
                for t in r if t.get("tx_hash")]
        rows.sort(key=lambda t: (t[1], t[0]), reverse=desc)
        return rows[:lim]

    def utxos(self, hashes, dev=None):
        out = {}
        for body in KT.batches(hashes, "_tx_hashes", int(self.dc["utxo_batch"]), KT.TX_INFO_FLAGS):
            if self.late():
                break
            chunk = body["_tx_hashes"]
            got = None
            if self.koios_open() and self.koios_left() > 0:
                def budget_ok(chain=self):
                    if not chain.koios_open() or chain.koios_left() <= 0 or chain.late():
                        return False
                    return True
                got = KT.tx_info(self.h, chunk, len(chunk), budget_ok)
                self.note_koios()
            if not got and self._fallback():
                for h in chunk:
                    if self.late():
                        break
                    x = self.bf(f"/txs/{h}/utxos")
                    if x:
                        parsed = self._bf_utxo(x)
                        self._maybe_withdrawals(parsed, h, dev)
                        out[h] = parsed
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


def _remember(cache, unit, stake, live_ms, late, complete, ticker=None, baseline_only=False):
    if not stake:
        return
    prev = (cache.get("units") or {}).get(unit) or {}
    row = {
        "stake": stake, "live_at_ms": live_ms if live_ms is not None else prev.get("live_at_ms"),
        "late_rug": late if late is not None else prev.get("late_rug"),
        "complete": bool(complete) or bool(prev.get("complete")),
    }
    name = ticker or prev.get("ticker")
    if name:
        row["ticker"] = str(name).upper()
    if baseline_only:
        row["baseline_only"] = True
    cache.setdefault("units", {})[unit] = row


def _scan(chain, item, ex, dc):
    """Read one unit. Stake keys stay in the returned dict for the caller; they are not a public field."""
    launch, unit = item.get("launch") or {}, item.get("unit") or ""
    stake = _launcher_stake(launch, ex)
    if not stake:
        ks = item.get("known_stake")
        if isinstance(ks, str) and ks and not _hit(ks, "", ex):
            stake = ks
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
    known = stake
    ut = chain.utxos([t[0] for t in txs], dev=known) if txs else {}
    ordered, unread = [], 0
    for h, hg, bt in txs:
        if h not in ut:
            unread += 1
            continue
        ordered.append({"hash": h, "height": hg, "time": bt, **ut[h]})
    if not stake and ordered:
        stake = creator_from_mint(unit, ordered[0], ex)
        base["stake"] = stake
        if stake:
            chain.attach_withdrawals(ordered, stake)
    complete = len(asc) < lim and unread == 0 and (desc_rows is None or isinstance(desc_rows, list) and len(asc) < lim)
    # a full first page means the middle of the history was not read
    if len(asc) >= lim:
        complete = False
    saw_newest = len(asc) < lim or isinstance(desc_rows, list)
    launch_s = _launch_s(launch)
    if launch_s is None:
        launch_s = _verified_mint_s(unit, ordered)
    stats = fold(unit, ordered, stake, ex, launch_s, dc)
    if complete and stats["sold_ada"] == 0:
        stats["holders_at_sell"] = None
    # An incomplete read keeps the observed sells. _entry never lowers a stored sell and marks the row partial.
    base.update(complete=complete, txs_read=len(ordered), unread_txs=unread, saw_newest=saw_newest,
                last_tx_s=max((t["time"] for t in ordered), default=None), stats=stats, stake=stake,
                launch_s=launch_s)
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


def _entry(item, scan, prev, cache, launches, dc, now_s, now_ms, baseline, new_hashes=(), fresh_ada=0.0):
    launch = item.get("launch") or {}
    unit = item.get("unit") or ""
    row = _blank(item.get("ticker"), _policy_short(unit), iso(now_s), unit)
    stake = (scan or {}).get("stake")
    row["dev_tag"] = short_tag(stake, item.get("watch"))
    s3, s7 = serial_counts(launches, stake, now_ms, dc, this_unit=unit)
    if launches is None and stake and isinstance(prev, dict) and prev.get("unit") == unit:
        if s3 is None and prev.get("serial_3h") is not None:
            s3 = prev.get("serial_3h")
        if s7 is None and prev.get("serial_7d") is not None:
            s7 = prev.get("serial_7d")
    row["serial_3h"], row["serial_7d"] = s3, s7
    launch_s = _known_launch_s(launch, scan)
    live_ms = launch.get("liveAtMs")
    if live_ms is None and launch_s is not None:
        live_ms = int(launch_s * 1000)
    row["prior_rugs"] = prior_rug_count(cache, stake, unit, live_ms)
    ath, dd, mcap_basis = ath_drawdown(prev, item.get("mcap_ada"), item.get("mcap_basis"))
    row["ath_mcap"], row["drawdown_pct"] = ath, dd
    if mcap_basis:
        row["mcap_basis"] = mcap_basis
    skipped = (scan or {}).get("skipped")
    stats = (scan or {}).get("stats")
    if skipped or not stats:
        if isinstance(prev, dict) and prev.get("unit") == unit:
            for k in ("dev_tag", "dev_sold_ada", "dev_sold_after_min", "holders_at_sell", "late_rug"):
                if prev.get(k) is not None and (k != "dev_tag" or row["dev_tag"] is None):
                    row[k] = prev.get(k)
            if row["ath_mcap"] is None and prev.get("ath_mcap") is not None:
                row["ath_mcap"] = prev.get("ath_mcap")
            if not row.get("mcap_basis") and prev.get("mcap_basis"):
                row["mcap_basis"] = prev.get("mcap_basis")
        row["inactive"] = inactive_of(None, False, item.get("vol_h24_ada"), now_s, launch_s, dc["inactive_h"],
                                      volume_counts=not bool(skipped))
        risk = risk_of(row["dev_sold_ada"], row["late_rug"], row["prior_rugs"], s3, s7, row["drawdown_pct"],
                       row["inactive"], False, dc)
        if risk is None and isinstance(prev, dict) and prev.get("unit") == unit and prev.get("risk") in ("low", "caution", "high"):
            risk = prev.get("risk")
        row["risk"] = risk
        row["changed_since_last"] = False
        row["coverage"] = {"txs_read": 0, "complete": False, "partial": bool(prev), "skipped": skipped or "budget", "unread_txs": 0}
        return _no_stake(row, stake, keep=True)
    complete = bool(scan.get("complete"))
    observed = stats["sold_ada"]
    raw_late = stats["late_rug"]
    if raw_late is True:
        late = True
    elif raw_late is None:
        late = None
    else:
        late = False if complete else None
    after = None
    if stats.get("first_sell_s") and launch_s:
        after = round((stats["first_sell_s"] - launch_s) / 60.0, 1)
    row["dev_sold_ada"] = observed
    row["dev_sold_after_min"] = after
    row["holders_at_sell"] = stats["holders_at_sell"] if (complete or stats["late_rug"]) else None
    row["late_rug"] = late
    row["inactive"] = inactive_of(scan.get("last_tx_s"), scan.get("saw_newest"), item.get("vol_h24_ada"),
                                  now_s, launch_s, dc["inactive_h"])
    row["coverage"] = {"txs_read": scan.get("txs_read") or 0, "complete": complete, "partial": not complete,
                       "skipped": None, "unread_txs": scan.get("unread_txs") or 0}
    # only the same policy+asset continues a row. A ticker that changed token is a new baseline.
    same = prev if isinstance(prev, dict) and prev.get("unit") == unit else None
    if not complete and isinstance(same, dict):
        # this window can miss older sells, so add only the ADA on hashes not seen before
        row["dev_sold_ada"] = round(float(same.get("dev_sold_ada") or 0) + float(fresh_ada or 0), 2)
    if not complete:
        _keep_sell(row, same, False)
    elif row.get("late_rug") is None and isinstance(same, dict) and same.get("late_rug") is True:
        # a complete read that still cannot time the launch must not erase a real late rug
        row["late_rug"] = True
        if row.get("dev_sold_after_min") is None and same.get("dev_sold_after_min") is not None:
            row["dev_sold_after_min"] = same["dev_sold_after_min"]
    sharp = bool(stats.get("sharp"))
    risk = risk_of(row["dev_sold_ada"], row["late_rug"], row["prior_rugs"], s3, s7, dd, row["inactive"],
                   complete, dc, sharp=sharp)
    if risk == "low" and (not complete or sharp):
        risk = "caution" if sharp else None
    if risk is None and isinstance(same, dict) and same.get("risk") in ("low", "caution", "high"):
        risk = same.get("risk")
    row["risk"] = risk
    row["changed_since_last"] = bool(
        _may_fire(same, baseline, item) and new_hashes and _worth_firing(fresh_ada, row["late_rug"], same, dc))
    if new_hashes:
        row["_new_sell"] = new_hashes[-1]
    return _no_stake(row, stake)


def trigger_for(row):
    """One dev_sell trigger when this row's sell picture changed. The key is the token plus the new sell tx."""
    if not row.get("changed_since_last"):
        return None
    sold = float(row.get("dev_sold_ada") or 0)
    late = row.get("late_rug") is True
    if sold < 0 and not late:
        return None
    if sold == 0 and not late:
        return None
    unit = row.get("unit") or ""
    if not unit:
        return None
    metrics = {k: row.get(k) for k in
               ("dev_tag", "dev_sold_ada", "dev_sold_after_min", "holders_at_sell", "late_rug", "risk", "changed_since_last")}
    F.assert_public(metrics)
    tag = metrics.get("dev_tag")
    if tag is not None and len(tag) > 12:
        raise SystemExit("dev_checks: refusing a dev_tag that is not a short tag")
    # public state.json stores this key. Hash the tx prefix so the raw hash is not in the fired map.
    pref = (row.get("_new_sell") or "")[:16]
    digest = hashlib.sha256(pref.encode()).hexdigest()[:16] if pref else ""
    return {"key": f"devsell:{unit}:{digest}", "kind": "dev_sell", "name": row["ticker"], "metrics": metrics}


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


def dev_deadline(budget_s, t_start, now):
    """Wall-clock end for dev checks: min(now + budget, run start + 600s). A past `now_s` is not the clock."""
    start = float(now) if t_start is None else float(t_start)
    return min(float(now) + max(0.0, float(budget_s)), start + 600.0)


_ADDR_IN_PATTERN = re.compile(r"addr1[02-9ac-hj-np-z]{50,}")


def _patterns(owner_env):
    """(compiled patterns, stake keys derived from full-address patterns), or None when unusable (fail closed).

    Never prints a pattern or a derived stake. Stake derivation matches the privacy guard: a full base
    address in a pattern line contributes addr_info's stake, so a sibling address of that wallet matches.
    """
    import big_moves as BM
    if owner_env is None:
        owner_env = os.environ.get("PRIVACY_GUARD_PATTERNS", "")
    pats, bad = BM.owner_patterns(owner_env)
    if bad or not pats:
        print(f"dev_checks: skipped: owner patterns unusable ({bad} bad, {len(pats)} usable)", file=sys.stderr)
        return None
    stakes = set()
    for line in (owner_env or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for addr in _ADDR_IN_PATTERN.findall(line):
            st = F.addr_info(addr)[0]
            if st:
                stakes.add(st)
    return pats, stakes


def _hidden(pats, stakes, *vals):
    for v in vals:
        if not v:
            continue
        if stakes and v in stakes:
            return True
        if pats and any(p.search(v) for p in pats):
            return True
    return False


def _note_sells(cache, unit, observed, prev=None, ticker=None):
    """Union sell-tx hashes into the private cache. Returns hashes not stored before this read.

    No seen-sells entry (missing or not a list) for a token that already has a public row is a
    re-baseline: record this read's hashes, including none, and do not fire. Keeps the newest 2000.
    """
    observed = [h for h in (observed or []) if isinstance(h, str)]
    sells = cache.get("sells") if isinstance(cache.get("sells"), dict) else None
    entry = sells.get(unit) if sells else None
    if isinstance(prev, dict) and prev.get("unit") == unit and not isinstance(entry, list):
        if not isinstance(cache.get("sells"), dict):
            cache["sells"] = {}
        cache["sells"][unit] = observed[-2000:]
        print(f"dev_checks: {ticker or 'token'} sell cache missing; recording current sells without firing",
              file=sys.stderr)
        return []
    prev_h = [h for h in (entry or []) if isinstance(h, str)]
    seen = set(prev_h)
    fresh = [h for h in observed if h not in seen]
    if not isinstance(cache.get("sells"), dict):
        cache["sells"] = {}
    cache["sells"][unit] = (prev_h + fresh)[-2000:]
    return fresh


def _quiet_owner(sc, item, pats, stakes):
    """Drop a creator stake that matches an owner pattern or a stake derived from one. Not cached, not printed."""
    if not sc or not pats:
        return sc
    st = sc.get("stake")
    addr = ((item.get("launch") or {}).get("launcherAddress") or "")
    if st and _hidden(pats, stakes, st, addr):
        sc["stake"] = None
        if sc.get("stats"):
            sc["stats"] = dict(sc["stats"], sell_hashes=[], sell_ada={}, sharp=False)
    return sc


def cardano_top10(log):
    """Snek's Cardano top 10 from the desk log (current row, else the newest history row that has one)."""
    if not isinstance(log, dict):
        return []
    rows = log.get("cardano_top10")
    if isinstance(rows, list) and rows:
        return [r for r in rows if isinstance(r, dict)][:10]
    hist = log.get("history")
    if isinstance(hist, list):
        for h in reversed(hist):
            if isinstance(h, dict) and isinstance(h.get("cardano_top10"), list) and h["cardano_top10"]:
                return [r for r in h["cardano_top10"] if isinstance(r, dict)][:10]
    return []


def _row_for(ticker, unit, live_ms=None, mcap=None, known_stake=None):
    if not ticker or not isinstance(unit, str) or len(unit) < 56:
        return None
    row = {"ticker": str(ticker).upper(), "unit": unit,
           "launch": {"ticker": ticker, "policyId": unit[:56], "assetName": unit[56:], "liveAtMs": live_ms},
           "mcap_ada": mcap if isinstance(mcap, (int, float)) and not isinstance(mcap, bool) else None,
           "vol_h24_ada": None}
    if isinstance(known_stake, str) and known_stake:
        row["known_stake"] = known_stake  # private; never copied onto the public row
    return row


def _ticker_for_unit(unit, prev_rows, units_by_ticker, cached):
    prev = (prev_rows or {}).get(unit) or {}
    if isinstance(prev, dict) and prev.get("ticker"):
        return str(prev["ticker"]).upper()
    if isinstance(cached, dict) and cached.get("ticker"):
        return str(cached["ticker"]).upper()
    if isinstance(units_by_ticker, dict):
        for tk, u in units_by_ticker.items():
            if u == unit:
                return str(tk).upper()
    return None


def _rows_from_cache(cache, prev_rows, units_by_ticker):
    units = cache.get("units") if isinstance(cache.get("units"), dict) else {}
    # A previous public file is the allow-list. Priors scanned for serial counts stay private.
    allow = set(prev_rows) if prev_rows else None
    rows = []
    for unit, info in units.items():
        if not isinstance(info, dict):
            continue
        if allow is not None and unit not in allow:
            continue
        tk = _ticker_for_unit(unit, prev_rows, units_by_ticker, info)
        row = _row_for(tk, unit, info.get("live_at_ms"), None, info.get("stake"))
        if not row:
            continue
        if info.get("baseline_only") is True and not (row["launch"].get("launcherAddress")):
            row["baseline_only"] = True
        rows.append(row)
    return rows


def _rows_from_prev(prev_rows):
    """Public rows already written. Used when the private cache is gone so the same units re-baseline."""
    rows = []
    for unit, prev in (prev_rows or {}).items():
        if not isinstance(prev, dict):
            continue
        row = _row_for(prev.get("ticker"), unit, None, None)
        if row:
            rows.append(row)
    return rows


def _rows_from_top10(top10, units_by_ticker, prev_rows):
    if not isinstance(top10, list):
        return []
    ordered = sorted((r for r in top10 if isinstance(r, dict) and r.get("ticker")),
                     key=lambda r: r.get("rank") if isinstance(r.get("rank"), (int, float)) and not isinstance(r.get("rank"), bool) else 99)
    rev = {}
    if isinstance(units_by_ticker, dict):
        for tk, u in units_by_ticker.items():
            if isinstance(u, str):
                rev[str(tk).upper()] = u
    rows, seen = [], set()
    for r in ordered[:10]:
        tk = str(r["ticker"]).upper()
        short = r.get("policy_short") if isinstance(r.get("policy_short"), str) else ""
        unit = rev.get(tk)
        if unit is None:
            for u, prev in (prev_rows or {}).items():
                if isinstance(prev, dict) and str(prev.get("ticker") or "").upper() == tk:
                    unit = u
                    break
        if not isinstance(unit, str) or len(unit) < 56 or unit in seen:
            continue
        if short and not unit.startswith(short):
            continue
        seen.add(unit)
        launcher = r.get("launcherAddress") if isinstance(r.get("launcherAddress"), str) else ""
        row = _row_for(tk, unit, None, None)
        if not row:
            continue
        if launcher:
            row["launch"]["launcherAddress"] = launcher
        else:
            row["baseline_only"] = True
        rows.append(row)
    return rows


def _fallback_rows(cache, prev_rows, top10, units_by_ticker):
    """Units to check when the Solada board did not load.

    Cached units first (they carry the creator stake), and only the units already in the last
    dev_checks.json when that file exists. If that cache is gone, the units in the file. If this
    job has never written one, Snek's top 10, resolved through desk pulse / config asset ids.
    A policy_short that does not match the unit is skipped. Fallback rows carry no current mcap
    (drawdown stays unknown). Top-10 rows with no launcher address are baseline-only.
    """
    cached = _rows_from_cache(cache, prev_rows, units_by_ticker)
    if cached:
        return cached, "cache"
    prev = _rows_from_prev(prev_rows)
    if prev:
        return prev, "previous"
    top = _rows_from_top10(top10, units_by_ticker, prev_rows)
    if top:
        return top, "top10"
    return [], None


def run(http, cfg, tracked, launches, watch, data_dir, cache_dir, dry_run, now_s, bf_key=None, baseline=False,
        t_start=None, owner_env=None, top10=None, units_by_ticker=None):
    """Write dev_checks.json (unless dry_run). Returns (summary, triggers).

    `tracked` is a list of {ticker, unit, launch, mcap_ada, vol_h24_ada} for tracked Solada launches.
    `launches` is the Solada payload already fetched this run (serial counts). Full stake keys and sell-tx
    hashes are written only to the private cache, and only when that directory is not the public data dir.
    Does not set http.deadline. Rows are keyed by unit (policy + asset name). A Koios 429 sets
    http.koios_pause_until and stops Koios inside this job; it does not trip the breaker.
    When `launches` is empty the job still runs on cached units, the previous file, or Snek's top 10.
    A missing sell cache re-baselines (no webhook). An empty board with none of those sources does not
    wipe a good file.
    """
    dc = dc_cfg(cfg)
    if not dc.get("enabled", True):
        return {"enabled": False}, []
    loaded = _patterns(owner_env)
    if loaded is None:
        return {"skipped": "owner patterns unusable", "wrote": False}, []
    pats, owner_stakes = loaded
    if bf_key is None:
        bf_key = os.environ.get("BLOCKFROST_PROJECT_ID", "")
    ex = F.excluded_tags((cfg or {}).get("flags"))
    path = os.path.join(data_dir, "dev_checks.json")
    prev_doc = KT.read_json(path, {})
    prev_rows = {r.get("unit"): r for r in (prev_doc.get("entries") or []) if isinstance(r, dict) and r.get("unit")}
    cpath = _private_cache(cache_dir, data_dir)
    cache = KT.read_json(cpath, {}) if cpath else {}
    if not isinstance(cache, dict):
        cache = {}
    cache.setdefault("units", {})
    if not isinstance(cache["units"], dict):
        cache["units"] = {}
    board, fallback = launches, None
    if not launches:
        # empty board is a fetch failure. Do not wipe the file, and do not treat the caller's
        # (empty) tracked list as the board. Use units this job already knows, or Snek's top 10.
        rows, fallback = _fallback_rows(cache, prev_rows, top10, units_by_ticker)
        if not rows:
            return {"skipped": "launches unavailable", "wrote": False, "entries": len(prev_rows)}, []
        tracked, board = rows, None
    rows_in = [r for r in (tracked or []) if isinstance(r, dict) and r.get("ticker") and r.get("unit")]
    rows_in.sort(key=lambda r: r["ticker"])
    if len(rows_in) > 1:
        k = int(cache.get("rot") or 0) % len(rows_in)
        rows_in = rows_in[k:] + rows_in[:k]
        cache["rot"] = int(cache.get("rot") or 0) + 1
    # Wall clock, not the asof timestamp: a historical now_s must not make the budget look already spent.
    # Capped at run start + 600s so a long rules screen cannot hand this job the rest of the process.
    wall = time.time()
    deadline = dev_deadline(dc["budget_s"], t_start, wall)
    http.dev_checks = True
    try:
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
            sc = _quiet_owner(scans[item["unit"]], item, pats, owner_stakes)
            scans[item["unit"]] = sc
            if not sc.get("stake"):
                info = cache["units"].get(item["unit"]) if isinstance(cache.get("units"), dict) else None
                if isinstance(info, dict) and _hidden(pats, owner_stakes, info.get("stake") or "", ""):
                    cache["units"].pop(item["unit"], None)
            live_ms = (item.get("launch") or {}).get("liveAtMs")
            if live_ms is None and sc.get("launch_s"):
                live_ms = int(float(sc["launch_s"]) * 1000)
            keep_base = bool(item.get("baseline_only")) and not (item.get("launch") or {}).get("launcherAddress")
            _remember(cache, item["unit"], sc.get("stake"), live_ms,
                      (sc.get("stats") or {}).get("late_rug") if sc.get("complete") else None, sc.get("complete"),
                      item.get("ticker"), baseline_only=keep_base)
        # priors: other recent launches by a dev we already identified, only with calls left
        scanned_units = set(scans)
        stakes = {sc.get("stake") for sc in scans.values() if sc.get("stake")}
        priors = []
        for l in board or []:
            st = _launcher_stake(l, ex)
            if st and _hidden(pats, owner_stakes, st, l.get("launcherAddress") or ""):
                st = None
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
                sc = _quiet_owner(sc, item, pats, owner_stakes)
                _remember(cache, item["unit"], sc.get("stake"), (item.get("launch") or {}).get("liveAtMs"),
                          (sc.get("stats") or {}).get("late_rug") if sc.get("complete") else None, sc.get("complete"),
                          item.get("ticker"))
        prior_unchecked = max(0, len(priors) - prior_done)
    finally:
        http.dev_checks = False
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
        prev = prev_rows.get(item.get("unit"))
        stats = sc.get("stats") or {}
        observed = stats.get("sell_hashes") if sc.get("stats") else None
        fresh = (_note_sells(cache, item["unit"], observed, prev, item.get("ticker"))
                 if sc.get("stats") is not None and sc.get("stake") else [])
        by_hash = stats.get("sell_ada") or {}
        fresh_ada = round(sum(float(by_hash.get(h) or 0) for h in fresh), 2)
        row = _entry(item, sc, prev, cache, board, dc, now_s, now_ms, baseline, fresh, fresh_ada)
        entries.append(row)
        trig = trigger_for(row)
        row.pop("_new_sell", None)
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
    if fallback:
        summary["fallback"] = fallback
    return summary, triggers
