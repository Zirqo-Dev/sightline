"""One race slot. Paper only. Writes the races-data files and nothing else."""

import hashlib
import importlib
import json
import random
from datetime import datetime, timedelta, timezone

from races import prereg
from races.budget import Budget
from races.chain import Chain
from races.discover import Discovery
from races.fills import book_of
from races.http import Transport
from races.jupiter import Jupiter
from races.ledger import Ledger
from races.stats import (
    average,
    bootstrap_ci,
    day_count,
    official_pct,
    since_start_pct,
)

ROOT_WALLETS = ("R1", "R2", "R3", "R4", "R5", "SOL")
WSOL = "So11111111111111111111111111111111111111112"


def run_slot(now):
    now = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if now.minute >= 42:
        return now.replace(minute=42)
    if now.minute >= 12:
        return now.replace(minute=12)
    return (now - timedelta(hours=1)).replace(minute=42)


def shared_draw(mints, slot_iso):
    ordered = sorted(mints)
    if not ordered:
        return {"enter": False, "reason": "empty_universe", "mint": None}
    material = f"{slot_iso}|R1R2".encode("utf-8")
    seed = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
    rng = random.Random(seed)
    if rng.randrange(6) != 0:
        return {"enter": False, "reason": "no_draw", "mint": None, "n": len(ordered)}
    return {"enter": True, "reason": "draw", "mint": ordered[rng.randrange(len(ordered))], "n": len(ordered)}


class Ctx:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.skip_marks = set()
        self.pool_cache = {}

    def pool_status(self, pool_id):
        if not pool_id:
            return "unknown"
        if pool_id in self.pool_cache:
            return self.pool_cache[pool_id]
        url = self.cfg["discovery"]["dex_batch_url"] + pool_id
        result = self.transport.request("GET", url, headers={"user-agent": "sightline-races", "accept": "application/json"})
        if result is None or result.temporary or result.status == 429:
            status = "unknown"
        elif result.status == 404:
            status = "gone"
        elif result.status == 200 and isinstance(result.body, dict):
            pairs = result.body.get("pairs") or []
            status = "gone" if not pairs else "ok"
        else:
            status = "unknown"
        self.pool_cache[pool_id] = status
        return status


def load_config(data_dir):
    repo = json.loads((prereg.ROOT / "config.json").read_text(encoding="utf-8"))
    lock = data_dir / "config.lock.json"
    if lock.is_file():
        return json.loads(lock.read_text(encoding="utf-8")), True
    return repo, False


def run_once(data_dir, now, dry_run=False, transport=None):
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg, locked = load_config(data_dir)
    slot = run_slot(now)
    slot_iso = slot.strftime("%Y-%m-%dT%H:%M:00Z")
    ledger = Ledger(data_dir, {cfg["wsol_mint"], cfg["usdc_mint"]})
    lines = []

    def log(wallet, kind, detail):
        row = {"ts": slot_iso, "wallet": wallet, "kind": kind}
        row.update(detail or {})
        if detail and detail.get("mint"):
            ledger.allow_add(detail.get("mint"))
        if detail and detail.get("pool_id"):
            ledger.allow_add(detail.get("pool_id"))
        ledger.append("decisions.jsonl", row)
        lines.append(row)

    prereg.check_prereg(cfg, log=lambda message: log("race", "decision", {"action": "prereg", "message": message}))
    state = ledger.read_json("state.json", {})
    if not state.get("started_at"):
        state["started_at"] = slot_iso
    budget = Budget(cfg)
    transport = transport or Transport(float(cfg["http_timeout_seconds"]))
    jup = Jupiter(cfg, transport, budget, log)
    chain = Chain(cfg, transport, log)
    discovery = Discovery(cfg, transport, chain, log)
    if dry_run:
        cfg = dict(cfg)
        cfg["gecko_interval_seconds"] = 0
        discovery.cfg = cfg
    rows = discovery.confirm(discovery.gecko_candidates(), slot.timestamp())
    for row in rows:
        ledger.allow_add(row["mint"], row["pool_id"])
    draw = shared_draw([row["mint"] for row in rows], slot_iso)
    log("R1", "decision", {"action": draw["reason"], "mint": draw.get("mint"), "n": draw.get("n")})
    ctx = Ctx(
        cfg=cfg, slot=slot, slot_iso=slot_iso, state=state, jup=jup, chain=chain,
        ledger=ledger, log=log, transport=transport, draw=draw,
        by_mint={row["mint"]: row for row in rows}, coverage=discovery.coverage,
    )
    for wallet in ROOT_WALLETS:
        book_of(state, wallet, cfg["starting_sol"])
        module = importlib.import_module(cfg["wallets"][wallet]["module"])
        module.on_run(ctx)
    _snapshots(ctx, budget, locked, dry_run, data_dir)
    return {"slot": slot_iso, "calls": budget.snapshot(), "draw": draw}


def _snapshots(ctx, budget, locked, dry_run, data_dir):
    cfg = ctx.cfg
    starting = float(cfg["starting_sol"])
    slot_ts = ctx.slot.timestamp()
    started = datetime.fromisoformat(ctx.state["started_at"].replace("Z", "+00:00")).timestamp()
    trades = ctx.ledger.read_jsonl("trades.jsonl")
    positions = []
    rows = []
    days = {}
    officials = {}
    for wallet in ROOT_WALLETS:
        book = ctx.state["wallets"][wallet]
        deployed = 0.0
        live_tickets = 0
        for pos in book["positions"]:
            mark = pos.get("mark_sol")
            if mark is None:
                mark = pos.get("entry_outlay_sol") or 0.0
            deployed += float(mark)
            if float(pos.get("mark_sol") or 0) > 0:
                live_tickets += 1
            positions.append({
                "wallet": wallet,
                "symbol": pos.get("symbol"),
                "mint": pos.get("mint"),
                "pool_id": pos.get("pool_id"),
                "opened_at": pos.get("opened_at"),
                "entry_price_sol": pos.get("entry_price_sol"),
                "entry_sol": pos.get("entry_outlay_sol"),
                "size_sol": pos.get("size_sol"),
                "mark_price_sol": pos.get("mark_price_sol"),
                "mark_sol": pos.get("mark_sol"),
                "unrealized_pnl_sol": None if pos.get("mark_sol") is None else float(pos["mark_sol"]) - float(pos["entry_outlay_sol"]),
                "failed_sell_quotes": pos.get("failed_sell_quotes") or 0,
                "mark_stale": bool(pos.get("mark_stale")),
                "meme": pos.get("meme"),
            })
            ctx.ledger.allow_add(pos.get("mint"), pos.get("pool_id"))
        equity = float(book["cash_sol"]) + deployed
        since = since_start_pct(equity, starting)
        hist = book.setdefault("equity_hist", [])
        basis, trailing = official_pct(equity, [(row[0], row[1]) for row in hist], slot_ts, cfg["stats"]["period_days"])
        hist.append([slot_ts, equity])
        pct_dep = 0.0 if wallet == "SOL" else (float(cfg["ticket_sol"]) * live_tickets) / starting
        book["deployed_sum"] = float(book.get("deployed_sum") or 0) + pct_dep
        book["deployed_n"] = int(book.get("deployed_n") or 0) + 1
        closed = _closed(trades, wallet, cfg["ticket_sol"])
        ci = None if wallet == "SOL" else bootstrap_ci(
            closed, cfg["stats"]["bootstrap_draws"], cfg["stats"]["bootstrap_seed"], cfg["stats"]["min_closes_for_interval"]
        )
        days[wallet] = day_count(started, slot_ts)
        officials[wallet] = {"since": since, "basis": basis, "trailing": trailing, "closed": closed, "ci": ci}
        ctx.ledger.append("equity.jsonl", {
            "ts": ctx.slot_iso, "wallet": wallet, "equity_sol": equity, "net_pct": since,
        })
        wcfg = cfg["wallets"][wallet]
        row = {
            "id": wallet,
            "name": wcfg["name"],
            "label": wcfg.get("label") or "",
            "note": wcfg.get("note") or "",
            "equity_sol": equity,
            "starting_sol": starting,
            "since_start_net_pct": since,
            "day_count": days[wallet],
            "n_trades": len(closed),
            "ci95_low": None if not ci else ci[0],
            "ci95_high": None if not ci else ci[1],
            "avg_pct_deployed": book["deployed_sum"] / book["deployed_n"],
            "open_positions": len(book["positions"]),
            "status": "ok",
        }
        if wallet == "SOL":
            mark = ctx.state.get("sol_usdc") or {}
            row["usdc_mark"] = mark.get("usdc")
            row["usdc_stale"] = bool(mark.get("stale"))
            row["since_start_net_pct"] = 0.0
        rows.append(row)
    ready = all(days[w] >= int(cfg["stats"]["period_days"]) for w in ROOT_WALLETS)
    for row in rows:
        info = officials[row["id"]]
        if ready and info["basis"] == "trailing_30d" and info["trailing"] is not None and row["id"] != "SOL":
            row["official_net_pct"] = info["trailing"]
            row["basis"] = "trailing_30d"
        else:
            row["official_net_pct"] = 0.0 if row["id"] == "SOL" else info["since"]
            row["basis"] = "since_start"
            row["window_label"] = f"so far, {row['day_count']} days"
    rows.sort(key=lambda row: (-float(row["official_net_pct"]), row["id"]))
    top = rows[0]["official_net_pct"] if rows else None
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
        row["leader_label"] = cfg["banners"]["leader"] if row["official_net_pct"] == top else None
    r5_open = [pos for pos in positions if pos["wallet"] == "R5"]
    r3_mints = {pos["mint"] for pos in positions if pos["wallet"] == "R3"}
    r3_mints.update(row["mint"] for row in (ctx.state.get("r3") or {}).get("selected") or [])
    overlap_k = sum(1 for pos in r5_open if pos.get("mint") in r3_mints)
    calls = budget.snapshot()
    summary = {
        "schema_version": 1,
        "asof": ctx.slot_iso,
        "last_run_ts": ctx.slot_iso,
        "run_status": "dry-run" if dry_run else ("degraded" if ctx.jup.block_entries or ctx.coverage.get("rate_limits") else "ok"),
        "banner": cfg["banners"]["standing"],
        "leader_label": cfg["banners"]["leader"],
        "rank_by": "official_net_pct",
        "jupiter_calls": calls,
        "r5_calls": calls.get("R5", 0),
        "coverage": ctx.coverage,
        "wallets": rows,
        "r5_split": _split(officials["R5"]["closed"]),
        "r5_overlap": {
            "k": overlap_k,
            "n": len(r5_open),
            "line": f"R5 holdings also in R3: {overlap_k} of {len(r5_open)}",
            "note": cfg["banners"]["r3_r5_note"],
        },
    }
    ctx.ledger.write_json("positions.json", {"schema_version": 1, "asof": ctx.slot_iso, "positions": positions})
    ctx.ledger.write_json("summary.json", summary)
    ctx.ledger.write_json("state.json", ctx.state)
    ctx.ledger.append("runs.jsonl", {"ts": ctx.slot_iso, "calls": calls, "wallets": {k: calls[k] for k in ("R1", "R2", "R3", "R4", "SOL", "R5")}})
    if not dry_run and not locked:
        (data_dir / "config.lock.json").write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _closed(trades, wallet, ticket):
    buys = {}
    out = []
    for row in trades:
        if row.get("wallet") != wallet:
            continue
        if row.get("action") == "buy":
            buys.setdefault(row.get("mint"), []).append(row)
        elif row.get("action") == "sell" and row.get("pnl_sol") is not None:
            queue = buys.get(row.get("mint")) or []
            buy = queue.pop(0) if queue else None
            day = ((buy or row).get("ts") or "")[:10]
            out.append({
                "day": day,
                "net_pct": float(row["pnl_sol"]) / float(ticket) * 100.0,
                "meme": row.get("meme"),
            })
    return out


def _split(closed):
    def bucket_of(row):
        if row.get("bucket") in ("meme", "non_meme", "untagged"):
            return row["bucket"]
        return "meme" if row.get("meme") else "non_meme"

    def side(name):
        rows = [row for row in closed if bucket_of(row) == name]
        net = average([row["net_pct"] for row in rows]) if rows else None
        return {"n": len(rows), "net_pct": net}

    return {"meme": side("meme"), "non_meme": side("non_meme"), "untagged": side("untagged")}
