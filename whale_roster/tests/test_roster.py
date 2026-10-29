"""Offline whale roster tests. No network and no secrets."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
import rugger_blacklist as RB  # noqa: E402
import whale_roster as WR  # noqa: E402
from whale_roster.sync import sync_roster  # noqa: E402

OLD = "founder" + "_whale"
OLD_FLAG = OLD + "_move"


def reading(price, liquidity=None):
    return {"answered": True, "price": price, "liquidity": liquidity, "reason": None}


class RenameTests(unittest.TestCase):
    def test_old_log_key_still_reads_and_writer_emits_the_new_one(self):
        old = {OLD: {"handle": "Grumpy Walrus", "net_flow_ada": -31, "line": "trimmed"}}
        rows = WR.read_log_whales(old)
        self.assertEqual(rows[0]["codename"], "Grumpy Walrus")
        self.assertEqual(rows[0]["net_flow_ada"], -31)
        emitted = WR.emit_log_whales(rows)
        self.assertEqual(list(emitted), ["whales"])
        self.assertNotIn(OLD, emitted)
        self.assertEqual(emitted["whales"][0]["net_flow_ada"], -31)
        self.assertNotIn("handle", emitted["whales"][0])

    def test_new_list_wins_over_the_old_object(self):
        log = {
            "whales": [{"codename": "Sleepy Otter", "role": "watch", "status": "candidate", "net_flow_ada": 4}],
            OLD: {"handle": "Grumpy Walrus", "net_flow_ada": 99},
        }
        rows = WR.read_log_whales(log)
        self.assertEqual([r["codename"] for r in rows], ["Sleepy Otter"])
        self.assertEqual(rows[0]["net_flow_ada"], 4)

    def test_flag_kind_accepts_both_names(self):
        self.assertTrue(WR.flag_kind(WR.NEW_FLAG))
        self.assertTrue(WR.flag_kind(OLD_FLAG))
        self.assertFalse(WR.flag_kind("coordinated_buys"))


class CodenameTests(unittest.TestCase):
    def test_same_wallet_id_keeps_the_same_name(self):
        first = WR.codename_for("w-aaaaaaaa")
        self.assertEqual(first, WR.codename_for("w-aaaaaaaa"))
        self.assertNotEqual(first, WR.LEAD_CODENAME)
        self.assertRegex(first, r"^[A-Z][a-z]+ [A-Z][a-z]+$")

    def test_a_taken_name_moves_to_the_next_animal(self):
        base = WR.codename_for("w-bbbbbbbb")
        other = WR.codename_for("w-bbbbbbbb", taken=[base])
        self.assertNotEqual(base, other)


class DiscoverTests(unittest.TestCase):
    def test_overlap_becomes_a_candidate_and_not_active(self):
        log = {"cardano_v2": {"clusters": [
            {"ticker": "ASCEND", "wallets": ["w-aaaaaaaa", "w-bbbbbbbb"]},
            {"ticker": "STRIKE", "wallets": ["w-aaaaaaaa", "w-bbbbbbbb"]},
            {"ticker": "FLOW", "wallets": ["w-cccccccc"]},
        ], "rotation": {"trends": [{"token": "ASCEND", "trend": "accumulating"}]},
            "hot": [{"ticker": "ASCEND", "line": "overlap"}]}}
        found = WR.discover(log, {"moves": []}, ["w-cccccccc"], {"ASCEND", "STRIKE", "FLOW"})
        self.assertEqual(found, ["w-aaaaaaaa", "w-bbbbbbbb"])
        roster = WR.empty_roster()
        WR.add_candidates(roster, found, ["cluster overlap"])
        self.assertTrue(all(w["status"] == "candidate" for w in roster["whales"]))
        self.assertTrue(all(not w["wallets"] for w in roster["whales"]))
        self.assertEqual(len({w["codename"] for w in roster["whales"]}), 2)

    def test_large_positions_across_tokens_qualify_without_a_partner(self):
        liquidity = {"moves": [
            {"actor_tag": "w-dddddddd", "ticker": "ASCEND", "ada": 2500},
            {"actor_tag": "w-dddddddd", "ticker": "STRIKE", "ada": 800},
        ]}
        found = WR.discover({"cardano_v2": {}}, liquidity, [], {"ASCEND", "STRIKE"})
        self.assertEqual(found, ["w-dddddddd"])

    def test_lead_tag_is_not_proposed_again(self):
        log = {"cardano_v2": {"clusters": [
            {"ticker": "ASCEND", "wallets": ["w-f3186187", "w-aaaaaaaa"]},
            {"ticker": "STRIKE", "wallets": ["w-f3186187", "w-aaaaaaaa"]},
        ]}}
        found = WR.discover(log, {}, ["w-f3186187"], {"ASCEND", "STRIKE"})
        self.assertEqual(found, ["w-aaaaaaaa"])


class ScoreTests(unittest.TestCase):
    def event(self):
        return WR.new_event({"at": 1_700_000_000, "ticker": "ASCEND", "action": "accumulate", "tx": "ab"}, "Grumpy Walrus", "unit")

    def test_windows_match_the_outcome_tracker(self):
        at = 1_700_000_000
        ev = self.event()
        calls = []

        def quote(unit):
            calls.append(unit)
            return reading(1.0, 100.0)

        WR.advance_event(ev, at + 1800, quote)
        self.assertEqual(ev["at_move"]["status"], "filled")
        self.assertEqual(ev["followups"]["h1"]["reason"], "not due")
        self.assertEqual(calls, ["unit"])

        late = WR.new_event({"at": at, "ticker": "ASCEND", "action": "accumulate", "tx": "cd"}, "Grumpy Walrus", "unit")
        WR.advance_event(late, at + 2 * WR.H1_S, quote)
        self.assertEqual(late["at_move"]["status"], "missed")
        self.assertEqual(late["at_move"]["reason"], "window passed before a run observed it")
        self.assertEqual(late["followups"]["h1"]["status"], "filled")
        self.assertIsNone(late["followups"]["h1"]["price_change_pct"])

        missed = WR.new_event({"at": at, "ticker": "ASCEND", "action": "trim", "tx": "ef"}, "Grumpy Walrus", "unit")
        WR.advance_event(missed, at + 7 * WR.H1_S, quote)
        self.assertEqual(missed["followups"]["h1"]["status"], "missed")
        self.assertEqual(missed["followups"]["h6"]["status"], "filled")

        day = WR.new_event({"at": at, "ticker": "ASCEND", "action": "exit", "tx": "gh"}, "Grumpy Walrus", "unit")
        WR.advance_event(day, at + 25 * WR.H1_S, quote)
        self.assertEqual(day["followups"]["h6"]["status"], "missed")
        self.assertEqual(day["followups"]["h24"]["status"], "filled")

        gone = WR.new_event({"at": at, "ticker": "ASCEND", "action": "exit", "tx": "ij"}, "Grumpy Walrus", "unit")
        WR.advance_event(gone, at + 31 * WR.H1_S, quote)
        self.assertEqual(gone["followups"]["h24"]["status"], "missed")

    def test_hit_uses_price_and_liquidity_after_the_move(self):
        at = 1_700_000_000
        ev = self.event()
        prices = {0: (1.0, 100.0), 1: (1.1, 90.0)}
        step = {"n": 0}

        def quote(unit):
            price, liq = prices[step["n"]]
            step["n"] += 1
            return reading(price, liq)

        WR.advance_event(ev, at + 10, quote)
        WR.advance_event(ev, at + 2 * WR.H1_S, quote)
        cp = ev["followups"]["h1"]
        self.assertEqual(cp["price_change_pct"], 10.0)
        self.assertEqual(cp["liquidity_change_pct"], -10.0)
        self.assertIs(WR.favorable("accumulate", cp["price_change_pct"], cp["liquidity_change_pct"]), False)
        self.assertIs(WR.favorable("accumulate", 10.0, 4.0), True)
        self.assertIs(WR.favorable("trim", -3.0, None), True)
        self.assertIsNone(WR.favorable("exit", None, None))

    def test_rates_stay_blank_under_eight_events(self):
        events = []
        for i in range(7):
            ev = WR.new_event({"at": 1_700_000_000 + i, "ticker": "ASCEND", "action": "accumulate", "tx": str(i)}, "Grumpy Walrus", "u")
            ev["at_move"]["status"] = "filled"
            ev["at_move"]["price"] = 1
            ev["at_move"]["liquidity"] = 1
            ev["followups"]["h1"]["status"] = "filled"
            ev["followups"]["h1"]["price_change_pct"] = 5
            ev["followups"]["h1"]["liquidity_change_pct"] = 5
            events.append(ev)
        scores = WR.summarize(events)["Grumpy Walrus"]
        self.assertEqual(scores["events"], 7)
        self.assertTrue(scores["small_sample"])
        self.assertIsNone(scores["hit_rate_h1"])
        self.assertEqual(scores["hits_h1"], 7)
        events.append(WR.new_event({"at": 1_700_000_100, "ticker": "ASCEND", "action": "trim", "tx": "8"}, "Grumpy Walrus", "u"))
        events[-1]["at_move"]["status"] = "filled"
        events[-1]["at_move"]["price"] = 1
        events[-1]["followups"]["h1"]["status"] = "filled"
        events[-1]["followups"]["h1"]["price_change_pct"] = -4
        scores = WR.summarize(events)["Grumpy Walrus"]
        self.assertFalse(scores["small_sample"])
        self.assertEqual(scores["hit_rate_h1"], 1.0)

    def test_ledger_rows_still_classify(self):
        doc = {"whale_tag": "w-f3186187", "rows": [
            {"time": "2026-10-29T15:00:00Z", "tx_short": "abc", "kind": "swap", "token": "ASCEND", "qty": 10, "ada_leg": 20},
            {"time": "2026-10-29T15:05:00Z", "tx_short": "def", "kind": "swap", "token": "FLOW", "qty": -3, "ada_leg": 8},
            {"time": "2026-10-29T15:06:00Z", "tx_short": "ghi", "kind": "lp_remove", "token": "COCK", "qty": -1, "ada_leg": 4},
            {"time": "2026-10-29T15:07:00Z", "tx_short": "jkl", "kind": "order_open", "token": "ADA", "qty": 1, "ada_leg": 1},
        ]}
        moves = WR.moves_from_ledger(doc)
        self.assertEqual([m["action"] for m in moves], ["accumulate", "trim", "exit"])


class RosterTests(unittest.TestCase):
    def test_union_keeps_remote_status_and_social_accounts(self):
        local = WR.empty_roster()
        WR.ensure_lead(local, None, "w-f3186187")
        WR.add_candidates(local, ["w-aaaaaaaa"], ["cluster overlap"])
        remote = {"schema": 1, "private": True, "kind": "whale_roster", "whales": [
            {"codename": "Grumpy Walrus", "wallets": ["stake1u" + "y" * 53], "tags": ["w-f3186187"],
             "role": "lead", "added": "2026-10-26", "source": "desk config", "evidence": ["whale ledger"],
             "status": "dropped", "social": {"accounts": [{"network": "x", "handle": "example"}]}},
        ]}
        merged = WR.union_roster(local, remote)
        lead = [w for w in merged["whales"] if w["codename"] == "Grumpy Walrus"][0]
        self.assertEqual(lead["status"], "dropped")
        self.assertEqual(lead["social"]["accounts"], [{"network": "x", "handle": "example"}])
        self.assertTrue(any(w["status"] == "candidate" for w in merged["whales"]))

    def test_seed_does_not_reactivate_a_dropped_lead(self):
        roster = WR.empty_roster()
        WR.ensure_lead(roster, None, "w-f3186187")
        roster["whales"][0]["status"] = "dropped"
        WR.ensure_lead(roster, None, "w-f3186187")
        self.assertEqual(roster["whales"][0]["status"], "dropped")

    def test_public_summary_has_no_wallets(self):
        roster = WR.empty_roster()
        WR.ensure_lead(roster, "stake1u" + "z" * 53, "w-f3186187")
        public = WR.public_summary(roster, [], "2026-10-29T00:00:00Z")
        self.assertTrue(WR.public_text_ok(public))
        blob = json.dumps(public)
        self.assertNotIn("stake1", blob)
        self.assertNotIn("wallets", blob)
        self.assertEqual(public["buy_signal"], False)
        self.assertEqual(public["whales"][0]["codename"], "Grumpy Walrus")
        self.assertIsNone(public["whales"][0]["scores"]["social_moves_matched"])

    def test_build_writes_scores_and_keeps_the_stake_private(self):
        stake = "stake1u" + "m" * 53
        tmp = tempfile.mkdtemp()
        try:
            cache = os.path.join(tmp, "cache")
            public = os.path.join(tmp, "public")
            os.makedirs(cache)
            os.makedirs(public)
            repo = os.path.join(tmp, "repo")
            os.makedirs(os.path.join(repo, "solada-prefilter"))
            with open(os.path.join(repo, "solada-prefilter", "config.json"), "w") as fh:
                json.dump({"whale": {"stake": stake}, "units": {}}, fh)
            data = os.path.join(tmp, "data")
            os.makedirs(os.path.join(data, "solada"))
            os.makedirs(os.path.join(data, "solada-prefilter"))
            with open(os.path.join(data, "solada", "log.json"), "w") as fh:
                json.dump({"cardano_v2": {"clusters": [
                    {"ticker": "ASCEND", "wallets": ["w-aaaaaaaa", "w-bbbbbbbb"]},
                    {"ticker": "STRIKE", "wallets": ["w-aaaaaaaa", "w-bbbbbbbb"]},
                ]}}, fh)
            with open(os.path.join(data, "solada-prefilter", "whale_ledger.json"), "w") as fh:
                json.dump({"whale_tag": "w-f3186187", "rows": [
                    {"time": "2026-10-29T15:00:00Z", "tx_short": "abc", "kind": "swap", "token": "ASCEND", "qty": 2, "ada_leg": 10}
                ]}, fh)
            with open(os.path.join(data, "solada-prefilter", "liquidity_moves.json"), "w") as fh:
                json.dump({"moves": []}, fh)
            proc = subprocess.run(
                [sys.executable, "-m", "whale_roster.build", "--repo", repo, "--data-dir", data,
                 "--cache-dir", cache, "--public-dir", public],
                cwd=ROOT, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertNotIn(stake, proc.stdout)
            with open(os.path.join(public, "whale_roster.json")) as fh:
                pub = json.load(fh)
            self.assertTrue(WR.public_text_ok(pub))
            self.assertNotIn(stake, json.dumps(pub))
            with open(os.path.join(cache, "whale-roster.json")) as fh:
                private = json.load(fh)
            self.assertIn(stake, json.dumps(private))
            self.assertTrue(any(w["status"] == "candidate" for w in private["whales"]))
            self.assertTrue(all(w["status"] != "active" or w["codename"] == "Grumpy Walrus" for w in private["whales"]))
        finally:
            shutil.rmtree(tmp)

    def test_a_repo_that_is_not_private_is_not_cloned(self):
        old = (RB._repo_is_private, RB._clone_private, RB._token, RB._private_repo)
        cloned = []
        RB._token = lambda: "t" * 40
        RB._private_repo = lambda: "Zirqo-Dev/sightline-private"
        RB._repo_is_private = lambda repo, token: False
        RB._clone_private = lambda repo, token: cloned.append(repo)
        try:
            self.assertFalse(sync_roster(tempfile.mkdtemp()))
            self.assertEqual(cloned, [])
        finally:
            RB._repo_is_private, RB._clone_private, RB._token, RB._private_repo = old
        with open(os.path.join(ROOT, "whale_roster", "sync.py")) as fh:
            src = fh.read()
        self.assertNotIn("--force", src)
        self.assertIn("_repo_is_private", src)
        self.assertIn("_rebase_and_push", src)


class GuardTests(unittest.TestCase):
    def run_guard(self, rel, doc):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, rel)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as fh:
                json.dump(doc, fh)
            env = dict(os.environ)
            env.pop("PRIVACY_GUARD_PATTERNS", None)
            return subprocess.run(
                ["bash", os.path.join(ROOT, "scripts", "privacy-guard.sh"), "--root", tmp, rel],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
        finally:
            shutil.rmtree(tmp)

    def test_private_roster_and_leaks_fail_and_a_clean_summary_passes(self):
        private = {"schema": 1, "private": True, "kind": "whale_roster", "whales": [
            {"codename": "Grumpy Walrus", "wallets": ["stake1u" + "q" * 53], "role": "lead", "status": "active"}
        ]}
        failed = self.run_guard("whales/roster.json", private)
        self.assertNotEqual(failed.returncode, 0, failed.stdout + failed.stderr)
        leaked = WR.public_summary(WR.empty_roster(), [], "2026-10-29T00:00:00Z")
        leaked["note"] = "the old wording"
        leaked["note"] = "founder text"
        bad = self.run_guard("solada-prefilter/whale_roster.json", leaked)
        self.assertNotEqual(bad.returncode, 0, bad.stderr)
        stake_doc = WR.public_summary({"whales": [{"codename": "Grumpy Walrus", "role": "lead", "status": "active"}]}, [], "2026-10-29T00:00:00Z")
        stake_doc["whales"][0]["scores"]["events"] = 0
        blob = json.dumps(stake_doc)
        stake_doc = json.loads(blob)
        stake_doc["whales"][0]["codename"] = "Grumpy Walrus"
        # A stake key anywhere in the public file fails.
        dirty = json.loads(json.dumps(WR.public_summary(
            {"whales": [{"codename": "Grumpy Walrus", "role": "lead", "status": "active"}]}, [], "2026-10-29T00:00:00Z")))
        dirty["note"] = "stake1u" + "q" * 53
        held = self.run_guard("solada-prefilter/whale_roster.json", dirty)
        self.assertNotEqual(held.returncode, 0)
        clean = WR.public_summary(
            {"whales": [{"codename": "Grumpy Walrus", "role": "lead", "status": "active"}]}, [], "2026-10-29T00:00:00Z")
        ok = self.run_guard("solada-prefilter/whale_roster.json", clean)
        self.assertEqual(ok.returncode, 0, ok.stderr)


if __name__ == "__main__":
    unittest.main()
