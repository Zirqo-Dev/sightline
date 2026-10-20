"""Pure parsers for jsonParsed transactions.

Handles legacy, v0 (address lookup tables, already resolved into accountKeys)
and v1 (transactionConfig, no addressTableLookups). Balance deltas are only a
fallback: a swap's largest lamport move is often not the funder.
"""

from __future__ import annotations

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

# Prefer a parsed SOL transfer over a stablecoin only when the SOL leg is
# clearly a funding amount. A dust SOL top-up next to a USDC transfer is not.
SOL_OVER_STABLE_LAMPORTS = 10_000_000

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
PUMP_AMM_PROGRAM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
PROTOCOL_PROGRAMS = frozenset({PUMP_PROGRAM, PUMP_AMM_PROGRAM})
TOKEN_PROGRAMS = frozenset({TOKEN_PROGRAM, TOKEN_2022_PROGRAM})


def account_keys(tx):
    """Pubkeys in message order. Lookup-table keys are already inline when parsed."""
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    out = []
    for key in msg.get("accountKeys") or []:
        if isinstance(key, str):
            out.append(key)
        elif isinstance(key, dict) and key.get("pubkey"):
            out.append(key["pubkey"])
    return out


def fee_payer(tx):
    keys = account_keys(tx)
    return keys[0] if keys else None


def message_view(tx):
    """Facts the version tests care about. Parsing does not branch on these."""
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    return {
        "version": (tx or {}).get("version", "legacy"),
        "address_table_lookups": bool(msg.get("addressTableLookups")),
        "transaction_config": "transactionConfig" in msg,
    }


def iter_instructions(tx):
    msg = ((tx or {}).get("transaction") or {}).get("message") or {}
    for ix in msg.get("instructions") or []:
        yield ix
    for inner in ((tx or {}).get("meta") or {}).get("innerInstructions") or []:
        for ix in inner.get("instructions") or []:
            yield ix


def token_deltas(tx, mint):
    """Owner -> raw amount change for one mint. Missing owners are counted and skipped.

    Works for SPL Token and Token-2022: jsonParsed balances carry owner and mint
    either way. Positive means the owner received tokens.
    """
    meta = (tx or {}).get("meta") or {}
    deltas = {}
    missing_owner = 0
    for side, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for bal in meta.get(side) or []:
            if bal.get("mint") != mint:
                continue
            owner = bal.get("owner")
            if not owner:
                missing_owner += 1
                continue
            raw = ((bal.get("uiTokenAmount") or {}).get("amount")) or "0"
            try:
                amount = int(raw)
            except (TypeError, ValueError):
                continue
            deltas[owner] = deltas.get(owner, 0) + sign * amount
    return deltas, missing_owner


def token_account_owners(tx):
    """Token-account pubkey -> owner, from pre/post balances (post wins when set)."""
    keys = account_keys(tx)
    owners = {}
    meta = (tx or {}).get("meta") or {}
    for side in ("preTokenBalances", "postTokenBalances"):
        for bal in meta.get(side) or []:
            owner = bal.get("owner")
            idx = bal.get("accountIndex")
            if not owner or idx is None or idx >= len(keys):
                continue
            owners[keys[idx]] = owner
    return owners


def _parsed_info(ix):
    parsed = ix.get("parsed")
    if not isinstance(parsed, dict):
        return None, None, None
    return ix.get("program"), parsed.get("type"), parsed.get("info") or {}


def _spl_amount(info):
    token_amount = info.get("tokenAmount")
    if isinstance(token_amount, dict) and token_amount.get("amount") is not None:
        return int(token_amount["amount"])
    if info.get("amount") is not None:
        return int(info["amount"])
    return None


def protocol_accounts_created(tx):
    """Accounts created in this tx and owned by the pump program or pump AMM.

    The bonding-curve PDA is one of these. It is not a buyer.
    """
    found = set()
    for ix in iter_instructions(tx):
        program, typ, info = _parsed_info(ix)
        if program == "system" and typ == "createAccount" and info.get("owner") in PROTOCOL_PROGRAMS:
            if info.get("newAccount"):
                found.add(info["newAccount"])
    return found


def iter_value_transfers(tx, mint, stables):
    """Owner-level SOL, target-mint, and stablecoin transfers.

    `stables` maps mint -> symbol. Token-account addresses are resolved to owners
    with the balances in this transaction. Unknown owners are skipped.
    """
    stables = stables or {}
    owners = token_account_owners(tx)
    for ix in iter_instructions(tx):
        program, typ, info = _parsed_info(ix)
        if program == "system" and typ in ("transfer", "transferWithSeed"):
            src, dst = info.get("source"), info.get("destination")
            if src and dst and src != dst and info.get("lamports") is not None:
                yield {
                    "src": src,
                    "dst": dst,
                    "asset": "SOL",
                    "mint": None,
                    "amount_raw": int(info["lamports"]),
                    "kind": f"system.{typ}",
                }
        if program == "spl-token" and typ in ("transfer", "transferChecked"):
            mint_id = info.get("mint")
            src_owner = owners.get(info.get("source")) or info.get("authority")
            dst_owner = owners.get(info.get("destination"))
            if not src_owner or not dst_owner or src_owner == dst_owner:
                continue
            if mint_id != mint and mint_id not in stables:
                continue
            amount = _spl_amount(info)
            if amount is None:
                continue
            asset = "MINT" if mint_id == mint else stables.get(mint_id, "SPL")
            yield {
                "src": src_owner,
                "dst": dst_owner,
                "asset": asset,
                "mint": mint_id,
                "amount_raw": amount,
                "kind": f"spl-token.{typ}",
            }


def _lamport_deltas(tx):
    keys = account_keys(tx)
    meta = (tx or {}).get("meta") or {}
    pre = meta.get("preBalances") or []
    post = meta.get("postBalances") or []
    fee = int(meta.get("fee") or 0)
    out = {}
    for i, key in enumerate(keys):
        if i >= len(pre) or i >= len(post):
            break
        delta = int(post[i]) - int(pre[i])
        if i == 0:
            delta += fee
        out[key] = out.get(key, 0) + delta
    return out


def find_incoming(tx, wallet, stables=None):
    """First incoming SOL or stablecoin funding of `wallet`, or None.

    Order: a meaningful parsed SOL transfer, else a parsed stablecoin transfer,
    else any parsed SOL transfer (including dust), else createAccount of the
    wallet itself, else lamport / stablecoin balance deltas.
    """
    if not tx or not wallet or (tx.get("meta") or {}).get("err"):
        return None
    stables = dict(stables or {USDC_MINT: "USDC"})
    sol_hits = []
    stable_hits = []
    created = []
    for ix in iter_instructions(tx):
        program, typ, info = _parsed_info(ix)
        if program == "system" and typ in ("transfer", "transferWithSeed"):
            if info.get("destination") == wallet and info.get("source") not in (None, wallet):
                if info.get("lamports") is None:
                    continue
                sol_hits.append(
                    {
                        "funder": info["source"],
                        "amount_raw": int(info["lamports"]),
                        "asset": "SOL",
                        "mint": None,
                        "kind": f"system.{typ}",
                    }
                )
        elif program == "system" and typ in ("createAccount", "createAccountWithSeed"):
            if info.get("newAccount") == wallet and info.get("source") not in (None, wallet):
                created.append(
                    {
                        "funder": info["source"],
                        "amount_raw": int(info.get("lamports") or 0),
                        "asset": "SOL",
                        "mint": None,
                        "kind": f"system.{typ}",
                    }
                )
        elif program == "spl-token" and typ in ("transfer", "transferChecked"):
            mint_id = info.get("mint")
            if mint_id not in stables:
                continue
            owners = token_account_owners(tx)
            dst_owner = owners.get(info.get("destination"))
            if dst_owner != wallet:
                continue
            src_owner = owners.get(info.get("source")) or info.get("authority")
            if not src_owner or src_owner == wallet:
                continue
            amount = _spl_amount(info)
            if amount is None or amount <= 0:
                continue
            stable_hits.append(
                {
                    "funder": src_owner,
                    "amount_raw": amount,
                    "asset": stables[mint_id],
                    "mint": mint_id,
                    "kind": f"spl-token.{typ}",
                }
            )
    if sol_hits:
        best = max(sol_hits, key=lambda h: h["amount_raw"])
        if best["amount_raw"] >= SOL_OVER_STABLE_LAMPORTS or not stable_hits:
            return best
    if stable_hits:
        return max(stable_hits, key=lambda h: h["amount_raw"])
    if sol_hits:
        return max(sol_hits, key=lambda h: h["amount_raw"])
    if created:
        return max(created, key=lambda h: h["amount_raw"])
    lamports = _lamport_deltas(tx)
    incoming = lamports.get(wallet, 0)
    if incoming > 0:
        losers = [(k, v) for k, v in lamports.items() if k != wallet and v < 0]
        if losers:
            funder = min(losers, key=lambda kv: kv[1])[0]
            return {
                "funder": funder,
                "amount_raw": incoming,
                "asset": "SOL",
                "mint": None,
                "kind": "balance-diff",
            }
    for mint_id, symbol in stables.items():
        deltas, _missing = token_deltas(tx, mint_id)
        got = deltas.get(wallet, 0)
        if got <= 0:
            continue
        losers = [(k, v) for k, v in deltas.items() if k != wallet and v < 0]
        if not losers:
            continue
        funder = min(losers, key=lambda kv: kv[1])[0]
        return {
            "funder": funder,
            "amount_raw": got,
            "asset": symbol,
            "mint": mint_id,
            "kind": "spl-balance-diff",
        }
    return None
