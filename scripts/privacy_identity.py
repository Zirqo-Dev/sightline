"""Identity check for the privacy guard.

Public files must not contain the owner name, the Discord account used to read
chats, the lead whale's social handles, or founder wording. Those strings are
not stored here. The guard hashes normalized tokens and word n-grams and
compares them to SHA-256 (or HMAC-SHA256) digests.

Hash source, first hit wins for each digest (they are unioned):
  PRIVACY_IDENTITY_HASHES   newline-separated hex digests
  .privacy-identity-hashes  gitignored file beside the scanned root
  identities/blocked.sha256 in the private repo, fetched with
  SIGHTLINE_PRIVATE_TOKEN (the repo must be private)

When PRIVACY_IDENTITY_KEY is set (at least 16 characters), digests are
HMAC-SHA256 with that key. Otherwise they are plain SHA-256. The stored
digests must use the same mode. Nothing matched is printed.

Normalization and keying are fixed. Existing private digests depend on them:
  Case: the text is lowercased before tokens are taken.
  Whitespace: any character outside [a-z0-9_] is a separator. A 2-word or
  3-word gram joins those tokens with one ASCII space.
  @ prefix: @ is a separator, so a leading @ is not part of the hashed token.
  Salt: none is added. The hashed payload is the gram as UTF-8. The only
  keying material is PRIVACY_IDENTITY_KEY, and only when it is at least 16
  characters. A shorter key is ignored.

An absent, empty, or malformed digest list fails the process. Unset
PRIVACY_IDENTITY_REQUIRE does not turn that into a pass, and setting the
variable does not either. A blank PRIVACY_IDENTITY_HASHES value is ignored
when another source yields digests. Non-digest lines are ignored and do not
drop digest lines. Wallet checks in the shell guard still run.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sys
import urllib.request

_TOKEN = re.compile(r"[a-z0-9_]+")
_HEX = re.compile(r"^[0-9a-f]{64}$")
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SKIP_BASENAMES = {
    ".privacy-identity-hashes",
    ".privacy-patterns",
    "blocked.sha256",
}


def identity_key():
    raw = os.environ.get("PRIVACY_IDENTITY_KEY", "")
    if not isinstance(raw, str):
        return None
    secret = raw.strip()
    if len(secret) < 16:
        return None
    return secret.encode("utf-8")


def digest(text, key=None):
    payload = text.encode("utf-8")
    if key:
        return hmac.new(key, payload, hashlib.sha256).hexdigest()
    return hashlib.sha256(payload).hexdigest()


def grams(text):
    """Tokens, underscore-stripped variants, 2- and 3-word grams, and prefixes.

    Underscores stay inside a token, so a machine key is not the same as the
    two-word phrase it would be if the underscore were a space. A prefix of a
    longer token (length 8 or more) is included so a short handle fragment
    still matches the full token.

    The hashed string is this gram, not the raw span. Case is folded, extra
    whitespace is not kept, and a leading @ is dropped because it is not a
    token character. No salt is added.
    """
    if not isinstance(text, str) or not text:
        return []
    tokens = _TOKEN.findall(text.lower())
    found = []
    seen = set()

    def add(item):
        if item and item not in seen:
            seen.add(item)
            found.append(item)

    for tok in tokens:
        add(tok)
        stripped = tok.strip("_")
        if stripped != tok:
            add(stripped)
        flat = tok.replace("_", "")
        if flat != tok and flat != stripped:
            add(flat)
        base = stripped or tok
        if len(base) > 8:
            for i in range(8, len(base)):
                add(base[:i])
    for n in (2, 3):
        for i in range(len(tokens) - n + 1):
            add(" ".join(tokens[i:i + n]))
    return found


def parse_hashes(text):
    """Valid digests only. Loaders still fail closed on an empty or bad source."""
    out = set()
    if not isinstance(text, str):
        return out
    for line in text.splitlines():
        item = line.strip().lower()
        if _HEX.fullmatch(item):
            out.add(item)
    return out


def classify_hash_text(text):
    """Return (status, hashes, ignored).

    status is ok, empty, or malformed. ignored is how many non-digest lines
    were skipped. A digest line is 64 hex digits. Surrounding whitespace is
    ignored and case is folded, which is how an existing stored digest is
    recognized. Blank lines are ignored and not counted. Any other line is
    ignored too, and it does not remove digest lines. Status is malformed
    only when a non-digest line is present and no digest line remains.
    Status is empty when nothing but blank lines remains.
    """
    if not isinstance(text, str):
        return "malformed", set(), 0
    found = set()
    ignored = 0
    for line in text.splitlines():
        item = line.strip().lower()
        if not item:
            continue
        if _HEX.fullmatch(item):
            found.add(item)
        else:
            ignored += 1
    if found:
        return "ok", found, ignored
    if ignored:
        return "malformed", set(), ignored
    return "empty", set(), 0


def _warn_ignored(count, source):
    """Count only. source is a fixed label, never file text or a private path."""
    if count:
        print(
            "::warning::privacy-guard: %d non-digest line(s) ignored (%s)" % (count, source),
            file=sys.stderr,
        )


def _private_repo():
    repo = os.environ.get("SIGHTLINE_PRIVATE_REPO", "Zirqo-Dev/sightline-private")
    if not isinstance(repo, str):
        return ""
    repo = repo.strip()
    return repo if _REPO.fullmatch(repo) else ""


def _github_json(url, token):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "sightline-privacy",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        import json
        doc = json.loads(resp.read(1_000_000).decode())
    return doc if isinstance(doc, dict) else None


def fetch_private_hashes(token, repo=None):
    """(status, hashes, ignored) from identities/blocked.sha256.

    status is ok, empty, malformed, or unavailable. Refuses a repo the API
    does not mark private. Does not print the token, the URL, or the file body.
    """
    token = (token or "").strip()
    if not token:
        return "unavailable", set()
    repo = repo or _private_repo()
    if not repo:
        return "unavailable", set()
    try:
        meta = _github_json("https://api.github.com/repos/" + repo, token)
    except Exception:
        meta = None
    if not isinstance(meta, dict) or meta.get("private") is not True:
        return "unavailable", set()
    url = "https://api.github.com/repos/%s/contents/identities/blocked.sha256" % repo
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github.raw",
            "User-Agent": "sightline-privacy",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(1_000_000).decode()
    except Exception:
        return "unavailable", set()
    return classify_hash_text(body)


def _problem(status, source):
    if status == "empty":
        return "identity hash list is empty (%s)" % source
    if status == "malformed":
        return "identity hash list is malformed (%s)" % source
    return "identity hash file unavailable (%s)" % source


def load_hashes(root=".", fetch=None):
    """Well-formed digests, plus fail-closed problems. The key is separate.

    A source that yields digest lines contributes those digests. A source
    that yields none contributes a problem. A blank PRIVACY_IDENTITY_HASHES
    value is not a source when another source yields digests: CI leaves that
    secret empty and loads the private file. It is an empty list when it is
    the only source. No source at all is an absent list.
    """
    found = set()
    problems = []
    configured = False
    blank_env = False

    if "PRIVACY_IDENTITY_HASHES" in os.environ:
        raw = os.environ.get("PRIVACY_IDENTITY_HASHES") or ""
        if raw.strip():
            configured = True
            status, hashes, ignored = classify_hash_text(raw)
            if status == "ok":
                found |= hashes
                _warn_ignored(ignored, "PRIVACY_IDENTITY_HASHES")
            else:
                problems.append(_problem(status, "PRIVACY_IDENTITY_HASHES"))
        else:
            blank_env = True

    path = os.path.join(root, ".privacy-identity-hashes")
    if os.path.isfile(path):
        configured = True
        try:
            with open(path, errors="replace") as fh:
                text = fh.read()
        except OSError:
            problems.append(_problem("unavailable", ".privacy-identity-hashes"))
        else:
            status, hashes, ignored = classify_hash_text(text)
            if status == "ok":
                found |= hashes
                _warn_ignored(ignored, ".privacy-identity-hashes")
            else:
                problems.append(_problem(status, ".privacy-identity-hashes"))

    token = os.environ.get("SIGHTLINE_PRIVATE_TOKEN", "")
    token = token.strip() if isinstance(token, str) else ""
    if token:
        configured = True
        remote = fetch(token) if fetch is not None else fetch_private_hashes(token)
        if isinstance(remote, tuple) and len(remote) == 3:
            status, hashes, ignored = remote
        else:
            status, hashes, ignored = "unavailable", set(), 0
        if status == "ok" and isinstance(hashes, set):
            found |= hashes
            _warn_ignored(ignored, "private repo")
        else:
            kind = status if status in ("empty", "malformed", "unavailable") else "unavailable"
            problems.append(_problem(kind, "private repo"))

    if not configured:
        if blank_env:
            problems.append(_problem("empty", "PRIVACY_IDENTITY_HASHES"))
        else:
            problems.append("identity hashes were not loaded")
    return found, problems


def scan_paths(paths, hashes, key):
    """Return {path: match count}. Counts are gram hits, not printed text."""
    hits = {}
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        if os.path.basename(path) in _SKIP_BASENAMES:
            continue
        try:
            with open(path, errors="replace") as fh:
                data = fh.read()
        except OSError:
            continue
        if "\0" in data[:8192]:
            continue
        n = sum(1 for gram in grams(data) if digest(gram, key) in hashes)
        if n:
            hits[path] = n
    return hits


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Scan files for identity n-grams")
    parser.add_argument("--root", default=".")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    paths = list(args.paths)
    if not paths and not sys.stdin.isatty():
        paths.extend(ln.strip("\n") for ln in sys.stdin if ln.strip())
    hashes, problems = load_hashes(args.root)
    # A bad list fails closed. PRIVACY_IDENTITY_REQUIRE is not consulted: unset,
    # 0, or false must not turn this back into a warning.
    key = identity_key()
    hits = scan_paths(paths, hashes, key) if hashes else {}
    for msg in problems:
        print("PRIVACY GUARD FAILED: %s" % msg, file=sys.stderr)
    if hits:
        for path, n in hits.items():
            print("PRIVACY GUARD: %s: %d identity match(es)" % (path, n), file=sys.stderr)
        print("PRIVACY GUARD FAILED: identity wording must not be committed. See PRIVACY.md.", file=sys.stderr)
    if problems or hits:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
