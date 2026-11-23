"""Idle-book checks on synthetic fixtures. No live book is opened."""

import json
import subprocess
import sys
import unittest
from pathlib import Path

from tools.idle_books_check import idle_lines, load_state, main

ROOT = Path(__file__).resolve().parents[2]
NOW = 1_710_000_000
DAY = 86400


def _write(dirpath: Path, name: str, body: str) -> None:
    (dirpath / name).write_text(body, encoding="utf-8")


class IdleBooks(unittest.TestCase):
    def _dir(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Path(tmp.name)

    def test_passing_leads_and_zero_entries(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "books": {
                "F": {"cash": 9.5, "open": {}, "closed": [{"opened": NOW - 3 * DAY, "pnl": 99999}]},
                "R": {"cash": 8.0, "open": {"x": {"opened": NOW - 1000}}, "closed": []},
            }
        }))
        _write(root, "tasks.csv", "\n".join([
            "time,run_id,result,book",
            f"{NOW - 1000},R1,pass,",
            f"{NOW - 2 * DAY},R0,pass,",
            f"{NOW - 500},R1,skip,",
        ]))
        state = load_state(root)
        lines = idle_lines(state, NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=passing leads in the last 24h with zero entries (leads=1 entries=0)",
        ])
        self.assertNotIn("99999", "\n".join(lines))

    def test_cash_below_ticket_floor(self):
        root = self._dir()
        _write(root, "paper.json", json.dumps({
            "schema_version": 2,
            "book": "F,R,S",
            "ticket_size": 0.5,
            "books": {
                "F": {"cash": 0.2},
                "S": {"cash": 0.5},
            },
            "open": [],
            "closed": [{"book": "S", "opened_at": "2024-03-09T16:00:00Z", "result": {"pnl": 424242}}],
        }))
        state = load_state(root)
        lines = idle_lines(state, NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=cash 0.20000000 is below the ticket floor 0.50000000",
        ])
        self.assertNotIn("424242", "\n".join(lines))

    def test_both_reasons_are_one_line(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "ticket_sol": 0.5,
            "books": {"F": {"cash": 0.1, "open": {}, "closed": []}},
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 10},pass,\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("IDLE book=F reason="))
        self.assertIn("cash 0.10000000 is below the ticket floor 0.50000000", lines[0])
        self.assertIn("passing leads in the last 24h with zero entries (leads=1 entries=0)", lines[0])

    def test_recent_entry_and_healthy_cash_are_not_idle(self):
        root = self._dir()
        _write(root, "paper.json", json.dumps({
            "ticket_size": 0.5,
            "books": {"F": {"cash": 4.0}},
            "open": [{"book": "F", "opened_at": "2024-03-09T16:00:00Z"}],
            "closed": [],
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,F\n")
        self.assertEqual(idle_lines(load_state(root), NOW), [])

    def test_named_lead_does_not_flag_another_book(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "books": {
                "F": {"cash": 9.0, "open": {}, "closed": []},
                "S": {"cash": 9.0, "open": {}, "closed": []},
            }
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,F\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=passing leads in the last 24h with zero entries (leads=1 entries=0)",
        ])

    def test_cli_is_read_only_and_returns_status(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "books": {"F": {"cash": 9.0, "open": {}, "closed": []}},
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,\n")
        before = sorted(p.name for p in root.iterdir())
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "idle_books_check.py"),
             "--data-dir", str(root), "--now", str(NOW)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(
            proc.stdout,
            "IDLE book=F reason=passing leads in the last 24h with zero entries (leads=1 entries=0)\n",
        )
        self.assertEqual(sorted(p.name for p in root.iterdir()), before)
        quiet = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "idle_books_check.py"),
             "--data-dir", str(root), "--now", str(NOW - 3 * DAY)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(quiet.returncode, 0)
        self.assertEqual(quiet.stdout, "")
        self.assertEqual(main(["--data-dir", str(root / "missing")]), 2)

    def test_bad_json_does_not_echo_the_file(self):
        root = self._dir()
        _write(root, "paper.json", '{"ticket_size": 0.5, "pnl": 777777,')
        code = main(["--data-dir", str(root), "--now", str(NOW)])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
