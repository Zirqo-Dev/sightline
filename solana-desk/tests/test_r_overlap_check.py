"""R overlap check is a report. It never emits a day-14 verdict."""
import calendar, importlib.util, os, time, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    path = os.path.join(ROOT, "specs", "r_overlap_check.py")
    spec = importlib.util.spec_from_file_location("r_overlap_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def seed_of(run_id):
    slot = calendar.timegm(time.strptime(run_id, "%Y%m%dT%H%M"))
    return str(slot // 60)


def task(run, mint, skip, reason, source="token-profiles"):
    return dict(time="1", run_id=run, mint=mint, result="pass" if not skip else "skip",
                skip_type=skip, reason=reason, source=source, seed=seed_of(run))


def closed(book, run, mint, pnl):
    return dict(id=f"{book}-{run}-xxxx", mint=mint, pnl=pnl, opened=1, closed=2)


def book(closed_rows, open_rows=None):
    return {"closed": closed_rows, "open": open_rows or {}}


class Overlap(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_pool_excludes_not_graduated_and_execution_and_does_not_flag_a_fair_draw(self):
        run = "20261028T1200"
        tasks = [
            task(run, "passA", "", ""),
            task(run, "passB", "", ""),
            task(run, "fresh", "safety", "too_fresh"),
            task(run, "curve", "safety", "not_graduated"),
            task(run, "noroute", "execution", "no_route"),
            task("20261028T1230", "later", "data", "missing_data"),
        ]
        # R took the safety skip, not the F mint. Overlap 0, expected 1/3.
        pos = {"books": {
            "F": book([closed("F", run, "passA", -0.2)]),
            "R": book([closed("R", run, "fresh", -0.05)]),
            "S": book([closed("S", run, "fresh", -0.4)]),
        }}
        text = self.m.render(tasks, pos, "abc")
        self.assertIn("20261028T1200 pool=3 n_f=1 k_r=1 overlap=0 expected=0.333333", text)
        self.assertIn("flag: no", text)
        self.assertIn("r_from_safety: 1", text)
        self.assertIn("r_from_pass: 0", text)
        self.assertIn("seed_constant: no", text)
        self.assertIn("seed_matches_slot_min: 2", text)
        self.assertIn("feed_rank_r: 1:0 2:0 3:1", text)
        self.assertNotIn("VERDICT:", text)
        self.assertIn("secondary: not a verdict input", text)
        self.assertIn("f_only_entries: 1", text)
        self.assertIn("r_only_entries: 1", text)
        self.assertIn("shared_pairs: 0", text)
        self.assertIn("f_only_mean_pnl: -0.200000000", text)
        self.assertIn("r_only_mean_pnl: -0.050000000", text)

    def test_flag_when_observed_exceeds_expected_plus_two_se(self):
        run = "20261028T1230"
        tasks = [task(run, f"p{i}", "", "") for i in range(2)]
        tasks += [task(run, f"s{i}", "safety", "too_old") for i in range(8)]
        pos = {"books": {
            "F": book([closed("F", run, "p0", -0.1), closed("F", run, "p1", -0.1)]),
            "R": book([closed("R", run, "p0", 0.2), closed("R", run, "p1", 0.3)]),
            "S": book([]),
        }}
        facts = self.m.analyze(tasks, pos)
        self.assertEqual(facts["observed"], 2)
        self.assertAlmostEqual(sum(facts["pool_rank_exp"].values()), facts["r_entries"])
        self.assertAlmostEqual(facts["expected"], 0.4)
        self.assertGreater(facts["observed"], facts["expected"] + 2 * facts["se"])
        text = self.m.render(tasks, pos, "abc")
        self.assertIn("flag: yes", text)
        self.assertIn("shared_pairs: 2", text)
        self.assertIn("shared_f_mean_pnl: -0.100000000", text)
        self.assertIn("shared_r_mean_pnl: 0.250000000", text)
        self.assertNotIn("VERDICT:", text)

    def test_open_tickets_count_as_entries_and_not_as_realized_pnl(self):
        run = "20261028T1300"
        tasks = [task(run, "both", "", ""), task(run, "other", "safety", "risk_score")]
        pos = {"books": {
            "F": book([], {"F-20261028T1300-both": dict(mint="both", pnl=5.0)}),
            "R": book([closed("R", run, "both", -0.3)]),
            "S": book([]),
        }}
        text = self.m.render(tasks, pos, "abc")
        self.assertIn("shared_pairs: 1", text)
        self.assertIn("shared_f_closed: 0", text)
        self.assertIn("shared_f_mean_pnl: na", text)
        self.assertIn("shared_r_mean_pnl: -0.300000000", text)
        self.assertIn("r_without_f_runs: none", text)

    def test_hypergeometric_variance_is_zero_when_the_draw_takes_the_whole_pool(self):
        self.assertEqual(self.m.hyper_var(4, 2, 4), 0.0)
        self.assertEqual(self.m.hyper_var(1, 1, 1), 0.0)
        self.assertAlmostEqual(self.m.hyper_var(10, 2, 2), 2 * 0.2 * 0.8 * 8 / 9)


if __name__ == "__main__":
    unittest.main()
