"""Load historical scan and pool-snapshot series. Read-only. No network.

Only manifest-* and pool-snaps-* files are read. Death-log rows are skipped.
A row is a bar only when sol and token are both numbers.

Book ledgers, fill logs, and lst-count captures are refused before they are
opened. A file whose text names a locked lst-count field is refused before
that field is parsed.

Until 2026-11-25T15:00:00Z the loader refuses bars at or after
2026-10-26T00:00:00Z and any realpath under solana-desk/v2. The clock is
the real UTC clock. A caller cannot pass another clock or an override.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

# Names that are books, ledgers, or fills, not a scan/snapshot series.
BANNED_NAMES = frozenset({
    "positions.json",
    "trades.csv",
    "paper.json",
    "captures.jsonl",
    "fills.csv",
    "ledger.csv",
})

# Key names only. The pattern does not capture the value.
_LOCKED_FIELD = re.compile(r'"(?:onset|d_bps)"\s*:')

# Death-log lines are not prices. Manifest scan headers are not bars.
_SKIP_TYPES = frozenset({"obs", "label", "skip", "death", "scan"})
_STATUSES = frozenset({"ok", "temp_error", "no_route", "pool_gone"})

# v6.5 freeze through the Nov 25 read. Dates only; no book outcomes.
BLIND_START = datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc)
BLIND_UNTIL = datetime(2026, 11, 25, 15, 0, tzinfo=timezone.utc)
BLIND_START_S = int(BLIND_START.timestamp())


class ReplayRefused(ValueError):
    """The path is not a historical scan or snapshot series."""


def _banned(path: Path) -> bool:
    return path.name in BANNED_NAMES or "lst-count-data" in path.parts


def _guard(path: Path) -> None:
    if "lst-count-data" in path.parts:
        raise ReplayRefused("refused: lst-count-data is not a replay source")
    if path.name in BANNED_NAMES:
        raise ReplayRefused(f"refused: {path.name} is not a scan or snapshot series")


def _refuse_locked(text: str, path: Path) -> None:
    if _LOCKED_FIELD.search(text):
        raise ReplayRefused(f"refused: {path.name} contains a locked field")


def _num(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _sells(row: dict):
    if "sells_h1" in row:
        return _num(row.get("sells_h1"))
    txns = row.get("txns_h1")
    if isinstance(txns, dict):
        return _num(txns.get("sells"))
    return None


def _buys(row: dict):
    if "buys_h1" in row:
        return _num(row.get("buys_h1"))
    txns = row.get("txns_h1")
    if isinstance(txns, dict):
        return _num(txns.get("buys"))
    return None


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _clock_blinded() -> bool:
    return datetime.now(timezone.utc) < BLIND_UNTIL


def _realpath(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _under_solana_v2(path: Path) -> bool:
    parts = path.parts
    for i, part in enumerate(parts[:-1]):
        if part == "solana-desk" and parts[i + 1] == "v2":
            return True
    return False


def _kind(path: Path) -> str | None:
    name = path.name
    if name.startswith("death-"):
        return "death"
    if name.startswith("manifest-") and name.endswith(".jsonl"):
        return "manifest"
    if name.startswith("pool-snaps-") and name.endswith(".jsonl"):
        return "snap"
    return None


def _bar_from_mapping(row: dict) -> dict | None:
    """One replay bar. Rules and reserves are copied; unknown keys are dropped."""
    if not isinstance(row, dict):
        return None
    if row.get("type") in _SKIP_TYPES:
        return None
    mint = row.get("mint")
    t_s = row.get("t_s")
    if not isinstance(mint, str) or not mint:
        return None
    if isinstance(t_s, bool) or t_s is None:
        return None
    try:
        t_s = int(t_s)
    except (TypeError, ValueError):
        return None
    inputs = row.get("inputs") if isinstance(row.get("inputs"), dict) else {}
    reserve = row.get("reserve") if isinstance(row.get("reserve"), dict) else {}
    sol = _num(row.get("sol"))
    if sol is None:
        sol = _num(inputs.get("sol"))
    if sol is None:
        sol = _num(reserve.get("sol"))
    token = _num(row.get("token"))
    if token is None:
        token = _num(inputs.get("token"))
    if token is None:
        token = _num(reserve.get("token"))
    age = _num(row.get("age_s"))
    if age is None:
        age = _num(inputs.get("age_s"))
    rules = row.get("rules") if isinstance(row.get("rules"), dict) else None
    sells = _sells(row)
    if sells is None:
        sells = _sells(inputs)
    buys = _buys(row)
    if buys is None:
        buys = _buys(inputs)
    error = None
    for src in (row, inputs, reserve):
        if isinstance(src, dict) and src.get("error"):
            error = str(src["error"])
            break
    # A dead pool is a row with null reserves and an error, not a dropped line.
    if error and (not _finite(sol) or not _finite(token)):
        bar = {
            "mint": mint, "t_s": t_s, "sol": None, "token": None,
            "age_s": age, "sells_h1": sells, "buys_h1": buys, "error": error,
        }
        if rules is not None:
            bar["rules"] = {str(k): rules[k] for k in rules}
        return bar
    if not _finite(sol) or not _finite(token):
        return None
    bar = {"mint": mint, "t_s": t_s, "sol": float(sol), "token": float(token), "age_s": age, "sells_h1": sells, "buys_h1": buys, "error": error}
    if rules is not None:
        bar["rules"] = {str(k): rules[k] for k in rules}
    if row.get("status") in _STATUSES:
        bar["status"] = row["status"]
    return bar


def _merge(left: dict, right: dict) -> dict:
    out = dict(left)
    for key, value in right.items():
        if value is None:
            continue
        if key == "rules" and isinstance(value, dict):
            merged = dict(out.get("rules") or {})
            merged.update(value)
            out["rules"] = merged
            continue
        out[key] = value
    return out


def _dedupe(bars: list[dict]) -> list[dict]:
    by_key: dict[tuple, dict] = {}
    order: list[tuple] = []
    for bar in bars:
        if bar is None:
            continue
        key = (bar["mint"], bar["t_s"])
        if key in by_key:
            by_key[key] = _merge(by_key[key], bar)
        else:
            by_key[key] = bar
            order.append(key)
    out = [by_key[key] for key in order]
    out.sort(key=lambda b: (b["mint"], b["t_s"]))
    return out


def _lines(text: str, path: Path) -> list[dict]:
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayRefused(f"refused: {path.name} line {n} is not json") from exc
        if not isinstance(row, dict):
            raise ReplayRefused(f"refused: {path.name} line {n} is not an object")
        rows.append(row)
    return rows


def _read(path: Path) -> str:
    _guard(path)
    with path.open("r", encoding="utf-8") as handle:
        text = handle.read()
    _refuse_locked(text, path)
    return text


def _bars_from_rows(rows: list[dict]) -> list[dict]:
    bars = []
    for row in rows:
        bar = _bar_from_mapping(row)
        if bar is not None:
            bars.append(bar)
    return _dedupe(bars)


def _load_jsonl(path: Path) -> list[dict]:
    if _kind(path) == "death":
        return []
    if _kind(path) not in {"manifest", "snap"}:
        raise ReplayRefused("refused: only manifest-* and pool-snaps-* series are loaded")
    return _bars_from_rows(_lines(_read(path), path))


def _blocked_v2(path: Path, blinded: bool) -> bool:
    if not blinded:
        return False
    resolved = _realpath(path)
    return _under_solana_v2(path) or _under_solana_v2(resolved)


def _series_files(path: Path, blinded: bool) -> list[Path]:
    found = []
    roots = [_realpath(path)]
    v2 = roots[0] / "v2"
    if v2.is_dir() and not _blocked_v2(v2, blinded):
        roots.append(v2)
    for root in roots:
        if _blocked_v2(root, blinded):
            continue
        for child in sorted(root.iterdir()):
            if not child.is_file() or _banned(child):
                continue
            if _blocked_v2(child, blinded):
                continue
            kind = _kind(child)
            if kind in {"manifest", "snap"}:
                found.append(child)
    return found


def _refuse_blind_bars(bars: list[dict]) -> None:
    for bar in bars:
        if int(bar["t_s"]) >= BLIND_START_S:
            raise ReplayRefused("refused: a bar is inside the blinded window")


def load_bars(path) -> list[dict]:
    """Bars sorted by mint, then time. Reads the path and does not write.

    The blinded-window check uses the real UTC clock and the realpath.
    """
    given = Path(path)
    src = _realpath(given)
    _guard(src)
    blinded = _clock_blinded()
    if _blocked_v2(given, blinded) or _blocked_v2(src, blinded):
        raise ReplayRefused("refused: solana-desk/v2 is inside the blinded window")
    if src.is_file():
        bars = _load_jsonl(src)
    elif src.is_dir():
        files = _series_files(src, blinded)
        if not files:
            raise ReplayRefused("refused: no manifest-* or pool-snaps-* series in the directory")
        bars = []
        for item in files:
            bars.extend(_load_jsonl(item))
        bars = _dedupe(bars)
    else:
        raise ReplayRefused("refused: series path is not a file or directory")
    if blinded:
        _refuse_blind_bars(bars)
    return bars
