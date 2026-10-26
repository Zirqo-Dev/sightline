"""SPD-47 kill_status.json: per-ticker kill-line status; the kill webhook fires only when changed_since_last."""
import json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import valuation as V  # noqa: E402
import prefilter as P  # noqa: E402
from test_regressions import run_main, quiet_req, fresh_state  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "kill_status.json")))
NOW = "2026-10-26T20:07:00Z"
KEYS = {"line", "metric", "value", "hit", "first_hit_at", "changed_since_last"}


def tracked_with_units():
    t = json.loads(json.dumps(FX["tracked"]))
    for tk in t:
        t[tk]["asset_id"] = "aa" * 28 + tk.encode().hex()
    return t


def patches(tracked, run):
    def pool(http, u):
        tk = bytes.fromhex(u[56:]).decode()
        return {"ada_side": run[tk]["ada_side"], "mcap_ada": None, "price_ada": 1.0, "basis": "geckoterminal"}

    def holders(self, u, m):
        return run[bytes.fromhex(u[56:]).decode()]["holders"], False
    return [mock.patch.object(P, "tracked_names", lambda paper, log: json.loads(json.dumps(tracked))),
            mock.patch.object(P, "gt_pool", pool), mock.patch.object(P.Chain, "holders", holders),
            mock.patch.object(P.Chain, "handle_address", lambda self, p, n: None)]


def kills(run):
    return sorted(t["name"] for t in run["triggers"] if t["kind"] == "kill_line")


class Entry(unittest.TestCase):
    def e(self, prev, crossed, complete=True, **kw):
        lines = {"ada_side": 5000.0}
        values = {"ada_side": crossed}
        cm = {"ada_side": (crossed, 5000.0)} if crossed is not None and crossed < 5000 else {}
        return V.kill_status_entry(prev, "", values, lines, cm, complete and crossed is not None, NOW, **kw)

    def test_shape_and_first_hit(self):
        r = self.e(None, 4000)
        self.assertTrue(KEYS <= set(r))
        self.assertEqual((r["hit"], r["first_hit_at"], r["changed_since_last"], r["metric"], r["line"], r["value"]),
                         (True, NOW, True, "ada_side", 5000.0, 4000))

    def test_unchanged_hit_is_not_a_change_and_keeps_first_hit(self):
        r = self.e(FX["prev_kill_status"]["SER"], 3990)
        self.assertFalse(r["changed_since_last"])
        self.assertEqual(r["first_hit_at"], "2026-10-25T20:08:00Z")

    def test_recovery_is_a_change(self):
        r = self.e(FX["prev_kill_status"]["SER"], 6000)
        self.assertEqual((r["hit"], r["changed_since_last"], r["first_hit_at"]), (False, True, None))

    def test_data_gap_keeps_hit_no_change(self):
        r = self.e(FX["prev_kill_status"]["SER"], None, complete=False)
        self.assertEqual((r["hit"], r["changed_since_last"]), (True, False))

    def test_line_change_is_a_change(self):
        prev = dict(FX["prev_kill_status"]["SER"], line=6000.0)
        self.assertTrue(self.e(prev, 4000)["changed_since_last"])

    def test_deeper_refire_is_a_change(self):
        self.assertTrue(self.e(FX["prev_kill_status"]["SER"], 3000, deeper=True)["changed_since_last"])

    def test_seed_from_state_bool(self):
        self.assertFalse(self.e(None, 4000, prev_hit=True)["changed_since_last"])


class Oct26Refire(unittest.TestCase):
    def test_ser_and_evil_do_not_refire_when_nothing_moved(self):
        tracked = tracked_with_units()
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)  # kill_reported empty: kill_decide alone would treat both as newly crossed
            json.dump(FX["prev_kill_status"], open(os.path.join(d, "kill_status.json"), "w"))
            run = run_main(d, quiet_req, patches(tracked, FX["run"]), dry=False)
            ks = json.load(open(os.path.join(d, "kill_status.json")))
        self.assertEqual(kills(run), [])
        self.assertEqual(sorted(ks), ["EVIL", "SER"])
        for tk in ks:
            self.assertTrue(KEYS <= set(ks[tk]))
            self.assertTrue(ks[tk]["hit"])
            self.assertFalse(ks[tk]["changed_since_last"])
        self.assertEqual(ks["EVIL"]["value"], 87)
        self.assertEqual(ks["SER"]["first_hit_at"], "2026-10-25T20:08:00Z")

    def test_first_cross_fires_once_then_recovers_and_refires(self):
        tracked = {"SER": tracked_with_units()["SER"]}
        seq = [(4000, ["SER"]), (3990, []), (6000, []), (6000, []), (3900, ["SER"])]
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            got, changed = [], []
            for side, _ in seq:
                run = run_main(d, quiet_req, patches(tracked, {"SER": {"ada_side": side, "holders": 300}}), dry=False)
                got.append(kills(run))
                changed.append(json.load(open(os.path.join(d, "kill_status.json")))["SER"]["changed_since_last"])
        self.assertEqual(got, [w for _, w in seq])
        self.assertEqual(changed, [True, False, True, False, True])

    def test_desk_hit_marker_still_never_sends(self):
        tracked = {"SER": dict(tracked_with_units()["SER"], desk_hit=True, text="hit: ADA side < 5000")}
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run = run_main(d, quiet_req, patches(tracked, {"SER": {"ada_side": 4000, "holders": 300}}), dry=False)
            ks = json.load(open(os.path.join(d, "kill_status.json")))
            st = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(kills(run), [])
        self.assertTrue(ks["SER"]["hit"] and ks["SER"]["desk_hit"])
        self.assertTrue(st["kill_reported"]["SER"]["desk_hit"])

    def test_dry_run_does_not_write_kill_status(self):
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run_main(d, quiet_req, patches(tracked_with_units(), FX["run"]), dry=True)
            self.assertFalse(os.path.exists(os.path.join(d, "kill_status.json")))


class LineSources(unittest.TestCase):
    def test_lines_from_paper_and_log_with_hit_marker(self):
        t = P.tracked_names(FX["paper"], FX["log"])
        self.assertEqual(t["EVIL"]["kill"], {"holders_below": 120})
        self.assertEqual(t["SER"]["source"], "log.kill_lines")
        self.assertTrue(t["OLDX"]["desk_hit"])

    def test_desk_data_first_then_main(self):
        with tempfile.TemporaryDirectory() as desk, tempfile.TemporaryDirectory() as main:
            for base, tag in ((desk, "desk"), (main, "main")):
                os.makedirs(os.path.join(base, "solada"))
                json.dump({"from": tag}, open(os.path.join(base, "solada", "log.json"), "w"))
            json.dump({"from": "main"}, open(os.path.join(main, "solada", "paper.json"), "w"))
            self.assertEqual(P.load_desk_json("solada/log.json", desk, main), ({"from": "desk"}, "desk-data"))
            self.assertEqual(P.load_desk_json("solada/paper.json", desk, main), ({"from": "main"}, "main"))


if __name__ == "__main__":
    unittest.main()
