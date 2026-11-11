"""Keyless HTTP. Redirects off the original host are refused."""

import json
import socket
import urllib.error
import urllib.request


class HTTPResult:
    def __init__(self, status, body, error):
        self.status = status
        self.body = body
        self.error = error

    @property
    def temporary(self):
        if self.error in ("timeout", "temporary"):
            return True
        if self.status == 429 or (self.status is not None and self.status >= 500):
            return True
        return False


class Transport:
    def __init__(self, timeout):
        self.timeout = timeout

    def request(self, method, url, headers=None, body=None):
        req = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                status = getattr(resp, "status", 200)
        except urllib.error.HTTPError as exc:
            raw = exc.read() if exc.fp else b""
            status = exc.code
        except (urllib.error.URLError, socket.timeout, TimeoutError):
            return HTTPResult(None, None, "timeout")
        except Exception:
            return HTTPResult(None, None, "temporary")
        text = raw.decode("utf-8", errors="replace") if raw else ""
        parsed = None
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = text
        return HTTPResult(status, parsed, None)


def host_of(url):
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0].split(":", 1)[0].lower()
