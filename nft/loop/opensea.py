"""OpenSea v2 reads for the snapshot pilot. The key is the caller's secret, never logged."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pricing as P


class Budget(Exception):
    pass


class AuthError(Exception):
    pass


class OpenSeaError(Exception):
    def __init__(self, status):
        super().__init__(f"http {status}")
        self.status = status


class OpenSea:
    def __init__(self, key, transport=None, sleep=None, max_calls=P.MAX_CALLS):
        self.key = key
        self.calls = 0
        self.max_calls = max_calls
        self.rate = {"limit": None, "remaining": None, "reset": None}
        self.raw = []
        self._transport = transport or self._http
        self._sleep = sleep or __import__("time").sleep

    def _http(self, path, params):
        query = urllib.parse.urlencode([(k, v) for k, v in params.items() if v is not None], doseq=True)
        url = "https://api.opensea.io" + path + ("?" + query if query else "")
        req = urllib.request.Request(url, headers={
            "x-api-key": self.key,
            "accept": "application/json",
            "user-agent": "sightline-nft-snapshot",
        })
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return res.status, {k: v for k, v in res.headers.items()}, json.load(res)
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            try:
                body = json.loads(raw.decode() or "{}")
            except (ValueError, UnicodeError):
                body = {}
            return exc.code, {k: v for k, v in (exc.headers or {}).items()}, body

    def _note(self, headers):
        wanted = {"x-ratelimit-limit": "limit", "x-ratelimit-remaining": "remaining", "x-ratelimit-reset": "reset"}
        for key, value in headers.items():
            slot = wanted.get(key.lower())
            if slot:
                self.rate[slot] = str(value)

    def request(self, path, params):
        status, headers, body = self._once(path, params)
        if status == 429 and self.calls < self.max_calls:
            try:
                delay = min(int(float(headers.get("Retry-After") or headers.get("retry-after") or 1)), 20)
            except (TypeError, ValueError):
                delay = 1
            self._sleep(delay)
            status, headers, body = self._once(path, params)
        if status == 429:
            raise Budget()
        if status in (401, 403):
            raise AuthError()
        if status >= 400:
            raise OpenSeaError(status)
        return body

    def _once(self, path, params):
        if self.calls >= self.max_calls:
            raise Budget()
        self.calls += 1
        status, headers, body = self._transport(path, params)
        self._note(headers)
        self.raw.append({"path": path, "params": params, "status": status, "body": body})
        return status, headers, body

    def list_collections(self, chain, cursor):
        body = self.request(P.PAGE_ENDPOINT, {
            "chain": chain, "order_by": P.ORDER_BY, "limit": P.PAGE_LIMIT, "next": cursor,
        })
        return body.get("collections") or [], body.get("next")

    def collection(self, slug):
        return self.request("/api/v2/collections/" + urllib.parse.quote(slug, safe=""), {})

    def listings(self, slug):
        body = self.request(
            "/api/v2/listings/collection/" + urllib.parse.quote(slug, safe="") + "/best",
            {"limit": P.LISTING_LIMIT, "include_private_listings": "false"},
        )
        return body.get("listings") or []

    def offers(self, slug, cursor):
        body = self.request(
            "/api/v2/offers/collection/" + urllib.parse.quote(slug, safe=""),
            {"limit": P.OFFER_PAGE_LIMIT, "next": cursor},
        )
        return body.get("offers") or [], body.get("next")

    def events(self, slug, after, cursor):
        body = self.request(
            "/api/v2/events/collection/" + urllib.parse.quote(slug, safe=""),
            {"event_type": "sale", "after": after, "limit": P.EVENT_PAGE_LIMIT, "next": cursor},
        )
        return body.get("asset_events") or [], body.get("next")

    def contract(self, chain, address):
        body = self.request(
            "/api/v2/chain/" + urllib.parse.quote(chain, safe="") + "/contract/" + urllib.parse.quote(address, safe=""),
            {},
        )
        return body.get("contract_standard") or body.get("contractStandard")
