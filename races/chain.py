"""Public Solana RPC for mint authority and holder checks. 429 skips."""

import json

from races.b58 import b58decode, b58encode, find_pda
from races.http import host_of

KNOWN_PROGRAMS_EXTRA = set()


class Chain:
    def __init__(self, cfg, transport, log):
        self.cfg = cfg
        self.transport = transport
        self.log = log
        self.rpc_urls = list(cfg["discovery"]["rpc_urls"])
        disc = cfg["discovery"]
        self.programs = {
            disc["pumpswap_program"],
            disc["pumpfun_program"],
            disc["metadata_program"],
            "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
            "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb",
        }

    def _rpc(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = None
        for url in self.rpc_urls:
            if host_of(url) not in ("api.mainnet-beta.solana.com", "solana-rpc.publicnode.com"):
                continue
            result = self.transport.request(
                "POST", url,
                headers={"content-type": "application/json", "user-agent": "sightline-races"},
                body=body,
            )
            last = result
            if result.temporary or result.status == 429:
                self.log("rpc", "decision", {"action": "skip", "reason": "429" if result.status == 429 else "timeout", "method": method})
                return "temporary", None
            if result.status == 200 and isinstance(result.body, dict):
                return "ok", result.body.get("result")
        return "error", last

    def mint_auth(self, mint):
        status, result = self._rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        if status == "temporary":
            return None
        value = (result or {}).get("value") if isinstance(result, dict) else None
        info = (((value or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
        if not info:
            return {"ok": False}
        mint_auth = info.get("mintAuthority")
        freeze_auth = info.get("freezeAuthority")
        return {
            "ok": True,
            "revoked": mint_auth in (None, "") and freeze_auth in (None, ""),
            "decimals": int(info.get("decimals") or 0),
            "supply_raw": int(info.get("supply") or 0),
        }

    def supply(self, mint):
        status, result = self._rpc("getTokenSupply", [mint])
        if status != "ok" or not isinstance(result, dict):
            return None
        value = result.get("value") or {}
        try:
            return int(value.get("amount"))
        except (TypeError, ValueError):
            return None

    def largest(self, mint):
        status, result = self._rpc("getTokenLargestAccounts", [mint])
        if status == "temporary":
            return "temporary"
        if status != "ok" or not isinstance(result, dict):
            return None
        return list((result.get("value") or [])[:20])

    def accounts(self, addresses):
        if not addresses:
            return {}
        status, result = self._rpc("getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}])
        if status == "temporary":
            return "temporary"
        if status != "ok" or not isinstance(result, dict):
            return None
        out = {}
        for addr, value in zip(addresses, result.get("value") or []):
            info = (((value or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            owner = info.get("owner")
            amount = ((info.get("tokenAmount") or {}).get("amount"))
            out[addr] = {"owner": owner, "amount": int(amount) if str(amount or "").isdigit() else 0}
        return out

    def account_owner_program(self, address):
        status, result = self._rpc("getAccountInfo", [address, {"encoding": "jsonParsed"}])
        if status != "ok" or not isinstance(result, dict):
            return None
        value = result.get("value") or {}
        return value.get("owner")

    def token_balance(self, owner, mint):
        """Sum of token accounts. `owner` is never logged."""
        status, result = self._rpc("getTokenAccountsByOwner", [
            owner, {"mint": mint}, {"encoding": "jsonParsed"}
        ])
        if status == "temporary":
            return "temporary"
        if status != "ok" or not isinstance(result, dict):
            return None
        total = 0
        for row in result.get("value") or []:
            info = (((row.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info") or {}
            amount = ((info.get("tokenAmount") or {}).get("amount"))
            if str(amount or "").isdigit():
                total += int(amount)
        return total

    def metadata_update_authority(self, mint):
        program = b58decode(self.cfg["discovery"]["metadata_program"])
        mint_b = b58decode(mint)
        pda = find_pda([b"metadata", program, mint_b], program)
        pda_b58 = b58encode(pda)
        status, result = self._rpc("getAccountInfo", [pda_b58, {"encoding": "base64"}])
        if status != "ok" or not isinstance(result, dict):
            return None
        value = result.get("value")
        if not value:
            return None
        data = value.get("data")
        if not (isinstance(data, list) and data):
            return None
        import base64
        raw = base64.b64decode(data[0])
        if len(raw) < 1 + 32:
            return None
        authority = raw[1:33]
        if authority == b"\x00" * 32:
            return None
        text = b58encode(authority)
        if text in self.programs:
            return None
        return text

    def wsol_vault_balance(self, pool_id, quote_is_wsol=True):
        """PumpSwap pool layout. A failed parse returns None (skip, do not guess)."""
        status, result = self._rpc("getAccountInfo", [pool_id, {"encoding": "base64"}])
        if status != "ok" or not isinstance(result, dict) or not result.get("value"):
            return None
        import base64
        data = result["value"].get("data")
        if not (isinstance(data, list) and data):
            return None
        raw = base64.b64decode(data[0])
        disc = self.cfg["discovery"]
        off = int(disc["pool_quote_vault_offset"] if quote_is_wsol else disc["pool_base_vault_offset"])
        if len(raw) < off + 32:
            return None
        vault = b58encode(raw[off:off + 32])
        status, bal = self._rpc("getTokenAccountBalance", [vault])
        if status != "ok" or not isinstance(bal, dict):
            return None
        ui = (bal.get("value") or {}).get("uiAmount")
        try:
            return float(ui)
        except (TypeError, ValueError):
            return None


def top10_and_dev(chain, mint, pool_id, creator, pool_owner_program):
    """Return dict with top10_pct and dev_pct, or a skip reason. No addresses."""
    supply = chain.supply(mint)
    if supply == "temporary":
        return {"skip": "429"}
    if not supply:
        return {"skip": "supply"}
    largest = chain.largest(mint)
    if largest == "temporary":
        return {"skip": "429"}
    if not largest:
        return {"skip": "holders"}
    parsed = chain.accounts([row.get("address") for row in largest if row.get("address")])
    if parsed == "temporary":
        return {"skip": "429"}
    if parsed is None:
        return {"skip": "holders"}
    programs = set(chain.programs)
    if pool_owner_program:
        programs.add(pool_owner_program)
    kept = []
    for row in largest:
        addr = row.get("address")
        info = parsed.get(addr) or {}
        owner = info.get("owner")
        amount = info.get("amount")
        if amount is None:
            try:
                amount = int(row.get("amount") or 0)
            except (TypeError, ValueError):
                amount = 0
        if addr == pool_id or owner == pool_id or owner in programs or owner == mint:
            continue
        kept.append(int(amount))
    top = kept[:10]
    top10 = sum(top) / supply if supply else None
    dev_pct = None
    if creator:
        if creator == pool_id:
            dev_pct = 0.0
        else:
            bal = chain.token_balance(creator, mint)
            if bal == "temporary":
                return {"skip": "429"}
            if bal is None:
                return {"skip": "dev"}
            dev_pct = bal / supply
    else:
        return {"skip": "dev"}
    return {"top10_pct": top10, "dev_pct": dev_pct, "skip": None}
