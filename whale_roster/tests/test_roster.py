"""Offline whale roster tests. No network and no secrets."""
import hashlib
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
        # Two shared tokens and no sized moves are not enough for a candidate.
        found = WR.discover(log, {"moves": []}, ["w-cccccccc"], {"ASCEND", "STRIKE", "FLOW"})
        self.assertEqual(found, [])
        moves = []
        for i in range(10):
            moves.append({"tag": "w-aaaaaaaa", "ticker": "ASCEND" if i % 2 == 0 else "STRIKE",
                          "direction": "in", "ada": 300, "tx_short": "a%02d" % i, "time": "2026-10-29T15:%02d:00Z" % i,
                          "ada_basis": "tx", "qty_basis": "whole", "qty": 1})
        found = WR.discover(log, {"moves": []}, ["w-cccccccc"], {"ASCEND", "STRIKE", "FLOW"}, {"moves": moves}, 1_793_000_000)
        self.assertEqual(found, ["w-aaaaaaaa"])
        roster = WR.empty_roster()
        WR.add_candidates(roster, found, ["cluster overlap"])
        self.assertTrue(all(w["status"] == "candidate" for w in roster["whales"]))
        self.assertTrue(all(not w["wallets"] for w in roster["whales"]))
        self.assertEqual(len({w["codename"] for w in roster["whales"]}), 1)

    def test_large_positions_across_tokens_qualify_without_a_partner(self):
        liquidity = {"moves": [
            {"actor_tag": "w-dddddddd", "ticker": "ASCEND", "ada": 2500},
            {"actor_tag": "w-dddddddd", "ticker": "STRIKE", "ada": 800},
        ]}
        found = WR.discover({"cardano_v2": {}}, liquidity, [], {"ASCEND", "STRIKE"})
        self.assertEqual(found, [])
        rows = []
        for i in range(10):
            rows.append({"actor_tag": "w-dddddddd", "ticker": "ASCEND" if i % 2 == 0 else "STRIKE",
                         "direction": "buy", "ada": 250, "tx_short": "d%02d" % i, "time": "2026-10-29T16:%02d:00Z" % i})
        found = WR.discover({"cardano_v2": {}}, {"moves": rows}, [], {"ASCEND", "STRIKE"}, {}, 1_793_000_000)
        self.assertEqual(found, ["w-dddddddd"])

    def test_lead_tag_is_not_proposed_again(self):
        log = {"cardano_v2": {"clusters": [
            {"ticker": "ASCEND", "wallets": ["w-f3186187", "w-aaaaaaaa"]},
            {"ticker": "STRIKE", "wallets": ["w-f3186187", "w-aaaaaaaa"]},
        ]}}
        moves = []
        for tag in ("w-f3186187", "w-aaaaaaaa"):
            for i in range(10):
                moves.append({"tag": tag, "ticker": "ASCEND" if i % 2 == 0 else "STRIKE", "direction": "in",
                              "ada": 300, "tx_short": tag[-2:] + "%02d" % i, "time": "2026-10-29T15:%02d:00Z" % i})
        found = WR.discover(log, {}, ["w-f3186187"], {"ASCEND", "STRIKE"}, {"moves": moves}, 1_793_000_000)
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
        self.assertEqual(late["at_move"]["status"], "unobserved")
        self.assertEqual(late["at_move"]["reason"], "no observation in the window")
        self.assertEqual(late["followups"]["h1"]["status"], "filled")
        self.assertIsNone(late["followups"]["h1"]["price_change_pct"])

        missed = WR.new_event({"at": at, "ticker": "ASCEND", "action": "trim", "tx": "ef"}, "Grumpy Walrus", "unit")
        WR.advance_event(missed, at + 7 * WR.H1_S, quote)
        self.assertEqual(missed["followups"]["h1"]["status"], "unobserved")
        self.assertEqual(missed["followups"]["h1"]["outcome"], "unobserved")
        self.assertEqual(missed["followups"]["h6"]["status"], "filled")

        day = WR.new_event({"at": at, "ticker": "ASCEND", "action": "exit", "tx": "gh"}, "Grumpy Walrus", "unit")
        WR.advance_event(day, at + 25 * WR.H1_S, quote)
        self.assertEqual(day["followups"]["h6"]["status"], "unobserved")
        self.assertEqual(day["followups"]["h24"]["status"], "filled")

        gone = WR.new_event({"at": at, "ticker": "ASCEND", "action": "exit", "tx": "ij"}, "Grumpy Walrus", "unit")
        WR.advance_event(gone, at + 31 * WR.H1_S, quote)
        self.assertEqual(gone["followups"]["h24"]["status"], "unobserved")
        scores = WR.summarize([gone])["Grumpy Walrus"]
        self.assertEqual(scores["n_observed_h24"], 0)
        self.assertEqual(scores["hits_h24"], 0)

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
            moves = []
            for i in range(10):
                moves.append({
                    "tag": "w-aaaaaaaa", "ticker": "ASCEND" if i % 2 == 0 else "STRIKE",
                    "direction": "in", "ada": 300, "tx_short": "m%02d" % i,
                    "time": "2026-10-29T15:%02d:00Z" % i, "ada_basis": "tx", "qty_basis": "whole", "qty": 1,
                })
            with open(os.path.join(data, "solada-prefilter", "big_moves.json"), "w") as fh:
                json.dump({"moves": moves, "coverage": {"ASCEND": {}, "STRIKE": {}}}, fh)
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
            env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
            # A well-formed digest so a clean file still passes. The preimage is
            # split so this file does not contain that token.
            env["PRIVACY_IDENTITY_HASHES"] = hashlib.sha256(
                ("zzsynthetic" + "identityblocktoken").encode()
            ).hexdigest()
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


class CandidateScoreTests(unittest.TestCase):
    def big(self, rows):
        return {"moves": rows, "coverage": {"AAA": {}, "BBB": {}}}

    def row(self, tag, ticker, direction, ada, tx, when="2026-10-29T15:00:00Z"):
        return {"tag": tag, "ticker": ticker, "direction": direction, "ada": ada, "tx_short": tx, "time": when,
                "ada_basis": "tx", "qty_basis": "whole", "qty": 10}

    def _sized(self, tag, n=10, ada=300):
        rows = []
        for i in range(n):
            rows.append(self.row(
                tag, "AAA" if i % 2 == 0 else "BBB", "in", ada, "%s%02d" % (tag[-4:], i),
                "2026-10-29T15:%02d:00Z" % i,
            ))
        return rows

    def test_big_moves_tags_qualify_and_the_lead_tag_does_not(self):
        rows = self._sized("m-aaaaaaaa") + self._sized("m-f3186187", ada=400) + [
            self.row("w-bbbbbbbb", "AAA", "in", 100, "txbbb1"),
        ]
        found = WR.discover(
            {"cardano_top10": [{"ticker": "AAA"}, {"ticker": "BBB"}]},
            {"moves": []},
            ["w-f3186187"],
            {"AAA", "BBB"},
            self.big(rows),
            1_793_500_000,
        )
        self.assertEqual(found, ["m-aaaaaaaa"])
        self.assertNotIn("m-f3186187", found)
        self.assertNotIn("w-bbbbbbbb", found)

    def test_a_balanced_router_is_not_a_candidate(self):
        rows = []
        for i in range(100):
            ticker = "AAA" if i % 2 == 0 else "BBB"
            direction = "in" if i < 50 else "out"
            rows.append(self.row("m-cccccccc", ticker, direction, 1000, "tx%03d" % i))
        self.assertTrue(WR.market_maker(100, 50000, 50000))
        self.assertTrue(WR.market_maker(100, 100, 90))
        self.assertFalse(WR.market_maker(99, 50000, 50000))
        self.assertFalse(WR.market_maker(100, 100, 80))
        self.assertFalse(WR.market_maker(100, 100, 0))
        found = WR.discover({}, {"moves": []}, [], {"AAA", "BBB"}, self.big(rows))
        self.assertEqual(found, [])
        self.assertEqual(WR.router_tags(self.big(rows)), ["m-cccccccc"])
        roster = WR.empty_roster()
        blocked = WR.codename_for("m-cccccccc")
        WR.add_candidates(roster, ["m-cccccccc"], ["big-move overlap"], reserved=[blocked])
        self.assertNotEqual(roster["whales"][0]["codename"], blocked)
        self.assertEqual(roster["whales"][0]["status"], "candidate")
        uneven = [self.row("m-dddddddd", "AAA" if i % 2 == 0 else "BBB", "in", 1000, "u%03d" % i) for i in range(40)]
        uneven += [self.row("m-dddddddd", "AAA", "out", 1000, "s%03d" % i) for i in range(10)]
        found = WR.discover({}, {"moves": []}, [], {"AAA", "BBB"}, self.big(uneven))
        self.assertEqual(found, ["m-dddddddd"])

    def test_the_same_trade_in_two_runs_is_one_event(self):
        first = self.row("m-aaaaaaaa", "AAA", "in", 1500, "txaaa1")
        second = self.row("m-aaaaaaaa", "BBB", "in", 900, "txaaa2", "2026-10-29T15:05:00Z")
        again = dict(first)
        again["ada"] = 1501
        rows = WR.dedupe_rows([first, second, again])
        self.assertEqual(len(rows), 2)
        moves = WR.moves_from_tagged(rows)
        self.assertEqual([m["action"] for m in moves], ["accumulate", "accumulate"])
        log = {"cardano_top10": [{"ticker": "AAA"}, {"ticker": "BBB"}]}
        cache = {"schema": 1, "private": True, "kind": "whale_roster", "whales": [{
            "codename": "Sleepy Otter", "wallets": [], "tags": ["m-aaaaaaaa"], "role": "watch",
            "added": "", "source": "cache", "evidence": [], "status": "candidate", "social": {"accounts": []},
        }]}
        roster, events, public = WR.build(
            log, {"whale_tag": "w-f3186187", "rows": []}, {"moves": []},
            {"whale": {}, "units": {}}, {}, cache, [], 1_793_500_000, None, None,
            [self.big([first, second]), self.big([again])],
        )
        otter = [w for w in roster["whales"] if w["codename"] == "Sleepy Otter"][0]
        self.assertEqual(otter["status"], "dormant")
        self.assertEqual(sum(1 for event in events if event["codename"] == "Sleepy Otter"), 2)
        self.assertEqual(public["dormant_count"], 1)
        self.assertFalse(any(w["codename"] == "Sleepy Otter" for w in public["whales"]))
        self.assertTrue(WR.public_text_ok(public))
        self.assertNotIn("m-aaaaaaaa", json.dumps(public))

    def test_candidate_hits_use_the_lead_horizons(self):
        at = 1_700_000_000
        move = {"at": at, "ticker": "AAA", "action": "accumulate", "tx": "tx1", "tag": "m-aaaaaaaa"}
        event = WR.new_event(move, "Sleepy Otter", "unit")
        prices = {0: (1.0, 100.0), 1: (1.2, 110.0)}
        step = {"n": 0}

        def quote(unit):
            price, liq = prices[step["n"]]
            step["n"] += 1
            return reading(price, liq)

        WR.advance_event(event, at + 10, quote)
        WR.advance_event(event, at + 2 * WR.H1_S, quote)
        scores = WR.summarize([event])["Sleepy Otter"]
        self.assertEqual(scores["hits_h1"], 1)
        self.assertIs(WR.favorable("accumulate", 20.0, 10.0), True)
        # Same window boundaries the lead uses.
        self.assertEqual(event["followups"]["h1"]["status"], "filled")
        self.assertEqual(event["followups"]["h6"]["reason"], "not due")

    def _live(self, scores, n6, n24, r6, r24):
        scores["live"]["h6"]["n_observed"] = n6
        scores["live"]["h6"]["hit_rate"] = r6
        scores["live"]["h24"]["n_observed"] = n24
        scores["live"]["h24"]["hit_rate"] = r24

    def test_eligibility_is_a_flag_and_not_a_promotion(self):
        low = WR.empty_scores()
        self._live(low, 25, 25, 0.55, 0.90)
        eligible, reason = WR.promotion_eligibility(low)
        self.assertFalse(eligible)
        self.assertIn("need > 0.55", reason)
        high = WR.empty_scores()
        self._live(high, 25, 25, 0.551, 0.56)
        eligible, reason = WR.promotion_eligibility(high)
        self.assertTrue(eligible)
        self.assertIn("manual owner decision", reason)
        short = WR.empty_scores()
        self._live(short, 24, 25, 0.551, 0.56)
        eligible, _reason = WR.promotion_eligibility(short)
        self.assertFalse(eligible)
        blank = WR.empty_scores()
        blank["events"] = 30
        eligible, reason = WR.promotion_eligibility(blank)
        self.assertFalse(eligible)
        self.assertIn("blank", reason)
        roster = {"whales": [{"codename": "Sleepy Otter", "role": "watch", "status": "candidate"}]}
        event = WR.new_event({"at": 1, "ticker": "AAA", "action": "accumulate", "tx": "t"}, "Sleepy Otter", None)
        public = WR.public_summary(roster, [event] * 25, "2026-10-29T00:00:00Z")
        whale = public["whales"][0]
        self.assertIn("eligible_for_active", whale)
        self.assertEqual(whale["status"], "candidate")
        self.assertIs(whale["eligible_for_active"], False)

    def test_public_output_has_scores_and_no_wallet(self):
        stake = "stake1u" + "p" * 53
        digest = __import__("hashlib").sha256(stake.encode()).hexdigest()[:8]
        tag = "m-" + digest
        rows = self._sized(tag, ada=250)
        cache = {"schema": 1, "private": True, "kind": "whale_roster", "whales": [{
            "codename": "Sleepy Otter", "wallets": [stake], "tags": [], "role": "watch",
            "added": "", "source": "cache", "evidence": [], "status": "candidate",
            "social": {"accounts": []},
        }]}
        log = {"cardano_top10": [{"ticker": "AAA"}, {"ticker": "BBB"}]}
        _roster, events, public = WR.build(
            log, {"rows": []}, {"moves": []}, {"whale": {}, "units": {}}, {},
            cache, [], 1_793_500_000, None, None, self.big(rows),
        )
        blob = json.dumps(public)
        self.assertNotIn(stake, blob)
        self.assertNotIn(tag, blob)
        self.assertNotIn("wallets", blob)
        self.assertTrue(WR.public_text_ok(public))
        whale = [w for w in public["whales"] if w["codename"] == "Sleepy Otter"][0]
        self.assertEqual(whale["scores"]["events"], 10)
        self.assertEqual(whale["status"], "candidate")
        self.assertEqual(len([event for event in events if event["codename"] == "Sleepy Otter"]), 10)
        self.assertTrue(all(e["codename"] == "Sleepy Otter" for e in events))

    def test_a_tagged_liquidity_row_is_scored_for_that_whale(self):
        cache = {"schema": 1, "private": True, "kind": "whale_roster", "whales": [{
            "codename": "Sleepy Otter", "wallets": [], "tags": ["m-aaaaaaaa"], "role": "watch",
            "added": "", "source": "cache", "evidence": [], "status": "active",
            "social": {"accounts": []},
        }]}
        liquidity = {"moves": [{
            "actor_tag": "m-aaaaaaaa", "ticker": "AAA", "direction": "sell", "kind": "swap",
            "ada": 1200, "tx_short": "liq1", "time": "2026-10-29T15:00:00Z",
        }, {
            "actor_tag": "m-aaaaaaaa", "ticker": "AAA", "direction": "sell", "kind": "swap",
            "ada": 1200, "tx_short": "liq1", "time": "2026-10-29T15:00:00Z",
        }]}
        _roster, events, public = WR.build(
            {}, {"rows": []}, liquidity, {"units": {}}, {}, cache, [], 1_700_000_000, None, None, [],
        )
        whale = [w for w in public["whales"] if w["codename"] == "Sleepy Otter"][0]
        self.assertEqual(whale["status"], "active")
        self.assertEqual(whale["scores"]["events"], 1)
        self.assertEqual(whale["scores"]["trim"], 1)
        self.assertEqual(len(events), 1)

    def test_log_big_moves_count_once_across_history_snapshots(self):
        row = {"tag": "m-aaaaaaaa", "ticker": "AAA", "direction": "in", "ada": 1500, "tx_short": "tx1", "time": "2026-10-29T15:00:00Z"}
        other = {"tag": "m-aaaaaaaa", "ticker": "BBB", "direction": "in", "ada": 800, "tx_short": "tx2", "time": "2026-10-29T15:05:00Z"}
        log = {
            "cardano_top10": [{"ticker": "AAA"}, {"ticker": "BBB"}],
            "liquidity_flows": {"big_moves": [row, other]},
            "history": [{"liquidity_flows": {"big_moves": [row, other]}}],
        }
        found = WR.discover(log, {"moves": []}, [], {"AAA", "BBB"})
        self.assertEqual(found, [])
        moves = WR.moves_from_tagged(WR.dedupe_rows([row, other, row]))
        self.assertEqual(len(moves), 2)

    def test_unobserved_is_not_a_miss_and_desk_prints_score(self):
        at = WR.parse_time("2026-10-29T12:00:00Z")
        event = WR.new_event({"at": at, "ticker": "AAA", "action": "accumulate", "tx": "own"}, "Grumpy Walrus", None)
        later = WR.new_event({"at": at, "ticker": "BBB", "action": "trim", "tx": "own2"}, "Grumpy Walrus", None)
        prices = {
            "AAA": [
                {"t": at, "price": 1.0, "tx": "own"},
                {"t": at + WR.H1_S + 600, "price": 1.2, "tx": "later"},
            ],
            "BBB": [
                {"t": at, "price": 2.0, "tx": "own2"},
                {"t": at + WR.H1_S, "price": 2.4, "tx": "up"},
            ],
        }
        liquidity = {"AAA": [{"t": at, "liquidity": 100.0}, {"t": at + WR.H1_S + 600, "liquidity": 90.0}]}
        WR.apply_desk_observations([event, later], prices, liquidity, at + WR.H24_S + WR.H24_SLACK_S)
        self.assertEqual(event["followups"]["h1"]["outcome"], "miss")
        self.assertEqual(later["followups"]["h1"]["outcome"], "miss")
        self.assertEqual(event["followups"]["h6"]["outcome"], "unobserved")
        self.assertNotEqual(event["followups"]["h6"]["status"], "missed")
        scores = WR.summarize([event, later])["Grumpy Walrus"]
        self.assertEqual(scores["backfill"]["h1"]["n_observed"], 2)
        self.assertEqual(scores["backfill"]["h1"]["hits"], 0)
        self.assertEqual(scores["backfill"]["approximate"], True)
        self.assertEqual(scores["live"]["h1"]["n_observed"], 0)
        self.assertEqual(scores["n_observed_h6"], 0)
        self.assertIsNone(scores["hit_rate_h6"])
        # Price up and liquidity up is a hit for an accumulate.
        up = WR.new_event({"at": at, "ticker": "AAA", "action": "accumulate", "tx": "z"}, "Sleepy Otter", None)
        prices["AAA"].append({"t": at + 30, "price": 1.0, "tx": "base"})
        WR.apply_desk_observations([up], prices, {}, at + 2 * WR.H1_S)
        self.assertEqual(up["followups"]["h1"]["outcome"], "hit")

    def test_a_candidate_outside_the_cap_goes_dormant_and_stays_private(self):
        rows = []
        tags = []
        for n in range(13):
            tag = "m-%08x" % (n + 1)
            tags.append(tag)
            for i in range(10):
                rows.append(self.row(tag, "AAA" if i % 2 == 0 else "BBB", "in", 1000 + n * 100, "%x%02d" % (n, i),
                                     "2026-10-29T15:%02d:00Z" % i))
        found = WR.discover({}, {"moves": []}, [], {"AAA", "BBB"}, self.big(rows), 1_793_500_000)
        self.assertEqual(len(found), WR.MAX_CANDIDATES)
        roster = WR.empty_roster()
        WR.add_candidates(roster, found, ["big-move overlap"])
        WR.add_candidates(roster, [tags[0]], ["older"])
        # The smallest tag was not in the top 12, so the pre-existing row goes dormant.
        WR.settle_candidates(roster, found)
        smallest = [w for w in roster["whales"] if tags[0] in w["tags"]][0]
        self.assertEqual(smallest["status"], "dormant")
        public = WR.public_summary(roster, [], "2026-10-29T00:00:00Z")
        self.assertEqual(public["dormant_count"], 1)
        self.assertFalse(any(tags[0] in json.dumps(w) for w in public["whales"]))
        self.assertEqual(len(public["whales"]), WR.MAX_CANDIDATES)

    def test_live_and_backfill_stay_split_and_eligibility_uses_live(self):
        at = 1_700_000_000
        live = WR.new_event({"at": at, "ticker": "AAA", "action": "accumulate", "tx": "L"}, "Sleepy Otter", "u")
        live["at_move"]["status"] = "filled"
        live["at_move"]["price"] = 1
        live["at_move"]["liquidity"] = 1
        live["followups"]["h6"]["status"] = "filled"
        live["followups"]["h6"]["price_change_pct"] = 4
        live["followups"]["h6"]["liquidity_change_pct"] = 4
        live["followups"]["h6"]["outcome"] = "hit"
        live["followups"]["h6"]["source"] = "live"
        live["followups"]["h24"]["status"] = "filled"
        live["followups"]["h24"]["price_change_pct"] = -3
        live["followups"]["h24"]["outcome"] = "miss"
        live["followups"]["h24"]["source"] = "live"
        back = WR.new_event({"at": at + 1, "ticker": "AAA", "action": "accumulate", "tx": "B"}, "Sleepy Otter", "u")
        for name, outcome in (("h6", "hit"), ("h24", "hit")):
            back["followups"][name]["status"] = "filled"
            back["followups"][name]["price_change_pct"] = 5
            back["followups"][name]["outcome"] = outcome
            back["followups"][name]["source"] = "desk-data"
        scores = WR.summarize([live, back])["Sleepy Otter"]
        self.assertEqual(scores["live"]["h6"]["n_observed"], 1)
        self.assertEqual(scores["live"]["h6"]["hits"], 1)
        self.assertEqual(scores["live"]["h24"]["hits"], 0)
        self.assertEqual(scores["backfill"]["h6"]["n_observed"], 1)
        self.assertEqual(scores["backfill"]["h6"]["hits"], 1)
        self.assertEqual(scores["backfill"]["h24"]["hits"], 1)
        self.assertTrue(scores["backfill"]["approximate"])
        self.assertEqual(scores["n_observed_h6"], 1)
        self.assertEqual(scores["hits_h24"], 0)
        # Backfill clears the bar. Live does not. Eligible stays false.
        for name in ("h6", "h24"):
            scores["backfill"][name]["n_observed"] = 25
            scores["backfill"][name]["hits"] = 20
            scores["backfill"][name]["hit_rate"] = 0.8
        self.assertFalse(WR.promotion_eligibility(scores)[0])
        self.assertTrue(WR.promising_backfill(scores))
        for name in ("h6", "h24"):
            scores["live"][name]["n_observed"] = 25
            scores["live"][name]["hits"] = 20
            scores["live"][name]["hit_rate"] = 0.8
        self.assertTrue(WR.promotion_eligibility(scores)[0])

    def test_published_lead_live_tally_is_not_replaced_by_backfill(self):
        previous = {"whales": [{"codename": "Grumpy Walrus", "role": "lead", "status": "active", "scores": {
            "priced_h1": 49, "hits_h1": 21, "hit_rate_h1": 0.429,
            "priced_h6": 53, "hits_h6": 30, "hit_rate_h6": 0.566,
            "priced_h24": 36, "hits_h24": 18, "hit_rate_h24": 0.5,
        }}]}
        at = WR.parse_time("2026-10-29T12:00:00Z")
        event = WR.new_event({"at": at, "ticker": "AAA", "action": "accumulate", "tx": "own"}, "Grumpy Walrus", None)
        prices = {"AAA": [
            {"t": at, "price": 1.0, "tx": "own"},
            {"t": at + WR.H6_S, "price": 1.2, "tx": "later6"},
            {"t": at + WR.H24_S, "price": 1.3, "tx": "later24"},
        ]}
        WR.apply_desk_observations([event], prices, {}, at + WR.H24_S + WR.H24_SLACK_S)
        self.assertEqual(event["followups"]["h6"]["source"], "desk-data")
        roster = {"whales": [{"codename": "Grumpy Walrus", "role": "lead", "status": "active"}]}
        public = WR.public_summary(roster, [event], "2026-11-01T00:00:00Z", None, previous)
        whale = public["whales"][0]
        self.assertEqual(whale["scores"]["live"]["h6"]["n_observed"], 53)
        self.assertEqual(whale["scores"]["live"]["h6"]["hits"], 30)
        self.assertEqual(whale["scores"]["live"]["h6"]["hit_rate"], 0.566)
        self.assertEqual(whale["scores"]["live"]["h1"]["hits"], 21)
        self.assertGreater(whale["scores"]["backfill"]["h6"]["n_observed"], 0)
        self.assertFalse(whale["eligible_for_active"])
        self.assertNotEqual(whale["scores"]["live"]["h6"]["hit_rate"], whale["scores"]["backfill"]["h6"]["hit_rate"])


if __name__ == "__main__":
    unittest.main()
