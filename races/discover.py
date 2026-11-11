"""GeckoTerminal graduates plus a DexScreener reserve check. No USD conversion."""

import time
import urllib.parse

from races.http import host_of

WSOL = "So11111111111111111111111111111111111111112"


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class Discovery:
    def __init__(self, cfg, transport, chain, log):
        self.cfg = cfg
        self.transport = transport
        self.chain = chain
        self.log = log
        self.disc = cfg["discovery"]
        self.coverage = {
            "pages": {},
            "http_failures": 0,
            "oldest": {},
            "window": 0,
            "dropped_reserve": 0,
            "rate_limits": 0,
        }

    def _get(self, url, headers):
        if host_of(url) not in ("api.geckoterminal.com", "api.dexscreener.com", "api.rugcheck.xyz"):
            return None
        return self.transport.request("GET", url, headers=headers)

    def _gecko(self, url, label):
        interval = float(self.cfg.get("gecko_interval_seconds") or 0)
        if interval and self.coverage["pages"]:
            time.sleep(interval)
        result = self._get(url, {"accept": self.disc["gecko_accept"], "user-agent": "sightline-races"})
        self.coverage["pages"][label] = None if result is None else result.status
        if result is None or result.temporary or result.status == 429:
            self.coverage["rate_limits"] += 1
            self.coverage["http_failures"] += 1
            return []
        if result.status != 200 or not isinstance(result.body, dict):
            self.coverage["http_failures"] += 1
            return []
        return self._rows(result.body, label)

    def _rows(self, body, label):
        included = {}
        for item in body.get("included") or []:
            included[item.get("id")] = item
        rows = []
        oldest = None
        for pool in body.get("data") or []:
            attrs = pool.get("attributes") or {}
            created = attrs.get("pool_created_at")
            if created and (oldest is None or created < oldest):
                oldest = created
            rel = pool.get("relationships") or {}
            dex = ((rel.get("dex") or {}).get("data") or {}).get("id") or attrs.get("dex_id") or ""
            if label.startswith("new") and dex not in self.disc["eligible_dex_ids"]:
                continue
            base_id = ((rel.get("base_token") or {}).get("data") or {}).get("id")
            quote_id = ((rel.get("quote_token") or {}).get("data") or {}).get("id")
            base = (included.get(base_id) or {}).get("attributes") or {}
            quote = (included.get(quote_id) or {}).get("attributes") or {}
            mint = base.get("address")
            quote_mint = quote.get("address")
            if not mint or quote_mint != WSOL:
                continue
            sells = ((attrs.get("transactions") or {}).get("h1") or {}).get("sells")
            rows.append({
                "pool_id": attrs.get("address") or (pool.get("id") or "").split("_", 1)[-1],
                "mint": mint,
                "symbol": (base.get("symbol") or "TOKEN")[:16],
                "dex": dex or label.split(":", 1)[0],
                "pool_created_at": created,
                "sells_h1": int(sells) if isinstance(sells, int) else None,
                "creator": _one_creator(attrs) or _one_creator(base),
            })
        if oldest:
            prev = self.coverage["oldest"].get(label)
            if prev is None or oldest < prev:
                self.coverage["oldest"][label] = oldest
        return rows

    def gecko_candidates(self):
        found = []
        for dex, key in (("pumpswap", "gecko_pumpswap"), ("letsbonk-fun", "gecko_letsbonk")):
            for page in range(1, int(self.disc["dex_pages_per_list"]) + 1):
                url = self.disc[key] + "?" + urllib.parse.urlencode({
                    "page": page, "include": "base_token,quote_token",
                })
                found.extend(self._gecko(url, f"{dex}:p{page}"))
        for page in range(1, int(self.disc["new_pool_pages"]) + 1):
            url = self.disc["gecko_new_pools"] + "?" + urllib.parse.urlencode({
                "page": page, "include": "base_token,quote_token,dex",
            })
            found.extend(self._gecko(url, f"new:p{page}"))
        return found

    def confirm(self, rows, slot_epoch):
        """DexScreener batches of at most 30, then RPC reserve if needed."""
        by_pool = {}
        for row in rows:
            if row.get("pool_id") and row.get("mint"):
                by_pool[row["pool_id"]] = row
        pools = list(by_pool)
        confirmed = []
        batch = int(self.disc["dex_batch_max"])
        for i in range(0, len(pools), batch):
            chunk = pools[i:i + batch]
            url = self.disc["dex_batch_url"] + ",".join(chunk)
            result = self._get(url, {"user-agent": "sightline-races", "accept": "application/json"})
            pairs = {}
            if result and result.status == 200 and isinstance(result.body, dict):
                for pair in result.body.get("pairs") or []:
                    if isinstance(pair, dict) and pair.get("pairAddress"):
                        pairs[pair["pairAddress"]] = pair
            elif result is None or (result and (result.status == 429 or result.temporary)):
                self.coverage["rate_limits"] += 1
            for pool_id in chunk:
                row = dict(by_pool[pool_id])
                pair = pairs.get(pool_id)
                if pair is None and result and result.status == 404:
                    continue
                if pair is not None:
                    if not _pair_is_wsol(pair):
                        self.log("discover", "decision", {"action": "drop", "reason": "quote_mint", "pool_id": pool_id})
                        continue
                    reserve = _sol_reserve(pair)
                    sells = ((pair.get("txns") or {}).get("h1") or {}).get("sells")
                    if isinstance(sells, int):
                        row["sells_h1"] = sells
                    created = pair.get("pairCreatedAt")
                    row["reserve_sol"] = reserve
                    row["reserve_source"] = "dex" if reserve is not None else None
                    if row.get("creator") is None:
                        row["creator"] = _one_creator(pair)
                else:
                    row["reserve_sol"] = None
                    row["reserve_source"] = None
                if row.get("reserve_sol") is None:
                    vault = self.chain.wsol_vault_balance(pool_id, True)
                    if vault is not None:
                        row["reserve_sol"] = vault
                        row["reserve_source"] = "rpc"
                if row.get("reserve_sol") is None:
                    self.coverage["dropped_reserve"] += 1
                    self.log("discover", "decision", {"action": "drop", "reason": "reserve", "pool_id": pool_id, "mint": row["mint"]})
                    continue
                if row["reserve_sol"] < float(self.disc["min_sol_reserve"]):
                    continue
                age = _age(row.get("pool_created_at"), slot_epoch)
                if age is None:
                    continue
                if not (float(self.disc["min_age_seconds"]) <= age <= float(self.disc["max_age_seconds"])):
                    continue
                if row.get("dex") not in self.disc["eligible_dex_ids"] and not str(row.get("dex") or "").startswith("new"):
                    # new_pools rows already filtered; dex list rows are the dex itself
                    if row.get("dex") not in self.disc["eligible_dex_ids"]:
                        continue
                confirmed.append(row)
        self.coverage["window"] = len({r["mint"] for r in confirmed})
        return _unique_mints(confirmed)

    def rugcheck(self, mint):
        """One call for the drawn mint. Score only. Not a filter."""
        url = self.disc["rugcheck_url"] + mint + "/report"
        result = self._get(url, {"user-agent": "sightline-races", "accept": "application/json"})
        if result is None or result.status != 200 or not isinstance(result.body, dict):
            self.log("R1", "decision", {"action": "rugcheck", "mint": mint, "ok": False})
            return
        score = result.body.get("score_normalised")
        if score is None:
            score = result.body.get("score")
        self.log("R1", "decision", {"action": "rugcheck", "mint": mint, "ok": True, "score_normalised": score})


def _one_creator(node):
    if not isinstance(node, dict):
        return None
    for key in ("creator", "deployer", "dev"):
        value = node.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            addr = value.get("address") or value.get("id")
            if isinstance(addr, str):
                return addr
    return None


def _pair_is_wsol(pair):
    base = (pair.get("baseToken") or {}).get("address")
    quote = (pair.get("quoteToken") or {}).get("address")
    return base == WSOL or quote == WSOL


def _sol_reserve(pair):
    liq = pair.get("liquidity") or {}
    base = (pair.get("baseToken") or {}).get("address")
    quote = (pair.get("quoteToken") or {}).get("address")
    if quote == WSOL:
        return _num(liq.get("quote"))
    if base == WSOL:
        return _num(liq.get("base"))
    return None


def _age(created, slot_epoch):
    if not created:
        return None
    from datetime import datetime
    try:
        ts = datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None
    return slot_epoch - ts


def _unique_mints(rows):
    best = {}
    for row in rows:
        prev = best.get(row["mint"])
        if prev is None or (row.get("pool_created_at") or "") < (prev.get("pool_created_at") or ""):
            best[row["mint"]] = row
    return [best[m] for m in sorted(best)]
