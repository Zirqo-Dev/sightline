#!/usr/bin/env python3
"""UA-F1 weekly runner (prereg §1-§10).

Research only, paper only, not financial advice. Evidence label: WEAK.
No trading, no keys, no wallets, no spend. It is an avoid signal only
("don't hold or buy this token from T-30 to T+0"), not a trading book.

.github/workflows/ua-f1.yml schedules Mondays at 01:00 UTC. That clock
time is a target, not a guarantee: GitHub cron can lag or drop a week,
and the next run fills every missed Monday slot. A scheduled run writes
only under --data-dir (the ua-f1-data branch). It never pushes to main.

Stdlib only, except numpy for the §5 twin draw:
    numpy.random.default_rng(20261125 + episode_index)
The workflow pins numpy==2.5.4. If it is missing locally:
    pip install numpy==2.5.4
Do not invent a substitute RNG; registration of a new episode must use
that exact Generator. The draw is:

    rng = numpy.random.default_rng(20261125 + episode_index)
    s = clean_days[int(rng.integers(0, len(clean_days)))]

where clean_days is a sorted list of datetime.date, and episode_index is
registration order (0-based), with same-run ties broken by symbol.

Reads the locked prereg and S0 tree next to this file. Writes run output
under --data-dir when set, otherwise under this directory. Never reads
v6.5, race, desk or S2-test files. Never calls api.llama.fi. Never writes
Notion. Until judgment, status and logs are counts-only: no means, t or
win rates.

Before 2026-12-11 this job only snapshots, registers and caches prices.
Judgment (§7) refuses until 30 usable episodes are resolved.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import hashlib
import zipfile
import io
import csv
import datetime
import urllib.request
import urllib.error
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
# Writable outputs. The scheduled job points DATA at the ua-f1-data
# worktree. The locked prereg and S0 tree stay under ROOT.
DATA = ROOT
PREREG = ROOT / "prereg_ua_f1.md"
PREREG_SHA = ROOT / "prereg_ua_f1.sha256"
RUN_LOG = DATA / "run_log.jsonl"
DEVIATIONS = DATA / "deviations.md"
EPISODES = DATA / "episodes.jsonl"
STATUS = DATA / "status.json"
RAW = DATA / "raw"


def refresh_output_paths() -> None:
    """Point writable files at DATA. Locked inputs stay under ROOT."""
    global RUN_LOG, DEVIATIONS, EPISODES, STATUS, RAW
    RUN_LOG = DATA / "run_log.jsonl"
    DEVIATIONS = DATA / "deviations.md"
    EPISODES = DATA / "episodes.jsonl"
    STATUS = DATA / "status.json"
    RAW = DATA / "raw"


def set_data_dir(path: Path) -> None:
    global DATA
    DATA = Path(path).resolve()
    refresh_output_paths()


def seed_run_log() -> None:
    """Copy the locked run log into an empty data dir. Do not edit the original."""
    if DATA.resolve() == ROOT.resolve() or RUN_LOG.exists():
        return
    src = ROOT / "run_log.jsonl"
    if not src.is_file():
        return
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    RUN_LOG.write_bytes(src.read_bytes())
LOCK_DATE = datetime.date(2026, 11, 11)
EARLIEST_CLIFF = LOCK_DATE + datetime.timedelta(days=30)  # 2026-12-11
HARD_STOP = datetime.date(2026, 11, 11) + datetime.timedelta(days=14 * 30 + 14)
# 14 months after lock: 2028-01-11
HARD_STOP = datetime.date(2028, 1, 11)
PREREG_HASH = "954dd472193c2bd7095225fd18b868a31ba3c6d0808888fd7b387e2ec7ebca95"

DATASETS = "https://defillama-datasets.llama.fi"
GT_API = "https://api.geckoterminal.com/api/v2"
BN_VISION = "https://data.binance.vision/data/spot/daily/klines"

# Keyless hosts only. No keys and no paid APIs. api.llama.fi is never used.
# CoinGecko is not on this list: an origin that cannot be confirmed from
# these hosts fails closed, and the manifest included list stays in force.
ALLOWED_HOSTS = {
    "defillama-datasets.llama.fi",
    "api.geckoterminal.com",
    "data.binance.vision",
}
FORBIDDEN_HOSTS = {"api.llama.fi"}

# "solana" is the native L1 coin, not an SPL mint. It stays an explicit
# exclusion even if a later manifest lists it. Every other name, including
# helium-network, comes only from the manifest included list.
SOLANA_EXCLUSION = "solana"

# --dry-run sets this false. http_get then refuses before any socket.
NETWORK = True

QUOTE_NAMES = {
    "sol", "wsol", "usdc", "usdt",
    "so11111111111111111111111111111111111111112",
    "epjfwdd5aufqssqem2qn1xzybapc8g4weggkzwjtj1a",
    "es9vmfrzacermjfrf4h2fyd4kconky11mcce8benwnyb",
}

CTX = ssl.create_default_context()
UA = "UA-F1-weekly/1.0 (keyless; research; zero-spend)"

# quote mint aliases used when picking the main pool
WSOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSQEm2qn1xzybapC8G4wEGGkZwyTDt1v"
USDT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
PREFERRED_QUOTES = {WSOL.lower(), USDC.lower(), USDT.lower(), "sol", "wsol", "usdc", "usdt"}


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_utc() -> datetime.date:
    return datetime.datetime.now(datetime.timezone.utc).date()


def log(event: str, **kw) -> None:
    rec = {"ts": utcnow(), "event": event, **kw}
    with RUN_LOG.open("a") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def append_deviation(text: str) -> None:
    """Prereg §1: every deviation is appended to deviations.md BEFORE acting."""
    header = "" if DEVIATIONS.exists() else "# UA-F1 deviations\n\n"
    with DEVIATIONS.open("a") as f:
        if header:
            f.write(header)
        f.write(f"## {utcnow()}\n\n{text.rstrip()}\n\n")


def assert_inside_root(path: Path) -> Path:
    """Refuse any read/write outside the locked tree or the data dir."""
    path = path.resolve()
    roots = [ROOT.resolve()]
    data = DATA.resolve()
    if data not in roots:
        roots.append(data)
    for root in roots:
        if path == root or str(path).startswith(str(root) + os.sep):
            return path
    raise RuntimeError(f"refusing path outside ua_f1 outputs: {path}")


def disable_network() -> None:
    global NETWORK
    NETWORK = False


def host_allowed(host: str) -> bool:
    """True only for the keyless allow-list. api.llama.fi is never allowed."""
    host = (host or "").lower().rstrip(".")
    if host in FORBIDDEN_HOSTS:
        return False
    if host.endswith(".llama.fi") and host != "defillama-datasets.llama.fi":
        return False
    return host in ALLOWED_HOSTS


class _AllowListRedirect(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only when the next host is also on the allow-list."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlparse(newurl).hostname or ""
        if not host_allowed(host):
            raise urllib.error.HTTPError(
                newurl, code, f"redirect host refused: {host}", headers, fp,
            )
        return urllib.request.HTTPRedirectHandler.redirect_request(
            self, req, fp, code, msg, headers, newurl,
        )


_OPENER = urllib.request.build_opener(
    urllib.request.HTTPSHandler(context=CTX),
    _AllowListRedirect,
)


def http_get(url: str, dest: Path | None = None, timeout: int = 60, retries: int = 4):
    """Keyless GET. Never used for api.llama.fi. Returns (body_or_none, meta)."""
    if not NETWORK:
        raise RuntimeError(f"network disabled, refusing {url}")
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return None, {"url": url, "ok": False, "error": "https required", "status": None}
    host = parsed.hostname or ""
    if not host_allowed(host):
        return None, {"url": url, "ok": False, "error": f"host not on allow-list: {host}", "status": None}
    last = None
    for i in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with _OPENER.open(req, timeout=timeout) as resp:
                body = resp.read()
                meta = {
                    "url": url,
                    "ok": True,
                    "status": resp.status,
                    "content_type": resp.headers.get("Content-Type"),
                    "bytes": len(body),
                    "ts": utcnow(),
                }
                if dest is not None:
                    dest = assert_inside_root(dest)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(body)
                return body, meta
        except urllib.error.HTTPError as e:
            last = {"url": url, "ok": False, "status": e.code, "reason": e.reason, "ts": utcnow()}
            if e.code == 429:
                # §9: on a 429, back off and retry next week
                last["retry_next_week"] = True
                return None, last
            if e.code in (404, 401, 403, 402):
                return None, last
            time.sleep(1.5 * (i + 1))
        except Exception as e:
            last = {"url": url, "ok": False, "status": None, "error": f"{type(e).__name__}: {e}", "ts": utcnow()}
            time.sleep(1.5 * (i + 1))
    return None, last


# ---------------------------------------------------------------------------
# §1 pre-run integrity
# ---------------------------------------------------------------------------

def prereg_check(dry_run: bool = False) -> tuple[bool, str]:
    """Run sha256sum -c before any fetch. Log the full output unless dry-run."""
    proc = subprocess.run(
        ["sha256sum", "-c", str(PREREG_SHA.name)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    ok = proc.returncode == 0
    if dry_run:
        return ok, output
    log(
        "prereg_check",
        cmd="sha256sum -c prereg_ua_f1.sha256",
        output=output,
        exit_code=proc.returncode,
        ok=ok,
    )
    if not ok:
        write_status("PAUSED", note="§1 prereg check failed; no further action until Challenger rules.")
    return ok, output


def snapshot_sha_check(dry_run: bool = False) -> tuple[bool, str]:
    """Verify S0.sha256 and S0_addendum1.sha256. Fail closed on any FAIL."""
    chunks = []
    ok = True
    for name in ("S0.sha256", "S0_addendum1.sha256"):
        proc = subprocess.run(
            ["sha256sum", "-c", name],
            cwd=str(locked_snapshots()),
            capture_output=True,
            text=True,
        )
        output = ((proc.stdout or "") + (proc.stderr or "")).strip()
        chunks.append(output)
        if proc.returncode != 0 or "FAILED" in output.split():
            ok = False
    text = "\n".join(chunks)
    if dry_run:
        return ok, text
    log(
        "snapshot_sha_check",
        cmd="sha256sum -c S0.sha256 && sha256sum -c S0_addendum1.sha256",
        output=text,
        ok=ok,
    )
    if not ok:
        write_status("PAUSED", note="Snapshot checksum failed. No further action.")
    return ok, text


# ---------------------------------------------------------------------------
# Snapshot helpers (§3)
# ---------------------------------------------------------------------------

def locked_snapshots() -> Path:
    return ROOT / "snapshots"


def writable_snapshots() -> Path:
    return DATA / "snapshots"


def _sid_key(name: str) -> tuple:
    try:
        return (0, int(name[1:]), name)
    except ValueError:
        return (1, 0, name)


def snapshot_path(sid: str) -> Path:
    """S0 is always the locked tree. Later snapshots prefer the data dir."""
    if sid == "S0":
        return locked_snapshots() / "S0"
    writable = writable_snapshots() / sid
    if writable.is_dir():
        return writable
    return locked_snapshots() / sid


def snapshot_ids() -> list[str]:
    found = set()
    for base in (locked_snapshots(), writable_snapshots()):
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if p.is_dir() and p.name.startswith("S") and (p / "emissions").is_dir():
                found.add(p.name)
    return sorted(found, key=_sid_key)


def next_snapshot_id() -> str:
    nums = []
    for sid in snapshot_ids():
        try:
            nums.append(int(sid[1:]))
        except ValueError:
            continue
    n = (max(nums) + 1) if nums else 0
    return f"S{n}"


def load_manifest(sid: str) -> dict:
    p = snapshot_path(sid) / "manifest.json"
    if not p.is_file():
        return {}
    return json.loads(assert_inside_root(p).read_text())


def included_protocols(manifest: dict) -> list[str] | None:
    """The manifest's included list, or None when this file has no such list."""
    inc = manifest.get("included")
    if isinstance(inc, dict) and isinstance(inc.get("protocols"), list):
        return [x for x in inc["protocols"] if isinstance(x, str)]
    if isinstance(inc, list) and all(isinstance(x, str) for x in inc):
        return list(inc)
    protocols = manifest.get("protocols")
    if isinstance(protocols, list) and all(isinstance(x, str) for x in protocols):
        return list(protocols)
    return None


def _addendum_sort_key(path: Path) -> tuple:
    rest = path.stem[len("manifest_addendum"):] if path.stem.startswith("manifest_addendum") else path.stem
    try:
        return (0, int(rest), path.name)
    except ValueError:
        return (1, 0, path.name)


def universe_manifest_paths(locked: Path | None = None, writable: Path | None = None) -> list[Path]:
    """Lock manifest, then addenda, then monthly snapshots. Later files override."""
    locked = locked if locked is not None else locked_snapshots()
    writable = writable if writable is not None else writable_snapshots()
    paths: list[Path] = []
    s0 = locked / "S0"
    lock = s0 / "manifest.json"
    if lock.is_file():
        paths.append(lock)
    if s0.is_dir():
        addenda = [p for p in s0.glob("manifest_addendum*.json") if p.is_file()]
        paths.extend(sorted(addenda, key=_addendum_sort_key))
    by_sid: dict[str, Path] = {}
    bases = [locked]
    if writable.resolve() != locked.resolve():
        bases.append(writable)
    for base in bases:
        if not base.is_dir():
            continue
        for p in base.iterdir():
            if not p.is_dir() or not p.name.startswith("S") or p.name == "S0":
                continue
            man = p / "manifest.json"
            if man.is_file():
                by_sid[p.name] = man
    for name in sorted(by_sid, key=_sid_key):
        paths.append(by_sid[name])
    return paths


def load_universe(locked: Path | None = None, writable: Path | None = None) -> list[str]:
    """Latest manifest included list. That list is the one source of truth.

    Order: S0/manifest.json, then S0/manifest_addendumN.json in numeric
    order, then monthly snapshots S1, S2, ... A later file that has an
    included list replaces the list from the file before it. "solana" is
    always removed. helium-network is included when the manifest says so.
    """
    chosen: list[str] | None = None
    for path in universe_manifest_paths(locked, writable):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        protocols = included_protocols(data)
        if protocols is not None:
            chosen = protocols
    if not chosen:
        return []
    out: list[str] = []
    for name in chosen:
        # "solana" is the native L1 coin, not an SPL mint. Explicit exclusion.
        if name == SOLANA_EXCLUSION:
            continue
        if name not in out:
            out.append(name)
    return out


def protocols_in_snapshot(sid: str) -> list[str]:
    em = snapshot_path(sid) / "emissions"
    if not em.is_dir():
        return []
    allowed = set(load_universe())
    return sorted(p.stem for p in em.glob("*.json") if p.stem in allowed)


def sha_snapshot(sid: str) -> Path:
    """Write snapshots/<sid>.sha256 for a new monthly snapshot. Never touches S0."""
    if sid == "S0":
        raise RuntimeError("refusing to rewrite locked S0 checksums")
    snap_dir = assert_inside_root(writable_snapshots() / sid)
    sha_path = assert_inside_root(writable_snapshots() / f"{sid}.sha256")
    lines = []
    files = []
    for dirpath, dirnames, filenames in os.walk(snap_dir):
        dirnames.sort()
        for fn in sorted(filenames):
            full = Path(dirpath) / fn
            rel = full.relative_to(writable_snapshots())
            files.append((str(rel), full))
    for rel, full in files:
        h = hashlib.sha256(full.read_bytes()).hexdigest()
        lines.append(f"{h}  {rel}")
    sha_path.write_text("\n".join(lines) + "\n")
    return sha_path


def take_monthly_snapshot(dry_run: bool = False) -> str | None | bool:
    """Take Sn if this month has no snapshot.

    Weekly cadence is Mondays 01:00 UTC, so when the 1st is not a Monday the
    snapshot is taken on the first run of the month (best effort). S1 is due
    00:00 UTC on 1 Dec 2026; a run on or after that date takes it.
    Returns the new id, None when no snapshot is due, or False on a failed fetch.
    """
    today = today_utc()
    month_tag = today.strftime("%Y-%m")
    for sid in snapshot_ids():
        if sid == "S0":
            continue
        man = load_manifest(sid)
        taken = (man.get("taken_at") or "")[:7]
        if taken == month_tag:
            return None
    # S0 is the lock snapshot (November 2026). Later months get S1, S2, ...
    if today.year == 2026 and today.month == 11:
        return None
    sid = next_snapshot_id()
    if dry_run:
        print(f"dry-run: would take monthly snapshot {sid} for {month_tag}")
        return sid

    # Confirm the datasets bucket is still keyless HTTP 200, then refresh
    # schedules for the manifest universe only.
    body, meta = http_get(f"{DATASETS}/emissionsProtocolsList")
    if not meta.get("ok") or meta.get("status") != 200:
        append_deviation(f"monthly snapshot {sid}: datasets bucket not 200 keyless: {meta}")
        log("bucket_check", ok=False, **{k: meta.get(k) for k in ("url", "status", "error", "reason")})
        return False
    try:
        json.loads(body)
    except json.JSONDecodeError:
        append_deviation(f"monthly snapshot {sid}: emissions list was not JSON")
        log("bucket_check", ok=False, error="not_json")
        return False
    log("bucket_check", keyless=True, api_llama_fi_used=False, status=200, url=f"{DATASETS}/emissionsProtocolsList")
    dest_root = assert_inside_root(writable_snapshots() / sid)
    (dest_root / "emissions").mkdir(parents=True, exist_ok=True)
    (dest_root / "emissionsProtocolsList.json").write_bytes(body)

    # The manifest included list is the source of truth. "solana" is
    # excluded inside load_universe. There is no hand-written exclusion
    # tuple: helium-network is included when the addendum lists it, and a
    # name that is not on the included list is not fetched.
    universe = list(load_universe())
    if not universe:
        append_deviation(f"monthly snapshot {sid}: manifest included list is empty; not taking a snapshot.")
        log("monthly_snapshot", snapshot_id=sid, ok=False, reason="empty_universe")
        return False

    copied = []
    for name in universe:
        b, m = http_get(
            f"{DATASETS}/emissions/{name}",
            dest=dest_root / "emissions" / f"{name}.json",
        )
        copied.append({"protocol": name, "ok": bool(m.get("ok")), "status": m.get("status")})
    if not all(row["ok"] for row in copied):
        append_deviation(
            f"monthly snapshot {sid}: one or more emissions files failed; "
            "not publishing a partial snapshot. The slot stays open for catch-up."
        )
        log("monthly_snapshot", snapshot_id=sid, ok=False, n=len(universe), fetch=copied)
        shutil.rmtree(dest_root, ignore_errors=True)
        return False

    manifest = {
        "snapshot_id": sid,
        "taken_at": utcnow(),
        "prereg_sha256": PREREG_HASH,
        "prices_fetched": False,
        "included": {"n": len(universe), "protocols": list(universe)},
        "protocols": list(universe),
        "added_since_previous": [],
        "fetch": copied,
        "note": "Monthly snapshot. Sha'd before use. Universe is the manifest included list. No prices, no scoring.",
    }
    (dest_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    sha_path = sha_snapshot(sid)
    try:
        sha_rel = str(sha_path.relative_to(DATA))
    except ValueError:
        sha_rel = str(sha_path)
    log("monthly_snapshot", snapshot_id=sid, n=len(universe), added=[], sha256_path=sha_rel)
    return sid


def latest_snapshot_on_or_before(day: datetime.date) -> str | None:
    """Return the newest snapshot whose taken_at date is <= day."""
    best = None
    best_d = None
    for sid in snapshot_ids():
        man = load_manifest(sid)
        taken = man.get("taken_at")
        if not taken and sid == "S0":
            d = LOCK_DATE
        else:
            try:
                d = datetime.date.fromisoformat(taken[:10])
            except Exception:
                continue
        if d <= day and (best_d is None or d > best_d or (d == best_d and sid > (best or ""))):
            best, best_d = sid, d
    return best


# ---------------------------------------------------------------------------
# Cliff calendar from a snapshot (batch-3 2% proxy, TGE excluded)
# ---------------------------------------------------------------------------

def _as_date(ts: int) -> datetime.date:
    return datetime.datetime.fromtimestamp(int(ts), tz=datetime.timezone.utc).date()


def cumulative_unlocked(doc: dict) -> dict[int, float]:
    by_ts: dict[int, float] = defaultdict(float)
    for series in (doc.get("data") or []):
        for pt in series.get("data") or []:
            try:
                by_ts[int(pt["timestamp"])] += float(pt.get("unlocked") or 0)
            except (TypeError, ValueError, KeyError):
                continue
    return dict(by_ts)


def prior_supply(cum: dict[int, float], ts: int) -> float:
    prior = 0.0
    for t in sorted(cum):
        if t < ts:
            prior = cum[t]
        else:
            break
    return prior


def cliffs_from_protocol(data: dict) -> list[dict]:
    events = (data.get("metadata") or {}).get("events") or []
    cum = cumulative_unlocked(data.get("documentedData") or {})
    out = []
    for ev in events:
        if ev.get("unlockType") != "cliff":
            continue
        try:
            ts = int(ev["timestamp"])
        except (TypeError, ValueError, KeyError):
            continue
        tokens = ev.get("noOfTokens")
        try:
            amt = float(tokens[0]) if isinstance(tokens, list) else float(tokens or 0)
        except (TypeError, ValueError):
            continue
        prior = prior_supply(cum, ts)
        if prior <= 0:
            continue  # TGE / no prior documented-unlock supply
        if amt / prior >= 0.02:
            out.append({
                "ts": ts,
                "date": _as_date(ts).isoformat(),
                "amount": amt,
                "prior": prior,
                "category": ev.get("category"),
            })
    out.sort(key=lambda x: x["ts"])
    return out


def token_identity(data: dict, protocol: str) -> dict:
    meta = data.get("metadata") or {}
    token = meta.get("token") or data.get("token")
    gid = data.get("gecko_id")
    mint = None
    if isinstance(token, str) and token.startswith("solana:"):
        mint = token.split(":", 1)[1]
    return {
        "protocol": protocol,
        "token": token,
        "gecko_id": gid,
        "mint": mint,
        "name": data.get("name"),
        "symbol": None,
    }


def merge_cliffs(cliffs: list[dict]) -> list[dict]:
    """Same token within 30 days: merge from T-30 of the first to T+0 of the last."""
    if not cliffs:
        return []
    groups = []
    cur = [cliffs[0]]
    for c in cliffs[1:]:
        last = datetime.date.fromisoformat(cur[-1]["date"])
        this = datetime.date.fromisoformat(c["date"])
        if (this - last).days <= 30:
            cur.append(c)
        else:
            groups.append(cur)
            cur = [c]
    groups.append(cur)
    episodes = []
    for g in groups:
        first = datetime.date.fromisoformat(g[0]["date"])
        last = datetime.date.fromisoformat(g[-1]["date"])
        episodes.append({
            "cliff_dates": [x["date"] for x in g],
            "cliff_amounts": [x["amount"] for x in g],
            "t0": last.isoformat(),
            "t30": (first - datetime.timedelta(days=30)).isoformat(),
        })
    return episodes


def candidate_episodes(sid: str) -> list[dict]:
    """Build not-yet-registered countable episode candidates from a snapshot."""
    out = []
    for protocol in protocols_in_snapshot(sid):
        path = assert_inside_root(snapshot_path(sid) / "emissions" / f"{protocol}.json")
        data = json.loads(path.read_text())
        ident = token_identity(data, protocol)
        merged = merge_cliffs(cliffs_from_protocol(data))
        tge_days = tge_dates(data)
        for ep in merged:
            t30 = datetime.date.fromisoformat(ep["t30"])
            t0 = datetime.date.fromisoformat(ep["t0"])
            if t30 < LOCK_DATE:
                continue
            if t0 < EARLIEST_CLIFF:
                continue
            # Must be in a snapshot taken at or before T-30
            snap = latest_snapshot_on_or_before(t30)
            if snap is None:
                continue
            out.append({
                **ep,
                **ident,
                "snapshot_id": snap,
                "tge_dates": [d.isoformat() for d in tge_days],
            })
    return out


def tge_dates(data: dict) -> list[datetime.date]:
    """Days that look like a TGE (cliff with no prior documented supply)."""
    events = (data.get("metadata") or {}).get("events") or []
    cum = cumulative_unlocked(data.get("documentedData") or {})
    days = []
    for ev in events:
        if ev.get("unlockType") != "cliff":
            continue
        try:
            ts = int(ev["timestamp"])
        except (TypeError, ValueError, KeyError):
            continue
        if prior_supply(cum, ts) <= 0:
            days.append(_as_date(ts))
    return days


def all_two_pct_cliff_dates(data: dict) -> set[datetime.date]:
    return {datetime.date.fromisoformat(c["date"]) for c in cliffs_from_protocol(data)}


# ---------------------------------------------------------------------------
# §5 random twin
# ---------------------------------------------------------------------------

def load_numpy_rng(episode_index: int):
    try:
        import numpy
    except ImportError as e:
        raise SystemExit(
            "numpy is required to draw the §5 twin. On this box run: pip install numpy\n"
            "The draw must be numpy.random.default_rng(20261125 + episode_index); "
            "do not substitute another RNG.\n"
            f"ImportError: {e}"
        )
    return numpy.random.default_rng(20261125 + episode_index)


def clean_start_days(data: dict, t30: datetime.date, t0: datetime.date) -> list[datetime.date]:
    """s in [max(lock, T-90), T+30] such that no >=2% cliff and no TGE in [s-30, s+60]."""
    lo = max(LOCK_DATE, t30 - datetime.timedelta(days=60))  # T-90 relative to T0 = t30+30, so t30-60
    # T-90 from the episode's T0: T0 = t30+30, T-90 = t30-60. The prereg says
    # s is drawn from [max(lock date, T-90), T+30]. T is the episode T+0.
    lo = max(LOCK_DATE, t0 - datetime.timedelta(days=90))
    hi = t0 + datetime.timedelta(days=30)
    cliffs = all_two_pct_cliff_dates(data)
    tges = set(tge_dates(data))
    blocked = cliffs | tges
    days = []
    d = lo
    while d <= hi:
        window_lo = d - datetime.timedelta(days=30)
        window_hi = d + datetime.timedelta(days=60)
        k = window_lo
        clean = True
        while k <= window_hi:
            if k in blocked:
                clean = False
                break
            k += datetime.timedelta(days=1)
        if clean:
            days.append(d)
        d += datetime.timedelta(days=1)
    return days


def draw_twin(data: dict, t30: datetime.date, t0: datetime.date, episode_index: int):
    days = clean_start_days(data, t30, t0)
    if not days:
        return None, days
    rng = load_numpy_rng(episode_index)
    idx = int(rng.integers(0, len(days)))
    s = days[idx]
    return {
        "twin_start": s.isoformat(),
        "twin_end": (s + datetime.timedelta(days=30)).isoformat(),
        "n_clean_days": len(days),
        "draw_index": idx,
        "seed": 20261125 + episode_index,
        "episode_index": episode_index,
    }, days


# ---------------------------------------------------------------------------
# §2 price cache (no scoring written)
# ---------------------------------------------------------------------------

def cache_path(*parts: str) -> Path:
    p = RAW.joinpath(*parts)
    return assert_inside_root(p)


def cache_binance_sol(day: datetime.date, dry_run: bool = False) -> bool:
    """Cache Binance SOLUSDT daily close (data.binance.vision), UTC day."""
    name = f"SOLUSDT-1d-{day.isoformat()}.zip"
    dest = cache_path("binance", "SOLUSDT", name)
    if dest.exists():
        return True
    if dry_run:
        print(f"dry-run: would fetch {name}")
        return False
    url = f"{BN_VISION}/SOLUSDT/1d/{name}"
    body, meta = http_get(url, dest=dest)
    if not body:
        if dest.exists():
            dest.unlink()
        log("price_cache", source="binance", symbol="SOLUSDT", day=day.isoformat(), ok=False, status=meta.get("status"), retry_next_week=meta.get("retry_next_week", False))
        return False
    log("price_cache", source="binance", symbol="SOLUSDT", day=day.isoformat(), ok=True, bytes=meta.get("bytes"))
    return True


def cache_binance_token(symbol: str, day: datetime.date, dry_run: bool = False) -> bool:
    name = f"{symbol}USDT-1d-{day.isoformat()}.zip"
    dest = cache_path("binance", symbol + "USDT", name)
    if dest.exists():
        return True
    if dry_run:
        print(f"dry-run: would fetch {name}")
        return False
    url = f"{BN_VISION}/{symbol}USDT/1d/{name}"
    body, meta = http_get(url, dest=dest)
    if not body:
        if dest.exists():
            dest.unlink()
        log("price_cache", source="binance", symbol=f"{symbol}USDT", day=day.isoformat(), ok=False, status=meta.get("status"), retry_next_week=meta.get("retry_next_week", False))
        return False
    log("price_cache", source="binance", symbol=f"{symbol}USDT", day=day.isoformat(), ok=True, bytes=meta.get("bytes"))
    return True


def cache_geckoterminal_ohlcv(network: str, pool: str, dry_run: bool = False) -> bool:
    dest = cache_path("geckoterminal", network, f"{pool}_ohlcv_day_{today_utc().isoformat()}.json")
    if dry_run:
        print(f"dry-run: would fetch GT ohlcv pool={pool}")
        return False
    url = f"{GT_API}/networks/{network}/pools/{pool}/ohlcv/day?aggregate=1&limit=1000"
    body, meta = http_get(url, dest=dest)
    if not body:
        log("price_cache", source="geckoterminal", pool=pool, ok=False, status=meta.get("status"), retry_next_week=meta.get("retry_next_week", False))
        return False
    log("price_cache", source="geckoterminal", pool=pool, ok=True, bytes=meta.get("bytes"))
    return True


def pick_main_pool(mint: str, registration_day: datetime.date, dry_run: bool = False):
    """Main pool = first SOL/WSOL, USDC or USDT pool with bars on registration day."""
    dest = cache_path("geckoterminal", "solana", f"pools_{mint}.json")
    if dry_run:
        print(f"dry-run: would list GT pools for mint {mint[:8]}…")
        return None
    if not dest.exists():
        url = f"{GT_API}/networks/solana/tokens/{mint}/pools"
        body, meta = http_get(url, dest=dest)
        if not body:
            log("pool_lookup", mint=mint, ok=False, status=meta.get("status"), retry_next_week=meta.get("retry_next_week", False))
            return None
    try:
        payload = json.loads(dest.read_text())
    except Exception:
        return None
    pools = payload.get("data") or []
    for item in pools:
        attrs = item.get("attributes") or {}
        rel = item.get("relationships") or {}
        name = (attrs.get("name") or "").lower()
        # prefer a name that includes SOL/WSOL/USDC/USDT
        if not any(q in name.split("/") or q in name for q in ("sol", "wsol", "usdc", "usdt")):
            # also accept if quote token address is known
            quote = (
                ((rel.get("quote_token") or {}).get("data") or {}).get("id") or ""
            ).lower()
            if not any(q in quote for q in PREFERRED_QUOTES):
                continue
        pool_id = attrs.get("address") or item.get("id") or ""
        if pool_id.startswith("solana_"):
            pool_id = pool_id.split("_", 1)[1]
        if pool_id:
            return {"network": "solana", "pool": pool_id, "name": attrs.get("name")}
    return None


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def load_episodes() -> list[dict]:
    if not EPISODES.exists():
        return []
    out = []
    for line in EPISODES.read_text().splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def episode_key(ep: dict) -> tuple:
    return (ep.get("protocol"), ep.get("t30"), ep.get("t0"), tuple(ep.get("cliff_dates") or []))


def symbol_of(ep: dict) -> str:
    return (ep.get("symbol") or ep.get("gecko_id") or ep.get("protocol") or "").lower()


def resolve_price_source(ep: dict, dry_run: bool = False) -> dict:
    """Fix the price source at registration. Never switch later."""
    mint = ep.get("mint")
    day = datetime.date.fromisoformat(ep["t30"])
    if mint:
        pool = pick_main_pool(mint, day, dry_run=dry_run)
        if pool:
            return {"kind": "geckoterminal", "network": pool["network"], "pool": pool["pool"], "mint": mint}
    # Binance TOKENUSDT fallback, only if a ticker can be inferred later; leave
    # the fixed source recorded as binance_pending_symbol so a later run cannot
    # silently switch to a different venue. Symbol is taken from gecko_id/name
    # only when an explicit mapping exists on the episode.
    symbol = (ep.get("binance_symbol") or "").upper()
    if symbol:
        return {"kind": "binance", "symbol": symbol}
    if mint:
        return {"kind": "unresolved", "reason": "no_gt_pool_and_no_binance_symbol", "mint": mint}
    return {"kind": "unresolved", "reason": "no_mint_and_no_binance_symbol"}


def register_due_episodes(dry_run: bool = False) -> list[dict]:
    today = today_utc()
    existing = load_episodes()
    have = {episode_key(e) for e in existing}
    # Candidates from the latest usable snapshot at or before today
    cands = []
    for sid in snapshot_ids():
        cands.extend(candidate_episodes(sid))
    # unique by key, prefer earlier snapshot
    uniq = {}
    for c in cands:
        k = episode_key(c)
        if k not in uniq:
            uniq[k] = c
    due = []
    for c in uniq.values():
        if episode_key(c) in have:
            continue
        t30 = datetime.date.fromisoformat(c["t30"])
        if t30 > today:
            continue  # first weekly run on or after T-30
        due.append(c)
    # Registration order: already-registered keep their index. New ones are
    # appended in (t30, symbol) order so ties break by symbol.
    due.sort(key=lambda e: (e["t30"], symbol_of(e), e.get("protocol"), e["t0"]))
    if dry_run:
        print(f"dry-run: {len(due)} episode(s) would be registered (not written)")
        for e in due:
            print(f"  {e['protocol']} T-30={e['t30']} T+0={e['t0']} cliffs={e['cliff_dates']}")
        return due

    next_index = len(existing)
    written = []
    for c in due:
        sid = c["snapshot_id"]
        path = assert_inside_root(snapshot_path(sid) / "emissions" / f"{c['protocol']}.json")
        data = json.loads(path.read_text())
        twin, clean = draw_twin(
            data,
            datetime.date.fromisoformat(c["t30"]),
            datetime.date.fromisoformat(c["t0"]),
            next_index,
        )
        price_source = resolve_price_source(c, dry_run=False)
        rec = {
            "registered_at": utcnow(),
            "episode_index": next_index,
            "snapshot_id": sid,
            "protocol": c["protocol"],
            "token": c.get("token"),
            "gecko_id": c.get("gecko_id"),
            "mint": c.get("mint"),
            "symbol": c.get("symbol") or c.get("gecko_id") or c["protocol"],
            "cliff_dates": c["cliff_dates"],
            "cliff_amounts": c["cliff_amounts"],
            "t30": c["t30"],
            "t0": c["t0"],
            "price_source": price_source,
            "twin": twin if twin else None,
            "unusable_reason": None if twin else "no_clean_twin_start_day",
            "blocked": False,
            "blocked_reason": None,
            "schedule_changed": False,
            "status": "registered",
            # meme flag is fixed at registration from CoinGecko/Jupiter if known
            "meme_at_registration": None,
        }
        with EPISODES.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        log(
            "episode_registered",
            episode_index=next_index,
            protocol=c["protocol"],
            t30=c["t30"],
            t0=c["t0"],
            snapshot_id=sid,
            twin_drawn=bool(twin),
            unusable_reason=rec["unusable_reason"],
        )
        written.append(rec)
        next_index += 1
    return written


def flag_schedule_changes(dry_run: bool = False) -> int:
    """Compare registered episodes to later snapshots; log schedule_changed only."""
    existing = load_episodes()
    latest = snapshot_ids()[-1] if snapshot_ids() else None
    if not latest:
        return 0
    n = 0
    # Read-only comparison; do not mutate frozen episode fields other than
    # appending a schedule_changed marker via a sidecar log.
    changed_path = assert_inside_root(ROOT / "schedule_changed.jsonl")
    already = set()
    if changed_path.exists():
        for line in changed_path.read_text().splitlines():
            if line.strip():
                already.add(json.loads(line).get("episode_index"))
    for ep in existing:
        if ep.get("episode_index") in already:
            continue
        proto = ep["protocol"]
        later = snapshot_path(latest) / "emissions" / f"{proto}.json"
        if not later.exists():
            continue
        data = json.loads(later.read_text())
        merged = merge_cliffs(cliffs_from_protocol(data))
        key = (tuple(ep.get("cliff_dates") or []), ep.get("t0"))
        still = any(tuple(m["cliff_dates"]) == tuple(ep.get("cliff_dates") or []) and m["t0"] == ep.get("t0") for m in merged)
        if still:
            continue
        rec = {
            "ts": utcnow(),
            "episode_index": ep.get("episode_index"),
            "protocol": proto,
            "latest_snapshot": latest,
            "note": "schedule_changed (display only; registered episode stays frozen)",
        }
        if dry_run:
            print("dry-run: would log schedule_changed", rec)
            n += 1
            continue
        with changed_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        log("schedule_changed", episode_index=ep.get("episode_index"), protocol=proto, latest_snapshot=latest)
        n += 1
    return n


# ---------------------------------------------------------------------------
# Counts-only status (§8). No means, t, or win rates.
# ---------------------------------------------------------------------------

def window_dates(start: str, end: str) -> list[datetime.date]:
    a = datetime.date.fromisoformat(start)
    b = datetime.date.fromisoformat(end)
    out = []
    d = a
    while d <= b:
        out.append(d)
        d += datetime.timedelta(days=1)
    return out


def have_cached_close(source: dict, day: datetime.date) -> bool | None:
    """True/False if we can tell from cache; None if not yet cached."""
    if not source:
        return None
    kind = source.get("kind")
    if kind == "binance":
        p = cache_path("binance", source["symbol"] + "USDT", f"{source['symbol']}USDT-1d-{day.isoformat()}.zip")
        return True if p.exists() else None
    if kind == "geckoterminal":
        # any cached ohlcv file for this pool; presence of the day is checked
        # only at judgment / resolution. Here we only note cache files exist.
        folder = cache_path("geckoterminal", source.get("network") or "solana")
        if not folder.exists():
            return None
        files = list(folder.glob(f"{source.get('pool')}_ohlcv_day_*.json"))
        if not files:
            return None
        # Do not compute returns. Just see whether that UTC day appears.
        for fp in files:
            try:
                payload = json.loads(fp.read_text())
            except Exception:
                continue
            # GeckoTerminal ohlcv list is usually under data.attributes.ohlcv_list
            rows = (
                ((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list")
                or []
            )
            want = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp())
            for row in rows:
                if not row:
                    continue
                ts = int(row[0])
                if ts == want or abs(ts - want) < 12 * 3600:
                    return True
        return False if files else None
    return None


def episode_resolution(ep: dict) -> dict:
    """Counts-only resolution. Reasons only; no returns, means, t or win rates."""
    today = today_utc()
    reasons = []
    flags = load_flags()
    fl = flags.get(ep.get("episode_index")) or {}
    if ep.get("unusable_reason"):
        return {"state": "unusable", "reasons": [ep["unusable_reason"]]}
    if ep.get("blocked") or fl.get("blocked"):
        return {"state": "BLOCKED", "reasons": [ep.get("blocked_reason") or fl.get("blocked_reason") or "blocked"]}
    t0 = datetime.date.fromisoformat(ep["t0"])
    twin = ep.get("twin") or {}
    twin_end = datetime.date.fromisoformat(twin["twin_end"]) if twin.get("twin_end") else None
    # Resolved once both windows have ended (episode T+0 and twin s+30).
    if today < t0 or (twin_end and today < twin_end):
        return {"state": "open", "reasons": []}
    # Windows have ended. Usable requires both episode and twin closes, and
    # not BLOCKED. Close presence is recorded as a boolean, never as a return.
    src = ep.get("price_source") or {}
    need = [datetime.date.fromisoformat(ep["t30"]), t0]
    if twin.get("twin_start") and twin.get("twin_end"):
        need += [datetime.date.fromisoformat(twin["twin_start"]), datetime.date.fromisoformat(twin["twin_end"])]
    missing = []
    unknown = []
    for d in need:
        # SOL close
        sol = cache_path("binance", "SOLUSDT", f"SOLUSDT-1d-{d.isoformat()}.zip")
        tok = have_cached_close(src, d)
        if not sol.exists() or tok is False:
            missing.append(d.isoformat())
        elif tok is None:
            unknown.append(d.isoformat())
    if missing:
        return {"state": "unusable", "reasons": ["missing_closes"]}
    if unknown:
        return {"state": "resolved_pending_cache", "reasons": ["cache_incomplete"]}
    return {"state": "usable", "reasons": []}


def write_status(run_status: str, note: str = "") -> dict:
    existing = load_episodes()
    counts = {
        "registered": len(existing),
        "resolved": 0,
        "usable": 0,
        "BLOCKED": 0,
        "unusable": 0,
        "open": 0,
        "resolved_pending_cache": 0,
    }
    reason_counts: dict[str, int] = defaultdict(int)
    for ep in existing:
        r = episode_resolution(ep)
        st = r["state"]
        if st in counts:
            counts[st] += 1
        if st in ("usable", "unusable", "BLOCKED"):
            counts["resolved"] += 1
        for reason in r["reasons"]:
            reason_counts[reason] += 1
    rec = {
        "as_of": utcnow(),
        "status": run_status,
        "counts": counts,
        "reasons": dict(reason_counts),
        "lock_date": LOCK_DATE.isoformat(),
        "earliest_countable_cliff": EARLIEST_CLIFF.isoformat(),
        "hard_stop": HARD_STOP.isoformat(),
        "note": note or "Counts only until judgment. No means, t or win rates are written.",
    }
    assert_inside_root(STATUS).write_text(json.dumps(rec, indent=2) + "\n")
    # Log counts only — never means, t, or win rates.
    log("status", status=run_status, counts=counts, reasons=dict(reason_counts))
    return rec



def load_flags() -> dict[int, dict]:
    path = ROOT / "episode_flags.jsonl"
    if not path.exists():
        return {}
    out: dict[int, dict] = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("episode_index") is not None:
            out[int(rec["episode_index"])] = rec
    return out


def _binance_close_from_zip(path: Path) -> float | None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not names:
                return None
            with zf.open(names[0]) as fh:
                rows = list(csv.reader(io.TextIOWrapper(fh, newline="")))
        if not rows:
            return None
        # Binance kline: open_time, open, high, low, close, ...
        return float(rows[0][4])
    except Exception:
        return None


def _gt_close_from_cache(source: dict, day: datetime.date) -> float | None:
    folder = cache_path("geckoterminal", source.get("network") or "solana")
    if not folder.exists():
        return None
    want = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp())
    for fp in folder.glob(f"{source.get('pool')}_ohlcv_day_*.json"):
        try:
            payload = json.loads(fp.read_text())
        except Exception:
            continue
        rows = (((payload.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or [])
        for row in rows:
            if not row:
                continue
            try:
                ts = int(row[0])
                close = float(row[4])
            except (TypeError, ValueError, IndexError):
                continue
            if ts == want or abs(ts - want) < 12 * 3600:
                return close
    return None


def close_on(source: dict, day: datetime.date) -> float | None:
    """In-memory close only. Never logged."""
    if not source:
        return None
    kind = source.get("kind")
    if kind == "binance":
        p = cache_path("binance", source["symbol"] + "USDT", f"{source['symbol']}USDT-1d-{day.isoformat()}.zip")
        if p.exists():
            return _binance_close_from_zip(p)
        return None
    if kind == "geckoterminal":
        return _gt_close_from_cache(source, day)
    return None


def apply_block_rules(dry_run: bool = False) -> int:
    """§6 BLOCK rules. Records the rule that fired, not the move size or a return."""
    if dry_run:
        print("dry-run: §6 block rules would be applied on cached closes only")
        return 0
    flags = load_flags()
    n_new = 0
    flag_path = assert_inside_root(ROOT / "episode_flags.jsonl")
    for ep in load_episodes():
        idx = ep.get("episode_index")
        if idx in flags and flags[idx].get("blocked"):
            continue
        if ep.get("blocked"):
            continue
        src = ep.get("price_source") or {}
        days = window_dates(ep["t30"], ep["t0"])
        tw = ep.get("twin") or {}
        if tw.get("twin_start") and tw.get("twin_end"):
            days += window_dates(tw["twin_start"], tw["twin_end"])
        days = sorted(set(days))
        closes = []
        incomplete = False
        for d in days:
            if d > today_utc():
                incomplete = True
                break
            c = close_on(src, d)
            if c is None:
                incomplete = True
                break
            closes.append((d, c))
        if incomplete or len(closes) < 2:
            continue
        reason = None
        is_meme = bool(ep.get("meme_at_registration"))
        if not is_meme:
            prev = closes[0][1]
            for _, c in closes[1:]:
                if prev and abs(c / prev - 1.0) > 0.50:
                    reason = "day_move_over_50pct_non_meme"
                    break
                prev = c
        if reason is None and ep.get("binance_symbol"):
            bsym = ep["binance_symbol"]
            for d, c in closes:
                bp = cache_path("binance", bsym + "USDT", f"{bsym}USDT-1d-{d.isoformat()}.zip")
                if not bp.exists():
                    continue
                bc = _binance_close_from_zip(bp)
                if bc and c and abs(c / bc - 1.0) > 0.20:
                    reason = "binance_gap_over_20pct"
                    break
        if not reason:
            continue
        rec = {
            "ts": utcnow(),
            "episode_index": idx,
            "blocked": True,
            "blocked_reason": reason,
        }
        with flag_path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        log("episode_blocked", episode_index=idx, blocked_reason=reason)
        n_new += 1
    return n_new


# ---------------------------------------------------------------------------
# §7 judgment (stub)
# ---------------------------------------------------------------------------

def cmd_judge(dry_run: bool = False) -> int:
    existing = load_episodes()
    usable_resolved = []
    for ep in existing:
        r = episode_resolution(ep)
        if r["state"] == "usable":
            usable_resolved.append(ep)
    # First 30 usable by T+0 order
    usable_resolved.sort(key=lambda e: (e.get("t0"), e.get("episode_index")))
    n = len(usable_resolved)
    print(f"usable resolved (count only): {n}")
    if n < 30:
        msg = f"§7 judgment refused: {n} usable resolved episodes, need 30."
        print(msg)
        if not dry_run:
            log("judgment_refused", usable_resolved=n, need=30)
        return 2
    # Even at n>=30 this stub does not compute means, t or p-values until a
    # later unlocked implementation is gated. Refuse rather than peek.
    msg = (
        "§7 judgment body is stubbed. 30 usable episodes are resolved, but this "
        "runner will not compute means, t or bootstrap p until the stub is "
        "replaced under Challenger's gate."
    )
    print(msg)
    if not dry_run:
        log("judgment_refused", usable_resolved=n, reason="stub")
    return 2


# ---------------------------------------------------------------------------
# Cache prices for registered (and due) windows — no scoring
# ---------------------------------------------------------------------------

def cache_for_registered(dry_run: bool = False) -> None:
    existing = load_episodes()
    days = set()
    for ep in existing:
        days.update(window_dates(ep["t30"], ep["t0"]))
        tw = ep.get("twin") or {}
        if tw.get("twin_start") and tw.get("twin_end"):
            days.update(window_dates(tw["twin_start"], tw["twin_end"]))
        src = ep.get("price_source") or {}
        if src.get("kind") == "geckoterminal" and src.get("pool") and not dry_run:
            cache_geckoterminal_ohlcv(src.get("network") or "solana", src["pool"], dry_run=False)
        if src.get("kind") == "binance" and src.get("symbol"):
            for d in list(days):
                cache_binance_token(src["symbol"], d, dry_run=dry_run)
    for d in sorted(days):
        if d > today_utc():
            continue
        cache_binance_sol(d, dry_run=dry_run)


# ---------------------------------------------------------------------------
# Schedule slot and catch-up (§9). 01:00 UTC is a target, not a guarantee.
# ---------------------------------------------------------------------------

def slot_stamp(moment: datetime.datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    else:
        moment = moment.astimezone(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def scheduled_slot(now: datetime.datetime) -> datetime.datetime:
    """Latest Monday 01:00 UTC at or before `now`."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    else:
        now = now.astimezone(datetime.timezone.utc)
    monday = now.date() - datetime.timedelta(days=now.weekday())
    slot = datetime.datetime(monday.year, monday.month, monday.day, 1, 0, tzinfo=datetime.timezone.utc)
    if now < slot:
        slot -= datetime.timedelta(days=7)
    return slot


def first_scheduled_slot() -> datetime.datetime:
    """First Monday 01:00 UTC on or after the lock date."""
    days_ahead = (0 - LOCK_DATE.weekday()) % 7
    day = LOCK_DATE + datetime.timedelta(days=days_ahead)
    return datetime.datetime(day.year, day.month, day.day, 1, 0, tzinfo=datetime.timezone.utc)


def expected_slots_through(target: datetime.datetime) -> list[datetime.datetime]:
    slots = []
    cursor = first_scheduled_slot()
    while cursor <= target:
        slots.append(cursor)
        cursor += datetime.timedelta(days=7)
    return slots


def read_filled_slots(log_path: Path | None) -> set[str]:
    filled: set[str] = set()
    if log_path is None or not log_path.is_file():
        return filled
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("event") == "weekly_slot_filled" and rec.get("scheduled_slot"):
            filled.add(str(rec["scheduled_slot"]))
    return filled


def plan_slots(now: datetime.datetime, log_path: Path | None = None) -> dict:
    """Scheduled-slot target, actual start, and Monday slots not yet filled.

    A slot before the first Monday on or after lock is not a UA-F1 slot.
    Filling a slot is idempotent: a second run in the same week does not
    append another weekly_slot_filled row. Missed weeks are listed so the
    run can catch them up. Price gaps stay missing; they are not interpolated.
    """
    target = scheduled_slot(now)
    target_stamp = slot_stamp(target)
    filled = read_filled_slots(log_path)
    expected = expected_slots_through(target)
    missed = [slot_stamp(s) for s in expected if s < target and slot_stamp(s) not in filled]
    return {
        "scheduled_slot": target_stamp,
        "actual_start": slot_stamp(now),
        "missed_slots": missed,
        "current_due": any(slot_stamp(s) == target_stamp for s in expected) and target_stamp not in filled,
        "already_filled": target_stamp in filled,
    }


def _mark_slots(plan: dict) -> None:
    stamps = list(plan["missed_slots"])
    if plan["current_due"]:
        stamps.append(plan["scheduled_slot"])
    for stamp in stamps:
        log("weekly_slot_filled", scheduled_slot=stamp, actual_start=plan["actual_start"])


def _integrity(dry_run: bool) -> int:
    """Prereg hash, then S0 hashes. Any failure stops the run before a fetch."""
    ok, output = prereg_check(dry_run=dry_run)
    print("§1 prereg check:", output, "ok=" + str(ok))
    if not ok:
        print("PAUSED: prereg check failed. No further action.")
        return 1
    ok_s, snap_out = snapshot_sha_check(dry_run=dry_run)
    if not ok_s:
        print(snap_out)
        print("PAUSED: snapshot checksum failed. No further action.")
        return 1
    print(f"snapshot sha check ok=True lines={snap_out.count(': OK')}")
    return 0


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def cmd_weekly(dry_run: bool = False) -> int:
    if dry_run:
        disable_network()
    if _integrity(dry_run) != 0:
        return 1
    if not dry_run:
        seed_run_log()
    now = datetime.datetime.now(datetime.timezone.utc)
    plan = plan_slots(now, RUN_LOG if RUN_LOG.is_file() else None)
    print(
        f"scheduled slot {plan['scheduled_slot']} actual start {plan['actual_start']}"
        f" missed={len(plan['missed_slots'])} already_filled={plan['already_filled']}"
    )
    if plan["missed_slots"]:
        print("missed slots: " + ", ".join(plan["missed_slots"]))
    elif not plan["current_due"] and not plan["already_filled"]:
        print(f"no UA-F1 slot is due yet; first slot is {slot_stamp(first_scheduled_slot())}")
    if dry_run:
        print("dry-run: no files will be written, no fetches will be made.")
        take_monthly_snapshot(dry_run=True)
        register_due_episodes(dry_run=True)
        flag_schedule_changes(dry_run=True)
        cache_for_registered(dry_run=True)
        apply_block_rules(dry_run=True)
        print("dry-run: status would be counts-only; no means/t/win rates.")
        print(f"Before {EARLIEST_CLIFF.isoformat()} this job only snapshots, registers and caches.")
        return 0

    log(
        "weekly_start",
        scheduled_slot=plan["scheduled_slot"],
        actual_start=plan["actual_start"],
        missed_slots=plan["missed_slots"],
        slot_already_filled=plan["already_filled"],
        note=(
            "01:00 UTC is the target slot. A late or dropped cron is caught up "
            "on this run. Missed weeks are filled from cache or the source window. "
            "Gaps are logged, never interpolated."
        ),
    )
    snap = take_monthly_snapshot(dry_run=False)
    if snap is False:
        write_status(
            "RUNNING",
            note="Counts only. Monthly snapshot fetch failed; this slot stays open for catch-up.",
        )
        return 1
    register_due_episodes(dry_run=False)
    flag_schedule_changes(dry_run=False)
    cache_for_registered(dry_run=False)
    apply_block_rules(dry_run=False)
    note = "Counts only until judgment. No means, t or win rates are written."
    if today_utc() < EARLIEST_CLIFF:
        note += f" Before {EARLIEST_CLIFF.isoformat()} this job only snapshots, registers and caches."
    if today_utc() >= HARD_STOP:
        # Hard stop: fewer than 30 usable episodes resolves as INCONCLUSIVE.
        # The count is reported; means are not.
        rec = write_status("HARD_STOP", note=note)
        usable = rec["counts"].get("usable", 0)
        if usable < 30:
            write_status("INCONCLUSIVE", note=note + " Hard stop reached with fewer than 30 usable episodes.")
        _mark_slots(plan)
        return 0
    write_status("RUNNING", note=note)
    _mark_slots(plan)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="UA-F1 weekly runner. Paper only. No trading, keys, wallets, or spend.",
    )
    p.add_argument("--dry-run", action="store_true", help="Plan the run; no writes and no fetches.")
    p.add_argument("--judge", action="store_true", help="Attempt §7 judgment (refuses until 30 usable).")
    p.add_argument(
        "--data-dir",
        default=None,
        help="Writable output directory (the ua-f1-data worktree). Locked S0 stays next to this script.",
    )
    args = p.parse_args(argv)
    if args.data_dir:
        set_data_dir(Path(args.data_dir))
    if args.dry_run:
        disable_network()
    if args.judge:
        if _integrity(args.dry_run) != 0:
            return 1
        return cmd_judge(dry_run=args.dry_run)
    return cmd_weekly(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
