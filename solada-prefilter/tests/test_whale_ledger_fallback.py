"""SPD-44 Blockfrost fallback: Koios stays primary; Blockfrost only when Koios fails and the key is set (offline mocks)."""
import contextlib, io, json, os, sys, tempfile, unittest
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import valuation as V  # noqa: E402
import whale_ledger as WL  # noqa: E402
import prefilter as P  # noqa: E402
from test_whale_ledger import FX, STAKE, tx, full  # noqa: E402

KEY = "mainnetFAKEKEY0000000000000000000"
TXS = {full(t["hash"]): (tx(t), 100 + i) for i, t in enumerate(FX["txs"])}
CFG = {"whale": {"stake": STAKE, "stake_prefix": STAKE[6:14]}, "whale_ledger": {}}


def koios_shape(t):
    return {"tx_hash": t["hash"], **{s: [{"tx_hash": u["ref"].split("#")[0], "tx_index": int(u["ref"].split("#")[1]),
                                          "payment_addr": {"bech32": u["addr"]}, "stake_addr": u["stake"], "value": str(u["lovelace"]),
                                          "asset_list": [{"policy_id": k[:56], "asset_name": k[56:], "quantity": str(q)} for k, q in u["assets"].items()]}
                                         for u in t[s]] for s in ("inputs", "outputs")}}


def bf_shape(t):
    return {"hash": t["hash"], **{s: [{"tx_hash": u["ref"].split("#")[0], "output_index": int(u["ref"].split("#")[1]), "address": u["addr"],
                                       "amount": [{"unit": "lovelace", "quantity": str(u["lovelace"])}] +
                                                 [{"unit": k, "quantity": str(q)} for k, q in u["assets"].items()],
                                       "consumed_by_tx": None} for u in t[s]] for s in ("inputs", "outputs")}}


def fake(koios_up, seen):
    def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
        self_.calls[src] = self_.calls.get(src, 0) + 1
        seen.append((src, url, headers or {}))
        if src == "koios":
            if not koios_up:
                return None  # failure / timeout after Http's own retry
            if "/account_txs" in url and "desc" in url:
                return [{"tx_hash": "x", "block_height": 99, "block_time": 0}]
            if "/account_txs" in url:
                after = int(url.split("_after_block_height=")[1].split("&")[0])
                return [{"tx_hash": h, "block_height": hg, "block_time": t["time"]} for h, (t, hg) in TXS.items() if hg > after]
            if url.endswith("/tx_utxos"):
                return [koios_shape(TXS[h][0]) for h in body["_tx_hashes"]]
            if url.endswith("/account_info"):
                return [{"total_balance": "0", "rewards_available": "0"}]
            if "/account_assets" in url:
                return []
            if url.endswith("/utxo_info"):
                return []
            return None
        assert src == "blockfrost" and headers.get("project_id") == KEY
        if "/transactions" in url and "order=desc" in url:
            return [{"tx_hash": "x", "block_height": 99, "block_time": 0}]
        if "/transactions" in url:
            frm = int(url.split("from=")[1].split("&")[0])
            return [{"tx_hash": h, "block_height": hg, "block_time": t["time"]} for h, (t, hg) in TXS.items() if hg >= frm]
        if "/txs/" in url:
            return bf_shape(TXS[url.split("/txs/")[1].split("/")[0]][0])
        if url.endswith(f"/accounts/{STAKE}"):
            return {"controlled_amount": "0", "withdrawable_amount": "0"}
        if "/addresses/assets" in url:
            return []
        return None
    return req


def two_runs(koios_up, key):
    seen = []
    out = io.StringIO()
    with mock.patch.object(P.Http, "req", fake(koios_up, seen)), tempfile.TemporaryDirectory() as d, \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        http = P.Http({})
        base = WL.run(http, CFG, d, False, 1793050000, V.lp_policies(), bf_key=key)
        r = WL.run(http, CFG, d, False, 1793050000, V.lp_policies(), bf_key=key)
        doc = json.load(open(os.path.join(d, "whale_ledger.json"))) if os.path.exists(os.path.join(d, "whale_ledger.json")) else None
    return base, r, doc, seen, out.getvalue()


class Fallback(unittest.TestCase):
    def test_koios_primary_never_touches_blockfrost(self):
        base, r, doc, seen, _ = two_runs(True, KEY)
        self.assertEqual({s for s, _, _ in seen}, {"koios"})
        self.assertEqual(r["blockfrost_fallbacks"], 0)
        self.assertGreater(r["rows"], 0)

    def test_koios_down_falls_back_to_blockfrost_with_same_ledger(self):
        _, rk, dk, _, _ = two_runs(True, KEY)
        _, rb, db, seen, log = two_runs(False, KEY)
        self.assertIn("blockfrost", {s for s, _, _ in seen})
        self.assertGreater(rb["blockfrost_fallbacks"], 0)
        self.assertEqual((rb["rows"], db["positions"], db["rows"]), (rk["rows"], dk["positions"], dk["rows"]))
        self.assertEqual([o["status"] for o in db["orders"].values()], [o["status"] for o in dk["orders"].values()])
        self.assertNotIn(KEY, json.dumps(db))  # the key never lands in output
        self.assertNotIn(KEY, log)  # nor in logs
        self.assertTrue(all(KEY not in u for _, u, _ in seen))  # header only, never in a URL

    def test_empty_key_skips_blockfrost_gracefully(self):
        base, r, doc, seen, _ = two_runs(False, "")
        self.assertEqual({s for s, _, _ in seen}, {"koios"})
        self.assertEqual(base, {"error": "baseline failed"})
        self.assertIsNone(doc)

    def test_key_read_from_env_only_when_not_passed(self):
        with mock.patch.dict(os.environ, {"BLOCKFROST_PROJECT_ID": "  " + KEY + "\n"}):
            _, r, _, seen, _ = two_runs(False, None)
        self.assertTrue(r["blockfrost_calls"] > 0)


if __name__ == "__main__":
    unittest.main()
