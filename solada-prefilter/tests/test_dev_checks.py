"""Phase 1 dev checks. Offline, no secrets, no network. Addresses are generated from labels."""
import contextlib, copy, hashlib, io, json, os, re, sys, tempfile, time, unittest, urllib.error
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)
import flags as F  # noqa: E402
import dev_checks as DC  # noqa: E402
import prefilter as P  # noqa: E402
from test_regressions import run_main, quiet_req, fresh_state, kill_patches  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "dev_checks.json")))
KEY = "mainnetFAKEKEY0000000000000000000"
CFG_PATH = os.path.join(SRC, "config.json")


def kh(label):
    return hashlib.sha256(("devchecks/" + label).encode()).digest()[:28]


def addr(label):
    return F.b32_encode("addr", b"\x01" + kh(label + "/p") + kh(label))


def script(label):
    return F.b32_encode("addr", b"\x71" + kh(label) + kh(label + "/s"))


def stake(label):
    return F.addr_info(addr(label))[0]


def unit(name):
    return hashlib.sha256(("unit/" + name).encode()).hexdigest()[:56] + name.encode().hex()


def hx(label):
    return hashlib.sha256(("tx/" + label).encode()).hexdigest()


def utxo(label, lov, assets, ref):
    a = script(label) if label.startswith("script:") else addr(label)
    st = None if label.startswith("script:") else stake(label)
    return {"ref": ref, "addr": a, "stake": st, "lovelace": lov, "assets": assets}


def leg(h, t, inputs, outputs):
    return {"hash": h, "height": t, "time": t, "inputs": inputs, "outputs": outputs}


DC_CFG = {"dev_checks": {"max_calls": 50, "max_bf_calls": 50, "budget_s": 30}, "flags": {"exclude_tags": {}}}
OWNER = "^zzOWNER_NOT_A_WALLET$"


def ex_reserve():
    return {F.tag(stake("reserve")): "solada_curve_reserve", "w-f46e5a31": "solada_curve_reserve"}


def dc():
    return DC.dc_cfg(DC_CFG)


class TagsAndRules(unittest.TestCase):
    def test_dev_tag_joins_watch_prefix_and_never_a_full_key(self):
        st = stake("dev")
        prefix = st[6:16]
        self.assertEqual(len(prefix), 10)
        self.assertEqual(DC.short_tag(st, {prefix: "repeat_launcher", st[6:12]: "watch"}), prefix)
        self.assertEqual(DC.short_tag(st, {st[6:26]: "too_long"}), F.tag(st))
        self.assertEqual(DC.short_tag(st, {}), F.tag(st))
        self.assertLessEqual(len(DC.short_tag(st, {})), 12)
        self.assertNotIn(st, DC.short_tag(st, {prefix: "repeat_launcher"}))
        self.assertIsNone(DC.short_tag(None, {}))

    def test_config_curve_reserve_is_the_excluded_tag(self):
        cfg = json.load(open(CFG_PATH))
        self.assertEqual(cfg["flags"]["exclude_tags"]["w-f46e5a31"], "solada_curve_reserve")
        self.assertIn("w-f46e5a31", F.excluded_tags(cfg["flags"]))
        self.assertTrue(DC._hit(stake("reserve"), addr("reserve"), ex_reserve()))

    def test_reserve_is_not_a_holder_or_a_seller(self):
        u, t0 = unit("RUG"), FX["launch_s"]
        # the reserve dumps a huge bag for ADA; the dev does nothing. That is not a dev sell.
        txs = [leg(hx("m"), t0, [], [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("m") + "#0"),
                                     utxo("reserve", 2_000_000, {u: 9_000_000}, hx("m") + "#1")]),
               leg(hx("r"), t0 + 7200,
                   [utxo("reserve", 2_000_000, {u: 9_000_000}, hx("m") + "#1")],
                   [utxo("reserve", 2_000_000 + 5_000_000_000, {}, hx("r") + "#0"),
                    utxo("script:pool", 2_000_000, {u: 9_000_000}, hx("r") + "#1")])]
        stats = DC.fold(u, txs, stake("dev"), ex_reserve(), t0, dc())
        self.assertEqual(stats["sold_ada"], 0)
        self.assertFalse(stats["late_rug"])
        # a real dev sell still ignores the reserve's balance
        txs.append(leg(hx("s"), t0 + FX["late_after_s"],
                       [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("m") + "#0"),
                        utxo("buyer", 2_000_000, {}, "b#0")],
                       [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: FX["mint_qty"] - FX["sell_qty"]}, hx("s") + "#0"),
                        utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("s") + "#1"),
                        utxo("script:pool", 2_000_000, {u: FX["sell_qty"] - FX["buyer_qty"]}, hx("s") + "#2"),
                        utxo("reserve", 2_000_000, {u: 9_000_000}, hx("s") + "#3")]))
        # buyer received tokens in the sell tx itself; reserve output is excluded
        stats = DC.fold(u, txs, stake("dev"), ex_reserve(), t0, dc())
        self.assertEqual(stats["sold_ada"], FX["sell_lovelace"] / 1e6)
        self.assertTrue(stats["late_rug"])
        self.assertEqual(stats["holders_at_sell"], 2)  # dev remainder + buyer, not the reserve or the pool

    def test_early_dump_is_a_sell_but_not_a_late_rug(self):
        u, t0 = unit("EARLY"), FX["launch_s"]
        txs = [leg(hx("m"), t0, [], [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("m") + "#0")]),
               leg(hx("b"), t0 + 60, [], [utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("b") + "#0")]),
               leg(hx("s"), t0 + FX["early_after_s"],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("m") + "#0")],
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: FX["mint_qty"] - FX["sell_qty"]}, hx("s") + "#0")])]
        stats = DC.fold(u, txs, stake("dev"), ex_reserve(), t0, dc())
        self.assertEqual(stats["sold_ada"], 2500.0)
        self.assertFalse(stats["late_rug"])
        self.assertEqual(round((stats["first_sell_s"] - t0) / 60.0, 1), 10.0)

    def test_transfer_without_ada_is_not_a_sell(self):
        u, t0 = unit("MOVE"), FX["launch_s"]
        txs = [leg(hx("m"), t0, [], [utxo("dev", 4_000_000, {u: FX["mint_qty"]}, hx("m") + "#0")]),
               leg(hx("g"), t0 + 5000,
                   [utxo("dev", 4_000_000, {u: FX["mint_qty"]}, hx("m") + "#0")],
                   [utxo("dev", 2_000_000, {}, hx("g") + "#0"),
                    utxo("friend", 2_000_000, {u: FX["mint_qty"]}, hx("g") + "#1")])]
        stats = DC.fold(u, txs, stake("dev"), set(), t0, dc())
        self.assertEqual((stats["sold_ada"], stats["late_rug"]), (0, False))

    def test_no_buyers_left_is_not_a_late_rug(self):
        u, t0 = unit("SOLO"), FX["launch_s"]
        txs = [leg(hx("m"), t0, [], [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("m") + "#0")]),
               leg(hx("s"), t0 + FX["late_after_s"],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("m") + "#0")],
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"], {}, hx("s") + "#0"),
                    utxo("script:pool", 2_000_000, {u: FX["mint_qty"]}, hx("s") + "#1")])]
        stats = DC.fold(u, txs, stake("dev"), set(), t0, dc())
        self.assertGreater(stats["sold_ada"], 0)
        self.assertFalse(stats["late_rug"])
        self.assertEqual(stats["holders_at_sell"], 0)

    def test_serial_counts_and_prior_rugs(self):
        now_ms = FX["now_s"] * 1000
        st = stake("dev")
        launches = [
            {"launcherAddress": addr("dev"), "liveAtMs": now_ms - 3_600_000},
            {"launcherAddress": addr("dev"), "liveAtMs": now_ms - 2 * 86_400_000},
            {"launcherAddress": addr("dev"), "liveAtMs": now_ms - 10 * 86_400_000},
            {"launcherAddress": addr("other"), "liveAtMs": now_ms - 1_000},
            {"ticker": "NOSTAMP", "launcherAddress": addr("dev")},
        ]
        self.assertEqual(DC.serial_counts(launches, st, now_ms, dc()), (1, 2))
        self.assertEqual(DC.serial_counts(launches, None, now_ms, dc()), (None, None))
        cache = {"units": {"old": {"stake": st, "late_rug": True, "live_at_ms": now_ms - 86_400_000},
                           "newer": {"stake": st, "late_rug": True, "live_at_ms": now_ms},
                           "other": {"stake": stake("other"), "late_rug": True, "live_at_ms": 1},
                           "clean": {"stake": st, "late_rug": False, "live_at_ms": 1}}}
        self.assertEqual(DC.prior_rug_count(cache, st, "this", now_ms - 3_600_000), 1)

    def test_risk_drawdown_inactive_and_change_gate(self):
        d = dc()
        self.assertEqual(DC.risk_of(0, True, 0, 1, 1, 0, False, True, d), "high")
        self.assertEqual(DC.risk_of(0, False, 2, 1, 1, 0, False, True, d), "high")
        self.assertEqual(DC.risk_of(25, False, 0, 1, 1, 0, False, True, d), "caution")
        self.assertEqual(DC.risk_of(0, False, 0, 2, 1, 0, False, True, d), "caution")
        self.assertEqual(DC.risk_of(0, False, 0, 1, 1, 90, False, True, d), "caution")
        self.assertEqual(DC.risk_of(0, False, 0, 1, 1, 0, True, True, d), "caution")
        self.assertEqual(DC.risk_of(0, False, 0, 1, 1, 10, False, True, d), "low")
        self.assertIsNone(DC.risk_of(0, None, 0, 1, 1, 10, False, False, d))
        self.assertEqual(DC.ath_drawdown({"ath_mcap": 1000}, 400), (1000, 60.0))
        self.assertEqual(DC.ath_drawdown({"ath_mcap": 1000}, 1200), (1200, 0.0))
        self.assertIsNone(DC.ath_drawdown({"ath_mcap": 1000}, None)[1])
        now = FX["now_s"]
        self.assertTrue(DC.inactive_of(now - 25 * 3600, True, 500, now, None, 24))
        self.assertFalse(DC.inactive_of(now - 3600, True, 0, now, None, 24))
        self.assertTrue(DC.inactive_of(None, False, 0, now, None, 24))
        self.assertIsNone(DC.inactive_of(None, False, None, now, None, 24))
        self.assertFalse(DC.sell_changed(None, 100, True, 1))
        self.assertFalse(DC.sell_changed({"dev_sold_ada": 10, "late_rug": False}, 10.4, False, 1))
        self.assertTrue(DC.sell_changed({"dev_sold_ada": 10, "late_rug": False}, 12, False, 1))
        self.assertTrue(DC.sell_changed({"dev_sold_ada": 10, "late_rug": False}, 10, True, 1))
        self.assertFalse(DC.sell_changed({"dev_sold_ada": 10, "late_rug": True}, 10, True, 1))

    def test_mint_funder_not_the_largest_recipient(self):
        u = unit("MINT")
        # script payment + stake, so dropping the script filter would pick this larger ADA input
        raw = bytes([0x11]) + kh("pool/pay") + kh("pool/stake")
        script_addr = F.b32_encode("addr", raw)
        script_stake = F.addr_info(script_addr)[0]
        self.assertTrue(F.addr_info(script_addr)[1])
        self.assertIsNotNone(script_stake)
        tx = {"inputs": [{"ref": "in#0", "addr": script_addr, "stake": script_stake, "lovelace": 50_000_000, "assets": {}},
                         utxo("dev", 3_000_000, {}, "in#1"),
                         utxo("reserve", 80_000_000, {}, "in#2")],
              "outputs": [utxo("script:pool", 2_000_000, {u: 9_000_000}, "m#0"),
                          utxo("buyer", 2_000_000, {u: 100}, "m#1")]}
        self.assertEqual(DC.creator_from_mint(u, tx, ex_reserve()), stake("dev"))
        # not a mint: the token already arrives in an input
        tx["inputs"].append(utxo("dev", 2_000_000, {u: 1}, "in#3"))
        self.assertIsNone(DC.creator_from_mint(u, tx, ex_reserve()))


def _koios_tx(tx):
    def side(rows):
        out = []
        for u in rows:
            h, idx = u["ref"].split("#")
            out.append({"tx_hash": h, "tx_index": int(idx), "payment_addr": {"bech32": u["addr"]},
                        "stake_addr": u["stake"], "value": str(u["lovelace"]),
                        "asset_list": [{"policy_id": k[:56], "asset_name": k[56:], "quantity": str(q)}
                                       for k, q in u["assets"].items()]})
        return out
    return {"tx_hash": tx["hash"], "inputs": side(tx["inputs"]), "outputs": side(tx["outputs"])}


def _bf_tx(tx):
    def side(rows, out):
        res = []
        for u in rows:
            h, idx = (u["ref"].split("#") if not out else (tx["hash"], u["ref"].split("#")[1]))
            res.append({"tx_hash": h, "output_index": int(idx), "address": u["addr"],
                        "amount": [{"unit": "lovelace", "quantity": str(u["lovelace"])}] +
                                  [{"unit": k, "quantity": str(q)} for k, q in u["assets"].items()]})
        return res
    return {"hash": tx["hash"], "inputs": side(tx["inputs"], False), "outputs": side(tx["outputs"], True)}


def world(extra_clean=False):
    """CLEAN (held) live 1h ago, RUG (late rug) live 2d ago. Same creator. Optional extra CLEAN sell."""
    now = FX["now_s"]
    clean_live = now - 3600
    rug_live = now - 2 * 86400
    cu, ru = unit("CLEAN"), unit("RUG")
    rug_sell_t = int(rug_live + FX["late_after_s"])
    books = {
        cu: [leg(hx("cm"), int(clean_live), [],
                 [utxo("dev", 2_000_000, {cu: FX["mint_qty"]}, hx("cm") + "#0"),
                  utxo("buyer", 2_000_000, {cu: FX["buyer_qty"]}, hx("cm") + "#1")])],
        ru: [leg(hx("rm"), int(rug_live), [],
                 [utxo("dev", 2_000_000, {ru: FX["mint_qty"]}, hx("rm") + "#0"),
                  utxo("buyer", 2_000_000, {ru: FX["buyer_qty"]}, hx("rm") + "#1"),
                  utxo("reserve", 2_000_000, {ru: 9_000_000}, hx("rm") + "#2")]),
             leg(hx("rs"), rug_sell_t,
                 [utxo("dev", 5_000_000, {ru: FX["mint_qty"]}, hx("rm") + "#0")],
                 [utxo("dev", 5_000_000 + FX["sell_lovelace"], {ru: FX["mint_qty"] - FX["sell_qty"]}, hx("rs") + "#0"),
                  utxo("script:pool", 2_000_000, {ru: FX["sell_qty"]}, hx("rs") + "#1")])],
    }
    if extra_clean:
        books[cu].append(leg(hx("cs"), int(now - 60),
                             [utxo("dev", 5_000_000, {cu: 10_000}, hx("cm") + "#0")],
                             [utxo("dev", 5_000_000 + 100_000_000, {}, hx("cs") + "#0"),
                              utxo("script:pool", 2_000_000, {cu: 10_000}, hx("cs") + "#1")]))
    launches = [
        {"id": "c", "ticker": "CLEAN", "policyId": cu[:56], "assetName": cu[56:],
         "launcherAddress": addr("dev"), "liveAtMs": clean_live * 1000},
        {"id": "r", "ticker": "RUG", "policyId": ru[:56], "assetName": ru[56:],
         "launcherAddress": addr("dev"), "liveAtMs": rug_live * 1000},
    ]
    tracked = [
        {"ticker": "CLEAN", "unit": cu, "launch": launches[0], "mcap_ada": 800, "vol_h24_ada": 10},
        {"ticker": "RUG", "unit": ru, "launch": launches[1], "mcap_ada": 100, "vol_h24_ada": 0},
    ]
    return cu, ru, books, launches, tracked


def fake_req(koios_up, books, seen, key=KEY):
    def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
        self_.calls[src] = self_.calls.get(src, 0) + 1
        seen.append((src, url, headers or {}))
        if src == "koios":
            if not koios_up:
                return None
            if "/asset_txs" in url:
                pol = url.split("_asset_policy=")[1].split("&")[0]
                name = url.split("_asset_name=")[1].split("&")[0]
                rows = books.get(pol + name) or []
                return [{"tx_hash": t["hash"], "block_height": t["height"], "block_time": t["time"]} for t in rows]
            if url.endswith("/tx_info"):
                want = set(body["_tx_hashes"])
                return [_koios_tx(t) for rows in books.values() for t in rows if t["hash"] in want]
            return None
        assert headers.get("project_id") == key
        if "/transactions" in url:
            uid = url.split("/assets/")[1].split("/")[0]
            rows = books.get(uid) or []
            return [{"tx_hash": t["hash"], "block_height": t["height"], "block_time": t["time"]} for t in rows]
        if "/txs/" in url and url.endswith("/utxos"):
            h = url.split("/txs/")[1].split("/")[0]
            tx = next(t for rows in books.values() for t in rows if t["hash"] == h)
            return _bf_tx(tx)
        return None
    return req


def run_dc(koios_up, books, launches, tracked, d, key=KEY, cfg=None, now_s=None, budget_s=30,
           baseline=False, owner_env=OWNER, t_start=None):
    """data/ and cache/ are siblings, matching CI (desk-data vs the Actions cache)."""
    seen = []
    data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
    os.makedirs(data, exist_ok=True)
    os.makedirs(cache, exist_ok=True)
    cfg = cfg or {"dev_checks": {"budget_s": budget_s, "max_calls": 40, "max_bf_calls": 40},
                  "flags": {"exclude_tags": ex_reserve()}}
    http = P.Http({})
    with mock.patch.object(P.Http, "req", fake_req(koios_up, books, seen, key)):
        summary, triggers = DC.run(http, cfg, tracked, launches, {stake("dev")[6:16]: "repeat_launcher"},
                                   data, cache, False, now_s or FX["now_s"], bf_key=key,
                                   baseline=baseline, owner_env=owner_env, t_start=t_start)
    path = os.path.join(data, "dev_checks.json")
    doc = None
    if os.path.exists(path):
        with open(path) as f:
            doc = json.load(f)
    return summary, triggers, doc, seen


class Run(unittest.TestCase):
    def test_baseline_then_same_state_does_not_refire_and_a_new_sell_does_once(self):
        cu, ru, books, launches, tracked = world()
        prefix = stake("dev")[6:16]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            s1, t1, doc, seen = run_dc(True, books, launches, tracked, d)
            self.assertEqual(t1, [])
            by = {e["ticker"]: e for e in doc["entries"]}
            rug, clean = by["RUG"], by["CLEAN"]
            self.assertEqual(rug["dev_tag"], prefix)
            self.assertLessEqual(len(rug["dev_tag"]), 12)
            self.assertEqual(rug["policy_short"], ru[:12])
            self.assertEqual(rug["dev_sold_ada"], 2500.0)
            self.assertEqual(rug["dev_sold_after_min"], FX["late_after_s"] / 60.0)
            self.assertEqual(rug["holders_at_sell"], 2)
            self.assertTrue(rug["late_rug"])
            self.assertFalse(rug["changed_since_last"])
            self.assertEqual(rug["risk"], "high")
            self.assertEqual((rug["serial_3h"], rug["serial_7d"], rug["prior_rugs"]), (1, 2, 0))
            self.assertTrue(rug["inactive"])
            self.assertEqual((clean["dev_sold_ada"], clean["late_rug"], clean["prior_rugs"], clean["risk"]), (0, False, 1, "high"))
            self.assertEqual((clean["serial_3h"], clean["serial_7d"]), (1, 2))
            self.assertFalse(clean["changed_since_last"])
            self.assertEqual(clean["ath_mcap"], 800)
            self.assertEqual(clean["drawdown_pct"], 0.0)
            for e in doc["entries"]:
                self.assertEqual((e["dev_buy_ada"], e["dev_locked_pct"], e["unlock_date"]), (None, None, None))
            text = json.dumps(doc)
            self.assertNotIn(stake("dev"), text)
            self.assertNotIn(addr("dev"), text)
            F.assert_public(doc)
            self.assertIn(stake("dev"), open(os.path.join(d, "cache", "dev-checks-cache.json")).read())
            self.assertTrue(doc["coverage"]["complete"] >= 1)
            self.assertEqual({s for s, _, _ in seen}, {"koios"})
            self.assertFalse(any(n.startswith("dev_checks.json.tmp-") or ".json.tmp-" in n
                                  for n in os.listdir(os.path.join(d, "data"))))
            s2, t2, doc2, _ = run_dc(True, books, launches, tracked, d)
            self.assertEqual(t2, [])
            self.assertFalse(any(e["changed_since_last"] for e in doc2["entries"]))
            self.assertEqual([e["dev_sold_ada"] for e in doc2["entries"] if e["ticker"] == "RUG"], [2500.0])
            _, _, books2, _, tracked2 = world(extra_clean=True)
            # The extra CLEAN tx sells 10k of the minted bag for 100 ADA. That is a new sell on top of the baseline.
            s3, t3, doc3, _ = run_dc(True, books2, launches, tracked2, d)
            self.assertEqual([t["kind"] for t in t3], ["dev_sell"])
            self.assertEqual(t3[0]["name"], "CLEAN")
            self.assertIn(cu, t3[0]["key"])
            self.assertFalse(t3[0]["key"].startswith("devsell:CLEAN:"))
            self.assertNotIn(hx("cs")[:16], t3[0]["key"])
            self.assertIn(hashlib.sha256(hx("cs")[:16].encode()).hexdigest()[:16], t3[0]["key"])
            self.assertTrue(t3[0]["metrics"]["changed_since_last"])
            self.assertNotIn(stake("dev"), json.dumps(t3))
            clean3 = next(e for e in doc3["entries"] if e["ticker"] == "CLEAN")
            self.assertGreaterEqual(clean3["dev_sold_ada"], 100)
            self.assertTrue(clean3["changed_since_last"])
            _, t4, doc4, _ = run_dc(True, books2, launches, tracked2, d)
            self.assertEqual(t4, [])
            self.assertFalse(next(e for e in doc4["entries"] if e["ticker"] == "CLEAN")["changed_since_last"])

    def test_drawdown_from_stored_peak(self):
        cu, ru, books, launches, tracked = world()
        tracked[0]["mcap_ada"] = 200  # CLEAN was 800
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            run_dc(True, books, launches, tracked, d)
            tracked[0]["mcap_ada"] = 200
            # seed the peak as if a previous run saw 1000
            path = os.path.join(d, "data", "dev_checks.json")
            doc = json.load(open(path))
            for e in doc["entries"]:
                if e["ticker"] == "CLEAN":
                    e["ath_mcap"] = 1000
            json.dump(doc, open(path, "w"))
            _, _, doc2, _ = run_dc(True, books, launches, tracked, d)
            clean = next(e for e in doc2["entries"] if e["ticker"] == "CLEAN")
            self.assertEqual((clean["ath_mcap"], clean["drawdown_pct"]), (1000, 80.0))

    def test_deadline_skips_and_keeps_the_previous_sell(self):
        cu, ru, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            run_dc(True, books, launches, tracked, d)
            seen = []
            cfg = {"dev_checks": {"budget_s": 0, "max_calls": 40}, "flags": {"exclude_tags": ex_reserve()}}
            http = P.Http({})
            data = os.path.join(d, "data")
            with mock.patch.object(P.Http, "req", fake_req(True, books, seen)):
                summary, triggers = DC.run(http, cfg, tracked, launches, {}, data, os.path.join(d, "cache"),
                                           False, time.time() - 10, bf_key=KEY, owner_env=OWNER)
            doc = json.load(open(os.path.join(data, "dev_checks.json")))
            rug = next(e for e in doc["entries"] if e["ticker"] == "RUG")
            self.assertEqual(triggers, [])
            self.assertEqual(rug["dev_sold_ada"], 2500.0)
            self.assertFalse(rug["changed_since_last"])
            self.assertTrue(all(e["coverage"]["skipped"] == "deadline" for e in doc["entries"]))
            self.assertEqual(seen, [])
            self.assertGreater(summary["skipped"], 0)

    def test_empty_launches_does_not_wipe_or_seed(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            summary, triggers = DC.run(P.Http({}), DC_CFG, [{"ticker": "X", "unit": unit("X"), "launch": {}}],
                                      [], {}, d, os.path.join(d, "cache"), False, FX["now_s"], bf_key="",
                                      owner_env=OWNER)
            self.assertEqual(summary["skipped"], "launches unavailable")
            self.assertEqual(triggers, [])
            self.assertFalse(os.path.exists(os.path.join(d, "dev_checks.json")))

    def test_private_cache_is_not_written_into_the_public_dir(self):
        cu, ru, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            seen = []
            http = P.Http({})
            with mock.patch.object(P.Http, "req", fake_req(True, books, seen)):
                DC.run(http, {"dev_checks": {"budget_s": 30, "max_calls": 40}, "flags": {"exclude_tags": ex_reserve()}},
                       tracked, launches, {}, d, d, False, FX["now_s"], bf_key=KEY, owner_env=OWNER)
            self.assertFalse(os.path.exists(os.path.join(d, "dev-checks-cache.json")))
            text = open(os.path.join(d, "dev_checks.json")).read()
            self.assertNotIn(stake("dev"), text)

class Fallback(unittest.TestCase):
    def _both(self, key):
        _, _, books, launches, tracked = world()
        out = {}
        for name, up in (("koios", True), ("bf", False)):
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, "cache"))
                summary, triggers, doc, seen = run_dc(up, books, launches, tracked, d, key=key)
                out[name] = (summary, doc, seen)
        return out

    def test_koios_primary_and_blockfrost_fallback_match(self):
        both = self._both(KEY)
        sk, dk, seen_k = both["koios"]
        sb, db, seen_b = both["bf"]
        self.assertEqual({s for s, _, _ in seen_k}, {"koios"})
        self.assertEqual(sk["blockfrost_fallbacks"], 0)
        self.assertIn("blockfrost", {s for s, _, _ in seen_b})
        self.assertGreater(sb["blockfrost_fallbacks"], 0)
        rk = {e["ticker"]: (e["dev_sold_ada"], e["late_rug"], e["holders_at_sell"]) for e in dk["entries"]}
        rb = {e["ticker"]: (e["dev_sold_ada"], e["late_rug"], e["holders_at_sell"]) for e in db["entries"]}
        self.assertEqual(rk, rb)
        self.assertNotIn(KEY, json.dumps(db))
        self.assertTrue(all(KEY not in u for _, u, _ in seen_b))

    def test_empty_key_reports_the_skip(self):
        _, _, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            summary, triggers, doc, seen = run_dc(False, books, launches, tracked, d, key="")
        self.assertEqual({s for s, _, _ in seen}, {"koios"})
        self.assertEqual(summary["blockfrost_fallbacks"], 0)
        self.assertFalse(doc["coverage"]["blockfrost_available"])
        self.assertTrue(all(e["coverage"]["skipped"] == "koios" for e in doc["entries"]))
        self.assertEqual(triggers, [])


class Wiring(unittest.TestCase):
    def test_research_plan_gives_whale_a_slice_inside_the_shared_deadline(self):
        shared, whale = P.research_plan({"research_budget_s": 240, "whale_ledger": {"budget_s": 90}}, 1_000, 1_000)
        self.assertEqual(shared, 1_240)
        self.assertEqual(whale, 1_090)
        shared, whale = P.research_plan({"research_budget_s": 240}, 1_000, 1_600)
        self.assertEqual(shared, 1_720)  # capped at start + 720
        self.assertEqual(whale, 1_690)
        shared, whale = P.research_plan({"whale_ledger": {"budget_s": 10_000}}, 1_000, 1_000)
        self.assertEqual(whale, shared)

    def test_whale_runs_before_big_moves(self):
        order = []

        def wl(*a, **k):
            order.append(("whale", k.get("deadline")))
            return {"ok": True}

        def bm(*a, **k):
            order.append(("big", k.get("deadline")))
            return {}, None

        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run_main(d, quiet_req, [mock.patch.object(P.WL, "run", wl), mock.patch.object(P.BM, "run", bm)], dry=False)
        self.assertEqual([o[0] for o in order], ["whale", "big"])
        self.assertLessEqual(order[0][1], order[1][1])

    def test_new_dev_sell_joins_the_webhook_once(self):
        sent = []

        def post(self, url, body, h=None):
            sent.append(body)
            return True

        n = {"i": 0}

        def dc_run(*a, **k):
            n["i"] += 1
            trig = {"key": "devsell:QUIET:10.00:0", "kind": "dev_sell", "name": "QUIET",
                    "metrics": {"dev_tag": "w-abcdef01", "dev_sold_ada": 10, "changed_since_last": True, "late_rug": False}}
            return {"entries": 1}, [trig]

        tr = {"QUIET": {"kill": {"ada_side_below": 100}, "text": "", "asset_id": "aa" * 28 + b"QUIET".hex(), "source": "test"}}
        env = {"SOLADA_WEBHOOK_URL": "https://example.invalid/h"}
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            patches = kill_patches(tr, 5000) + [mock.patch.object(P.Http, "post_webhook", post),
                                                mock.patch.object(P.DC, "run", dc_run)]
            run_main(d, quiet_req, patches, dry=False, env=env)
            run_main(d, quiet_req, patches, dry=False, env=env)
            st = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(len(sent), 1)
        kinds = [t["kind"] for t in sent[0]["metrics"]["triggers"]]
        self.assertIn("dev_sell", kinds)
        self.assertNotIn("kill_line", kinds)
        self.assertIn("devsell:QUIET:10.00:0", st["fired"])
        self.assertGreaterEqual(n["i"], 2)

    def test_dev_checks_error_does_not_block_the_webhook(self):
        sent = []
        hook = mock.patch.object(P.Http, "post_webhook", lambda self, url, body, h=None: sent.append(body) or True)
        boom = mock.patch.object(P.DC, "run", side_effect=SystemExit("dev checks failed"))
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run = run_main(d, quiet_req, kill_patches(
                {"SER": {"kill": {"ada_side_below": 5000}, "text": "", "asset_id": "aa" * 28 + b"SER".hex(), "source": "test"}},
                4000) + [boom, hook], dry=False, env={"SOLADA_WEBHOOK_URL": "https://example.invalid/h"})
            self.assertTrue(os.path.exists(os.path.join(d, "state.json")))
        self.assertEqual(len(sent), 1)
        self.assertIn("SER", {t["name"] for t in sent[0]["metrics"]["triggers"]})
        self.assertTrue(run["webhook_sent"])


def _retarget(txs, old, new):
    out = []
    for tx in txs:
        t = copy.deepcopy(tx)
        for side in ("inputs", "outputs"):
            for u in t[side]:
                u["assets"] = {(new if k == old else k): q for k, q in u["assets"].items()}
        out.append(t)
    return out


def _enterprise(label):
    return F.b32_encode("addr", bytes([0x61]) + kh(label)[:28])


class Review(unittest.TestCase):
    """Guards the peer-review fixes. Each test fails if that guard is removed."""

    def test_b1_seen_sell_hash_does_not_refire_and_a_partial_read_does_not_lower_it(self):
        cu, ru, books, launches, tracked = world()
        sell = hx("rs")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, t1, doc, _ = run_dc(True, books, launches, tracked, d)
            self.assertEqual(t1, [])
            rug = next(e for e in doc["entries"] if e["ticker"] == "RUG")
            self.assertEqual((rug["dev_sold_ada"], rug["late_rug"]), (2500.0, True))
            cache_path = os.path.join(d, "cache", "dev-checks-cache.json")
            cache = json.load(open(cache_path))
            self.assertIn(sell, cache["sells"][ru])
            self.assertNotIn(sell, json.dumps(doc))
            path = os.path.join(d, "data", "dev_checks.json")
            doc = json.load(open(path))
            for e in doc["entries"]:
                if e["ticker"] == "RUG":
                    e["dev_sold_ada"] = 0
                    e["late_rug"] = False
            json.dump(doc, open(path, "w"))
            _, t2, doc2, _ = run_dc(True, books, launches, tracked, d)
            rug2 = next(e for e in doc2["entries"] if e["ticker"] == "RUG")
            self.assertEqual(t2, [])
            self.assertFalse(rug2["changed_since_last"])
            self.assertEqual((rug2["dev_sold_ada"], rug2["late_rug"]), (2500.0, True))
            cache = json.load(open(cache_path))
            cache["rot"] = 1
            json.dump(cache, open(cache_path, "w"))
            cfg = {"dev_checks": {"budget_s": 30, "max_calls": 1, "max_bf_calls": 0, "max_launches": 8},
                   "flags": {"exclude_tags": ex_reserve()}}
            summary, t3, doc3, seen = run_dc(True, books, launches, tracked, d, key="", cfg=cfg)
            rug3 = next(e for e in doc3["entries"] if e["unit"] == ru)
            clean3 = next(e for e in doc3["entries"] if e["unit"] == cu)
            self.assertEqual(t3, [])
            self.assertLessEqual(summary["koios_calls"], 1)
            self.assertEqual(len([1 for s, _, _ in seen if s == "koios"]), summary["koios_calls"])
            self.assertTrue(rug3["coverage"]["partial"])
            self.assertIsNone(rug3["coverage"]["skipped"])
            self.assertEqual((rug3["dev_sold_ada"], rug3["late_rug"]), (2500.0, True))
            self.assertEqual(clean3["coverage"]["skipped"], "budget")
            self.assertNotIn(sell, json.dumps(doc3))

    def test_b2_a_new_token_for_the_same_ticker_is_a_baseline(self):
        _, ru, books, launches, tracked = world()
        held = [tracked[0]]
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, t1, doc, _ = run_dc(True, books, launches, tracked[:1], d)
            self.assertEqual(t1, [])
            self.assertEqual(doc["entries"][0]["dev_sold_ada"], 0)
            nu = unit("COPY")
            live = (FX["now_s"] - 2 * 86400) * 1000
            launch = {"id": "copy", "ticker": "CLEAN", "policyId": nu[:56], "assetName": nu[56:],
                      "launcherAddress": addr("dev"), "liveAtMs": live}
            tracked2 = [{"ticker": "CLEAN", "unit": nu, "launch": launch, "mcap_ada": 100, "vol_h24_ada": 0}]
            _, t2, doc2, _ = run_dc(True, {nu: _retarget(books[ru], ru, nu)}, [launch], tracked2, d)
            row = doc2["entries"][0]
            self.assertEqual(t2, [])
            self.assertEqual(row["unit"], nu)
            self.assertEqual(row["ticker"], "CLEAN")
            self.assertFalse(row["changed_since_last"])
            self.assertGreaterEqual(row["dev_sold_ada"], 25)
            self.assertTrue(all(t["key"].startswith("devsell:" + nu + ":") for t in t2))

    def test_b3a_late_rug_is_the_net_drop_not_churn(self):
        u, t0 = unit("CHURN"), FX["launch_s"]
        rem = FX["mint_qty"] - FX["sell_qty"]
        sell_ref = hx("csell") + "#0"
        pool_ref = hx("csell") + "#1"
        txs = [leg(hx("cmint"), t0, [],
                   [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("cmint") + "#0"),
                    utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("cmint") + "#1")]),
               leg(hx("csell"), t0 + FX["late_after_s"],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("cmint") + "#0")],
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: rem}, sell_ref),
                    utxo("script:pool", 2_000_000, {u: FX["sell_qty"]}, pool_ref)])]
        dumped = DC.fold(u, txs, stake("dev"), set(), t0, dc())
        self.assertTrue(dumped["late_rug"])
        self.assertGreaterEqual(dumped["sold_qty"], FX["sell_qty"])
        buy = leg(hx("bb"), t0 + FX["late_after_s"] + 120,
                  [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: rem}, sell_ref),
                   utxo("script:pool", 2_000_000, {u: FX["sell_qty"]}, pool_ref)],
                  [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("bb") + "#0")])
        back = DC.fold(u, txs + [buy], stake("dev"), set(), t0, dc())
        self.assertGreater(back["sold_ada"], 0)
        self.assertGreaterEqual(back["sold_qty"], FX["sell_qty"])
        self.assertEqual(back["net_drop_pct"], 0.0)
        self.assertFalse(back["late_rug"])

    def test_b3b_staking_withdrawals_are_not_sale_proceeds(self):
        u, t0 = unit("REWARD"), FX["launch_s"]
        reward = 5_000_000_000
        sell = leg(hx("ws"), t0 + FX["late_after_s"],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("wm") + "#0")],
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"] + reward, {u: FX["mint_qty"] - FX["sell_qty"]}, hx("ws") + "#0"),
                    utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("ws") + "#1")])
        sell["withdrawals"] = {stake("dev"): reward}
        txs = [leg(hx("wm"), t0, [], [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("wm") + "#0")]), sell]
        stats = DC.fold(u, txs, stake("dev"), set(), t0, dc())
        self.assertEqual(stats["sold_ada"], FX["sell_lovelace"] / 1e6)
        move = leg(hx("wg"), t0 + 5000,
                   [utxo("dev", 4_000_000, {u: FX["mint_qty"]}, hx("wm") + "#0")],
                   [utxo("dev", 4_000_000 + reward, {}, hx("wg") + "#0"),
                    utxo("friend", 2_000_000, {u: FX["mint_qty"]}, hx("wg") + "#1")])
        move["withdrawals"] = {stake("dev"): reward}
        moved = DC.fold(u, [txs[0], move], stake("dev"), set(), t0, dc())
        self.assertEqual(moved["sold_ada"], 0)
        posted = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            posted.append(body)
            return [{"tx_hash": hx("wd"), "inputs": [], "outputs": [],
                     "withdrawals": [{"stake_address": stake("dev"), "amount": str(reward)}]}]

        with mock.patch.object(P.Http, "req", req):
            parsed = KT_tx_info(hx("wd"))
        self.assertTrue(posted and posted[0].get("_withdrawals") is True)
        self.assertEqual(parsed["withdrawals"].get(stake("dev")), reward)

    def test_both_baseline_guards(self):
        cu, ru, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, t1, _, _ = run_dc(True, books, launches, tracked, d)
            self.assertEqual(t1, [])
            _, _, books2, _, tracked2 = world(extra_clean=True)
            _, t2, doc2, _ = run_dc(True, books2, launches, tracked2, d, baseline=True)
            self.assertEqual(t2, [])
            self.assertFalse(any(e["changed_since_last"] for e in doc2["entries"]))
            self.assertFalse(DC._may_fire(None, False))
            self.assertFalse(DC._may_fire({"unit": cu, "dev_sold_ada": 0}, True))

    def test_launcher_stake_is_checked_against_the_reserve_tag(self):
        real = F.tag

        def tagged(s):
            if s in (stake("curve"), addr("curve")):
                return "w-f46e5a31"
            return real(s)

        with mock.patch.object(F, "tag", side_effect=tagged):
            self.assertIsNone(DC._launcher_stake({"launcherAddress": addr("curve")}, {"w-f46e5a31"}))
            self.assertEqual(DC._launcher_stake({"launcherAddress": addr("dev")}, {"w-f46e5a31"}), stake("dev"))

    def test_trigger_and_doc_assert_public(self):
        row = {"ticker": "T", "unit": unit("PUB"), "dev_tag": "w-abcdef01", "changed_since_last": True,
               "dev_sold_ada": 100, "dev_sold_after_min": 40, "holders_at_sell": 2, "late_rug": False,
               "risk": stake("dev"), "_new_sell": hx("pub")}
        with self.assertRaises(SystemExit):
            DC.trigger_for(row)
        doc = {"entries": [{"ticker": stake("dev"), "dev_tag": "w-abcdef01",
                            "dev_buy_ada": None, "dev_locked_pct": None, "unlock_date": None}]}
        with self.assertRaises(SystemExit):
            DC._guard(doc)

    def test_dev_tag_longer_than_12_is_refused(self):
        row = {"ticker": "T", "unit": unit("LONG"), "dev_tag": "w-" + "a" * 11, "changed_since_last": True,
               "dev_sold_ada": 100, "dev_sold_after_min": None, "holders_at_sell": 1, "late_rug": False,
               "risk": "caution", "_new_sell": hx("long"),
               "dev_buy_ada": None, "dev_locked_pct": None, "unlock_date": None}
        with self.assertRaises(SystemExit):
            DC.trigger_for(row)
        with self.assertRaises(SystemExit):
            DC._guard({"entries": [row]})

    def test_dev_checks_429_does_not_arm_the_breaker_and_research_skips_koios(self):
        http = P.Http({"koios": 0}, backoff={"koios": [0]})
        http.dev_checks = True
        sleeps = []
        http.sleep = lambda s: sleeps.append(s)
        opens = {"n": 0}

        def boom(req, timeout=30):
            opens["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 429, "busy", {"Retry-After": "60"}, None)

        with mock.patch("urllib.request.urlopen", side_effect=boom), mock.patch("time.sleep"):
            got = http.req("koios", "https://api.koios.rest/api/v1/tip")
        self.assertIsNone(got)
        self.assertEqual(opens["n"], 1)
        self.assertEqual(sleeps, [])
        self.assertNotIn("koios", http.tripped)
        self.assertIsNone(http.deadline)
        self.assertGreater(http.koios_pause_until, time.time() + 30)

        _, _, books, launches, tracked = world()

        def urlopen(req, timeout=30):
            url = req.full_url
            if "koios.rest" in url:
                raise urllib.error.HTTPError(url, 429, "busy", {"Retry-After": "60"}, None)
            if "/transactions" in url:
                uid = url.split("/assets/")[1].split("/")[0]
                rows = books.get(uid) or []
                body = [{"tx_hash": t["hash"], "block_height": t["height"], "block_time": t["time"]} for t in rows]
            elif "/txs/" in url and url.endswith("/utxos"):
                h = url.split("/txs/")[1].split("/")[0]
                tx = next(t for rows in books.values() for t in rows if t["hash"] == h)
                body = _bf_tx(tx)
            else:
                body = {}
            return _Resp(body)

        with tempfile.TemporaryDirectory() as d:
            data, cache = os.path.join(d, "data"), os.path.join(d, "cache")
            os.makedirs(cache)
            http2 = P.Http({"koios": 0, "blockfrost": 0})
            cfg = {"dev_checks": {"budget_s": 30, "max_calls": 40, "max_bf_calls": 40},
                   "flags": {"exclude_tags": ex_reserve()}}
            with mock.patch("urllib.request.urlopen", side_effect=urlopen), mock.patch("time.sleep"):
                summary, triggers = DC.run(http2, cfg, tracked, launches, {}, data, cache, False, FX["now_s"],
                                           bf_key=KEY, owner_env=OWNER)
            doc = json.load(open(os.path.join(data, "dev_checks.json")))
            rug = next(e for e in doc["entries"] if e["ticker"] == "RUG")
            self.assertEqual(rug["dev_sold_ada"], 2500.0)
            self.assertGreater(summary["blockfrost_fallbacks"], 0)
            self.assertNotIn("koios", http2.tripped)
            self.assertIsNone(http2.deadline)
            self.assertGreater(http2.koios_pause_until, time.time())
            P.note_koios_pause(http2)
            self.assertNotIn("koios", http2.tripped)
            self.assertTrue(http2.blocked("koios"))
            http2.koios_pause_until = time.time() - 1
            self.assertFalse(http2.blocked("koios"))
            self.assertEqual(triggers, [])  # first read is a baseline

    def test_research_sees_the_pause_and_dev_checks_does_not_set_the_deadline(self):
        seen = {}

        def dc_run(http, *a, **k):
            seen["deadline"] = http.deadline
            seen["tripped_before"] = "koios" in http.tripped
            http.koios_pause_until = time.time() + 60
            return {"entries": 0}, []

        def wl_run(http, *a, **k):
            seen["whale_paused"] = http.blocked("koios")
            seen["whale_tripped"] = "koios" in http.tripped
            return {"ok": True}

        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            run_main(d, quiet_req, [mock.patch.object(P.DC, "run", dc_run), mock.patch.object(P.WL, "run", wl_run)],
                     dry=False)
        self.assertIsNone(seen["deadline"])
        self.assertFalse(seen["tripped_before"])
        self.assertTrue(seen["whale_paused"])
        self.assertFalse(seen["whale_tripped"])

    def test_max_calls_cap(self):
        _, _, books, launches, tracked = world()
        cfg = {"dev_checks": {"budget_s": 30, "max_calls": 1, "max_bf_calls": 0, "max_launches": 8},
               "flags": {"exclude_tags": ex_reserve()}}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            summary, _, doc, seen = run_dc(True, books, launches, tracked, d, key="", cfg=cfg)
        self.assertLessEqual(summary["koios_calls"], 1)
        self.assertLessEqual(len([1 for s, _, _ in seen if s == "koios"]), 1)
        self.assertGreaterEqual(summary["skipped"], 1)
        self.assertTrue(any(e["coverage"]["skipped"] for e in doc["entries"]))

    def test_rotation_does_not_always_scan_the_same_ticker(self):
        _, _, books, launches, tracked = world()
        cfg = {"dev_checks": {"budget_s": 30, "max_calls": 5, "max_bf_calls": 0, "max_launches": 1},
               "flags": {"exclude_tags": ex_reserve()}}
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, _, _, seen1 = run_dc(True, books, launches, tracked, d, key="", cfg=cfg)
            _, _, _, seen2 = run_dc(True, books, launches, tracked, d, key="", cfg=cfg)
        first = lambda seen: next(u for s, u, _ in seen if s == "koios" and "asset_txs" in u)
        self.assertNotEqual(first(seen1), first(seen2))

    def test_owner_patterns_fail_closed_and_hide_a_matching_creator(self):
        _, _, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            for bad in ("", "[[:alnum:]]"):
                summary, triggers, doc, _ = run_dc(True, books, launches, tracked, d, owner_env=bad)
                self.assertEqual(summary.get("skipped"), "owner patterns unusable")
                self.assertFalse(summary.get("wrote", True))
                self.assertEqual(triggers, [])
                self.assertIsNone(doc)
                self.assertFalse(os.path.exists(os.path.join(d, "data", "dev_checks.json")))
            summary, triggers, doc, _ = run_dc(
                True, books, launches, tracked, d, owner_env="^" + re.escape(stake("dev")) + "$")
            text = json.dumps(doc)
            cache = open(os.path.join(d, "cache", "dev-checks-cache.json")).read()
            self.assertEqual(triggers, [])
            self.assertNotIn(stake("dev"), text)
            self.assertNotIn(stake("dev"), cache)
            self.assertNotIn(addr("dev"), text)
            rug = next(e for e in doc["entries"] if e["ticker"] == "RUG")
            self.assertIsNone(rug["dev_tag"])
            self.assertIsNone(rug["dev_sold_ada"])

    def test_order_fill_counts_and_a_sharp_drop_is_not_low(self):
        u, t0 = unit("ORD"), FX["launch_s"]
        mint_ref, park = hx("om") + "#0", hx("park") + "#1"
        txs = [leg(hx("om"), t0, [],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, mint_ref),
                    utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("om") + "#1")]),
               leg(hx("park"), t0 + FX["late_after_s"],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, mint_ref)],
                   [utxo("dev", 3_000_000, {}, hx("park") + "#0"),
                    utxo("script:pool", 2_000_000, {u: FX["mint_qty"]}, park)])]
        parked = DC.fold(u, txs, stake("dev"), set(), t0, dc())
        self.assertEqual(parked["sold_ada"], 0)
        self.assertGreater(parked["pending_qty"], 0)
        self.assertTrue(parked["sharp"])
        self.assertFalse(parked["late_rug"])
        self.assertNotEqual(DC.risk_of(0, False, 0, 0, 0, 0, False, True, dc(), sharp=True), "low")
        fill = leg(hx("fill"), t0 + FX["late_after_s"] + 60,
                   [utxo("script:pool", 2_000_000, {u: FX["mint_qty"]}, park),
                    utxo("dev", 3_000_000, {}, hx("park") + "#0")],
                   [utxo("dev", 3_000_000 + FX["sell_lovelace"], {}, hx("fill") + "#0")])
        done = DC.fold(u, txs + [fill], stake("dev"), set(), t0, dc())
        self.assertEqual(done["sold_ada"], FX["sell_lovelace"] / 1e6)
        self.assertIn(hx("fill"), done["sell_hashes"])

    def test_enterprise_holder_counts_and_serial_includes_this_launch(self):
        ent = _enterprise("ent")
        self.assertEqual(F.addr_info(ent), (None, False))
        u, t0 = unit("ENT"), FX["launch_s"]
        txs = [leg(hx("em"), t0, [], [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("em") + "#0")]),
               leg(hx("es"), t0 + FX["late_after_s"],
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("em") + "#0")],
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: FX["mint_qty"] - FX["sell_qty"]}, hx("es") + "#0"),
                    {"ref": hx("es") + "#1", "addr": ent, "stake": None, "lovelace": 2_000_000,
                     "assets": {u: FX["buyer_qty"]}},
                    utxo("script:pool", 2_000_000, {u: FX["sell_qty"] - FX["buyer_qty"]}, hx("es") + "#2")])]
        stats = DC.fold(u, txs, stake("dev"), ex_reserve(), t0, dc())
        self.assertTrue(stats["late_rug"])
        self.assertEqual(stats["holders_at_sell"], 2)
        now_ms = FX["now_s"] * 1000
        mint_u = unit("MINT")
        launches = [{"policyId": mint_u[:56], "assetName": mint_u[56:], "liveAtMs": now_ms - 1000}]
        self.assertEqual(DC.serial_counts(launches, stake("dev"), now_ms, dc(), this_unit=mint_u), (1, 1))
        self.assertEqual(DC.serial_counts(launches, stake("dev"), now_ms, dc()), (0, 0))

    def test_deadline_is_capped_at_run_start_plus_600s(self):
        self.assertEqual(DC.dev_deadline(45, 10000, 10000), 10045)
        self.assertEqual(DC.dev_deadline(45, 9000, 10000), 9600)
        cu, ru, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            run_dc(True, books, launches, tracked, d)
            seen = []
            cfg = {"dev_checks": {"budget_s": 45, "max_calls": 40}, "flags": {"exclude_tags": ex_reserve()}}
            http = P.Http({})
            data = os.path.join(d, "data")
            with mock.patch.object(P.Http, "req", fake_req(True, books, seen)):
                DC.run(http, cfg, tracked, launches, {}, data, os.path.join(d, "cache"), False, FX["now_s"],
                       bf_key=KEY, owner_env=OWNER, t_start=time.time() - 1000)
            doc = json.load(open(os.path.join(data, "dev_checks.json")))
            self.assertEqual(seen, [])
            self.assertTrue(all(e["coverage"]["skipped"] == "deadline" for e in doc["entries"]))
            rug = next(e for e in doc["entries"] if e["ticker"] == "RUG")
            self.assertEqual(rug["dev_sold_ada"], 2500.0)

    def test_min_sell_floor_is_25_and_lp_pulls_stay_phase_2(self):
        cfg = json.load(open(CFG_PATH))
        self.assertGreaterEqual(cfg["dev_checks"]["min_sell_ada"], 25)
        self.assertEqual(DC.DEFAULTS["min_sell_ada"], 25.0)
        self.assertFalse(DC._worth_firing(24, False, {"late_rug": False}, dc()))
        self.assertTrue(DC._worth_firing(25, False, {"late_rug": False}, dc()))
        self.assertIn("LP pulls", DC.NOTE)
        self.assertIn("LP pulls", cfg["dev_checks"]["_note"])


class Review2(unittest.TestCase):
    """Second-review regressions. Each test fails if that fix is removed."""

    def test_lost_sell_cache_rebaselines_without_firing(self):
        _, ru, books, launches, tracked = world()
        sell = hx("rs")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, t1, _, _ = run_dc(True, books, launches, tracked, d)
            self.assertEqual(t1, [])
            cache_path = os.path.join(d, "cache", "dev-checks-cache.json")
            cache = json.load(open(cache_path))
            self.assertIn(sell, cache["sells"][ru])
            del cache["sells"][ru]
            json.dump(cache, open(cache_path, "w"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _, t2, doc2, _ = run_dc(True, books, launches, tracked, d)
            rug = next(e for e in doc2["entries"] if e["unit"] == ru)
            self.assertEqual(t2, [])
            self.assertFalse(rug["changed_since_last"])
            self.assertEqual(rug["dev_sold_ada"], 2500.0)
            self.assertIn("sell cache missing", err.getvalue())
            self.assertNotIn(sell, err.getvalue())
            cache = json.load(open(cache_path))
            self.assertIn(sell, cache["sells"][ru])
            cache["sells"][ru] = "corrupt"
            json.dump(cache, open(cache_path, "w"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                _, t3, _, _ = run_dc(True, books, launches, tracked, d)
            self.assertEqual(t3, [])
            self.assertIn("sell cache missing", err.getvalue())
            self.assertNotIn(sell, err.getvalue())

    def test_lock_is_not_a_late_rug_and_a_real_sale_still_is(self):
        u, t0 = unit("LOCK"), FX["launch_s"]
        peak = FX["mint_qty"]
        small = peak // 10
        rest = peak - small
        txs = [leg(hx("lm"), t0, [],
                   [utxo("dev", 5_000_000, {u: peak}, hx("lm") + "#0"),
                    utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("lm") + "#1")]),
               leg(hx("ls"), t0 + FX["late_after_s"],
                   [utxo("dev", 5_000_000, {u: peak}, hx("lm") + "#0")],
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: rest}, hx("ls") + "#0"),
                    utxo("buyer", 2_000_000, {u: small}, hx("ls") + "#1")]),
               leg(hx("lk"), t0 + FX["late_after_s"] + 30,
                   [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: rest}, hx("ls") + "#0")],
                   [utxo("dev", 3_000_000, {}, hx("lk") + "#0"),
                    utxo("script:vest", 2_000_000, {u: rest}, hx("lk") + "#1")])]
        locked = DC.fold(u, txs, stake("dev"), set(), t0, dc())
        self.assertGreaterEqual(locked["net_drop_pct"], 80)
        self.assertLess(locked["sold_qty"] / peak * 100.0, 80)
        self.assertFalse(locked["late_rug"])
        self.assertNotIn(hx("lk"), locked["sell_hashes"])
        dumped = DC.fold(u, txs[:2], stake("dev"), set(), t0, dc())
        # the 10% sale alone is not a rug either
        self.assertFalse(dumped["late_rug"])
        full = [leg(hx("fm"), t0, [],
                    [utxo("dev", 2_000_000, {u: peak}, hx("fm") + "#0"),
                     utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("fm") + "#1")]),
                leg(hx("fs"), t0 + FX["late_after_s"],
                    [utxo("dev", 5_000_000, {u: peak}, hx("fm") + "#0")],
                    [utxo("dev", 5_000_000 + FX["sell_lovelace"], {u: peak - FX["sell_qty"]}, hx("fs") + "#0"),
                     utxo("script:pool", 2_000_000, {u: FX["sell_qty"]}, hx("fs") + "#1")])]
        sold = DC.fold(u, full, stake("dev"), set(), t0, dc())
        self.assertTrue(sold["late_rug"])

    def test_blockfrost_withdrawals_use_their_endpoint_and_the_budget(self):
        u, t0 = unit("BFW"), FX["launch_s"]
        reward = 5_000_000_000
        sell_h, drop_h = hx("bws"), hx("bwd")
        books = {u: [
            leg(hx("bwm"), t0, [], [utxo("dev", 2_000_000, {u: FX["mint_qty"]}, hx("bwm") + "#0"),
                                    utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, hx("bwm") + "#1")]),
            # dev spends ADA here, so this tx must not trigger a withdrawals read
            leg(drop_h, t0 + 30,
                [utxo("dev", 2_000_000, {}, "keep#0")],
                [utxo("dev", 1_000_000, {}, drop_h + "#0"),
                 utxo("friend", 1_000_000, {}, drop_h + "#1")]),
            leg(sell_h, t0 + FX["late_after_s"],
                [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("bwm") + "#0")],
                [utxo("dev", 5_000_000 + FX["sell_lovelace"] + reward, {u: FX["mint_qty"] - FX["sell_qty"]}, sell_h + "#0"),
                 utxo("buyer", 2_000_000, {u: FX["buyer_qty"]}, sell_h + "#1")]),
        ]}
        launch = {"id": "b", "ticker": "BFW", "policyId": u[:56], "assetName": u[56:],
                  "launcherAddress": addr("dev"), "liveAtMs": t0 * 1000}
        tracked = [{"ticker": "BFW", "unit": u, "launch": launch, "mcap_ada": 10, "vol_h24_ada": 1}]
        calls = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append((src, url))
            if src == "koios":
                return None
            if "/transactions" in url:
                rows = books[u]
                return [{"tx_hash": t["hash"], "block_height": t["height"], "block_time": t["time"]} for t in rows]
            if url.endswith("/withdrawals"):
                h = url.split("/txs/")[1].split("/")[0]
                if h == sell_h:
                    return [{"address": stake("dev"), "amount": str(reward)}]
                return []
            if url.endswith("/utxos"):
                h = url.split("/txs/")[1].split("/")[0]
                tx = next(t for t in books[u] if t["hash"] == h)
                body = _bf_tx(tx)
                body["withdrawals"] = [{"address": stake("dev"), "amount": "1"}]
                return body
            return None

        def once(max_bf):
            calls.clear()
            with tempfile.TemporaryDirectory() as d:
                os.makedirs(os.path.join(d, "cache"))
                http = P.Http({})
                cfg = {"dev_checks": {"budget_s": 30, "max_calls": 10, "max_bf_calls": max_bf},
                       "flags": {"exclude_tags": {}}}
                with mock.patch.object(P.Http, "req", req):
                    summary, _, = DC.run(http, cfg, tracked, [launch], {}, os.path.join(d, "data"),
                                         os.path.join(d, "cache"), False, FX["now_s"], bf_key=KEY, owner_env=OWNER)
                doc = json.load(open(os.path.join(d, "data", "dev_checks.json")))
            return summary, doc["entries"][0], [url for src, url in calls if src == "blockfrost"]

        _, row, urls = once(20)
        wd = [url for url in urls if url.endswith("/withdrawals")]
        self.assertTrue(any(sell_h in url for url in wd))
        self.assertFalse(any(drop_h in url for url in wd))
        self.assertEqual(row["dev_sold_ada"], FX["sell_lovelace"] / 1e6)
        # asset list, mint utxo, mint withdrawals, drop utxo, sell utxo: the sell's withdrawals call is the next one
        summary_s, starved, urls_s = once(5)
        self.assertLessEqual(summary_s["blockfrost_calls"], 5)
        self.assertFalse(any(sell_h in url for url in urls_s if url.endswith("/withdrawals")))
        self.assertGreater(starved["dev_sold_ada"], FX["sell_lovelace"] / 1e6)

    def test_cancel_is_not_a_sell_and_the_floor_is_the_new_ada(self):
        u, t0 = unit("CAN"), FX["launch_s"]
        park = hx("cp") + "#1"
        txs = [leg(hx("cm"), t0, [], [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("cm") + "#0")]),
               leg(hx("cp"), t0 + 60,
                   [utxo("dev", 5_000_000, {u: FX["mint_qty"]}, hx("cm") + "#0")],
                   [utxo("dev", 3_000_000, {}, hx("cp") + "#0"),
                    utxo("script:pool", 2_000_000, {u: FX["mint_qty"]}, park)])]
        cancel = leg(hx("cc"), t0 + 120,
                     [utxo("script:pool", 2_000_000, {u: FX["mint_qty"]}, park),
                      utxo("dev", 3_000_000, {}, hx("cp") + "#0")],
                     [utxo("dev", 4_500_000, {u: FX["mint_qty"]}, hx("cc") + "#0")])
        stats = DC.fold(u, txs + [cancel], stake("dev"), set(), t0, dc())
        self.assertEqual(stats["sold_ada"], 0)
        self.assertEqual(stats["sold_qty"], 0)
        self.assertNotIn(hx("cc"), stats["sell_hashes"])
        part = FX["mint_qty"] // 5
        filled = FX["mint_qty"] - part
        fill = leg(hx("cf"), t0 + 120,
                   [utxo("script:pool", 2_000_000, {u: FX["mint_qty"]}, park),
                    utxo("dev", 3_000_000, {}, hx("cp") + "#0")],
                   [utxo("dev", 3_000_000 + FX["sell_lovelace"], {u: part}, hx("cf") + "#0"),
                    utxo("script:pool", 2_000_000, {u: filled}, hx("cf") + "#1")])
        done = DC.fold(u, txs + [fill], stake("dev"), set(), t0, dc())
        self.assertEqual(done["sold_qty"], filled)
        self.assertNotEqual(done["sold_qty"], FX["mint_qty"])
        self.assertEqual(done["sold_ada"], FX["sell_lovelace"] / 1e6)
        prev = {"dev_sold_ada": 100, "late_rug": False}
        self.assertFalse(DC._worth_firing(120, False, prev, dc()))
        self.assertTrue(DC._worth_firing(130, False, prev, dc()))

    def test_owner_address_pattern_hides_a_sibling_stake(self):
        sib = F.b32_encode("addr", bytes([0x01]) + kh("sib/p") + kh("dev"))
        self.assertEqual(F.addr_info(sib)[0], stake("dev"))
        self.assertNotEqual(sib, addr("dev"))
        _, _, books, launches, tracked = world()
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, _, doc, _ = run_dc(True, books, launches, tracked, d, owner_env="^" + re.escape(addr("other")) + "$")
            self.assertIsNotNone(next(e for e in doc["entries"] if e["ticker"] == "RUG")["dev_tag"])
            self.assertIn(stake("dev"), open(os.path.join(d, "cache", "dev-checks-cache.json")).read())
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            _, triggers, doc, _ = run_dc(True, books, launches, tracked, d, owner_env="^" + re.escape(sib) + "$")
            text = json.dumps(doc)
            cache = open(os.path.join(d, "cache", "dev-checks-cache.json")).read()
            self.assertEqual(triggers, [])
            self.assertNotIn(stake("dev"), text)
            self.assertNotIn(stake("dev"), cache)
            self.assertNotIn(sib, text)
            self.assertIsNone(next(e for e in doc["entries"] if e["ticker"] == "RUG")["dev_tag"])

    def test_sell_cache_keeps_the_newest_hashes(self):
        old = [f"{i:064x}" for i in range(2000)]
        newest = "f" * 64
        cache = {"sells": {"u": list(old)}}
        fresh = DC._note_sells(cache, "u", old + [newest], prev=None)
        self.assertEqual(fresh, [newest])
        self.assertEqual(len(cache["sells"]["u"]), 2000)
        self.assertEqual(cache["sells"]["u"][-1], newest)
        self.assertNotIn(old[0], cache["sells"]["u"])

    def test_prefilter_baseline_does_not_take_dev_sell_triggers(self):
        key = "devsell:UNIT:abc"

        def dc_run(*a, **k):
            return {"entries": 1}, [{"key": key, "kind": "dev_sell", "name": "QUIET",
                                    "metrics": {"dev_tag": "w-abcdef01", "dev_sold_ada": 100,
                                                "changed_since_last": True, "late_rug": False}}]

        with tempfile.TemporaryDirectory() as d:
            json.dump({"fired": {}, "kill_status": {}}, open(os.path.join(d, "state.json"), "w"))
            summary = run_main(d, quiet_req, [mock.patch.object(P.DC, "run", dc_run)], dry=False)
            st = json.load(open(os.path.join(d, "state.json")))
        self.assertNotIn(key, st.get("fired") or {})
        self.assertNotIn("dev_sell", [t.get("kind") for t in summary.get("triggers") or []])


class _Resp:
    def __init__(self, obj):
        self.status = 200
        self._raw = json.dumps(obj).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def KT_tx_info(h):
    import koios_tx as KT
    return KT.tx_info(P.Http({}), [h], 10)[h]


if __name__ == "__main__":
    unittest.main()
