"""Logging-only snapshots for the Solana paper desk.

Nothing returned here is an input to skips, book selection, exits, sizing, or the
fill price. Callers already hold the DexScreener pair and the Jupiter quote; this
module does not fetch. A bad payload becomes null fields and a short error code.
"""
import json, os, sys, time
from . import config as C

# pump.fun BOOST mode: pools migrated on or after this instant, entered within 5 minutes.
# Book F cannot land in the window: a pass requires the pool to be at least 15 minutes old.
BOOST_SINCE = 1784643780  # 2026-07-21T14:23:00Z
BOOST_WINDOW_S = 300
TIMING_NAME = "scan_timing.jsonl"
SNAP_CAP = 48  # latest scans only; entry_* and exit_* are stored on their own
PUMPSWAP = "pumpswap"
OBS_V = 1

PUBLIC_KEYS = (
    "obs_v",
    "entry_sol_reserve", "entry_token_reserve", "entry_k", "entry_reserve_error",
    "exit_sol_reserve", "exit_token_reserve", "exit_k", "exit_reserve_error",
    "last_sol_reserve", "last_token_reserve", "last_k", "last_reserve_error",
    "reserve_snaps",
    "entry_pool", "exit_pool", "same_pool",
    "pool_created_at", "pool_age_at_entry_s", "graduated_pump", "boost_window",
    "entry_quote_out", "entry_quote_px", "entry_quote_impact", "entry_quote_error",
    "exit_quote_out", "exit_quote_px", "exit_quote_impact", "exit_quote_error",
    "max_price_held", "min_price_held", "max_price_held_pct", "min_price_held_pct",
)


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _num(v):
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x or x in (float("inf"), float("-inf")):
        return None
    return x


def pair_created_s(pair):
    """DexScreener pairCreatedAt, seconds. Milliseconds when the value is 1e12 or more."""
    if not isinstance(pair, dict):
        return None
    raw = _num(pair.get("pairCreatedAt"))
    if raw is None or raw <= 0:
        return None
    if raw >= 1e12:
        raw = raw / 1000.0
    return raw


def graduated_pump(pair):
    """True for a PumpSwap pool (graduated pump.fun). False for any other known dex. None if unknown."""
    if not isinstance(pair, dict):
        return None
    dex = pair.get("dexId")
    if not isinstance(dex, str) or not dex.strip():
        return None
    return dex.strip().lower() == PUMPSWAP


def pool_addr(raw):
    """DexScreener pairAddress, or None when the snapshot has no pool id."""
    if not isinstance(raw, dict):
        return None
    addr = raw.get("pairAddress")
    if isinstance(addr, str) and addr:
        return addr
    return None


def boost_window(created_s, entry_s, graduated):
    """True only for a PumpSwap pool migrated on or after 2026-07-21T14:23:00Z whose entry is 0..300s later.
    False when the pool is known and the window does not apply. None when migration time or dex is unknown.
    Book F's 15-minute age rule is longer than this window, so an F pass is never inside it.
    """
    if graduated is False:
        return False
    if graduated is not True or created_s is None or entry_s is None:
        return None
    try:
        created_s = float(created_s)
        entry_s = float(entry_s)
    except (TypeError, ValueError):
        return None
    if created_s < BOOST_SINCE:
        return False
    dt = entry_s - created_s
    return 0 <= dt <= BOOST_WINDOW_S


def snapshot_reserves(raw):
    """(sol, token, k, error). k = sol * token from DexScreener liquidity.quote and liquidity.base.
    A missing side nulls all three so a partial snapshot cannot be read as a real k.
    A bare float is the historical SOL-only callback: still incomplete.
    """
    try:
        if raw is None or isinstance(raw, bool):
            return None, None, None, "no_pool"
        if isinstance(raw, (int, float)):
            return None, None, None, "reserves_incomplete"
        if not isinstance(raw, dict):
            return None, None, None, "no_pool"
        liq = raw.get("liquidity") if isinstance(raw.get("liquidity"), dict) else None
        if liq is None and "quote" in raw and "base" in raw and "dexId" not in raw:
            liq = raw
        if not isinstance(liq, dict):
            return None, None, None, "no_liquidity"
        sol = _num(liq.get("quote"))
        tok = _num(liq.get("base"))
        if sol is None or tok is None:
            return None, None, None, "reserves_incomplete"
        if sol < 0 or tok < 0:
            return None, None, None, "reserves_negative"
        return sol, tok, sol * tok, None
    except Exception:
        return None, None, None, "log_failed"


def _snap(now, sol, tok, k, err, pool):
    row = {"t": iso(now), "sol": sol, "token": tok, "k": k, "pool": pool}
    if err:
        row["error"] = err
    return row


def quote_numbers(quote, side, amount_in=None):
    """(out_amount, implied_px, impact_fraction, error).

    implied_px is lamports per raw token, the same units as the book fill.
    Buy: ticket lamports / tokens out. Sell: lamports out / tokens in.
    impact_fraction is the raw Jupiter priceImpactPct field, parsed as a float and not rescaled.
    0.01 means 1%. The trade row's impact_pct is that fraction times 100 and is a different cell.
    Missing impact is null, not an error.
    """
    try:
        if not isinstance(quote, dict) or not quote.get("outAmount"):
            return None, None, None, "no_quote"
        out = int(quote["outAmount"])
        if out <= 0:
            return None, None, None, "bad_out_amount"
        impact = _num(quote.get("priceImpactPct")) if "priceImpactPct" in quote else None
        px = None
        if side == "buy":
            px = C.TICKET_SOL * 1e9 / out
        elif side == "sell":
            try:
                amt = int(amount_in)
            except (TypeError, ValueError):
                amt = 0
            if amt <= 0:
                return out, None, impact, "bad_amount"
            px = out / amt
        else:
            return out, None, impact, "bad_side"
        return out, px, impact, None
    except (TypeError, ValueError):
        return None, None, None, "bad_out_amount"
    except Exception:
        return None, None, None, "log_failed"


def held_pct(px, entry):
    try:
        e = float(entry)
        if e == 0 or e != e:
            return None
        return round((float(px) / e - 1.0) * 100.0, 4)
    except (TypeError, ValueError):
        return None


def entry_log(lead, entry_px, now):
    """Fields for a new position. Does not read or write the fill."""
    try:
        pair = lead.get("pair") if isinstance(lead, dict) else None
        if not isinstance(pair, dict):
            pair = None
        sol, tok, k, err = snapshot_reserves(pair)
        created = pair_created_s(pair)
        grad = graduated_pump(pair)
        addr = pool_addr(pair)
        age = None if created is None else round(float(now) - float(created), 1)
        fields = {
            "obs_v": OBS_V,
            "entry_sol_reserve": sol, "entry_token_reserve": tok, "entry_k": k, "entry_reserve_error": err,
            "last_sol_reserve": sol, "last_token_reserve": tok, "last_k": k, "last_reserve_error": err,
            "reserve_snaps": [_snap(now, sol, tok, k, err, addr)],
            "entry_pool": addr,
            "pool_created_at": iso(created) if created is not None else None,
            "pool_age_at_entry_s": age,
            "graduated_pump": grad,
            "boost_window": boost_window(created, now, grad),
        }
        quote = lead.get("quote") if isinstance(lead, dict) else None
        out, px, impact, qerr = quote_numbers(quote, "buy")
        fields.update(entry_quote_out=out, entry_quote_px=px, entry_quote_impact=impact, entry_quote_error=qerr)
        if isinstance(entry_px, (int, float)) and not isinstance(entry_px, bool) and entry_px > 0:
            fields["max_price_held"] = entry_px
            fields["min_price_held"] = entry_px
            fields["max_price_held_pct"] = 0.0
            fields["min_price_held_pct"] = 0.0
        return fields
    except Exception:
        return {
            "entry_sol_reserve": None, "entry_token_reserve": None, "entry_k": None,
            "entry_reserve_error": "log_failed",
            "obs_v": OBS_V, "entry_pool": None, "pool_age_at_entry_s": None,
            "pool_created_at": None, "graduated_pump": None, "boost_window": None,
            "entry_quote_out": None, "entry_quote_px": None, "entry_quote_impact": None,
            "entry_quote_error": "log_failed",
        }


def note_held(pos, raw, now):
    """Latest pool snapshot while the position is open. One row per scan, capped."""
    try:
        sol, tok, k, err = snapshot_reserves(raw)
        snaps = [s for s in (pos.get("reserve_snaps") or []) if isinstance(s, dict)]
        snaps.append(_snap(now, sol, tok, k, err, pool_addr(raw)))
        return {
            "last_sol_reserve": sol, "last_token_reserve": tok, "last_k": k, "last_reserve_error": err,
            "reserve_snaps": snaps[-SNAP_CAP:],
        }
    except Exception:
        return {"last_reserve_error": "log_failed"}


def note_price(pos, px):
    """Max and min mark seen while held. The exit rule's peak is a different field and is not written."""
    try:
        if not isinstance(px, (int, float)) or isinstance(px, bool) or not px > 0:
            return {}
        mx = pos.get("max_price_held")
        mn = pos.get("min_price_held")
        if not isinstance(mx, (int, float)) or isinstance(mx, bool):
            mx = px
        if not isinstance(mn, (int, float)) or isinstance(mn, bool):
            mn = px
        mx = max(mx, px)
        mn = min(mn, px)
        return {
            "max_price_held": mx, "min_price_held": mn,
            "max_price_held_pct": held_pct(mx, pos.get("entry")),
            "min_price_held_pct": held_pct(mn, pos.get("entry")),
        }
    except Exception:
        return {}


def stamp_exit(pos, raw, quote, amount, fetched):
    """Exit reserves and the sell quote already fetched for the mark. fetched False means this scan did not load a pool."""
    out = {}
    try:
        if fetched:
            sol, tok, k, err = snapshot_reserves(raw)
        else:
            sol = tok = k = None
            err = "not_fetched"
        out.update(exit_sol_reserve=sol, exit_token_reserve=tok, exit_k=k, exit_reserve_error=err)
    except Exception:
        out.update(exit_sol_reserve=None, exit_token_reserve=None, exit_k=None, exit_reserve_error="log_failed")
    exit_pool = pool_addr(raw) if fetched else None
    entry_pool = pos.get("entry_pool")
    if isinstance(entry_pool, str) and entry_pool and isinstance(exit_pool, str) and exit_pool:
        same = entry_pool == exit_pool
    else:
        same = None
    out["exit_pool"] = exit_pool
    out["same_pool"] = same
    try:
        o, px, impact, qerr = quote_numbers(quote, "sell", amount)
        out.update(exit_quote_out=o, exit_quote_px=px, exit_quote_impact=impact, exit_quote_error=qerr)
    except Exception:
        out.update(exit_quote_out=None, exit_quote_px=None, exit_quote_impact=None, exit_quote_error="log_failed")
    try:
        if pos.get("max_price_held") is not None:
            out["max_price_held_pct"] = held_pct(pos.get("max_price_held"), pos.get("entry"))
        if pos.get("min_price_held") is not None:
            out["min_price_held_pct"] = held_pct(pos.get("min_price_held"), pos.get("entry"))
    except Exception:
        pass
    # A zero fill (no route, pool gone) is a real low. The mark loop does not record a non-positive price.
    if pos.get("last_mark") == 0:
        out["min_price_held"] = 0.0
        out["min_price_held_pct"] = -100.0
    return out


def append_scan_timing(data_dir, slot, started, run_id, mode, trigger=None):
    """One jsonl line: cron slot, actual start, delay seconds, workflow trigger. Failures are printed and swallowed."""
    try:
        delay = round(float(started) - float(slot), 3)
        if delay < 0:
            delay = 0.0
        if not isinstance(trigger, str) or not trigger.strip():
            trigger = os.environ.get("GITHUB_EVENT_NAME") or "local"
        trigger = trigger.strip()[:40] or "local"
        row = {
            "scheduled_utc": iso(slot),
            "started_utc": iso(started),
            "delay_s": delay,
            "run_id": str(run_id),
            "mode": mode if mode in ("scan", "fill") else "scan",
            "trigger": trigger,
        }
        os.makedirs(data_dir, exist_ok=True)
        path = os.path.join(data_dir, TIMING_NAME)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception:
        print("scan timing: skipped", file=sys.stderr)
