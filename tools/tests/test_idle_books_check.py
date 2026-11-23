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


def _no_amounts(lines):
    text = "\n".join(lines)
    for token in ("0.20000000", "0.50000000", "0.10000000", "99999", "424242", "9.0", "9.5"):
        if token in text:
            raise AssertionError(token)


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
                "F": {"cash": 9.5, "pool_sol": 100, "open": {}, "closed": [{"opened": NOW - 3 * DAY, "pnl": 99999}], "fills_today": 0},
                "R": {"cash": 8.0, "pool_sol": 100, "open": {"x": {"opened": NOW - 1000}}, "closed": [], "fills_today": 1},
            }
        }))
        _write(root, "tasks.csv", "\n".join([
            "time,run_id,result,book",
            f"{NOW - 1000},R1,pass,",
            f"{NOW - 2 * DAY},R0,pass,",
            f"{NOW - 500},R1,skip,",
        ]))
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=cash_below_floor=no; passing leads in the last 24h with zero entries (leads=1 entries=0)",
        ])
        _no_amounts(lines)

    def test_cash_below_entry_cost_not_the_bare_ticket(self):
        root = self._dir()
        _write(root, "paper.json", json.dumps({
            "schema_version": 2,
            "book": "F,R,S",
            "ticket_size": 0.5,
            "books": {
                "F": {"cash": 0.5, "pool_sol": 100, "open": 0, "fills_today": 0},
                "S": {"cash": 9.0, "pool_sol": 100, "open": 0, "fills_today": 0},
            },
            "open": [],
            "closed": [{"book": "S", "opened_at": "2024-03-09T16:00:00Z", "result": {"pnl": 424242}}],
        }))
        _write(root, "tasks.csv", "time,result,book\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=cash_below_floor=yes",
        ])
        _no_amounts(lines)

    def test_both_reasons_are_one_line_without_amounts(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "books": {"F": {"cash": 0.1, "pool_sol": 100, "open": {}, "closed": [], "fills_today": 0}},
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 10},pass,\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=cash_below_floor=yes; passing leads in the last 24h with zero entries (leads=1 entries=0)",
        ])
        _no_amounts(lines)

    def test_open_cap_and_day_cap_are_capped_not_idle(self):
        root = self._dir()
        opens = {f"p{i}": {"opened": NOW - 3 * DAY} for i in range(4)}
        _write(root, "positions.json", json.dumps({
            "books": {
                "F": {"cash": 9.0, "pool_sol": 100, "open": opens, "closed": [], "fills_today": 0},
                "S": {"cash": 9.0, "pool_sol": 100, "open": {}, "closed": [], "fills_today": 8},
            }
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(lines, [
            "CAPPED book=F reason=open_cap cash_below_floor=no",
            "CAPPED book=S reason=day_cap cash_below_floor=no",
        ])
        _no_amounts(lines)

    def test_cash_below_floor_stays_idle_even_at_the_open_cap(self):
        root = self._dir()
        opens = {f"p{i}": {"opened": NOW - 3 * DAY} for i in range(4)}
        _write(root, "positions.json", json.dumps({
            "books": {"F": {"cash": 0.1, "pool_sol": 100, "open": opens, "closed": [], "fills_today": 0}},
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith("IDLE book=F "))
        self.assertIn("cash_below_floor=yes", lines[0])
        self.assertNotIn("CAPPED", lines[0])
        _no_amounts(lines)

    def test_recent_entry_and_healthy_cash_are_not_idle(self):
        root = self._dir()
        _write(root, "paper.json", json.dumps({
            "ticket_size": 0.5,
            "books": {"F": {"cash": 4.0, "pool_sol": 100, "open": 1, "fills_today": 1}},
            "open": [{"book": "F", "opened_at": "2024-03-09T16:00:00Z"}],
            "closed": [],
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,F\n")
        self.assertEqual(idle_lines(load_state(root), NOW), [])

    def test_named_lead_does_not_flag_another_book(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "books": {
                "F": {"cash": 9.0, "pool_sol": 100, "open": {}, "closed": [], "fills_today": 0},
                "S": {"cash": 9.0, "pool_sol": 100, "open": {}, "closed": [], "fills_today": 0},
            }
        }))
        _write(root, "tasks.csv", f"time,result,book\n{NOW - 60},pass,F\n")
        lines = idle_lines(load_state(root), NOW)
        self.assertEqual(lines, [
            "IDLE book=F reason=cash_below_floor=no; passing leads in the last 24h with zero entries (leads=1 entries=0)",
        ])

    def test_cli_is_read_only_and_returns_status(self):
        root = self._dir()
        _write(root, "positions.json", json.dumps({
            "books": {"F": {"cash": 9.0, "pool_sol": 100, "open": {}, "closed": [], "fills_today": 0}},
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
            "IDLE book=F reason=cash_below_floor=no; passing leads in the last 24h with zero entries (leads=1 entries=0)\n",
        )
        self.assertNotIn("9.0", proc.stdout)
        self.assertEqual(sorted(p.name for p in root.iterdir()), before)
        quiet = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "idle_books_check.py"),
             "--data-dir", str(root), "--now", str(NOW - 3 * DAY)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(quiet.returncode, 0)
        self.assertEqual(quiet.stdout, "")
        self.assertEqual(main(["--data-dir", str(root / "missing")]), 2)
        self.assertEqual(main(["--data-dir", str(self._dir())]), 2)

    def test_bad_json_and_bad_tasks_do_not_echo_the_file(self):
        root = self._dir()
        _write(root, "paper.json", '{"ticket_size": 0.5, "pnl": 777777,')
        self.assertEqual(main(["--data-dir", str(root), "--now", str(NOW)]), 2)
        other = self._dir()
        _write(other, "positions.json", '{"books": {"F": {"cash": 9, "pool_sol": 100, "open": {}}}}')
        _write(other, "tasks.csv", "{not,a,header\n")
        self.assertEqual(main(["--data-dir", str(other), "--now", str(NOW)]), 2)
        missing_tasks = self._dir()
        _write(missing_tasks, "positions.json", '{"books": {"F": {"cash": 9, "pool_sol": 100, "open": {}}}}')
        self.assertEqual(main(["--data-dir", str(missing_tasks), "--now", str(NOW)]), 2)
        missing_pool = self._dir()
        _write(missing_pool, "positions.json", '{"books": {"F": {"cash": 9, "open": {}}}}')
        _write(missing_pool, "tasks.csv", "time,result,book\n")
        self.assertEqual(main(["--data-dir", str(missing_pool), "--now", str(NOW)]), 2)


if __name__ == "__main__":
    unittest.main()
