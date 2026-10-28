"""Same-name whale holdings: policy labels, full-unit duplicates, and key migration.

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
    def test_fada_same_ticker_and_qty_different_policy_are_both_kept(self):
        # Oct 27 10:07 PM ET: fADA~daa59d was dropped because its quantity matched fADA~47c42d.
        a, b = unit("47c42d"), unit("daa59d")
        self.assertNotEqual(a[:56], b[:56])
        self.assertEqual(WL.full_unit(a)[:56], a[:56])
        self.assertNotEqual(WL.full_unit(a), WL.full_unit(b))
        view, _ = view_of({a: 1000, b: 1000})
        ka, kb = "fADA~47c42d", "fADA~daa59d"
        self.assertEqual(ka, "fADA~" + a[:6])
        self.assertEqual(kb, "fADA~" + b[:6])
        by = {t["token"]: t for t in view["tokens"]}
        self.assertEqual(set(by), {ka, kb})
        for key, u in ((ka, a), (kb, b)):
            self.assertNotIn("dup_of", by[key])
            self.assertNotIn("same_qty_as", by[key])
            self.assertEqual(by[key]["qty"], 1000)
            self.assertEqual(by[key]["policy"], u[:8])
            self.assertEqual(len(by[key]["policy"]), 8)
        self.assertNotEqual(by[ka]["policy"], by[kb]["policy"])
        self.assertEqual(view["positions"], {ka: 1000, kb: 1000})
        self.assertEqual(view["positions_counted"], {ka: 1000, kb: 1000})
        self.assertEqual(sum(view["positions_counted"].values()), 2000)
        # a matching quantity change on two policies is two moves
        self.assertEqual(WL.position_deltas({ka: 1000, kb: 1000}, {ka: 1800, kb: 1800}), {ka: 800, kb: 800})
        self.assertEqual(WL.omit_dup_drift({ka: 5, kb: 5}, view["tokens"]), {ka: 5, kb: 5})
        summary, doc = run_state(None, {a: 1000, b: 1000})
        self.assertNotIn("error", summary)
        self.assertEqual(doc["positions_counted"], {ka: 1000, kb: 1000})
        self.assertTrue(all("dup_of" not in t and "same_qty_as" not in t for t in doc["tokens"]))
        blob = json.dumps(doc)
        self.assertNotIn(a, blob)
        self.assertNotIn(b, blob)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(addr("whale"), blob)
        for row in doc["rows"]:
            cp = row.get("counterparty_tag")
            if cp is not None:
                self.assertRegex(cp, r"^w-[0-9a-f]{8}$")
        F.assert_public(doc)

    def test_same_unit_duplicate_is_counted_once(self):
        raw = unit("47c42d")
        other = raw.upper()
        self.assertNotEqual(raw, other)
        self.assertEqual(WL.full_unit(raw), WL.full_unit(other))
        self.assertNotEqual(WL.full_unit(raw), WL.full_unit(unit("daa59d")))
        view, _ = view_of({raw: 1000, other: 1000})
        counted = [t for t in view["tokens"] if "dup_of" not in t]
        copies = [t for t in view["tokens"] if "dup_of" in t]
        self.assertEqual(len(view["tokens"]), 2)
        self.assertEqual(len(counted), 1)
        self.assertEqual(len(copies), 1)
        self.assertEqual(copies[0]["dup_of"], counted[0]["token"])
        self.assertNotEqual(copies[0]["token"], counted[0]["token"])
        self.assertEqual(copies[0]["policy"], counted[0]["policy"])
        self.assertEqual(counted[0]["policy"], raw[:8])
        self.assertEqual(copies[0]["same_qty_as"], [counted[0]["token"]])
        self.assertEqual(counted[0]["same_qty_as"], [copies[0]["token"]])
        self.assertEqual(view["positions_counted"], {counted[0]["token"]: 1000})
        self.assertEqual(sum(view["positions_counted"].values()), 1000)
        self.assertEqual(sum(view["positions"].values()), 2000)  # both spellings stay visible
        canon, copy = counted[0]["token"], copies[0]["token"]
        self.assertEqual(WL.omit_dup_drift({canon: 5, copy: 5}, view["tokens"]), {canon: 5})
        self.assertEqual(WL.omit_dup_drift({canon: 5, copy: 9}, view["tokens"]), {canon: 5, copy: 9})
        summary, doc = run_state(None, {raw: 1000, other: 1000})
        self.assertNotIn("error", summary)
        self.assertEqual(sum(doc["positions_counted"].values()), 1000)
        self.assertEqual(sum(1 for t in doc["tokens"] if "dup_of" in t), 1)
        blob = json.dumps(doc)
        self.assertNotIn(raw, blob)
        self.assertNotIn(other, blob)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(addr("whale"), blob)
        for row in doc["rows"]:
            cp = row.get("counterparty_tag")
            if cp is not None:
                self.assertRegex(cp, r"^w-[0-9a-f]{8}$")
        F.assert_public(doc)

    def test_different_qty_is_not_a_duplicate(self):
        a, b = unit("47c42d"), unit("daa59d")
        view, _ = view_of({a: 1000, b: 1001})
        self.assertTrue(all("dup_of" not in t and "same_qty_as" not in t for t in view["tokens"]))
        self.assertEqual(set(view["positions_counted"]), set(view["positions"]))
        self.assertEqual(sum(view["positions_counted"].values()), 2001)

    def test_a_third_name_is_not_folded_into_the_pair(self):
        a, b, c = unit("47c42d"), unit("daa59d"), unit("bb", "COCK")
        view, _ = view_of({a: 1000, b: 1000, c: 7})
        self.assertEqual(view["positions_counted"]["COCK"], 7)
        self.assertEqual(view["positions_counted"]["fADA~47c42d"], 1000)
        self.assertEqual(view["positions_counted"]["fADA~daa59d"], 1000)
        self.assertEqual(sum(q for k, q in view["positions_counted"].items() if k.startswith("fADA")), 2000)

    def test_same_change_on_different_policies_counts_twice(self):
        a, b = unit("47c42d"), unit("daa59d")
        ka, kb = "fADA~" + a[:6], "fADA~" + b[:6]
        self.assertEqual(WL.position_deltas({ka: 1000, kb: 1000}, {ka: 1800, kb: 1800}), {ka: 800, kb: 800})
        self.assertEqual(WL.position_deltas({ka: 1000, kb: 1000}, {ka: 1000, kb: 1800}), {kb: 800})
        self.assertEqual(WL.position_deltas({"COCK": 1, "fADA": 1}, {"COCK": 2, "fADA": 2}),
                         {"COCK": 1, "fADA": 1})

    def test_matching_drift_on_different_policies_is_kept(self):
        a, b = unit("47c42d"), unit("daa59d")
        view, _ = view_of({a: 1000, b: 1000})
        ka, kb = "fADA~" + a[:6], "fADA~" + b[:6]
        self.assertEqual(WL.omit_dup_drift({ka: 5, kb: 5}, view["tokens"]), {ka: 5, kb: 5})
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
        self.assertEqual(doc["positions_counted"][ka], 1000)
        self.assertEqual(doc["positions_counted"]["fADA~daa59d"], 1000)
        self.assertEqual(sum(doc["positions_counted"].values()), 2000)
        self.assertTrue(all("dup_of" not in t for t in doc["tokens"]))
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


def _state(raw, names, alias, height=50):
    return {"height": height, "baseline_at": "2026-10-26T12:00:00Z", "baseline_height": height, "lovelace": 0,
            "positions_raw": raw, "seen": {}, "orders": {}, "token_names": names, "key_alias": alias, "rows": []}


class K(WL.KoiosLedger):
    """Offline ledger. Subclasses set `bal` to the balances() asset dict (insertion order kept)."""
    bal = {}
    tip = 50

    def txs_after(self, stake, height):
        return []

    def tip_height(self, stake):
        return self.tip

    def utxos(self, hs):
        return {}

    def balances(self, stake):
        return (0, self.bal)

    def spent(self, refs):
        return {}


def run_state(state, balances, height=50):
    cfg = {"whale": {"stake": STAKE, "stake_prefix": STAKE[6:14]}, "whale_ledger": {}}
    K.bal, K.tip = balances, height
    with mock.patch.object(WL, "KoiosLedger", K), tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "c"))
        if state is not None:
            with open(os.path.join(d, "c", "whale-ledger-state.json"), "w") as f:
                json.dump(state, f)
        summary = WL.run(P.Http({}), cfg, d, False, 1793050000, {}, cache_dir=os.path.join(d, "c"),
                         owner_env="^zzOWNERzz$")
        with open(os.path.join(d, "whale_ledger.json")) as f:
            doc = json.load(f)
    return summary, doc


class ReviewFixes(unittest.TestCase):
    def test_same_policy_unprintable_nfts_are_not_deduped(self):
        policy = pol("cd")
        a, b = policy + ("ab" * 28), policy + ("cd" * 28)
        qty = 4
        view, _ = view_of({a: qty, b: qty})
        self.assertEqual(sum(view["positions_counted"].values()), qty * 2)
        self.assertEqual(set(view["positions_counted"]), set(view["positions"]))
        self.assertEqual(len(view["tokens"]), 2)
        for t in view["tokens"]:
            self.assertNotIn("dup_of", t)
            self.assertNotIn("same_qty_as", t)
            self.assertIn("+", t["token"])
            self.assertIn(policy[:6], t["token"])
            self.assertNotIn(policy, t["token"])
            self.assertLess(len(t["token"]), 32)
            self.assertNotEqual(t["token"], a[:12])

    def test_assign_token_keys_gives_the_lower_unit_the_short_key(self):
        # Same policy, two printable spellings of one name (plain and CIP-68). The short key
        # belongs to the lexicographically lower unit. sorted() in assign_token_keys is what
        # makes that true for both input orders; without it the first unit in the call wins.
        policy = pol("ab")
        name = "NFT"
        plain = policy + name.encode().hex()
        cip = policy + "000de140" + name.encode().hex()
        low, high = sorted((plain, cip))
        self.assertLess(low, high)
        forward = WL.assign_token_keys([high, low])
        backward = WL.assign_token_keys([low, high])
        self.assertEqual(forward, backward)
        self.assertEqual(forward[low], "NFT~" + policy[:6])
        self.assertTrue(forward[high].startswith("NFT~" + policy[:6] + "+"))
        self.assertLess(len(forward[low]), len(forward[high]))
        self.assertNotIn(policy, forward[low])
        self.assertNotIn(policy, forward[high])
        view, _ = view_of({plain: 3, cip: 3})
        self.assertEqual(sum(view["positions_counted"].values()), 6)
        self.assertTrue(all("dup_of" not in t and "same_qty_as" not in t for t in view["tokens"]))
        for t in view["tokens"]:
            self.assertNotIn(policy, t["token"])

    def test_corrupt_key_alias_and_quantities_do_not_crash(self):
        a = unit("aa")
        summary, doc = run_state(_state({a: "1000"}, {a: "fADA"}, None), {a: 1000})
        self.assertTrue(summary["reconcile_ok"])
        self.assertEqual(doc["positions"], {"fADA": 1000})
        self.assertEqual(doc["positions_counted"], {"fADA": 1000})
        self.assertEqual(doc["reconcile"]["token_drift"], {})
        blob = json.dumps(doc)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(addr("whale"), blob)
        self.assertNotIn(a, blob)
        F.assert_public(doc)

        summary2, doc2 = run_state(_state({a: "nope"}, {a: "fADA"}, []), {a: 1000})
        self.assertIn("reconcile_ok", summary2)
        self.assertNotIn(a, json.dumps(doc2))
        self.assertNotIn(STAKE, json.dumps(doc2))
        F.assert_public(doc2)
        led = fresh()
        led["key_alias"] = None
        led["positions_raw"] = {a: "1000", unit("bb"): "nope"}
        WL.sync_token_keys(led)
        WL.token_view(led)
        WL.token_key(a, {}, [])
        WL.token_key(a, {}, None)

    def test_ada_named_asset_does_not_share_the_lovelace_key(self):
        u = unit("ee", "ADA")
        names, alias = {}, {}
        self.assertNotEqual(WL.token_key(u, names, alias), "ADA")
        self.assertTrue(names[u].startswith("ADA~"))
        self.assertNotIn("ADA", alias)
        led = fresh()
        led["token_names"] = {u: "ADA"}
        led["rows"] = [{"time": "2026-10-26T12:00:00Z", "tx_short": "abc1234567", "kind": "transfer_in",
                        "token": "ADA", "qty": None, "ada_leg": 2.0, "counterparty_tag": None}]
        led["orders"] = {"r#0": {"ref_short": "r#0", "unit": "lovelace", "token": "ADA", "status": "open"}}
        led["positions_raw"] = {u: 5}
        WL.sync_token_keys(led)
        self.assertEqual(led["rows"][0]["token"], "ADA")
        self.assertEqual(led["orders"]["r#0"]["token"], "ADA")
        self.assertTrue(led["token_names"][u].startswith("ADA~"))
        self.assertNotEqual(led["token_names"][u], "ADA")
        self.assertNotIn("ADA", led.get("key_alias") or {})
        view, _ = view_of({u: 9})
        self.assertNotIn("ADA", view["positions"])
        self.assertTrue(all(k.startswith("ADA~") for k in view["positions"]))

    def test_chain_only_unit_drift_is_name_tilde_policy8(self):
        held, other = unit("aa"), unit("bb")
        summary, doc = run_state(_state({held: 1000}, {held: "fADA"}, {}), {held: 1000, other: 50})
        label = "fADA~" + other[:8]
        self.assertEqual(doc["reconcile"]["token_drift"], {label: 50})
        self.assertEqual(len(label.split("~", 1)[1]), 8)
        self.assertTrue(summary["reconcile_ok"] is False)
        blob = json.dumps(doc)
        self.assertNotIn(held, blob)
        self.assertNotIn(other, blob)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(addr("whale"), blob)
        F.assert_public(doc)

    def test_baseline_seeds_token_keys_in_sorted_unit_order(self):
        low, high = unit("aa"), unit("ff")
        self.assertLess(low, high)
        seen = []
        real = WL.token_key

        def spy(u, names, alias=None):
            seen.append(u)
            return real(u, names, alias)

        assets = {}
        assets[high] = 1
        assets[low] = 1
        self.assertEqual(list(assets), [high, low])
        with mock.patch.object(WL, "token_key", spy):
            summary, doc = run_state(None, assets)
        self.assertEqual(seen, [low, high])
        self.assertNotIn("error", summary)
        blob = json.dumps(doc)
        self.assertNotIn(low, blob)
        self.assertNotIn(high, blob)
        self.assertNotIn(STAKE, blob)
        self.assertNotIn(addr("whale"), blob)
        F.assert_public(doc)


if __name__ == "__main__":
    unittest.main()
