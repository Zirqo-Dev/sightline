"""Claude peer review of PRs #26-#28: one test per finding (offline, no secrets, no network). Fake ids from labels."""
import json, os, sys, tempfile, time, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import big_moves as BM  # noqa: E402
import whale_ledger as WL  # noqa: E402
import koios_tx as KT  # noqa: E402
import prefilter as P  # noqa: E402
from test_regressions import run_main, quiet_req, fresh_state  # noqa: E402
from test_kill_status import tracked_with_units, patches, kills, FX as KFX  # noqa: E402
from test_whale_ledger import STAKE, UNITS, addr, full, fresh, kh  # noqa: E402
from test_whale_ledger_fallback import bf_shape  # noqa: E402

LP = V.lp_policies()
WC = dict(WL.wl_cfg(), _owner_pats=[__import__('re').compile('^zzOWNERzz$')])
TOK = "bb" * 28 + b"TOK".hex()
TOK2 = "ab" * 28 + b"TWO".hex()
NOW = "2026-10-26T20:07:00Z"


def u(ref, lab, lov, assets=None, datum="", stake=None):
    a = addr(lab) if not lab.startswith("addr") else lab
    return {"ref": ref, "addr": a, "stake": stake if stake is not None else (None if F.addr_info(a)[1] else F.addr_info(a)[0]),
            "lovelace": lov, "assets": assets or {}, "datum": datum}


def own_script_addr():  # order contract payment + the WHALE's stake part (addr1x-style script with key stake)
    return F.b32_encode("addr", b"\x11" + kh("order") + kh("whale"))


def ser_run(side):
    return patches({"SER": tracked_with_units()["SER"]}, {"SER": {"ada_side": side, "holders": 300}})


# ---------------------------------------------------------------- M1 + kill_status webhook gap
class M1ResearchCannotStopWebhook(unittest.TestCase):
    def test_systemexit_in_research_still_sends_webhook_and_writes_state(self):
        sent = []
        boom = mock.patch.object(BM, "run", side_effect=SystemExit("bad pattern"))
        boom2 = mock.patch.object(WL, "run", side_effect=SystemExit("prefix mismatch"))
        hook = mock.patch.object(P.Http, "post_webhook", lambda self, url, body, h=None: sent.append(body) or True)
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run = run_main(d, quiet_req, ser_run(4000) + [boom, boom2, hook], dry=False, env={"SOLADA_WEBHOOK_URL": "https://example.invalid/h"})
            st = json.load(open(os.path.join(d, "state.json")))
            ks = json.load(open(os.path.join(d, "kill_status.json")))
        self.assertEqual(kills(run), ["SER"])
        self.assertEqual(len(sent), 1)
        self.assertIn("SER", st["kill_reported"])
        self.assertTrue(ks["SER"]["hit"])

    def test_webhook_fail_then_next_run_retries_once(self):
        res = [False, True, True]
        sent = []

        def post(self, url, body, h=None):
            sent.append(sorted(body["names"]))
            return res[len(sent) - 1]
        hook = mock.patch.object(P.Http, "post_webhook", post)
        env = {"SOLADA_WEBHOOK_URL": "https://example.invalid/h"}
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            for _ in range(3):
                run_main(d, quiet_req, ser_run(4000) + [hook], dry=False, env=env)
            st = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(sent, [["SER"], ["SER"]])  # failed send retried from the queue; never a third time
        self.assertEqual(st["pending_triggers"], [])


class HttpBreakerAndDeadline(unittest.TestCase):
    def test_koios_trips_after_failures_and_skips_network(self):
        http = P.Http({})
        http.deadline = time.time() + 100  # breaker is armed only in the research window
        for _ in range(3):
            http._fail("koios", "x")
        with mock.patch("urllib.request.urlopen", side_effect=AssertionError("network")):
            self.assertIsNone(http.req("koios", "https://api.koios.rest/api/v1/tip"))
        self.assertIn("koios", http.tripped)
        self.assertEqual(http.calls.get("koios_skipped"), 1)

    def test_koios_429_trips_at_once(self):
        import urllib.error
        http = P.Http({"koios": 0})
        http.deadline = time.time() + 100
        err = urllib.error.HTTPError("u", 429, "busy", {}, None)
        with mock.patch("urllib.request.urlopen", side_effect=err) as m:
            self.assertIsNone(http.req("koios", "https://api.koios.rest/api/v1/tip"))
            self.assertIsNone(http.req("koios", "https://api.koios.rest/api/v1/tip"))
        self.assertEqual(m.call_count, 1)

    def test_deadline_blocks_research_sources_only(self):
        http = P.Http({})
        http.deadline = time.time() - 1
        self.assertTrue(http.blocked("koios") and http.blocked("blockfrost"))
        self.assertFalse(http.blocked("geckoterminal"))


# ---------------------------------------------------------------- M2 / S2 / S3 / S4 / S9 / S10 big moves
POOLZ = F.b32_encode("addr", b"\x11" + b"\x55" * 28 + kh("poolstake"))  # addr1z/x-style: script payment + stake


class BigMoves(unittest.TestCase):
    def test_m2_addr1z_pool_is_a_counterparty(self):
        w = addr("buyer")
        ut = {"inputs": [(w, 5_000_000_000, {}, None), (POOLZ, 90_000_000_000, {TOK: 10_000}, F.addr_info(POOLZ)[0])],
              "outputs": [(w, 2_000_000, {TOK: 1000}, None), (POOLZ, 94_998_000_000, {TOK: 9000}, F.addr_info(POOLZ)[0])]}
        legs = BM.legs_from_tx(TOK, "aa" * 32, 1, ut, LP)
        self.assertEqual([(l["owner"], l["direction"]) for l in legs], [(F.addr_info(w)[0], "in")])

    def test_m2_script_row_counts_when_same_stake_has_a_key_utxo(self):
        w = addr("whale")
        own = own_script_addr()
        ut = {"inputs": [(own, 3_000_000, {TOK: 50}, STAKE)], "outputs": [(w, 2_000_000, {TOK: 50}, STAKE)]}
        self.assertEqual(BM.legs_from_tx(TOK, "ab" * 32, 1, ut, LP), [])  # own order contract back to wallet: net 0
        ut2 = {"inputs": [(own, 3_000_000, {TOK: 50}, STAKE), (w, 1_000_000, {}, STAKE)], "outputs": [(addr("x"), 3_000_000, {TOK: 50}, None)]}
        self.assertEqual([l["direction"] for l in BM.legs_from_tx(TOK, "ac" * 32, 1, ut2, LP) if l["owner"] == STAKE], ["out"])

    def test_s2_multi_token_and_same_direction_use_price(self):
        w = addr("zap")
        m = {"price_ada": 2.0, "decimals": 0}
        # two tokens out against ADA in: the ADA is not all TOK's
        ut = {"inputs": [(w, 1_000_000, {TOK: 100, TOK2: 100}, None)], "outputs": [(w, 900_000_000, {}, None), (POOLZ, 1, {TOK: 100, TOK2: 100}, None)]}
        l = BM.legs_from_tx(TOK, "ad" * 32, 1, ut, LP, m=m)[0]
        self.assertEqual((l["lovelace"], l["ada_basis"]), (200_000_000, "price"))
        # token AND ADA out (LP add style): same direction -> price, not the full ADA
        ut = {"inputs": [(w, 900_000_000, {TOK: 100}, None)], "outputs": [(w, 2_000_000, {}, None)]}
        self.assertEqual(BM.legs_from_tx(TOK, "ae" * 32, 1, ut, LP, m=m)[0]["ada_basis"], "price")
        # plain swap keeps the tx ADA
        ut = {"inputs": [(w, 900_000_000, {}, None)], "outputs": [(w, 2_000_000, {TOK: 100}, None)]}
        l = BM.legs_from_tx(TOK, "af" * 32, 1, ut, LP, m=m)[0]
        self.assertEqual((l["lovelace"], l["ada_basis"]), (898_000_000, "tx"))

    def test_s4_unusable_patterns_fail_closed(self):
        self.assertEqual(BM.owner_patterns("[[:alnum:]]{20}")[1], 1)
        self.assertEqual(BM.owner_patterns("[bad")[1], 1)
        http = P.Http({})
        with mock.patch.object(P.Http, "req", side_effect=AssertionError("no calls when skipped")), tempfile.TemporaryDirectory() as d:
            for env in ("", "# only a comment", "ok\n[[:alpha:]]+"):
                s, doc = BM.run(http, {}, {TOK: {"ticker": "T"}}, d, d, False, 1, LP, env)
                self.assertIn("skipped", s)
                self.assertIsNone(doc)
            self.assertFalse(os.path.exists(os.path.join(d, "big_moves.json")))

    def fake(self, txs, calls):
        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append((url, body))
            if url.endswith("/tip"):
                return [{"block_height": 10_000, "block_time": 100_000}]
            if "/asset_txs" in url:
                r = [{"tx_hash": h, "block_height": hg, "block_time": 100_000 - (10_000 - hg)} for h, hg in txs]
                return r[::-1] if "desc" in url else r
            if url.endswith("/tx_info"):
                w = addr("mover")
                return [{"tx_hash": h, "inputs": [{"tx_hash": "e" * 64, "tx_index": 0, "payment_addr": {"bech32": w}, "stake_addr": None,
                                                    "value": "9000000000", "asset_list": []}],
                         "outputs": [{"tx_hash": h, "tx_index": 0, "payment_addr": {"bech32": w}, "stake_addr": None, "value": "1000000",
                                      "asset_list": [{"policy_id": TOK[:56], "asset_name": TOK[56:], "quantity": "5"}]}]} for h in body["_tx_hashes"]]
            return None
        return req

    def test_s3_s9_s10_capped_unit_reads_newest_rotates_and_cache_holds_tags(self):
        txs = [(f"{i:064x}", 9_000 + i) for i in range(6)]
        calls = []
        meta = {TOK: {"ticker": "T", "ada_side": 1000, "decimals": 0}, TOK2: {"ticker": "U", "ada_side": 10}}
        cfg = {"big_moves": {"max_txs_per_unit": 6, "utxo_batch": 13}}
        with mock.patch.object(P.Http, "req", self.fake(txs, calls)), tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "big-moves-cache.json"), "w").write("{trunc")  # S10: corrupt cache resets
            http = P.Http({})
            s1, _ = BM.run(http, cfg, meta, d, d, False, 100_000, LP, [])
            c1 = json.load(open(os.path.join(d, "big-moves-cache.json")))
            BM.run(http, cfg, meta, d, d, False, 100_000, LP, [])
            c2 = json.load(open(os.path.join(d, "big-moves-cache.json")))
            leftovers = [f for f in os.listdir(d) if ".tmp-" in f]
        self.assertIn("T", s1["capped"])
        self.assertIn(TOK, c1["capped"])
        self.assertTrue(any("order=block_height.desc" in u for u, _ in calls))  # capped unit read newest-first
        self.assertEqual((c1["rot"], c2["rot"]), (1, 2))
        self.assertTrue(all(l["owner"].startswith("m-") and len(l["owner"]) == 10 for l in c2["legs"]))
        self.assertFalse(any(addr("mover") in json.dumps(c) for c in (c1, c2)))
        self.assertTrue(all(u.endswith("/tx_info") for u, b in calls if b))  # S9: no deprecated /tx_utxos
        self.assertTrue(all(len(json.dumps(b)) <= 1000 for _, b in calls if b))
        self.assertEqual(leftovers, [])

    def test_moves_new_counts_only_new_rows(self):
        txs = [(f"{i:064x}", 9_990 + i) for i in range(2)]
        meta = {TOK: {"ticker": "T", "ada_side": 10, "decimals": 0}}
        with mock.patch.object(P.Http, "req", self.fake(txs, [])), tempfile.TemporaryDirectory() as d:
            http = P.Http({})
            s1, _ = BM.run(http, {"big_moves": {"min_ada": 1}}, meta, d, d, False, 100_000, LP, [])
            os.remove(os.path.join(d, "big-moves-cache.json"))
            s2, _ = BM.run(http, {"big_moves": {"min_ada": 1}}, meta, d, d, False, 100_000, LP, [])
        self.assertEqual((s1["moves_new"], s2["moves_new"]), (2, 0))


class BatchClamp(unittest.TestCase):
    def test_batches_fit_one_kb(self):
        hs = ["a" * 64] * 40
        for extra in (None, KT.TX_INFO_FLAGS):
            bs = KT.batches(hs, "_tx_hashes", 25, extra)
            self.assertTrue(all(len(json.dumps(b)) <= 1000 for b in bs))
            self.assertEqual(sum(len(b["_tx_hashes"]) for b in bs), 40)
        self.assertEqual(len(KT.batches(hs[:14], "_tx_hashes", 25)[0]["_tx_hashes"]), 14)

    def test_flagchain_never_sends_413_bodies(self):
        bodies = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            bodies.append(body)
            return []
        with mock.patch.object(P.Http, "req", req):
            F.FlagChain(P.Http({}), "").tx_utxos(["b" * 64] * 25)
        self.assertGreater(len(bodies), 1)
        self.assertTrue(all(len(json.dumps(b)) <= 1000 for b in bodies))


# ---------------------------------------------------------------- Snek (b): curve reserve exclusion
class ExcludeTags(unittest.TestCase):
    def test_reserve_left_out_of_concentration_and_clusters(self):
        res, h1, h2 = addr("reserve"), addr("h1"), addr("h2")
        cfg = {"exclude_tags": {F.tag(F.addr_info(res)[0]): "curve"}}
        dist = [(res, 900), (h1, 60), (h2, 40)]
        c = F.holder_concentration(dist, cfg)
        self.assertEqual((c["top_cluster_pct"], c["top_cluster"]), (60.0, F.tag(F.addr_info(h1)[0])))
        self.assertEqual(F.holder_concentration(dist, {})["top_cluster_pct"], 90.0)
        cfg2 = {"exclude_tags": {F.tag(res): "curve"}}  # address tag works too
        self.assertEqual(F.holder_concentration(dist, cfg2)["top_cluster_pct"], 60.0)

    def test_config_lists_the_solada_reserve(self):
        cfg = json.load(open(os.path.join(HERE, "..", "config.json")))
        self.assertIn("w-f46e5a31", F.excluded_tags(cfg["flags"]))


# ---------------------------------------------------------------- S1 + kill_status nice-to-haves
class KillStatusEntry(unittest.TestCase):
    L = {"ada_side": 5000.0, "holders": 200.0}

    def e(self, prev, vals, complete=True, **kw):
        cm = {k: (v, self.L[k]) for k, v in vals.items() if v is not None and v < self.L[k]}
        return V.kill_status_entry(prev, "", vals, self.L, cm, complete, NOW, **kw)

    def test_s1_second_metric_crossing_is_a_change(self):
        r1 = self.e(None, {"ada_side": 4000, "holders": 300})
        r2 = self.e(r1, {"ada_side": 3990, "holders": 300})
        r3 = self.e(r2, {"ada_side": 3990, "holders": 150})
        self.assertEqual((r2["changed_since_last"], r3["changed_since_last"]), (False, True))
        self.assertEqual(r3["crossed"], ["ada_side", "holders"])

    def test_s1_legacy_row_without_crossed(self):
        old = {"line": 5000.0, "metric": "ada_side", "value": 4000, "hit": True, "first_hit_at": "x", "changed_since_last": True}
        self.assertFalse(self.e(old, {"ada_side": 3990, "holders": 300})["changed_since_last"])
        self.assertTrue(self.e(old, {"ada_side": 3990, "holders": 100})["changed_since_last"])

    def test_prev_without_hit_key_and_data_gap_flag(self):
        r = self.e({"metric": "ada_side"}, {"ada_side": None, "holders": None}, complete=False)
        self.assertEqual((r["hit"], r["data_gap"]), (False, True))
        self.assertNotIn("data_gap", self.e(None, {"ada_side": 6000, "holders": 300}))

    def test_first_hit_at_seeded_from_kill_reported(self):
        r = self.e(None, {"ada_side": 4000, "holders": 300}, prev_hit=True, prev_hit_at="2026-10-24T12:00:00Z")
        self.assertEqual(r["first_hit_at"], "2026-10-24T12:00:00Z")

    def test_ticker_without_unit_keeps_last_row(self):
        tracked = {"NOU": {"kill": {"ada_side_below": 5000}, "text": ""}}
        prev = {"NOU": {"line": 5000.0, "metric": "ada_side", "value": 1, "hit": True, "first_hit_at": "t0", "changed_since_last": True}}
        ps = [mock.patch.object(P, "tracked_names", lambda paper, log: json.loads(json.dumps(tracked)))]
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            json.dump(prev, open(os.path.join(d, "kill_status.json"), "w"))
            run_main(d, quiet_req, ps, dry=False)
            ks = json.load(open(os.path.join(d, "kill_status.json")))
        self.assertEqual((ks["NOU"]["first_hit_at"], ks["NOU"]["stale"], ks["NOU"]["changed_since_last"]), ("t0", True, False))


# ---------------------------------------------------------------- whale ledger M3 / S5 / S6 / S7 / S8 / nice
TIP_TX = (full("7777"), 99, 1793040000)
NEW_TX = (full("8888"), 100, 1793040100)


def ada_in_tx(h, ada):
    return {"inputs": [u(f"{full('9999')}#0", "friend", ada + 200_000)],
            "outputs": [u(f"{h}#0", "whale", ada)]}


class FakeKL(WL.KoiosLedger):
    txs, tips, bal, spent_map = [], [99], (0, {}), {}

    def txs_after(self, stake, height):
        return None if self.txs is None else [t for t in self.txs if t[1] > height]

    def tip_height(self, stake):
        return self.tips.pop(0) if len(self.tips) > 1 else self.tips[0]

    def utxos(self, hs):
        return {h: ada_in_tx(h, 50_000_000) for h in hs}

    def balances(self, stake):
        return self.bal

    def spent(self, refs):
        return {r: self.spent_map.get(r, False) for r in refs}


CFG = {"whale": {"stake": STAKE}, "whale_ledger": {}}


class WhaleLedger(unittest.TestCase):
    def go(self, d, **kw):
        for k, v in kw.items():
            setattr(FakeKL, k, v)
        with mock.patch.object(WL, "KoiosLedger", FakeKL):
            return WL.run(P.Http({}), CFG, d, False, 1793050000, LP, cache_dir=os.path.join(d, "c"))

    def tearDown(self):
        FakeKL.txs, FakeKL.tips, FakeKL.bal, FakeKL.spent_map = [], [99], (0, {}), {}

    def test_m3_baseline_seeds_seen_so_the_tip_tx_is_not_reapplied(self):
        with tempfile.TemporaryDirectory() as d:
            self.go(d, txs=[TIP_TX], tips=[99], bal=(50_000_000, {}))
            st = json.load(open(os.path.join(d, "c", "whale-ledger-state.json")))
            r = self.go(d, txs=[TIP_TX, NEW_TX], bal=(100_000_000, {}))
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertIn(TIP_TX[0], st["seen"])
        self.assertEqual(st["baseline_height"], 99)
        self.assertEqual([x["tx_short"] for x in doc["rows"]], [NEW_TX[0][:10]])
        self.assertTrue(r["reconcile_ok"])
        self.assertNotIn("_state", doc)  # nice-to-have: private state split into its own file

    def test_m3_tip_moved_during_baseline_retries(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.go(d, tips=[99, 100])
            self.assertIn("retry", r["error"])
            self.assertFalse(os.path.exists(os.path.join(d, "whale_ledger.json")))

    def test_m3_legacy_baseline_from_the_8pm_run(self):
        legacy = {"height": 99, "baseline_at": "2026-10-27T00:07:00Z", "lovelace": 50_000_000, "positions_raw": {},
                  "seen": [], "rows": [], "orders": {}, "token_names": {}}
        with tempfile.TemporaryDirectory() as d:
            json.dump({"_state": legacy, "rows": []}, open(os.path.join(d, "whale_ledger.json"), "w"))
            r = self.go(d, txs=[TIP_TX, NEW_TX], bal=(100_000_000, {}))
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertEqual([x["tx_short"] for x in doc["rows"]], [NEW_TX[0][:10]])
        self.assertTrue(r["reconcile_ok"])

    def test_s7_unread_tx_list_never_closes_orders_or_reconciles(self):
        led = dict(fresh(), height=99, baseline_height=99,
                   orders={"o#0": {"ref_short": "o#0", "unit": "lovelace", "token": "ADA", "qty_raw": 1, "ada": 1, "status": "open"}})
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "c")); json.dump(led, open(os.path.join(d, "c", "whale-ledger-state.json"), "w"))
            r = self.go(d, txs=None, spent_map={"o#0": True}, bal=(10**12, {}))
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertTrue(r["stale"] and doc["capped"])
        self.assertEqual(doc["orders"]["o#0"]["status"], "open")
        self.assertNotIn("reconcile", doc)

    def test_seen_pruned_to_last_two_blocks(self):
        led = dict(fresh(), height=500, seen={"a": 10, "b": 499, "c": 500})
        WL.prune_seen(led)
        self.assertEqual(sorted(led["seen"]), ["b", "c"])

    def test_basis_reports_blockfrost(self):
        doc = WL.public_doc(fresh(), "w-x", 0, WC, {"basis": "blockfrost"})
        self.assertEqual(doc["basis"], "blockfrost")


class Fallback(unittest.TestCase):
    def test_s5_collateral_and_reference_are_not_spends(self):
        t = {"hash": full("c011"), "inputs": [u(f"{full('aaaa')}#0", "whale", 5_000_000), u(f"{full('aaab')}#0", "whale", 9_000_000)],
             "outputs": [u(f"{full('c011')}#0", "friend", 4_800_000), u(f"{full('c011')}#1", "whale", 8_000_000)]}
        x = bf_shape(t)
        x["inputs"][1]["collateral"] = True
        x["inputs"].append(dict(x["inputs"][0], tx_hash=full("aaac"), reference=True))
        x["outputs"][1]["collateral"] = True
        out = WL.KoiosLedger(P.Http({}), WC)._bf_utxo(x)
        self.assertEqual([i["ref"] for i in out["inputs"]], [f"{full('aaaa')}#0"])
        self.assertEqual(len(out["outputs"]), 1)

    def test_s6_fallback_tx_list_is_height_filtered(self):
        kl = WL.KoiosLedger(P.Http({}), WC, "k")
        with mock.patch.object(kl, "get", return_value=None), mock.patch.object(
                kl, "bf", return_value=[{"tx_hash": "old", "block_height": 50, "block_time": 0},
                                        {"tx_hash": "new", "block_height": 101, "block_time": 0}]):
            self.assertEqual([t[0] for t in kl.txs_after(STAKE, 99)], ["new"])


class OrderLifecycle(unittest.TestCase):
    def apply(self, led, t):
        return WL.apply(led, t, STAKE, LP, WC)

    def test_ada_buy_order_cancel_is_not_a_fill(self):
        led = fresh()
        self.apply(led, {"hash": full("b001"), "time": 1, "inputs": [u("i#0", "whale", 102_000_000)],
                         "outputs": [u(f"{full('b001')}#0", "order", 100_000_000), u(f"{full('b001')}#1", "whale", 1_800_000)]})
        rows = self.apply(led, {"hash": full("b002"), "time": 2, "inputs": [u(f"{full('b001')}#0", "order", 100_000_000)],
                                "outputs": [u(f"{full('b002')}#0", "whale", 99_800_000)]})
        self.assertEqual([(r["kind"], r["order_status"]) for r in rows], [("order_cancel", "cancelled")])

    def test_token_to_token_fill_has_no_stray_transfer(self):
        led = fresh()
        self.apply(led, {"hash": full("b011"), "time": 1, "inputs": [u("i#0", "whale", 5_000_000, {UNITS["COCK"]: 500})],
                         "outputs": [u(f"{full('b011')}#0", "order", 4_000_000, {UNITS["COCK"]: 500}), u(f"{full('b011')}#1", "whale", 800_000)]})
        rows = self.apply(led, {"hash": full("b012"), "time": 2, "inputs": [u(f"{full('b011')}#0", "order", 4_000_000, {UNITS["COCK"]: 500})],
                                "outputs": [u(f"{full('b012')}#0", "whale", 2_000_000, {UNITS["ASCEND"]: 77})]})
        self.assertEqual([(r["kind"], r["token"], r["sold"]) for r in rows], [("order_fill", "ASCEND", "COCK")])

    def test_order_at_own_stake_script_address_is_tracked(self):
        led = fresh()
        own = own_script_addr()
        rows = self.apply(led, {"hash": full("b021"), "time": 1, "inputs": [u("i#0", "whale", 5_000_000, {UNITS["COCK"]: 700}, stake=STAKE)],
                                "outputs": [u(f"{full('b021')}#0", own, 3_000_000, {UNITS["COCK"]: 700}, stake=STAKE),
                                            u(f"{full('b021')}#1", "whale", 1_800_000, stake=STAKE)]})
        self.assertEqual([(r["kind"], r["token"], r["qty"]) for r in rows], [("order_open", "COCK", 700)])
        rows = self.apply(led, {"hash": full("b022"), "time": 2, "inputs": [u(f"{full('b021')}#0", own, 3_000_000, {UNITS["COCK"]: 700}, stake=STAKE)],
                                "outputs": [u(f"{full('b022')}#0", "whale", 2_800_000, {UNITS["COCK"]: 700}, stake=STAKE)]})
        self.assertEqual([(r["kind"], r["order_status"]) for r in rows], [("order_cancel", "cancelled")])
        rows = self.apply(fresh(), {"hash": full("b031"), "time": 1, "inputs": [u("i#0", "whale", 5_000_000, {UNITS["COCK"]: 700}, stake=STAKE)],
                                    "outputs": [u(f"{full('b031')}#0", own, 3_000_000, {UNITS["COCK"]: 700}, stake=STAKE)]})
        self.assertEqual(rows[0]["kind"], "order_open")

    def test_order_edit_same_token_new_ref_is_a_replace(self):
        led = fresh()
        self.apply(led, {"hash": full("b041"), "time": 1, "inputs": [u("i#0", "whale", 5_000_000, {UNITS["COCK"]: 1000})],
                         "outputs": [u(f"{full('b041')}#0", "order", 4_000_000, {UNITS["COCK"]: 1000})]})
        cred = F.b32_decode(STAKE)[1][1:].hex()
        rows = self.apply(led, {"hash": full("b042"), "time": 2,
                                "inputs": [u(f"{full('b041')}#0", "order", 4_000_000, {UNITS["COCK"]: 1000}), u("i#1", "whale", 2_000_000)],
                                "outputs": [u(f"{full('b042')}#0", "order", 4_000_000, {UNITS["COCK"]: 1000}, datum=json.dumps({"bytes": cred})),
                                            u(f"{full('b042')}#1", "whale", 1_700_000)]})
        self.assertEqual([(r["kind"], r.get("order_status")) for r in rows], [("order_cancel", "replaced"), ("order_open", "open")])
        self.assertEqual(sum(1 for o in led["orders"].values() if o["status"] == "open"), 1)


if __name__ == "__main__":
    unittest.main()
