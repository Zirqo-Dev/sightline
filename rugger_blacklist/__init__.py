"""Cross-run rugger blacklist for the Cardano and Solana research desks.

Raw wallets stay in a private store. Public vetoes and the Solana would-veto log
publish a w- id (sha256, 8 hex digits), the same shape as the desks' other wallet tags.

Working store: a JSON file in the Actions cache directory (rugger-blacklist.json).
The file is refused when that directory is the public desk-data directory.
A missing file is an empty list (cache miss): nothing is vetoed from history, and
this run's evidence is recorded only when the private path is writable.

Durable option, not wired: EncryptedFileStore seals the same document with
HMAC-SHA256 (keystream + MAC). The key would be a repo secret the owner creates
(RUGGER_BLACKLIST_KEY). This module does not create secrets, schedules, or crons.
The default open_book() path does not read that variable and does not encrypt.

Defaults: hops 1, expiry_days null (no expiry), shared_funder_min 25.
buy_signal is never produced here. Research / paper only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
from datetime import datetime, timezone

TAGS = ("late_rug", "lp_pulled", "ahead_of_dump", "rugcheck_rugged")
CHAINS = ("cardano", "solana")
SCHEMA = 1
PRIVATE_NAME = "rugger-blacklist.json"
MAX_TOKENS = 64
MAX_EDGES = 5000
MAX_HOPS = 8
DEFAULTS = {"hops": 1, "expiry_days": None, "shared_funder_min": 25}

_ID_RE = re.compile(r"^w-[0-9a-f]{8}$")
_STAKE_RE = re.compile(r"stake1[02-9ac-hj-np-z]{20,}")
_ADDR_RE = re.compile(r"addr1[02-9ac-hj-np-z]{20,}")
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def public_id(wallet):
    """w- hash of a wallet. None when the value is empty, a tag, or not a string."""
    if not isinstance(wallet, str):
        return None
    wallet = wallet.strip()
    if not wallet or len(wallet) > 200 or _ID_RE.fullmatch(wallet):
        return None
    if any(ord(c) < 32 for c in wallet):
        return None
    return "w-" + hashlib.sha256(wallet.encode()).hexdigest()[:8]


def iso(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).strftime(_ISO)


def parse_iso(text):
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text, _ISO).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def settings_from(cfg):
    """hops, expiry_days, shared_funder_min. Bad values fall back to DEFAULTS."""
    block = (cfg or {}).get("rugger_blacklist") if isinstance(cfg, dict) else None
    block = block if isinstance(block, dict) else {}
    hops = _int(block.get("hops"), DEFAULTS["hops"], 0, MAX_HOPS)
    hub = _int(block.get("shared_funder_min"), DEFAULTS["shared_funder_min"], 2, 100000)
    expiry = block.get("expiry_days", DEFAULTS["expiry_days"])
    if expiry is not None:
        try:
            expiry = float(expiry)
        except (TypeError, ValueError):
            expiry = None
        if expiry is not None and expiry < 0:
            expiry = None
    return {"hops": hops, "expiry_days": expiry, "shared_funder_min": hub}


def _int(value, default, lo, hi):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, value))


def _token_ok(token):
    if not isinstance(token, str):
        return False
    token = token.strip()
    if not token or len(token) > 200:
        return False
    if _STAKE_RE.search(token) or _ADDR_RE.search(token):
        return False
    return True


def _empty():
    return {"schema": SCHEMA, "entries": {}, "edges": [], "exclusions": []}


def _inside(path, parent):
    if not parent:
        return False
    path = os.path.abspath(path)
    parent = os.path.abspath(parent)
    return path == parent or path.startswith(parent + os.sep)


class Store:
    """Load and save the private document. Implementations must not log wallets."""

    def load(self):
        raise NotImplementedError

    def save(self, doc):
        raise NotImplementedError

    @property
    def writable(self):
        return True


class MemoryStore(Store):
    def __init__(self, doc=None):
        self.doc = doc

    def load(self):
        return self.doc

    def save(self, doc):
        self.doc = doc


class NullStore(Store):
    """Cache path refused (inside the public directory) or unreadable. Never writes."""

    def __init__(self):
        self.blocked = True

    def load(self):
        return None

    def save(self, doc):
        return None

    @property
    def writable(self):
        return False


class JsonFileStore(Store):
    """Actions-cache JSON. Refuses a path inside the public data directory."""

    def __init__(self, path, public_dir=None):
        self.path = os.path.abspath(path) if path else ""
        self.blocked = (not self.path) or _inside(self.path, public_dir)
        self._corrupt = False

    def load(self):
        if self.blocked or not os.path.exists(self.path):
            return None
        try:
            with open(self.path) as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            self._corrupt = True
            print("rugger blacklist: private store unreadable; not overwritten")
            return None
        if not isinstance(doc, dict):
            self._corrupt = True
            print("rugger blacklist: private store unreadable; not overwritten")
            return None
        return doc

    def save(self, doc):
        if self.blocked or self._corrupt:
            return
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".rugger-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(doc, fh, separators=(",", ":"))
                fh.write("\n")
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @property
    def writable(self):
        return not self.blocked and not self._corrupt


class EncryptedFileStore(Store):
    """Optional seal. Not used by open_book().

    The key is bytes the caller already holds (a repo secret the owner would create).
    Layout: magic RG1, 16-byte nonce, 32-byte MAC, ciphertext.
    The keystream is HMAC-SHA256 in counter mode. The MAC covers nonce and ciphertext.
    A bad key or a bad MAC loads as unreadable and does not overwrite the file.
    """

    MAGIC = b"RG1"

    def __init__(self, path, key, public_dir=None):
        self.path = os.path.abspath(path) if path else ""
        self.blocked = (not self.path) or _inside(self.path, public_dir)
        if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
            self._key = None
        else:
            self._key = bytes(key)
        self._corrupt = False

    def load(self):
        if self.blocked or self._key is None or not os.path.exists(self.path):
            if self._key is None:
                self._corrupt = True
            return None
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read()
            doc = json.loads(_open_seal(self._key, raw).decode())
        except (OSError, ValueError):
            self._corrupt = True
            print("rugger blacklist: private store unreadable; not overwritten")
            return None
        if not isinstance(doc, dict):
            self._corrupt = True
            print("rugger blacklist: private store unreadable; not overwritten")
            return None
        return doc

    def save(self, doc):
        if self.blocked or self._corrupt or self._key is None:
            return
        parent = os.path.dirname(self.path)
        os.makedirs(parent, exist_ok=True)
        blob = _seal(self._key, json.dumps(doc, separators=(",", ":")).encode())
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".rugger-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(blob)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @property
    def writable(self):
        return not self.blocked and not self._corrupt and self._key is not None


def _seal(key, plaintext):
    nonce = os.urandom(16)
    ct = bytes(p ^ k for p, k in zip(plaintext, _keystream(key, nonce, len(plaintext))))
    mac = hmac.new(key, b"mac1" + nonce + ct, hashlib.sha256).digest()
    return EncryptedFileStore.MAGIC + nonce + mac + ct


def _open_seal(key, blob):
    if not blob.startswith(EncryptedFileStore.MAGIC) or len(blob) < 3 + 16 + 32:
        raise ValueError("seal")
    nonce = blob[3:19]
    mac = blob[19:51]
    ct = blob[51:]
    expect = hmac.new(key, b"mac1" + nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        raise ValueError("seal")
    return bytes(c ^ k for c, k in zip(ct, _keystream(key, nonce, len(ct))))


def _keystream(key, nonce, n):
    out = bytearray()
    counter = 0
    while len(out) < n:
        out.extend(hmac.new(key, b"ks1" + nonce + counter.to_bytes(4, "big"), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:n])


def _clean_doc(raw):
    doc = _empty()
    if not isinstance(raw, dict):
        return doc
    entries = raw.get("entries")
    if isinstance(entries, dict):
        for wallet, row in entries.items():
            if not public_id(wallet) or not isinstance(row, dict):
                continue
            evidence = {}
            ev = row.get("evidence")
            if isinstance(ev, dict):
                for tag, tokens in ev.items():
                    if tag not in TAGS or not isinstance(tokens, list):
                        continue
                    kept = []
                    for token in tokens:
                        if _token_ok(token) and token not in kept:
                            kept.append(token)
                    if kept:
                        evidence[tag] = kept[-MAX_TOKENS:]
            chain = row.get("chain") if row.get("chain") in CHAINS else "cardano"
            doc["entries"][wallet] = {
                "chain": chain,
                "evidence": evidence,
                "first_seen": row.get("first_seen") if parse_iso(row.get("first_seen")) else None,
                "last_seen": row.get("last_seen") if parse_iso(row.get("last_seen")) else None,
                "funded": [],
            }
    edges = raw.get("edges")
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            funder, funded = edge.get("funder"), edge.get("funded")
            if not public_id(funder) or not public_id(funded) or funder == funded:
                continue
            chain = edge.get("chain") if edge.get("chain") in CHAINS else "cardano"
            at = edge.get("at") if parse_iso(edge.get("at")) else None
            doc["edges"].append({"funder": funder, "funded": funded, "chain": chain, "at": at})
    exclusions = raw.get("exclusions")
    if isinstance(exclusions, list):
        for wallet in exclusions:
            if public_id(wallet) and wallet not in doc["exclusions"]:
                doc["exclusions"].append(wallet)
    _dedupe_edges(doc)
    _attach_all(doc)
    return doc


def _dedupe_edges(doc):
    best = {}
    for edge in doc.get("edges") or []:
        key = (edge["funder"], edge["funded"])
        prev = best.get(key)
        if prev is None or (edge.get("at") or "") >= (prev.get("at") or ""):
            best[key] = edge
    ordered = sorted(best.values(), key=lambda e: ((e.get("at") or ""), e["funder"], e["funded"]))
    if len(ordered) > MAX_EDGES:
        ordered = ordered[-MAX_EDGES:]
    doc["edges"] = ordered


def _attach_all(doc):
    by = {}
    excluded = set(doc.get("exclusions") or [])
    for edge in doc.get("edges") or []:
        if edge["funder"] in excluded or edge["funded"] in excluded:
            continue
        by.setdefault(edge["funder"], [])
        if edge["funded"] not in by[edge["funder"]]:
            by[edge["funder"]].append(edge["funded"])
    for wallet, entry in (doc.get("entries") or {}).items():
        entry["funded"] = list(by.get(wallet, []))


class Book:
    """In-memory blacklist backed by a Store. Judge returns public ids only."""

    def __init__(self, store, settings=None, extra_exclusions=()):
        self.store = store
        self.settings = dict(DEFAULTS)
        if settings:
            self.settings.update(settings)
        loaded = store.load()
        self.doc = _clean_doc(loaded)
        self._saved_ok = store.writable
        for wallet in extra_exclusions or []:
            self.exclude(wallet)

    @property
    def writable(self):
        return bool(self.store.writable)

    def exclude(self, wallet):
        if not public_id(wallet):
            return
        if wallet not in self.doc["exclusions"]:
            self.doc["exclusions"].append(wallet)
        self.doc["entries"].pop(wallet, None)
        self.doc["edges"] = [
            e for e in self.doc["edges"] if e["funder"] != wallet and e["funded"] != wallet
        ]
        _attach_all(self.doc)

    def note_evidence(self, wallet, chain, tag, token, at):
        """Record rug evidence on a deployer. False when the row is dropped."""
        if tag not in TAGS or chain not in CHAINS or not _token_ok(token):
            return False
        if not public_id(wallet) or wallet in self.doc["exclusions"]:
            return False
        if not parse_iso(at):
            return False
        entry = self.doc["entries"].get(wallet)
        if entry is None:
            entry = {
                "chain": chain, "evidence": {}, "first_seen": at, "last_seen": at, "funded": [],
            }
            self.doc["entries"][wallet] = entry
        entry["chain"] = chain
        entry["last_seen"] = at
        if not entry.get("first_seen"):
            entry["first_seen"] = at
        tokens = entry["evidence"].setdefault(tag, [])
        if token not in tokens:
            tokens.append(token)
            if len(tokens) > MAX_TOKENS:
                del tokens[:-MAX_TOKENS]
        _attach_all(self.doc)
        return True

    def note_edge(self, funder, funded, chain, at):
        """One funding hop: funder sent funds to funded. Excluded endpoints are dropped."""
        if chain not in CHAINS or not parse_iso(at):
            return False
        if not public_id(funder) or not public_id(funded) or funder == funded:
            return False
        if funder in self.doc["exclusions"] or funded in self.doc["exclusions"]:
            return False
        self.doc["edges"].append({"funder": funder, "funded": funded, "chain": chain, "at": at})
        _dedupe_edges(self.doc)
        _attach_all(self.doc)
        return True

    def save(self):
        if not self.writable:
            return False
        _dedupe_edges(self.doc)
        _attach_all(self.doc)
        self.store.save(self.doc)
        return True

    def _expired(self, at, now_s):
        days = self.settings.get("expiry_days")
        if days is None:
            return False
        ts = parse_iso(at)
        if ts is None:
            return True
        return (float(now_s) - ts) > float(days) * 86400.0

    def _graph(self, now_s):
        adj = {}
        inbound = {}
        excluded = set(self.doc["exclusions"])
        for edge in self.doc["edges"]:
            if edge["funder"] in excluded or edge["funded"] in excluded:
                continue
            if self._expired(edge.get("at"), now_s):
                continue
            adj.setdefault(edge["funder"], set()).add(edge["funded"])
            inbound.setdefault(edge["funded"], set()).add(edge["funder"])
        return adj, inbound

    def _active(self, now_s):
        out = {}
        excluded = set(self.doc["exclusions"])
        for wallet, entry in self.doc["entries"].items():
            if wallet in excluded or not entry.get("evidence"):
                continue
            if self._expired(entry.get("last_seen"), now_s):
                continue
            out[wallet] = entry
        return out

    def judge(self, wallet, token, now_s):
        """Public verdicts for a deployer. No raw wallet in the result.

        known_rugger: this wallet already has rug evidence on a different token.
        known_rugger_funded: this wallet is within `hops` of a rugger, outbound,
        or a hop-1 sibling through a shared funder who is not excluded and whose
        fan-out is below shared_funder_min.
        """
        if not public_id(wallet) or wallet in self.doc["exclusions"]:
            return []
        if not _token_ok(token):
            token = ""
        now_s = float(now_s)
        verdicts = []
        entry = self._active(now_s).get(wallet)
        if entry:
            tags = []
            for tag, tokens in entry["evidence"].items():
                if any(item != token for item in tokens):
                    tags.append(tag)
            if tags:
                verdicts.append({
                    "code": "known_rugger",
                    "rugger_id": public_id(wallet),
                    "tags": sorted(tags),
                })
        funded = self._funded_hit(wallet, now_s)
        if funded:
            verdicts.append({
                "code": "known_rugger_funded",
                "rugger_id": funded["rugger_id"],
                "wallet_id": public_id(wallet),
                "tags": funded["tags"],
                "hop": funded["hop"],
            })
        return verdicts

    def _funded_hit(self, wallet, now_s):
        hops = int(self.settings.get("hops") or 0)
        if hops < 1:
            return None
        ruggers = self._active(now_s)
        adj, inbound = self._graph(now_s)
        hub = int(self.settings["shared_funder_min"])
        excluded = set(self.doc["exclusions"])

        def fanout(node):
            return len(adj.get(node) or ())

        def expandable(node):
            return node not in excluded and fanout(node) < hub

        best = None

        def consider(rugger, hop):
            nonlocal best
            if hop < 1 or hop > hops or rugger == wallet:
                return
            entry = ruggers.get(rugger)
            if not entry:
                return
            tags = sorted(entry["evidence"])
            if not tags:
                return
            rid = public_id(rugger)
            if not rid:
                return
            if best is None or hop < best[0] or (hop == best[0] and rid < best[1]):
                best = (hop, rid, tags)

        for rugger in ruggers:
            if rugger == wallet:
                continue
            if expandable(rugger):
                seen = {rugger}
                frontier = [(rugger, 0)]
                while frontier:
                    node, dist = frontier.pop(0)
                    if dist >= hops or not expandable(node):
                        continue
                    for nxt in sorted(adj.get(node) or ()):
                        if nxt in excluded or nxt in seen:
                            continue
                        seen.add(nxt)
                        if nxt == wallet:
                            consider(rugger, dist + 1)
                        if dist + 1 < hops and expandable(nxt):
                            frontier.append((nxt, dist + 1))
            if hops >= 1:
                for funder in sorted(inbound.get(rugger) or ()):
                    if not expandable(funder):
                        continue
                    if wallet in (adj.get(funder) or ()) and wallet != rugger:
                        consider(rugger, 1)
        if best is None:
            return None
        return {"hop": best[0], "rugger_id": best[1], "tags": best[2]}


def open_book(cache_dir, public_dir, cfg=None, exclusions=()):
    """Private book for this run. None when no cache directory was given.

    A cache directory inside public_dir still returns a book, but it does not
    read or write a file. That is the refused-path case, not a cache miss.
    """
    if not cache_dir:
        return None
    path = os.path.join(cache_dir, PRIVATE_NAME)
    store = JsonFileStore(path, public_dir)
    if store.blocked:
        store = NullStore()
    extra = list(exclusions or [])
    block = (cfg or {}).get("rugger_blacklist") if isinstance(cfg, dict) else None
    if isinstance(block, dict) and isinstance(block.get("exclusions"), list):
        extra.extend(block["exclusions"])
    return Book(store, settings_from(cfg), extra)


def ada_only_recipients(dev, txs, min_lovelace=5_000_000):
    """Stakes that received an ADA-only output in a tx `dev` paid into.

    Each tx is {inputs: [{stake}], outputs: [{stake, lovelace, assets, script}]}.
    A script output, a token output, or dust under min_lovelace is not a hop.
    This does not call the chain. It does not return addresses.
    """
    if not dev or not isinstance(txs, list):
        return []
    try:
        floor = int(min_lovelace)
    except (TypeError, ValueError):
        floor = 5_000_000
    found = []
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        inputs = tx.get("inputs") or []
        if not any(isinstance(u, dict) and u.get("stake") == dev for u in inputs):
            continue
        for out in tx.get("outputs") or []:
            if not isinstance(out, dict) or out.get("script"):
                continue
            stake = out.get("stake")
            if not stake or stake == dev or not public_id(stake):
                continue
            assets = out.get("assets") or {}
            if assets:
                continue
            try:
                lov = int(out.get("lovelace") or 0)
            except (TypeError, ValueError):
                continue
            if lov < floor:
                continue
            if stake not in found:
                found.append(stake)
    return found


def reseed_late_rugs(book, cache_dir, public_dir, at):
    """Copy late_rug rows from the dev-checks private cache into the book.

    A missing dev cache is a cache miss for this feed: nothing is added.
    Raw stakes are not printed.
    """
    if book is None or not cache_dir:
        return 0
    path = os.path.join(cache_dir, "dev-checks-cache.json")
    if _inside(path, public_dir) or not os.path.exists(path):
        return 0
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return 0
    units = doc.get("units") if isinstance(doc, dict) else None
    if not isinstance(units, dict):
        return 0
    n = 0
    for unit, row in units.items():
        if not isinstance(row, dict) or row.get("late_rug") is not True:
            continue
        if book.note_evidence(row.get("stake"), "cardano", "late_rug", str(unit), at):
            n += 1
    return n


def deployers_from_dev_cache(cache_dir, public_dir):
    """{unit: stake} from the dev-checks private cache. Empty on a cache miss."""
    if not cache_dir:
        return {}
    path = os.path.join(cache_dir, "dev-checks-cache.json")
    if _inside(path, public_dir) or not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return {}
    units = doc.get("units") if isinstance(doc, dict) else None
    if not isinstance(units, dict):
        return {}
    out = {}
    for unit, row in units.items():
        if isinstance(row, dict) and public_id(row.get("stake")) and _token_ok(str(unit)):
            out[str(unit)] = row["stake"]
    return out
