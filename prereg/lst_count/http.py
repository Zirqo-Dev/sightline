"""Keyless HTTP. Sanctum sol-value is refused. Redirects are refused."""

import json
import socket
import urllib.error
import urllib.request

RPC_URL = "https://api.mainnet-beta.solana.com"
ALLOWED_HOSTS = frozenset({
    "api.mainnet-beta.solana.com",
    "lite-api.jup.ag",
    "api.geckoterminal.com",
})
BACKOFFS = (20, 60)
USER_AGENT = "sightline-lst-count"


class HTTPResult:
    def __init__(self, status, raw, error):
        self.status = status
        self.raw = raw if raw is not None else b""
        self.error = error

    @property
    def transient(self):
        if self.error in ("timeout", "temporary"):
            return True
        if self.status == 429 or (self.status is not None and self.status >= 500):
            return True
        return False


def host_of(url):
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()


def assert_allowed(url):
    lowered = url.lower()
    if "sol-value" in lowered or "sanctum" in lowered:
        raise SystemExit("banned rate source")
    if host_of(url) not in ALLOWED_HOSTS:
        raise SystemExit("host not allowed")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect", headers, fp)


class Transport:
    def __init__(self, timeout=20):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method, url, body=None, headers=None):
        assert_allowed(url)
        hdrs = {"accept": "application/json", "user-agent": USER_AGENT}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            return HTTPResult(exc.code, raw, None)
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            return HTTPResult(None, b"", "timeout")
        except Exception:
            return HTTPResult(None, b"", "temporary")
        return HTTPResult(status, raw or b"", None)


def with_retries(call, sleep, backoffs=BACKOFFS):
    """One try plus up to two retries. Sleep 20s, then 60s, on transient failures.

    A definitive HTTP 4xx is returned immediately. Timeouts, 429, and 5xx retry.
    """
    attempts = len(backoffs) + 1
    last = HTTPResult(None, b"", "temporary")
    for attempt in range(attempts):
        try:
            last = call()
        except SystemExit:
            raise
        except Exception:
            last = HTTPResult(None, b"", "temporary")
        if not last.transient:
            return last
        if attempt < len(backoffs):
            sleep(backoffs[attempt])
    return last


def rpc_body(method, params):
    return json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }).encode()


def rpc_result(result):
    if result.status != 200 or not result.raw:
        return None
    try:
        body = json.loads(result.raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict) or body.get("error"):
        return None
    return body.get("result")
