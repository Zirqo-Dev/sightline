"""Prove SOLANA_RPC_URL answers, without printing it.

This is a one-shot Actions check. getSlot is used only here. The analysis
client still rejects it. The daily schedule does not run this module.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from sol_forensics.rpc import clean_rpc_url

QBITS_MINT = "Ceuu7uQHwhtHuMhPWrWL9dgri3FaGJZFkTy6FoDow7NA"


def redact(text, url):
    s = str(text)
    if url:
        s = s.replace(url, "<rpc>")
    for mark in ("api-key=", "apikey="):
        s = _redact_query(s, mark)
    return s


def _redact_query(text, mark):
    out = []
    i = 0
    low = text.lower()
    mlow = mark.lower()
    while True:
        j = low.find(mlow, i)
        if j < 0:
            out.append(text[i:])
            break
        out.append(text[i:j])
        out.append(mark)
        out.append("<redacted>")
        k = j + len(mark)
        while k < len(text) and text[k] not in "& \n\r\t":
            k += 1
        i = k
    return "".join(out)


def endpoint_shape(url):
    """Facts about the endpoint that do not include the key or the raw URL."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return "scheme=no host=unparsed api_key_param=no"
    host = (parts.hostname or "").lower()
    known = host in {"mainnet.helius-rpc.com", "beta.helius-rpc.com", "devnet.helius-rpc.com"}
    if known:
        host_label = host
    elif host.endswith(".helius-rpc.com"):
        host_label = "*.helius-rpc.com"
    elif host:
        host_label = "other"
    else:
        host_label = "missing"
    query = urllib.parse.parse_qs(parts.query)
    key = (query.get("api-key") or query.get("apikey") or [""])[0]
    kind = "uuid" if re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        key,
    ) else "other"
    return (
        f"scheme={parts.scheme or 'missing'} host={host_label} "
        f"api_key_param={'yes' if key else 'no'} api_key_len={len(key)} api_key_form={kind}"
    )


def normalize_url(url):
    return clean_rpc_url(url)


def rpc_call(url, method, params, transport=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    try:
        if transport is not None:
            raw = transport(body)
        else:
            req = urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read()
        data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", "replace")
        except Exception:
            detail = ""
        raise RuntimeError(redact(f"HTTP {exc.code} {detail}".strip(), url)) from None
    except Exception as exc:
        raise RuntimeError(redact(exc, url)) from None
    if not isinstance(data, dict):
        raise RuntimeError("RPC response was not an object")
    if data.get("error"):
        err = data["error"] or {}
        raise RuntimeError(redact(f"RPC {err.get('code')} {err.get('message')}", url))
    if "result" not in data:
        raise RuntimeError("RPC response had no result")
    return data["result"]


def smoke(env=None, transport=None):
    """Return 0 after printing the slot and the QBITS supply. Never print the URL."""
    env = os.environ if env is None else env
    url = normalize_url(env.get("SOLANA_RPC_URL") or "")
    if not url:
        print("SOLANA_RPC_URL secret is empty", file=sys.stderr)
        return 1
    print("smoke: " + endpoint_shape(url), flush=True)
    try:
        slot = rpc_call(url, "getSlot", [{"commitment": "confirmed"}], transport)
        supply = rpc_call(
            url,
            "getTokenSupply",
            [QBITS_MINT, {"commitment": "confirmed"}],
            transport,
        )
    except Exception as exc:
        print("smoke: fail " + redact(exc, url), file=sys.stderr)
        return 1
    value = supply.get("value") if isinstance(supply, dict) else None
    amount = value.get("amount") if isinstance(value, dict) else None
    if amount is None:
        print("smoke: fail getTokenSupply returned no amount", file=sys.stderr)
        return 1
    print("smoke: pass")
    print(f"slot: {slot}")
    print(f"qbits_supply_amount: {amount}")
    print(f"qbits_supply_decimals: {value.get('decimals')}")
    print(f"qbits_supply_ui: {value.get('uiAmountString')}")
    return 0


def main():
    return smoke()


if __name__ == "__main__":
    sys.exit(main())
