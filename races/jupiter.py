"""lite-api.jup.ag only. Quotes and the R5 token lists. No API key."""

import json
import urllib.parse

from races.http import host_of

HOST = "lite-api.jup.ag"
QUOTE_PATH = "/swap/v1/quote"


class Quote:
    def __init__(self, out_raw, in_raw, impact_pct, input_mint, output_mint):
        self.out_raw = int(out_raw)
        self.in_raw = int(in_raw)
        self.impact_pct = impact_pct
        self.input_mint = input_mint
        self.output_mint = output_mint


class Jupiter:
    def __init__(self, cfg, transport, budget, log):
        self.cfg = cfg
        self.transport = transport
        self.budget = budget
        self.log = log
        self.block_entries = set()
        self.calls = []
        self.last = None

    def _get(self, wallet, url, kind):
        if host_of(url) != HOST:
            self.log(wallet, "decision", {"action": "skip", "reason": "jupiter_host"})
            return None
        if not self.budget.charge(wallet):
            self.block_entries.add(wallet)
            self.last = "cap"
            self.log(wallet, "decision", {"action": "skip", "reason": "cap"})
            return "cap"
        self.calls.append({"kind": kind, "url": url, "wallet": wallet})
        result = self.transport.request(
            "GET", url, headers={"accept": "application/json", "user-agent": "sightline-races"}
        )
        if result is None or result.temporary or result.status == 429:
            self.block_entries.add(wallet)
            reason = "429" if result is not None and result.status == 429 else "timeout"
            if result is not None and result.status is not None and result.status >= 500:
                reason = "http_5xx"
            self.last = reason
            self.log(wallet, "decision", {"action": "skip", "reason": reason, "url_path": urllib.parse.urlparse(url).path})
            return reason
        return result

    def quote(self, wallet, input_mint, output_mint, amount_raw, purpose="quote"):
        base = self.cfg["jupiter"]["base"].rstrip("/")
        if host_of(base) != HOST:
            self.log(wallet, "decision", {"action": "skip", "reason": "jupiter_host"})
            return None
        qs = urllib.parse.urlencode({
            "inputMint": input_mint,
            "outputMint": output_mint,
            "amount": str(int(amount_raw)),
            "slippageBps": str(int(self.cfg["quote_slippage_bps"])),
        })
        url = base + QUOTE_PATH + "?" + qs
        got = self._get(wallet, url, purpose)
        if not hasattr(got, "status"):
            return None
        result = got
        body = result.body if isinstance(result.body, dict) else None
        if result.status != 200 or not body or not str(body.get("outAmount") or "").isdigit():
            self.log(wallet, "quote", {
                "ok": False, "reason": "no_route", "input_mint": input_mint,
                "output_mint": output_mint, "amount": str(int(amount_raw)),
            })
            self.last = "no_route"
            return None
        self.last = "ok"
        impact = body.get("priceImpactPct")
        self.log(wallet, "quote", {
            "ok": True,
            "input_mint": input_mint,
            "output_mint": output_mint,
            "in_amount": str(body.get("inAmount")),
            "out_amount": str(body.get("outAmount")),
            "price_impact_pct": impact,
        })
        return Quote(body["outAmount"], body.get("inAmount") or amount_raw, impact, input_mint, output_mint)

    def token_list(self, wallet, url):
        path = urllib.parse.urlparse(url).path
        if host_of(url) != HOST or "/tokens/v2/" not in path:
            self.log(wallet, "decision", {"action": "skip", "reason": "jupiter_host"})
            return None
        kind = "search" if path.endswith("/search") or "/tokens/v2/search" in path else "universe"
        got = self._get(wallet, url, kind)
        if not hasattr(got, "status"):
            return None
        if got.status != 200 or not isinstance(got.body, list):
            self.log(wallet, "decision", {"action": "skip", "reason": "token_list"})
            return None
        self.log(wallet, "decision", {"action": "token_list", "n": len(got.body), "path": urllib.parse.urlparse(url).path})
        return got.body


def dumps_line(obj):
    return json.dumps(obj, separators=(",", ":"), sort_keys=True)
