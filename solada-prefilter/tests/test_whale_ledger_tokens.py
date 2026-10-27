"""Same-name whale holdings: policy labels, duplicate quantities, and key migration.

Offline. Policy ids are public asset data. These tests never put a wallet address or stake key
in a ledger row, and they check the published file does not contain one.
"""
import json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import flags as F  # noqa: E402
import whale_ledger as WL  # noqa: E402
import prefilter as P  # noqa: E402
from test_whale_ledger import STAKE, addr, fresh  # noqa: E402


def pol(prefix):
    return (prefix + "0123456789abcdef" * 8)[:56]


def unit(prefix, name="fADA"):
    return pol(prefix) + name.encode().hex()


def view_of(raw):
    led = fresh()
    led["positions_raw"] = dict(raw)
    for u in raw:
        WL.token_key(u, led["token_names"], led.setdefault("key_alias", {}))
    WL.sync_token_keys(led)
    return WL.token_view(led), led


class PolicyLabels(unittest.TestCase):
    def test_same_name_keys_follow_policy_not_arrival_order(self):
        a, b = unit("aa"), unit("daa59d")
        first_b, first_a = {}, {}
        WL.token_key(b, first_b)
        WL.token_key(a, first_b)
        WL.token_key(a, first_a)
        WL.token_key(b, first_a)
        self.assertEqual(first_b, first_a)
        self.assertEqual(first_a[a], "fADA~" + a[:6])
        self.assertEqual(first_a[b], "fADA~" + b[:6])
        self.assertNotIn("fADA", first_a.values())  # neither unit keeps the bare name

    def test_provider_order_does_not_change_the_published_labels(self):
        a, b = unit("aa"), unit("daa59d")
        forward, _ = view_of({a: 1000, b: 1000})
        backward, _ = view_of({b: 1000, a: 1000})
        self.assertEqual(forward, backward)
        policies = {t["token"]: t["policy"] for t in forward["tokens"]}
        self.assertEqual(set(policies), {"fADA~" + a[:6], "fADA~" + b[:6]})
        for u, key in ((a, "fADA~" + a[:6]), (b, "fADA~" + b[:6])):
            self.assertEqual(policies[key], u[:8])
            self.assertEqual(len(policies[key]), 8)
            self.assertTrue(key.endswith(policies[key][:6]))

    def test_unique_name_stays_bare_and_still_stores_policy(self):
        u = unit("cc", "ASCEND")
        view, led = view_of({u: 42})
        self.assertEqual(view["positions"], {"ASCEND": 42})
        self.assertEqual(view["tokens"], [{"token": "ASCEND", "policy": u[:8], "qty": 42}])
        self.assertNotIn("key_alias", led)

    def test_shared_six_hex_prefix_widens_to_eight(self):
        a, b = unit("abcdef01"), unit("abcdef02")
        names = {}
        WL.token_key(a, names)
        WL.token_key(b, names)
        self.assertEqual(names[a], "fADA~" + a[:8])
        self.assertEqual(names[b], "fADA~" + b[:8])
        self.assertNotEqual(names[a], names[b])
        self.assertEqual(len(names[a].split("~", 1)[1]), 8)


class DuplicateQty(unittest.TestCase):
    def test_identical_qty_is_marked_and_counted_once(self):
        a, b = unit("aa"), unit("daa59d")
        view, _ = view_of({a: 1000, b: 1000})
        by = {t["token"]: t for t in view["tokens"]}
        canon, copy = "fADA~" + a[:6], "fADA~" + b[:6]  # aa0123... sorts before daa59d...
        self.assertEqual(by[copy]["dup_of"], canon)
        self.assertEqual(by[canon]["same_qty_as"], [copy])
        self.assertEqual(by[copy]["same_qty_as"], [canon])
        self.assertNotIn("dup_of", by[canon])
        self.assertEqual(view["positions"], {canon: 1000, copy: 1000})  # both stay visible
        self.assertEqual(view["positions_counted"], {canon: 1000})
        self.assertEqual(sum(view["positions_counted"].values()), 1000)

    def test_different_qty_is_not_a_duplicate(self):
        a, b = unit("aa"), unit("daa59d")
        view, _ = view_of({a: 1000, b: 1001})
        self.assertTrue(all("dup_of" not in t and "same_qty_as" not in t for t in view["tokens"]))
        self.assertEqual(set(view["positions_counted"]), set(view["positions"]))
        self.assertEqual(sum(view["positions_counted"].values()), 2001)

    def test_a_third_name_is_not_folded_into_the_pair(self):
        a, b, c = unit("aa"), unit("daa59d"), unit("bb", "COCK")
        view, _ = view_of({a: 1000, b: 1000, c: 7})
        self.assertEqual(view["positions_counted"]["COCK"], 7)
        self.assertEqual(sum(q for k, q in view["positions_counted"].items() if k.startswith("fADA")), 1000)

    def test_same_change_counts_once_and_a_divergence_does_not(self):
        # This is the big-move comparison: two copycat balances moving together are one move.
        a, b = unit("aa"), unit("daa59d")
        ka, kb = "fADA~" + a[:6], "fADA~" + b[:6]
        self.assertEqual(WL.position_deltas({ka: 1000, kb: 1000}, {ka: 1800, kb: 1800}), {ka: 800})
        self.assertEqual(WL.position_deltas({ka: 1000, kb: 1000}, {ka: 1000, kb: 1800}), {kb: 800})
        self.assertEqual(WL.position_deltas({"COCK": 1, "fADA": 1}, {"COCK": 2, "fADA": 2}),
                         {"COCK": 1, "fADA": 1})

    def test_matching_drift_is_not_reported_twice(self):
        a, b = unit("aa"), unit("daa59d")
        view, _ = view_of({a: 1000, b: 1000})
        ka, kb = "fADA~" + a[:6], "fADA~" + b[:6]
        self.assertEqual(WL.omit_dup_drift({ka: 5, kb: 5}, view["tokens"]), {ka: 5})
        self.assertEqual(WL.omit_dup_drift({ka: 5, kb: 9}, view["tokens"]), {ka: 5, kb: 9})


class Migration(unittest.TestCase):
    def test_renamed_key_is_not_a_move(self):
        a, b = unit("aa"), unit("daa59d")
        ka = "fADA~" + a[:6]
        prev = {"fADA": 1000, "fADA~daa59d": 1000}
        cur = {ka: 1000, "fADA~daa59d": 1000}
        self.assertEqual(WL.position_deltas(prev, cur, {"fADA": ka}), {})
        # the original holding changed size; the copycat did not. One move, under the new key.
        self.assertEqual(WL.position_deltas(prev, {ka: 1500, "fADA~daa59d": 1000}, {"fADA": ka}), {ka: 500})

    def test_old_ledger_migrates_without_a_new_row_or_a_drift(self):
        a, b = unit("aa"), unit("daa59d")
        ka = "fADA~" + a[:6]
        prev_positions = {"fADA": 1000, "fADA~daa59d": 1000}
        rows = [
            {"time": "2026-10-26T12:00:00Z", "tx_short": "abc1234567", "kind": "swap", "token": "fADA",
             "qty": 1000, "ada_leg": 10.0, "counterparty_tag": None, "direction": "buy"},
            {"time": "2026-10-26T12:05:00Z", "tx_short": "bbb1234567", "kind": "transfer_in", "token": "fADA~daa59d",
             "qty": 1000, "ada_leg": None, "counterparty_tag": "w-1234abcd"},
        ]
        state = {"height": 50, "baseline_at": "2026-10-26T12:00:00Z", "baseline_height": 50, "lovelace": 0,
                 "positions_raw": {a: 1000, b: 1000}, "seen": {}, "orders": {
                     "abcdef0123#0": {"ref_short": "abcdef0123#0", "unit": a, "token": "fADA", "qty_raw": 1000,
                                      "ada": 2.0, "status": "open", "opened_tx": "abcdef0123",
                                      "opened_at": "2026-10-26T12:00:00Z"}},
                 "token_names": {a: "fADA", b: "fADA~daa59d"}}

        class K(WL.KoiosLedger):
            def txs_after(self, stake, height):
                return []

            def tip_height(self, stake):
                return 50

            def utxos(self, hs):
                return {}

            def balances(self, stake):
                return (0, {a: 1000, b: 1000})

            def spent(self, refs):
                return {}

        cfg = {"whale": {"stake": STAKE, "stake_prefix": STAKE[6:14]}, "whale_ledger": {}}
        with mock.patch.object(WL, "KoiosLedger", K), tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "c"))
            json.dump(state, open(os.path.join(d, "c", "whale-ledger-state.json"), "w"))
            json.dump({"positions": prev_positions, "rows": rows}, open(os.path.join(d, "whale_ledger.json"), "w"))
            r1 = WL.run(P.Http({}), cfg, d, False, 1793050000, {}, cache_dir=os.path.join(d, "c"),
                        owner_env="^zzOWNERzz$")
            doc = json.load(open(os.path.join(d, "whale_ledger.json")))
            st = json.load(open(os.path.join(d, "c", "whale-ledger-state.json")))
            r2 = WL.run(P.Http({}), cfg, d, False, 1793053600, {}, cache_dir=os.path.join(d, "c"),
                        owner_env="^zzOWNERzz$")
            doc2 = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertTrue(r1["reconcile_ok"])
        self.assertTrue(r2["reconcile_ok"])
        self.assertEqual(doc["reconcile"]["token_drift"], {})
        self.assertEqual([row["kind"] for row in doc["rows"]], ["swap", "transfer_in"])
        self.assertEqual([row["qty"] for row in doc["rows"]], [1000, 1000])
        self.assertEqual(doc["rows"][0]["token"], ka)
        self.assertEqual(doc["rows"][1]["token"], "fADA~daa59d")
        self.assertEqual(doc["key_alias"], {"fADA": ka})
        self.assertEqual(WL.position_deltas(prev_positions, doc["positions"], doc["key_alias"]), {})
        self.assertEqual(sum(doc["positions_counted"].values()), 1000)
        self.assertEqual({t["policy"] for t in doc["tokens"]}, {a[:8], b[:8]})
        self.assertEqual(doc["orders"]["abcdef0123#0"]["status"], "open")
        self.assertEqual(doc["orders"]["abcdef0123#0"]["token"], ka)
        self.assertEqual(st["positions_raw"], {a: 1000, b: 1000})
        self.assertEqual(doc2["positions"], doc["positions"])
        self.assertEqual(doc2["rows"], doc["rows"])
        self.assertEqual(WL.position_deltas(doc["positions"], doc2["positions"], doc2.get("key_alias")), {})
        blob = json.dumps(doc) + json.dumps(doc2)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(addr("whale"), blob)
        self.assertNotIn(a, blob)
        self.assertNotIn(b, blob)
        F.assert_public(doc)
        F.assert_public(doc2)


if __name__ == "__main__":
    unittest.main()
