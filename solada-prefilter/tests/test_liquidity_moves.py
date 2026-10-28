"""SPD-55 liquidity moves (offline, no secrets, no network). Frozen fixture, synthetic addresses."""
import hashlib, json, os, shutil, subprocess, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import whale_ledger as WL  # noqa: E402
import liquidity_moves as LM  # noqa: E402
import prefilter as P  # noqa: E402
from test_regressions import fresh_state, quiet_req, run_main  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "liquidity_moves.json")))
UNIT = FX["policy"] + FX["name"]
LP = LM.MINSWAP_V2_LP + FX["lp_name"]
NOW = FX["now"]
POOL = F.b32_encode("addr", b"\x71" + bytes.fromhex(LM.MINSWAP_V2_SCRIPT))
ORDER = F.b32_encode("addr", b"\x71" + b"\x22" * 28)
SUNDAE = F.b32_encode("addr", b"\x71" + b"\x55" * 28)
SPLASH = F.b32_encode("addr", b"\x71" + b"\x66" * 28)
SUNDAE_NFT = LM.SUNDAE_V3_LP + "ab" * 28
SPLASH_NFT = "cd" * 28 + b"SNEK_ADA_NFT".hex()
OWNER = "^zzNOTyet$"


def kh(label):
    return hashlib.sha256(("liq/" + label).encode()).digest()[:28]


def addr(label):
    return F.b32_encode("addr", b"\x01" + kh(label + "/pay") + kh(label))


def stake(label):
    return F.b32_encode("stake", b"\xe1" + kh(label))


def _assets(tok, extra=None):
    out = {}
    if tok:
        out[UNIT] = tok
    if extra:
        out.update(extra)
    return out


def _row(addr_, lov, assets, st=None):
    return {"addr": addr_, "lovelace": lov, "assets": assets, "stake": st}


def _koios_side(rows, hsh):
    out = []
    for i, u in enumerate(rows):
        out.append({"tx_hash": hsh, "tx_index": i, "payment_addr": {"bech32": u["addr"]},
                    "stake_addr": u.get("stake"), "value": str(u["lovelace"]),
                    "asset_list": [{"policy_id": k[:56], "asset_name": k[56:], "quantity": str(q)}
                                   for k, q in u["assets"].items()]})
    return out


def swap_tx():
    s = FX["swap"]
    return {"hash": s["hash"], "time": NOW - s["age_s"], "height": 999000,
            "inputs": [_row(POOL, s["pool_in_lov"], _assets(s["tok_in"], {LP: s["lp_pool"]}))],
            "outputs": [_row(POOL, s["pool_out_lov"], _assets(s["tok_out"], {LP: s["lp_pool"]}))]
            + [_row(ORDER, 5_000_000_000, _assets(s["tok_in"]), None)]}


def add_tx(st=None, user=None):
    s = FX["add"]
    user = user or addr("lp")
    return {"hash": s["hash"], "time": NOW - s["age_s"], "height": 999200,
            "inputs": [_row(POOL, s["pool_in_lov"], _assets(s["tok_in"], {LP: s["lp_pool_in"]})),
                       _row(user, 5_000_000, {}, st or stake("lp"))],
            "outputs": [_row(POOL, s["pool_out_lov"], _assets(s["tok_out"], {LP: s["lp_pool_out"]})),
                        _row(user, 2_000_000, {LP: s["lp_user"]}, st or stake("lp"))]}


def pull_tx(st=None):
    s = FX["pull"]
    user = addr("lp")
    return {"hash": s["hash"], "time": NOW - s["age_s"], "height": 999400,
            "inputs": [_row(POOL, s["pool_in_lov"], _assets(s["tok_in"], {LP: s["lp_pool_in"]})),
                       _row(user, 2_000_000, {LP: s["lp_user"]}, st or stake("lp"))],
            "outputs": [_row(POOL, s["pool_out_lov"], _assets(s["tok_out"], {LP: s["lp_pool_out"]})),
                        _row(user, 20_000_000_000, {}, st or stake("lp"))]}


def sell_tx():
    s = FX["sell"]
    return {"hash": s["hash"], "time": NOW - s["age_s"], "height": 999600,
            "inputs": [_row(POOL, s["pool_in_lov"], _assets(s["tok_in"], {LP: s["lp_pool"]})),
                       {"addr": POOL, "lovelace": 9_000_000, "assets": {UNIT: 1}, "stake": None, "collateral": True}],
            "outputs": [_row(POOL, s["pool_out_lov"], _assets(s["tok_out"], {LP: s["lp_pool"]}))]}


def book(which):
    return {"swap": swap_tx, "add": add_tx, "pull": pull_tx, "sell": sell_tx}[which]()


class Pure(unittest.TestCase):
    def test_minswap_lp_name_matches_the_frozen_min_pair(self):
        self.assertEqual(LM.minswap_v2_lp_name(UNIT), FX["lp_name"])
        self.assertEqual(LM.minswap_v2_lp_name(FX["known_min_unit"]), FX["known_min_lp"])

    def test_policy_keys_do_not_depend_on_order(self):
        a = "11" * 28 + b"MIN".hex()
        b = "22" * 28 + b"MIN".hex()
        one = WL.assign_token_keys([a, b])
        two = WL.assign_token_keys([b, a])
        self.assertEqual(one, two)
        self.assertIn("~", one[a])
        self.assertIn("~", one[b])
        self.assertNotEqual(one[a], one[b])
        self.assertTrue(one[a].endswith(a[:6]) or one[b].endswith(b[:6]))

    def test_swap_add_pull_and_the_pool_script(self):
        bc = LM.lm_cfg({})
        scripts, splash = LM.dex_tables({})
        buy = LM.events_from_tx(swap_tx(), UNIT, scripts, splash, bc["min_pool_ada"] * 1e6)
        self.assertEqual(len(buy), 1)
        self.assertEqual(buy[0]["dex"], "minswap_v2")
        self.assertEqual(LM.kind_of(buy[0]["ada_delta"], buy[0]["token_delta"], bc), ("swap", "buy"))
        self.assertEqual(round(abs(buy[0]["ada_delta"]) / 1e6, 2), FX["swap"]["expect_ada"])
        added = LM.events_from_tx(add_tx(), UNIT, scripts, splash, bc["min_pool_ada"] * 1e6)
        self.assertEqual(LM.kind_of(added[0]["ada_delta"], added[0]["token_delta"], bc), ("lp_add", None))
        self.assertEqual(added[0]["lp_stake"], stake("lp"))
        pulled = LM.events_from_tx(pull_tx(), UNIT, scripts, splash, bc["min_pool_ada"] * 1e6)
        self.assertEqual(LM.kind_of(pulled[0]["ada_delta"], pulled[0]["token_delta"], bc), ("lp_remove", None))
        small = FX["small"]
        tiny = {"hash": small["hash"], "time": NOW, "inputs": [_row(POOL, small["pool_in_lov"], _assets(small["tok_in"]))],
                "outputs": [_row(POOL, small["pool_out_lov"], _assets(small["tok_out"]))]}
        ev = LM.events_from_tx(tiny, UNIT, scripts, splash, bc["min_pool_ada"] * 1e6)[0]
        self.assertEqual(LM.kind_of(ev["ada_delta"], ev["token_delta"], bc), (None, None))

    def test_order_script_is_not_a_pool_and_collateral_is_not_a_leg(self):
        scripts, splash = LM.dex_tables({})
        order = {"hash": "11" * 32, "time": NOW,
                 "inputs": [_row(ORDER, 20_000_000_000, _assets(500000))],
                 "outputs": [_row(ORDER, 2_000_000, {})]}
        self.assertEqual(LM.events_from_tx(order, UNIT, scripts, splash, 500_000_000), [])
        sell = sell_tx()
        # collateral row is only present for the Blockfrost normalizer; the pure path uses the dict as-is
        sell["inputs"] = [u for u in sell["inputs"] if not u.get("collateral")]
        ev = LM.events_from_tx(sell, UNIT, scripts, splash, 500_000_000)[0]
        self.assertEqual(LM.kind_of(ev["ada_delta"], ev["token_delta"], LM.lm_cfg({})), ("swap", "sell"))
        self.assertEqual(round(abs(ev["ada_delta"]) / 1e6, 2), FX["sell"]["expect_ada"])

    def test_sundae_and_splash_markers(self):
        scripts, splash = LM.dex_tables({})
        lov = 80_000_000_000
        sun = {"hash": "22" * 32, "time": NOW,
               "inputs": [_row(SUNDAE, lov, _assets(1000, {SUNDAE_NFT: 1}))],
               "outputs": [_row(SUNDAE, lov + 6_000_000_000, _assets(400, {SUNDAE_NFT: 1}))]}
        ev = LM.events_from_tx(sun, UNIT, scripts, splash, 500_000_000)[0]
        self.assertEqual(ev["dex"], "sundaeswap_v3")
        spl = {"hash": "33" * 32, "time": NOW,
               "inputs": [_row(SPLASH, lov, _assets(1000, {SPLASH_NFT: 1}))],
               "outputs": [_row(SPLASH, lov - 6_000_000_000, _assets(2000, {SPLASH_NFT: 1}))]}
        ev = LM.events_from_tx(spl, UNIT, scripts, splash, 500_000_000)[0]
        self.assertEqual(ev["dex"], "splash")
        self.assertEqual(LM.kind_of(ev["ada_delta"], ev["token_delta"], LM.lm_cfg({})), ("swap", "sell"))

    def test_pull_then_sell_is_ahead_of_dump_and_the_other_way_is_not(self):
        bc = LM.lm_cfg({})
        pull = {"kind": "lp_remove", "time": NOW - 3600, "pool_key": "minswap_v2~41edcb63", "unit": UNIT,
                "hash": FX["pull"]["hash"], "tx_short": FX["pull"]["hash"][:10], "ada": FX["pull"]["expect_ada"],
                "reserve_ada": 90000.0}
        sell = {"kind": "swap", "direction": "sell", "time": NOW - 1800, "pool_key": pull["pool_key"], "unit": UNIT,
                "hash": FX["sell"]["hash"], "tx_short": FX["sell"]["hash"][:10], "ada": FX["sell"]["expect_ada"]}
        waiting, flagged = LM.mark_dumps([pull, sell], [], bc, NOW)
        self.assertEqual(waiting, [])
        self.assertTrue(pull["ahead_of_dump"])
        self.assertEqual(pull["dump_tx_short"], sell["tx_short"])
        early = dict(sell, time=NOW - 7200)
        later = dict(pull, ahead_of_dump=False, dump_tx_short=None)
        waiting, flagged = LM.mark_dumps([early, later], [], bc, NOW)
        self.assertFalse(later.get("ahead_of_dump"))
        self.assertEqual(flagged, [])
        dust = dict(sell, ada=10, time=NOW - 1000)
        quiet = dict(pull, ahead_of_dump=False, dump_tx_short=None, reserve_ada=90000.0)
        LM.mark_dumps([quiet, dust], [], bc, NOW)
        self.assertFalse(quiet.get("ahead_of_dump"))

    def test_ada_leg_is_not_quantity_times_price(self):
        ev = {"hash": FX["swap"]["hash"], "time": NOW, "kind": "swap", "direction": "buy", "dex": "minswap_v2",
              "pool_key": "minswap_v2~41edcb63", "ada_delta": -8_000_000_000, "token_delta": -600000,
              "reserve_lovelace": 108_000_000_000, "actor_tag": None, "ahead_of_dump": False,
              "dump_tx_short": None, "dump_ada": None}
        row = LM.public_move(ev, "MIN", "MINX", "aaaaaaaa", FX["decimals"], False)
        self.assertEqual(row["ada"], 8000.0)
        self.assertEqual(row["ada_basis"], "ada_leg")
        self.assertNotEqual(row["ada"], round(0.6 * FX["price_ada_ignored"], 2))
        self.assertEqual(row["qty"], 0.6)
        self.assertEqual(row["level"], "pool")
        lp_row = LM.public_move(ev, "LP~aaaaaaaa", "MINX", "f5808c2c", None, True)
        self.assertEqual(lp_row["ticker"], "LP")
        self.assertTrue(lp_row["lp"])
        self.assertEqual(lp_row["qty_basis"], "raw")


def _top(ticker=None):
    return [{"rank": 1, "ticker": ticker or FX["ticker"], "policy_short": FX["policy_short"]}]


class Run(unittest.TestCase):
    def fake(self, txs, fail_koios=False, bf=False):
        by_hash = {t["hash"]: t for t in txs}
        calls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append((src, url, body, headers))
            if src == "koios" and fail_koios:
                return None
            if url.endswith("/tip"):
                return [{"block_height": 1000000, "block_time": NOW}]
            if url.endswith("/blocks/latest"):
                return {"height": 1000000, "time": NOW}
            if "/asset_txs" in url or "/transactions?" in url:
                if FX["name"] in url and FX["lp_name"] not in url and not bf:
                    return []
                after = 0
                if "_after_block_height=" in url:
                    after = int(url.split("_after_block_height=")[1].split("&")[0])
                rows = [t for t in txs if (t["height"] > after)]
                if src == "blockfrost" or "/transactions?" in url:
                    return [{"tx_hash": t["hash"], "block_height": t["height"], "block_time": t["time"]} for t in rows]
                return [{"tx_hash": t["hash"], "block_height": t["height"], "block_time": t["time"]} for t in rows]
            if url.endswith("/tx_info"):
                out = []
                for h in body["_tx_hashes"]:
                    t = by_hash[h]
                    sides = {}
                    for side in ("inputs", "outputs"):
                        sides[side] = _koios_side([u for u in t[side] if not u.get("collateral")], h)
                    out.append({"tx_hash": h, **sides})
                return out
            if url.endswith("/utxos") and src == "blockfrost":
                h = url.split("/txs/")[1].split("/")[0]
                t = by_hash[h]
                def amt(u):
                    rows = [{"unit": "lovelace", "quantity": str(u["lovelace"])}]
                    rows += [{"unit": k, "quantity": str(q)} for k, q in u["assets"].items()]
                    row = {"address": u["addr"], "amount": rows}
                    if u.get("collateral"):
                        row["collateral"] = True
                    return row
                return {"hash": h, "inputs": [amt(u) for u in t["inputs"]], "outputs": [amt(u) for u in t["outputs"]]}
            return None
        return req, calls

    def _run(self, txs, d, c, cfg=None, owner=OWNER, watch=None, bf_key="", fail_koios=False, dry=False, meta=None):
        http = P.Http({"koios": 0, "blockfrost": 0})
        req, calls = self.fake(txs, fail_koios=fail_koios)
        cfg = cfg or {}
        with mock.patch.object(P.Http, "req", req):
            summary = LM.run(http, cfg, _top(), {FX["ticker"]: UNIT}, meta or {UNIT: {"decimals": FX["decimals"], "price_ada": FX["price_ada_ignored"]}},
                             V.lp_policies(), d, c, dry, NOW, bf_key=bf_key, owner_env=owner, watch=watch or {})
        return summary, calls

    def test_run_writes_pool_rows_and_flags_the_pull(self):
        txs = [swap_tx(), add_tx(), pull_tx(), sell_tx()]
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary, calls = self._run(txs, d, c)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
            summary2, calls2 = self._run(txs, d, c)
            doc2 = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertIn("pool level", doc["note"])
        self.assertIn("batcher", doc["note"])
        self.assertEqual(doc["budget"]["max_calls"], 36)
        self.assertEqual(doc["budget"]["max_bf_calls"], 12)
        self.assertLessEqual(summary["koios_calls"], 36)
        kinds = {(r["kind"], r.get("direction")) for r in doc["moves"]}
        self.assertEqual(kinds, {("swap", "buy"), ("lp_add", None), ("lp_remove", None), ("swap", "sell")})
        buy = next(r for r in doc["moves"] if r["direction"] == "buy")
        self.assertEqual((buy["ada"], buy["qty"], buy["ada_basis"], buy["level"]), (8000.0, 0.6, "ada_leg", "pool"))
        self.assertIsNone(buy["actor_tag"])
        self.assertEqual(buy["token"], WL.assign_token_keys([UNIT])[UNIT])
        pull = next(r for r in doc["moves"] if r["kind"] == "lp_remove")
        self.assertTrue(pull["ahead_of_dump"])
        self.assertEqual(pull["dump_tx_short"], FX["sell"]["hash"][:10])
        self.assertEqual(pull["actor_tag"], F.tag(stake("lp")))
        self.assertTrue(pull["actor_tag"].startswith("w-") and len(pull["actor_tag"]) == 10)
        self.assertEqual(len(doc2["moves"]), len(doc["moves"]))
        self.assertEqual(summary2["moves_new"], 0)
        self.assertFalse(any("/tx_info" in u or u.endswith("/utxos") for s, u, b, h in calls2))
        blob = json.dumps(doc) + json.dumps(cache)
        self.assertNotIn(stake("lp"), blob)
        self.assertNotIn(addr("lp"), blob)
        self.assertNotIn(POOL, blob)
        F.assert_public(doc)
        self.assertTrue(all(len(b["_tx_hashes"]) <= 10 and len(json.dumps(b)) < 1024 for s, u, b, h in calls if b))

    def test_role_label_owner_drop_and_missing_patterns(self):
        raw = "stake1uynnk0mezk" + "q" * 40
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            self._run([add_tx(raw)], d, c, watch={"uynnk0mezk": "pool_preseeder"})
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertEqual(doc["moves"][0]["actor_tag"], "pool_preseeder")
        self.assertNotIn(raw, json.dumps(doc))
        self.assertNotIn("uynnk0mezk", json.dumps(doc))
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            self._run([add_tx()], d, c, owner="^" + __import__("re").escape(stake("lp")) + "$")
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertIsNone(doc["moves"][0]["actor_tag"])
        self.assertNotIn(stake("lp"), json.dumps(doc))
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary, _ = self._run([add_tx()], d, c, owner="")
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertFalse(doc["actor_tags"])
        self.assertIsNone(doc["moves"][0]["actor_tag"])
        self.assertGreaterEqual(summary["moves"], 1)

    def test_dump_on_the_next_run_flags_the_stored_pull(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            self._run([pull_tx()], d, c)
            first = json.load(open(os.path.join(d, "liquidity_moves.json")))
            self.assertFalse(first["moves"][0]["ahead_of_dump"])
            self._run([pull_tx(), sell_tx()], d, c)
            second = json.load(open(os.path.join(d, "liquidity_moves.json")))
        pull = next(r for r in second["moves"] if r["kind"] == "lp_remove")
        self.assertTrue(pull["ahead_of_dump"])
        self.assertEqual(pull["dump_tx_short"], FX["sell"]["hash"][:10])

    def test_unresolved_and_policy_mismatch_make_no_calls(self):
        http = P.Http({"koios": 0})
        with mock.patch.object(P.Http, "req", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))), \
                tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            s1 = LM.run(http, {}, [{"ticker": "ZED", "policy_short": "ffff"}], {}, {}, V.lp_policies(), d, c, False, NOW, owner_env=OWNER)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
            self.assertEqual(s1["koios_calls"], 0)
            self.assertEqual(doc["skipped"][0]["skipped"], "unresolved")
            LM.run(http, {}, _top(), {FX["ticker"]: "bb" * 28 + FX["name"]}, {}, V.lp_policies(), d, c, False, NOW, owner_env=OWNER)
            doc2 = json.load(open(os.path.join(d, "liquidity_moves.json")))
            self.assertEqual(doc2["skipped"][0]["skipped"], "policy_mismatch")

    def test_budget_and_dry_run_and_blockfrost(self):
        txs = [swap_tx()]
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary, _ = self._run(txs, d, c, cfg={"liquidity_moves": {"max_calls": 1}})
            self.assertLessEqual(summary["koios_calls"], 1)
            self.assertIn(FX["ticker"], summary["capped"] or [])
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary, calls = self._run(txs, d, c, dry=True)
            self.assertFalse(os.path.exists(os.path.join(d, "liquidity_moves.json")))
            self.assertFalse(summary["wrote"])
        key = "k-test-not-a-secret"
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary, calls = self._run(txs, d, c, bf_key=key, fail_koios=True)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertGreater(summary["blockfrost_fallbacks"], 0)
        self.assertIn(summary["basis"], ("blockfrost", "mixed"))
        self.assertTrue(any(r["kind"] == "swap" for r in doc["moves"]))
        self.assertTrue(all((h or {}).get("project_id") == key for s, u, b, h in calls if s == "blockfrost"))
        self.assertNotIn(key, json.dumps(doc))
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary, calls = self._run(txs, d, c, bf_key="", fail_koios=True)
        self.assertEqual(summary["blockfrost_fallbacks"], 0)
        self.assertFalse(any(s == "blockfrost" for s, *_ in calls))

    def test_lp_token_is_labelled_and_a_crash_is_swallowed(self):
        tx = swap_tx()
        # Tracked asset is the LP token itself. The pool script still identifies Minswap.
        lp_unit = LP
        for side in ("inputs", "outputs"):
            for u in tx[side]:
                if UNIT in u["assets"]:
                    u["assets"][lp_unit] = u["assets"].pop(UNIT)
        http = P.Http({"koios": 0})
        req, _ = self.fake([tx])
        with mock.patch.object(P.Http, "req", req), tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            LM.run(http, {}, [{"rank": 1, "ticker": "MINX", "policy_short": LM.MINSWAP_V2_LP[:10], "unit": lp_unit}],
                   {}, {lp_unit: {"decimals": 0}}, V.lp_policies(), d, c, False, NOW, owner_env=OWNER)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertTrue(doc["moves"])
        self.assertTrue(all(r["ticker"] == "LP" and r["lp"] for r in doc["moves"]))
        self.assertEqual(LM.run(None, {}, [], {}, {}, {}, "/tmp", None, False, NOW)["wrote"], False)

    def test_a_liquidity_crash_does_not_stop_the_prefilter(self):
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            boom = mock.patch.object(P.LM, "run", side_effect=SystemExit("liquidity failed"))
            run = run_main(d, quiet_req, [boom], dry=False)
            self.assertIn("liquidity_moves", run)
            self.assertIsNone(run["liquidity_moves"])
            self.assertTrue(os.path.exists(os.path.join(d, "state.json")))


class ReviewFixes(unittest.TestCase):
    """B1, B2, S1, S2, S6, S7 from the PR #38 review."""

    def test_sundae_cip68_prefix_does_not_collapse_pool_keys(self):
        a = LM.SUNDAE_V3_LP + "000de140" + "11" * 28
        b = LM.SUNDAE_V3_LP + "000de140" + "22" * 28
        ka, kb = LM.pool_key("sundaeswap_v3", a, UNIT), LM.pool_key("sundaeswap_v3", b, UNIT)
        self.assertNotEqual(ka, kb)
        self.assertNotEqual(ka, "sundaeswap_v3~000de140")
        self.assertEqual(ka, "sundaeswap_v3~" + hashlib.sha256(a.encode()).hexdigest()[:8])
        lov = 80_000_000_000
        tx = {"hash": "44" * 32, "time": NOW, "inputs": [
            _row(SUNDAE, lov, _assets(1000, {a: 1})), _row(SUNDAE, lov, _assets(1000, {b: 1}))],
            "outputs": [_row(SUNDAE, lov + 6_000_000_000, _assets(400, {a: 1})),
                        _row(SUNDAE, lov - 7_000_000_000, _assets(1800, {b: 1}))]}
        evs = LM.events_from_tx(tx, UNIT, *LM.dex_tables({}), 500_000_000)
        self.assertEqual(len(evs), 2)
        self.assertEqual({LM.pool_key(e["dex"], e["marker"], UNIT) for e in evs}, {ka, kb})
        self.assertEqual(sorted(abs(e["ada_delta"]) for e in evs), [6_000_000_000, 7_000_000_000])

    def test_msp_auth_token_is_not_the_pool_key(self):
        msp = LM.MINSWAP_V2_LP + "4d5350"
        lp_a = LM.MINSWAP_V2_LP + "ab" * 32
        lp_b = LM.MINSWAP_V2_LP + "cd" * 32
        self.assertTrue(LM._shared_auth(msp))
        self.assertIsNone(LM._pick_marker([msp], "minswap_v2"))
        self.assertEqual(LM._pick_marker([msp, lp_a], "minswap_v2"), lp_a)
        self.assertNotEqual(LM.pool_key("minswap_v2", lp_a, UNIT), LM.pool_key("minswap_v2", msp, UNIT))
        lov = 90_000_000_000
        tx = {"hash": "55" * 32, "time": NOW, "inputs": [
            _row(POOL, lov, _assets(1000, {msp: 1, lp_a: 10})),
            _row(POOL, lov, _assets(1000, {msp: 1, lp_b: 10}))],
            "outputs": [
            _row(POOL, lov + 8_000_000_000, _assets(400, {msp: 1, lp_a: 10})),
            _row(POOL, lov - 9_000_000_000, _assets(2000, {msp: 1, lp_b: 10}))]}
        evs = LM.events_from_tx(tx, UNIT, *LM.dex_tables({}), 500_000_000)
        self.assertEqual({e["marker"] for e in evs}, {lp_a, lp_b})
        keys = {LM.pool_key(e["dex"], e["marker"], UNIT) for e in evs}
        self.assertEqual(len(keys), 2)
        self.assertNotIn(LM.pool_key("minswap_v2", msp, UNIT), keys)

    def test_marker_must_persist_and_min_pool_ada_and_dump_rules(self):
        scripts, splash = LM.dex_tables({})
        nft = LM.SUNDAE_V3_LP + "000de140" + "33" * 28
        only_in = {"hash": "66" * 32, "time": NOW,
                   "inputs": [_row(SUNDAE, 80_000_000_000, _assets(1000, {nft: 1}))],
                   "outputs": [_row(SUNDAE, 80_000_000_000, _assets(1000))]}
        self.assertEqual(LM.events_from_tx(only_in, UNIT, scripts, splash, 500_000_000), [])
        dust = {"hash": "77" * 32, "time": NOW,
                "inputs": [_row(POOL, 400_000_000, _assets(1000, {LP: 1}))],
                "outputs": [_row(POOL, 499_000_000, _assets(900, {LP: 1}))]}
        self.assertEqual(LM.events_from_tx(dust, UNIT, scripts, splash, 500 * 1_000_000), [])
        bc = LM.lm_cfg({})
        pull = {"kind": "lp_remove", "time": NOW - 10 * 3600, "pool_key": "p1", "unit": UNIT,
                "ada": 20000, "reserve_ada": 90000, "hash": "aa" * 32, "tx_short": "aa" * 5}
        sell = {"kind": "swap", "direction": "sell", "time": NOW - 3600, "pool_key": "p1", "unit": UNIT,
                "ada": 12000, "hash": "bb" * 32, "tx_short": "bb" * 5}
        waiting, flagged = LM.mark_dumps([pull, sell], [], bc, NOW)
        self.assertEqual(flagged, [])
        self.assertFalse(pull.get("ahead_of_dump"))
        inside = dict(pull, time=NOW - 3 * 3600, ahead_of_dump=False)
        other = dict(sell, pool_key="p2", time=NOW - 1800)
        LM.mark_dumps([inside, other], [], bc, NOW)
        self.assertFalse(inside.get("ahead_of_dump"))
        other_unit = dict(sell, unit=UNIT + "ff", time=NOW - 1000)
        same = dict(inside, ahead_of_dump=False)
        LM.mark_dumps([same, other_unit], [], bc, NOW)
        self.assertFalse(same.get("ahead_of_dump"))

    def test_blockfrost_ignores_after_so_we_read_newest_first(self):
        recent = swap_tx()
        ancient = {"tx_hash": "00" * 32, "block_height": 10, "block_time": 1}
        calls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append((src, url))
            if src == "koios":
                return None
            if url.endswith("/blocks/latest"):
                return {"height": 1000000, "time": NOW}
            if "/transactions?" in url:
                row = {"tx_hash": recent["hash"], "block_height": recent["height"], "block_time": recent["time"]}
                if "order=desc" in url:
                    return [row, ancient]
                return [ancient]
            if url.endswith("/utxos"):
                h = url.split("/txs/")[1].split("/")[0]
                if h == ancient["tx_hash"]:
                    return {"hash": h, "inputs": [], "outputs": []}
                t = recent
                def amt(u):
                    rows = [{"unit": "lovelace", "quantity": str(u["lovelace"])}]
                    rows += [{"unit": k, "quantity": str(q)} for k, q in u["assets"].items()]
                    return {"address": u["addr"], "amount": rows}
                return {"hash": h, "inputs": [amt(u) for u in t["inputs"]], "outputs": [amt(u) for u in t["outputs"]]}
            return None

        http = P.Http({"koios": 0, "blockfrost": 0})
        with mock.patch.object(P.Http, "req", req), tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump({"discovered_at": {UNIT: NOW}}, open(os.path.join(c, "liquidity-moves-cache.json"), "w"))
            summary = LM.run(http, {}, _top(), {FX["ticker"]: UNIT}, {UNIT: {"decimals": 6}}, V.lp_policies(),
                             d, c, False, NOW, bf_key="k-bf", owner_env=OWNER)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        self.assertTrue(any("order=desc" in u for s, u in calls if s == "blockfrost" and "/transactions?" in u))
        self.assertFalse(any("order=asc" in u for s, u in calls if "/transactions?" in u))
        self.assertTrue(any(r["kind"] == "swap" for r in doc["moves"]))
        self.assertNotIn(ancient["tx_hash"], cache["seen"])
        self.assertTrue(doc["coverage"][FX["ticker"]]["complete"])
        self.assertGreater(summary["blockfrost_fallbacks"], 0)

    def test_koios_cap_skips_later_markers_and_does_not_miss_unsent_txs(self):
        sundae = LM.SUNDAE_V3_LP + "000de140" + "44" * 28
        txs = [swap_tx()]
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump({"markers": {UNIT: [{"dex": "sundaeswap_v3", "marker": sundae}]},
                       "next": 2, "capped": False}, open(os.path.join(c, "liquidity-moves-cache.json"), "w"))
            summary, calls = Run()._run(txs, d, c, cfg={"liquidity_moves": {"max_calls": 2, "discover_every_h": 100000}})
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
            self.assertFalse(any(sundae[56:] in u for s, u, b, h in calls))
            self.assertFalse(doc["coverage"][FX["ticker"]]["complete"])
            self.assertEqual(cache.get("misses") or {}, {})
            self.assertNotIn(txs[0]["hash"], cache.get("misses") or {})
            summary2, _ = Run()._run(txs, d, c, cfg={"liquidity_moves": {"max_calls": 36, "discover_every_h": 100000}})
        self.assertGreaterEqual(summary2["moves"], 1)
        self.assertLessEqual(summary["koios_calls"], 2)
        asset_urls = [u for s, u, b, h in calls if "/asset_txs" in u]
        self.assertEqual(len(asset_urls), 1)

    def test_corrupt_cache_and_tip_skip_keeps_rotation(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump({"units": [], "next": "x", "height": "nope", "markers": 3, "seen": "abc", "open_pulls": {}},
                      open(os.path.join(c, "liquidity-moves-cache.json"), "w"))
            summary = Run()._run([swap_tx()], d, c)[0]
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        self.assertNotIn("error", summary)
        self.assertIsInstance(cache["units"], dict)
        self.assertIsInstance(cache["next"], int)
        self.assertIsInstance(cache["height"], dict)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump({"next": 4, "capped": False, "units": {FX["ticker"]: UNIT}},
                      open(os.path.join(c, "liquidity-moves-cache.json"), "w"))
            http = P.Http({"koios": 0})
            with mock.patch.object(P.Http, "req", lambda *a, **k: None):
                LM.run(http, {}, _top(), {FX["ticker"]: UNIT}, {}, V.lp_policies(), d, c, False, NOW,
                       owner_env=OWNER, deadline=0)
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        self.assertEqual(cache["next"], 4)

    def test_owner_given_as_an_address_is_not_tagged(self):
        user = addr("own")
        st = stake("own")
        self.assertEqual(F.addr_info(user)[0], st)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            Run()._run([add_tx(st, user)], d, c, owner=user)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertTrue(doc["moves"])
        self.assertIsNone(doc["moves"][0]["actor_tag"])
        blob = json.dumps(doc)
        self.assertNotIn(user, blob)
        self.assertNotIn(st, blob)


class FileGuard(unittest.TestCase):
    def _run(self, doc, args):
        with tempfile.TemporaryDirectory() as d:
            main = os.path.join(d, "main")
            os.makedirs(os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-guard.sh"), os.path.join(main, "scripts"))
            shutil.copy(os.path.join(ROOT, "scripts", "privacy-public-keys.txt"), os.path.join(main, "scripts"))
            shutil.copytree(os.path.join(ROOT, "solada-prefilter"), os.path.join(main, "solada-prefilter"),
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            desk = os.path.join(d, "desk-data", "solada-prefilter")
            os.makedirs(desk)
            with open(os.path.join(desk, "liquidity_moves.json"), "w") as f:
                json.dump(doc, f)
            with open(os.path.join(desk, "note.json"), "w") as f:
                json.dump({"ok": True}, f)
            data = os.path.join(d, "desk-data")
            subprocess.run(["git", "init", "-q"], cwd=data, check=True)
            subprocess.run(["git", "add", "-A"], cwd=data, check=True)
            env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
            r = subprocess.run(["bash", "main/scripts/privacy-guard.sh", "--root", "desk-data", *args],
                               cwd=d, env=env, capture_output=True)
            return r

    def test_prefix_fails_even_if_the_caller_omits_the_file(self):
        doc = {"moves": [{"actor_tag": "uynnk0mezk"}, {"actor_tag": "w-0123abcd"}]}
        r = self._run(doc, ["solada-prefilter/note.json"])
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"actor_tag is not a role label or w- hash", r.stderr)
        self.assertNotIn(b"uynnk0mezk", r.stdout + r.stderr)

    def test_role_and_hash_pass(self):
        good = {"moves": [{"actor_tag": "w-0123abcd"}, {"actor_tag": "pool_preseeder"}, {"actor_tag": None}],
                "pools": [{"pool_key": "minswap_v2~41edcb63", "reserve_ada": 1}]}
        r = self._run(good, [])
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
