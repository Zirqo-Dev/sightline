"""SPD-43 big-move detector (offline, no secrets, no network). Fake addresses are generated from labels."""
import hashlib, json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import big_moves as BM  # noqa: E402
import prefilter as P  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "big_moves.json")))
UNIT = "bb" * 28 + FX["unit_ticker"].encode().hex()
LP_UNIT = "f5808c2c990d86da54bfc97d89cee6efa20cd8461616359478d96b4c" + "ab" * 32
POOL = F.b32_encode("addr", b"\x71" + b"\x44" * 28)  # script payment, no stake: DEX pool


def kh(label):
    return hashlib.sha256(("bigmv/" + label).encode()).digest()[:28]


def addr(label):
    if label == "pool":
        return POOL
    if label == "enterprise":  # key payment, no stake part
        return F.b32_encode("addr", b"\x61" + kh(label))
    return F.b32_encode("addr", b"\x01" + kh(label + "/pay") + kh(label))


def owner(label):
    if label == "enterprise":
        return addr(label)
    return F.b32_encode("stake", b"\xe1" + kh(label))


def utxo(tx):
    side = lambda rows: [(addr(l), lov, {UNIT: q} if q else {}, None) for l, lov, q in rows]
    return {"inputs": side(tx["in"]), "outputs": side(tx["out"])}


def all_legs(excluded=()):
    out = []
    for tx in FX["txs"]:
        out += BM.legs_from_tx(UNIT, tx["hash"] * 8, FX["now"] - tx["age_h"] * 3600, utxo(tx), V.lp_policies(), excluded)
    return out


META = {UNIT: {"ticker": FX["unit_ticker"], "ada_side": FX["ada_side"], "decimals": FX["decimals"]}}


class Legs(unittest.TestCase):
    def test_ada_legs_summed_per_tx_and_pool_skipped(self):
        l = [x for x in all_legs() if x["tx"].startswith("aa01")]
        self.assertEqual(len(l), 1)  # pool (script, no stake) is a counterparty, not a mover
        self.assertEqual((l[0]["direction"], l[0]["lovelace"], l[0]["qty_raw"]), ("in", 4400000000, 2000000000))
        self.assertEqual(l[0]["owner"], owner("whale"))

    def test_enterprise_address_is_its_own_owner(self):
        l = [x for x in all_legs() if x["tx"].startswith("aa08")]
        self.assertEqual(l[0]["owner"], addr("enterprise"))

    def test_lp_labelled(self):
        ut = {"inputs": [(addr("lp"), 1000000000, {LP_UNIT: 5}, None)], "outputs": [(POOL, 1000000000, {LP_UNIT: 5}, None)]}
        l = BM.legs_from_tx(LP_UNIT, "cc" * 32, FX["now"], ut, V.lp_policies())
        self.assertTrue(l[0]["lp"])
        rows = BM.flag_moves(l, {LP_UNIT: {"ticker": "X", "ada_side": 1000}}, FX["now"], dict(BM.DEFAULTS, min_ada=1))
        self.assertEqual(rows[0]["ticker"], "LP")

    def test_owner_patterns_drop_owner_before_anything_else(self):
        pats = BM.owner_patterns(owner("owner")[:30] + "\n# comment\n[bad")
        self.assertFalse([x for x in all_legs(pats) if x["owner"] == owner("owner")])
        self.assertTrue([x for x in all_legs() if x["owner"] == owner("owner")])


class Flagging(unittest.TestCase):
    def rows(self, **kw):
        excl = BM.owner_patterns(owner("owner"))
        return BM.flag_moves(all_legs(excl), json.loads(json.dumps(META)), FX["now"], dict(BM.DEFAULTS, **kw))

    def test_mid_flagged_only_by_24h_sum(self):
        rows = [r for r in self.rows() if r["tag"] == BM.tag(owner("mid"))]
        self.assertEqual([r["ada"] for r in rows], [2500.0, 3000.0])
        self.assertEqual({r["window_ada"] for r in rows}, {5500.0})

    def test_thresholds_window_and_fields(self):
        rows = self.rows()
        tags = sorted({r["tag"] for r in rows})
        self.assertEqual(tags, sorted({BM.tag(owner(l)) for l in FX["expect_tags_for"]}))
        for r in rows:
            self.assertLessEqual(len(r["tag"]), 12)
            self.assertTrue({"tag", "ticker", "direction", "ada", "qty", "tx_short", "time", "next_hop"} <= set(r))
            self.assertIsNone(r["next_hop"])
            self.assertEqual(len(r["tx_short"]), 10)
        w = [r for r in rows if r["tag"] == BM.tag(owner("whale"))][0]
        self.assertEqual((w["direction"], w["ada"], w["qty"], w["pool_pct"]), ("in", 4400.0, 2000.0, 11.0))
        s = [r for r in rows if r["tag"] == BM.tag(owner("seller"))][0]
        self.assertEqual((s["direction"], s["ada"]), ("out", 6000.0))

    def test_pool_pct_rule_alone(self):
        rows = self.rows(min_ada=10**9)  # whale's 4400 ADA is 11% of the 40k ADA side
        m = [r for r in rows if r["tag"] == BM.tag(owner("mid"))]
        self.assertEqual(m and m[0]["window_ada"], 5500.0)  # 13.75% summed over 24h
        self.assertIn(BM.tag(owner("whale")), {r["tag"] for r in rows})
        self.assertNotIn(BM.tag(owner("small")), {r["tag"] for r in rows})

    def test_no_full_ids_in_output(self):
        doc = {"moves": self.rows()}
        F.assert_public(doc)
        s = json.dumps(doc)
        for l in ("whale", "mid", "seller", "enterprise", "owner"):
            self.assertNotIn(owner(l), s)
        self.assertNotIn(BM.tag(owner("owner")), s)

    def test_merge_keeps_72h_and_dedupes(self):
        rows = self.rows()
        old = dict(rows[0], time="2026-10-20T00:00:00Z", tx_short="zzzzzzzzzz")
        m = BM.merge([old] + rows, rows, FX["now"], 72)
        self.assertEqual(len(m), len(rows))


class EndToEnd(unittest.TestCase):
    def fake_req(self, calls):
        txs = {tx["hash"] * 8: tx for tx in FX["txs"]}

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            calls.append((src, url, body))
            if url.endswith("/tip"):
                return [{"block_height": 1000000, "block_time": FX["now"]}]
            if "/asset_txs" in url:
                return [(lambda h, t: {"tx_hash": h, "block_height": 1000000 - t["age_h"] * 180, "block_time": FX["now"] - t["age_h"] * 3600})(h, t)
                        for h, t in sorted(txs.items(), key=lambda x: -x[1]["age_h"])]
            if url.endswith("/tx_utxos"):
                out = []
                for h in body["_tx_hashes"]:
                    u = utxo(txs[h])
                    out.append({"tx_hash": h, **{s: [{"payment_addr": {"bech32": a}, "stake_addr": None, "value": str(l),
                                                      "asset_list": [{"policy_id": k[:56], "asset_name": k[56:], "quantity": str(q)} for k, q in m.items()]}
                                                     for a, l, m, _ in u[s]] for s in ("inputs", "outputs")}})
                return out
            return None
        return req

    def test_run_writes_doc_with_budget_and_small_batches(self):
        calls = []
        http = P.Http({"koios": 0})
        with mock.patch.object(P.Http, "req", self.fake_req(calls)), tempfile.TemporaryDirectory() as d:
            summ, doc = BM.run(http, {"big_moves": {"utxo_batch": 3}}, json.loads(json.dumps(META)), os.path.join(d, "c"), d,
                               False, FX["now"], V.lp_policies(), BM.owner_patterns(owner("owner")))
            on_disk = json.load(open(os.path.join(d, "big_moves.json")))
            self.assertTrue(os.path.exists(os.path.join(d, "c", "big-moves-cache.json")))
            # second run: cached height, no duplicate rows
            summ2, doc2 = BM.run(http, {"big_moves": {"utxo_batch": 3}}, json.loads(json.dumps(META)), os.path.join(d, "c"), d,
                                 False, FX["now"], V.lp_policies(), BM.owner_patterns(owner("owner")))
        self.assertEqual(on_disk["moves"], doc["moves"])
        self.assertEqual(len(doc2["moves"]), len(doc["moves"]))
        self.assertTrue(all(len(b["_tx_hashes"]) <= 3 for s, u, b in calls if b))
        self.assertTrue(all(len(json.dumps(b)) < 1024 for s, u, b in calls if b))
        self.assertEqual(sorted({m["tag"] for m in doc["moves"]}), sorted({BM.tag(owner(l)) for l in FX["expect_tags_for"]}))
        self.assertLessEqual(summ["koios_calls"], BM.DEFAULTS["max_calls"])

    def test_budget_cap_resumes(self):
        calls = []
        http = P.Http({"koios": 0})
        with mock.patch.object(P.Http, "req", self.fake_req(calls)), tempfile.TemporaryDirectory() as d:
            summ, _ = BM.run(http, {"big_moves": {"utxo_batch": 2, "max_calls": 3}}, json.loads(json.dumps(META)),
                             os.path.join(d, "c"), d, False, FX["now"], V.lp_policies(), ())
        self.assertLessEqual(summ["koios_calls"], 3)
        self.assertEqual(summ["capped"], [FX["unit_ticker"]])

    def test_dry_run_writes_nothing(self):
        calls = []
        http = P.Http({"koios": 0})
        with mock.patch.object(P.Http, "req", self.fake_req(calls)), tempfile.TemporaryDirectory() as d:
            BM.run(http, {}, json.loads(json.dumps(META)), os.path.join(d, "c"), d, True, FX["now"], V.lp_policies(), ())
            self.assertFalse(os.path.exists(os.path.join(d, "big_moves.json")))


if __name__ == "__main__":
    unittest.main()
