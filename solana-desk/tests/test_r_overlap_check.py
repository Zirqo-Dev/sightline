"""R overlap check replays the sampler. It never emits a day-14 verdict."""
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


def task(run, mint, skip, reason):
    opened = calendar.timegm(time.strptime(run, "%Y%m%dT%H%M")) + 30
    return dict(time=str(opened), run_id=run, mint=mint, result="pass" if not skip else "skip",
                skip_type=skip, reason=reason, source="token-profiles", seed=seed_of(run))


def closed(book, run, mint, pnl, opened=None):
    if opened is None:
        opened = calendar.timegm(time.strptime(run, "%Y%m%dT%H%M")) + 30
    return dict(id=f"{book}-{run}-xxxx", mint=mint, pnl=pnl, opened=opened, closed=opened + 60)


def book(rows, open_rows=None):
    return {"closed": rows, "open": open_rows or {}}


class Overlap(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_pool_and_pretest_seeds(self):
        run = "20261029T1200"
        tasks = [
            task(run, "passA", "", ""),
            task(run, "fresh", "safety", "too_fresh"),
            task(run, "curve", "safety", "not_graduated"),
            task(run, "noroute", "execution", "no_route"),
            task("20261028T1230", "old", "data", "missing_data"),
            task("20261101T0000", "later", "data", "missing_data"),
        ]
        pos = {"books": {
            "F": book([closed("F", run, "passA", -0.2)]),
            "R": book([closed("R", run, "fresh", -0.05)]),
            "S": book([closed("S", run, "fresh", -0.4)]),
        }}
        text = self.m.render(tasks, pos, "abc")
        # One pass and one safety skip. R drew the safety skip, so overlap is 0 and the null is 1/2.
        self.assertIn("20261029T1200 pool=2 n_f=1 k_r=1 overlap=0 expected=0.500000", text)
        self.assertIn("flag: no", text)
        self.assertIn("r_from_safety: 1", text)
        self.assertIn("r_from_pass: 0", text)
        self.assertIn("seed_counts_after: 20261028", text)
        self.assertIn("seed_runs: 2", text)
        self.assertNotIn("VERDICT:", text)
        self.assertIn("secondary: not a verdict input", text)
        self.assertIn("day28_note:", text)
        self.assertIn("f_only_mean_pnl: -0.200000000", text)
        self.assertIn("r_only_mean_pnl: -0.050000000", text)

    def test_flag_when_observed_exceeds_the_replay_null(self):
        run = "20261029T1230"
        tasks = [task(run, f"p{i}", "", "") for i in range(2)]
        tasks += [task(run, f"s{i}", "safety", "too_old") for i in range(8)]
        pos = {"books": {
            "F": book([closed("F", run, "p0", -0.1), closed("F", run, "p1", -0.1)]),
            "R": book([closed("R", run, "p0", 0.2), closed("R", run, "p1", 0.3)]),
            "S": book([]),
        }}
        facts = self.m.analyze(tasks, pos)
        self.assertEqual(facts["observed"], 2)
        self.assertAlmostEqual(facts["expected"], 0.4)
        self.assertGreater(facts["observed"], facts["expected"] + 2 * facts["se"])
        text = self.m.render(tasks, pos, "abc")
        self.assertIn("flag: yes", text)
        self.assertIn("shared_f_mean_pnl: -0.100000000", text)
        self.assertIn("shared_r_mean_pnl: 0.250000000", text)
        self.assertNotIn("VERDICT:", text)

    def test_a_held_mint_in_the_f_list_widens_the_draw(self):
        # F already holds "old", so the new pass and that held pass are both selected
        # and R draws 2 names. Only "new" can overlap. A count that used one opened
        # R ticket would put the expectation at 0.25.
        leads = [("new", None, ""), ("old", None, ""), ("s1", "safety", "x"), ("s2", "safety", "y")]
        st = {"F": (["old"], 0), "R": ([], 0), "S": ([], 0)}
        mu, _var, opened_f, held = self.m.null_one(leads, st)
        self.assertEqual(opened_f, {"new"})
        self.assertEqual(held, ["old"])
        self.assertAlmostEqual(mu, 0.5)
        self.assertGreater(mu, 0.25)

    def test_open_tickets_count_as_entries_and_not_as_realized_pnl(self):
        run = "20261029T1400"
        opened = calendar.timegm(time.strptime(run, "%Y%m%dT%H%M")) + 30
        tasks = [task(run, "both", "", ""), task(run, "other", "safety", "risk_score")]
        pos = {"books": {
            "F": book([], {"F-20261029T1400-both": dict(mint="both", opened=opened, pnl=5.0)}),
            "R": book([closed("R", run, "both", -0.3)]),
            "S": book([]),
        }}
        text = self.m.render(tasks, pos, "abc")
        self.assertIn("shared_pairs: 1", text)
        self.assertIn("shared_f_closed: 0", text)
        self.assertIn("shared_f_mean_pnl: na", text)
        self.assertIn("shared_r_mean_pnl: -0.300000000", text)


if __name__ == "__main__":
    unittest.main()
