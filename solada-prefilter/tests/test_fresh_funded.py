"""fresh_funded caution flag. Offline. Frozen fixture. No network, no secrets.

The flag is not a veto. Koios account_txs is first. Blockfrost is the fallback.
Both stay inside the dev_checks call caps. A missing read stays null.
"""
import hashlib, json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.dirname(HERE)
sys.path.insert(0, SRC)
sys.path.insert(0, HERE)

import dev_checks as DC  # noqa: E402
import flags as F  # noqa: E402
import prefilter as P  # noqa: E402
import vetoes as V  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "fresh_funded.json")))
CFG = json.load(open(os.path.join(SRC, "config.json")))
OWNER = "^zzOWNER$"


def _kh(label):
    return hashlib.sha256(("fresh/" + label).encode()).digest()[:28]


def _addr(label):
    return F.b32_encode("addr", b"\x01" + _kh(label + "/p") + _kh(label))


def _stake(label):
    return F.addr_info(_addr(label))[0]


def _unit(name):
    return hashlib.sha256(("fresh-unit/" + name).encode()).hexdigest()[:56] + name.encode().hex()


class Hours(unittest.TestCase):
    def test_fixture_cases_and_the_config_default(self):
        self.assertEqual(CFG["dev_checks"]["fresh_funded_h"], 24)
        self.assertEqual(DC.DEFAULTS["fresh_funded_h"], 24)
        self.assertEqual(CFG["dev_checks"]["max_calls"], 36)
        self.assertEqual(CFG["dev_checks"]["max_bf_calls"], 50)
        hours, launch = FX["hours"], FX["launch_s"]
        for case in FX["cases"]:
            got = DC.fresh_funded_of(case["block_time"], launch, hours)
            self.assertEqual(got, case["expect"], case["name"])
        self.assertIsNone(DC.fresh_funded_of(None, launch, hours))
        self.assertIsNone(DC.fresh_funded_of(launch, None, hours))
        # A fresh wallet is caution. It is not high, and a missing read is not caution.
        d = DC.dc_cfg({"dev_checks": {"fresh_funded_h": 24}})
        self.assertEqual(DC.risk_of(0, False, 0, 0, 0, 0, False, True, d, fresh_funded=True), "caution")
        self.assertEqual(DC.risk_of(0, False, 0, 0, 0, 0, False, True, d, fresh_funded=False), "low")
        self.assertEqual(DC.risk_of(0, False, 0, 0, 0, 0, False, True, d, fresh_funded=None), "low")
        self.assertEqual(DC.risk_of(0, True, 0, 0, 0, 0, False, True, d, fresh_funded=True), "high")


class _Http:
    def __init__(self, koios, bf):
        self.calls = {}
        self.shapes = []
        self.koios, self.bf = koios, bf

    def req(self, src, url, method="GET", body=None, headers=None, quiet404=True):
        self.calls[src] = self.calls.get(src, 0) + 1
        self.shapes.append(_shape(src, url))
        if src == "koios" and "/account_txs" in url:
            return self.koios
        if src == "blockfrost" and "/accounts/" in url:
            return self.bf
        return None


def _shape(src, url):
    """Path shape only. The stake in the query is not kept."""
    if src == "koios" and "/account_txs" in url and "order=block_height.asc" in url and "limit=1" in url:
        return "koios:account_txs"
    if src == "blockfrost" and "/accounts/" in url and "/transactions" in url and "order=asc" in url and "count=1" in url:
        return "bf:account_txs"
    return "other"


class Chain(unittest.TestCase):
    def _chain(self, http, calls=5, bf_calls=5, key="bf-key"):
        dc = {"max_calls": calls, "max_bf_calls": bf_calls, "bf_per_unit": 8}
        chain = DC.DevChain(http, dc, key, __import__("time").time() + 30)
        chain.begin_unit(1)
        return chain

    def test_koios_is_first_and_blockfrost_is_not_called(self):
        row = FX["koios"]["funded_1h_before"]
        http = _Http(row, FX["blockfrost"]["funded_1h_before"])
        chain = self._chain(http)
        got = chain.first_funded_s(_stake("dev"), {})
        self.assertEqual(got, row[0]["block_time"])
        self.assertEqual(http.shapes, ["koios:account_txs"])
        self.assertEqual(chain.fallbacks, 0)
        self.assertNotIn(_stake("dev"), json.dumps(http.shapes))

    def test_blockfrost_runs_only_after_koios_fails(self):
        http = _Http(None, FX["blockfrost"]["funded_1h_before"])
        chain = self._chain(http)
        got = chain.first_funded_s(_stake("dev"), {})
        self.assertEqual(got, FX["blockfrost"]["funded_1h_before"][0]["block_time"])
        self.assertEqual(http.shapes, ["koios:account_txs", "bf:account_txs"])
        self.assertEqual(chain.fallbacks, 1)

    def test_both_missing_is_null_and_an_empty_account_does_not_fall_back(self):
        http = _Http(None, None)
        chain = self._chain(http)
        self.assertIsNone(chain.first_funded_s(_stake("dev"), {}))
        self.assertEqual(http.shapes, ["koios:account_txs", "bf:account_txs"])
        empty = _Http([], FX["blockfrost"]["funded_1h_before"])
        chain = self._chain(empty)
        self.assertIsNone(chain.first_funded_s(_stake("dev"), {}))
        self.assertEqual(empty.shapes, ["koios:account_txs"])
        self.assertEqual(chain.fallbacks, 0)

    def test_no_budget_makes_no_call_and_a_cached_time_is_not_fetched_again(self):
        http = _Http(FX["koios"]["funded_1h_before"], None)
        chain = self._chain(http, calls=0, bf_calls=0, key="")
        self.assertIsNone(chain.first_funded_s(_stake("dev"), {}))
        self.assertEqual(http.shapes, [])
        cache = {}
        chain = self._chain(_Http(FX["koios"]["funded_1h_before"], None))
        first = chain.first_funded_s(_stake("dev"), cache)
        again = _Http(FX["koios"]["funded_1h_before"], None)
        chain2 = self._chain(again)
        self.assertEqual(chain2.first_funded_s(_stake("dev"), cache), first)
        self.assertEqual(again.shapes, [])
        self.assertNotIn(_stake("dev"), json.dumps({k: v for k, v in cache.items() if k != "funded"}))
        # The private cache holds the stake. The public flag does not.
        self.assertIn(_stake("dev"), cache["funded"])

    def test_koios_closed_uses_blockfrost_inside_the_row_cap(self):
        http = _Http(FX["koios"]["funded_1h_before"], FX["blockfrost"]["funded_1h_before"])
        http.koios_pause_until = __import__("time").time() + 120
        http.dev_checks = True
        chain = self._chain(http, calls=36, bf_calls=1)
        got = chain.first_funded_s(_stake("dev"), {})
        self.assertEqual(got, FX["blockfrost"]["funded_1h_before"][0]["block_time"])
        self.assertEqual(http.shapes, ["bf:account_txs"])
        http2 = _Http(None, FX["blockfrost"]["funded_1h_before"])
        http2.koios_pause_until = __import__("time").time() + 120
        http2.dev_checks = True
        chain = self._chain(http2, bf_calls=0)
        self.assertIsNone(chain.first_funded_s(_stake("dev"), {}))
        self.assertEqual(http2.shapes, [])


def _book(unit, launch_s):
    h = hashlib.sha256(b"mint").hexdigest()
    return [{
        "hash": h, "height": 10, "time": int(launch_s),
        "inputs": [{"ref": "in#0", "addr": _addr("funder"), "stake": _stake("funder"),
                    "lovelace": 5_000_000, "assets": {}}],
        "outputs": [
            {"ref": h + "#0", "addr": _addr("dev"), "stake": _stake("dev"),
             "lovelace": 2_000_000, "assets": {unit: 1000}},
        ],
    }]


class Row(unittest.TestCase):
    def _run(self, koios_account, bf_account=None, bf_key="bf-key", hours=24):
        launch_s = FX["launch_s"]
        unit = _unit("NEW")
        books = {unit: _book(unit, launch_s)}
        launch = {"id": "n", "ticker": "NEW", "policyId": unit[:56], "assetName": unit[56:],
                  "launcherAddress": _addr("dev"), "liveAtMs": launch_s * 1000}
        tracked = [{"ticker": "NEW", "unit": unit, "launch": launch, "mcap_ada": 100, "vol_h24_ada": 5}]
        seen = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            seen.append(_shape(src, url))
            if src == "koios" and "/asset_txs" in url:
                return [{"tx_hash": books[unit][0]["hash"], "block_height": 10, "block_time": int(launch_s)}]
            if src == "koios" and url.endswith("/tx_info"):
                tx = books[unit][0]

                def side(rows, incoming):
                    out = []
                    for u in rows:
                        h, idx = (tx["hash"], u["ref"].split("#")[1]) if incoming else u["ref"].split("#")
                        out.append({"tx_hash": h, "tx_index": int(idx),
                                    "payment_addr": {"bech32": u["addr"]}, "stake_addr": u["stake"],
                                    "value": str(u["lovelace"]),
                                    "asset_list": [{"policy_id": a[:56], "asset_name": a[56:], "quantity": str(q)}
                                                   for a, q in u["assets"].items()]})
                    return out

                return [{"tx_hash": tx["hash"], "inputs": side(tx["inputs"], False),
                         "outputs": side(tx["outputs"], True)}]
            if src == "koios" and "/account_txs" in url:
                return koios_account
            if src == "blockfrost" and "/accounts/" in url:
                return bf_account
            return None

        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            http = P.Http({})
            cfg = {"dev_checks": {"budget_s": 30, "max_calls": 36, "max_bf_calls": 50,
                                  "max_launches": 1, "fresh_funded_h": hours},
                   "flags": {"exclude_tags": {}}}
            with unittest.mock.patch.object(P.Http, "req", req):
                summary, triggers = DC.run(
                    http, cfg, tracked, [launch], {}, os.path.join(d, "data"), os.path.join(d, "cache"),
                    False, launch_s + 3600, bf_key=bf_key, owner_env=OWNER)
            with open(os.path.join(d, "data", "dev_checks.json")) as f:
                doc = json.load(f)
            with open(os.path.join(d, "cache", "dev-checks-cache.json")) as f:
                cache = json.load(f)
        return summary, triggers, doc, seen, cache

    def test_a_fresh_dev_is_caution_and_not_a_veto_or_a_trigger(self):
        summary, triggers, doc, seen, cache = self._run(FX["koios"]["funded_1h_before"])
        row = doc["entries"][0]
        self.assertTrue(row["fresh_funded"])
        self.assertEqual(row["risk"], "caution")
        self.assertEqual(triggers, [])
        self.assertFalse(row["changed_since_last"])
        self.assertEqual(seen.count("koios:account_txs"), 1)
        self.assertNotIn("bf:account_txs", seen)
        self.assertLessEqual(summary["koios_calls"], 36)
        text = json.dumps(doc)
        self.assertNotIn(_stake("dev"), text)
        self.assertNotIn(_addr("dev"), text)
        F.assert_public(doc)
        self.assertTrue(DC.dev_tag_ok(row["dev_tag"]))
        veto = V.build({"entries": doc["entries"], "thresholds": doc["thresholds"]},
                       {"moves": []}, CFG, OWNER, "2026-10-28T02:07:00Z")
        self.assertEqual(veto["tokens"], [])
        self.assertEqual(veto["counts"]["ahead_of_dump"], 0)
        self.assertNotIn("fresh_funded", json.dumps(veto["counts"]))
        # counts keys are the veto codes only
        self.assertEqual(set(veto["counts"]), {"dev_sold", "serial_launcher", "lp_pulled", "ahead_of_dump", "known_rugger", "known_rugger_funded"})

    def test_an_old_wallet_is_not_caution_and_a_miss_stays_null(self):
        old = [{"tx_hash": "cc", "block_height": 1, "block_time": 1}]
        _, triggers, doc, _, _ = self._run(old)
        row = doc["entries"][0]
        self.assertIs(row["fresh_funded"], False)
        self.assertEqual(row["risk"], "low")
        self.assertEqual(triggers, [])
        _, _, missed, seen, _ = self._run(None, bf_account=None, bf_key="")
        self.assertIsNone(missed["entries"][0]["fresh_funded"])
        self.assertEqual(missed["entries"][0]["risk"], "low")
        self.assertIn("koios:account_txs", seen)
        self.assertNotIn("bf:account_txs", seen)
        _, _, fell, seen_b, _ = self._run(None, bf_account=FX["blockfrost"]["funded_1h_before"])
        self.assertTrue(fell["entries"][0]["fresh_funded"])
        self.assertEqual(fell["entries"][0]["risk"], "caution")
        self.assertEqual(seen_b.count("koios:account_txs"), 1)
        self.assertEqual(seen_b.count("bf:account_txs"), 1)

    def test_a_failed_reread_keeps_the_stored_flag_and_does_not_fire(self):
        unit = _unit("KEEP")
        launch = {"policyId": unit[:56], "assetName": unit[56:], "liveAtMs": FX["launch_s"] * 1000,
                  "launcherAddress": _addr("dev")}
        item = {"ticker": "KEEP", "unit": unit, "launch": launch, "watch": {}, "mcap_ada": 10, "vol_h24_ada": 1}
        prev = {"unit": unit, "ticker": "KEEP", "fresh_funded": True, "dev_sold_ada": 0, "late_rug": False,
                "risk": "caution", "dev_tag": "w-0123abcd"}
        scan = {"stake": _stake("dev"), "skipped": "budget", "stats": None, "complete": False}
        row = DC._entry(item, scan, prev, {"units": {}}, [launch], DC.dc_cfg({}),
                        FX["launch_s"] + 10, (FX["launch_s"] + 10) * 1000, False)
        self.assertIs(row["fresh_funded"], True)
        self.assertEqual(row["risk"], "caution")
        self.assertFalse(row["changed_since_last"])
        self.assertIsNone(DC.trigger_for(row))
        self.assertNotIn(_stake("dev"), json.dumps(row))

    def test_owner_stake_is_not_looked_up(self):
        launch_s = FX["launch_s"]
        unit = _unit("OWN")
        launch = {"id": "o", "ticker": "OWN", "policyId": unit[:56], "assetName": unit[56:],
                  "launcherAddress": _addr("dev"), "liveAtMs": launch_s * 1000}
        tracked = [{"ticker": "OWN", "unit": unit, "launch": launch, "mcap_ada": 10, "vol_h24_ada": 1}]
        seen = []

        def req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            seen.append(url)
            if "/account_txs" in url or ("/accounts/" in url and "/transactions" in url):
                seen.append("looked-up")
            return []

        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "cache"))
            http = P.Http({})
            cfg = {"dev_checks": {"budget_s": 30, "max_calls": 36, "max_bf_calls": 50, "max_launches": 1},
                   "flags": {"exclude_tags": {}}}
            with unittest.mock.patch.object(P.Http, "req", req):
                DC.run(http, cfg, tracked, [launch], {}, os.path.join(d, "data"), os.path.join(d, "cache"),
                       False, launch_s + 10, bf_key="bf-key",
                       owner_env="^" + __import__("re").escape(_stake("dev")) + "$")
            with open(os.path.join(d, "data", "dev_checks.json")) as f:
                doc = json.load(f)
            with open(os.path.join(d, "cache", "dev-checks-cache.json")) as f:
                cache = f.read()
        self.assertIsNone(doc["entries"][0]["fresh_funded"])
        self.assertNotIn("looked-up", seen)
        self.assertNotIn(_stake("dev"), json.dumps(doc))
        self.assertNotIn(_stake("dev"), cache)


if __name__ == "__main__":
    unittest.main()
