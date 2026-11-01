"""Day-14 verdict is a pure report. It does not touch the golden decisions."""
import calendar, importlib.util, os, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    path = os.path.join(ROOT, "specs", "day14_verdict.py")
    spec = importlib.util.spec_from_file_location("day14_verdict", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ts(text):
    return calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%S"))


def close(i, pnl, opened, done, model=""):
    return dict(id=f"F-20261028T1200-{i:04d}", mint=f"mint{i}", opened=opened, closed=done,
                pnl=pnl, entry=1.0, exit_px=1.0, why="time_stop", cost_model=model)


def trades_for(rows):
    out = []
    for row in rows:
        out.append(dict(book="F", action="buy", mint=row["mint"], time=str(row["opened"]), frac="1", pnl=""))
        out.append(dict(book="F", action="sell", mint=row["mint"], time=str(row["closed"]), frac="1",
                        pnl=str(row["pnl"]), net_pnl="999"))
    return out


def positions(closed, opened=None, last=None):
    return {"last_run_ts": last, "books": {
        "F": {"closed": closed, "open": opened or {}},
        "R": {"closed": [], "open": {}},
        "S": {"closed": [], "open": {}},
    }}


class Day14Verdict(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.early = ts("2026-10-28T12:00:00")
        self.done = ts("2026-10-28T14:00:00")
        self.after = self.m.parse_utc(self.m.CUTOFF_DEFAULT) + 60

    def text(self, closed, opened=None, trades=None, commit="abc", cutoff=None, last=None):
        if trades is None:
            trades = trades_for(closed)
        pos = positions(closed, opened, last)
        return self.m.render(
            pos, trades, cutoff or self.m.CUTOFF_DEFAULT, commit, "poshash", "trhash",
        )

    def test_constants_are_checked_at_runtime(self):
        self.assertEqual(self.m.COST_VERSION, "as_logged")
        self.assertEqual(self.m.CUTOFF_DEFAULT, "2026-11-11T15:00:00Z")
        self.assertEqual(self.m.LAST_COUNTED_RUN, "20261111T1430")
        self.m.assert_registered()
        old = self.m.C.MIN_F_CLOSES
        self.m.C.MIN_F_CLOSES = 19
        try:
            with self.assertRaises(RuntimeError):
                self.m.assert_registered()
        finally:
            self.m.C.MIN_F_CLOSES = old

    def test_naive_cutoff_is_rejected(self):
        with self.assertRaises(ValueError):
            self.m.parse_utc("2026-11-11T15:00:00")
        with self.assertRaises(ValueError):
            self.m.parse_utc("2026-11-11T15:00:00+00:00")

    def test_rule_numbers_stay_visible_when_the_run_is_not_final(self):
        bad = [close(i, -0.08, self.early + i, self.done + i) for i in range(20)]
        mild = [close(i, -0.07, self.early + i, self.done + i) for i in range(20)]
        short = [close(i, -0.2, self.early + i, self.done + i) for i in range(19)]
        exact = [close(i, -0.075, self.early + i, self.done + i) for i in range(20)]
        bad_text = self.text(bad)
        self.assertIn("rule_result: STOP", bad_text)
        self.assertIn("pre_v3_counted: yes", bad_text)
        self.assertIn("dashboard_v3_only_governs: no", bad_text)
        self.assertIn("net_pnl is incomplete", bad_text)
        self.assertIn("whole_day_void: rejected", bad_text)
        self.assertIn("last_counted_run: 20261111T1430", bad_text)
        self.assertTrue(bad_text.rstrip().endswith("VERDICT: NOT_FINAL"))
        self.assertNotIn("VERDICT: STOP", bad_text)
        self.assertIn("rule_result: CONTINUE", self.text(mild))
        self.assertIn("rule_result: CONTINUE", self.text(short))
        self.assertIn("rule_result: CONTINUE", self.text(exact))

    def test_final_stop_needs_the_default_cutoff_a_late_run_and_the_trade_log(self):
        rows = [close(i, -0.08, self.early + i, self.done + i) for i in range(20)]
        final = self.text(rows, last=self.after)
        self.assertIn("finality: yes", final)
        self.assertIn("closes_reconcile: yes", final)
        self.assertIn("close_ids_unique: yes", final)
        self.assertTrue(final.rstrip().endswith("VERDICT: STOP"))
        other = self.text(rows, last=self.after, cutoff="2026-12-01T15:00:00Z")
        self.assertIn("cutoff_binding: no", other)
        self.assertIn("cutoff_not_default", other)
        self.assertIn("last_counted_run: na", other)
        self.assertTrue(other.rstrip().endswith("VERDICT: NOT_FINAL"))
        broken = self.text(rows, last=self.after, trades=trades_for(rows[:-1]))
        self.assertIn("closes_do_not_reconcile", broken)
        self.assertTrue(broken.rstrip().endswith("VERDICT: NOT_FINAL"))
        dup = [close(i, -0.08, self.early + i, self.done + i) for i in range(20)]
        dup[1]["id"] = dup[0]["id"]
        text = self.text(dup, last=self.after)
        self.assertIn("close_ids_not_unique", text)

    def test_open_partial_and_late_close_and_void_entry_are_out(self):
        kept = close(1, -0.01, self.early, self.done, model="v3-exit-aware")
        voided = close(2, -0.5, self.m.parse_utc(self.m.VOID_START), self.done)
        before = close(3, -0.01, self.m.parse_utc(self.m.VOID_START) - 1, self.done)
        after = close(4, -0.01, self.m.parse_utc(self.m.VOID_END), self.done)
        late = close(5, -0.5, self.early, self.m.parse_utc(self.m.CUTOFF_DEFAULT) + 1)
        on_cut = close(6, -0.01, self.early, self.m.parse_utc(self.m.CUTOFF_DEFAULT))
        rows = [kept, voided, before, after, late, on_cut]
        opened = {"F-20261101T0230-open": dict(mint="openmint", opened=self.early, pnl=10.0, frac=0.3)}
        text = self.text(rows, opened)
        self.assertIn("n_closes: 4", text)
        self.assertIn("excluded_void_entry: 1", text)
        self.assertIn("excluded_after_cutoff: 1", text)
        self.assertIn("open_ignored: 1", text)
        self.assertIn("open_partial_pnl_excluded: 10.000000000", text)
        self.assertIn("total_pnl_sol: -0.040000000", text)
        self.assertIn("pre_v3_closes: 3", text)
        self.assertIn("whole_day_void_would_drop: 1", text)
        self.assertTrue(text.rstrip().endswith("VERDICT: NOT_FINAL"))

    def test_render_is_deterministic(self):
        rows = [close(1, 0.1, self.early, self.done)]
        a = self.text(rows, commit="deadbeef")
        b = self.text(rows, commit="deadbeef")
        self.assertEqual(a, b)
        self.assertIn("data_commit: deadbeef", a)
        self.assertIn("positions_sha256: poshash", a)
        self.assertEqual(a.strip().splitlines()[-1], "VERDICT: NOT_FINAL")


if __name__ == "__main__":
    unittest.main()
