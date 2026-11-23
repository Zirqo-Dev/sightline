"""Read-only idle check for paper books.

Prints one IDLE line when a book had passing leads and zero entries over the
window (24h by default), or when its cash is below entry_cost at the desk's
smallest enterable pool (10 SOL). A second flag compares cash with the cheapest
entry cost, the cost with no pool-impact term. A book that missed entries only
because it is at the open cap or the daily cap is CAPPED. Cash is reported as
cash_below_floor=yes|no and cash_below_cheapest=yes|no, never as an amount.
Does not write, does not use the network, and is not on a schedule.

  python3 tools/idle_books_check.py --data-dir DIR [--now EPOCH|ISO] [--window-hours 24]

Reads positions.json and paper.json for cash and entry times, and tasks.csv
for passing leads. Exit status is 1 when any book is idle, 0 when none are,
and 2 when the directory cannot be read.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

WINDOW_H = 24


def parse_time(value):
    """Unix seconds, milliseconds, or an ISO-8601 timestamp. None if blank."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        out = float(value)
        if out > 1e12:
            out /= 1000.0
        return out
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        out = float(text)
    except ValueError:
        out = None
    if out is not None:
        if out > 1e12:
            out /= 1000.0
        return out
    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        stamp = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.timestamp()


def _num(value):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _entries_from_positions_book(book: dict) -> list[float]:
    times = []
    opened = book.get("open") or {}
    if isinstance(opened, dict):
        rows = opened.values()
    elif isinstance(opened, list):
        rows = opened
    else:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        stamp = parse_time(row.get("opened") if "opened" in row else row.get("opened_at"))
        if stamp is not None:
            times.append(stamp)
    for row in book.get("closed") or []:
        if not isinstance(row, dict):
            continue
        stamp = parse_time(row.get("opened") if "opened" in row else row.get("opened_at"))
        if stamp is not None:
            times.append(stamp)
    return times


def _from_positions(doc: dict, ticket):
    books = {}
    raw = doc.get("books")
    if not isinstance(raw, dict):
        return books, ticket
    top_ticket = _num(doc.get("ticket_size"))
    if top_ticket is None:
        top_ticket = _num(doc.get("ticket_sol"))
    if ticket is None:
        ticket = top_ticket
    for name, book in raw.items():
        if not isinstance(book, dict):
            continue
        book_ticket = _num(book.get("ticket_size"))
        if book_ticket is None:
            book_ticket = _num(book.get("ticket_sol"))
        books[str(name)] = {
            "cash": _num(book.get("cash")),
            "entries": _entries_from_positions_book(book),
            "ticket": book_ticket if book_ticket is not None else ticket,
            "open_n": _open_count(book),
            "fills_today": _num(book.get("fills_today")),
            "pool_sol": _pool_size(book),
        }
    return books, ticket


def _ticket_rows(doc: dict):
    rows = []
    for key in ("open", "closed"):
        block = doc.get(key) or []
        if isinstance(block, list):
            rows.extend(r for r in block if isinstance(r, dict))
    return rows


def _from_paper(doc: dict):
    ticket = _num(doc.get("ticket_size"))
    raw = doc.get("books")
    if isinstance(raw, dict) and raw and all(isinstance(v, dict) for v in raw.values()):
        books = {}
        for name, book in raw.items():
            book_ticket = _num(book.get("ticket_size")) or ticket
            books[str(name)] = {
                "cash": _num(book.get("cash")),
                "entries": [],
                "ticket": book_ticket,
                "open_n": _open_count(book),
                "fills_today": _num(book.get("fills_today")),
                "pool_sol": _pool_size(book),
            }
        for row in _ticket_rows(doc):
            stamp = parse_time(row.get("opened_at") if "opened_at" in row else row.get("opened"))
            if stamp is None:
                continue
            name = str(row.get("book") or "")
            if name in books:
                books[name]["entries"].append(stamp)
            elif name == "" and len(books) == 1:
                next(iter(books.values()))["entries"].append(stamp)
        return books, ticket
    name = str(doc.get("book") or doc.get("desk") or "book")
    entries = []
    for row in _ticket_rows(doc):
        stamp = parse_time(row.get("opened_at") if "opened_at" in row else row.get("opened"))
        if stamp is not None:
            entries.append(stamp)
    if isinstance(doc.get("open"), list):
        open_n = len([row for row in doc["open"] if isinstance(row, dict)])
    else:
        open_n = _open_count(doc)
    return {
        name: {
            "cash": _num(doc.get("cash")),
            "entries": entries,
            "ticket": ticket,
            "open_n": open_n,
            "fills_today": _num(doc.get("fills_today")),
            "pool_sol": _pool_size(doc),
        }
    }, ticket


def _leads_from_tasks(path: Path) -> list[tuple[float, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        sample = handle.read(4096)
    header = ""
    for line in sample.splitlines():
        if line.strip():
            header = line
            break
    if header and "result" not in {part.strip().lower() for part in header.split(",")}:
        raise csv.Error("malformed tasks")
    leads = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("result") or "").strip().lower() != "pass":
                continue
            stamp = parse_time(row.get("time"))
            if stamp is None:
                continue
            leads.append((stamp, str(row.get("book") or "").strip()))
    return leads


def _desk():
    root = Path(__file__).resolve().parents[1]
    desk = root / "solana-desk"
    if str(desk) not in sys.path:
        sys.path.insert(0, str(desk))
    from desk import config as C
    from desk import fills
    return C, fills


def _floors() -> tuple[float, float]:
    """Floor at the 10 SOL pool, and the cheapest entry (no impact term).

    Desk books store pool size on each position, not on the book. The floor is
    entry_cost at the smallest pool the desk will enter, not a per-position size.
    """
    C, fills = _desk()
    smallest = C.SKIP_IF["min_quote_liquidity_sol"]
    if smallest is None:
        raise ValueError("missing pool floor")
    floor, _haircut = fills.entry_cost(float(smallest))
    cheapest, _haircut = fills.entry_cost(None)
    return float(floor), float(cheapest)


def _pool_size(book: dict):
    for key in ("pool_sol", "liq0", "sol"):
        if key in book and book.get(key) is not None:
            return _num(book.get(key))
    return None


def _open_count(book: dict):
    raw = book.get("open")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, dict):
        return len(raw)
    if isinstance(raw, list):
        return len([row for row in raw if isinstance(row, dict)])
    return None


def load_state(data_dir: Path) -> dict:
    """Cash, entry times, ticket floor, and passing-lead times. Read-only."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {data_dir}")
    positions_path = data_dir / "positions.json"
    paper_path = data_dir / "paper.json"
    tasks_path = data_dir / "tasks.csv"
    if not positions_path.is_file() and not paper_path.is_file():
        raise FileNotFoundError("missing book state")
    ticket = None
    books: dict = {}
    if paper_path.is_file():
        paper = _read_json(paper_path)
        if not isinstance(paper, dict):
            raise ValueError("malformed book state")
        paper_books, ticket = _from_paper(paper)
        books = paper_books
    if positions_path.is_file():
        positions = _read_json(positions_path)
        if not isinstance(positions, dict) or not isinstance(positions.get("books"), dict):
            raise ValueError("malformed book state")
        pos_books, ticket = _from_positions(positions, ticket)
        if pos_books:
            books = pos_books
    if not books:
        raise FileNotFoundError("missing book state")
    if not tasks_path.is_file():
        raise FileNotFoundError("missing tasks")
    for book in books.values():
        if book.get("ticket") is None:
            book["ticket"] = ticket
    leads = _leads_from_tasks(tasks_path)
    return {"books": books, "leads": leads, "ticket": ticket}


def _in_window(stamp: float, now: float, window_s: float) -> bool:
    return now - window_s <= stamp <= now


def _leads_for(name: str, leads, now: float, window_s: float) -> int:
    return sum(1 for stamp, book in leads if _in_window(stamp, now, window_s) and (book == "" or book == name))


def _entries_for(book: dict, now: float, window_s: float) -> int:
    return sum(1 for stamp in book["entries"] if _in_window(stamp, now, window_s))


def idle_lines(state: dict, now: float, window_s: float = WINDOW_H * 3600) -> list[str]:
    """One line per idle or capped book. Cash is only yes or no, never an amount."""
    C, _fills = _desk()
    floor, cheapest = _floors()
    hours = window_s / 3600
    hour_label = str(int(hours)) if hours == int(hours) else str(hours)
    lines = []
    for name in sorted(state["books"]):
        book = state["books"][name]
        cash = book.get("cash")
        below = cash is not None and cash < floor
        below_cheap = cash is not None and cash < cheapest
        flag = "yes" if below else "no"
        cheap_flag = "yes" if below_cheap else "no"
        leads = _leads_for(name, state["leads"], now, window_s)
        entries = _entries_for(book, now, window_s)
        lead_idle = leads > 0 and entries == 0
        open_n = book.get("open_n")
        fills_today = book.get("fills_today")
        at_open = open_n is not None and open_n >= C.MAX_OPEN_F
        at_day = fills_today is not None and fills_today >= C.MAX_NEW_F_PER_DAY
        capped = lead_idle and not below and (at_open or at_day)
        if below or (lead_idle and not capped):
            parts = [f"cash_below_floor={flag}", f"cash_below_cheapest={cheap_flag}"]
            if lead_idle:
                parts.append(
                    f"passing leads in the last {hour_label}h with zero entries (leads={leads} entries=0)"
                )
            lines.append(f"IDLE book={name} reason={'; '.join(parts)}")
        elif capped:
            which = ",".join([label for label, on in (("open_cap", at_open), ("day_cap", at_day)) if on])
            lines.append(
                f"CAPPED book={name} reason={which} cash_below_floor={flag} cash_below_cheapest={cheap_flag}"
            )
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Flag idle paper books. Read-only.")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--now", default=None, help="epoch seconds or ISO-8601; default is the current time")
    parser.add_argument("--window-hours", type=float, default=WINDOW_H)
    args = parser.parse_args(argv)
    now = parse_time(args.now) if args.now is not None else datetime.now(timezone.utc).timestamp()
    if now is None:
        print("idle-books: --now is not a time", file=sys.stderr)
        return 2
    if args.window_hours <= 0:
        print("idle-books: --window-hours must be positive", file=sys.stderr)
        return 2
    try:
        state = load_state(Path(args.data_dir))
    except FileNotFoundError as exc:
        print(f"idle-books: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError, csv.Error, ValueError) as exc:
        print(f"idle-books: could not read book state ({type(exc).__name__})", file=sys.stderr)
        return 2
    lines = idle_lines(state, now, args.window_hours * 3600)
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
