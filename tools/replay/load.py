"""Load historical scan and pool-snapshot series. Read-only. No network.

Book ledgers, fill logs, and lst-count captures are refused before they are
opened. A file whose text names a locked lst-count field is refused before
that field is parsed.
"""

from __future__ import annotations

import json
import re
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


def _bar_from_mapping(row: dict) -> dict | None:
    """One replay bar. Rules and reserves are copied; unknown keys are dropped."""
    if not isinstance(row, dict):
        return None
    if row.get("type") == "scan":
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
    bar = {"mint": mint, "t_s": t_s, "sol": sol, "token": token, "age_s": age, "sells_h1": sells, "buys_h1": buys}
    if rules is not None:
        bar["rules"] = {str(k): rules[k] for k in rules}
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


def _load_file(path: Path) -> list[dict]:
    text = _read(path)
    if path.suffix == ".json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ReplayRefused(f"refused: {path.name} is not json") from exc
        if isinstance(doc, dict) and isinstance(doc.get("bars"), list):
            return _bars_from_rows(doc["bars"])
        if isinstance(doc, list):
            return _bars_from_rows(doc)
        raise ReplayRefused(f"refused: {path.name} has no bar list")
    rows = _lines(text, path)
    return _bars_from_rows(rows)


def _is_manifest(path: Path) -> bool:
    return "manifest" in path.name


def _is_snap(path: Path) -> bool:
    return "pool-snap" in path.name or "pool_snap" in path.name


def _series_files(path: Path) -> list[Path]:
    found = []
    roots = [path]
    v2 = path / "v2"
    if v2.is_dir():
        roots.append(v2)
    for root in roots:
        for child in sorted(root.iterdir()):
            if not child.is_file() or child.suffix not in {".jsonl", ".json"}:
                continue
            if _banned(child):
                continue
            found.append(child)
    return found


def load_bars(path) -> list[dict]:
    """Bars sorted by mint, then time. Reads the path and does not write."""
    src = Path(path)
    _guard(src)
    if src.is_file():
        return _load_file(src)
    if not src.is_dir():
        raise ReplayRefused("refused: series path is not a file or directory")
    files = _series_files(src)
    if not files:
        raise ReplayRefused("refused: no scan or snapshot series in the directory")
    manifests = [p for p in files if _is_manifest(p)]
    snaps = [p for p in files if _is_snap(p)]
    plain = [p for p in files if p not in manifests and p not in snaps]
    # A manifest directory is joined with its pool snaps. Other jsonl files in
    # that directory are extra snapshot lines, not a second series.
    if manifests:
        rows: list[dict] = []
        for item in manifests + snaps + plain:
            text = _read(item)
            if item.suffix == ".json":
                try:
                    doc = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise ReplayRefused(f"refused: {item.name} is not json") from exc
                if isinstance(doc, list):
                    rows.extend(r for r in doc if isinstance(r, dict))
                elif isinstance(doc, dict) and isinstance(doc.get("bars"), list):
                    rows.extend(r for r in doc["bars"] if isinstance(r, dict))
                continue
            rows.extend(_lines(text, item))
        return _bars_from_rows(rows)
    bars: list[dict] = []
    for item in plain:
        bars.extend(_load_file(item))
    return _dedupe(bars)
