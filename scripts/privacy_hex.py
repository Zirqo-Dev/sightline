"""Flag long hex that looks like a hash, a salt, a private key, or an address.

Scope is test fixtures and committed data or docs (markdown, json, jsonl, csv, text).
Source files are not scanned. LST outcome files are not opened. The rules file
itself is not scanned.

Shapes that are flagged:
  0x and 40 hex digits (an address)
  0x and 64 hex digits (a hash or a key)
  64 hex digits with no 0x

Not flagged: a bare 40-hex string, a 56-hex policy id, or text in .py .js .yml
.html .log.

JSON and JSONL are read as values. A field rule applies only when the whole
value has that key's shape. The key for an array item or a dict key is the
parent key. A markdown label applies only in .md, only to a bare 64-hex
directly after the word sha256. A URL rule applies only to a bare 64-hex on a
named CDN host, never to an 0x address and never on a block explorer.
A hold-cell line may omit until. With no until the hold does not expire.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import json
import os
import re
import sys
from urllib.parse import urlparse

_OX64 = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_OX40 = re.compile(r"(?<![0-9A-Za-z])0x[0-9a-fA-F]{40}(?![0-9a-fA-F])")
_BARE64 = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{64}(?![0-9a-fA-F])")
_URL = re.compile(r"https://[^\s\"'<>]+")
_DIRECT_SHA = re.compile(
    r"(?i)(?<![A-Za-z0-9_])sha256(?![A-Za-z0-9_])[ \t`'\"::()]*$"
)
_COLLECTION = re.compile(r"^nft_collection:eip155:[0-9]+:(0x[0-9a-fA-F]{40})$")
_UTXO = re.compile(r"^([0-9a-fA-F]{64})(?:#\d+(?: \([^)]*\))?)?$")
_DIGEST_PATH = re.compile(r"^([0-9a-fA-F]{64}) [- \w./]+$")
_PREREG_LINE = re.compile(r"^prereg sha256 ([0-9a-fA-F]{64})$")
_FIXTURE_DUMMY = re.compile(
    r"^[0-9a-f]{2}11bb22cc33dd44ee55ff6677889900aabbccdd11223344556677889900ab[0-9a-f]{2}$"
)
_DATA_EXT = {".md", ".json", ".jsonl", ".csv", ".txt"}
_JSON_EXT = {".json", ".jsonl"}
_SKIP_NAMES = {"captures.jsonl", "privacy-hex-allow.txt"}
_DUMMY = "bb22cc33dd44ee55ff6677889900"
_CYCLES = ("0123456789abcdef", "abcdef0123456789")
_DEAD = {"dead", "beef", "cafe", "c0ffee", "deadbeef"}
_SHAPES = {"bare-64", "0x-40", "0x-64", "collection-id", "utxo", "digest-path", "prereg-line"}
_EXPLORERS = (
    "etherscan.io",
    "basescan.org",
    "bscscan.com",
    "polygonscan.com",
    "arbiscan.io",
    "snowtrace.io",
    "solscan.io",
    "cardanoscan.io",
)
_HOLD = re.compile(
    r"hold-cell:(\S+)\s+column:([A-Za-z0-9_]+)\s+run:(\S+)\s+"
    r"sha256:([0-9a-fA-F]{64})(?:\s+until:(\d{4}-\d{2}-\d{2}))?\s*$"
)


def obvious_fake(token):
    """True only when the whole token is an obvious dummy, not when it contains one."""
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
    if body == _DUMMY or (len(_DUMMY) and len(body) % len(_DUMMY) == 0 and body == _DUMMY * (len(body) // len(_DUMMY))):
        return True
    if _FIXTURE_DUMMY.fullmatch(body):
        return True
    return False


def in_scope(path):
    name = os.path.basename(path)
    if name in _SKIP_NAMES:
        return False
    low = path.replace("\\", "/").lower()
    if low.endswith("/captures.jsonl") or low == "captures.jsonl":
        return False
    if "/fixtures/" in low:
        return True
    return os.path.splitext(name)[1].lower() in _DATA_EXT


def path_is(path, rule_path):
    """Match a rule path on a slash boundary. A glued prefix does not count."""
    rel = path.replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return rel == rule_path or rel.endswith("/" + rule_path)


def _shape_ok(shape, whole, token, kind):
    text = (whole or "").strip()
    if shape == "bare-64":
        return kind == "bare-64" and text.lower() == token.lower()
    if shape == "0x-40":
        return kind == "0x-40" and text.lower() == token.lower()
    if shape == "0x-64":
        return kind == "0x-64" and text.lower() == token.lower()
    if shape == "collection-id":
        match = _COLLECTION.fullmatch(text)
        return bool(match) and kind == "0x-40" and match.group(1).lower() == token.lower()
    if shape == "utxo":
        match = _UTXO.fullmatch(text)
        return bool(match) and kind == "bare-64" and match.group(1).lower() == token.lower()
    if shape == "digest-path":
        match = _DIGEST_PATH.fullmatch(text)
        if not match or kind != "bare-64" or match.group(1).lower() != token.lower():
            return False
        return re.search(r"[0-9a-fA-F]{32}", text[len(match.group(1)):]) is None
    if shape == "prereg-line":
        match = _PREREG_LINE.fullmatch(text)
        return bool(match) and kind == "bare-64" and match.group(1).lower() == token.lower()
    return False


def load_rules(path):
    tokens = set()
    json_rules = []
    labels = set()
    cdn_hosts = set()
    holds = []
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, errors="replace") as fh:
        for line in fh:
            item = line.split("#", 1)[0].strip()
            if not item:
                continue
            if item.startswith("hold-cell:"):
                match = _HOLD.fullmatch(item)
                if not match:
                    raise ValueError("bad hold-cell")
                until = match.group(5)
                holds.append({
                    "path": match.group(1),
                    "column": match.group(2),
                    "run": match.group(3),
                    "sha256": match.group(4).lower(),
                    "until": datetime.date.fromisoformat(until) if until else None,
                })
                continue
            if item.startswith("label:"):
                labels.add(item.split(":", 1)[1].strip().lower())
                continue
            if item.startswith("url:"):
                host = item.split(":", 1)[1].strip().lower()
                if host:
                    cdn_hosts.add(host)
                continue
            if item == "url":
                raise ValueError("bad url rule")
            if item.startswith("field:") or item.startswith("path:"):
                parts = {}
                for piece in item.split():
                    if ":" not in piece:
                        raise ValueError("bad rule")
                    key, value = piece.split(":", 1)
                    parts[key] = value
                shape = parts.get("shape")
                if shape not in _SHAPES:
                    raise ValueError("bad shape")
                if "field" not in parts and "path" not in parts:
                    raise ValueError("bad rule")
                json_rules.append({
                    "path": parts.get("path"),
                    "field": parts.get("field"),
                    "shape": shape,
                })
                continue
            tokens.add(item.lower())
    return {
        "tokens": tokens,
        "json_rules": json_rules,
        "labels": labels,
        "cdn_hosts": cdn_hosts,
        "holds": holds,
    }


def iter_tokens(line):
    """Yield (start, token, kind). 0x forms are taken before a bare 64."""
    spans = []
    for kind, pattern in (("0x-64", _OX64), ("0x-40", _OX40), ("bare-64", _BARE64)):
        for match in pattern.finditer(line):
            if any(match.start() < end and match.end() > start for start, end in spans):
                continue
            spans.append(match.span())
            yield match.start(), match.group(0), kind


def _host_is(host, names):
    host = (host or "").lower().rstrip(".")
    return any(host == name or host.endswith("." + name) for name in names)


def url_allows(token, kind, text, index=None):
    """Bare 64-hex on a named CDN host. Never an 0x address, never an explorer."""
    if kind != "bare-64":
        return False
    for match in _URL.finditer(text or ""):
        if index is not None:
            if not (match.start() <= index < match.end()):
                continue
        elif token.lower() not in match.group(0).lower():
            continue
        host = (urlparse(match.group(0)).hostname or "").lower()
        if _host_is(host, _EXPLORERS):
            return False
        # The caller passes the allowed hosts via the text check below.
        return host
    return None


def _label_direct(line, index):
    return bool(_DIRECT_SHA.search(line[:index]))


def _json_allowed(token, kind, field, whole, path, rules):
    if obvious_fake(token) or token.lower() in rules["tokens"]:
        return True
    host = url_allows(token, kind, whole)
    if isinstance(host, str) and host in rules["cdn_hosts"] and not _host_is(host, _EXPLORERS):
        return True
    for rule in rules["json_rules"]:
        if rule["path"] and not path_is(path, rule["path"]):
            continue
        if rule["field"] and rule["field"] != field:
            continue
        if _shape_ok(rule["shape"], whole, token, kind):
            return True
    return False


def _line_allowed(token, kind, line, index, path, ext, rules):
    if obvious_fake(token) or token.lower() in rules["tokens"]:
        return True
    host = url_allows(token, kind, line, index)
    if isinstance(host, str) and host in rules["cdn_hosts"] and not _host_is(host, _EXPLORERS):
        return True
    if ext == ".md" and "sha256" in rules["labels"] and kind == "bare-64" and _label_direct(line, index):
        return True
    return False


def _holds_for(path, rules):
    return [hold for hold in rules.get("holds") or [] if path_is(path, hold["path"])]


def _csv_row(line):
    try:
        return next(csv.reader([line]))
    except csv.Error:
        return None


def _cell_held(header, row, token, holds, today):
    """True only for the pinned cell: same file, column, run, and sha256.

    A hold with no until does not expire. A hold with until stops after that date.
    A different value in that column, even on the same run, is not held.
    """
    if not header or not row or not holds or "run_id" not in header:
        return False
    try:
        run = row[header.index("run_id")]
    except IndexError:
        return False
    for hold in holds:
        if hold["until"] is not None and today > hold["until"]:
            continue
        if run != hold["run"] or hold["column"] not in header:
            continue
        try:
            cell = (row[header.index(hold["column"])] or "").strip()
        except IndexError:
            continue
        if cell.lower() != token.lower():
            continue
        digest = hashlib.sha256(cell.encode("utf-8")).hexdigest()
        if digest == hold["sha256"]:
            return True
    return False


def _line_of(text, token, cursors):
    start = cursors.get(token, 0)
    pos = text.find(token, start)
    if pos < 0:
        pos = text.find(token)
        if pos < 0:
            return 1
    cursors[token] = pos + len(token)
    return text.count("\n", 0, pos) + 1


def _walk(node, parent, path, rules, text, cursors, found):
    if isinstance(node, dict):
        for key, value in node.items():
            key_text = key if isinstance(key, str) else ""
            if key_text:
                _consider(key_text, parent, path, rules, text, cursors, found)
            _walk(value, key_text or parent, path, rules, text, cursors, found)
    elif isinstance(node, list):
        for item in node:
            if isinstance(item, (dict, list)):
                _walk(item, parent, path, rules, text, cursors, found)
            elif isinstance(item, str):
                _consider(item, parent, path, rules, text, cursors, found)
    elif isinstance(node, str):
        _consider(node, parent, path, rules, text, cursors, found)


def _consider(whole, field, path, rules, text, cursors, found):
    for _index, token, kind in iter_tokens(whole):
        if _json_allowed(token, kind, field, whole, path, rules):
            continue
        found.append({
            "path": path,
            "line": _line_of(text, token, cursors),
            "kind": kind,
            "key": field or "",
        })


def _scan_json(path, text, rules):
    found = []
    cursors = {}
    ext = os.path.splitext(path)[1].lower()
    if ext == ".jsonl":
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                node = json.loads(stripped)
            except json.JSONDecodeError:
                _scan_lines(path, line, rules, found, base_line=number)
                continue
            before = len(found)
            _walk(node, "", path, rules, line, cursors, found)
            for item in found[before:]:
                item["line"] = number
        return found
    try:
        node = json.loads(text)
    except json.JSONDecodeError:
        _scan_lines(path, text, rules, found)
        return found
    _walk(node, "", path, rules, text, cursors, found)
    return found


def _scan_lines(path, text, rules, found, base_line=0, today=None):
    ext = os.path.splitext(path)[1].lower()
    holds = _holds_for(path, rules) if ext == ".csv" else []
    today = today or datetime.date.today()
    lines = text.splitlines()
    header = _csv_row(lines[0]) if holds and lines else None
    for number, line in enumerate(lines, 1):
        row = _csv_row(line) if holds and number > 1 else None
        for index, token, kind in iter_tokens(line):
            if _line_allowed(token, kind, line, index, path, ext, rules):
                continue
            if _cell_held(header, row, token, holds, today):
                continue
            found.append({
                "path": path,
                "line": base_line or number,
                "kind": kind,
                "key": "",
            })


def scan_file(path, rules, today=None):
    """Findings are path, line, kind, and the JSON key. The hex is not returned."""
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
    ext = os.path.splitext(path)[1].lower()
    if ext in _JSON_EXT:
        return _scan_json(path, text, rules)
    found = []
    _scan_lines(path, text, rules, found, today=today)
    return found


def scan_paths(paths, rules, today=None):
    found = []
    for path in paths:
        if path:
            found.extend(scan_file(path, rules, today=today))
    return found


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="Scan fixtures and data for long hex")
    parser.add_argument("--root", default=".")
    parser.add_argument("--rules", default="")
    parser.add_argument("--count", action="store_true", help="Print the hit count only")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args(argv)
    here = os.path.dirname(os.path.abspath(__file__))
    rules_path = args.rules or os.path.join(here, "privacy-hex-allow.txt")
    try:
        rules = load_rules(rules_path)
    except OSError:
        print("PRIVACY GUARD FAILED: hex allowlist is missing", file=sys.stderr)
        return 1
    except ValueError:
        print("PRIVACY GUARD FAILED: hex allowlist has a bad rule", file=sys.stderr)
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
    if args.count:
        print(len(found))
        return 0 if not found else 1
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
