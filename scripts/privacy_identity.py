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
    out = set()
    if not isinstance(text, str):
        return out
    for line in text.splitlines():
        item = line.strip().lower()
        if _HEX.fullmatch(item):
            out.add(item)
    return out


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
    """Hex digests from identities/blocked.sha256, or None when unavailable.

    Refuses a repo the API does not mark private. Does not print the token,
    the URL, or the file body.
    """
    token = (token or "").strip()
    if not token:
        return None
    repo = repo or _private_repo()
    if not repo:
        return None
    try:
        meta = _github_json("https://api.github.com/repos/" + repo, token)
    except Exception:
        meta = None
    if not isinstance(meta, dict) or meta.get("private") is not True:
        print("::warning::privacy-guard: private repo refused; identity hashes not loaded", file=sys.stderr)
        return None
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
        print("::warning::privacy-guard: identity hash file unavailable", file=sys.stderr)
        return None
    return parse_hashes(body)


def load_hashes(root=".", fetch=None):
    """Union of env, gitignored file, and the private repo. The key is separate."""
    found = set()
    found |= parse_hashes(os.environ.get("PRIVACY_IDENTITY_HASHES", ""))
    path = os.path.join(root, ".privacy-identity-hashes")
    if os.path.isfile(path):
        try:
            found |= parse_hashes(open(path, errors="replace").read())
        except OSError:
            pass
    token = os.environ.get("SIGHTLINE_PRIVATE_TOKEN", "")
    token = token.strip() if isinstance(token, str) else ""
    remote = None
    if token:
        remote = fetch(token) if fetch is not None else fetch_private_hashes(token)
        if remote:
            found |= remote
    return found, token, remote


def hashes_required():
    return os.environ.get("PRIVACY_IDENTITY_REQUIRE", "").strip() in ("1", "true", "yes")


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
    hashes, token, remote = load_hashes(args.root)
    if not hashes:
        # A missing list must not stop the desks. Set PRIVACY_IDENTITY_REQUIRE=1
        # (or pass hashes) when the check has to fail closed.
        if hashes_required():
            print("PRIVACY GUARD FAILED: identity hashes were not loaded", file=sys.stderr)
            return 1
        if token and remote is None:
            print("::warning::privacy-guard: identity hash file unavailable; name and handle checks skipped", file=sys.stderr)
        else:
            print("::warning::privacy-guard: identity hashes not loaded; name and handle checks skipped", file=sys.stderr)
        return 0
    key = identity_key()
    hits = scan_paths(paths, hashes, key)
    if hits:
        for path, n in hits.items():
            print("PRIVACY GUARD: %s: %d identity match(es)" % (path, n), file=sys.stderr)
        print("PRIVACY GUARD FAILED: identity wording must not be committed. See PRIVACY.md.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
