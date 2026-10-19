"""Standard Solana JSON-RPC client with a call budget, a rate limit, and backoff.

The endpoint URL is read by the caller from SOLANA_RPC_URL. This module never
prints it. Only the methods the free tier allows are sent.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

ALLOWED_METHODS = frozenset(
    {
        "getSignaturesForAddress",
        "getTransaction",
        "getTokenSupply",
        "getTokenLargestAccounts",
        "getAccountInfo",
        "getTokenAccountsByOwner",
    }
)

# Public mainnet-beta. Used only when SOLANA_RPC_URL is unset. Not a secret.
PUBLIC_RPC_URL = "https://api.mainnet-beta.solana.com"

# Retried: HTTP 429 / 5xx and the usual RPC rate-limit codes.
RETRYABLE_RPC_CODES = frozenset({-32429, 429, -32005})


class BudgetExceeded(Exception):
    def __init__(self, used, budget):
        super().__init__(f"call budget reached ({used}/{budget})")
        self.used = used
        self.budget = budget


class RpcRetriesExhausted(Exception):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class RpcMethodError(Exception):
    def __init__(self, code, message):
        super().__init__(f"RPC {code}")
        self.code = code
        self.rpc_message = message


def clean_rpc_url(url):
    """Drop wrapping quotes and whitespace. Never log the result: it may hold a key."""
    text = str(url or "").strip().lstrip("\ufeff").strip().strip('"').strip("'").strip()
    if not text or "://" not in text:
        return text
    parts = urllib.parse.urlsplit(text)
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    cleaned = []
    for key, value in pairs:
        if key.lower() in {"api-key", "apikey"}:
            value = value.strip().strip('"').strip("'").strip("\u201c\u201d").strip()
        cleaned.append((key, value))
    query = urllib.parse.urlencode(cleaned, safe="-")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def transaction_config(commitment="confirmed"):
    """jsonParsed, and version 1 so v0 (lookup tables) and v1 txs both decode.

    maxSupportedTransactionVersion 0 makes a node reject version-1 transactions
    with -32015. v1 messages carry transactionConfig and no addressTableLookups;
    v0 messages carry addressTableLookups. jsonParsed still returns resolved keys.
    """
    return {
        "encoding": "jsonParsed",
        "maxSupportedTransactionVersion": 1,
        "commitment": commitment,
    }


class RpcClient:
    def __init__(
        self,
        url,
        *,
        budget=5000,
        max_rps=8.0,
        max_retries=5,
        timeout=60,
        commitment="confirmed",
        transport=None,
        now=None,
        sleep=None,
    ):
        if not url:
            raise ValueError("RPC URL is empty")
        if max_rps <= 0:
            raise ValueError("max_rps must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self._url = url
        self.budget = int(budget)
        self.max_retries = int(max_retries)
        self.timeout = timeout
        self.commitment = commitment
        self.min_interval = 1.0 / float(max_rps)
        self.transport = transport
        self._now = now or time.time
        self._sleep = sleep or time.sleep
        self.calls = Counter()
        self._next = 0.0

    @property
    def used(self):
        return int(sum(self.calls.values()))

    def redact(self, text):
        s = str(text)
        if self._url:
            s = s.replace(self._url, "<rpc>")
        for mark in ("api-key=", "apikey="):
            s = _redact_query(s, mark)
        return s

    def call(self, method, params):
        if method not in ALLOWED_METHODS:
            raise ValueError(f"RPC method not allowed: {method}")
        if self.used >= self.budget:
            raise BudgetExceeded(self.used, self.budget)
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        ).encode()
        last = "unknown"
        attempts = self.max_retries + 1
        for attempt in range(attempts):
            if self.used >= self.budget:
                raise BudgetExceeded(self.used, self.budget)
            self._pace()
            self.calls[method] += 1
            try:
                raw = self._send(body)
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code == 429 or e.code >= 500:
                    self._backoff(attempt, _retry_after(e))
                    _drain(e)
                    continue
                raise RpcMethodError(e.code, last) from None
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last = type(e).__name__
                self._backoff(attempt)
                continue
            try:
                data = json.loads(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
            except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                last = "bad JSON"
                self._backoff(attempt)
                continue
            if isinstance(data, dict) and "error" in data:
                err = data.get("error") or {}
                code = err.get("code")
                message = str(err.get("message") or "")
                if code in RETRYABLE_RPC_CODES or "rate" in message.lower():
                    last = f"RPC {code}"
                    self._backoff(attempt)
                    continue
                raise RpcMethodError(code, message)
            if isinstance(data, dict):
                return data.get("result")
            last = "bad JSON"
            self._backoff(attempt)
        raise RpcRetriesExhausted(self.redact(last))

    def _send(self, body):
        if self.transport is not None:
            return self.transport(body)
        req = urllib.request.Request(
            self._url, data=body, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read()

    def _pace(self):
        wait = self._next - self._now()
        if wait > 0:
            self._sleep(wait)
        self._next = self._now() + self.min_interval

    def _backoff(self, attempt, retry_after=None):
        delay = min(2**attempt, 20)
        if retry_after:
            # Honor Retry-After, but don't let a hostile value stall the run.
            delay = max(delay, min(float(retry_after), 30))
        self._sleep(delay)
        # Stay slower after a 429 or 5xx. Public mainnet keeps rejecting a
        # client that returns to 8 requests/second. Cap at one request per 5s.
        # The configured max_rps is still a ceiling, never a floor.
        self.min_interval = min(max(self.min_interval * 2, 1.0), 5.0)


def _retry_after(exc):
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    raw = None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _drain(exc):
    try:
        exc.read(256)
    except Exception:
        return


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
