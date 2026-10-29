"""Cross-run rugger blacklist for the Cardano and Solana research desks.

Raw wallets stay in a private store. New rugger public ids are r- plus 12 hex
digits of HMAC-SHA256. The key is HKDF-SHA256 of RUGGER_ID_PEPPER, any UTF-8
string of at least 32 characters (info sightline-rugger-id-v1). They do not
match the desks' w- dev tags. If the pepper is missing or shorter than 32
characters, public rugger rows keep counts and evidence tags and omit the ids.

Working store: rugger-blacklist.json in the Actions cache (fallback read and write).
Durable copy: a human-readable JSON file in the private repo named by
SIGHTLINE_PRIVATE_REPO (default Zirqo-Dev/sightline-private). A separate sync
step pushes it with SIGHTLINE_PRIVATE_TOKEN after the desk run. Scans do not
clone or push. The token is a fine-grained PAT for that private repo only,
Contents read and write. If the token is unset, or the repo is not private,
the cache is used and a fixed warning is printed. Ciphertext is not written
to a public branch.

Defaults: hops 1, expiry_days null (no expiry), shared_funder_min 25.
buy_signal is never produced here. Research / paper only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
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

_ID_RE = re.compile(r"^r-[0-9a-f]{12}$")
_W_RE = re.compile(r"^w-[0-9a-f]{8}$")
_STAKE_RE = re.compile(r"stake1[02-9ac-hj-np-z]{20,}")
_ADDR_RE = re.compile(r"addr1[02-9ac-hj-np-z]{20,}")
_SOL_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{32,44}(?![1-9A-HJ-NP-Za-km-z])")
_CARDANO_RE = re.compile(r"stake1[02-9ac-hj-np-z]{20,}|addr1[02-9ac-hj-np-z]{20,}")
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ISO = "%Y-%m-%dT%H:%M:%SZ"
PEPPER_ENV = "RUGGER_ID_PEPPER"
PEPPER_MIN = 32
_PEPPER_INFO = b"sightline-rugger-id-v1"
TOKEN_ENV = "SIGHTLINE_PRIVATE_TOKEN"
REPO_ENV = "SIGHTLINE_PRIVATE_REPO"
DEFAULT_PRIVATE_REPO = "Zirqo-Dev/sightline-private"
_pepper_warned = False
_token_warned = False
_remote_read = {}


def wallet_ok(wallet):
    """True for a raw wallet string. A public tag is not a wallet."""
    if not isinstance(wallet, str):
        return False
    wallet = wallet.strip()
    if not wallet or len(wallet) > 200 or _ID_RE.fullmatch(wallet) or _W_RE.fullmatch(wallet):
        return False
    return not any(ord(c) < 32 for c in wallet)


def _hkdf_sha256(ikm):
    """32-byte HKDF-SHA256. Salt is 32 zero bytes. Info is the domain label."""
    prk = hmac.new(b"\x00" * 32, ikm, hashlib.sha256).digest()
    return hmac.new(prk, _PEPPER_INFO + b"\x01", hashlib.sha256).digest()


def load_pepper():
    """32-byte HMAC key from RUGGER_ID_PEPPER, or None. The value is not printed.

    Any string of at least 32 characters after strip is accepted. The key is
    HKDF-SHA256 of the UTF-8 string. A shorter string is missing.
    """
    raw = os.environ.get(PEPPER_ENV, "")
    if not isinstance(raw, str):
        return None
    secret = raw.strip()
    if len(secret) < PEPPER_MIN:
        return None
    return _hkdf_sha256(secret.encode("utf-8"))


def pepper_ready():
    return load_pepper() is not None


def warn_no_pepper():
    global _pepper_warned
    if _pepper_warned:
        return
    _pepper_warned = True
    print("::warning::rugger blacklist: RUGGER_ID_PEPPER missing or shorter than 32 characters; publishing counts only")


def unsalted_id(wallet):
    """The desks' existing w- tag. Not a rugger id. Used only to recognise an owner tag."""
    if not wallet_ok(wallet):
        return None
    return "w-" + hashlib.sha256(wallet.strip().encode()).hexdigest()[:8]


def public_id(wallet):
    """r- id. None when the pepper is missing or the wallet is not usable."""
    if not wallet_ok(wallet):
        return None
    key = load_pepper()
    if not key:
        return None
    digest = hmac.new(key, wallet.strip().encode(), hashlib.sha256).hexdigest()[:12]
    return "r-" + digest


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
    if not wallet_ok(wallet):
        return None
    return hashlib.sha256(wallet.strip().encode()).hexdigest()


def _add_owner(ids, wallet):
    wid = unsalted_id(wallet)
    if wid:
        ids.add(wid)
    rid = public_id(wallet)
    if rid:
        ids.add(rid)


def owner_wallets(text):
    """Stake keys, base addresses, and base58 Solana keys (32-44 chars) in `text`.

    A base58 run that overlaps a stake1 or addr1 match is not a second wallet.
    """
    if not isinstance(text, str) or not text:
        return []
    found = []
    spans = []
    for match in _CARDANO_RE.finditer(text):
        found.append(match.group(0))
        spans.append(match.span())
    for match in _SOL_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in spans):
            continue
        token = match.group(0)
        if token not in found:
            found.append(token)
    return found


def owner_ids(owner_env, derive=None):
    """Owner tags that must not be published. w- tags always; r- ids when the pepper is set.

    `derive`, when set, maps an addr1 to its stake key. Solana keys are the base58
    tokens themselves. Nothing is printed. An r- id does not equal that wallet's w- tag.
    """
    ids = set()
    for token in owner_wallets(owner_env):
        _add_owner(ids, token)
        if derive is not None and token.startswith("addr1"):
            try:
                stake = derive(token)
            except Exception:
                stake = None
            if stake:
                _add_owner(ids, stake)
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
    """Local seal kept for tests. Not the durable store.

    Do not commit a ciphertext file to a public branch. The durable copy is the
    human-readable JSON in the private repo. Not used by open_book().

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
    if isinstance(entries, list):
        mapped = {}
        for row in entries:
            if not isinstance(row, dict) or not wallet_ok(row.get("wallet")):
                continue
            ev = row.get("tokens") if isinstance(row.get("tokens"), dict) else row.get("evidence")
            mapped[row["wallet"].strip()] = {
                "chain": row.get("chain"),
                "evidence": ev if isinstance(ev, dict) else {},
                "first_seen": row.get("first_seen"),
                "last_seen": row.get("last_seen"),
                "funded": row.get("funded") if isinstance(row.get("funded"), list) else [],
            }
        entries = mapped
        raw = dict(raw, entries=entries)
    if isinstance(entries, dict):
        for wallet, row in entries.items():
            if not wallet_ok(wallet) or not isinstance(row, dict):
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
            if not wallet_ok(funder) or not wallet_ok(funded) or funder == funded:
                continue
            chain = edge.get("chain") if edge.get("chain") in CHAINS else "cardano"
            at = edge.get("at") if parse_iso(edge.get("at")) else None
            doc["edges"].append({"funder": funder, "funded": funded, "chain": chain, "at": at})
    exclusions = raw.get("exclusions")
    if isinstance(exclusions, list):
        for wallet in exclusions:
            if wallet_ok(wallet) and wallet not in doc["exclusions"]:
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
        """A cache miss after a non-empty list does not save over the missing file.

        RUGGER_ACCEPT_RESET=1 clears that hold, including a corrupt file, so the
        owner can accept an empty list on purpose.
        """
        if accept_reset():
            self.readonly = False
            self.held = False
            if getattr(self.store, "_corrupt", False):
                self.store._corrupt = False
                self.store.kind = "miss"
            return
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
        """Hash-free counts for the public health line.

        sync, when present, is the previous private-repo sync: ok, unset, refused, or error.
        """
        if self.held:
            out = {"store": "miss", "entries": self._held_entries, "edges": self._held_edges, "held": True}
        else:
            kind = getattr(self.store, "kind", "hit")
            if kind == "corrupt":
                out = {"store": "corrupt", "entries": self._held_entries, "edges": self._held_edges}
            else:
                out = {
                    "store": kind,
                    "entries": len(self.doc.get("entries") or {}),
                    "edges": len(self.doc.get("edges") or []),
                }
        sync = read_sync_status(getattr(self, "cache_dir", None), getattr(self, "public_dir", None))
        if sync:
            out["sync"] = sync
        return out

    def exclude(self, wallet):
        if not wallet_ok(wallet):
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
        if not wallet_ok(wallet) or self._blocked(wallet):
            return False
        missing_time = at is None
        if not missing_time and parse_iso(at) is None:
            return False
        entry = self.doc["entries"].get(wallet)
        if entry is None:
            entry = {
                "chain": chain, "evidence": {},
                "first_seen": None if missing_time else at,
                "last_seen": None if missing_time else at,
                "funded": [],
            }
            self.doc["entries"][wallet] = entry
        entry["chain"] = chain
        if not missing_time:
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
        if not wallet_ok(funder) or not wallet_ok(funded) or funder == funded:
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
        if not wallet_ok(wallet) or self._blocked(wallet):
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
            rank = rid or ""
            if best is None or hop < best[0] or (hop == best[0] and rank < best[1]):
                best = (hop, rank, rid, tags)

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
            return {"hop": best[0], "rugger_id": best[2], "tags": best[3]}

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
        return {"hop": best[0], "rugger_id": best[2], "tags": best[3]}


def export_readable(doc, desk):
    """Owner-facing private file. Raw wallets, tags, tokens, tx times, funded links."""
    entries = []
    for wallet, row in sorted((doc.get("entries") or {}).items()):
        if not isinstance(row, dict):
            continue
        ev = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        entries.append({
            "wallet": wallet,
            "chain": row.get("chain"),
            "evidence_tags": sorted(ev),
            "tokens": ev,
            "first_seen": row.get("first_seen"),
            "last_seen": row.get("last_seen"),
            "funded": list(row.get("funded") or []),
        })
    edges = []
    for edge in doc.get("edges") or []:
        if isinstance(edge, dict):
            edges.append({
                "funder": edge.get("funder"),
                "funded": edge.get("funded"),
                "chain": edge.get("chain"),
                "at": edge.get("at"),
            })
    return {
        "schema": SCHEMA,
        "private": True,
        "desk": desk,
        "note": "Private. Raw wallets for the owner. Do not commit this file to a public branch.",
        "entries": entries,
        "edges": edges,
        "exclusions": list(doc.get("exclusions") or []),
    }


def warn_no_token():
    global _token_warned
    if _token_warned:
        return
    _token_warned = True
    print("::warning::rugger blacklist: SIGHTLINE_PRIVATE_TOKEN unset; using the Actions cache only")


def accept_reset():
    """True when the owner set RUGGER_ACCEPT_RESET=1 to drop a hold on purpose."""
    return os.environ.get("RUGGER_ACCEPT_RESET", "").strip() == "1"


def warn_not_updated():
    print("::warning::rugger blacklist: private repo not updated")


def warn_not_private():
    print("::warning::rugger blacklist: private repo refused; visibility is not private")


def _private_repo():
    repo = os.environ.get(REPO_ENV, DEFAULT_PRIVATE_REPO)
    if not isinstance(repo, str):
        return None
    repo = repo.strip() or DEFAULT_PRIVATE_REPO
    return repo if _REPO_RE.fullmatch(repo) else None


class _GitFail(Exception):
    def __init__(self, stderr):
        self.stderr = stderr or ""
        super().__init__("git failed")


def _git(args, cwd, token):
    """Run git. The token is passed through askpass, not argv, and is not printed.

    Never pass --force. A non-zero exit raises _GitFail with stderr for the retry check.
    """
    if any(arg == "--force" or arg.startswith("--force") for arg in args):
        raise _GitFail("force refused")
    fd, ask = tempfile.mkstemp(prefix="rugger-ask-")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write("#!/bin/sh\nprintf '%s\\n' \"$SIGHTLINE_PRIVATE_TOKEN\"\n")
        os.chmod(ask, 0o700)
        env = dict(os.environ)
        env["SIGHTLINE_PRIVATE_TOKEN"] = token
        env["GIT_ASKPASS"] = ask
        env["GIT_TERMINAL_PROMPT"] = "0"
        proc = subprocess.run(args, cwd=cwd, env=env, timeout=20, capture_output=True)
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace") if proc.stderr else ""
            raise _GitFail(err)
    finally:
        try:
            os.unlink(ask)
        except OSError:
            pass


def _non_fast_forward(text):
    low = (text or "").lower()
    return "non-fast-forward" in low or "[rejected]" in low or "fetch first" in low


def _rebase_and_push(tmp, token):
    """One pull --rebase, then push. One more pull --rebase after a non-fast-forward.

    Never force-pushes. Both desks share the repo and edit different files.
    A repo with no upstream yet is pushed with -u, still without --force.
    """
    def rebase():
        try:
            _git(["git", "pull", "--rebase"], tmp, token)
        except _GitFail as err:
            subprocess.run(["git", "rebase", "--abort"], cwd=tmp, capture_output=True)
            low = err.stderr.lower()
            if "no tracking" in low or "no upstream" in low or "there is no tracking" in low:
                return False
            raise
        return True

    if not rebase():
        _git(["git", "push", "-u", "origin", "HEAD"], tmp, token)
        return
    try:
        _git(["git", "push"], tmp, token)
    except _GitFail as err:
        if not _non_fast_forward(err.stderr):
            raise
        if not rebase():
            _git(["git", "push", "-u", "origin", "HEAD"], tmp, token)
            return
        _git(["git", "push"], tmp, token)


def _github_repo(repo, token):
    """GET /repos/{repo}. The token and the body are not printed."""
    req = urllib.request.Request(
        "https://api.github.com/repos/%s" % repo,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "User-Agent": "sightline-rugger",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        doc = json.loads(resp.read(1_000_000).decode())
    return doc if isinstance(doc, dict) else None


def _repo_is_private(repo, token):
    """False unless the API says private is true. Fail closed. Fixed warning only."""
    try:
        doc = _github_repo(repo, token)
    except Exception:
        doc = None
    if not isinstance(doc, dict) or doc.get("private") is not True:
        warn_not_private()
        return False
    return True


def _clone_private(repo, token):
    tmp = tempfile.mkdtemp(prefix="rugger-private-")
    url = "https://x-access-token@github.com/%s.git" % repo
    try:
        _git(["git", "clone", url, tmp], None, token)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp


def _token():
    token = os.environ.get(TOKEN_ENV, "")
    return token.strip() if isinstance(token, str) else ""


def read_private(desk):
    """(status, doc). status is unset, error, absent, or ok. Does not print the token.

    Refuses a repo that is not private before cloning. Not called from a scan.
    """
    if desk in _remote_read:
        return _remote_read[desk]
    if desk not in ("cardano", "solana"):
        return "unset", None
    token = _token()
    if not token:
        warn_no_token()
        _remote_read[desk] = ("unset", None)
        return _remote_read[desk]
    repo = _private_repo()
    if not repo:
        print("::warning::rugger blacklist: private repo name rejected")
        _remote_read[desk] = ("error", None)
        return _remote_read[desk]
    if not _repo_is_private(repo, token):
        _remote_read[desk] = ("error", None)
        return _remote_read[desk]
    tmp = None
    try:
        tmp = _clone_private(repo, token)
        path = os.path.join(tmp, "rugger", desk, PRIVATE_NAME)
        if not os.path.exists(path):
            _remote_read[desk] = ("absent", None)
            return _remote_read[desk]
        with open(path) as fh:
            doc = json.load(fh)
        if not isinstance(doc, dict):
            raise ValueError("shape")
        _remote_read[desk] = ("ok", doc)
        return _remote_read[desk]
    except Exception:
        print("::warning::rugger blacklist: private repo unread")
        _remote_read[desk] = ("error", None)
        return _remote_read[desk]
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def merge_docs(local, remote):
    """Union of two blacklist documents. The newer last_seen wins; tokens and edges add.

    Either side may be the internal shape or the readable export. A missing side is empty.
    """
    a = _clean_doc(local) if isinstance(local, dict) else _empty()
    b = _clean_doc(remote) if isinstance(remote, dict) else _empty()
    out = _empty()
    for wallet in list(a["entries"]) + [w for w in b["entries"] if w not in a["entries"]]:
        ea = a["entries"].get(wallet)
        eb = b["entries"].get(wallet)
        if ea and not eb:
            out["entries"][wallet] = ea
            continue
        if eb and not ea:
            out["entries"][wallet] = eb
            continue
        evidence = {}
        for tag in set(ea["evidence"]) | set(eb["evidence"]):
            tokens = []
            for src in (ea["evidence"].get(tag) or []), (eb["evidence"].get(tag) or []):
                for token in src:
                    if token not in tokens:
                        tokens.append(token)
            if tokens:
                evidence[tag] = tokens[-MAX_TOKENS:]
        firsts = [t for t in (ea.get("first_seen"), eb.get("first_seen")) if parse_iso(t)]
        lasts = [t for t in (ea.get("last_seen"), eb.get("last_seen")) if parse_iso(t)]
        later = eb if (lasts and eb.get("last_seen") == max(lasts)) else ea
        out["entries"][wallet] = {
            "chain": later.get("chain") if later.get("chain") in CHAINS else "cardano",
            "evidence": evidence,
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(lasts) if lasts else None,
            "funded": [],
        }
    out["edges"] = list(a["edges"]) + list(b["edges"])
    out["exclusions"] = list(a["exclusions"])
    for wallet in b["exclusions"]:
        if wallet not in out["exclusions"]:
            out["exclusions"].append(wallet)
    _dedupe_edges(out)
    _attach_all(out)
    return out


def _cache_parts(cache_dir):
    """(kind, doc) for the local blacklist file. kind is miss, corrupt, or hit."""
    path = os.path.join(cache_dir, PRIVATE_NAME)
    if not os.path.exists(path):
        return "miss", None
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return "corrupt", None
    if not isinstance(doc, dict):
        return "corrupt", None
    return "hit", doc


def _write_cache_doc(cache_dir, doc, public_dir=None):
    path = os.path.join(cache_dir, PRIVATE_NAME)
    if public_dir and _inside(path, public_dir):
        return False
    os.makedirs(cache_dir, exist_ok=True)
    parent = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".rugger-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, separators=(",", ":"))
            fh.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return True


def clear_hold(cache_dir):
    if not cache_dir:
        return
    try:
        os.remove(os.path.join(cache_dir, HOLD_NAME))
    except OSError:
        pass


def _apply_remote(cache_dir, public_dir, local_doc, remote_doc):
    """Write the union and clear a hold. remote_doc None means the remote file is absent."""
    merged = merge_docs(local_doc, remote_doc)
    _write_cache_doc(cache_dir, merged, public_dir)
    clear_hold(cache_dir)
    return merged


def _export_into(tmp, desk, doc):
    rel = os.path.join(tmp, "rugger", desk)
    os.makedirs(rel, exist_ok=True)
    path = os.path.join(rel, PRIVATE_NAME)
    with open(path, "w") as fh:
        json.dump(export_readable(doc, desk), fh, indent=2)
        fh.write("\n")
    return os.path.join("rugger", desk, PRIVATE_NAME)


def _commit_export(tmp, token, desk, rel):
    _git(["git", "add", "--", rel], tmp, token)
    quiet = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp, capture_output=True)
    if quiet.returncode != 0:
        _git(["git", "-c", "user.email=rugger-bot@users.noreply.github.com", "-c", "user.name=rugger-bot",
              "commit", "-q", "-m", "rugger %s" % desk], tmp, token)
    _rebase_and_push(tmp, token)


def _sync_done(cache_dir, public_dir, status, ok):
    write_sync_status(cache_dir, status, public_dir)
    return ok


def sync_cache(cache_dir, desk, public_dir=None):
    """Union the cache with the private repo, then pull --rebase and push.

    A scan does not call this. A successful remote document clears a corrupt file
    and a miss. A push failure leaves the merged cache in place and does not say
    the Actions cache saved the run. Never force-pushes. Writes a hash-free sync
    status into the cache for the next health line.
    """
    if desk not in ("cardano", "solana") or not cache_dir:
        return False
    token = _token()
    if not token:
        warn_no_token()
        return _sync_done(cache_dir, public_dir, "unset", False)
    repo = _private_repo()
    if not repo:
        print("::warning::rugger blacklist: private repo name rejected")
        return _sync_done(cache_dir, public_dir, "error", False)
    if not _repo_is_private(repo, token):
        return _sync_done(cache_dir, public_dir, "refused", False)
    kind, local = _cache_parts(cache_dir)
    tmp = None
    try:
        tmp = _clone_private(repo, token)
        path = os.path.join(tmp, "rugger", desk, PRIVATE_NAME)
        remote = None
        remote_ok = False
        if not os.path.exists(path):
            remote_ok = True
        else:
            try:
                with open(path) as fh:
                    remote = json.load(fh)
                remote_ok = isinstance(remote, dict)
            except (OSError, ValueError):
                remote_ok = False
                remote = None
        if not remote_ok:
            warn_not_updated()
            return _sync_done(cache_dir, public_dir, "error", False)
        if remote is not None or kind == "hit":
            merged = _apply_remote(cache_dir, public_dir, local, remote)
        else:
            merged = None
        if merged is None:
            return _sync_done(cache_dir, public_dir, "ok", True)
        rel = _export_into(tmp, desk, merged)
        _commit_export(tmp, token, desk, rel)
        _remote_read[desk] = ("ok", export_readable(merged, desk))
        return _sync_done(cache_dir, public_dir, "ok", True)
    except Exception:
        warn_not_updated()
        return _sync_done(cache_dir, public_dir, "error", False)
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def push_private(doc, desk):
    """Push one document. Prefer sync_cache, which unions with the remote first."""
    if desk not in ("cardano", "solana"):
        return False
    token = _token()
    if not token:
        warn_no_token()
        return False
    repo = _private_repo()
    if not repo:
        print("::warning::rugger blacklist: private repo name rejected")
        return False
    if not _repo_is_private(repo, token):
        return False
    tmp = None
    try:
        tmp = _clone_private(repo, token)
        path = os.path.join(tmp, "rugger", desk, PRIVATE_NAME)
        remote = None
        if os.path.exists(path):
            with open(path) as fh:
                remote = json.load(fh)
        merged = merge_docs(doc, remote)
        rel = _export_into(tmp, desk, merged)
        _commit_export(tmp, token, desk, rel)
        return True
    except Exception:
        warn_not_updated()
        return False
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)


def open_book(cache_dir, public_dir, cfg=None, exclusions=(), prior=None, desk=None):
    """Private book for this run. None when no cache directory was given.

    Reads the Actions-cache file only. The private repo is merged later by sync_cache.
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
    book.desk = desk if desk in ("cardano", "solana") else None
    book.cache_dir = cache_dir
    book.public_dir = public_dir
    book.hold_for_miss(prior)
    return book


def ada_only_recipients(dev, txs, min_lovelace=5_000_000):
    """ADA-only recipients in a tx whose every non-script input is `dev`.

    Each tx is {time, inputs: [{stake, script, enterprise}], outputs: [{stake, lovelace, assets, script}]}.
    A second non-script payer, a stakeless enterprise co-payer, a script output, a token
    output, or dust is not a hop. A script input (a batcher) does not disqualify the tx.
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
        non_script = [u for u in inputs if not u.get("script")]
        if any(u.get("enterprise") for u in non_script):
            continue
        payers = [u for u in non_script if u.get("stake")]
        if not payers or any(u.get("stake") != dev for u in payers):
            continue
        when = tx.get("time")
        if isinstance(when, bool) or not isinstance(when, (int, float)) or when <= 0:
            when = None
        for out in tx.get("outputs") or []:
            if not isinstance(out, dict) or out.get("script"):
                continue
            stake = out.get("stake")
            if not stake or stake == dev or not wallet_ok(stake) or stake in seen:
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
        stake = row.get("stake")
        live = row.get("live_at_ms")
        if isinstance(live, (int, float)) and not isinstance(live, bool) and live > 0:
            stamp = iso(float(live) / 1000.0)
        else:
            prev = (book.doc.get("entries") or {}).get(stake) if stake else None
            old = prev.get("last_seen") if isinstance(prev, dict) else None
            stamp = old if parse_iso(old) else None
        if book.note_evidence(stake, "cardano", "late_rug", str(unit), stamp):
            n += 1
    return n


HOLD_NAME = "HOLD"
PULLS_NAME = "rugger-pulls.json"
SYNC_NAME = "rugger-sync.json"
SYNC_VALUES = ("ok", "unset", "refused", "error")


def write_sync_status(cache_dir, status, public_dir=None):
    """Record ok, unset, refused, or error. The file has no wallets."""
    if not cache_dir or status not in SYNC_VALUES:
        return False
    path = os.path.join(cache_dir, SYNC_NAME)
    if public_dir and _inside(path, public_dir):
        return False
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w") as fh:
            json.dump({"sync": status}, fh)
            fh.write("\n")
    except OSError:
        return False
    return True


def read_sync_status(cache_dir, public_dir=None):
    """The enum from the cache, or None. A file with anything else is ignored."""
    if not cache_dir:
        return None
    path = os.path.join(cache_dir, SYNC_NAME)
    if public_dir and _inside(path, public_dir):
        return None
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        return None
    if isinstance(doc, dict) and set(doc) == {"sync"} and doc.get("sync") in SYNC_VALUES:
        return doc["sync"]
    return None


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
    """Mark a missed rugger file. Other files in the cache directory still save.

    RUGGER_ACCEPT_RESET=1 removes the mark instead of writing it.
    """
    if not cache_dir or book is None:
        return
    if accept_reset():
        clear_hold(cache_dir)
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
        if isinstance(row, dict) and wallet_ok(row.get("stake")) and _token_ok(str(unit)):
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
