"""SPD-55 liquidity moves (offline, no secrets, no network). Frozen fixture, synthetic addresses."""
import hashlib, io, json, os, shutil, subprocess, sys, tempfile, time, unittest
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


def _hx(label):
    return hashlib.sha256(("liq-party/" + label).encode()).hexdigest()


def party_pull_tx():
    """30_000 ADA out of a 100_000 ADA pool (30%), by the lp wallet."""
    user, st = addr("lp"), stake("lp")
    return {"hash": _hx("pull"), "time": NOW - 3600, "height": 999400,
            "inputs": [_row(POOL, 100_000_000_000, _assets(1_200_000, {LP: 4000})),
                       _row(user, 2_000_000, {LP: 1000}, st)],
            "outputs": [_row(POOL, 70_000_000_000, _assets(800_000, {LP: 5000})),
                        _row(user, 32_000_000_000, {}, st)]}


def party_sell_tx(label="lp", age_s=1800):
    """10_000 ADA sell. label names the key wallet that spent the token."""
    user, st = addr(label), stake(label)
    return {"hash": _hx("sell-" + label + "-" + str(age_s)), "time": NOW - age_s, "height": 999600,
            "inputs": [_row(POOL, 70_000_000_000, _assets(800_000, {LP: 5000})),
                       _row(user, 2_000_000, {UNIT: 400_000}, st)],
            "outputs": [_row(POOL, 60_000_000_000, _assets(1_200_000, {LP: 5000})),
                        _row(user, 12_000_000_000, {}, st)]}


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
        tag = "w-0123abcd"
        # 20_000 of a 40_000 pool (20_000 left) is 50% and above 2_000 ADA. The sell is 60% of that.
        pull = {"kind": "lp_remove", "time": NOW - 3600, "pool_key": "minswap_v2~41edcb63", "unit": UNIT,
                "hash": FX["pull"]["hash"], "tx_short": FX["pull"]["hash"][:10], "ada": FX["pull"]["expect_ada"],
                "reserve_ada": 20000.0, "actor_tag": tag}
        sell = {"kind": "swap", "direction": "sell", "time": NOW - 1800, "pool_key": pull["pool_key"], "unit": UNIT,
                "hash": FX["sell"]["hash"], "tx_short": FX["sell"]["hash"][:10], "ada": FX["sell"]["expect_ada"],
                "actor_tag": tag}
        waiting, flagged = LM.mark_dumps([pull, sell], [], bc, NOW)
        self.assertEqual(waiting, [])
        self.assertTrue(pull["ahead_of_dump"])
        self.assertEqual(pull["dump_tx_short"], sell["tx_short"])
        early = dict(sell, time=NOW - 7200, hash="ee" * 32, tx_short="ee" * 5)
        later = dict(pull, ahead_of_dump=False, dump_tx_short=None, dump_ada=None, sold_ada=0,
                     hash="ff" * 32, tx_short="ff" * 5)
        waiting, flagged = LM.mark_dumps([early, later], [], bc, NOW)
        self.assertFalse(later.get("ahead_of_dump"))
        self.assertEqual(flagged, [])
        dust = dict(sell, ada=10, time=NOW - 1000, hash="ab" * 32, tx_short="ab" * 5)
        quiet = dict(pull, ahead_of_dump=False, dump_tx_short=None, dump_ada=None, sold_ada=0,
                     hash="cd" * 32, tx_short="cd" * 5)
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
        # Fixture pull is 20_000 ADA of a 110_000 pool (about 18%), and the sell has no key wallet.
        self.assertFalse(pull["ahead_of_dump"])
        self.assertIsNone(pull["dump_tx_short"])
        self.assertEqual(pull["actor_tag"], F.tag(stake("lp")))
        self.assertEqual(doc["thresholds"]["ahead_of_dump"], dict(LM.AHEAD_OF_DUMP))
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
        self.assertFalse(pull["ahead_of_dump"])
        self.assertIsNone(pull["dump_tx_short"])

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
        # A missing client used to raise AttributeError in Reader.__init__ (http.calls).
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            missing = LM.run(None, {}, [], {}, {}, {}, "/tmp", None, False, NOW)
        self.assertEqual(missing, {"error": "no_http", "wrote": False})
        self.assertNotIn("AttributeError", buf.getvalue())
        http = P.Http({"koios": 0})
        with mock.patch("sys.stdout", buf), mock.patch.object(LM, "_run", side_effect=AttributeError("calls")):
            crashed = LM.run(http, {}, [], {}, {}, {}, "/tmp", None, False, NOW)
        self.assertEqual(crashed["error"], "AttributeError")
        self.assertFalse(crashed["wrote"])

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
        tag = "w-0123abcd"
        pull = {"kind": "lp_remove", "time": NOW - 10 * 3600, "pool_key": "p1", "unit": UNIT,
                "ada": 20000, "reserve_ada": 20000, "actor_tag": tag, "hash": "aa" * 32, "tx_short": "aa" * 5}
        sell = {"kind": "swap", "direction": "sell", "time": NOW - 3600, "pool_key": "p1", "unit": UNIT,
                "ada": 12000, "actor_tag": tag, "hash": "bb" * 32, "tx_short": "bb" * 5}
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
        self.assertTrue(cache["capped"])

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

    def test_bad_pull_time_is_dropped_and_does_not_raise(self):
        marker = LM.MINSWAP_V2_LP + LM.minswap_v2_lp_name(UNIT)
        cleaned = LM._cache_public({"open_pulls": [
            {"pool_key": "minswap_v2~aaaaaaaa", "time": "abc", "unit": UNIT},
            {"pool_key": "minswap_v2~bbbbbbbb", "time": "1700000000", "unit": UNIT, "token": "MIN"},
            {"pool_key": "", "time": 5},
        ]})
        self.assertEqual([p["time"] for p in cleaned["open_pulls"]], [1700000000])
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump({"open_pulls": [{"pool_key": "minswap_v2~aaaaaaaa", "time": "abc", "unit": UNIT}],
                       "height": {marker: 1}, "units": {FX["ticker"]: UNIT}, "discovered_at": {UNIT: NOW}},
                      open(os.path.join(c, "liquidity-moves-cache.json"), "w"))
            summary = Run()._run([swap_tx()], d, c)[0]
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        self.assertNotIn("error", summary)
        self.assertFalse(any(p.get("time") == "abc" for p in cache["open_pulls"]))

    def test_incomplete_newest_page_does_not_move_the_cursor(self):
        marker = LM.MINSWAP_V2_LP + LM.minswap_v2_lp_name(UNIT)
        recent = swap_tx()
        recent["height"] = 999900
        calls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append(url)
            if src == "koios":
                return None
            if url.endswith("/blocks/latest"):
                return {"height": 1000000, "time": NOW}
            if "/transactions?" in url:
                return [{"tx_hash": recent["hash"], "block_height": recent["height"], "block_time": recent["time"]}]
            if url.endswith("/utxos"):
                def amt(u):
                    rows = [{"unit": "lovelace", "quantity": str(u["lovelace"])}]
                    rows += [{"unit": k, "quantity": str(q)} for k, q in u["assets"].items()]
                    return {"address": u["addr"], "amount": rows}
                return {"hash": recent["hash"], "inputs": [amt(u) for u in recent["inputs"]],
                        "outputs": [amt(u) for u in recent["outputs"]]}
            return None

        http = P.Http({"koios": 0, "blockfrost": 0})
        cfg = {"liquidity_moves": {"max_txs_per_pool": 1, "discover_every_h": 100000}}
        with mock.patch.object(P.Http, "req", req), tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            json.dump({"height": {marker: 999500}, "discovered_at": {UNIT: NOW}, "units": {FX["ticker"]: UNIT}},
                      open(os.path.join(c, "liquidity-moves-cache.json"), "w"))
            LM.run(http, cfg, _top(), {FX["ticker"]: UNIT}, {UNIT: {"decimals": 6}}, V.lp_policies(),
                   d, c, False, NOW, bf_key="k-bf", owner_env=OWNER)
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
            self.assertEqual(cache["height"].get(marker), 999500)
            self.assertIn(recent["hash"], cache["seen"])
            LM.run(http, cfg, _top(), {FX["ticker"]: UNIT}, {UNIT: {"decimals": 6}}, V.lp_policies(),
                   d, c, False, NOW, bf_key="k-bf", owner_env=OWNER)
            cache2 = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        self.assertEqual(cache2["height"].get(marker), 999500)

    def test_merge_keeps_two_tokens_on_one_tx_and_owner_log_names_this_job(self):
        a = {"time": "2026-10-28T00:00:00Z", "tx_short": "0123456789", "pool_key": "minswap_v2~aaaaaaaa",
             "kind": "swap", "token": "MIN", "direction": "buy", "ada": 1}
        b = dict(a, token="SNEK")
        merged = LM.merge_moves([a], [b], NOW, 72)
        self.assertEqual(sorted(r["token"] for r in merged), ["MIN", "SNEK"])
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            err = __import__("io").StringIO()
            with mock.patch("sys.stderr", err):
                Run()._run([swap_tx()], d, c, owner="")
        text = err.getvalue()
        self.assertIn("liquidity_moves: skipped: owner patterns unusable", text)
        self.assertNotIn("dev_checks: skipped", text)


def _pair(pull_ada, reserve_after, sell_ada, pull_age, sell_age, pull_tag, sell_tag, sell_hash="bb"):
    pull = {"kind": "lp_remove", "time": NOW - pull_age, "pool_key": "minswap_v2~41edcb63", "unit": UNIT,
            "hash": "aa" * 32, "tx_short": "a" * 10, "ada": pull_ada, "reserve_ada": reserve_after,
            "actor_tag": pull_tag}
    sell = {"kind": "swap", "direction": "sell", "time": NOW - sell_age, "pool_key": pull["pool_key"],
            "unit": UNIT, "hash": sell_hash * 32, "tx_short": sell_hash * 5, "ada": sell_ada,
            "actor_tag": sell_tag}
    return pull, sell


class AheadOfDump(unittest.TestCase):
    """S5 Balanced floors. The four knobs live in one block."""

    def test_floors_are_one_block(self):
        self.assertEqual(LM.AHEAD_OF_DUMP, {
            "pull_reserve_pct": 20, "pull_min_ada": 2000, "sell_of_pull_pct": 25, "window_h": 6,
        })
        with open(os.path.join(os.path.dirname(HERE), "config.json")) as fh:
            raw = json.load(fh)
        block = raw["liquidity_moves"]["ahead_of_dump"]
        self.assertEqual({k: block[k] for k in LM.AHEAD_OF_DUMP}, LM.AHEAD_OF_DUMP)
        tuned = LM.lm_cfg({"liquidity_moves": {"ahead_of_dump": {"pull_min_ada": 5000}}})
        self.assertEqual(tuned["ahead_of_dump"]["pull_min_ada"], 5000)
        self.assertEqual(tuned["ahead_of_dump"]["window_h"], 6)
        self.assertEqual(tuned["ahead_of_dump"]["pull_reserve_pct"], 20)
        self.assertNotIn("dump_ada", tuned)

    def _flagged(self, pull, sell, extra=None):
        waiting, flagged = LM.mark_dumps([pull, sell] + (extra or []), [], LM.lm_cfg({}), NOW)
        return waiting, flagged, bool(pull.get("ahead_of_dump"))

    def test_pull_below_20_percent_is_not_flagged(self):
        # 3_000 of a 20_000 pool is 15%, and it clears 2_000 ADA. The sell is the whole pull.
        pull, sell = _pair(3000, 17000, 3000, 3600, 1800, "w-0123abcd", "w-0123abcd")
        _, flagged, hit = self._flagged(pull, sell)
        self.assertFalse(hit)
        self.assertEqual(flagged, [])

    def test_pull_below_2000_ada_is_not_flagged(self):
        # 1_999 ADA is about two thirds of that pool, under the ADA floor. The sell is the whole pull.
        pull, sell = _pair(1999, 1000, 1999, 3600, 1800, "w-0123abcd", "w-0123abcd")
        _, flagged, hit = self._flagged(pull, sell)
        self.assertFalse(hit)
        self.assertEqual(flagged, [])

    def test_sell_under_25_percent_is_not_flagged(self):
        # 10_000 of a 20_000 pool clears both pull floors. 2_499 ADA is 24.99% of the pull.
        pull, sell = _pair(10000, 10000, 2499, 3600, 1800, "w-0123abcd", "w-0123abcd")
        waiting, flagged, hit = self._flagged(pull, sell)
        self.assertFalse(hit)
        self.assertEqual(flagged, [])
        self.assertEqual(waiting[0]["sold_ada"], 2499)

    def test_sell_after_6h_is_not_flagged(self):
        # Same qualifying pull and a 50% sell, one second outside the 6h window.
        pull, sell = _pair(10000, 10000, 5000, 7 * 3600, 3600 - 1, "w-0123abcd", "w-0123abcd")
        self.assertEqual(sell["time"] - pull["time"], 6 * 3600 + 1)
        _, flagged, hit = self._flagged(pull, sell)
        self.assertFalse(hit)
        self.assertEqual(flagged, [])

    def test_pull_that_meets_all_four_floors_is_flagged(self):
        # Exactly 20% (2_000 of 10_000), exactly 2_000 ADA, exactly 25% sold, exactly 6h, same w- hash.
        pull, sell = _pair(2000, 8000, 500, 6 * 3600, 0, "w-0123abcd", "w-0123abcd")
        self.assertEqual(sell["time"] - pull["time"], 6 * 3600)
        waiting, flagged, hit = self._flagged(pull, sell)
        self.assertTrue(hit)
        self.assertEqual(waiting, [])
        self.assertEqual(flagged[0]["dump_tx_short"], sell["tx_short"])
        self.assertEqual(pull["dump_ada"], 500)
        # A dev/role tag matches the same way.
        role_pull, role_sell = _pair(2000, 8000, 500, 6 * 3600, 0, "repeat_launcher", "repeat_launcher", "cc")
        _, _, role_hit = self._flagged(role_pull, role_sell)
        self.assertTrue(role_hit)
        # A different tag, even for the whole pull, does not.
        other_pull, other_sell = _pair(2000, 8000, 2000, 3600, 1800, "w-0123abcd", "w-89abcdef", "dd")
        _, _, other_hit = self._flagged(other_pull, other_sell)
        self.assertFalse(other_hit)
        # Two sells from the pulling party add up. 10% then 15% crosses 25%.
        base, first = _pair(2000, 8000, 200, 3600, 3000, "w-0123abcd", "w-0123abcd", "ee")
        waiting, _, hit = self._flagged(base, first)
        self.assertFalse(hit)
        second = dict(first, ada=300, time=NOW - 1800, hash="ff" * 32, tx_short="f" * 10)
        _, flagged = LM.mark_dumps([second], waiting, LM.lm_cfg({}), NOW)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["dump_ada"], 500)
        self.assertEqual(flagged[0]["dump_tx_short"], second["tx_short"])

    def test_same_party_sell_flags_across_runs(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            Run()._run([party_pull_tx()], d, c)
            first = json.load(open(os.path.join(d, "liquidity_moves.json")))
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
            self.assertFalse(first["moves"][0]["ahead_of_dump"])
            self.assertEqual(cache["open_pulls"][0]["actor_tag"], F.tag(stake("lp")))
            self.assertNotIn(stake("lp"), json.dumps(cache))
            Run()._run([party_pull_tx(), party_sell_tx()], d, c)
            second = json.load(open(os.path.join(d, "liquidity_moves.json")))
            cache2 = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        pull = next(r for r in second["moves"] if r["kind"] == "lp_remove")
        sell = next(r for r in second["moves"] if r.get("direction") == "sell")
        self.assertTrue(pull["ahead_of_dump"])
        self.assertEqual(pull["dump_tx_short"], sell["tx_short"])
        self.assertGreaterEqual(pull["dump_ada"], 0.25 * pull["ada"])
        self.assertEqual(pull["actor_tag"], sell["actor_tag"])
        self.assertTrue(pull["actor_tag"].startswith("w-") and len(pull["actor_tag"]) == 10)
        blob = json.dumps(second) + json.dumps(cache2)
        self.assertNotIn(stake("lp"), blob)
        self.assertNotIn(addr("lp"), blob)
        F.assert_public(second)
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            Run()._run([party_pull_tx(), party_sell_tx("other")], d, c)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        stranger = next(r for r in doc["moves"] if r["kind"] == "lp_remove")
        self.assertFalse(stranger["ahead_of_dump"])
        self.assertNotEqual(stranger["actor_tag"], next(r["actor_tag"] for r in doc["moves"] if r.get("direction") == "sell"))


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


# Desk cardano_top10 from 2026-10-28 (ticker + policy prefix only). The six that were
# unresolved are ASCEND, NIGHT, STRIKE, SNEK, MIN and IAG.
DESK_TOP10 = [
    {"rank": 1, "ticker": "ASCEND", "policy_short": "eb7a93ebc3"},
    {"rank": 2, "ticker": "NIGHT", "policy_short": "0691b2fecc"},
    {"rank": 3, "ticker": "STRIKE", "policy_short": "f13ac4d66b"},
    {"rank": 4, "ticker": "SNEK", "policy_short": "279c909f34"},
    {"rank": 5, "ticker": "MIN", "policy_short": "29d222ce76"},
    {"rank": 6, "ticker": "SOLADA", "policy_short": "e7c77b2784"},
    {"rank": 7, "ticker": "IAG", "policy_short": "5d16cc1a17"},
    {"rank": 8, "ticker": "COCK", "policy_short": "49e423161e"},
    {"rank": 9, "ticker": "CLOVE", "policy_short": "c1b89ed48d"},
    {"rank": 10, "ticker": "BOLT", "policy_short": "14628c260a"},
]
# Published Minswap V2 SNEK/ADA pool (LP policy + derived asset name).
SNEK_POOL = ("f5808c2c990d86da54bfc97d89cee6efa20cd8461616359478d96b4c"
             "2ffadbb87144e875749122e0bbb9f535eeaa7f5660c6c4a91bcc4121e477f08d")


def _asset_name(unit):
    name = unit[56:]
    if name[:8] in ("0014df10", "000de140", "000643b0"):
        name = name[8:]
    return bytes.fromhex(name).decode("ascii")


class Top10Names(unittest.TestCase):
    """Every current top-10 name maps to a verified unit and its Minswap V2 pool marker. No network."""

    def test_static_map_matches_the_frozen_names(self):
        doc = json.load(open(os.path.join(os.path.dirname(HERE), "known_units.json")))
        units = LM.known_units()
        self.assertEqual(set(units), set(doc["units"]))
        for tk, row in doc["units"].items():
            unit = units[tk]
            self.assertEqual(unit, row["unit"])
            self.assertEqual(len(unit[:56]), 56)
            self.assertEqual(_asset_name(unit), row["name"])
            self.assertTrue(row["name"] == tk or row["name"].startswith(tk))
            self.assertIn(row["source"], ("cardano-token-registry", "koios-asset-info", "desk-pulse"))
        self.assertEqual(units["MIN"], FX["known_min_unit"])
        self.assertEqual(units["SNEK"][:10], "279c909f34")
        self.assertEqual(LM.MINSWAP_V2_LP + LM.minswap_v2_lp_name(units["SNEK"]), SNEK_POOL)
        self.assertTrue(units["SHARKY"].startswith("a9c3b13c"))
        self.assertTrue(units["VAMP"].startswith("3f5a1f7a3b"))
        cfg = json.load(open(os.path.join(os.path.dirname(HERE), "config.json")))
        self.assertEqual(units["COCK"], cfg["units"]["COCK"])
        self.assertEqual(units["SOLADA"], cfg["units"]["SOLADA"])

    def test_desk_top10_resolves_without_pulse(self):
        rows, skipped = LM.resolve_tokens(DESK_TOP10, {}, {}, 10)
        self.assertEqual(skipped, [])
        self.assertEqual([r["ticker"] for r in rows], [r["ticker"] for r in DESK_TOP10])
        by = {r["ticker"]: r for r in rows}
        for src in DESK_TOP10:
            row = by[src["ticker"]]
            self.assertTrue(row["unit"].startswith(src["policy_short"]))
            self.assertEqual(row["unit_source"], "static")
            self.assertEqual(len(row["unit"][:56]), 56)
            self.assertGreaterEqual(len(row["unit"]), 58)
        # A same-ticker unit from pulse that fails the prefix does not block the verified one.
        wrong = "ab" * 28 + "534e454b"
        rows, skipped = LM.resolve_tokens(
            [{"rank": 4, "ticker": "SNEK", "policy_short": "279c909f34"}], {"SNEK": wrong}, {}, 10)
        self.assertEqual(skipped, [])
        self.assertEqual(rows[0]["unit_source"], "static")
        self.assertNotEqual(rows[0]["unit"], wrong)
        # An explicit unit on the row wins. The cache wins over the static map.
        right = LM.known_units()["SNEK"]
        rows, _ = LM.resolve_tokens(
            [{"ticker": "SNEK", "policy_short": "279c909f34", "unit": right}], {"SNEK": wrong}, {}, 10)
        self.assertEqual(rows[0]["unit_source"], "row")
        rows, _ = LM.resolve_tokens(
            [{"ticker": "SNEK", "policy_short": "279c909f34"}], {}, {"SNEK": right}, 10)
        self.assertEqual(rows[0]["unit_source"], "cache")
        # A prefix the verified unit does not match is still a mismatch, not a guess.
        rows, skipped = LM.resolve_tokens([{"ticker": "SNEK", "policy_short": "ffff"}], {}, {}, 10)
        self.assertEqual(rows, [])
        self.assertEqual(skipped[0]["skipped"], "policy_mismatch")
        with mock.patch.object(LM.KT, "read_json", return_value={}):
            rows, skipped = LM.resolve_tokens([{"ticker": "SNEK", "policy_short": "279c909f34"}], {}, {}, 10)
        self.assertEqual(rows, [])
        self.assertEqual(skipped[0]["skipped"], "unresolved")

    def test_resolved_names_request_their_minswap_pool(self):
        calls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append(url)
            if url.endswith("/tip"):
                return [{"block_height": 1000, "block_time": NOW}]
            if "/asset_txs" in url:
                return []
            return None

        http = P.Http({"koios": 0})
        cfg = {"liquidity_moves": {"discover_every_h": 10 ** 9, "max_calls": 36}}
        with mock.patch.object(P.Http, "req", req), tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary = LM.run(http, cfg, DESK_TOP10, {}, {}, V.lp_policies(), d, c, False, NOW, owner_env=OWNER)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
            cache = json.load(open(os.path.join(c, "liquidity-moves-cache.json")))
        self.assertEqual(summary["tokens"], 10)
        self.assertEqual(summary["koios_calls"], 11)
        self.assertEqual(doc["skipped"], [])
        self.assertTrue(all(t["unit_source"] == "static" for t in doc["tokens"]))
        asked = [u for u in calls if "/asset_txs" in u]
        units = LM.known_units()
        for src in DESK_TOP10:
            marker = LM.MINSWAP_V2_LP + LM.minswap_v2_lp_name(units[src["ticker"]])
            self.assertTrue(any(f"_asset_policy={marker[:56]}" in u and f"_asset_name={marker[56:]}" in u
                                for u in asked), src["ticker"])
        self.assertTrue(any(f"_asset_policy={SNEK_POOL[:56]}" in u and f"_asset_name={SNEK_POOL[56:]}" in u
                            for u in asked))
        self.assertNotIn(units["SNEK"], json.dumps(doc))
        self.assertEqual(cache["units"]["SNEK"], units["SNEK"])
        self.assertNotIn("stake1", json.dumps(doc))
        self.assertNotIn("addr1", json.dumps(doc))

    def test_a_spent_reserve_still_calls_koios_for_every_resolved_name(self):
        """The hard cap can already be gone when this slice starts.

        job_deadline used to return `now`, and every resolved name was then
        coverage.skipped budget with 0 Koios calls. The slice now keeps budget_s.
        """
        calls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append((src, url))
            if src == "koios" and url.endswith("/tip"):
                return [{"block_height": 1000, "block_time": NOW}]
            if src == "koios" and "/asset_txs" in url:
                return []
            return None

        cfg = json.load(open(os.path.join(os.path.dirname(HERE), "config.json")))
        t_start = time.time() - P.HARD_CAP_S
        deadline = P.job_deadline(cfg, t_start, time.time(), "liquidity_moves")
        self.assertGreaterEqual(deadline - time.time(), cfg["liquidity_moves"]["budget_s"] - 1)
        self.assertLessEqual(deadline, t_start + P.LATE_SLICE_END_S + 0.001)
        http = P.Http({"koios": 0})
        narrow = {"liquidity_moves": {"discover_every_h": 10 ** 9, "max_calls": 36, "max_bf_calls": 12}}
        with mock.patch.object(P.Http, "req", req), tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary = LM.run(http, narrow, DESK_TOP10, {}, {}, V.lp_policies(), d, c, False, NOW,
                             deadline=deadline, owner_env=OWNER)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertEqual(summary["tokens"], 10)
        self.assertEqual(summary["koios_calls"], 11)
        self.assertLessEqual(summary["koios_calls"], 36)
        self.assertEqual(doc["skipped"], [])
        units = LM.known_units()
        asked = [u for src, u in calls if src == "koios" and "/asset_txs" in u]
        for src in DESK_TOP10:
            cov = doc["coverage"][src["ticker"]]
            self.assertNotEqual(cov.get("skipped"), "budget")
            self.assertTrue(cov.get("complete"))
            marker = LM.MINSWAP_V2_LP + LM.minswap_v2_lp_name(units[src["ticker"]])
            self.assertTrue(any(f"_asset_policy={marker[:56]}" in u and f"_asset_name={marker[56:]}" in u
                                for u in asked), src["ticker"])
        text = json.dumps(doc)
        self.assertNotIn("stake1", text)
        self.assertNotIn("addr1", text)
        self.assertNotIn(units["SNEK"], text)
        # A deadline that is already past still skips before the tip. That is the old failure.
        http2 = P.Http({"koios": 0})
        with mock.patch.object(P.Http, "req", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))), \
                tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            skipped = LM.run(http2, narrow, DESK_TOP10, {}, {}, V.lp_policies(), d, c, False, NOW,
                             deadline=time.time() - 5, owner_env=OWNER)
            missed = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertEqual(skipped["koios_calls"], 0)
        self.assertEqual(missed["skipped"], [])
        for src in DESK_TOP10:
            self.assertEqual(missed["coverage"][src["ticker"]], {"read": 0, "complete": False, "skipped": "budget"})

    def test_a_bad_tip_does_not_raise_attribute_error(self):
        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            if url.endswith("/tip"):
                return ["nope"]
            return []

        http = P.Http({"koios": 0})
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf), mock.patch.object(P.Http, "req", req), \
                tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            summary = LM.run(http, {}, DESK_TOP10, {}, {}, V.lp_policies(), d, c, False, NOW, owner_env=OWNER)
            doc = json.load(open(os.path.join(d, "liquidity_moves.json")))
        self.assertNotIn("error", summary)
        self.assertNotIn("AttributeError", buf.getvalue())
        self.assertEqual(summary["tokens"], 10)
        self.assertTrue(all(v.get("skipped") == "tip" for v in doc["coverage"].values()))


class TokenRotation(unittest.TestCase):
    """A short slice reads two of the top 10. Later runs read the ones it skipped.

    The clock advances 10s per Koios call and the deadline is 25s after the start, so the
    tip plus two marker reads fit and the other eight are skipped: budget. max_calls stays 36.
    """

    CFG = {"liquidity_moves": {"discover_every_h": 10 ** 9, "max_calls": 36, "max_bf_calls": 12}}

    def _marker(self, ticker):
        unit = LM.known_units()[ticker]
        return LM.MINSWAP_V2_LP + LM.minswap_v2_lp_name(unit)

    def _read(self, urls):
        got = []
        for src in DESK_TOP10:
            marker = self._marker(src["ticker"])
            if any(f"_asset_policy={marker[:56]}" in u and f"_asset_name={marker[56:]}" in u for u in urls):
                got.append(src["ticker"])
        return got

    def _run_slice(self, d, c, now_s, start=1_000_000.0, span=25):
        asked = []
        clock = {"t": float(start)}

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            clock["t"] += 10
            if "/asset_txs" in url:
                asked.append(url)
            if url.endswith("/tip"):
                return [{"block_height": 1000, "block_time": now_s}]
            if "/asset_txs" in url:
                return []
            return None

        http = P.Http({"koios": 0, "blockfrost": 0})
        with mock.patch.object(LM.time, "time", lambda: clock["t"]), mock.patch.object(P.Http, "req", req):
            summary = LM.run(
                http, self.CFG, DESK_TOP10, {}, {}, V.lp_policies(), d, c, False, now_s,
                deadline=start + span, owner_env=OWNER)
        return summary, asked

    def test_order_is_least_recent_then_rank(self):
        rows = [
            {"ticker": "NIGHT", "rank": 2},
            {"ticker": "ASCEND", "rank": 1},
            {"ticker": "STRIKE", "rank": 3},
            {"ticker": "ODD", "rank": True},
        ]
        self.assertEqual(
            [r["ticker"] for r in LM.order_least_recent(rows, {})],
            ["ASCEND", "NIGHT", "STRIKE", "ODD"])
        self.assertEqual(
            [r["ticker"] for r in LM.order_least_recent(rows, {"NIGHT": 10, "ASCEND": 50, "STRIKE": 50})],
            ["ODD", "NIGHT", "ASCEND", "STRIKE"])
        cleaned = LM._cache_public({
            "last_checked": {"night": "1793100000", "bad": "abc", "STRIKE": 0, "x": [], "snek": True, "MIN": 5.0},
        })
        self.assertEqual(cleaned["last_checked"], {"NIGHT": 1793100000, "MIN": 5})
        stamped = LM.stamp_checked({}, ["NIGHT"], ["NIGHT", "STRIKE"], NOW + 0.9)
        self.assertEqual(stamped, {"NIGHT": NOW})
        skipped = LM.stamp_checked({"last_checked": {"NIGHT": NOW}}, [], ["NIGHT", "STRIKE"], NOW + 7200.4)
        self.assertEqual(skipped, {"NIGHT": NOW})

    def test_short_slice_covers_the_top_10_across_runs(self):
        names = [r["ticker"] for r in DESK_TOP10]
        units = LM.known_units()
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            for i in range(5):
                now = NOW + i * 7200
                summary, asked = self._run_slice(d, c, now)
                self.assertEqual(self._read(asked), names[i * 2:i * 2 + 2], i)
                self.assertLessEqual(summary["koios_calls"], 36)
                self.assertGreater(summary["koios_calls"], 0)
                with open(os.path.join(d, "liquidity_moves.json")) as f:
                    doc = json.load(f)
                with open(os.path.join(c, "liquidity-moves-cache.json")) as f:
                    cache = json.load(f)
                self.assertEqual([t["ticker"] for t in doc["tokens"]], names)
                self.assertEqual(doc["budget"]["max_calls"], 36)
                self.assertEqual(doc["budget"]["budget_s"], 50)
                self.assertEqual(doc["thresholds"]["ahead_of_dump"], dict(LM.AHEAD_OF_DUMP))
                by = {t["ticker"]: t["last_checked"] for t in doc["tokens"]}
                for j, tk in enumerate(names):
                    if j < (i + 1) * 2:
                        stamped = NOW + (j // 2) * 7200
                        self.assertEqual(by[tk], LM.iso(stamped))
                        self.assertEqual(cache["last_checked"][tk], stamped)
                    else:
                        self.assertIsNone(by[tk])
                        self.assertNotIn(tk, cache["last_checked"])
                    if j >= i * 2 + 2:
                        self.assertEqual(doc["coverage"][tk], {"read": 0, "complete": False, "skipped": "budget"})
                    elif j >= i * 2:
                        # The second token can finish its page as the deadline passes, so complete
                        # may be false. It was still read: no budget skip, and last_checked moved.
                        self.assertNotEqual(doc["coverage"][tk].get("skipped"), "budget")
                text = json.dumps(doc)
                self.assertNotIn("stake1", text)
                self.assertNotIn("addr1", text)
                for unit in units.values():
                    self.assertNotIn(unit, text)
                F.assert_public(doc)
            summary, asked = self._run_slice(d, c, NOW + 5 * 7200)
            self.assertEqual(self._read(asked), names[:2])
            self.assertLessEqual(summary["koios_calls"], 36)
            # A deadline that is already past reads nobody and does not move the stamps.
            http = P.Http({"koios": 0})
            with mock.patch.object(P.Http, "req", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called"))):
                skipped = LM.run(
                    http, self.CFG, DESK_TOP10, {}, {}, V.lp_policies(), d, c, False, NOW + 99_999,
                    deadline=time.time() - 5, owner_env=OWNER)
            with open(os.path.join(d, "liquidity_moves.json")) as f:
                missed = json.load(f)
            with open(os.path.join(c, "liquidity-moves-cache.json")) as f:
                cache = json.load(f)
        self.assertEqual(skipped["koios_calls"], 0)
        self.assertEqual(missed["tokens"][0]["last_checked"], LM.iso(NOW + 5 * 7200))
        self.assertEqual(cache["last_checked"]["ASCEND"], NOW + 5 * 7200)
        self.assertEqual(cache["last_checked"]["STRIKE"], NOW + 7200)
        for src in DESK_TOP10:
            self.assertEqual(missed["coverage"][src["ticker"]], {"read": 0, "complete": False, "skipped": "budget"})
        self.assertNotIn("stake1", json.dumps(missed))
        self.assertNotIn("addr1", json.dumps(missed))

    def test_legacy_cursor_does_not_override_last_checked(self):
        """next used to rotate by one. A stored index must not jump ahead of never-checked tokens."""
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as c:
            with open(os.path.join(c, "liquidity-moves-cache.json"), "w") as f:
                json.dump({"last_checked": {"ASCEND": NOW, "NIGHT": NOW}, "next": 5, "capped": True}, f)
            summary, asked = self._run_slice(d, c, NOW + 7200)
            with open(os.path.join(d, "liquidity_moves.json")) as f:
                doc = json.load(f)
            with open(os.path.join(c, "liquidity-moves-cache.json")) as f:
                cache = json.load(f)
        self.assertEqual(self._read(asked), ["STRIKE", "SNEK"])
        self.assertLessEqual(summary["koios_calls"], 36)
        by = {t["ticker"]: t["last_checked"] for t in doc["tokens"]}
        self.assertEqual(by["ASCEND"], LM.iso(NOW))
        self.assertEqual(by["STRIKE"], LM.iso(NOW + 7200))
        self.assertEqual(by["SNEK"], LM.iso(NOW + 7200))
        self.assertIsNone(by["MIN"])
        self.assertEqual(cache["last_checked"]["ASCEND"], NOW)
        self.assertEqual(cache["last_checked"]["STRIKE"], NOW + 7200)
        self.assertNotIn(LM.known_units()["SNEK"], json.dumps(doc))
        self.assertNotIn("stake1", json.dumps(doc))


if __name__ == "__main__":
    unittest.main()
