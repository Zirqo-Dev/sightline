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


def close(mint, pnl, opened, done, model=""):
    return dict(id=f"F-20261028T1200-{mint[:4]}", mint=mint, opened=opened, closed=done,
                pnl=pnl, entry=1.0, exit_px=1.0, why="time_stop", cost_model=model,
                entry_sol_reserve=100.0, exit_sol_reserve=100.0)


def positions(closed, opened=None):
    return {"books": {"F": {"closed": closed, "open": opened or {}}, "R": {"closed": [], "open": {}},
                      "S": {"closed": [], "open": {}}}}


class Day14Verdict(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.early = ts("2026-10-28T12:00:00")
        self.done = ts("2026-10-28T14:00:00")

    def text(self, closed, opened=None, trades=None, commit="abc"):
        return self.m.render(positions(closed, opened), trades or [], self.m.CUTOFF_DEFAULT, commit)

    def test_constants_are_the_committed_rule(self):
        self.assertEqual(self.m.COST_VERSION, "as_logged")
        self.assertEqual(self.m.CUTOFF_DEFAULT, "2026-11-11T15:00:00Z")
        self.assertEqual(self.m.VOID_START, "2026-10-31T01:30:00Z")
        self.assertEqual(self.m.VOID_END, "2026-11-01T00:00:00Z")
        self.assertEqual(self.m.VOID_SOURCE, "committed_constant")
        self.assertEqual(self.m.C.TICKET_SOL, 0.5)
        self.assertEqual(self.m.C.MIN_F_CLOSES, 20)
        self.assertEqual(self.m.C.EARLY_STOP_EXP, -0.15)

    def test_stop_only_when_sample_and_expectancy_are_both_past_the_line(self):
        bad = [close(f"m{i}", -0.08, self.early, self.done) for i in range(20)]
        mild = [close(f"m{i}", -0.07, self.early, self.done) for i in range(20)]
        short = [close(f"m{i}", -0.2, self.early, self.done) for i in range(19)]
        exact = [close(f"m{i}", -0.075, self.early, self.done) for i in range(20)]
        self.assertTrue(self.text(bad).rstrip().endswith("VERDICT: STOP"))
        self.assertTrue(self.text(mild).rstrip().endswith("VERDICT: CONTINUE"))
        self.assertTrue(self.text(short).rstrip().endswith("VERDICT: CONTINUE"))
        self.assertTrue(self.text(exact).rstrip().endswith("VERDICT: CONTINUE"))

    def test_open_partial_and_late_close_and_void_entry_are_out(self):
        kept = close("keep", -0.01, self.early, self.done, model="v3-exit-aware")
        voided = close("void", -0.5, self.m.parse_utc(self.m.VOID_START), self.done)
        before = close("edge", -0.01, self.m.parse_utc(self.m.VOID_START) - 1, self.done)
        after = close("back", -0.01, self.m.parse_utc(self.m.VOID_END), self.done)
        late = close("late", -0.5, self.early, self.m.parse_utc(self.m.CUTOFF_DEFAULT) + 1)
        on_cut = close("cut", -0.01, self.early, self.m.parse_utc(self.m.CUTOFF_DEFAULT))
        opened = {"F-20261101T0230-open": dict(mint="open", opened=self.early, pnl=10.0, frac=0.3)}
        text = self.text([kept, voided, before, after, late, on_cut], opened)
        self.assertIn("n_closes: 4", text)  # kept, before, after, on_cut
        self.assertIn("excluded_void_entry: 1", text)
        self.assertIn("excluded_after_cutoff: 1", text)
        self.assertIn("open_ignored: 1", text)
        self.assertIn("open_partial_pnl_excluded: 10.000000000", text)
        self.assertIn("total_pnl_sol: -0.040000000", text)
        self.assertIn("pre_v3_closes: 3", text)
        self.assertTrue(text.rstrip().endswith("VERDICT: CONTINUE"))

    def test_v3_recompute_does_not_change_a_stop(self):
        rows, trades = [], []
        for i in range(20):
            mint = f"mint{i}"
            row = close(mint, -0.2, self.early + i, self.done + i, model="v3-exit-aware")
            rows.append(row)
            trades.append(dict(book="F", mint=mint, action="sell", time=str(self.done + i), frac="1"))
        text = self.text(rows, trades=trades)
        self.assertIn("v3_recompute: informational", text)
        self.assertIn("v3_recompute_note:", text)
        self.assertNotIn("VERDICT: CONTINUE", text)
        self.assertTrue(text.rstrip().endswith("VERDICT: STOP"))
        # The rebuilt expectancy is the haircut on a flat quote, not the logged -40% per trade.
        exp = [line for line in text.splitlines() if line.startswith("v3_recompute_expectancy:")][0]
        self.assertGreater(float(exp.split()[1]), -0.15)

    def test_v3_recompute_is_withheld_when_a_close_has_no_quote(self):
        rows = [close(f"m{i}", -0.2, self.early, self.done) for i in range(20)]
        rows[0]["entry_sol_reserve"] = None
        text = self.text(rows, trades=[])
        self.assertIn("v3_recompute: not_possible", text)
        self.assertTrue(text.rstrip().endswith("VERDICT: STOP"))

    def test_render_is_deterministic_and_names_the_commit(self):
        rows = [close("one", 0.1, self.early, self.done)]
        a = self.text(rows, commit="deadbeef")
        b = self.text(rows, commit="deadbeef")
        self.assertEqual(a, b)
        self.assertIn("data_commit: deadbeef", a)
        self.assertIn("cost_version: as_logged", a)
        self.assertEqual(a.strip().splitlines()[-1], "VERDICT: CONTINUE")


if __name__ == "__main__":
    unittest.main()
