"""SPD-44 founder whale ledger (offline, no secrets, no network). Fake addresses come from labels."""
import shutil, hashlib, json, os, subprocess, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import whale_ledger as WL  # noqa: E402
import prefilter as P  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "whale_ledger.json")))
UNITS = {"COCK": "cc" * 28 + b"COCK".hex(), "ASCEND": "dd" * 28 + "0014df10" + b"ASCEND".hex()}
SCRIPTS = ("order", "pool", "vault")


def kh(label):
    return hashlib.sha256(("wl/" + label).encode()).digest()[:28]


def addr(label):
    if label in SCRIPTS:
        return F.b32_encode("addr", b"\x31" + kh(label) + kh(label + "/s"))  # script payment + script stake
    return F.b32_encode("addr", b"\x01" + kh(label + "/p") + kh(label))


STAKE = F.b32_encode("stake", b"\xe1" + kh("whale"))


def full(h):
    return (h * 16)[:64]


def tx(t):
    def side(rows, out):
        res = []
        for i, (lab, lov, assets, *dt) in enumerate(rows):
            if lab.startswith("@"):  # spend of an earlier output
                h, idx = lab[1:].split("#")
                ref, lab = f"{full(h)}#{idx}", "order"
            else:
                ref = f"{full(t['hash'])}#{i}" if out else f"{full('ffff')}#{i}"
            a = addr(lab)
            res.append({"ref": ref, "addr": a, "stake": F.addr_info(a)[0] if lab not in SCRIPTS else None,
                        "lovelace": lov, "assets": {UNITS[k]: q for k, q in assets.items()}, "datum": datum(dt[0]) if dt else ""})
        return res
    return {"hash": full(t["hash"]), "time": t["time"], "inputs": side(t["in"], False), "outputs": side(t["out"], True)}


def datum(spec):
    """Fixture datum text: 'datum-refs:<hash4>#<i>+whale' -> json naming that full tx hash and the whale's stake cred."""
    body = spec.split(":", 1)[1]
    ref, who = body.split("+")
    h, i = ref.split("#")
    creds = {"whale": F.b32_decode(STAKE)[1][1:].hex()}
    return json.dumps({"fields": [{"bytes": full(h)}, {"int": int(i)}, {"bytes": creds[who]}]})


def fresh():
    return {"height": 1, "lovelace": 0, "positions_raw": {}, "seen": [], "rows": [], "orders": {}, "token_names": {}}


def replay(ledger=None, txs=None):
    led = ledger or fresh()
    for t in txs or FX["txs"]:
        WL.apply(led, tx(t), STAKE, V.lp_policies(), WL.wl_cfg())
    return led


def by_tx(led, h):
    return [r for r in led["rows"] if r["tx_short"] == full(h)[:10]]


class Orders(unittest.TestCase):
    def test_cock_order_open_then_replaced_not_filled(self):
        # review S8 / Snek (a): the real Oct 26 11:59 AM ET case. 6c16 spends the order and puts the same 1M COCK in a
        # new vault output whose datum names the old order and the whale -> replaced (still open), not a 930 ADA fill
        led = replay()
        o = by_tx(led, "c8c1")
        self.assertEqual([(r["kind"], r["token"], r["qty"], r["ada_leg"]) for r in o], [("order_open", "COCK", 1000000, 4.3)])
        f = by_tx(led, "6c16")
        self.assertEqual([(r["kind"], r["token"], r.get("order_status")) for r in f],
                         [("order_cancel", "COCK", "replaced"), ("order_open", "COCK", "open"), ("transfer_in", "ADA", None)])
        self.assertEqual((f[2]["ada_leg"], f[2]["via"]), (930.3, "script"))
        od = [x for x in led["orders"].values() if x["opened_tx"] == full("c8c1")[:10]][0]
        self.assertEqual(od["status"], "replaced")
        new = [x for x in led["orders"].values() if x.get("replaces") == od["ref_short"]][0]
        self.assertEqual((new["status"], new["qty_raw"], od["replaced_by"]), ("open", 1000000, new["ref_short"]))

    def test_cancel_returns_token(self):
        led = replay()
        self.assertEqual([r["kind"] for r in by_tx(led, "e001")], ["order_open"])
        self.assertEqual([(r["kind"], r["order_status"]) for r in by_tx(led, "e002")], [("order_cancel", "cancelled")])

    def test_ada_buy_order_filled_with_tokens(self):
        led = replay()
        self.assertEqual([(r["kind"], r["token"]) for r in by_tx(led, "e003")], [("order_open", "ADA")])
        self.assertEqual([(r["kind"], r["token"], r["qty"], r["ada_leg"]) for r in by_tx(led, "e004")],
                         [("order_fill", "ASCEND", 861130600, 252.69)])

    def test_batched_swap_through_pools_is_not_an_order(self):
        rows = by_tx(replay(), "56a7")
        self.assertEqual(sorted((r["kind"], r["token"], r["direction"], r["qty"]) for r in rows),
                         [("swap", "ASCEND", "buy", 247218407), ("swap", "COCK", "sell", 50000)])


class Transfers(unittest.TestCase):
    def test_transfers_have_counterparty_tags(self):
        led = replay()
        i = by_tx(led, "e005")[0]
        o = by_tx(led, "e006")[0]
        self.assertEqual((i["kind"], i["token"], i["ada_leg"]), ("transfer_in", "ADA", 499.0))
        self.assertEqual((o["kind"], o["token"], o["qty"]), ("transfer_out", "COCK", 10000))
        self.assertEqual(i["counterparty_tag"], o["counterparty_tag"])
        self.assertLessEqual(len(i["counterparty_tag"]), 12)

    def test_lp_token_rows(self):
        lp = "f5808c2c990d86da54bfc97d89cee6efa20cd8461616359478d96b4c" + "ab" * 32
        led = fresh()
        t = {"hash": full("aa11"), "time": 1793040600,
             "inputs": [{"ref": "x#0", "addr": addr("whale"), "stake": STAKE, "lovelace": 100000000, "assets": {UNITS["COCK"]: 500}},
                        {"ref": "x#1", "addr": addr("pool"), "stake": None, "lovelace": 10**10, "assets": {}}],
             "outputs": [{"ref": "y#0", "addr": addr("whale"), "stake": STAKE, "lovelace": 2000000, "assets": {lp: 77}},
                         {"ref": "y#1", "addr": addr("pool"), "stake": None, "lovelace": 10**10 + 98000000, "assets": {UNITS["COCK"]: 500}}]}
        rows = WL.apply(led, t, STAKE, V.lp_policies(), WL.wl_cfg())
        self.assertEqual([(r["kind"], r["token"], r["qty"]) for r in rows], [("lp_add", "LP:minswap_v2", 77)])


class Idempotent(unittest.TestCase):
    def test_same_tx_never_counted_twice(self):
        led = replay()
        pos, lov, n = dict(led["positions_raw"]), led["lovelace"], len(led["rows"])
        replay(led)  # every tx again (overlapping window / retried run)
        self.assertEqual((led["positions_raw"], led["lovelace"], len(led["rows"])), (pos, lov, n))

    def test_positions_are_net_deltas(self):
        led = replay()
        names = {v: k for k, v in led["token_names"].items()}
        # COCK: -1M (order, replaced: still out of the wallet) -50k (swap) -200k +200k (cancel) -10k (transfer) = -1,060,000 from a 0 seed
        self.assertEqual(led["positions_raw"][names["COCK"]], -1060000)
        self.assertEqual(led["positions_raw"][names["ASCEND"]], 247218407 + 861130600)

    def test_run_twice_with_overlap_and_reconcile(self):
        hashes = [(full(t["hash"]), 100 + i, t["time"]) for i, t in enumerate(FX["txs"])]
        txmap = {full(t["hash"]): tx(t) for t in FX["txs"]}
        led = replay()
        bal = (led["lovelace"] + 0, {u: q for u, q in led["positions_raw"].items()})
        calls = []

        class K(WL.KoiosLedger):
            def txs_after(self, stake, height):
                calls.append(height)
                return [h for h in hashes if h[1] > height]

            def tip_height(self, stake):
                return 99

            def utxos(self, hs):
                return {h: {k: txmap[h][k] for k in ("inputs", "outputs")} for h in hs}

            def balances(self, stake):
                return bal

            def spent(self, refs):
                return {}
        cfg = {"whale": {"stake": STAKE, "stake_prefix": STAKE[6:14]}, "whale_ledger": {}}
        with mock.patch.object(WL, "KoiosLedger", K), tempfile.TemporaryDirectory() as d:
            http = P.Http({"koios": 0})
            with mock.patch.object(K, "balances", lambda self, s: (0, {})):
                base = WL.run(http, cfg, d, False, 1793050000, V.lp_policies())  # baseline: seed, no rows
            r1 = WL.run(http, cfg, d, False, 1793050000, V.lp_policies())
            doc1 = json.load(open(os.path.join(d, "whale_ledger.json")))
            r2 = WL.run(http, cfg, d, False, 1793053600, V.lp_policies())  # overlap: height-1 is re-read
            doc2 = json.load(open(os.path.join(d, "whale_ledger.json")))
        self.assertEqual(base["rows"], 0)
        self.assertEqual(r1["rows"], r2["rows"])
        self.assertEqual(doc1["positions"], doc2["positions"])
        self.assertTrue(doc2["reconcile"]["ok"], doc2["reconcile"])
        self.assertEqual(calls[-1], doc1["height"] - 1)
        self.assertEqual(doc2["whale_tag"], WL.tag(STAKE))
        s = json.dumps(doc2)
        self.assertNotIn(STAKE, s)
        self.assertNotIn(addr("whale"), s)
        F.assert_public(doc2)

    def test_prefix_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                WL.run(P.Http({}), {"whale": {"stake": STAKE, "stake_prefix": "zzzzzzzz"}}, d, True, 0, {})


class Config(unittest.TestCase):
    def test_config_stake_matches_prefix_and_guard_allows_only_it(self):
        cfg = json.load(open(os.path.join(HERE, "..", "config.json")))
        self.assertTrue(cfg["whale"]["stake"].startswith("stake1" + cfg["whale"]["stake_prefix"]))
        self.assertEqual(cfg["whale"]["stake_prefix"], "uylu74g0")
        guard = os.path.join(ROOT, "scripts", "privacy-guard.sh")
        dummy = "^zzOWNERzz-not-a-real-pattern$"  # a loaded owner pattern (the real one is a secret)
        env = dict(os.environ, PRIVACY_GUARD_PATTERNS=dummy)
        run = lambda f, e=env: subprocess.run(["bash", guard, f], env=e, capture_output=True, cwd=ROOT).returncode  # noqa: E731
        cfgp = "solada-prefilter/config.json"
        with tempfile.TemporaryDirectory() as d:
            bad = os.path.join(d, "bad.txt")
            open(bad, "w").write(STAKE + "\n")
            other = os.path.join(d, "other.txt")  # the allowlisted key outside config.json is NOT masked
            open(other, "w").write(cfg["whale"]["stake"] + "\n")
            longer = os.path.join(d, "longer.txt")
            open(longer, "w").write(cfg["whale"]["stake"] + "qqqq\n")
            self.assertEqual(run(cfgp), 0)
            self.assertEqual(run(bad), 1)
            self.assertEqual(run(other), 1)
            self.assertEqual(run(longer), 1)
            # M4: with NO owner patterns loaded the allowlist is not applied -> config.json fails closed
            self.assertEqual(run(cfgp, dict(os.environ, PRIVACY_GUARD_PATTERNS="")), 1)
            e2 = dict(env, PRIVACY_GUARD_PATTERNS=cfg["whale"]["stake"][:20])  # an owner pattern always wins
            self.assertEqual(run(cfgp, e2), 1)

    def test_guard_rejects_malformed_allowlist_entry(self):
        src = os.path.join(ROOT, "scripts")
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "scripts"))
            shutil.copy(os.path.join(src, "privacy-guard.sh"), os.path.join(d, "scripts"))
            open(os.path.join(d, "scripts", "privacy-public-keys.txt"), "w").write("stake1u\n")  # prefix, not a key
            f = os.path.join(d, "x.txt")
            open(f, "w").write("hello\n")
            env = dict(os.environ, PRIVACY_GUARD_PATTERNS="^zzOWNERzz$")
            r = subprocess.run(["bash", os.path.join(d, "scripts", "privacy-guard.sh"), f], env=env, capture_output=True, cwd=d)
            self.assertEqual(r.returncode, 1)
            self.assertIn(b"not exactly one full stake key", r.stderr)

if __name__ == "__main__":
    unittest.main()
