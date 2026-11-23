"""Replay pluggable rules over already-loaded bars.

The entry signal reads the decision bar only. The exit object is copied onto
the position before any later bar is read. Every rule needs a positive
max_hold_s, and a position is opened only when the tape already reaches that
clock, so nothing is left open.

Quotes are the constant-product fill for the paper ticket (the same role as
the desk's Jupiter outAmount). Net PnL then uses desk.fills: the v3-exit-aware
haircut, the stress add-on on hard_stop, trailing_stop, and liquidity_pulled,
and the network fee. This module does not write and does not use the network.
"""

from __future__ import annotations

import copy
import random
import sys
from pathlib import Path

_DESK = Path(__file__).resolve().parents[2] / "solana-desk"
if str(_DESK) not in sys.path:
    sys.path.insert(0, str(_DESK))

from desk import config as C  # noqa: E402
from desk import fills  # noqa: E402


class RuleError(ValueError):
    """The rule config cannot be replayed."""


def buy_quote(sol: float, token: float, ticket: float) -> tuple[float, float]:
    """Constant-product buy. Returns (tokens_out, effective SOL per token)."""
    tokens = token * ticket / (sol + ticket)
    return tokens, ticket / tokens


def sell_mark(sol: float, token: float, tokens_to_sell: float) -> float:
    """Effective SOL per token for selling tokens_to_sell into the pool."""
    if tokens_to_sell <= 0 or sol <= 0 or token <= 0:
        return 0.0
    return sol / (token + tokens_to_sell)


def _pool_ok(bar: dict) -> bool:
    sol = bar.get("sol")
    token = bar.get("token")
    return isinstance(sol, (int, float)) and isinstance(token, (int, float)) and sol > 0 and token > 0


def _require_number(spec: dict, key: str, rule_id: str) -> float:
    value = spec[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuleError(f"rule {rule_id}: exit.{key} must be a number")
    return float(value)


def validate_rule(rule: dict) -> None:
    if not isinstance(rule, dict):
        raise RuleError("a rule must be an object")
    rule_id = rule.get("id")
    if not isinstance(rule_id, str) or not rule_id.strip():
        raise RuleError("a rule needs an id")
    entry = rule.get("entry", {})
    if not isinstance(entry, dict):
        raise RuleError(f"rule {rule_id}: entry must be an object")
    exit_spec = rule.get("exit")
    if not isinstance(exit_spec, dict):
        raise RuleError(f"rule {rule_id}: exit must be an object bound before entry")
    if "max_hold_s" not in exit_spec:
        raise RuleError(f"rule {rule_id}: exit.max_hold_s is required so the position cannot stay open")
    hold = _require_number(exit_spec, "max_hold_s", rule_id)
    if hold <= 0:
        raise RuleError(f"rule {rule_id}: exit.max_hold_s must be positive")
    if "hard_stop" in exit_spec:
        stop = _require_number(exit_spec, "hard_stop", rule_id)
        if stop >= 0:
            raise RuleError(f"rule {rule_id}: exit.hard_stop must be negative")
    if "trail" in exit_spec and exit_spec.get("trail") is not None:
        trail = _require_number(exit_spec, "trail", rule_id)
        if not 0 < trail < 1:
            raise RuleError(f"rule {rule_id}: exit.trail must be between 0 and 1")
    if "liq_drop" in exit_spec and exit_spec.get("liq_drop") is not None:
        drop = _require_number(exit_spec, "liq_drop", rule_id)
        if not 0 < drop < 1:
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
        if isinstance(level, bool) or isinstance(frac, bool):
            raise RuleError(f"rule {rule_id}: take_profit values must be numbers")
        if not isinstance(level, (int, float)) or not isinstance(frac, (int, float)):
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
    return True


def _group(bars: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for bar in bars:
        grouped.setdefault(bar["mint"], []).append(bar)
    for series in grouped.values():
        series.sort(key=lambda b: b["t_s"])
    return grouped


def _covered(series: list[dict], index: int, max_hold_s: float) -> bool:
    """A later bar exists at or after the hold clock and has a positive pool."""
    deadline = series[index]["t_s"] + max_hold_s
    for bar in series[index + 1:]:
        if bar["t_s"] >= deadline and _pool_ok(bar):
            return True
    return False


def _decide(pos: dict, bar: dict, exit_spec: dict, deadline: float):
    """Exit actions for one later bar. The exit object is the one bound at entry."""
    if not _pool_ok(bar):
        if bar["t_s"] >= deadline and pos["frac"] > 1e-12:
            return [("sell", pos["frac"], "pool_gone", 0.0)], True
        return [], False
    mark = sell_mark(bar["sol"], bar["token"], pos["tokens"] * pos["frac"])
    pos["peak"] = max(pos["peak"], mark)
    ret = mark / pos["entry"] - 1
    if exit_spec.get("liq_drop") is not None and pos["liq0"] and bar["sol"] < pos["liq0"] * (1 - float(exit_spec["liq_drop"])):
        return [("sell", pos["frac"], "liquidity_pulled", mark)], True
    if exit_spec.get("hard_stop") is not None and ret <= float(exit_spec["hard_stop"]):
        return [("sell", pos["frac"], "hard_stop", mark)], True
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
        return acts, True
    if bar["t_s"] >= deadline and pos["frac"] > 1e-12:
        acts.append(("sell", pos["frac"], "max_hold", mark))
        return acts, True
    return acts, pos["frac"] <= 1e-12


def _apply(pos: dict, acts, pool_sol, when: int) -> list[dict]:
    slices = []
    for _action, frac, why, px in acts:
        if frac <= 0:
            continue
        dx = fills.gross_proceeds(pos, frac, px) if px > 0 else 0.0
        verified = fills.verified_impact_bps(pool_sol, dx) if dx > 0 else None
        impact = 0.0 if verified is None else verified
        haircut = 0.0 if dx <= 0 else fills.haircut_frac(pool_sol, impact, why)
        gross, net = fills.slice_pnl(pos, frac, px, haircut)
        slices.append({
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
    return slices


def _walk(series: list[dict], index: int, exit_spec: dict, rule_id: str) -> tuple[dict, int]:
    """Simulate one position. exit_spec is already a private copy."""
    entry = series[index]
    hold = float(exit_spec["max_hold_s"])
    deadline = entry["t_s"] + hold
    tokens, entry_px = buy_quote(entry["sol"], entry["token"], C.TICKET_SOL)
    cost, h_entry = fills.entry_cost(entry["sol"])
    pos = {
        "entry": entry_px,
        "tokens": tokens,
        "frac": 1.0,
        "peak": entry_px,
        "opened": int(entry["t_s"]),
        "steps": 0,
        "liq0": entry["sol"],
        "entry_cost": cost,
        "entry_haircut": h_entry,
    }
    slices: list[dict] = []
    j = index + 1
    while j < len(series) and pos["frac"] > 1e-12:
        bar = series[j]
        acts, done = _decide(pos, bar, exit_spec, deadline)
        pool = bar["sol"] if _pool_ok(bar) else None
        slices.extend(_apply(pos, acts, pool, bar["t_s"]))
        j += 1
        if done or bar["t_s"] >= deadline:
            break
    if pos["frac"] > 1e-9 or not slices:
        raise RuleError(f"rule {rule_id}: exit was not fixed before entry")
    gross = sum(s["gross_pnl"] for s in slices)
    net = sum(s["net_pnl"] for s in slices)
    trade = {
        "rule": rule_id,
        "mint": entry["mint"],
        "entry_t": int(entry["t_s"]),
        "exit_t": int(slices[-1]["t"]),
        "exit_reason": slices[-1]["why"],
        "exit_fixed": exit_spec,
        "entry_haircut": h_entry,
        "entry_cost": cost,
        "gross_pnl": gross,
        "net_pnl": net,
        "slices": slices,
    }
    return trade, j


def _simulate(grouped: dict[str, list[dict]], rule: dict, predicate) -> list[dict]:
    rule_id = rule["id"]
    hold = float(rule["exit"]["max_hold_s"])
    trades = []
    for mint in sorted(grouped):
        series = grouped[mint]
        i = 0
        while i < len(series):
            bar = series[i]
            if not _pool_ok(bar) or not predicate(bar) or not _covered(series, i, hold):
                i += 1
                continue
            bound = copy.deepcopy(rule["exit"])
            trade, i = _walk(series, i, bound, rule_id)
            trades.append(trade)
    return trades


def _baseline(grouped, rule: dict, trades: list[dict], seed: int) -> tuple[list[dict], int]:
    """Random entries, same bound exit, same count. Rule entries are left out."""
    used = {(t["mint"], t["entry_t"]) for t in trades}
    hold = float(rule["exit"]["max_hold_s"])
    candidates = []
    for mint in sorted(grouped):
        series = grouped[mint]
        for i, bar in enumerate(series):
            if (mint, bar["t_s"]) in used or not _pool_ok(bar) or not _covered(series, i, hold):
                continue
            candidates.append((mint, i))
    rng = random.Random(seed)
    rng.shuffle(candidates)
    picked = candidates[:len(trades)]
    picked.sort(key=lambda item: (grouped[item[0]][item[1]]["t_s"], item[0]))
    out = []
    for mint, i in picked:
        bound = copy.deepcopy(rule["exit"])
        trade, _ = _walk(grouped[mint], i, bound, rule["id"])
        trade["baseline"] = True
        out.append(trade)
    shortfall = len(trades) - len(out)
    return out, shortfall


def _rate(trades: list[dict]):
    if not trades:
        return None
    return sum(1 for t in trades if t["net_pnl"] > 0) / len(trades)


def _round(value: float) -> float:
    return round(float(value), 9)


def _summary(trades: list[dict]) -> dict:
    return {
        "n": len(trades),
        "hit_rate": _rate(trades),
        "net_pnl": _round(sum(t["net_pnl"] for t in trades)),
        "gross_pnl": _round(sum(t["gross_pnl"] for t in trades)),
        "trades": trades,
    }


def _public_trade(trade: dict) -> dict:
    slices = []
    for sl in trade["slices"]:
        slices.append({
            "t": sl["t"],
            "why": sl["why"],
            "frac": sl["frac"],
            "px": sl["px"],
            "gross_pnl": sl["gross_pnl"],
            "net_pnl": sl["net_pnl"],
            "exit_haircut": sl["exit_haircut"],
            "pool_sol": sl["pool_sol"],
            "dx_sol": sl["dx_sol"],
        })
    return {
        "rule": trade["rule"],
        "mint": trade["mint"],
        "entry_t": trade["entry_t"],
        "exit_t": trade["exit_t"],
        "exit_reason": trade["exit_reason"],
        "exit_fixed": trade["exit_fixed"],
        "gross_pnl": trade["gross_pnl"],
        "net_pnl": trade["net_pnl"],
        "slices": slices,
        "baseline": bool(trade.get("baseline")),
    }


def replay(bars: list[dict], rules, seed: int | None = None) -> dict:
    """Per-rule trades, net-of-cost PnL, hit rate, and a random-entry baseline."""
    if isinstance(rules, dict):
        if seed is None:
            seed = int(rules.get("seed", 1))
        rule_list = rules.get("rules") or []
    else:
        rule_list = list(rules)
        if seed is None:
            seed = 1
    if not isinstance(rule_list, list) or not rule_list:
        raise RuleError("rules must be a non-empty list")
    for rule in rule_list:
        validate_rule(rule)
    grouped = _group(bars)
    rows = []
    for rule in rule_list:
        entry = rule.get("entry") or {}
        trades = _simulate(grouped, rule, lambda bar, entry=entry: entry_match(bar, entry))
        base, shortfall = _baseline(grouped, rule, trades, seed)
        rule_sum = _summary(trades)
        base_sum = _summary(base)
        rows.append({
            "id": rule["id"],
            "n": rule_sum["n"],
            "hit_rate": rule_sum["hit_rate"],
            "net_pnl": rule_sum["net_pnl"],
            "gross_pnl": rule_sum["gross_pnl"],
            "trades": [_public_trade(t) for t in rule_sum["trades"]],
            "baseline": {
                "seed": seed,
                "n": base_sum["n"],
                "hit_rate": base_sum["hit_rate"],
                "net_pnl": base_sum["net_pnl"],
                "gross_pnl": base_sum["gross_pnl"],
                "shortfall": shortfall,
                "trades": [_public_trade(t) for t in base_sum["trades"]],
            },
        })
    return {"cost_model": C.COST_MODEL, "ticket_sol": C.TICKET_SOL, "rules": rows}


def _fmt_rate(rate) -> str:
    if rate is None:
        return "n/a"
    return f"{rate:.4f}"


def format_report(result: dict) -> str:
    """One block per rule: the rule's trades, then the random-entry baseline."""
    lines = []
    for row in result.get("rules") or []:
        lines.append(
            f"rule {row['id']} trades={row['n']} hit_rate={_fmt_rate(row['hit_rate'])} "
            f"net_pnl_sol={row['net_pnl']:.9f} gross_pnl_sol={row['gross_pnl']:.9f}"
        )
        for trade in row["trades"]:
            lines.append(
                f"  trade mint={trade['mint']} entry_t={trade['entry_t']} exit_t={trade['exit_t']} "
                f"exit={trade['exit_reason']} net_pnl_sol={trade['net_pnl']:.9f} "
                f"gross_pnl_sol={trade['gross_pnl']:.9f}"
            )
        base = row["baseline"]
        lines.append(
            f"baseline {row['id']} trades={base['n']} hit_rate={_fmt_rate(base['hit_rate'])} "
            f"net_pnl_sol={base['net_pnl']:.9f} gross_pnl_sol={base['gross_pnl']:.9f} "
            f"seed={base['seed']} shortfall={base['shortfall']}"
        )
        for trade in base["trades"]:
            lines.append(
                f"  trade mint={trade['mint']} entry_t={trade['entry_t']} exit_t={trade['exit_t']} "
                f"exit={trade['exit_reason']} net_pnl_sol={trade['net_pnl']:.9f} "
                f"gross_pnl_sol={trade['gross_pnl']:.9f}"
            )
    if not lines:
        return ""
    return "\n".join(lines) + "\n"
