"""Day-28 verdict is a pure report over synthetic fixtures. It does not read desk data."""
import csv, importlib.util, io, json, os, tempfile, unittest
from contextlib import redirect_stderr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    path = os.path.join(ROOT, "specs", "day28_verdict.py")
    spec = importlib.util.spec_from_file_location("day28_verdict", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fields(text):
    out = {}
    for line in text.splitlines():
        if ": " in line:
            key, val = line.split(": ", 1)
            out[key] = val
    return out


class Day28Verdict(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.cut = self.m.D14.parse_utc(self.m.derived_cutoff_text())
        self.day14 = self.m.D14.parse_utc(self.m.D14.CUTOFF_DEFAULT)
        self.n = 0

    def close(self, book, pnl, opened, done, why="time_stop", model=""):
        self.n += 1
        rec = dict(
            id=f"{book}-20261120T1200-{self.n:04d}", mint=f"{book}M{self.n}",
            opened=int(opened), closed=int(done), pnl=pnl, entry=1.0, exit_px=1.0,
            why=why, cost_model=model, entry_cost=0.5, exit_haircut=0.0, frac=1.0,
        )
        buy = dict(book=book, action="buy", mint=rec["mint"], time=str(rec["opened"]), frac="1",
                   pnl="", quote_px="1", net_pnl="999", why="pass", cost_model=model, stale="0")
        sell = dict(book=book, action="sell", mint=rec["mint"], time=str(rec["closed"]), frac="1",
                    pnl=str(pnl), why=why, quote_px="1", cost_model=model, stale="0", net_pnl="999")
        return rec, [buy, sell]

    def pack(self, closed, trades, opens=None, last=None, voids=None):
        books = {}
        for name in "FRS":
            books[name] = {"closed": closed.get(name) or [], "open": (opens or {}).get(name) or {}}
        pos = {"last_run_ts": self.cut if last is None else last, "voids": voids or [], "books": books}
        return pos, trades

    def many(self, book, n, pnl, opened, done, why="time_stop"):
        rows, trades = [], []
        for _ in range(n):
            rec, legs = self.close(book, pnl, opened, done, why=why)
            rows.append(rec)
            trades.extend(legs)
        return rows, trades

    def report(self, pos, trades, cutoff=None, tasks=None, unfixable=False, recorded="CONTINUE",
               snapshot="same", snap_commit="fixture-snap"):
        if snapshot == "same":
            snapshot = pos
        text = self.m.render(
            pos, trades, cutoff or self.m.derived_cutoff_text(), "fixture", "pos", "tr",
            tasks=tasks, unfixable=unfixable, n_boot=40,
            day14_recorded=recorded, open_snapshot=snapshot, open_snapshot_commit=snap_commit,
        )
        return text, fields(text)

    def test_cutoff_is_derived_from_the_day14_constant(self):
        self.assertEqual(self.m.derived_cutoff_text(), "2026-11-25T15:00:00Z")
        self.assertEqual(self.m.derived_last_run(), "20261125T1430")
        self.assertEqual(self.m.HOLM_M, 6)
        self.assertEqual(self.m.holm_alpha(), 0.05 / 6)
        self.assertTrue(self.m.holm_p_passes(0.05 / 6))
        self.assertFalse(self.m.holm_p_passes(0.05 / 6 + 1e-12))
        self.m.assert_shared()

    def test_branch_matches_the_day14_script(self):
        early, late = self.day14 - 86400, self.day14 - 3600
        f, trades = self.many("F", 20, -0.08, early, late)
        pos, trades = self.pack({"F": f}, trades)
        text, got = self.report(pos, trades, recorded="STOP")
        d14 = self.m.D14.render(pos, trades, self.m.D14.CUTOFF_DEFAULT, "c", "p", "t")
        verdict = [line for line in d14.splitlines() if line.startswith("VERDICT: ")][-1].split(": ", 1)[1]
        self.assertEqual(got["day14_verdict"], verdict)
        self.assertEqual(verdict, "STOP")
        self.assertEqual(got["branch"], "1")

    def test_branch1_nogo_and_go_is_impossible(self):
        early, late = self.day14 - 86400, self.day14 - 3600
        f, trades = self.many("F", 20, -0.08, early, late)
        r, rt = self.many("R", 1, 0.0, self.cut - 1000, self.cut - 10)
        pos, trades = self.pack({"F": f, "R": r}, trades + rt)
        text, got = self.report(pos, trades, recorded="STOP")
        self.assertEqual(got["headline"], "NO-GO")
        self.assertEqual(got["VERDICT"], "NO-GO")
        self.assertEqual(got["table_label"], 'post-STOP, descriptive: KILL "edge not shown"')
        self.assertEqual(
            got["headline_text"],
            'v6.5 F: edge not shown. The day-14 early STOP (Nov 3, confirmed Nov 11) stands. '
            'Day-28 table (post-STOP, descriptive): KILL "edge not shown".',
        )
        self.assertNotIn("headline: GO", text)
        self.assertNotIn("VERDICT: GO", text)
        self.assertEqual(got["go_available_in_branch_1"], "no")
        self.assertNotIn("twin", got["headline_text"])
        self.assertNotIn("segment", got["headline_text"])
        self.assertEqual(got["closed_list"], "INVALIDATED")
        self.assertEqual(got["rules_changed"], "no")
        self.assertEqual(got["gate_counts_open_positions"], "no")
        self.assertEqual(got["net_pnl_used"], "no")
        self.assertEqual(got["book_equity_used"], "no")

        # A descriptive PASS still cannot headline GO.
        wins, wt = self.many("F", 20, 0.20, self.cut - 5000, self.cut)
        pos, trades = self.pack({"F": f + wins, "R": r}, trades + wt)
        text, got = self.report(pos, trades, recorded="STOP")
        self.assertEqual(got["day14_verdict"], "STOP")
        self.assertEqual(got["table_label"], "post-STOP, descriptive: PASS")
        self.assertEqual(got["headline"], "NO-GO")
        self.assertIn("PASS.", got["headline_text"])
        self.assertTrue(got["headline_text"].startswith("v6.5 F: edge not shown."))
        self.assertNotIn("\nheadline: GO\n", "\n" + text)
        self.assertNotIn("VERDICT: GO", text)
        self.assertEqual(got["closed_list"], "INCONCLUSIVE")
        self.assertEqual(got["SH-03"], "INVALIDATED")
        self.assertEqual(got["SH-03_changes_table"], "no")

    def test_branch1_inconclusive_when_unfixable(self):
        early, late = self.day14 - 86400, self.day14 - 3600
        f, trades = self.many("F", 20, -0.08, early, late)
        r, rt = self.many("R", 1, 0.0, self.cut - 1000, self.cut - 10)
        opens = {"F": {"F-20261120T1200-open": dict(
            mint="openmint", opened=self.cut - 500, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, stale=0, no_route_count=0,
        )}}
        buy = dict(book="F", action="buy", mint="openmint", time=str(self.cut - 500), frac="1",
                   pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        pos, trades = self.pack({"F": f, "R": r}, trades + rt + [buy], opens=opens)
        text, got = self.report(pos, trades, unfixable=True, recorded="STOP")
        self.assertEqual(got["day14_verdict"], "STOP")
        self.assertEqual(got["branch"], "1")
        self.assertEqual(got["finality"], "no")
        self.assertIn("F_open_mark_unknown", got["finality_reasons"])
        self.assertEqual(got["headline"], "INCONCLUSIVE")
        self.assertEqual(got["VERDICT"], "INCONCLUSIVE")
        self.assertEqual(got["table_label"], "na")
        self.assertEqual(
            got["headline_text"],
            "v6.5 F day-28 numbers can't be computed as registered "
            f"({got['finality_reasons']}). The day-14 early STOP stands.",
        )
        self.assertNotIn("headline: GO", text)
        held, held_got = self.report(pos, trades, unfixable=False, recorded="STOP")
        self.assertEqual(held_got["headline"], "none")
        self.assertEqual(held_got["VERDICT"], "NOT_FINAL")
        self.assertNotIn("NO-GO", held_got["headline"])

    def test_branch2_stop_under_20(self):
        f, trades = self.many("F", 19, -0.40, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        text, got = self.report(*self.pack({"F": f, "R": r}, trades + rt))
        self.assertEqual(got["day14_verdict"], "CONTINUE")
        self.assertEqual(got["branch"], "2")
        self.assertEqual(got["F_closes"], "19")
        self.assertEqual(got["table_label"], 'STOP "filters too strict"')
        self.assertEqual(got["headline"], "INCONCLUSIVE")
        self.assertEqual(got["headline_text"], self.m.STOP_TEXT)
        self.assertEqual(got["VERDICT"], "INCONCLUSIVE")
        self.assertEqual(got["closed_list"], "INCONCLUSIVE")
        self.assertNotIn("headline: NO-GO", text)

    def test_branch2_kill_pass_and_loses_money(self):
        # Edge not shown, including a tie (equal does not beat).
        f, trades = self.many("F", 20, -0.01, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        text, got = self.report(*self.pack({"F": f, "R": r}, trades + rt))
        self.assertEqual(got["day14_verdict"], "CONTINUE")
        self.assertEqual(got["table_label"], 'KILL "edge not shown"')
        self.assertEqual(got["headline"], "NO-GO")
        self.assertEqual(got["headline_text"], "v6.5 F: day-28 KILL (edge not shown).")
        self.assertEqual(got["SH-03"], "INVALIDATED")
        self.assertEqual(got["closed_list"], "INVALIDATED")
        tie_f, tie_tr = self.many("F", 20, 0.01, self.cut - 4000, self.cut)
        _text, tie = self.report(*self.pack({"F": tie_f, "R": r}, tie_tr + rt))
        self.assertEqual(tie["table_label"], 'KILL "edge not shown"')

        # PASS is paper-only. A small control is a warning and not a new gate.
        pf, pt = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        text, got = self.report(*self.pack({"F": pf, "R": r}, pt + rt))
        self.assertEqual(got["table_label"], "PASS")
        self.assertEqual(got["headline"], "PASS")
        self.assertEqual(got["headline_text"], self.m.PASS_TEXT)
        self.assertIn("Never live.", got["headline_text"])
        self.assertEqual(got["closed_list"], "INCONCLUSIVE")
        self.assertEqual(got["SH-03"], "INCONCLUSIVE")
        self.assertIn("addendum", got["SH-03_note"])
        self.assertEqual(got["SH-03_changes_table"], "no")
        self.assertIn("R", got["small_control_warning"])
        self.assertIn("S", got["small_control_warning"])
        self.assertNotIn("twin", got["headline_text"])
        self.assertNotIn("segment", got["headline_text"])
        self.assertEqual(got["segments_change_label"], "no")
        self.assertEqual(got["twin_changes_label"], "no")
        self.assertEqual(got["overlap_changes_verdict"], "no")
        self.assertEqual(got["segment_pr106_boundary"], "uncertain")

        # Beats R and does not make money.
        lf, lt = self.many("F", 20, -0.01, self.cut - 4000, self.cut)
        rr, rrt = self.many("R", 1, -0.05, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": lf, "R": rr}, lt + rrt))
        self.assertGreater(float(got["F_all_in_expectancy"]), float(got["R_all_in_expectancy"]))
        self.assertLessEqual(float(got["F_all_in_expectancy"]), 0)
        self.assertEqual(got["table_label"], 'KILL "beats random but loses money"')
        self.assertEqual(got["headline"], "NO-GO")
        self.assertEqual(got["headline_text"], "v6.5 F: day-28 KILL (beats random but loses money).")
        self.assertEqual(got["SH-03"], "INVALIDATED")

    def test_lot_open_in_the_snapshot_is_marked_not_dropped(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        late, legs = self.close("F", -10.0, self.cut - 100, self.cut + 60)
        pos, trades = self.pack({"F": f + [late], "R": r}, trades + rt + legs)
        # At the cutoff this lot was still open. The later sell price is not the mark.
        snap = {"last_run_ts": self.cut, "books": {"F": {"closed": f, "open": {"F-snap-late": dict(
            mint=late["mint"], opened=late["opened"], entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, last_mark=1.0, stale=0, no_route_count=0,
        )}}, "R": {"closed": r, "open": {}}, "S": {"closed": [], "open": {}}}}
        _text, got = self.report(pos, trades, snapshot=snap)
        self.assertEqual(got["F_closes"], "20")
        self.assertEqual(got["F_after_cutoff"], "1")
        self.assertEqual(got["F_open_at_cutoff"], "1")
        self.assertGreater(float(got["F_all_in_expectancy"]), 0.09)
        self.assertLess(float(got["F_all_in_expectancy"]), 0.1)
        self.assertEqual(got["table_label"], "PASS")
        missing, miss = self.report(pos, trades, snapshot=None, snap_commit=None)
        self.assertEqual(miss["VERDICT"], "NOT_FINAL")
        self.assertIn("open_snapshot_missing", miss["finality_reasons"])

    def test_open_mark_uses_last_mark_not_entry(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        base = trades + rt
        healthy_buy = dict(book="F", action="buy", mint="markmint", time=str(self.cut - 800), frac="1",
                           pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        healthy = {"F": {"F-20261120T1200-mk": dict(
            mint="markmint", opened=self.cut - 800, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, last_mark=2.0, stale=0, no_route_count=0,
        )}}
        pos, rows = self.pack({"F": f, "R": r}, base + [healthy_buy], opens=healthy)
        _text, got = self.report(pos, rows)
        self.assertEqual(got["F_closes"], "20")
        self.assertEqual(got["F_open_at_cutoff"], "1")
        self.assertEqual(got["F_stale_marks"], "0")
        self.assertGreater(float(got["F_all_in_expectancy"]), 0.14)
        self.assertEqual(got["table_label"], "PASS")
        # A later quote is used only when last_mark is absent. Entry is not the mark.
        quoted = {"F": {"F-20261120T1200-q": dict(
            mint="quotemint", opened=self.cut - 800, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, stale=1, no_route_count=0,
        )}}
        buy = dict(book="F", action="buy", mint="quotemint", time=str(self.cut - 800), frac="1",
                   pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        quote = dict(book="F", action="mark", mint="quotemint", time=str(self.cut - 20),
                     frac="0", pnl="", quote_px="2", why="stale", stale="1", net_pnl="999")
        pos, rows = self.pack({"F": f, "R": r}, base + [buy, quote], opens=quoted)
        _text, got = self.report(pos, rows)
        self.assertEqual(got["F_stale_marks"], "1")
        self.assertGreater(float(got["F_all_in_expectancy"]), 0.14)
        bare = {"F": {"F-20261120T1200-bare": dict(
            mint="bare", opened=self.cut - 800, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, no_route_count=0,
        )}}
        bare_buy = dict(book="F", action="buy", mint="bare", time=str(self.cut - 800), frac="1",
                        pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        pos, rows = self.pack({"F": f, "R": r}, base + [bare_buy], opens=bare)
        _text, got = self.report(pos, rows)
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertIn("F_open_mark_unknown", got["finality_reasons"])
        self.assertNotEqual(got["table_label"], "PASS")

    def test_refill_rows_are_skipped(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        base, base_got = self.report(*self.pack({"F": f, "R": r}, trades + rt))
        refill = dict(book="F", action="refill", mint="cash", time=str(self.cut - 50),
                      frac="", pnl="50", sol="100", why="top-up", net_pnl="50", quote_px="")
        mixed = trades[:3] + [refill] + trades[3:] + rt
        _text, got = self.report(*self.pack({"F": f, "R": r}, mixed))
        self.assertEqual(got["F_all_in_expectancy"], base_got["F_all_in_expectancy"])
        self.assertEqual(got["F_closes"], base_got["F_closes"])
        self.assertEqual(got["table_label"], "PASS")
        self.assertEqual(got["refill_rows_skipped"], "1")
        self.assertEqual(got["book_equity_used"], "no")

    def test_rugs_count_at_minus_100(self):
        good, trades = self.many("F", 19, 0.05, self.cut - 4000, self.cut)
        rug, rug_tr = self.close("F", -0.04, self.cut - 3000, self.cut - 10, why="pool_gone")
        r, rt = self.many("R", 1, 0.03, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": good + [rug], "R": r}, trades + rug_tr + rt))
        self.assertEqual(got["F_closes"], "20")
        self.assertEqual(got["F_rugs"], "1")
        self.assertAlmostEqual(float(got["F_all_in_expectancy"]), 0.045, places=6)
        self.assertAlmostEqual(float(got["F_realized_expectancy"]), 0.091, places=6)
        self.assertEqual(got["table_label"], 'KILL "edge not shown"')
        self.assertEqual(got["realized_only_label"], "PASS")
        self.assertIn("all-in governs", got["label_warning"])
        self.assertEqual(got["headline"], "NO-GO")

        # An open confirmed rug is in the expectancy and not in the close count.
        f, ft = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        rr, rrt = self.many("R", 1, 0.04, self.cut - 4000, self.cut)
        opens = {"F": {"F-20261120T1200-rug": dict(
            mint="rugmint", opened=self.cut - 800, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, no_route_count=3, stale=0,
        )}}
        rug_buy = dict(book="F", action="buy", mint="rugmint", time=str(self.cut - 800), frac="1",
                       pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        _text, got = self.report(*self.pack({"F": f, "R": rr}, ft + rrt + [rug_buy], opens=opens))
        self.assertEqual(got["F_closes"], "20")
        self.assertEqual(got["F_rugs"], "1")
        self.assertEqual(got["F_open_at_cutoff"], "1")
        self.assertAlmostEqual(float(got["F_all_in_expectancy"]), 1.0 / 21.0, places=6)
        self.assertEqual(got["table_label"], 'KILL "edge not shown"')

    def test_void_window_and_unknown_are_not_zero(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        voided, vt = self.close("F", -5.0, self.m.D14.parse_utc(self.m.D14.VOID_START) + 10, self.cut - 10)
        _text, got = self.report(*self.pack({"F": f + [voided], "R": r}, trades + rt + vt))
        self.assertEqual(got["F_voided"], "1")
        self.assertEqual(got["F_closes"], "20")
        self.assertAlmostEqual(float(got["F_all_in_expectancy"]), 0.1, places=6)
        self.assertEqual(got["table_label"], "PASS")

        opens = {"F": {"F-20261120T1200-unk": dict(
            mint="unk", opened=self.cut - 50, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0,
        )}}
        buy = dict(book="F", action="buy", mint="unk", time=str(self.cut - 50), frac="1",
                   pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        text, got = self.report(*self.pack({"F": f, "R": r}, trades + rt + [buy], opens=opens))
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertIn("F_open_mark_unknown", got["finality_reasons"])
        self.assertNotEqual(got["table_label"], "PASS")
        self.assertEqual(got["headline"], "none")

    def test_missing_r_is_not_treated_as_zero(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": f}, trades))
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertIn("r_or_f_expectancy_undefined", got["finality_reasons"])
        self.assertEqual(got["table_label"], "na")
        self.assertNotEqual(got["headline"], "PASS")

    def test_trimmed_warning_does_not_change_pass(self):
        f, trades = self.many("F", 19, -0.005, self.cut - 4000, self.cut)
        big, bt = self.close("F", 1.0, self.cut - 4000, self.cut)
        r, rt = self.many("R", 20, 0.0, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": f + [big], "R": r}, trades + bt + rt))
        self.assertEqual(got["trimmed_warning"], "yes")
        self.assertEqual(got["trimmed_changes_label"], "no")
        self.assertEqual(got["table_label"], "PASS")
        self.assertEqual(got["headline"], "PASS")
        self.assertEqual(got["bootstrap_changes_label"], "no")

    def test_nondefault_cutoff_is_not_final(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": f, "R": r}, trades + rt), cutoff="2026-12-01T15:00:00Z")
        self.assertEqual(got["cutoff_binding"], "no")
        self.assertIn("cutoff_not_default", got["finality_reasons"])
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertEqual(got["headline"], "none")

    def test_unfixable_flag_does_not_override_a_final_label(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": f, "R": r}, trades + rt), unfixable=True)
        self.assertEqual(got["headline"], "PASS")
        self.assertEqual(got["unfixable_flag"], "ignored")

    def test_lead_counts_and_malformed_mints_are_not_zero_pl(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        tasks = [
            dict(time=str(self.cut - 10), run_id="20261101T0000", source="profile", reason="", mint="a"),
            dict(time=str(self.cut - 9), run_id="20261101T0000", source="profile", reason="", mint="b"),
            dict(time=str(self.cut - 8), run_id="20261101T0000", source="boost", reason="", mint="c"),
            dict(time=str(self.cut - 7), run_id="20261101T0000", source="manual", reason="invalid_mint", mint="d"),
            dict(time=str(self.cut + 10), run_id="20261101T0000", source="boost", reason="", mint="late"),
        ]
        _text, got = self.report(*self.pack({"F": f, "R": r}, trades + rt), tasks=tasks)
        self.assertEqual(got["lead_source_profile"], "2")
        self.assertEqual(got["lead_source_boost"], "1")
        self.assertEqual(got["lead_source_manual"], "1")
        self.assertEqual(got["malformed_mint_skips"], "1")
        self.assertEqual(got["malformed_mint_counterfactual"], "unknown")
        self.assertEqual(got["table_label"], "PASS")
        self.assertEqual(got["SH-02"], "UNKNOWN")
        self.assertEqual(got["SH-05m-A"], "UNKNOWN")
        self.assertEqual(got["SH-08"], "DIAGNOSTIC")
        self.assertEqual(got["SH-08_holm"], "no")
        self.assertEqual(got["SH-06"], "not judged Nov 25")
        self.assertEqual(got["SH-07"], "not judged Nov 25")

    def test_render_is_deterministic(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        pos, trades = self.pack({"F": f, "R": r}, trades + rt)
        self.assertEqual(self.report(pos, trades)[0], self.report(pos, trades)[0])

    def test_holm_rules_on_synthetic_inputs(self):
        self.assertEqual(self.m.judge_sh02(30, 0.20, 0.04), ("VALIDATED", ""))
        self.assertEqual(self.m.judge_sh02(30, 0.05, 0.001), ("INVALIDATED", ""))
        self.assertEqual(self.m.judge_sh02(29, 0.20, 0.01)[0], "INCONCLUSIVE")
        status, note = self.m.judge_sh02(30, 0.20, 0.05)
        self.assertEqual(status, "INCONCLUSIVE")
        self.assertIn("futility", note)
        self.assertEqual(self.m.judge_sh03(20, True, 0.02, 0.01, True), ("VALIDATED", ""))
        status, note = self.m.judge_sh03(20, True, 0.02, 0.0, True)
        self.assertEqual(status, "INCONCLUSIVE")
        self.assertEqual(note, self.m.HOLM_FOOTNOTE)
        self.assertEqual(self.m.judge_sh03(20, False, -0.1, -0.2, True)[0], "INVALIDATED")
        self.assertEqual(self.m.judge_sh03(19, False, None, None, True)[0], "INCONCLUSIVE")
        self.assertEqual(self.m.judge_sh03(20, None, None, None, False)[0], "UNKNOWN")
        self.assertEqual(self.m.judge_sh05m_a(60, 2.0, 1.1, 1.1, 3.0), ("VALIDATED", ""))
        status, note = self.m.judge_sh05m_a(60, 2.0, 1.1, 0.9, 3.0)
        self.assertEqual((status, note), ("INCONCLUSIVE", self.m.HOLM_FOOTNOTE))
        self.assertEqual(self.m.judge_sh05m_a(60, 2.0, 1.1, 1.1, 1.4)[0], "INVALIDATED")
        self.assertEqual(self.m.judge_sh05m_a(59, 2.0, 1.1, 1.1, 1.4)[0], "INCONCLUSIVE")
        self.assertEqual(self.m.judge_sh05m_b(125, 0.15, 0.15), ("VALIDATED", ""))
        status, note = self.m.judge_sh05m_b(125, 0.146, 0.146)
        self.assertEqual(status, "INCONCLUSIVE")
        self.assertIn("futility", note)
        self.assertEqual(self.m.judge_sh05m_b(125, 0.15, 0.146)[0], "INCONCLUSIVE")
        self.assertEqual(self.m.judge_sh05m_b(124, 0.2, 0.2)[0], "INCONCLUSIVE")
        self.assertEqual(self.m.judge_sh09(30, 20, 3.0, 1.2, 1.2, 4.0), ("VALIDATED", ""))
        self.assertEqual(self.m.judge_sh09(30, 20, 3.0, 1.2, 1.2, 1.4)[0], "INVALIDATED")
        self.assertEqual(self.m.judge_sh09(29, 20, 3.0, 1.2, 1.2, 4.0)[0], "INCONCLUSIVE")
        self.assertEqual(self.m.judge_sh08(), ("DIAGNOSTIC", "never changes a verdict"))
        self.assertFalse(self.m.holm_p_passes(0.04))

    def _argv(self, directory, commit="fixture", snap="fixture-snap", recorded="CONTINUE", extra=None):
        argv = [
            "--data-dir", directory, "--data-commit", commit,
            "--day14-recorded", recorded, "--open-snapshot-commit", snap,
        ]
        if extra:
            argv.extend(extra)
        return argv

    def test_main_refuses_real_data_before_the_cutoff(self):
        self.assertTrue(self.m.refused_before_cutoff(self.cut - 1, False))
        self.assertFalse(self.m.refused_before_cutoff(self.cut - 1, True))
        self.assertFalse(self.m.refused_before_cutoff(self.cut, False))
        calls = []
        orig = self.m.os.path.isfile

        def spy(path):
            calls.append(path)
            raise AssertionError("opened " + str(path))

        self.m.os.path.isfile = spy
        try:
            err = io.StringIO()
            with redirect_stderr(err):
                code = self.m.main(self._argv("/tmp/day28-fixture-not-real"), now=self.cut - 1)
        finally:
            self.m.os.path.isfile = orig
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])
        self.assertIn("refused before the cutoff", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            code = self.m.main(self._argv("/tmp/day28-fixture-not-real"), now=self.cut)
        self.assertEqual(code, 2)
        self.assertNotIn("refused before the cutoff", err.getvalue())
        self.assertIn("positions.json", err.getvalue())

    def test_refusal_floor_ignores_an_earlier_day14_constant(self):
        old = self.m.D14.CUTOFF_DEFAULT
        self.m.D14.CUTOFF_DEFAULT = "2026-10-01T00:00:00Z"
        try:
            before = self.m.D14.parse_utc("2026-11-20T00:00:00Z")
            at_floor = self.m.D14.parse_utc(self.m.REGISTERED_FLOOR)
            self.assertTrue(self.m.refused_before_cutoff(before, False))
            self.assertFalse(self.m.refused_before_cutoff(at_floor, False))
        finally:
            self.m.D14.CUTOFF_DEFAULT = old

    def test_fixture_mode_needs_a_sentinel_and_is_not_a_verdict(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        pos, trades = self.pack({"F": f, "R": r}, trades + rt)
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "positions.json"), "w") as fh:
                json.dump(pos, fh)
            with open(os.path.join(directory, "open-snapshot.json"), "w") as fh:
                json.dump(pos, fh)
            keys = []
            for row in trades:
                for key in row:
                    if key not in keys:
                        keys.append(key)
            with open(os.path.join(directory, "trades.csv"), "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=keys)
                writer.writeheader()
                writer.writerows(trades)
            import builtins
            seen = []
            opened = []
            scanned = []
            orig = self.m.os.path.isfile
            orig_open = builtins.open
            orig_scan = os.scandir

            def spy(path):
                seen.append(os.path.basename(path))
                return orig(path)

            def spy_open(*args, **kwargs):
                opened.append(args[0] if args else "")
                return orig_open(*args, **kwargs)

            def spy_scan(*args, **kwargs):
                scanned.append(args[0] if args else "")
                return orig_scan(*args, **kwargs)

            self.m.os.path.isfile = spy
            self.m.os.scandir = spy_scan
            builtins.open = spy_open
            os.scandir = spy_scan
            try:
                err = io.StringIO()
                with redirect_stderr(err):
                    code = self.m.main(
                        self._argv(directory, extra=["--test", "--bootstrap", "20"]),
                        now=self.cut - 1,
                    )
            finally:
                self.m.os.path.isfile = orig
                self.m.os.scandir = orig_scan
                builtins.open = orig_open
                os.scandir = orig_scan
            self.assertEqual(code, 2)
            self.assertEqual(seen, [self.m.SENTINEL_NAME])
            self.assertEqual(opened, [])
            self.assertEqual(scanned, [])
            self.assertIn("DAY28_SYNTHETIC_FIXTURE", err.getvalue())
            open(os.path.join(directory, self.m.SENTINEL_NAME), "w").close()
            err = io.StringIO()
            with redirect_stderr(err):
                code = self.m.main(
                    self._argv(directory, commit="a" * 40, extra=["--test"]),
                    now=self.cut - 1,
                )
            self.assertEqual(code, 2)
            self.assertIn("40-hex", err.getvalue())
            err = io.StringIO()
            out = io.StringIO()
            from contextlib import redirect_stdout
            with redirect_stderr(err), redirect_stdout(out):
                code = self.m.main(
                    self._argv(directory, extra=["--synthetic", "--bootstrap", "20"]),
                    now=self.cut - 1,
                )
            self.assertEqual(code, 0, err.getvalue())
            self.assertIn("VERDICT: SYNTHETIC (not a verdict)", out.getvalue())
            self.assertNotIn("VERDICT: PASS", out.getvalue())
            self.assertIn("table_label: PASS", out.getvalue())

    def test_real_run_rejects_any_other_bootstrap(self):
        err = io.StringIO()
        with redirect_stderr(err):
            code = self.m.main(
                self._argv("/tmp/day28-fixture-not-real", extra=["--bootstrap", "20"]),
                now=self.cut,
            )
        self.assertEqual(code, 2)
        self.assertIn("10000", err.getvalue())

    def test_non_finite_values_are_not_final(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        bad = list(trades)
        for row in bad:
            if row.get("action") == "sell" and row.get("book") == "F":
                row["pnl"] = "nan"
                break
        pos, rows = self.pack({"F": f, "R": r}, bad + rt)
        _text, got = self.report(pos, rows)
        self.assertIn("non_finite", got["finality_reasons"])
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertNotEqual(got["table_label"], "PASS")
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        opens = {"F": {"F-20261120T1200-inf": dict(
            mint="infmint", opened=self.cut - 50, entry=1.0, frac=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, last_mark="inf", stale=0,
        )}}
        pos, rows = self.pack({"F": f, "R": r}, trades + rt, opens=opens)
        _text, got = self.report(pos, rows)
        self.assertIn("F_non_finite", got["finality_reasons"])
        self.assertEqual(got["VERDICT"], "NOT_FINAL")

    def test_rug_keeps_a_logged_loss_past_minus_100(self):
        good, trades = self.many("F", 19, 0.05, self.cut - 4000, self.cut)
        rug, rug_tr = self.close("F", -0.51, self.cut - 3000, self.cut - 10, why="pool_gone")
        r, rt = self.many("R", 1, 0.0, self.cut - 4000, self.cut)
        _text, got = self.report(*self.pack({"F": good + [rug], "R": r}, trades + rug_tr + rt))
        self.assertAlmostEqual(float(got["F_all_in_expectancy"]), (19 * 0.1 - 1.02) / 20, places=6)
        self.assertNotAlmostEqual(float(got["F_all_in_expectancy"]), (19 * 0.1 - 1.0) / 20, places=6)

    def test_day14_recorded_must_match_continue_as_well_as_stop(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        pos, rows = self.pack({"F": f, "R": r}, trades + rt)
        _text, got = self.report(pos, rows, recorded="CONTINUE")
        self.assertEqual(got["day14_verdict"], "CONTINUE")
        self.assertEqual(got["branch"], "2")
        self.assertEqual(got["table_label"], "PASS")
        _text, bad = self.report(pos, rows, recorded="STOP")
        self.assertEqual(bad["VERDICT"], "NOT_FINAL")
        self.assertIn("day14_branch_mismatch", bad["finality_reasons"])
        self.assertEqual(bad["branch"], "na")
        self.assertNotEqual(bad["headline"], "NO-GO")

    def test_trade_log_keeps_lots_older_than_the_positions_tail(self):
        rows, legs = [], []
        for i in range(201):
            rec, pair = self.close("F", 0.05, self.cut - 8000 - i, self.cut - 3000 + i)
            rows.append(rec)
            legs.extend(pair)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        kept = rows[-200:]
        pos, trades = self.pack({"F": kept, "R": r}, legs + rt)
        _text, got = self.report(pos, trades, recorded="CONTINUE")
        self.assertEqual(got["finality"], "yes", got["finality_reasons"])
        self.assertEqual(got["F_closes"], "201")
        self.assertEqual(got["table_label"], "PASS")

    def test_malformed_sell_pnl_is_not_final(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        for row in trades:
            if row.get("action") == "sell":
                row["pnl"] = "not-a-number"
                break
        pos, rows = self.pack({"F": f, "R": r}, trades + rt)
        _text, got = self.report(pos, rows)
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertIn("malformed_row", got["finality_reasons"])

    def test_snapshot_must_hold_every_lot_still_open_at_the_cutoff(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        buy = dict(book="F", action="buy", mint="stillopen", time=str(self.cut - 700), frac="1",
                   pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        pos, rows = self.pack({"F": f, "R": r}, trades + rt + [buy])
        # The snapshot is at the cutoff but omits the lot that is still open.
        _text, got = self.report(pos, rows, snapshot=pos)
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertIn("F_open_snapshot_incomplete", got["finality_reasons"])
        self.assertNotEqual(got["table_label"], "PASS")
        late_snap = dict(pos)
        late_snap["last_run_ts"] = self.cut + 60
        late_snap["books"] = {
            "F": {"closed": f, "open": {"F-still": dict(
                mint="stillopen", opened=self.cut - 700, entry=1.0, frac=1.0, pnl=0.0,
                entry_cost=0.5, exit_haircut=0.0, last_mark=1.0, stale=0,
            )}},
            "R": pos["books"]["R"], "S": pos["books"]["S"],
        }
        _text, late = self.report(pos, rows, snapshot=late_snap)
        self.assertIn("open_snapshot_after_cutoff", late["finality_reasons"])
        self.assertNotEqual(late["table_label"], "PASS")

    def test_snapshot_lot_without_a_trade_is_not_final(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        pos, rows = self.pack({"F": f, "R": r}, trades + rt)
        snap = {
            "last_run_ts": self.cut,
            "books": {
                "F": {"closed": f, "open": {"F-ghost": dict(
                    mint="ghost", opened=self.cut - 50, entry=1.0, frac=1.0, pnl=0.0,
                    entry_cost=0.5, exit_haircut=0.0, last_mark=1.0, stale=0,
                )}},
                "R": pos["books"]["R"], "S": pos["books"]["S"],
            },
        }
        _text, got = self.report(pos, rows, snapshot=snap)
        self.assertIn("F_open_snapshot_mismatch", got["finality_reasons"])
        self.assertEqual(got["VERDICT"], "NOT_FINAL")

    def test_missing_open_frac_is_not_final(self):
        f, trades = self.many("F", 20, 0.05, self.cut - 4000, self.cut)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        buy = dict(book="F", action="buy", mint="nofrac", time=str(self.cut - 600), frac="1",
                   pnl="", quote_px="1", net_pnl="", why="pass", cost_model="", stale="0")
        opens = {"F": {"F-nofrac": dict(
            mint="nofrac", opened=self.cut - 600, entry=1.0, pnl=0.0,
            entry_cost=0.5, exit_haircut=0.0, last_mark=1.0, stale=0,
        )}}
        pos, rows = self.pack({"F": f, "R": r}, trades + rt + [buy], opens=opens)
        _text, got = self.report(pos, rows)
        self.assertIn("F_open_frac_missing", got["finality_reasons"])
        self.assertEqual(got["VERDICT"], "NOT_FINAL")
        self.assertNotEqual(got["table_label"], "PASS")

    def test_day14_not_final_recompute_keeps_the_recorded_branch(self):
        early, late = self.day14 - 86400, self.day14 - 3600
        f, trades = self.many("F", 20, -0.08, early, late)
        r, rt = self.many("R", 1, 0.0, self.cut - 1000, self.cut - 10)
        f[1]["id"] = f[0]["id"]
        pos, rows = self.pack({"F": f, "R": r}, trades + rt)
        _text, got = self.report(pos, rows, recorded="STOP", unfixable=True)
        self.assertEqual(got["day14_verdict"], "NOT_FINAL")
        self.assertNotIn("day14_branch_mismatch", got["finality_reasons"])
        self.assertEqual(got["branch"], "1")
        self.assertEqual(got["headline"], "INCONCLUSIVE")
        self.assertIn("early STOP stands", got["headline_text"])

    def test_tied_close_timestamps_use_the_sell_row_order(self):
        rows, legs = [], []
        same = self.cut - 10
        for i in range(250):
            rec, pair = self.close("F", 0.05, self.cut - 9000 - i, same)
            rows.append(rec)
            legs.extend(pair)
        r, rt = self.many("R", 1, 0.01, self.cut - 4000, self.cut)
        pos, trades = self.pack({"F": rows[-200:], "R": r}, legs + rt)
        _text, got = self.report(pos, trades, recorded="CONTINUE")
        self.assertEqual(got["finality"], "yes", got["finality_reasons"])
        self.assertEqual(got["F_closes"], "250")
        self.assertEqual(got["table_label"], "PASS")

    def test_fixture_mode_refuses_a_git_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            os.mkdir(os.path.join(directory, ".git"))
            open(os.path.join(directory, self.m.SENTINEL_NAME), "w").close()
            err = io.StringIO()
            with redirect_stderr(err):
                code = self.m.main(
                    self._argv(directory, extra=["--test"]),
                    now=self.cut - 1,
                )
            self.assertEqual(code, 2)
            self.assertIn("git checkout", err.getvalue())


if __name__ == "__main__":
    unittest.main()
