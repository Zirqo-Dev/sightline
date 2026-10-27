"""SPD-44 founder whale ledger ($cockwhale), zero AI, free Koios public tier (no key).

Writes desk-data:solada-prefilter/whale_ledger.json. Rows:
  {time, tx_short, kind, token, qty, ada_leg, counterparty_tag, order_ref_short?, order_status?}
  kind: swap | lp_add | lp_remove | order_open | order_fill | order_cancel | transfer_in | transfer_out
plus running positions {token: raw qty}, ada_balance, and DEX orders {ref_short: {...status}}.

Idempotent: every tx is applied at most once (dedupe by full tx hash, kept in the ledger file), so a
re-run, an overlapping block window or a retried run never double-counts a buy (the Oct 26 ASCEND ~2x
overstatement came from re-counting a tx window). Positions change only from the net per-tx deltas of
the whale's own UTxOs; each run's balances are checked against Koios account_info / account_assets
and any drift is reported (never silently "fixed").

DEX orders (batcher-style: Splash/Spectrum/Minswap/Genius etc.): a whale tx that sends a token (or 10+ ADA)
to a script address without getting value back opens an order (ref = tx#index). A later tx that spends
that ref is a fill (whale gets the other side) or a cancel (whale gets the same token back). If the ref is
spent by a tx the whale does not appear in, status = closed_elsewhere (checked with utxo_info).

The whale stake key is public third-party research data in config.json (whale.stake). Output carries only
its tag; counterparties are tags (<= 12 chars). Never touches the owner's wallet.
"""
import hashlib, json, os
from datetime import datetime, timezone

import flags as F
import koios_tx as KT
import valuation as V

KOIOS = "https://api.koios.rest/api/v1"
BF = "https://cardano-mainnet.blockfrost.io/api/v0"
DEFAULTS = {"enabled": True, "max_txs": 100, "utxo_batch": 10, "max_calls": 60, "max_bf_calls": 120, "keep_rows": 3000,
            "keep_seen": 20000, "order_min_ada": 10, "min_ada_row": 1.0, "budget_s": 90}


def wl_cfg(cfg=None):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("whale_ledger") or {}).items() if not k.startswith("_")})
    return out


def tag(s):
    return "w-" + hashlib.sha256(s.encode()).hexdigest()[:8]


def iso(ts):
    return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def token_name(unit):
    h = unit[56:]
    h = h[8:] if h[:8] in ("0014df10", "000de140", "000643b0") else h
    try:
        s = bytes.fromhex(h).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        s = ""
    return s if s and s.isprintable() and len(s) <= 24 else unit[:12]


def token_key(unit, names):
    """Stable display key per unit; a second unit with the same name gets ~policy6 appended."""
    if unit in names:
        return names[unit]
    k = token_name(unit)
    if k in names.values():
        k = f"{k}~{unit[:6]}"
    names[unit] = k
    return k


def _is_mine(u, stake):
    return u.get("stake") == stake or (not u.get("stake") and F.addr_info(u["addr"])[0] == stake)


def _script(addr):
    return F.addr_info(addr)[1]


def _creds(tx, stake):
    """Hex credentials that identify the whale inside a datum: its stake credential plus the payment
    credentials of its key addresses in this tx (order contracts store owner creds in the datum)."""
    out = set()
    raw = F.b32_decode(stake)[1]
    if raw:
        out.add(raw[1:].hex())
    for u in tx["inputs"] + tx["outputs"]:
        if _is_mine(u, stake) and not _script(u["addr"]):
            r = F.b32_decode(u["addr"])[1]
            if r and len(r) >= 29:
                out.add(r[1:29].hex())
    return out


def classify(tx, stake, ledger, lp_pols, wc):
    """Rows + net deltas for one tx. tx = {hash, time, inputs/outputs: [{ref, addr, stake, lovelace, assets}]}.
    Mutates ledger['orders'] (open/fill/cancel). Pure otherwise (no network)."""
    names = ledger.setdefault("token_names", {})
    orders = ledger.setdefault("orders", {})
    lov, toks = 0, {}
    for side, sign in (("inputs", -1), ("outputs", 1)):
        for u in tx[side]:
            if _is_mine(u, stake):
                lov += sign * u["lovelace"]
                for unit, q in u["assets"].items():
                    toks[unit] = toks.get(unit, 0) + sign * q
    toks = {k: v for k, v in toks.items() if v}
    t, ts = iso(tx["time"]), tx["hash"][:10]
    rows = []

    def row(kind, token, qty, ada, cp=None, **kw):
        rows.append({"time": t, "tx_short": ts, "kind": kind, "token": token, "qty": qty,
                     "ada_leg": None if ada is None else round(ada, 6), "counterparty_tag": cp, **kw})

    others_in = [u for u in tx["inputs"] if not _is_mine(u, stake)]
    others_out = [u for u in tx["outputs"] if not _is_mine(u, stake)]
    script_io = any(_script(u["addr"]) for u in others_in + others_out)

    pats = wc.get("_owner_pats")

    def cp_tag(us):
        """Largest key-address counterparty's tag. Owner wallets (PRIVACY_GUARD_PATTERNS) are never tagged, and with no
        usable owner patterns no counterparty is tagged at all (fail closed; re-review nice-to-have)."""
        if not pats:
            return None
        keys = [u for u in us if not _script(u["addr"])]
        keys = [u for u in keys if not any(p.search(x) for p in pats
                                           for x in (u["addr"], u.get("stake") or F.addr_info(u["addr"])[0] or ""))]
        if not keys:
            return None
        big = max(keys, key=lambda u: u["lovelace"])
        return tag(big.get("stake") or F.addr_info(big["addr"])[0] or big["addr"])

    # 1) spends of tracked open orders -> replace (edit) / moved / partial fill / cancel / fill
    consumed = [u for u in tx["inputs"] if u.get("ref") in orders and orders[u["ref"]]["status"] == "open"]
    handled, taken = set(), set()  # taken: output refs already explained (replacement orders)
    creds = _creds(tx, stake) if consumed else set()
    # whale-signed: the whale spends one of its own key-address UTxOs here (an edit / cancel it made itself). A tx
    # without one was built by a batcher or a protocol (re-review S8: never call that a whale "replace").
    signed = any(_is_mine(x, stake) and not _script(x["addr"]) for x in tx["inputs"])
    cons_refs = {x["ref"] for x in consumed}
    other_script_in = any(_script(x["addr"]) for x in tx["inputs"] if x.get("ref") not in cons_refs and not _is_mine(x, stake))

    def new_order(ref, x, o, q, **kw):
        short = ref[:10] + ref[ref.index("#"):]
        orders[ref] = {"ref_short": short, "unit": o["unit"], "token": o["token"], "qty_raw": q,
                       "ada": round(x["lovelace"] / 1e6, 6), "status": "open", "opened_tx": ts, "opened_at": t, **kw}
        return short

    for u in consumed:
        o = orders[u["ref"]]
        old_hash = u["ref"].split("#")[0]
        # S8 / Snek (a): the order's token lands in a new script output that is the whale's (stake) or whose datum
        # names the old order / the whale's creds. Rules (re-review):
        #  - whale-signed + same token (any qty) at a named output, or at any script output when no pool UTxO is spent
        #    (datum-hash orders carry no inline datum) -> replaced (an edit; qty may change)
        #  - not whale-signed + the full qty (100-101%) at a named output -> order_moved (batcher/protocol; paid_ada)
        #  - not whale-signed + less at a named output -> partial fill, even a 1% one (remainder stays open)
        repl = None
        for i, x in enumerate(tx["outputs"]):
            ref = x.get("ref") or f"{tx['hash']}#{i}"
            if ref in taken or not _script(x["addr"]):
                continue
            if o["unit"] == "lovelace" and x["assets"]:
                continue
            q = x["lovelace"] if o["unit"] == "lovelace" else x["assets"].get(o["unit"], 0)
            if q <= 0:
                continue
            named = _is_mine(x, stake) or old_hash in x.get("datum", "") or any(c in x.get("datum", "") for c in creds)
            whole = o["qty_raw"] <= q <= o["qty_raw"] * 1.01  # unsigned move: the full qty, nothing taken out
            if signed and (named or (not x.get("datum") and not other_script_in)):
                repl = ("replaced", ref, x, q)
            elif not signed and named and whole:
                repl = ("moved", ref, x, q)
            elif not signed and named and q < o["qty_raw"] and o["unit"] != "lovelace":
                repl = ("partial", ref, x, q)
            if repl:
                break
        if repl:
            kind, ref, x, q = repl
            taken.add(ref)
            if kind == "replaced":
                short = new_order(ref, x, o, q, replaces=o["ref_short"])
                o.update(status="replaced", closed_tx=ts, closed_at=t, replaced_by=short)
                row("order_cancel", o["token"], o["qty_raw"], None, order_ref_short=o["ref_short"], order_status="replaced",
                    replaced_by=short)
                row("order_open", o["token"], q, x["lovelace"] / 1e6, order_ref_short=short, order_status="open",
                    replaces=o["ref_short"])
            elif kind == "moved":
                paid = round(lov / 1e6, 6) if lov > 0 else 0.0
                short = new_order(ref, x, o, q, moved_from=o["ref_short"])
                o.update(status="moved", closed_tx=ts, closed_at=t, moved_to=short)
                row("order_moved", o["token"], q, None, order_ref_short=o["ref_short"], order_status="moved",
                    moved_to=short, paid_ada=paid)
                handled.add("lovelace")  # the ADA paid to the whale is reported as paid_ada on this row
            else:
                short = new_order(ref, x, o, q, remainder_of=o["ref_short"])
                o.update(status="partially_filled", closed_tx=ts, closed_at=t, remainder=short,
                         filled_qty=o["qty_raw"] - q, filled_ada=round(lov / 1e6, 6))
                row("order_fill", o["token"], o["qty_raw"] - q, lov / 1e6, order_ref_short=o["ref_short"],
                    order_status="partial", remainder=short)
                handled.add("lovelace")
            handled.add(o["unit"])
            continue
        got = {k: v for k, v in toks.items() if v > 0 and k != o["unit"] and not V.lp_dex(k, lp_pols)}
        back = toks.get(o["unit"], 0) if o["unit"] != "lovelace" else None
        if o.get("own_stake") and not got and (back is None and lov > -1_000_000 * 3 or
                                                (back is not None and back > -o["qty_raw"] * 0.99)):
            # own-stake order contract: value never left the whale's balance; nothing swapped -> cancel
            o.update(status="cancelled", closed_tx=ts, closed_at=t)
            row("order_cancel", o["token"], o["qty_raw"], None, order_ref_short=o["ref_short"], order_status="cancelled")
            handled.add(o["unit"])
        elif o["unit"] != "lovelace" and back >= o["qty_raw"] * 0.99:
            o.update(status="cancelled", closed_tx=ts, closed_at=t)
            row("order_cancel", o["token"], o["qty_raw"], lov / 1e6, order_ref_short=o["ref_short"], order_status="cancelled")
            handled.add(o["unit"])
        elif o["unit"] == "lovelace" and not got and lov > 0 and lov >= o["qty_raw"] * 0.9:
            # S8: ADA buy order spent and the ADA came back with no token -> a cancel, not a fill
            o.update(status="cancelled", closed_tx=ts, closed_at=t)
            row("order_cancel", "ADA", o["qty_raw"], lov / 1e6, order_ref_short=o["ref_short"], order_status="cancelled")
        elif got:  # filled with tokens (ADA buy order, or token -> token)
            (gu, gq), = sorted(got.items(), key=lambda x: -x[1])[:1]
            o.update(status="filled", closed_tx=ts, closed_at=t, filled_token=token_key(gu, names), filled_qty=gq)
            row("order_fill", token_key(gu, names), gq, o["ada"] if o["unit"] == "lovelace" else None,
                order_ref_short=o["ref_short"], order_status="filled", sold=o["token"])
            handled.update(got)  # S8: received units are the fill, not a stray transfer_in
        else:  # sell order filled with ADA
            o.update(status="filled", closed_tx=ts, closed_at=t, filled_ada=round(lov / 1e6, 6))
            row("order_fill", o["token"], o["qty_raw"], lov / 1e6, order_ref_short=o["ref_short"], order_status="filled")
        handled.add(o["unit"])
        handled.add("lovelace")
    if consumed and taken and "lovelace" not in handled and abs(lov) / 1e6 >= wc["min_ada_row"]:
        # ADA that reached the whale in a replace tx came from another script (e.g. a loan pool), not a fill
        row("transfer_in" if lov > 0 else "transfer_out", "ADA", None, abs(lov) / 1e6, None, via="script")
        handled.add("lovelace")
    # 2) LP tokens
    for unit, q in toks.items():
        if unit not in handled and V.lp_dex(unit, lp_pols):
            row("lp_add" if q > 0 else "lp_remove", "LP:" + V.lp_dex(unit, lp_pols), abs(q), abs(lov) / 1e6)
            handled.add(unit)
            handled.add("lovelace")
    lp_tx = any(V.lp_dex(u, lp_pols) for u in toks)
    rest = {k: v for k, v in toks.items() if k not in handled and not lp_tx}
    signed_any = any(_is_mine(x, stake) and not _script(x["addr"]) for x in tx["inputs"])
    # 3) new orders: value parked at a script output while the whale gets nothing back
    if "lovelace" not in handled and not lp_tx:
        for i, u in enumerate(tx["outputs"]):
            if (u.get("ref") or f"{tx['hash']}#{i}") in taken or not _script(u["addr"]):
                continue
            if _is_mine(u, stake):
                # S8: an order contract address that carries the whale's own stake part. Its value still counts in
                # the whale's balance (Koios account_assets includes it), so no delta: open the order from the output.
                if not signed_any or any(_script(x["addr"]) for x in others_in) or any(V.lp_dex(k, lp_pols) for k in u["assets"]):
                    continue  # a third party paying into the whale-stake script is a transfer, not a whale order
                ref = u.get("ref") or f"{tx['hash']}#{i}"
                toks_here = {k: q for k, q in u["assets"].items() if q > 0}
                if toks_here:
                    (unit, q), = sorted(toks_here.items(), key=lambda x: -x[1])[:1]
                    key = token_key(unit, names)
                elif u["lovelace"] / 1e6 >= wc["order_min_ada"]:
                    unit, q, key = "lovelace", u["lovelace"], "ADA"
                else:
                    continue
                orders[ref] = {"ref_short": ref[:10] + ref[ref.index("#"):], "unit": unit, "token": key, "qty_raw": q,
                               "ada": round(u["lovelace"] / 1e6, 6), "status": "open", "opened_tx": ts, "opened_at": t,
                               "own_stake": True}
                row("order_open", key, q, u["lovelace"] / 1e6, order_ref_short=orders[ref]["ref_short"], order_status="open")
                continue
            # an order output holds only what the whale sent (a pool output holds far more), and an order-open
            # tx spends no other script UTxO (a direct or batched swap spends pool UTxOs)
            sent = {k: q for k, q in u["assets"].items() if rest.get(k, 0) < 0 and q <= -rest[k] * 1.01}
            if sent and lov <= 0 and not any(_script(x["addr"]) for x in others_in):
                (unit, q), = sorted(sent.items(), key=lambda x: -x[1])[:1]
                ref = u.get("ref") or f"{tx['hash']}#{i}"
                orders[ref] = {"ref_short": ref[:10] + ref[ref.index("#"):], "unit": unit, "token": token_key(unit, names),
                               "qty_raw": q, "ada": round(u["lovelace"] / 1e6, 6), "status": "open", "opened_tx": ts, "opened_at": t}
                row("order_open", orders[ref]["token"], q, u["lovelace"] / 1e6, order_ref_short=orders[ref]["ref_short"], order_status="open")
                rest.pop(unit, None)
                handled.add("lovelace")
            elif not u["assets"] and not rest and lov < 0 and u["lovelace"] / 1e6 >= wc["order_min_ada"] \
                    and u["lovelace"] <= -lov and not any(_script(x["addr"]) for x in others_in):
                ref = u.get("ref") or f"{tx['hash']}#{i}"
                orders[ref] = {"ref_short": ref[:10] + ref[ref.index("#"):], "unit": "lovelace", "token": "ADA",
                               "qty_raw": u["lovelace"], "ada": round(u["lovelace"] / 1e6, 6), "status": "open", "opened_tx": ts, "opened_at": t}
                row("order_open", "ADA", u["lovelace"], u["lovelace"] / 1e6, order_ref_short=orders[ref]["ref_short"], order_status="open")
                handled.add("lovelace")
    # 4) remaining token moves: swap through a script, else transfer
    for unit, q in rest.items():
        key = token_key(unit, names)
        pool_in = any(_script(x["addr"]) for x in others_in)
        if (script_io and lov and (q > 0) != (lov > 0)) or (pool_in and len(rest) > 1):
            one = len(rest) == 1 and lov and (q > 0) != (lov > 0)
            row("swap", key, abs(q), abs(lov) / 1e6 if one else None, direction="buy" if q > 0 else "sell")
        else:
            row("transfer_in" if q > 0 else "transfer_out", key, abs(q), None, cp_tag(others_in if q > 0 else others_out))
        handled.add("lovelace") if script_io else None
    # 5) ADA-only movement
    if "lovelace" not in handled and not toks and abs(lov) / 1e6 >= wc["min_ada_row"]:
        if lov > 0 and any(_script(u["addr"]) for u in others_in):
            row("order_fill", "ADA", None, lov / 1e6, order_status="filled_untracked")  # order opened before the ledger started
        else:
            row("transfer_in" if lov > 0 else "transfer_out", "ADA", None, abs(lov) / 1e6, cp_tag(others_in if lov > 0 else others_out))
    return rows, lov, toks


def apply(ledger, tx, stake, lp_pols, wc):
    """Apply one tx once. Returns rows (empty when the tx was already applied)."""
    seen = _seen_map(ledger)
    if tx["hash"] in seen:
        return []
    rows, lov, toks = classify(tx, stake, ledger, lp_pols, wc)
    names = ledger.setdefault("token_names", {})
    pos = ledger.setdefault("positions_raw", {})
    for unit, q in toks.items():
        pos[unit] = pos.get(unit, 0) + q
        if not pos[unit]:
            pos.pop(unit)
        token_key(unit, names)
    ledger["lovelace"] = ledger.get("lovelace", 0) + lov
    seen[tx["hash"]] = tx.get("height")
    ledger.setdefault("rows", []).extend(rows)
    return rows


def public_doc(ledger, whale_tag, now_s, wc, extra):
    names = ledger.get("token_names", {})
    doc = {"at": iso(now_s), "whale_tag": whale_tag, "basis": extra.pop("basis", "koios"),
           "note": "Research data for Snek. qty = raw on-chain units. Each tx is applied once (dedupe by tx hash).",
           "height": ledger.get("height"), "baseline_at": ledger.get("baseline_at"),
           "ada_balance": round(ledger.get("lovelace", 0) / 1e6, 6),
           "positions": {names.get(u, u[:12]): q for u, q in sorted(ledger.get("positions_raw", {}).items()) if q},
           "orders": {o["ref_short"]: {k: v for k, v in o.items() if k not in ("unit",)} for o in ledger.get("orders", {}).values()},
           "rows": ledger.get("rows", [])[-wc["keep_rows"]:], **extra}
    return F.assert_public(doc)


def load(path, state_paths=()):
    """Private state, first match wins: the Actions-cache file (never committed), then the #29-era
    desk-data whale_ledger_state.json (read once for migration, never written again), then the legacy `_state`
    block inside whale_ledger.json. Rows are not kept in state: they are read back from the public file.
    A corrupt file is treated as missing. Returns (state or None, source)."""
    pub = KT.read_json(path, {})
    for sp, src in state_paths:
        st = KT.read_json(sp, {}) if sp else {}
        if st:
            st.setdefault("rows", pub.get("rows") or [])
            return st, src
    st = pub.get("_state")
    return (st, "legacy") if st else (None, None)


def _row_key(r):
    return json.dumps(r, sort_keys=True)


def dedupe_rows(rows):
    """Drop exact duplicate rows (a replay from an older fallback state can re-emit rows already published)."""
    seen, out = set(), []
    for r in rows:
        k = _row_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def _seen_map(ledger):
    """seen as {tx_hash: height}. Legacy list entries get the ledger height (kept until 2 blocks past it)."""
    seen = ledger.get("seen") or {}
    if isinstance(seen, list):
        seen = {h: ledger.get("height") for h in seen}
    ledger["seen"] = seen
    return seen


def prune_seen(ledger):
    """Keep only txs from the last 2 blocks: older ones can never come back (txs_after reads > height-1)."""
    h = ledger.get("height") or 0
    ledger["seen"] = {k: v for k, v in _seen_map(ledger).items() if v is None or v >= h - 1}


class KoiosLedger:
    """Koios first (free, no key). When a Koios call fails or times out (Http.req -> None) and the
    BLOCKFROST_PROJECT_ID secret is set, the same data is read from Blockfrost instead (own call budget).
    No key = no fallback (skipped quietly). The key is only sent as a request header, never printed or stored."""

    def __init__(self, http, wc, bf_key=""):
        self.h, self.wc, self.bf_key = http, wc, (bf_key or "").strip()
        self.start = http.calls.get("koios", 0)
        self.bf_start = http.calls.get("blockfrost", 0)
        self.fallbacks = 0
        self.baseline_mode = False  # tip/balance seed: Blockfrost fallback ignores the shared deadline and a tripped Koios
        self.last_read = None  # (provider, status) of the read that just failed; never a URL, address, or key

    def bf_left(self):
        return self.wc["max_bf_calls"] - (self.h.calls.get("blockfrost", 0) - self.bf_start)

    def bf(self, path):
        if not self.bf_key or self.bf_left() <= 0:
            return None
        prev = self.h.allow_blocked
        # The research deadline closes koios and blockfrost together, so a fallback call is skipped
        # with no HTTP status. Baseline seed reads still try Blockfrost; Koios keeps its own breaker.
        self.h.allow_blocked = prev or self.baseline_mode
        try:
            return self.h.req("blockfrost", BF + path, headers={"project_id": self.bf_key})
        finally:
            self.h.allow_blocked = prev

    def _note(self, provider):
        info = (getattr(self.h, "last_fail", {}) or {}).get(provider) or {}
        status = info.get("status")
        if status is None and provider in self.h.tripped:
            status = "skipped"
        elif status is None and self.h.blocked(provider):
            status = "deadline"
        self.last_read = (provider, status)

    def baseline_failure(self, read):
        provider, status = self.last_read or ("koios", None)
        return {"error": "baseline failed", "failed_read": read, "provider": provider, "status": status}

    def _fallback(self):
        if self.bf_key:
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
            for u in x.get(s, []):
                if u.get("collateral") or u.get("reference"):
                    continue  # S5: collateral / reference inputs (and collateral return outputs) are not spends
                lov, assets = amt(u.get("amount") or [])
                ref = f"{u['tx_hash']}#{u['output_index']}" if s == "inputs" else f"{x['hash']}#{u['output_index']}"
                rows.append({"ref": ref, "addr": u.get("address", ""), "stake": F.addr_info(u.get("address", ""))[0],
                             "lovelace": lov, "assets": assets, "datum": str(u.get("inline_datum") or "")})
            out[s] = rows
        return out

    def left(self):
        return self.wc["max_calls"] - (self.h.calls.get("koios", 0) - self.start)

    def get(self, path):
        return self.h.req("koios", KOIOS + path, quiet404=False)

    def post(self, path, body):
        return self.h.req("koios", KOIOS + path, method="POST", body=body, quiet404=False)

    def txs_after(self, stake, height):
        r = self.get(f"/account_txs?_stake_address={stake}&_after_block_height={int(height)}&order=block_height.asc&limit={self.wc['max_txs']}")
        if r is None and self._fallback():
            r = self.bf(f"/accounts/{stake}/transactions?order=asc&count={min(100, self.wc['max_txs'])}&from={int(height) + 1}")
        if r is None:
            return None
        # S6: the Blockfrost `from` is not a strict height filter; keep only txs after `height` either way
        return sorted(((t["tx_hash"], t["block_height"], t["block_time"]) for t in r if t["block_height"] > int(height)),
                      key=lambda t: t[1])

    def tip_height(self, stake):
        r = self.get(f"/account_txs?_stake_address={stake}&order=block_height.desc&limit=1")
        if r is None:
            self._note("koios")
            if self._fallback():
                r = self.bf(f"/accounts/{stake}/transactions?order=desc&count=1")
                if r is None:
                    self._note("blockfrost")
        if r:
            self.last_read = None
        return r[0]["block_height"] if r else None

    def utxos(self, hashes):
        """/tx_info (S9) in <= 1 KB batches; on a Koios failure the batch is read per tx from Blockfrost."""
        out = {}
        for body in KT.batches(hashes, "_tx_hashes", self.wc["utxo_batch"], KT.TX_INFO_FLAGS):
            chunk = body["_tx_hashes"]
            if self.left() <= 2:
                break
            got = KT.tx_info(self.h, chunk, len(chunk))
            if not got and self._fallback():
                for h in chunk:
                    x = self.bf(f"/txs/{h}/utxos")
                    if x:
                        out[h] = self._bf_utxo(x)
                continue
            out.update(got)
        return out

    def _bf_balance_result(self, stake):
        """Full Blockfrost balance, or None. Never merged with a partial Koios page (that would double quantities)."""
        bal = self._bf_balances(stake)
        if bal is None:
            self._note("blockfrost")
        return bal

    def balances(self, stake):
        info = self.post("/account_info", {"_stake_addresses": [stake]})
        if info is None and self._fallback():
            return self._bf_balance_result(stake)
        if not info:
            self._note("koios")
            return None
        lov = int(info[0].get("total_balance", 0)) - int(info[0].get("rewards_available", 0) or 0)
        assets, off = {}, 0  # local to this call: a retried baseline must not add a second snapshot on top
        while self.left() > 0:
            r = self.post(f"/account_assets?offset={off}&limit=1000", {"_stake_addresses": [stake]})
            if r is None:
                if self._fallback():
                    return self._bf_balance_result(stake)  # discard the partial Koios page
                self._note("koios")
                return None
            for a in r:
                u = a["policy_id"] + a["asset_name"]
                assets[u] = assets.get(u, 0) + int(a["quantity"])
            if len(r) < 1000:
                return lov, assets
            off += 1000
        self._note("koios")
        return None

    def _bf_balances(self, stake):
        acct = self.bf(f"/accounts/{stake}")
        if not acct:
            return None
        lov = int(acct.get("controlled_amount", 0)) - int(acct.get("withdrawable_amount", 0) or 0)
        assets = {}
        for page in range(1, 40):
            r = self.bf(f"/accounts/{stake}/addresses/assets?count=100&page={page}")
            if r is None:
                return None
            for a in r:
                assets[a["unit"]] = assets.get(a["unit"], 0) + int(a["quantity"])
            if len(r) < 100:
                return lov, assets
        return None

    def spent(self, refs):
        out = {}
        for body in KT.batches(refs, "_utxo_refs", 10):
            chunk = body["_utxo_refs"]
            if self.left() <= 0:
                break
            r = self.post("/utxo_info", body)
            if r is None and self._fallback():
                for ref in chunk:
                    h, idx = ref.split("#")
                    x = self.bf(f"/txs/{h}/utxos")
                    o = next((u for u in (x or {}).get("outputs", []) if str(u.get("output_index")) == idx), None)
                    if o is not None:
                        out[ref] = bool(o.get("consumed_by_tx"))
                continue
            for u in r or []:
                out[f"{u['tx_hash']}#{u['tx_index']}"] = bool(u.get("is_spent"))
        return out


def _baseline_snapshot(kl, stake):
    """One tip + balance + confirm-tip pass. Nothing is seeded here, so a retry cannot add the same
    balances (or the tip tx, which is already inside them) a second time."""
    tip = kl.tip_height(stake)
    if tip is None:
        return None, kl.baseline_failure("tip")
    bal = kl.balances(stake)
    if bal is None:
        return None, kl.baseline_failure("balances")
    tip2 = kl.tip_height(stake)
    if tip2 is None:
        return None, kl.baseline_failure("tip")
    if tip2 != tip:  # M3: a tx landed between the reads; do not retry into an ambiguous seed
        return None, {"error": "baseline retry: account tip moved during baseline"}
    return (tip, bal), None


def run(http, cfg, data_dir, dry_run, now_s, lp_pols, bf_key=None, deadline=None, cache_dir=None, owner_env=None):
    """cache_dir: private Actions cache dir; the ledger state (tx hashes, order refs, raw positions) lives there and is
    never committed. owner_env: PRIVACY_GUARD_PATTERNS text; counterparties matching it are never tagged, and with no
    usable patterns no counterparty is tagged at all."""
    wc = wl_cfg(cfg)
    if owner_env is None:
        owner_env = os.environ.get("PRIVACY_GUARD_PATTERNS", "")
    import big_moves as BM
    pats, bad = BM.owner_patterns(owner_env)
    wc["_owner_pats"] = pats if pats and not bad else None
    stake = ((cfg.get("whale") or {}).get("stake") or "").strip()
    prefix = ((cfg.get("whale") or {}).get("stake_prefix") or "").strip()
    if not wc["enabled"] or not stake:
        return {"enabled": False}
    if prefix and not stake.startswith("stake1" + prefix):
        raise SystemExit("whale_ledger: config whale.stake does not match whale.stake_prefix")
    if bf_key is None:
        bf_key = os.environ.get("BLOCKFROST_PROJECT_ID", "")
    kl = KoiosLedger(http, wc, bf_key)
    path = os.path.join(data_dir, "whale_ledger.json")
    legacy_spath = os.path.join(data_dir, "whale_ledger_state.json")  # #29 wrote it to desk-data; read-only now
    cpath = os.path.join(cache_dir, "whale-ledger-state.json") if cache_dir else None
    ledger, src = load(path, [(cpath, "cache"), (legacy_spath, "desk-data-legacy")])
    extra = {"capped": False, "state_source": src or "baseline", "counterparty_tags": bool(wc["_owner_pats"])}
    late = lambda: bool(deadline) and __import__("time").time() >= deadline  # noqa: E731
    if ledger is not None and ledger.get("baseline_height") is None and ledger.get("baseline_at"):
        # M3 legacy: a baseline written before this fix seeded no `seen`; every tx at <= that height is already in
        # the baseline balances, so never apply it again (the 8:07 PM run's tip tx would otherwise double-count)
        ledger["baseline_height"] = ledger.get("height") if not ledger.get("rows") and not ledger.get("seen") else 0
    if ledger is None:  # baseline: seed balances, start applying txs after the current account tip
        # One retry of the tip/balance reads. Blockfrost still runs if Koios is tripped or the shared
        # research deadline has already closed both providers. A failed pass seeds nothing.
        kl.baseline_mode = True
        try:
            snap, err = _baseline_snapshot(kl, stake)
            if err and err.get("error") == "baseline failed":
                snap, err = _baseline_snapshot(kl, stake)
            if err:
                return err
            tip, bal = snap
            seed = kl.txs_after(stake, tip - 1) or []
            ledger = {"height": tip, "baseline_at": iso(now_s), "baseline_height": tip, "lovelace": bal[0],
                      "positions_raw": dict(bal[1]), "seen": {h: hg for h, hg, _ in seed if hg <= tip}, "rows": [],
                      "orders": {}, "token_names": {}}
            for u in bal[1]:
                token_key(u, ledger["token_names"])
            extra["baseline"] = True
        finally:
            kl.baseline_mode = False
    else:
        txs = kl.txs_after(stake, ledger["height"] - 1) if not late() else None  # overlap one block; dedupe by hash
        if txs is None:
            extra["capped"] = True  # S7: unread tx list -> no closed_elsewhere and no reconcile this run
            extra["stale"] = True
        else:
            seen = _seen_map(ledger)
            bh = ledger.get("baseline_height") or 0
            todo = [t for t in txs if t[0] not in seen and t[1] > bh]
            ut = kl.utxos([t[0] for t in todo])
            for h, hgt, bt in todo:
                if h not in ut:
                    extra["capped"] = True
                    break
                apply(ledger, {"hash": h, "time": bt, "height": hgt, **ut[h]}, stake, lp_pols, wc)
                ledger["height"] = hgt
            if len(txs) >= wc["max_txs"]:
                extra["capped"] = True
        open_refs = [r for r, o in ledger.get("orders", {}).items() if o["status"] == "open"]
        if open_refs and kl.left() > 2 and not extra["capped"] and not late():
            for r, sp in kl.spent(open_refs).items():
                if sp and ledger["orders"][r]["status"] == "open":
                    ledger["orders"][r].update(status="closed_elsewhere", closed_at=iso(now_s))
        if not extra["capped"] and kl.left() > 4 and not late():
            # reconcile race (re-review): a tx landing while balances are read makes a false drift. Read the tip on
            # both sides; reconcile only when neither is past the height the ledger has applied.
            t1 = kl.tip_height(stake)
            bal = kl.balances(stake) if t1 is not None and t1 <= ledger["height"] else None
            t2 = kl.tip_height(stake) if bal is not None else None
            if bal is not None and (t2 is None or t2 > ledger["height"]):
                bal = None
            if bal is None:
                extra["reconcile_skipped"] = "account tip moved past the applied height (or unreadable); next run"
            if bal is not None:
                names = ledger["token_names"]
                drift = {names.get(u, u[:12]) if u in names else token_name(u): bal[1].get(u, 0) - ledger["positions_raw"].get(u, 0)
                         for u in set(bal[1]) | set(ledger["positions_raw"]) if bal[1].get(u, 0) != ledger["positions_raw"].get(u, 0)}
                extra["reconcile"] = {"ada_drift": round((bal[0] - ledger["lovelace"]) / 1e6, 6), "token_drift": drift,
                                      "ok": not drift and abs(bal[0] - ledger["lovelace"]) < 1_000_000}
    prune_seen(ledger)
    ledger["rows"] = dedupe_rows(ledger.get("rows", []))[-wc["keep_rows"]:]
    ledger.pop("_seen_set", None)
    extra["koios_calls"] = http.calls.get("koios", 0) - kl.start
    extra["blockfrost_fallbacks"] = kl.fallbacks
    extra["blockfrost_calls"] = http.calls.get("blockfrost", 0) - kl.bf_start
    extra["blockfrost_available"] = bool(kl.bf_key)
    extra["basis"] = ("mixed" if extra["koios_calls"] else "blockfrost") if extra["blockfrost_calls"] else "koios"
    doc = public_doc(ledger, tag(stake), now_s, wc, extra)
    # private state -> Actions cache only (tx hashes, raw positions, order refs; no addresses). Rows are not duplicated
    # there: the public file holds them. Without a cache dir (local runs) nothing private is written at all.
    state = F.assert_public({k: v for k, v in ledger.items() if k != "rows"})
    if not dry_run:
        if cpath:
            KT.write_json(cpath, state, indent=1)
        KT.write_json(path, doc, indent=1)
    return {k: doc.get(k) for k in ("capped", "koios_calls", "blockfrost_fallbacks", "blockfrost_calls", "ada_balance", "basis")} | {
        "rows": len(doc["rows"]), "open_orders": sum(1 for o in doc["orders"].values() if o["status"] == "open"),
        "reconcile_ok": (extra.get("reconcile") or {}).get("ok"), "stale": bool(extra.get("stale"))}
