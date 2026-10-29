"""Regressions for Snek's 2026-10-26 12:25 PM ET followups and the Oct 25 8:27 PM HERO re-fire (offline).

1. ASCEND ada_equiv 8,171 vs ~3.88k real ADA leg: the tx window held older ASCEND buys than the snapshot delta,
   and all their ADA was credited to it. A leg is now used as-is only when its token qty matches the delta;
   otherwise the delta is valued at the legs' ADA-per-token rate.
2. ASCEND delta 130,904,443 (raw) with ada_equiv 426: decimals were only looked up on the price path, so ADA-leg
   moves showed raw units. Decimals are now always resolved and the payload carries whole-token qty.
3. STRIKE 6.4% on a 0.75-token move: dust moves get "dust": true instead of a pct (config whale.dust).
4. Curve tokens (e.g. KNOPPIES) carry curve_pct in the whale payload.
5. A kill line Snek already marked "hit: ..." in log.kill_lines never fires the webhook.
6. GeckoTerminal 429: backoff retries, per-run cache, and a skip after repeated failures."""
import io, json, os, sys, tempfile, unittest, urllib.error
from unittest import mock
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import flags as F  # noqa: E402
import valuation as V  # noqa: E402
import prefilter as P  # noqa: E402
from test_regressions import addr, stake, run_main, quiet_req, kill_patches, fresh_state  # noqa: E402

FX = json.load(open(os.path.join(HERE, "fixtures", "oct26.json")))
ASCEND = "a1" * 28 + b"ASCEND".hex()
STRIKE = "b2" * 28 + b"STRIKE".hex()
KNOP = "c3" * 28 + b"KNOPPIES".hex()
SOLADA = "d4" * 28 + b"SOLADA".hex()
POOL = F.b32_encode("addr", b"\x71" + b"\x33" * 28)


def buy_tx(wa, ada_raw, qty, unit=ASCEND):
    return {"inputs": [(wa, ada_raw + 2_000_000, {})], "outputs": [(wa, 2_000_000, {unit: qty}), (POOL, ada_raw, {})]}


class AdaLegWindow(unittest.TestCase):
    def legs(self, older=True):
        wa, ws = addr("WHALE"), stake("WHALE")
        ut = {f"b{i}": buy_tx(wa, a, q) for i, (a, q) in enumerate(FX["ascend_buys"])}
        # 114.5 ASCEND for 69k SOLADA: token-for-token, no ADA leg
        ut["swap"] = {"inputs": [(wa, 4_000_000, {SOLADA: 69000})], "outputs": [(wa, 2_000_000, {ASCEND: FX["ascend_token_swap_raw"]})]}
        if older:
            for i, (a, q) in enumerate(FX["ascend_older_window_buys"]):
                ut[f"old{i}"] = buy_tx(wa, a, q)
        return V.ada_leg_detail(ws, ut, F.addr_info)

    def test_window_wider_than_delta_is_valued_at_leg_rate(self):
        leg = self.legs()[ASCEND]
        self.assertAlmostEqual(leg["ada"], FX["ascend_bad_ada_equiv"], delta=1)  # the old bogus number
        val, basis = V.value_ada(FX["ascend_delta_raw"], leg["ada"], FX["decimals"], None, leg["qty"])
        self.assertEqual(basis, "ada_leg_rate")
        self.assertLess(abs(val - FX["ascend_real_leg_ada"]) / FX["ascend_real_leg_ada"], 0.10)

    def test_matching_window_uses_leg_as_is(self):
        leg = self.legs(older=False)[ASCEND]
        self.assertEqual(round(leg["ada"]), FX["ascend_real_leg_ada"])
        val, basis = V.value_ada(leg["qty"], leg["ada"], FX["decimals"], None, leg["qty"])
        self.assertEqual((round(val), basis), (FX["ascend_real_leg_ada"], "ada_leg"))

    def test_small_delta_not_given_whole_window_leg(self):
        leg = self.legs()[ASCEND]
        val, basis = V.value_ada(FX["ascend_small_delta_raw"], leg["ada"], FX["decimals"], None, leg["qty"])
        self.assertEqual(basis, "ada_leg_rate")
        self.assertLess(val, 200)  # ~131 tokens at ~1.1 ADA, never the 426 / 8,171 window total

    def test_legacy_wrapper_unchanged(self):
        self.assertEqual(V.ada_leg_by_unit(stake("WHALE"), {"x": buy_tx(addr("WHALE"), 73_000_000, 5)}, F.addr_info), {ASCEND: 73.0})


class Dust(unittest.TestCase):
    def test_strike_dust_has_no_pct(self):
        w = abs(FX["strike_delta_raw"]) / 10 ** FX["decimals"]
        self.assertAlmostEqual(100 * w / (FX["strike_prev_raw"] / 1e6), 6.4, places=1)  # the old misleading pct
        self.assertTrue(V.is_dust(FX["strike_delta_raw"], FX["decimals"], round(w * FX["strike_price_ada"], 2)))
        self.assertFalse(V.is_dust(FX["ascend_delta_raw"], FX["decimals"], 3900))
        self.assertTrue(V.is_dust(500_000, 6, None))  # 0.5 token, unknown value
        self.assertFalse(V.is_dust(500_000, None, None))  # nothing known: not called dust

    def test_thresholds_configurable(self):
        dc = V.dust_cfg({"dust": {"pct_min_ada": 1, "pct_min_qty": 0.1}})
        self.assertFalse(V.is_dust(FX["strike_delta_raw"], 6, 1.05, dc))


class WhalePayload(unittest.TestCase):
    """End-to-end --dry-run with a stubbed chain."""

    def run_whale(self):
        wa, ws = addr("WHALE"), stake("WHALE")
        prev = {"lovelace": 900_000_000_000, ASCEND: 270_000_000_000, STRIKE: FX["strike_prev_raw"], KNOP: 10_000_000_000}
        snap = {"lovelace": 900_000_000_000 - 3_881_000_000, ASCEND: prev[ASCEND] + FX["ascend_delta_raw"],
                STRIKE: prev[STRIKE] + FX["strike_delta_raw"], KNOP: 12_500_000_000}
        ut = {f"b{i}": buy_tx(wa, a, q) for i, (a, q) in enumerate(FX["ascend_buys"] + FX["ascend_older_window_buys"])}
        launches = [{"id": "kn", "ticker": "KNOPPIES", "policyId": KNOP[:56], "assetName": KNOP[56:],
                     "curve": {"progressBps": FX["knoppies_progress_bps"], "graduated": False}, "liveAtMs": 0}]

        def fake_req(self_, src, url, method="GET", body=None, headers=None, quiet404=True):
            self_.calls[src] = self_.calls.get(src, 0) + 1
            if url.endswith("/launches"):
                return launches
            if url.endswith("/market"):
                return {"quotes": {}}
            if url.endswith("/asset_info"):
                return [{"token_registry_metadata": {"decimals": FX["decimals"]}}]
            return None
        cfg = json.load(open(os.path.join(HERE, "..", "config.json")))
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "c"))
            json.dump({"tag": P.tag(ws), "snapshot": prev, "height": 100}, open(os.path.join(d, "c", "whale-snapshot.json"), "w"))
            fresh_state(d, {"seen_launches": ["old", "kn"]})
            patches = [mock.patch.object(P, "tracked_names", lambda paper, log: {}),
                       mock.patch.object(P.Chain, "handle_address", lambda self, p, n: wa),
                       mock.patch.object(P.Chain, "stake_of", lambda self, a: ws),
                       mock.patch.object(P.Chain, "account_assets", lambda self, s: dict(snap)),
                       mock.patch.object(P.Chain, "account_txs_after", lambda self, s, h, n: [(t, 101 + i) for i, t in enumerate(ut)]),
                       mock.patch.object(F.FlagChain, "tx_utxos", lambda self, hs: {h: ut[h] for h in hs}),
                       mock.patch.object(P, "gt_pool", lambda http, u: {"ada_side": 9000, "mcap_ada": None, "price_ada": FX["strike_price_ada"], "basis": "geckoterminal"} if u == STRIKE else None)]
            run = run_main(d, fake_req, patches, env={"WHALE_ADA_HANDLE": "fixture-handle"})
            F.assert_public(run)
        return {m["asset"]: m for m in run["whale"]["moves"]}, cfg

    def test_payload(self):
        mv, cfg = self.run_whale()
        a = mv["ASCEND"]
        self.assertEqual(a["value_basis"], "ada_leg_rate")
        self.assertLess(abs(a["ada_equiv"] - FX["ascend_real_leg_ada"]) / FX["ascend_real_leg_ada"], 0.10)
        self.assertEqual(a["decimals"], 6)
        self.assertAlmostEqual(a["qty"], FX["ascend_delta_raw"] / 1e6, places=3)
        s = mv["STRIKE"]  # always_tickers keeps it visible, but no misleading pct
        self.assertTrue(s.get("dust"))
        self.assertNotIn("pct", s)
        self.assertAlmostEqual(s["qty"], -0.75)
        k = mv["KNOPPIES"]  # +25% position, curve launch
        self.assertEqual(k["curve_pct"], FX["knoppies_progress_bps"] / 100)
        self.assertEqual(k["pct"], 25.0)


class DeskHitKillLine(unittest.TestCase):
    def test_parsed_as_desk_hit(self):
        t = P.tracked_names({"open": []}, {"kill_lines": {"HERO": FX["hero_kill_text"], "JUICE": "pool ADA side under 4k or holders under 40"}})
        self.assertTrue(t["HERO"]["desk_hit"])
        self.assertFalse(t["JUICE"]["desk_hit"])

    def test_already_hit_never_fires_even_on_extra_runs(self):
        tr = {"HERO": {"kill": {"ada_side_below": 4000}, "text": FX["hero_kill_text"], "asset_id": "ee" * 28 + b"HERO".hex(),
                       "source": "log.kill_lines", "desk_hit": True}}
        fired = []
        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            for side in (FX["hero_ada_side"], FX["hero_ada_side"], 2000):  # repeat run, then far deeper
                run = run_main(d, quiet_req, kill_patches(tr, side), dry=False)
                fired.append([t["name"] for t in run["triggers"] if t["kind"] == "kill_line"])
            st = json.load(open(os.path.join(d, "state.json")))
        self.assertEqual(fired, [[], [], []])
        self.assertTrue(st["kill_reported"]["HERO"]["desk_hit"])


class GeckoTerminal429(unittest.TestCase):
    def _err(self, code, ra=None):
        return urllib.error.HTTPError("u", code, "x", {"Retry-After": ra} if ra else {}, io.BytesIO(b""))

    def test_backoff_then_success(self):
        h = P.Http({"geckoterminal": 0}, {"geckoterminal": [6, 15]})
        slept, calls = [], []
        h.sleep = slept.append

        def op(r, timeout=30):
            calls.append(1)
            if len(calls) < 3:
                raise self._err(429, "20" if len(calls) == 1 else None)
            return io.BytesIO(b'{"ok": 1}')
        with mock.patch("urllib.request.urlopen", op):
            self.assertEqual(h.req("geckoterminal", "https://x"), {"ok": 1})
        self.assertEqual(slept, [20, 15])
        self.assertEqual(h.failures, {})

    def test_cache_and_skip_after_failures(self):
        h = P.Http({}, {})
        seen = []

        def req(src, url, **kw):
            seen.append(src)
            if src == "geckoterminal":
                h._fail(src, "http 429")
            return None
        h.req = req
        for i in range(6):
            P.gt_pool(h, f"u{i}")
        P.gt_pool(h, "u0")  # cached: no new calls
        self.assertEqual(seen.count("geckoterminal"), P.GT_MAX_429)
        self.assertEqual(seen.count("dexscreener"), 6)


if __name__ == "__main__":
    unittest.main()
