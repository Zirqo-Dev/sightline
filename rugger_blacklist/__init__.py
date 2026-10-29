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
DEFAULTS = {"hops": 1, "expiry_days": None, "shared_funder_min": 25, "sibling": False}
FUND_WINDOW_H = 72
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

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
    """hops, expiry_days, shared_funder_min, sibling. Bad values fall back to DEFAULTS.

    sibling defaults false. When true, a shared-funder link is a caution code, not a hard veto.
    The fan-out floor gates shared funders and intermediates. It does not gate the rugger's own
    first hop.
    """
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
    return {
        "hops": hops, "expiry_days": expiry, "shared_funder_min": hub,
        "sibling": block.get("sibling") is True,
    }


def wallet_sha256(wallet):
    """Full sha256 hex of a wallet string. None when the wallet is not usable."""
    if not public_id(wallet):
        return None
    return hashlib.sha256(wallet.encode()).hexdigest()


def owner_ids(owner_env, derive=None):
    """w- ids of literal stake keys and addresses in owner pattern text.

    `derive`, when set, maps an address to its stake key. Nothing is printed.
    """
    ids = set()
    for token in _STAKE_RE.findall(owner_env or "") + _ADDR_RE.findall(owner_env or ""):
        pid = public_id(token)
        if pid:
            ids.add(pid)
        if derive is not None and token.startswith("addr1"):
            try:
                stake = derive(token)
            except Exception:
                stake = None
            pid = public_id(stake) if stake else None
            if pid:
                ids.add(pid)
    return ids


def exclusion_sha256(cfg):
    """Full 64-hex hashes from config. Anything else is ignored. Raw wallets are not read."""
    block = (cfg or {}).get("rugger_blacklist") if isinstance(cfg, dict) else None
    if not isinstance(block, dict):
        return []
    out = []
    for item in block.get("exclusion_sha256") or []:
        if isinstance(item, str) and _SHA_RE.fullmatch(item) and item not in out:
            out.append(item)
    return out


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
        self.kind = "hit"

    def load(self):
        return self.doc

    def save(self, doc):
        self.doc = doc


class NullStore(Store):
    """Cache path refused (inside the public directory). Never writes."""

    def __init__(self):
        self.blocked = True
        self.kind = "refused"

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
        self.kind = "refused" if self.blocked else "miss"

    def load(self):
        if self.blocked:
            self.kind = "refused"
            return None
        if not os.path.exists(self.path):
            self.kind = "miss"
            return None
        try:
            with open(self.path) as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            self._corrupt = True
            self.kind = "corrupt"
            print("::warning::rugger blacklist: private store unreadable; not overwritten")
            return None
        if not isinstance(doc, dict):
            self._corrupt = True
            self.kind = "corrupt"
            print("::warning::rugger blacklist: private store unreadable; not overwritten")
            return None
        self.kind = "hit"
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
        self.kind = "refused" if self.blocked else "miss"

    def load(self):
        if self.blocked:
            self.kind = "refused"
            return None
        if self._key is None:
            self._corrupt = True
            self.kind = "corrupt"
            return None
        if not os.path.exists(self.path):
            self.kind = "miss"
            return None
        try:
            with open(self.path, "rb") as fh:
                raw = fh.read()
            doc = json.loads(_open_seal(self._key, raw).decode())
        except (OSError, ValueError):
            self._corrupt = True
            self.kind = "corrupt"
            print("::warning::rugger blacklist: private store unreadable; not overwritten")
            return None
        if not isinstance(doc, dict):
            self._corrupt = True
            self.kind = "corrupt"
            print("::warning::rugger blacklist: private store unreadable; not overwritten")
            return None
        self.kind = "hit"
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

    def __init__(self, store, settings=None, extra_exclusions=(), exclusion_sha=()):
        self.store = store
        self.settings = dict(DEFAULTS)
        if settings:
            self.settings.update(settings)
        self.exclusion_sha = {h for h in (exclusion_sha or []) if isinstance(h, str) and _SHA_RE.fullmatch(h)}
        self.readonly = False
        self.held = False
        self._held_entries = 0
        self._held_edges = 0
        loaded = store.load()
        self.doc = _clean_doc(loaded)
        self._saved_ok = store.writable
        for wallet in extra_exclusions or []:
            self.exclude(wallet)

    @property
    def writable(self):
        return bool(self.store.writable) and not self.readonly

    def _blocked(self, wallet):
        if not wallet or wallet in self.doc["exclusions"]:
            return True
        digest = wallet_sha256(wallet)
        return bool(digest) and digest in self.exclusion_sha

    def hold_for_miss(self, prior):
        """A cache miss after a non-empty list does not save over the missing file."""
        entries = 0
        edges = 0
        if isinstance(prior, dict):
            try:
                entries = int(prior.get("entries") or 0)
                edges = int(prior.get("edges") or 0)
            except (TypeError, ValueError):
                entries = 0
                edges = 0
        kind = getattr(self.store, "kind", "hit")
        if kind == "corrupt":
            self._held_entries = entries
            self._held_edges = edges
            return
        if kind == "miss" and entries > 0:
            self.readonly = True
            self.held = True
            self._held_entries = entries
            self._held_edges = edges
            print("::warning::rugger blacklist: cache miss after a non-empty list; not saving")

    def status(self):
        """Hash-free counts for the public health line."""
        if self.held:
            return {"store": "miss", "entries": self._held_entries, "edges": self._held_edges, "held": True}
        kind = getattr(self.store, "kind", "hit")
        if kind == "corrupt":
            return {"store": "corrupt", "entries": self._held_entries, "edges": self._held_edges}
        return {
            "store": kind,
            "entries": len(self.doc.get("entries") or {}),
            "edges": len(self.doc.get("edges") or []),
        }

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
        if not public_id(wallet) or self._blocked(wallet):
            return False
        if parse_iso(at) is None:
            return False
        entry = self.doc["entries"].get(wallet)
        if entry is None:
            entry = {
                "chain": chain, "evidence": {}, "first_seen": at, "last_seen": at, "funded": [],
            }
            self.doc["entries"][wallet] = entry
        entry["chain"] = chain
        if not entry.get("first_seen") or at < entry["first_seen"]:
            entry["first_seen"] = at
        if not entry.get("last_seen") or at > entry["last_seen"]:
            entry["last_seen"] = at
        tokens = entry["evidence"].setdefault(tag, [])
        if token not in tokens:
            tokens.append(token)
            if len(tokens) > MAX_TOKENS:
                del tokens[:-MAX_TOKENS]
        _attach_all(self.doc)
        return True

    def note_edge(self, funder, funded, chain, at):
        """One funding hop: funder sent funds to funded. Excluded endpoints are dropped."""
        if chain not in CHAINS or parse_iso(at) is None:
            return False
        if not public_id(funder) or not public_id(funded) or funder == funded:
            return False
        if self._blocked(funder) or self._blocked(funded):
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
        if getattr(self.store, "kind", "") == "miss":
            self.store.kind = "hit"
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
        for edge in self.doc["edges"]:
            if self._blocked(edge.get("funder")) or self._blocked(edge.get("funded")):
                continue
            if self._expired(edge.get("at"), now_s):
                continue
            adj.setdefault(edge["funder"], set()).add(edge["funded"])
            inbound.setdefault(edge["funded"], set()).add(edge["funder"])
        return adj, inbound

    def _active(self, now_s):
        out = {}
        for wallet, entry in self.doc["entries"].items():
            if self._blocked(wallet) or not entry.get("evidence"):
                continue
            if self._expired(entry.get("last_seen"), now_s):
                continue
            out[wallet] = entry
        return out

    def judge(self, wallet, token, now_s, launch_s=None):
        """Public verdicts for a deployer. No raw wallet in the result.

        known_rugger: this wallet already has rug evidence on a different token.
        known_rugger_funded: outbound from a rugger, within `hops`. The rugger's own
        first hop ignores the fan-out floor. Intermediates and shared funders do not.
        When launch_s is set, the edge into this wallet must be the earliest funding
        inside FUND_WINDOW_H before that launch, and not after it.
        known_rugger_sibling: caution only, and only when settings sibling is true.
        """
        if not public_id(wallet) or self._blocked(wallet):
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
        funded = self._funded_hit(wallet, now_s, launch_s, sibling=False)
        if funded:
            verdicts.append({
                "code": "known_rugger_funded",
                "rugger_id": funded["rugger_id"],
                "wallet_id": public_id(wallet),
                "tags": funded["tags"],
                "hop": funded["hop"],
            })
        if self.settings.get("sibling") is True:
            sib = self._funded_hit(wallet, now_s, launch_s, sibling=True)
            if sib:
                verdicts.append({
                    "code": "known_rugger_sibling",
                    "rugger_id": sib["rugger_id"],
                    "wallet_id": public_id(wallet),
                    "tags": sib["tags"],
                    "hop": sib["hop"],
                })
        return verdicts

    def _first_funder(self, wallet, launch_s, now_s):
        """Earliest in-window funder of wallet, or None when launch_s is set and nobody qualifies.

        Without launch_s every non-expired inbound edge is eligible and the earliest time wins.
        With launch_s the edge must be at or before the launch and inside FUND_WINDOW_H.
        """
        best = None
        window = float(FUND_WINDOW_H) * 3600.0
        for edge in self.doc.get("edges") or []:
            if edge.get("funded") != wallet or self._expired(edge.get("at"), now_s):
                continue
            if self._blocked(edge.get("funder")):
                continue
            ts = parse_iso(edge.get("at"))
            if launch_s is not None:
                if ts is None or ts > float(launch_s) or float(launch_s) - ts > window:
                    continue
            key = (ts is None, ts or 0, edge.get("funder") or "")
            if best is None or key < best[0]:
                best = (key, edge.get("funder"))
        return None if best is None else best[1]

    def _funded_hit(self, wallet, now_s, launch_s=None, sibling=False):
        hops = int(self.settings.get("hops") or 0)
        if hops < 1:
            return None
        ruggers = self._active(now_s)
        adj, inbound = self._graph(now_s)
        hub = int(self.settings["shared_funder_min"])

        def fanout(node):
            return len(adj.get(node) or ())

        def intermediate_ok(node):
            return not self._blocked(node) and fanout(node) < hub

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

        if sibling:
            for rugger in ruggers:
                if rugger == wallet:
                    continue
                for funder in sorted(inbound.get(rugger) or ()):
                    if not intermediate_ok(funder):
                        continue
                    if wallet not in (adj.get(funder) or ()) or wallet == rugger:
                        continue
                    if self._first_funder(wallet, launch_s, now_s) != funder:
                        continue
                    consider(rugger, 1)
            if best is None:
                return None
            return {"hop": best[0], "rugger_id": best[1], "tags": best[2]}

        for rugger in ruggers:
            if rugger == wallet or self._blocked(rugger):
                continue
            # The rugger is always expanded. The floor applies to later hops only.
            seen = {rugger}
            frontier = [(rugger, 0)]
            while frontier:
                node, dist = frontier.pop(0)
                if dist >= hops:
                    continue
                if dist > 0 and not intermediate_ok(node):
                    continue
                for nxt in sorted(adj.get(node) or ()):
                    if self._blocked(nxt) or nxt in seen:
                        continue
                    seen.add(nxt)
                    if nxt == wallet and self._first_funder(wallet, launch_s, now_s) == node:
                        consider(rugger, dist + 1)
                    if dist + 1 < hops and intermediate_ok(nxt):
                        frontier.append((nxt, dist + 1))
        if best is None:
            return None
        return {"hop": best[0], "rugger_id": best[1], "tags": best[2]}


def open_book(cache_dir, public_dir, cfg=None, exclusions=(), prior=None):
    """Private book for this run. None when no cache directory was given.

    A cache directory inside public_dir still returns a book, but it does not
    read or write a file. That is the refused-path case, not a cache miss.
    exclusion_sha256 values in cfg are full hashes, matched at runtime.
    prior is the last public rugger_status. A miss after entries > 0 is read-only.
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
    book = Book(store, settings_from(cfg), extra, exclusion_sha256(cfg))
    book.hold_for_miss(prior)
    return book


def ada_only_recipients(dev, txs, min_lovelace=5_000_000):
    """ADA-only recipients in a tx whose every non-script input is `dev`.

    Each tx is {time, inputs: [{stake, script}], outputs: [{stake, lovelace, assets, script}]}.
    A second non-script payer, a script output, a token output, or dust is not a hop.
    Returns [{stake, time}] with the tx time. This does not call the chain.
    """
    if not dev or not isinstance(txs, list):
        return []
    try:
        floor = int(min_lovelace)
    except (TypeError, ValueError):
        floor = 5_000_000
    found = []
    seen = set()
    for tx in txs:
        if not isinstance(tx, dict):
            continue
        inputs = [u for u in (tx.get("inputs") or []) if isinstance(u, dict)]
        payers = [u for u in inputs if not u.get("script") and u.get("stake")]
        if not payers or any(u.get("stake") != dev for u in payers):
            continue
        when = tx.get("time")
        if isinstance(when, bool) or not isinstance(when, (int, float)) or when <= 0:
            when = None
        for out in tx.get("outputs") or []:
            if not isinstance(out, dict) or out.get("script"):
                continue
            stake = out.get("stake")
            if not stake or stake == dev or not public_id(stake) or stake in seen:
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
            seen.add(stake)
            found.append({"stake": stake, "time": None if when is None else float(when)})
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
        stamp = at
        live = row.get("live_at_ms")
        if isinstance(live, (int, float)) and not isinstance(live, bool) and live > 0:
            stamp = iso(float(live) / 1000.0)
        if book.note_evidence(row.get("stake"), "cardano", "late_rug", str(unit), stamp):
            n += 1
    return n


HOLD_NAME = "HOLD"
PULLS_NAME = "rugger-pulls.json"


def prior_from_doc(doc):
    """The hash-free status on a previous public health or last-run document."""
    if not isinstance(doc, dict):
        return None
    st = doc.get("rugger_status")
    if isinstance(st, dict):
        return st
    vetoes = doc.get("vetoes")
    if isinstance(vetoes, dict) and isinstance(vetoes.get("rugger_status"), dict):
        return vetoes["rugger_status"]
    return None


def read_prior(path):
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return prior_from_doc(json.load(fh))
    except (OSError, ValueError):
        return None


def mark_hold(cache_dir, book):
    """Tell the workflow not to save a cache that would shadow a missed non-empty list."""
    if not cache_dir or book is None:
        return
    kind = getattr(book.store, "kind", "")
    if not book.held and kind != "corrupt":
        return
    if _inside(os.path.join(cache_dir, HOLD_NAME), None):
        return
    try:
        with open(os.path.join(cache_dir, HOLD_NAME), "w") as fh:
            fh.write("1\n")
    except OSError:
        pass


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


def launch_times_from_dev_cache(cache_dir, public_dir):
    """{unit: unix seconds} from live_at_ms. Empty on a cache miss."""
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
        if not isinstance(row, dict) or not _token_ok(str(unit)):
            continue
        live = row.get("live_at_ms")
        if isinstance(live, bool) or not isinstance(live, (int, float)) or live <= 0:
            continue
        out[str(unit)] = float(live) / 1000.0
    return out
