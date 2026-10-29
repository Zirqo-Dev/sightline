"""Regression tests for the 2026-10-25 4:08 PM ET false whale alert (offline, no secrets, no network).

1. Minswap V2 LP tokens counted as whale buys -> LP is labelled and excluded from the whale size trigger and buy flags.
2. Raw token units valued as ADA (FLOW shown as ~68.7M ADA, really ~73 ADA via an aggregator) -> moves are valued by
   the ADA leg of the tx, falling back to decimals x pool price; same for buy flags.
3. A kill line already reported re-fired -> deduped on ticker + crossed metric names; re-fires only when >=25% deeper.
Fake addresses are generated at test time from labels."""
import hashlib, json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import prefilter as P  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "regressions.json")))
LP_UNIT = FX["minswap_v2_lp_policy"] + FX["lp_asset_name"]
FLOW_UNIT = FX["flow_policy"] * 28 + FX["flow_asset_name_ascii"].encode().hex()
TOKEN = "ef" * 28 + "4b4f4f4c"
POOL = F.b32_encode("addr", b"\x71" + b"\x33" * 28)  # script payment = DEX pool / aggregator order


def keyhash(label):
    return hashlib.sha256(("regress/" + label).encode()).digest()[:28]


def addr(label, n=0):
    return F.b32_encode("addr", b"\x01" + hashlib.sha256(f"{label}/{n}".encode()).digest()[:28] + keyhash(label))


def stake(label):
    return F.b32_encode("stake", b"\xe1" + keyhash(label))


def run_main(d, fake_req, extra_patches=(), dry=True, env=None):
    argv = ["prefilter.py", "--repo", os.path.join(HERE, "..", ".."), "--data-dir", d, "--cache-dir", os.path.join(d, "c")]
    e = {"BLOCKFROST_PROJECT_ID": "", "SOLADA_WEBHOOK_URL": "", "SOLANA_RPC_URL": "", "HELIUS_API_KEY": ""}
    e.update(env or {})
    with mock.patch.object(P.Http, "req", fake_req), mock.patch.dict(os.environ, e), \
            mock.patch.object(sys, "argv", argv + (["--dry-run"] if dry else [])):
        ps = [p.start() for p in extra_patches]
        try:
            P.main()
        finally:
            for p in extra_patches:
                p.stop()
    return json.load(open(os.path.join(d, "dry-run.json" if dry else "last-run.json")))


def quiet_req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
    self_.calls[src] = self_.calls.get(src, 0) + 1
    if url.endswith("/launches"):
        return []
    if url.endswith("/market"):
        return {"quotes": {}}
    return None


class LpTokens(unittest.TestCase):
    def test_lp_policy_known(self):
        pols = V.lp_policies(json.load(open(os.path.join(HERE, "..", "config.json"))))
        self.assertEqual(V.lp_dex(LP_UNIT, pols), "minswap_v2")
        self.assertEqual(V.lp_dex("e4214b7cce62ac6fbba385d164df48e157eae5863521b4b67ca71d86" + "ab" * 32, pols), "minswap_v1")
        self.assertIsNone(V.lp_dex(TOKEN, pols))

    def test_lp_mint_is_not_a_buy(self):
        w = addr("LPW")
        # liquidity add: wallet sends ADA + token, receives freshly minted LP tokens
        ut = {"lp1": {"inputs": [(w, 103_000_000, {TOKEN: 5000})], "outputs": [(w, 2_000_000, {LP_UNIT: FX["lp_delta"]}), (POOL, 100_000_000, {TOKEN: 5000})]},
              # zap-style: token in AND LP minted to the same wallet in one tx -> liquidity, not a buy
              "lp2": {"inputs": [(w, 50_000_000, {})], "outputs": [(w, 2_000_000, {TOKEN: 10, LP_UNIT: 7}), (POOL, 47_000_000, {})]}}
        txs = [("lp1", 1, 10), ("lp2", 2, 20)]
        self.assertEqual(F.buyers_from_txs(TOKEN, txs, ut), [])
        ut_lp = {"m": {"inputs": [(w, 10_000_000, {})], "outputs": [(w, 2_000_000, {LP_UNIT: 5})]}}
        self.assertEqual(F.buyers_from_txs(LP_UNIT, [("m", 1, 1)], ut_lp), [])

    def test_lp_buys_never_form_identical_size_flags(self):
        fc = json.load(open(os.path.join(HERE, "..", "config.json")))["flags"]
        ut, txs = {}, []
        for i in range(4):
            w = addr(f"LPS{i}")
            ut[f"t{i}"] = {"inputs": [(w, 60_000_000, {})], "outputs": [(w, 2_000_000, {LP_UNIT: 1_000_000}), (POOL, 58_000_000, {})]}
            txs.append((f"t{i}", 10 + i, 1000 + i))
        b = F.buyers_from_txs(LP_UNIT, txs, ut)
        self.assertEqual(b, [])
        self.assertNotIn("identical_size_buys", {f["kind"] for f in F.analyze_launch("LP", LP_UNIT, b, {}, fc)})


class WhaleRun(unittest.TestCase):
    """prefilter.main() --dry-run with stubbed chain: LP mint + FLOW aggregator buy must not fire the whale trigger."""

    def _run(self, legs=True, dec_source="koios"):
        wa, ws = addr("WHALE"), stake("WHALE")
        prev = {"lovelace": 900_000_000_000, FLOW_UNIT: FX["flow_prev_raw"]}
        snap = {"lovelace": 900_000_000_000 - 73_000_000, FLOW_UNIT: FX["flow_prev_raw"] + FX["flow_delta_raw"], LP_UNIT: FX["lp_delta"]}
        agg = FX["aggregator_tx"]
        ut = {"agg1": {"inputs": [(wa, agg["whale_in_lovelace"], {})],
                       "outputs": [(wa, agg["whale_out_lovelace"], {FLOW_UNIT: FX["flow_delta_raw"]}), (POOL, 73_000_000, {})]},
              "lpadd": {"inputs": [(wa, 500_000_000, {})], "outputs": [(wa, 500_000_000, {LP_UNIT: FX["lp_delta"]})]}}
        pool = {"ada_side": 50000, "mcap_ada": None, "price_ada": FX["flow_price_ada"], "basis": "geckoterminal"}
        asked = []

        def fake_req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            asked.append(url)
            if url.endswith("/launches"):
                return []
            if url.endswith("/market"):
                return {"quotes": {}}
            if url.endswith("/asset_info") and dec_source == "koios":
                return [{"token_registry_metadata": {"decimals": FX["flow_decimals"]}}]
            if "tokens.cardano.org" in url and dec_source == "registry":
                return {"decimals": {"value": FX["flow_decimals"]}}
            return None
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "c"))
            json.dump({"tag": P.tag(ws), "snapshot": prev, "height": 100}, open(os.path.join(d, "c", "whale-snapshot.json"), "w"))
            json.dump({"seen_launches": ["old"], "fired": {}, "kill_status": {}}, open(os.path.join(d, "state.json"), "w"))
            patches = [mock.patch.object(P, "tracked_names", lambda paper, log: {}),
                       mock.patch.object(P.Chain, "handle_address", lambda self, p, n: wa),
                       mock.patch.object(P.Chain, "stake_of", lambda self, a: ws),
                       mock.patch.object(P.Chain, "account_assets", lambda self, s: dict(snap)),
                       mock.patch.object(P.Chain, "account_txs_after", lambda self, s, h, n: [("agg1", 101), ("lpadd", 102)] if legs else []),
                       mock.patch.object(F.FlagChain, "tx_utxos", lambda self, hs: {h: ut[h] for h in hs}),
                       mock.patch.object(P, "gt_pool", lambda http, u: dict(pool) if u == FLOW_UNIT else None)]
            run = run_main(d, fake_req, patches, env={"SOLADA_WEBHOOK_URL": "https://example.invalid/hook", "WHALE_ADA_HANDLE": "fixture-handle"})
            F.assert_public(run)
            return run, asked

    def test_lp_and_aggregator_buy_do_not_fire_whale(self):
        run, _ = self._run()
        self.assertEqual([t for t in run["triggers"] if t["kind"] == "whale_move"], [])
        w = run["whale"]
        self.assertEqual(w["moves"], [])
        self.assertEqual(w["lp_moves"], [{"asset": "LP", "lp": True, "dex": "minswap_v2", "delta": FX["lp_delta"]}])

    def test_flow_valued_by_ada_leg_not_raw_units(self):
        self.assertAlmostEqual(FX["flow_delta_raw"] * FX["flow_price_ada"] / 1e6, 68.7, places=1)  # the old bogus "68.7M ADA"
        legs = V.ada_leg_by_unit(stake("WHALE"), {"agg1": {"inputs": [(addr("WHALE"), 76_000_000, {})],
                                                           "outputs": [(addr("WHALE"), 3_000_000, {FLOW_UNIT: FX["flow_delta_raw"]})]}}, F.addr_info)
        self.assertEqual(legs, {FLOW_UNIT: 73.0})
        self.assertEqual(V.value_ada(FX["flow_delta_raw"], legs[FLOW_UNIT], None, FX["flow_price_ada"]), (73.0, "ada_leg"))

    def test_decimals_fallback_koios_and_registry(self):
        exp = round(FX["flow_delta_raw"] / 10 ** FX["flow_decimals"] * FX["flow_price_ada"], 2)
        self.assertEqual(V.value_ada(FX["flow_delta_raw"], None, FX["flow_decimals"], FX["flow_price_ada"]), (exp, "decimals_x_price"))
        self.assertEqual(V.value_ada(FX["flow_delta_raw"], None, None, FX["flow_price_ada"]), (None, None))  # never raw x price
        for src in ("koios", "registry"):
            run, asked = self._run(legs=False, dec_source=src)
            self.assertEqual(run["triggers"], [])
            self.assertTrue(any("asset_info" in u for u in asked))
        self.assertLess(exp, 2000)

    def test_buy_flags_use_decimals_when_no_ada_leg(self):
        """Aggregator fill: the buyer's ADA left in an earlier order tx, so this tx has token in and ADA in."""
        fc = json.load(open(os.path.join(HERE, "..", "config.json")))["flags"]
        ut, txs = {}, []
        for i in range(3):
            w = addr(f"AGG{i}")
            ut[f"a{i}"] = {"inputs": [(POOL, 80_000_000, {FLOW_UNIT: FX["flow_delta_raw"]})],
                           "outputs": [(w, 2_000_000, {FLOW_UNIT: FX["flow_delta_raw"] + i * 1000}), (POOL, 78_000_000, {})]}
            txs.append((f"a{i}", 10, 1000 + i))
        b = F.buyers_from_txs(FLOW_UNIT, txs, ut, FX["flow_decimals"], FX["flow_price_ada"])
        self.assertEqual(len(b), 3)
        for x in b:
            self.assertEqual(x["ada_basis"], "decimals_x_price")
            self.assertLess(x["ada"], 100)  # ~68.8 ADA, not 68.7M
        out = F.analyze_launch("FLOW", FLOW_UNIT, b, {}, fc)
        idf = [f for f in out if f["kind"] == "identical_size_buys"]
        self.assertEqual(len(idf), 1)
        self.assertLess(idf[0]["ada_each"], 100)
        # unknown decimals: not sized, not a buy row (raw units are never ADA)
        self.assertEqual(F.buyers_from_txs(FLOW_UNIT, txs, ut, None, FX["flow_price_ada"]), [])


class KillDedupe(unittest.TestCase):
    def test_kill_line_fires_once_until_value_changes(self):
        k = FX["kill"]
        unit = "aa" * 28 + k["ticker"].encode().hex()
        tracked = {k["ticker"]: {"kill": {"ada_side_below": k["ada_side_below"]}, "text": "", "asset_id": unit, "source": "test"}}
        fired = []
        with tempfile.TemporaryDirectory() as d:
            json.dump({"seen_launches": ["old"], "fired": {}, "kill_status": {}}, open(os.path.join(d, "state.json"), "w"))
            for side in k["runs_ada_side"] + [None, k["runs_ada_side"][-1]]:
                pool = {"ada_side": side, "mcap_ada": None, "price_ada": 1.0, "basis": "geckoterminal"} if side is not None else None
                patches = [mock.patch.object(P, "tracked_names", lambda paper, log: json.loads(json.dumps(tracked))),
                           mock.patch.object(P, "gt_pool", lambda http, u, _p=pool: dict(_p) if _p else None),
                           mock.patch.object(P.Chain, "holders", lambda self, u, m: (300, False)),
                           mock.patch.object(P.Chain, "handle_address", lambda self, p, n: None)]
                # non-dry, no webhook URL: triggers are recorded in state.json as reported
                run = run_main(d, quiet_req, patches, dry=False)
                fired.append([t["metrics"]["ada_side"] for t in run["triggers"] if t["kind"] == "kill_line"])
            state = json.load(open(os.path.join(d, "state.json")))
        # 4200 fires; 4200 again deduped; 3900 is only ~7% deeper (< 25%) so deduped; a data gap is not a recovery
        self.assertEqual(fired, [[4200], [], [], [], []])
        self.assertEqual(state["kill_reported"][k["ticker"]]["metrics"]["ada_side"]["value"], 4200)



def kill_patches(tracked, side, holders=(300, False)):
    pool = {"ada_side": side, "mcap_ada": None, "price_ada": 1.0, "basis": "geckoterminal"} if side is not None else None
    return [mock.patch.object(P, "tracked_names", lambda paper, log: json.loads(json.dumps(tracked))),
            mock.patch.object(P, "gt_pool", lambda http, u, _p=pool: dict(_p) if _p else None),
            mock.patch.object(P.Chain, "holders", lambda self, u, m: holders),
            mock.patch.object(P.Chain, "handle_address", lambda self, p, n: None)]


def fake_tracked(tk="FAKEKL", line=5000):
    return {tk: {"kill": {"ada_side_below": line}, "text": "", "asset_id": "aa" * 28 + tk.encode().hex(), "source": "test"}}


def fresh_state(d, extra=None):
    st = {"seen_launches": ["old"], "fired": {}, "kill_status": {}}
    st.update(extra or {})
    json.dump(st, open(os.path.join(d, "state.json"), "w"))


class KillHysteresis(unittest.TestCase):
    def test_moving_values_refire_only_when_25pct_deeper_and_recover_with_hysteresis(self):
        tracked = fake_tracked()
        # line 5000: drift down slowly (no re-fire), 26% deeper (fires), gap, near-line bounce (no recovery),
        # two full-data runs >=10% above (recovered; gap in between does not count), then a re-cross is news again
        runs = [4200, 4150, 4000, 3500, 3100, 3050, None, 5200, 5600, None, 5600, 4800]
        want = [[4200], [], [], [], [3100], [], [], [], [], [], [], [4800]]
        fired = []
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            for side in runs:
                run = run_main(d, quiet_req, kill_patches(tracked, side), dry=False)
                fired.append([t["metrics"]["ada_side"] for t in run["triggers"] if t["kind"] == "kill_line"])
            state = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(fired, want)
        self.assertEqual(state["kill_reported"]["FAKEKL"]["metrics"]["ada_side"]["value"], 4800)

    def test_threshold_is_configurable(self):
        kc = V.kill_cfg({"kill_dedupe": {"refire_deeper_pct": 5}})
        e = {"metrics": {"holders": {"value": 100, "line": 120}}, "recover_streak": 0}
        fire, _, _ = V.kill_decide(json.loads(json.dumps(e)), {"holders": (94, 120)}, {"holders": 94}, {"holders": 120}, True, kc)
        self.assertTrue(fire)
        fire, _, _ = V.kill_decide(json.loads(json.dumps(e)), {"holders": (94, 120)}, {"holders": 94}, {"holders": 120}, True, V.kill_cfg())
        self.assertFalse(fire)

    def test_new_metric_crossing_fires(self):
        e = {"metrics": {"holders": {"value": 100, "line": 120}}, "recover_streak": 0}
        fire, ent, why = V.kill_decide(e, {"holders": (100, 120), "mcap": (300, 400)}, {"holders": 100, "mcap": 300},
                                      {"holders": 120, "mcap": 400}, True, V.kill_cfg())
        self.assertTrue(fire)
        self.assertEqual(sorted(ent["metrics"]), ["holders", "mcap"])

    def test_signature_has_no_live_numbers(self):
        self.assertEqual(V.kill_signature(["holders 55 < 80"]), V.kill_signature({"holders": (41, 80)}))

    def test_legacy_state_migrates_without_refire(self):
        tracked = fake_tracked("BOLTX", 400000)
        tracked["BOLTX"]["kill"] = {"mcap_ada_below": 400000}
        legacy = {"BOLTX": {"at": "2026-10-25T20:57:52Z", "crossed": ["mcap 330616 < 400000"], "sig": "2797655f559e"}}
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d, {"kill_reported": legacy})
            fired = []
            for mcap in (318000, 290000, 260000):  # all < 25% deeper than the reported 330616
                pool = {"ada_side": 9000, "mcap_ada": mcap, "price_ada": 1.0, "basis": "geckoterminal"}
                ps = kill_patches(tracked, 9000)
                ps[1] = mock.patch.object(P, "gt_pool", lambda http, u, _p=pool: dict(_p))
                run = run_main(d, quiet_req, ps, dry=False)
                fired.append([t["name"] for t in run["triggers"] if t["kind"] == "kill_line"])
            state = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(fired, [[], [], []])
        self.assertEqual(state["kill_reported"]["BOLTX"]["metrics"]["mcap"], {"value": 330616.0, "line": 400000.0})

    def test_live_desk_data_shape_migrates(self):
        for c in ("holders 55 < 80", "ada_side 6078 < 7000", "mcap 330616 < 400000"):
            e = V.migrate_kill_entry({"crossed": [c], "sig": "x", "at": "t"})
            self.assertEqual(len(e["metrics"]), 1)


class HolderPagination(unittest.TestCase):
    def _chain(self, pages):
        http = P.Http({})
        ch = P.Chain(http, "test-key")
        calls = []

        def bf(path):
            calls.append(path)
            page = int(path.rsplit("page=", 1)[1])
            return pages[page - 1] if page <= len(pages) else []
        ch.bf = bf
        ch.koios_get = lambda path: self.fail("no Koios fallback after a mid-pagination failure")
        return ch

    def test_mid_pagination_failure_is_unknown(self):
        full = [{"address": addr("h", i), "quantity": "5"} for i in range(100)]
        ch = self._chain([full, None])
        unit = "bb" * 28 + "4b"
        self.assertEqual(ch.holders(unit, 10), (None, False))
        self.assertNotIn(unit, ch.dist)  # no partial distribution for concentration flags

    def test_unknown_holders_never_cross_kill_line(self):
        tracked = fake_tracked()
        tracked["FAKEKL"]["kill"] = {"holders_below": 80}
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run = run_main(d, quiet_req, kill_patches(tracked, 9000, holders=(None, False)), dry=False)
            row = [r for r in run["results"] if r.get("tracked")][0]
        self.assertEqual([t for t in run["triggers"] if t["kind"] == "kill_line"], [])
        self.assertIsNone(row["holders"])
        self.assertEqual(row["crossed"], [])

    def test_complete_pages_count(self):
        full = [{"address": addr("h", i), "quantity": "5"} for i in range(100)]
        ch = self._chain([full, full[:30]])
        self.assertEqual(ch.holders("cc" * 28, 10), (130, False))


class WebhookDelivery(unittest.TestCase):
    def test_any_2xx_is_success_and_timeouts_are_not_retried(self):
        import urllib.request as UR

        class Resp:
            status = 202
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"not json"
        http = P.Http({"webhook": 0})
        with mock.patch.object(UR, "urlopen", lambda r, timeout=30: Resp()):
            self.assertTrue(http.post_webhook("https://example.invalid/h", {"a": 1}, {"Idempotency-Key": "k"}))
        n = []

        def boom(r, timeout=30):
            n.append(r)
            raise TimeoutError()
        with mock.patch.object(UR, "urlopen", boom):
            self.assertFalse(http.post_webhook("https://example.invalid/h", {"a": 1}))
        self.assertEqual(len(n), 1)

    def test_idempotency_key_stable_within_slot(self):
        cfg = {"webhook": {"idempotency_slot_minutes": 120}}
        a = P.idempotency_key(cfg, ["b", "a"], now=7200 * 10 + 5)
        self.assertEqual(a, P.idempotency_key(cfg, ["a", "b"], now=7200 * 10 + 7000))
        self.assertNotEqual(a, P.idempotency_key(cfg, ["a", "b"], now=7200 * 11 + 1))

    def test_failed_send_queues_and_retries_next_run(self):
        sent = []
        results = iter([False, True])

        def post(self_, url, body, headers=None):
            sent.append((body, headers))
            return next(results)
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            env = {"SOLADA_WEBHOOK_URL": "https://example.invalid/hook"}
            pw = mock.patch.object(P.Http, "post_webhook", post)
            run_main(d, quiet_req, kill_patches(fake_tracked(), 4200) + [pw], dry=False, env=env)
            st = json.load(open(os.path.join(d, "state.json")))
            self.assertEqual([t["name"] for t in st["pending_triggers"]], ["FAKEKL"])
            self.assertNotIn("FAKEKL", st.get("kill_reported", {}))
            # next run: the name no longer crosses (no fresh trigger), but the queued one is retried
            pw = mock.patch.object(P.Http, "post_webhook", post)
            run_main(d, quiet_req, kill_patches({}, None) + [pw], dry=False, env=env)
            st = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(len(sent), 2)
        self.assertEqual([t["name"] for t in sent[1][0]["metrics"]["triggers"]], ["FAKEKL"])
        self.assertIn("Idempotency-Key", sent[1][1])
        self.assertEqual(st["pending_triggers"], [])
        self.assertIn("FAKEKL", st["kill_reported"])
        self.assertTrue(any(k.startswith("kill:FAKEKL:") for k in st["fired"]))


class DeskFilesAndRuleDefaults(unittest.TestCase):
    def test_parse_failure_uses_config_defaults(self):
        th = P.parse_rules({"rules": []}, {"min_holders": 120, "min_ada_side_k": 25000, "min_ada_side_y": 17000})
        self.assertEqual((th["min_holders"], th["min_ada_side"]), (120, 25000))

    def test_parse_failure_without_defaults_still_raises(self):
        with self.assertRaises(SystemExit):
            P.parse_rules({"rules": []})

    def test_desk_data_preferred_over_main(self):
        with tempfile.TemporaryDirectory() as desk, tempfile.TemporaryDirectory() as repo:
            for base, v in ((desk, "desk"), (repo, "main")):
                os.makedirs(os.path.join(base, "solada"))
                json.dump({"v": v}, open(os.path.join(base, "solada", "log.json"), "w"))
            json.dump({"v": "main"}, open(os.path.join(repo, "solada", "paper.json"), "w"))
            self.assertEqual(P.load_desk_json("solada/log.json", desk, repo), ({"v": "desk"}, "desk-data"))
            self.assertEqual(P.load_desk_json("solada/paper.json", desk, repo), ({"v": "main"}, "main"))


if __name__ == "__main__":
    unittest.main()
