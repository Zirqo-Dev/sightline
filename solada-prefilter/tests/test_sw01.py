"""SW-01 candidate detector. Offline, no secrets, no network.

Covers a full qualify, each gate failing on its own, the 2026-11-01 19:40Z cutoff,
once-per-policy with the 6h re-arm, and the 48h exit post.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import flags as F  # noqa: E402
import prefilter as P  # noqa: E402
import sw01 as S  # noqa: E402

POLICY = "ab" * 28
UNIT = POLICY + "54455354"
OTHER = "cd" * 28
LAUNCH = "2026-11-02T00:00:00Z"
FLAG_AT = "2026-11-02T00:10:00Z"
BUY = "2026-11-03T00:30:00Z"
NOW = "2026-11-03T02:00:00Z"
CUTOFF = "2026-11-01T19:40:00Z"


def epoch(stamp):
    return S.parse_time(stamp)


def addr(label):
    sk = hashlib.sha256(("sw01/" + label).encode()).digest()[:28]
    pay = hashlib.sha256(("pay/" + label).encode()).digest()[:28]
    return F.b32_encode("addr", b"\x01" + pay + sk)


def script_addr():
    return F.b32_encode("addr", b"\x71" + b"\x22" * 28)


def cfg():
    return {
        "sw01": {},
        "whale": {"codename": "Fixture Whale"},
        "flags": {"snek_prefix_tags": {"lead_whale": ["abc"]}, "exclude_tags": {}},
        "webhook": {"idempotency_slot_minutes": 120},
    }


def launch(ticker="TEST", live=LAUNCH, policy=POLICY, **kw):
    row = {
        "id": "id-" + policy[:8], "ticker": ticker, "policyId": policy, "assetName": "54455354",
        "liveAtMs": int(epoch(live) * 1000), "pool": {"dex": "minswap"},
        "curve": {"graduated": True}, "holders": 130,
    }
    row.update(kw)
    return row


def quote(**kw):
    row = {"ticker": "TEST", "unit": UNIT, "ada_side": 25000, "pool_basis": "geckoterminal", "native_holders": 130}
    row.update(kw)
    return row


def flag(kind="coordinated_buys", ticker="TEST", at=FLAG_AT):
    return {"kind": kind, "ticker": ticker, "at": at}


def buy(token="TEST", kind="swap", **kw):
    row = {"time": BUY, "kind": kind, "token": token, "direction": "buy"}
    row.update(kw)
    return row


def ctx(flags=None, launches=None, rows=None, ledger=None, moves=None, liquidity=None,
        dists=None, pools=None, exclude=None, settings=None):
    settings = settings or cfg()
    if exclude is not None:
        settings = json.loads(json.dumps(settings))
        settings["flags"]["exclude_tags"] = exclude
    allowed, codename = S.allowed_labels(settings)
    return {
        "launches": launches if launches is not None else [launch()],
        "rows": rows if rows is not None else [quote()],
        "dists": dists or {},
        "flags": flags if flags is not None else [flag()],
        "exclude_tags": (settings.get("flags") or {}).get("exclude_tags") or {},
        "ledger_rows": ledger if ledger is not None else [buy()],
        "ledger_index": {},
        "whale_moves": moves or [],
        "liquidity": liquidity or [],
        "pools": pools or {},
        "allowed": allowed,
        "codename": codename,
        "cfg": settings,
    }


def candidate(decision):
    return [a for a in decision["alerts"] if a["kind"] == "sw01_candidate"]


def exits(decision):
    return [a for a in decision["alerts"] if a["kind"] == "sw01_exit"]


class Qualify(unittest.TestCase):
    def test_all_gates_post_the_candidate_payload(self):
        state = {}
        decision = S.decide(state, ctx(pools={UNIT: {"buys_h6": 9, "sells_h6": 4}}), epoch(NOW))
        got = candidate(decision)
        self.assertEqual(len(got), 1)
        body = got[0]["payload"]
        self.assertEqual(body["reason"], "sw01_candidate")
        self.assertEqual(body["names"], ["TEST"])
        self.assertEqual(set(body["metrics"]), set(S.METRIC_KEYS))
        self.assertEqual(body["metrics"]["policy"], POLICY)
        self.assertEqual(body["metrics"]["pool_ada"], 25000)
        self.assertEqual(body["metrics"]["holders"], 130)
        self.assertEqual(body["metrics"]["buys_6h"], 9)
        self.assertEqual(body["metrics"]["sells_6h"], 4)
        self.assertEqual(body["metrics"]["whale_tags"], ["Fixture Whale"])
        self.assertGreaterEqual(body["metrics"]["age_h"], 24)
        F.assert_public(body)
        self.assertNotIn("w-", json.dumps(body))

    def test_missing_trade_counts_stay_null_and_still_qualify(self):
        decision = S.decide({}, ctx(), epoch(NOW))
        metrics = candidate(decision)[0]["payload"]["metrics"]
        self.assertIsNone(metrics["buys_6h"])
        self.assertIsNone(metrics["sells_6h"])

    def test_fresh_wallet_swarm_counts_as_the_launch_flag(self):
        decision = S.decide({}, ctx(flags=[flag("fresh_wallet_swarm")]), epoch(NOW))
        self.assertEqual(len(candidate(decision)), 1)

    def test_a_role_labelled_buy_is_a_codename(self):
        move = {"kind": "swap", "direction": "buy", "ticker": "TEST", "actor_tag": "lead_whale",
                "time": BUY, "policy_short": POLICY[:8]}
        decision = S.decide({}, ctx(ledger=[], liquidity=[move]), epoch(NOW))
        self.assertEqual(candidate(decision)[0]["payload"]["metrics"]["whale_tags"], ["lead_whale"])

    def test_snapshot_move_and_order_fill_count(self):
        move = {"at": BUY, "asset": "TEST", "delta": 5}
        fill = buy(kind="order_fill")
        fill.pop("direction", None)
        decision = S.decide({}, ctx(ledger=[fill], moves=[move]), epoch(NOW))
        self.assertEqual(candidate(decision)[0]["payload"]["metrics"]["whale_tags"], ["Fixture Whale"])

    def test_lower_holder_count_is_used(self):
        self.assertEqual(S.holder_count(180, 130), 130)
        self.assertEqual(S.holder_count(100, 200), 100)
        self.assertEqual(S.holder_count(None, 140), 140)
        self.assertEqual(S.holder_count(150, None), 150)
        keys = [addr(f"h{i}") for i in range(120)]
        dist = [(a, 10) for a in keys] + [(script_addr(), 10**9)]
        self.assertEqual(S.circulating_holders(dist, {}), 120)
        self.assertTrue(F.addr_info(script_addr())[1])
        c = ctx(dists={UNIT: dist}, launches=[launch(holders=500)])
        decision = S.decide({}, c, epoch(NOW))
        self.assertEqual(candidate(decision)[0]["payload"]["metrics"]["holders"], 120)

    def test_curve_reserve_tag_is_not_a_holder(self):
        keys = [addr(f"c{i}") for i in range(120)]
        dist = [(a, 5) for a in keys]
        exclude = {F.tag(keys[0]): "curve"}
        self.assertEqual(S.circulating_holders(dist, exclude), 119)
        c = ctx(dists={UNIT: dist}, launches=[launch(holders=400)], exclude=exclude)
        decision = S.decide({}, c, epoch(NOW))
        self.assertEqual(candidate(decision), [])
        self.assertIn("holders", decision["held"][0]["failed"])

    def test_config_floors_are_not_loosened(self):
        with open(os.path.join(HERE, "..", "config.json")) as fh:
            file_cfg = json.load(fh)
        knobs = S.sw_cfg(file_cfg)
        self.assertEqual(knobs["min_pool_ada"], 25000)
        self.assertEqual(knobs["min_holders"], 120)
        self.assertEqual(knobs["min_age_h"], 24)
        self.assertEqual(knobs["rearm_h"], 6)
        self.assertEqual(knobs["exit_h"], 48)
        self.assertEqual(knobs["cutoff"], CUTOFF)


class Gates(unittest.TestCase):
    def test_no_swarm_flag_does_not_qualify(self):
        state = {"fired": {"launch:old": "2026-01-01T00:00:00Z"}}
        decision = S.decide(state, ctx(flags=[flag("identical_size_buys")]), epoch(NOW))
        self.assertEqual(decision["alerts"], [])
        self.assertNotIn("sw01", state)
        self.assertEqual(state["fired"]["launch:old"], "2026-01-01T00:00:00Z")
        result = S.gates(
            {"policy": POLICY, "ticker": "TEST", "live_s": epoch(LAUNCH), "swarm": False,
             "origin": [epoch(LAUNCH)], "row": quote(), "on_chain": None, "solada": 130, "flow": {}},
            ctx(), epoch(NOW), S.sw_cfg(cfg()))
        self.assertIn("swarm", result["failed"])

    def test_curve_quote_is_not_a_dex_pool(self):
        decision = S.decide({}, ctx(rows=[quote(pool_basis="solada-curve", ada_side=100000)]), epoch(NOW))
        self.assertEqual(candidate(decision), [])
        self.assertIn("pool", decision["held"][0]["failed"])

    def test_pool_ada_under_25000_fails(self):
        decision = S.decide({}, ctx(rows=[quote(ada_side=24999)]), epoch(NOW))
        self.assertEqual(candidate(decision), [])
        self.assertEqual(decision["held"][0]["failed"], ["pool_ada"])

    def test_holders_under_120_fails(self):
        decision = S.decide({}, ctx(launches=[launch(holders=119)], rows=[quote(native_holders=119)]), epoch(NOW))
        self.assertIn("holders", decision["held"][0]["failed"])
        self.assertEqual(candidate(decision), [])

    def test_on_chain_lower_than_solada_can_fail_the_holder_gate(self):
        keys = [addr(f"s{i}") for i in range(100)]
        dist = [(a, 1) for a in keys]
        decision = S.decide({}, ctx(dists={UNIT: dist}, launches=[launch(holders=200)]), epoch(NOW))
        self.assertIn("holders", decision["held"][0]["failed"])

    def test_age_under_24h_fails(self):
        now = epoch(LAUNCH) + 23 * 3600
        decision = S.decide({}, ctx(), now)
        self.assertIn("age", decision["held"][0]["failed"])
        self.assertEqual(candidate(decision), [])

    def test_buy_before_launch_plus_24h_fails(self):
        early = buy()
        early["time"] = "2026-11-02T12:00:00Z"
        decision = S.decide({}, ctx(ledger=[early]), epoch(NOW))
        self.assertEqual(decision["held"][0]["failed"], ["whale"])

    def test_buy_at_exactly_launch_plus_24h_does_not_count(self):
        edge = buy()
        edge["time"] = "2026-11-03T00:00:00Z"
        decision = S.decide({}, ctx(ledger=[edge]), epoch(NOW))
        self.assertIn("whale", decision["held"][0]["failed"])

    def test_a_hash_tag_is_not_a_codename(self):
        move = {"kind": "swap", "direction": "buy", "ticker": "TEST", "actor_tag": "w-0123abcd",
                "time": BUY, "policy_short": POLICY[:8]}
        decision = S.decide({}, ctx(ledger=[], liquidity=[move]), epoch(NOW))
        self.assertIn("whale", decision["held"][0]["failed"])

    def test_a_different_token_does_not_count(self):
        decision = S.decide({}, ctx(ledger=[buy("OTHER")]), epoch(NOW))
        self.assertIn("whale", decision["held"][0]["failed"])

    def test_unknown_pool_retries_instead_of_locking_for_6h(self):
        state = {}
        first = S.decide(state, ctx(rows=[quote(pool_basis=None, ada_side=None)]), epoch(NOW))
        self.assertEqual(candidate(first), [])
        self.assertNotIn(POLICY, state.get("sw01", {}).get("miss", {}))
        second = S.decide(state, ctx(), epoch(NOW) + 60)
        self.assertEqual(len(candidate(second)), 1)

    def test_one_bad_token_does_not_block_the_next(self):
        real = S.gates

        def boom(token, ctx_, now_s, knobs):
            if token["ticker"] == "BAD":
                raise RuntimeError("nope")
            return real(token, ctx_, now_s, knobs)

        good = launch()
        bad = launch(ticker="BAD", policy=OTHER)
        bad_row = quote(ticker="BAD", unit=OTHER + "54455354")
        flags = [flag(), flag(ticker="BAD")]
        with mock.patch.object(S, "gates", boom):
            decision = S.decide({}, ctx(flags=flags, launches=[bad, good], rows=[bad_row, quote()]), epoch(NOW))
        self.assertEqual([a["ticker"] for a in candidate(decision)], ["TEST"])
        self.assertTrue(any(h["ticker"] == "BAD" and "error" in h["failed"] for h in decision["held"]))


class Cutoff(unittest.TestCase):
    def test_cutoff_matches_toronto_1440(self):
        self.assertEqual(S.CUTOFF, CUTOFF)
        try:
            from zoneinfo import ZoneInfo
            local = datetime(2026, 11, 1, 14, 40, tzinfo=ZoneInfo("America/Toronto"))
        except Exception:
            return
        self.assertEqual(local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), CUTOFF)

    def _at(self, live, flag_at=None):
        now = epoch(live) + 30 * 3600
        buy_at = S.iso(epoch(live) + 25 * 3600)
        return S.decide({}, ctx(
            flags=[flag(at=flag_at or S.iso(epoch(live) + 600))],
            launches=[launch(live=live)],
            ledger=[buy(time=buy_at)],
        ), now)

    def test_launch_before_the_cutoff_fails(self):
        decision = self._at("2026-11-01T19:39:59Z")
        self.assertEqual(candidate(decision), [])
        self.assertIn("cutoff", decision["held"][0]["failed"])

    def test_exactly_the_cutoff_fails(self):
        decision = self._at(CUTOFF)
        self.assertIn("cutoff", decision["held"][0]["failed"])

    def test_one_second_after_the_cutoff_passes(self):
        decision = self._at("2026-11-01T19:40:01Z")
        self.assertEqual(len(candidate(decision)), 1)

    def test_earlier_of_flag_and_sighting_is_the_origin(self):
        # Launch is after the cutoff, but the first swarm flag is before it.
        decision = self._at("2026-11-02T00:00:00Z", flag_at="2026-11-01T19:00:00Z")
        self.assertIn("cutoff", decision["held"][0]["failed"])
        # Flag is after the cutoff, but the launch was sighted before it.
        decision = self._at("2026-11-01T18:00:00Z", flag_at="2026-11-02T00:00:00Z")
        self.assertIn("cutoff", decision["held"][0]["failed"])


class RearmAndExit(unittest.TestCase):
    def test_once_per_policy_and_rearm_after_6h(self):
        state = {}
        now = epoch(NOW)
        locked = S.decide(state, ctx(rows=[quote(ada_side=24999)]), now)
        self.assertEqual(candidate(locked), [])
        miss = state["sw01"]["miss"][POLICY]
        still = S.decide(state, ctx(), now + 6 * 3600 - 1)
        self.assertEqual(candidate(still), [])
        self.assertEqual(state["sw01"]["miss"][POLICY], miss)
        opened = S.decide(state, ctx(), now + 6 * 3600)
        self.assertEqual(len(candidate(opened)), 1)
        S.mark_sent(state, opened["alerts"][0], now + 6 * 3600)
        again = S.decide(state, ctx(), now + 6 * 3600 + 60)
        self.assertEqual(candidate(again), [])
        self.assertEqual(state["fired"] if "fired" in state else {}, {})

    def test_exit_fires_once_at_alert_plus_48h(self):
        state = {}
        now = epoch(NOW)
        decision = S.decide(state, ctx(), now)
        alert = candidate(decision)[0]
        S.mark_sent(state, alert, now)
        early = S.decide(state, ctx(), now + 48 * 3600 - 1)
        self.assertEqual(exits(early), [])
        due = S.decide(state, ctx(), now + 48 * 3600)
        got = exits(due)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["payload"], {"reason": "sw01_exit", "names": ["TEST"]})
        self.assertNotIn("metrics", got[0]["payload"])
        S.mark_sent(state, got[0], now + 48 * 3600)
        done = S.decide(state, ctx(), now + 48 * 3600 + 3600)
        self.assertEqual(exits(done), [])
        self.assertEqual(candidate(done), [])

    def test_a_failed_post_retries_and_a_success_does_not(self):
        state = {}
        now = epoch(NOW)
        decision = S.decide(state, ctx(), now)
        sent = []

        class Http:
            def __init__(self, ok):
                self.ok = ok

            def post_webhook(self, url, body, headers=None):
                sent.append(body)
                return self.ok

        first = S.deliver(Http(False), cfg(), state, decision, "https://example.invalid/h", "k", now)
        self.assertEqual(first["candidates"], [])
        self.assertFalse(state.get("sw01", {}).get("fired"))
        second = S.deliver(Http(True), cfg(), state, decision, "https://example.invalid/h", "k", now)
        self.assertEqual(second["candidates"], ["TEST"])
        self.assertEqual(sent[0]["reason"], "sw01_candidate")
        self.assertIn(POLICY, state["sw01"]["fired"])
        third = S.decide(state, ctx(), now + 60)
        self.assertEqual(candidate(third), [])

    def test_empty_url_records_without_posting(self):
        state = {}
        now = epoch(NOW)
        decision = S.decide(state, ctx(), now)

        class Http:
            def post_webhook(self, url, body, headers=None):
                raise AssertionError("posted")

        out = S.deliver(Http(), cfg(), state, decision, "", "", now)
        self.assertEqual(out["candidates"], ["TEST"])
        self.assertIn(POLICY, state["sw01"]["fired"])


class RunIsolation(unittest.TestCase):
    def test_a_sw01_crash_does_not_stop_the_prefilter_or_change_threshold_reasons(self):
        from test_regressions import run_main, quiet_req, fresh_state, kill_patches, fake_tracked
        sent = []

        def post(self, url, body, h=None):
            sent.append(body.get("reason"))
            return True

        with tempfile.TemporaryDirectory() as d:
            fresh_state(d)
            patches = kill_patches(fake_tracked(), 4200) + [
                mock.patch.object(P.Http, "post_webhook", post),
                mock.patch.object(P.sw01, "decide", side_effect=SystemExit("sw01")),
            ]
            run = run_main(d, quiet_req, patches, dry=False, env={"SOLADA_WEBHOOK_URL": "https://example.invalid/h"})
            self.assertTrue(os.path.exists(os.path.join(d, "state.json")))
        self.assertEqual(run["data_failures"].get("sw01"), 1)
        self.assertIn("kill_line", [t["kind"] for t in run["triggers"]])
        self.assertTrue(sent)
        self.assertTrue(all(reason == "threshold" for reason in sent))
        self.assertEqual(run["sw01"]["error"], "SystemExit")


if __name__ == "__main__":
    unittest.main()
