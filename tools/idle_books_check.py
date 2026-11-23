"""Read-only idle check for paper books.

Prints one IDLE line when a book had passing leads and zero entries over the
window (24h by default), or when its cash is below the ticket floor. Does not
write, does not open a socket, and is not on a schedule.

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
    return {name: {"cash": _num(doc.get("cash")), "entries": entries, "ticket": ticket}}, ticket


def _leads_from_tasks(path: Path) -> list[tuple[float, str]]:
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


def _default_ticket() -> float:
    root = Path(__file__).resolve().parents[1]
    desk = root / "solana-desk"
    if str(desk) not in sys.path:
        sys.path.insert(0, str(desk))
    from desk.config import TICKET_SOL
    return float(TICKET_SOL)


def load_state(data_dir: Path) -> dict:
    """Cash, entry times, ticket floor, and passing-lead times. Read-only."""
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"not a directory: {data_dir}")
    positions_path = data_dir / "positions.json"
    paper_path = data_dir / "paper.json"
    tasks_path = data_dir / "tasks.csv"
    ticket = None
    books: dict = {}
    if paper_path.is_file():
        paper_books, ticket = _from_paper(_read_json(paper_path))
        books = paper_books
    from_positions = False
    if positions_path.is_file():
        pos_books, ticket = _from_positions(_read_json(positions_path), ticket)
        if pos_books:
            # Paper tickets are the published view of the same positions.
            books = pos_books
            from_positions = True
    if ticket is None and from_positions:
        ticket = _default_ticket()
    for book in books.values():
        if book.get("ticket") is None:
            book["ticket"] = ticket
    leads = _leads_from_tasks(tasks_path) if tasks_path.is_file() else []
    return {"books": books, "leads": leads, "ticket": ticket}


def _in_window(stamp: float, now: float, window_s: float) -> bool:
    return now - window_s <= stamp <= now


def _leads_for(name: str, leads, now: float, window_s: float) -> int:
    return sum(1 for stamp, book in leads if _in_window(stamp, now, window_s) and (book == "" or book == name))


def _entries_for(book: dict, now: float, window_s: float) -> int:
    return sum(1 for stamp in book["entries"] if _in_window(stamp, now, window_s))


def idle_lines(state: dict, now: float, window_s: float = WINDOW_H * 3600) -> list[str]:
    """One line per idle book. Empty when nothing is idle."""
    hours = window_s / 3600
    hour_label = str(int(hours)) if hours == int(hours) else str(hours)
    lines = []
    for name in sorted(state["books"]):
        book = state["books"][name]
        reasons = []
        cash = book.get("cash")
        ticket = book.get("ticket")
        if cash is not None and ticket is not None and cash < ticket:
            reasons.append(f"cash {cash:.8f} is below the ticket floor {ticket:.8f}")
        leads = _leads_for(name, state["leads"], now, window_s)
        entries = _entries_for(book, now, window_s)
        if leads > 0 and entries == 0:
            reasons.append(
                f"passing leads in the last {hour_label}h with zero entries (leads={leads} entries=0)"
            )
        if reasons:
            lines.append(f"IDLE book={name} reason={'; '.join(reasons)}")
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
    except (OSError, json.JSONDecodeError, csv.Error) as exc:
        print(f"idle-books: could not read book state ({type(exc).__name__})", file=sys.stderr)
        return 2
    lines = idle_lines(state, now, args.window_hours * 3600)
    for line in lines:
        print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
