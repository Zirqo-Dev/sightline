"""Public ids for Solana forensics. Token mints stay. Wallets become w- hashes.

The hash is sha256(address)[:8], the same w- tag the Cardano ledger uses, so one
address keeps one id across dev, buyer, funder, and cluster fields. Raw addresses
stay in memory for tracing. This module only rewrites the public document.
"""
import hashlib
import json
import pathlib
import re

_SOL = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_SOL_IN = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
# Whole values under these keys are mints or signatures, not wallets.
_KEEP_KEYS = {"mint", "signature", "fee_payer_signature", "dev_paid_signature", "creation_signature"}


def public_id(addr):
    return "w-" + hashlib.sha256(addr.encode()).hexdigest()[:8]


def _collect_mints(obj, found):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "mint" and isinstance(value, str):
                found.add(value)
            else:
                _collect_mints(value, found)
    elif isinstance(obj, list):
        for value in obj:
            _collect_mints(value, found)


def _scrub_text(text, keep):
    def repl(match):
        token = match.group(0)
        return token if token in keep else public_id(token)
    return _SOL_IN.sub(repl, text)


def _walk(obj, key, keep):
    if isinstance(obj, dict):
        return {k: _walk(v, k, keep) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(v, key, keep) for v in obj]
    if isinstance(obj, str):
        if key in _KEEP_KEYS or obj in keep:
            return obj
        if _SOL.fullmatch(obj):
            return public_id(obj)
        if _SOL_IN.search(obj):
            return _scrub_text(obj, keep)
        return obj
    return obj


def redact(obj, keep=None):
    """Copy obj with wallet addresses replaced. Mint values are kept."""
    found = set(keep or [])
    _collect_mints(obj, found)
    return _walk(obj, None, found)


def cache_is_public(data):
    """True when a funding cache has no raw wallet keys left to trace with."""
    if not isinstance(data, dict) or not data:
        return True
    for key, rec in data.items():
        if isinstance(key, str) and _SOL.fullmatch(key):
            return False
        if not isinstance(rec, dict):
            continue
        for field in ("wallet", "funder"):
            value = rec.get(field)
            if isinstance(value, str) and _SOL.fullmatch(value):
                return False
    return True


def redact_cache(cache):
    if not isinstance(cache, dict):
        return {}
    out = {}
    for key, rec in cache.items():
        new_key = public_id(key) if isinstance(key, str) and _SOL.fullmatch(key) else key
        out[new_key] = redact(rec)
    return out


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def redact_public_tree(base):
    """Rewrite reports, panel notes, and the public funding cache under a forensics dir.

    Config is left alone: the scanner matches known infrastructure by raw address.
    A raw cache is remembered for a private-repo flush before it is hashed.
    """
    base = pathlib.Path(base)
    reports = base / "reports"
    if reports.is_dir():
        for path in sorted(reports.glob("*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                _write(path, redact(doc))
        from sol_forensics.engine import _write_panel
        _write_panel(base / "panel.json", reports)
    cache = base / "cache" / "funding.json"
    if not cache.is_file():
        return
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and not cache_is_public(data):
        from sol_forensics.private_cache import current, remember
        if current(cache) is None:
            remember(cache, data)
        _write(cache, redact_cache(data))
