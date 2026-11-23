"""Replay pluggable rules over already-loaded bars.

The entry signal reads the decision bar only. It does not look ahead to see
whether a later bar exists. The exit mode defaults to desk_v65, which calls
desk.exits.check. A dead-pool error row (null reserves plus an error) is a
pool_gone or no_route mark on that path. A gap longer than max_gap_s closes
as missed at -entry_cost. There is no stale exit.

Quotes are constant-product pool math. Jupiter's outAmount is already net of
the pool fee, so each side adds the PumpSwap total from
https://pump.fun/docs/fees (16 November 2026). With no market cap on the bar
the fee is 125 bps, the canonical total below 420 SOL. Net PnL then uses
desk.fills. Book limits are the desk's starting cash, entry_cost, open cap,
and per-day cap.

The date guard uses the real UTC clock. A caller cannot pass a clock or an
override. This module does not write and does not use the network.
"""

from __future__ import annotations

import copy
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_DESK = Path(__file__).resolve().parents[2] / "solana-desk"
if str(_DESK) not in sys.path:
    sys.path.insert(0, str(_DESK))

from desk import config as C  # noqa: E402
from desk import exits, fills  # noqa: E402
from .load import BLIND_START_S, BLIND_UNTIL  # noqa: E402

# The desk's outage limit, used as the default gap between bars. Not a new exit rule.
DEFAULT_MAX_GAP_S = float(C.OUTAGE_VOID_H) * 3600.0
MIN_DRAWS = 1000
BOOTSTRAP_REPS = 1000
DESK_MODE = "desk_v65"
FIXED_MODE = "fixed"
# Canonical PumpSwap total, per side. https://pump.fun/docs/fees (16 November 2026).
# Lower edge of each SOL market-cap tier, then the total fee in bps.
DEFAULT_SWAP_FEE_BPS = 125.0
_FEE_LADDER = (
    (0, 125.0),
    (420, 120.0),
    (1470, 115.0),
    (2460, 110.0),
    (3440, 105.0),
    (4420, 100.0),
    (9820, 95.0),
    (14740, 90.0),
    (19650, 85.0),
    (24560, 80.0),
    (29470, 75.0),
    (34380, 70.0),
    (39300, 65.0),
    (44210, 60.0),
    (49120, 55.0),
    (54030, 52.5),
    (58940, 50.0),
    (63860, 47.5),
    (68770, 45.0),
    (73681, 42.5),
    (78590, 40.0),
    (83500, 37.5),
    (88400, 35.0),
    (93330, 32.5),
    (98240, 30.0),
)
_NO_ROUTE_ERRORS = frozenset({"no_route"})
PROXY_INPUTS = (
    "proxy inputs: marks come from pool math; liquidity is the bar's SOL; "
    "no_route_3x can't fire; marks happen only at snapshots; "
    "entries use the v2 S2 rules rather than the EXEC and v6.5 filters"
)


class RuleError(ValueError):
    """The rule config cannot be replayed."""


def pumpswap_fee_bps(market_cap_sol: float) -> float:
    """Total canonical PumpSwap fee for a SOL market cap. From the fees page, not a fit."""
    fee = _FEE_LADDER[0][1]
    for floor, bps in _FEE_LADDER:
        if market_cap_sol >= floor:
            fee = bps
        else:
            break
    return fee


def fee_bps_for(bar, override) -> float:
    """Explicit swap_fee_bps wins. Otherwise the ladder, or 125 bps when cap is absent."""
    if override is not None:
        return float(override)
    cap = bar.get("market_cap_sol") if isinstance(bar, dict) else None
    if cap is None:
        return DEFAULT_SWAP_FEE_BPS
    return pumpswap_fee_bps(float(cap))


def buy_quote(sol: float, token: float, ticket: float, swap_fee_bps: float = DEFAULT_SWAP_FEE_BPS) -> tuple[float, float]:
    """Constant-product buy. Returns (tokens_out, effective SOL per token).

    swap_fee_bps is taken from the SOL in before the curve, one side of the pool fee.
    """
    fee = float(swap_fee_bps) / 10000.0
    dx = ticket * (1.0 - fee)
    if dx <= 0 or sol <= 0 or token <= 0:
        return 0.0, 0.0
    tokens = token * dx / (sol + dx)
    if tokens <= 0:
        return 0.0, 0.0
    return tokens, ticket / tokens


def sell_mark(sol: float, token: float, tokens_to_sell: float, swap_fee_bps: float = DEFAULT_SWAP_FEE_BPS) -> float:
    """Effective SOL per token for selling tokens_to_sell into the pool."""
    fee = float(swap_fee_bps) / 10000.0
    sold = tokens_to_sell * (1.0 - fee)
    if sold <= 0 or sol <= 0 or token <= 0 or tokens_to_sell <= 0:
        return 0.0
    sol_out = sol * sold / (token + sold)
    return sol_out / tokens_to_sell


def _pool_ok(bar: dict) -> bool:
    sol = bar.get("sol")
    token = bar.get("token")
    return isinstance(sol, (int, float)) and not isinstance(sol, bool) and isinstance(token, (int, float)) and not isinstance(token, bool) and sol > 0 and token > 0


def _require_number(spec: dict, key: str, rule_id: str) -> float:
    value = spec[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleError(f"rule {rule_id}: {key} must be a number")
    return float(value)


def _exit_mode(rule: dict) -> str:
    named = rule.get("exit_mode")
    if named is None and isinstance(rule.get("exit"), dict):
        named = rule["exit"].get("mode")
    if named is None:
        return DESK_MODE
    if named not in {DESK_MODE, FIXED_MODE}:
        raise RuleError(f"rule {rule.get('id')}: exit mode must be {DESK_MODE} or {FIXED_MODE}")
    return named


def _max_gap(rule: dict) -> float:
    exit_spec = rule.get("exit") if isinstance(rule.get("exit"), dict) else {}
    if "max_gap_s" in rule:
        gap = _require_number(rule, "max_gap_s", str(rule.get("id")))
    elif "max_gap_s" in exit_spec:
        gap = _require_number(exit_spec, "max_gap_s", str(rule.get("id")))
    else:
        gap = DEFAULT_MAX_GAP_S
    if gap <= 0:
        raise RuleError(f"rule {rule.get('id')}: max_gap_s must be positive")
    return gap


def _fee_override(rule: dict, top):
    """None means use the PumpSwap ladder (125 bps when the bar has no market cap)."""
    if "swap_fee_bps" in rule:
        raw = rule["swap_fee_bps"]
    elif top is not None:
        raw = top
    else:
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RuleError(f"rule {rule.get('id')}: swap_fee_bps must be a number")
    if raw < 0 or raw >= 10000:
        raise RuleError(f"rule {rule.get('id')}: swap_fee_bps must be in [0, 10000)")
    return float(raw)


def validate_rule(rule: dict) -> None:
    if not isinstance(rule, dict):
        raise RuleError("a rule must be an object")
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise RuleError("a rule needs an id")
    entry = rule.get("entry", {})
    if entry is None:
        entry = {}
    if not isinstance(entry, dict):
        raise RuleError(f"rule {rule_id}: entry must be an object")
    mode = _exit_mode(rule)
    exit_spec = rule.get("exit")
    if exit_spec is None:
        exit_spec = {}
    if not isinstance(exit_spec, dict):
        raise RuleError(f"rule {rule_id}: exit must be an object")
    _max_gap(rule)
    if "starting_cash" in rule:
        cash = _require_number(rule, "starting_cash", rule_id)
        if cash < 0:
            raise RuleError(f"rule {rule_id}: starting_cash must be >= 0")
    if mode == FIXED_MODE:
        if "max_hold_s" not in exit_spec:
            raise RuleError(f"rule {rule_id}: fixed exit needs max_hold_s so the position cannot stay open")
        if _require_number(exit_spec, "max_hold_s", rule_id) <= 0:
            raise RuleError(f"rule {rule_id}: exit.max_hold_s must be positive")
        if "hard_stop" in exit_spec and _require_number(exit_spec, "hard_stop", rule_id) >= 0:
            raise RuleError(f"rule {rule_id}: exit.hard_stop must be negative")
        if exit_spec.get("trail") is not None and not 0 < _require_number(exit_spec, "trail", rule_id) < 1:
            raise RuleError(f"rule {rule_id}: exit.trail must be between 0 and 1")
        if exit_spec.get("liq_drop") is not None and not 0 < _require_number(exit_spec, "liq_drop", rule_id) < 1:
            raise RuleError(f"rule {rule_id}: exit.liq_drop must be between 0 and 1")
        ladder = exit_spec.get("take_profit") or []
        if not isinstance(ladder, list):
            raise RuleError(f"rule {rule_id}: exit.take_profit must be a list")
        total = 0.0
        prev = 0.0
        for step in ladder:
            if not isinstance(step, (list, tuple)) or len(step) != 2:
                raise RuleError(f"rule {rule_id}: each take_profit step is [level, fraction]")
            level, frac = step
            if isinstance(level, bool) or isinstance(frac, bool) or not isinstance(level, (int, float)) or not isinstance(frac, (int, float)):
                raise RuleError(f"rule {rule_id}: take_profit values must be numbers")
            if level <= prev or frac <= 0:
                raise RuleError(f"rule {rule_id}: take_profit levels must rise and fractions must be positive")
            total += float(frac)
            prev = float(level)
        if total > 1 + 1e-9:
            raise RuleError(f"rule {rule_id}: take_profit fractions sum to more than 1")


def entry_match(bar: dict, entry: dict) -> bool:
    """True when this bar, and only this bar, satisfies the entry object."""
    mints = entry.get("mints")
    if mints is not None and bar.get("mint") not in mints:
        return False
    if entry.get("rules_all_pass"):
        rules = bar.get("rules")
        if not isinstance(rules, dict) or not rules:
            return False
        if any(value != "pass" for value in rules.values()):
            return False
    for name in entry.get("require_rules") or []:
        rules = bar.get("rules") or {}
        if rules.get(name) != "pass":
            return False
    if "min_sol" in entry:
        sol = bar.get("sol")
        if sol is None or sol < float(entry["min_sol"]):
            return False
    if "max_sol" in entry:
        sol = bar.get("sol")
        if sol is None or sol > float(entry["max_sol"]):
            return False
    if "min_age_s" in entry:
        age = bar.get("age_s")
        if age is None or age < float(entry["min_age_s"]):
            return False
    if "max_age_s" in entry:
        age = bar.get("age_s")
        if age is None or age > float(entry["max_age_s"]):
            return False
    if "min_sells_h1" in entry:
        sells = bar.get("sells_h1")
        if sells is None or sells < float(entry["min_sells_h1"]):
            return False
    # Logged v2 S2 verdicts, not the v6.5 EXEC checks.
    rules = bar.get("rules")
    if isinstance(rules, dict) and rules and not entry.get("rules_all_pass"):
        if any(value != "pass" for value in rules.values()):
            return False
    return True


def _day_hour(t_s: int) -> tuple[str, int]:
    stamp = datetime.fromtimestamp(int(t_s), tz=timezone.utc)
    return stamp.strftime("%Y-%m-%d"), stamp.hour


def _group(bars: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for bar in bars:
        grouped.setdefault(bar["mint"], []).append(bar)
    for series in grouped.values():
        series.sort(key=lambda b: (b["t_s"], b["mint"]))
    return grouped


def _apply(book: dict, pos: dict, acts, pool_sol, when: int, lp_fee_bps: float) -> None:
    for action, frac, why, px in acts:
        if action != "sell" or frac <= 0:
            continue
        dx = fills.gross_proceeds(pos, frac, px) if px > 0 else 0.0
        verified = fills.verified_impact_bps(pool_sol, dx) if dx > 0 else None
        impact = 0.0 if verified is None else verified
        haircut = 0.0 if dx <= 0 else fills.haircut_frac(pool_sol, impact, why)
        gross, net = fills.slice_pnl(pos, frac, px, haircut)
        book["cash"] += fills.net_proceeds(pos, frac, px, haircut)
        pos["gross"] += gross
        pos["net"] += net
        pos["slices"].append({
            "t": int(when),
            "why": why,
            "frac": frac,
            "px": px,
            "gross_pnl": gross,
            "net_pnl": net,
            "exit_haircut": haircut,
            "pool_sol": pool_sol,
            "dx_sol": dx,
        })
        if not str(why).startswith("take_profit"):
            pos["frac"] = 0.0
    if pos["frac"] <= 1e-9 and pos["mint"] in book["open"]:
        _archive(book, pos)


def _archive(book: dict, pos: dict) -> None:
    book["open"].pop(pos["mint"], None)
    if not pos["slices"]:
        return
    book["trades"].append({
        "rule": pos["rule"],
        "mint": pos["mint"],
        "entry_t": int(pos["opened"]),
        "exit_t": int(pos["slices"][-1]["t"]),
        "exit_reason": pos["slices"][-1]["why"],
        "exit_fixed": pos["exit_fixed"],
        "gross_pnl": pos["gross"],
        "net_pnl": pos["net"],
        "slices": pos["slices"],
        "baseline": pos["baseline"],
    })


def _force_close(book: dict, pos: dict, why: str, px: float, when: int, pool_sol, lp_fee_bps: float) -> None:
    if pos["frac"] <= 1e-12:
        book["open"].pop(pos["mint"], None)
        return
    _apply(book, pos, [("sell", pos["frac"], why, px)], pool_sol, when, lp_fee_bps)


def _close_missed(book: dict, pos: dict, when: int, fee_override) -> None:
    """Gap past max_gap_s. Price is zero, so the booked result is -entry_cost."""
    _force_close(book, pos, "missed", 0.0, int(when), None, fee_override)


def _expire(book: dict, now: int, max_gap: float, fee_override) -> None:
    for pos in list(book["open"].values()):
        if now - pos["last_t"] > max_gap:
            _close_missed(book, pos, int(pos["last_t"] + max_gap), fee_override)


def _status_of(bar: dict) -> str:
    err = bar.get("error")
    if err:
        if str(err) in _NO_ROUTE_ERRORS:
            return "no_route"
        return "pool_gone"
    if not _pool_ok(bar):
        return "pool_gone"
    status = bar.get("status") or "ok"
    if status not in {"ok", "temp_error", "no_route", "pool_gone"}:
        return "ok"
    return status


def _mark_desk(book: dict, pos: dict, bar: dict, fee_override) -> None:
    status = _status_of(bar)
    fee = fee_bps_for(bar, fee_override)
    if status == "ok":
        mark = sell_mark(bar["sol"], bar["token"], pos["tokens"] * pos["frac"], fee)
        liq = bar["sol"]
        pool = bar["sol"]
    elif status == "pool_gone":
        mark = 0.0
        liq = None
        pool = None
    else:
        # One snapshot is not the desk's three route checks, so no_route_3x does not fire.
        mark = pos["last_mark"]
        liq = None
        pool = None
        pos["no_route_count"] = 0
    acts = exits.check(pos, mark, liq, int(bar["t_s"]), status)
    if any(why == "no_route_3x" for _a, _f, why, _p in acts):
        acts = [item for item in acts if item[2] != "no_route_3x"]
    _apply(book, pos, acts, pool, int(bar["t_s"]), fee_override)


def _mark_fixed(book: dict, pos: dict, bar: dict, exit_spec: dict, fee_override) -> None:
    status = _status_of(bar)
    fee = fee_bps_for(bar, fee_override)
    if status == "pool_gone":
        acts = exits.check(pos, 0.0, None, int(bar["t_s"]), "pool_gone")
        _apply(book, pos, acts, None, int(bar["t_s"]), fee_override)
        return
    if status in {"temp_error", "no_route"}:
        pos["no_route_count"] = 0
        acts = exits.check(pos, pos["last_mark"], None, int(bar["t_s"]), status)
        acts = [item for item in acts if item[2] != "no_route_3x"]
        _apply(book, pos, acts, None, int(bar["t_s"]), fee_override)
        return
    mark = sell_mark(bar["sol"], bar["token"], pos["tokens"] * pos["frac"], fee)
    pos["peak"] = max(pos["peak"], mark)
    ret = mark / pos["entry"] - 1 if pos["entry"] else 0.0
    if exit_spec.get("liq_drop") is not None and pos["liq0"] and bar["sol"] < pos["liq0"] * (1 - float(exit_spec["liq_drop"])):
        _apply(book, pos, [("sell", pos["frac"], "liquidity_pulled", mark)], bar["sol"], int(bar["t_s"]), fee_override)
        return
    if exit_spec.get("hard_stop") is not None and ret <= float(exit_spec["hard_stop"]):
        _apply(book, pos, [("sell", pos["frac"], "hard_stop", mark)], bar["sol"], int(bar["t_s"]), fee_override)
        return
    acts = []
    for step_i, (level, frac) in enumerate(exit_spec.get("take_profit") or []):
        if pos["steps"] == step_i and ret >= float(level) and pos["frac"] > 1e-12:
            sell_f = min(float(frac), pos["frac"])
            acts.append(("sell", sell_f, f"take_profit_{int(round(float(level) * 100))}", mark))
            pos["steps"] += 1
            pos["frac"] -= sell_f
    trail = exit_spec.get("trail")
    if trail is not None and pos["steps"] >= 1 and pos["frac"] > 1e-12 and mark <= pos["peak"] * (1 - float(trail)):
        acts.append(("sell", pos["frac"], "trailing_stop", mark))
        _apply(book, pos, acts, bar["sol"], int(bar["t_s"]), fee_override)
        return
    if bar["t_s"] >= pos["opened"] + float(exit_spec["max_hold_s"]) and pos["frac"] > 1e-12:
        acts.append(("sell", pos["frac"], "max_hold", mark))
    _apply(book, pos, acts, bar["sol"], int(bar["t_s"]), fee_override)


def _try_enter(book: dict, bar: dict, rule: dict, fee_override, bound: dict, baseline: bool) -> bool:
    if bar["mint"] in book["open"] or not _pool_ok(bar):
        return False
    day, _hour = _day_hour(bar["t_s"])
    cost, haircut = fills.entry_cost(bar["sol"])
    if book["cash"] < cost:
        book["skip_cash"] += 1
        return False
    if len(book["open"]) >= C.MAX_OPEN_F:
        book["skip_open"] += 1
        return False
    if book["fills"].get(day, 0) >= C.MAX_NEW_F_PER_DAY:
        book["skip_day"] += 1
        return False
    fee = fee_bps_for(bar, fee_override)
    tokens, px = buy_quote(bar["sol"], bar["token"], C.TICKET_SOL, fee)
    if tokens <= 0 or px <= 0:
        return False
    book["cash"] -= cost
    book["fills"][day] = book["fills"].get(day, 0) + 1
    pos = exits.new_position(px, bar["sol"], int(bar["t_s"]))
    pos.update(
        mint=bar["mint"],
        rule=rule["id"],
        tokens=tokens,
        entry_cost=cost,
        entry_haircut=haircut,
        last_t=int(bar["t_s"]),
        slices=[],
        gross=0.0,
        net=0.0,
        exit_fixed=bound,
        baseline=baseline,
    )
    book["open"][bar["mint"]] = pos
    return True


def _base_universe(bar: dict) -> bool:
    """Tradable bar. The null uses this set and not the rule's own filter."""
    return _pool_ok(bar) and not bar.get("error")


def run_book(bars: list[dict], rule: dict, fee_override, rng: random.Random | None = None, remaining: Counter | None = None, baseline: bool = False) -> dict:
    """One book. remaining limits null entries per UTC day and hour."""
    mode = _exit_mode(rule)
    gap = _max_gap(rule)
    exit_spec = rule.get("exit") if isinstance(rule.get("exit"), dict) else {}
    bound = {"mode": mode, "max_gap_s": gap}
    if mode == FIXED_MODE:
        bound.update(copy.deepcopy(exit_spec))
    cash0 = float(rule["starting_cash"]) if "starting_cash" in rule else float(C.START_BALANCE_SOL)
    book = {"cash": cash0, "open": {}, "trades": [], "fills": {}, "skip_cash": 0, "skip_open": 0, "skip_day": 0}
    entry = rule.get("entry") or {}
    grouped = _group(bars)
    events = [(bar["t_s"], mint, i) for mint, series in grouped.items() for i, bar in enumerate(series)]
    events.sort(key=lambda item: (item[0], item[1]))
    index = 0
    while index < len(events):
        now = events[index][0]
        batch = []
        while index < len(events) and events[index][0] == now:
            batch.append(events[index])
            index += 1
        _expire(book, now, gap, fee_override)
        busy = set(book["open"])
        for _t, mint, i in batch:
            if mint not in book["open"]:
                continue
            bar = grouped[mint][i]
            if mode == DESK_MODE:
                _mark_desk(book, book["open"][mint], bar, fee_override)
            else:
                _mark_fixed(book, book["open"][mint], bar, exit_spec, fee_override)
            if mint in book["open"]:
                book["open"][mint]["last_t"] = int(bar["t_s"])
        candidates = []
        for _t, mint, i in batch:
            if mint in busy or mint in book["open"]:
                continue
            bar = grouped[mint][i]
            if baseline:
                if not _base_universe(bar):
                    continue
            elif not _pool_ok(bar) or not entry_match(bar, entry):
                continue
            candidates.append(bar)
        if rng is None:
            candidates.sort(key=lambda bar: bar["mint"])
        else:
            rng.shuffle(candidates)
        for bar in candidates:
            key = _day_hour(bar["t_s"])
            if remaining is not None and remaining.get(key, 0) <= 0:
                continue
            if _try_enter(book, bar, rule, fee_override, bound, baseline):
                if remaining is not None:
                    remaining[key] -= 1
    return book


def _hour_counts(trades: list[dict]) -> Counter:
    counts: Counter = Counter()
    for trade in trades:
        counts[_day_hour(trade["entry_t"])] += 1
    return counts


def _eligible_n(bars: list[dict], entry: dict) -> int:
    return sum(1 for bar in bars if _pool_ok(bar) and entry_match(bar, entry))


def _percentile(rule_net: float, nulls: list[float]):
    if not nulls:
        return None
    less = sum(1 for value in nulls if value < rule_net)
    ties = sum(1 for value in nulls if value == rule_net)
    return 100.0 * (less + 0.5 * ties) / len(nulls)


def _clusters(trades: list[dict], by: str) -> list[list[dict]]:
    groups: dict = {}
    for trade in trades:
        if by == "day":
            key = _day_hour(trade["entry_t"])[0]
        else:
            key = trade["mint"]
        groups.setdefault(key, []).append(trade)
    return list(groups.values())


def _effective_n(clusters: list[list[dict]]) -> float:
    """Kish effective sample size from cluster sizes."""
    sizes = [len(cluster) for cluster in clusters if cluster]
    n = sum(sizes)
    if n == 0:
        return 0.0
    return (n * n) / sum(size * size for size in sizes)


def _intervals_overlap(left: dict, right: dict) -> bool:
    if left.get("mint") == right.get("mint"):
        return False
    a0, a1 = int(left["entry_t"]), left.get("exit_t")
    b0, b1 = int(right["entry_t"]), right.get("exit_t")
    a1 = 10**18 if a1 is None else int(a1)
    b1 = 10**18 if b1 is None else int(b1)
    return a0 < b1 and b0 < a1


def _overlap_components(holds: list[dict]) -> list[list[dict]]:
    """Holds that overlap in time across mints share one component."""
    parent = list(range(len(holds)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(holds)):
        for j in range(i + 1, len(holds)):
            if _intervals_overlap(holds[i], holds[j]):
                parent[find(j)] = find(i)
    groups: dict[int, list] = {}
    for i, hold in enumerate(holds):
        groups.setdefault(find(i), []).append(hold)
    return list(groups.values())


def _holds(book: dict) -> list[dict]:
    holds = []
    for trade in book["trades"]:
        holds.append({
            "mint": trade["mint"],
            "entry_t": trade["entry_t"],
            "exit_t": trade["exit_t"],
            "net_pnl": trade["net_pnl"],
        })
    for pos in book["open"].values():
        holds.append({
            "mint": pos["mint"],
            "entry_t": pos["opened"],
            "exit_t": None,
            "net_pnl": pos["net"],
        })
    return holds


def _realized(book: dict) -> float:
    return sum(trade["net_pnl"] for trade in book["trades"]) + sum(pos["net"] for pos in book["open"].values())


def _quantile(sorted_vals: list[float], p: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    weight = idx - lo
    return sorted_vals[lo] * (1 - weight) + sorted_vals[hi] * weight


def _bootstrap(holds: list[dict], rng: random.Random):
    """Resample overlap clusters. The statistic is the sum of net_pnl, same as the percentile."""
    clusters = _overlap_components(holds)
    n_eff = _effective_n(clusters)
    if not clusters:
        return None, None, n_eff, 0
    sums = []
    width = len(clusters)
    for _ in range(BOOTSTRAP_REPS):
        drawn = [clusters[rng.randrange(width)] for _ in range(width)]
        sums.append(sum(hold["net_pnl"] for cluster in drawn for hold in cluster))
    sums.sort()
    return _quantile(sums, 0.025), _quantile(sums, 0.975), n_eff, width


def _public_trade(trade: dict) -> dict:
    return {
        "rule": trade["rule"],
        "mint": trade["mint"],
        "entry_t": trade["entry_t"],
        "exit_t": trade["exit_t"],
        "exit_reason": trade["exit_reason"],
        "exit_fixed": trade["exit_fixed"],
        "gross_pnl": trade["gross_pnl"],
        "net_pnl": trade["net_pnl"],
        "slices": [dict(sl) for sl in trade["slices"]],
        "baseline": bool(trade.get("baseline")),
    }


def _rate(trades: list[dict]):
    if not trades:
        return None
    return sum(1 for trade in trades if trade["net_pnl"] > 0) / len(trades)


def _round(value: float) -> float:
    return round(float(value), 9)


def _refuse_blinded_bars(bars) -> None:
    """Real UTC clock only. A supplied timestamp cannot open the blinded window."""
    if datetime.now(timezone.utc) >= BLIND_UNTIL:
        return
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        stamp = bar.get("t_s")
        if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
            continue
        if int(stamp) >= BLIND_START_S:
            raise RuleError("refused: a bar is inside the blinded window")


def _reason_counts(trades: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for trade in trades:
        why = trade.get("exit_reason")
        if why:
            counts[why] = counts.get(why, 0) + 1
    return dict(sorted(counts.items()))


def replay(bars: list[dict], rules, seed: int | None = None, n_draws: int | None = None) -> dict:
    """Per-rule trades, net PnL, hit rate, and a 1000-draw null with a clustered CI."""
    _refuse_blinded_bars(bars)
    top_fee = None
    if isinstance(rules, dict):
        if seed is None:
            seed = int(rules.get("seed", 1))
        if n_draws is None and rules.get("n_draws") is not None:
            n_draws = int(rules["n_draws"])
        if "swap_fee_bps" in rules:
            top_fee = rules["swap_fee_bps"]
        rule_list = rules.get("rules") or []
    else:
        rule_list = list(rules)
        if seed is None:
            seed = 1
    if n_draws is None:
        n_draws = MIN_DRAWS
    if n_draws < MIN_DRAWS:
        raise RuleError(f"n_draws must be at least {MIN_DRAWS}")
    if not isinstance(rule_list, list) or not rule_list:
        raise RuleError("rules must be a non-empty list")
    for rule in rule_list:
        validate_rule(rule)
        _fee_override(rule, top_fee)
    rows = []
    for rule in rule_list:
        override = _fee_override(rule, top_fee)
        entry = rule.get("entry") or {}
        live = run_book(bars, rule, override, rng=None, remaining=None, baseline=False)
        trades = live["trades"]
        holds = _holds(live)
        realized = _realized(live)
        counts = _hour_counts(trades + [
            {"entry_t": pos["opened"]} for pos in live["open"].values()
        ])
        rng = random.Random(seed)
        nulls = []
        for _ in range(n_draws):
            remaining = Counter(counts)
            draw = run_book(bars, rule, override, rng=rng, remaining=remaining, baseline=True)
            nulls.append(_realized(draw))
        lo, hi, n_eff, n_clusters = _bootstrap(holds, rng)
        rows.append({
            "id": rule["id"],
            "n": len(trades),
            "open_n": len(live["open"]),
            "open": [
                {"mint": pos["mint"], "entry_t": int(pos["opened"]), "exit_fixed": pos["exit_fixed"]}
                for pos in live["open"].values()
            ],
            "hit_rate": _rate(trades),
            "net_pnl": _round(realized),
            "gross_pnl": _round(sum(trade["gross_pnl"] for trade in trades) + sum(pos["gross"] for pos in live["open"].values())),
            "close_reasons": _reason_counts(trades),
            "trades": [_public_trade(trade) for trade in trades],
            "skipped_cash": live["skip_cash"],
            "skipped_open_cap": live["skip_open"],
            "skipped_day_cap": live["skip_day"],
            "baseline": {
                "seed": seed,
                "draws": n_draws,
                "statistic": "sum",
                "percentile": _percentile(realized, nulls),
                "null_mean": _round(sum(nulls) / len(nulls)) if nulls else None,
                "bootstrap_ci": [None if lo is None else _round(lo), None if hi is None else _round(hi)],
                "cluster_by": "overlap",
                "n_clusters": n_clusters,
                "cluster_warning": n_clusters < 5,
                "effective_n": n_eff,
                "eligible_n": _eligible_n(bars, entry),
                "base_universe_n": sum(1 for bar in bars if _base_universe(bar)),
                "hour_counts": {f"{day}T{hour:02d}": count for (day, hour), count in sorted(counts.items())},
            },
        })
    return {
        "cost_model": C.COST_MODEL,
        "ticket_sol": C.TICKET_SOL,
        "swap_fee_bps_default": DEFAULT_SWAP_FEE_BPS,
        "swap_fee_source": "https://pump.fun/docs/fees",
        "proxy_inputs": PROXY_INPUTS,
        "starting_cash": C.START_BALANCE_SOL,
        "max_open": C.MAX_OPEN_F,
        "max_new_per_day": C.MAX_NEW_F_PER_DAY,
        "max_gap_s_default": DEFAULT_MAX_GAP_S,
        "rules": rows,
    }


def _fmt_rate(rate) -> str:
    if rate is None:
        return "n/a"
    return f"{rate:.4f}"


def _fmt_ci(bounds) -> str:
    if not bounds or bounds[0] is None or bounds[1] is None:
        return "n/a"
    return f"[{bounds[0]:.9f},{bounds[1]:.9f}]"


def format_report(result: dict) -> str:
    """One block per rule: its trades, then the null percentile and clustered CI."""
    lines = [result.get("proxy_inputs") or PROXY_INPUTS]
    for row in result.get("rules") or []:
        lines.append(
            f"rule {row['id']} trades={row['n']} hit_rate={_fmt_rate(row['hit_rate'])} "
            f"net_pnl_sol={row['net_pnl']:.9f} gross_pnl_sol={row['gross_pnl']:.9f}"
        )
        reasons = row.get("close_reasons") or {}
        if reasons:
            lines.append("close_reasons " + " ".join(f"{name}={count}" for name, count in reasons.items()))
        for trade in row["trades"]:
            lines.append(
                f"  trade mint={trade['mint']} entry_t={trade['entry_t']} exit_t={trade['exit_t']} "
                f"exit={trade['exit_reason']} net_pnl_sol={trade['net_pnl']:.9f} "
                f"gross_pnl_sol={trade['gross_pnl']:.9f}"
            )
        base = row["baseline"]
        pct = "n/a" if base["percentile"] is None else f"{base['percentile']:.2f}"
        mean = "n/a" if base["null_mean"] is None else f"{base['null_mean']:.9f}"
        warn = " warning=fewer-than-5-clusters" if base.get("cluster_warning") else ""
        lines.append(
            f"baseline {row['id']} draws={base['draws']} statistic={base.get('statistic', 'sum')} "
            f"percentile={pct} null_mean_sol={mean} "
            f"bootstrap_ci={_fmt_ci(base['bootstrap_ci'])} cluster={base['cluster_by']} "
            f"effective_n={base['effective_n']:.4f} seed={base['seed']}{warn}"
        )
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
