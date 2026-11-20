"""Flag long hex that looks like a hash, a salt, a private key, or an address.

Scope is test fixtures and committed data or docs (markdown, json, jsonl, csv, text).
Source files are not scanned. LST outcome files are not opened.

Shapes:
  0x and 40 hex digits (an address)
  0x and 64 hex digits (a hash or a key)
  64 hex digits with no 0x

An obvious fake is allowed in code, not by listing the repo's current values:
  one repeated nibble, a value from 0 through 16 (zero-padded counters),
  dead / beef / cafe / c0ffee / deadbeef with zero padding,
  a body that is only 0123456789abcdef or abcdef0123456789 repeated,
  or the dummy marker bb22cc33dd44ee55ff6677889900.

Anything else needs scripts/privacy-hex-allow.txt:
  an exact token, a JSON field name, a same-line label, or https URLs.
The file is the allowlist. It does not name order_hash, salt, or key.
"""
from __future__ import annotations

import os
import re
import sys

_OX64 = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_OX40 = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_BARE64 = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_KEY = re.compile(r'"([A-Za-z0-9_]+)"\s*:')
_URL = re.compile(r"https://[^\s\"'<>]+")
_DATA_EXT = {".md", ".json", ".jsonl", ".csv", ".txt"}
_SKIP_NAMES = {"captures.jsonl"}
_DUMMY = "bb22cc33dd44ee55ff6677889900"
_CYCLES = ("0123456789abcdef", "abcdef0123456789")
_DEAD = {"dead", "beef", "cafe", "c0ffee", "deadbeef"}


def obvious_fake(token):
    raw = token.lower()
    body = raw[2:] if raw.startswith("0x") else raw
    if not re.fullmatch(r"[0-9a-f]+", body or ""):
        return False
    if len(set(body)) == 1:
        return True
    if int(body, 16) <= 16:
        return True
    stripped = body.strip("0")
    if stripped in _DEAD:
        return True
    if stripped and stripped.replace("deadbeef", "") == "" and "dead" in stripped:
        return True
    for cycle in _CYCLES:
        if len(body) % len(cycle) == 0 and body == cycle * (len(body) // len(cycle)):
            return True
    if _DUMMY in body:
        return True
    return False


def in_scope(path):
    name = os.path.basename(path)
    if name in _SKIP_NAMES:
        return False
    low = path.replace("\\", "/").lower()
    if "/fixtures/" in low:
        return True
    return os.path.splitext(name)[1].lower() in _DATA_EXT


def load_rules(path):
    tokens, fields, labels = set(), set(), set()
    urls = False
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, errors="replace") as fh:
        for line in fh:
            item = line.split("#", 1)[0].strip()
            if not item:
                continue
            if item.startswith("field:"):
                fields.add(item.split(":", 1)[1].strip())
            elif item.startswith("label:"):
                labels.add(item.split(":", 1)[1].strip().lower())
            elif item == "url":
                urls = True
            else:
                tokens.add(item.lower())
    return {"tokens": tokens, "fields": fields, "labels": labels, "urls": urls}


def _key_before(line, index):
    found = None
    for match in _KEY.finditer(line[:index]):
        found = match.group(1)
    return found


def _label_hit(line, labels):
    for word in labels:
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(word) + r"(?![A-Za-z0-9_])", line, re.I):
            return True
    return False


def _in_url(line, index, rules):
    if not rules["urls"]:
        return False
    for match in _URL.finditer(line):
        if match.start() <= index < match.end():
            return True
    return False


def allowed(token, line, index, rules):
    if obvious_fake(token):
        return True
    if token.lower() in rules["tokens"]:
        return True
    key = _key_before(line, index)
    if key and key in rules["fields"]:
        return True
    if _label_hit(line, rules["labels"]):
        return True
    if _in_url(line, index, rules):
        return True
    return False


def iter_tokens(line):
    """Yield (start, token, kind). 0x forms are taken before a bare 64."""
    spans = []
    for kind, pattern in (("0x-64", _OX64), ("0x-40", _OX40), ("bare-64", _BARE64)):
        for match in pattern.finditer(line):
            if any(match.start() < end and match.end() > start for start, end in spans):
                continue
            spans.append(match.span())
            yield match.start(), match.group(0), kind


def scan_file(path, rules):
    """Findings are path, line, kind, and the JSON key when one sits in front.

    The hex value is not returned.
    """
    if not in_scope(path) or not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return []
    if b"\0" in raw[:8192]:
        return []
    text = raw.decode("utf-8", "replace")
    found = []
    for number, line in enumerate(text.splitlines(), 1):
        for index, token, kind in iter_tokens(line):
            if allowed(token, line, index, rules):
                continue
            found.append({
                "path": path,
                "line": number,
                "kind": kind,
                "key": _key_before(line, index) or "",
            })
    return found


def scan_paths(paths, rules):
    found = []
    for path in paths:
        if path:
            found.extend(scan_file(path, rules))
    return found


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Scan fixtures and data for long hex")
    parser.add_argument("--root", default=".")
    parser.add_argument("--rules", default="")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    rules_path = args.rules or os.path.join(here, "privacy-hex-allow.txt")
    try:
        rules = load_rules(rules_path)
    except OSError:
        print("PRIVACY GUARD FAILED: hex allowlist is missing", file=sys.stderr)
        return 1
    paths = list(args.paths)
    if not paths and not sys.stdin.isatty():
        paths.extend(ln.strip("\n") for ln in sys.stdin if ln.strip())
    root = args.root
    full = []
    for path in paths:
        if not path:
            continue
        full.append(path if os.path.isabs(path) else os.path.join(root, path))
    found = scan_paths(full, rules)
    for item in found:
        key = (" key " + item["key"]) if item["key"] else ""
        print(
            "PRIVACY GUARD: %s: long hex (%s) on line %d%s"
            % (item["path"], item["kind"], item["line"], key),
            file=sys.stderr,
        )
    if found:
        print("PRIVACY GUARD FAILED: long hex must not be committed. See PRIVACY.md.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
