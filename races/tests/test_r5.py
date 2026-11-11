"""R5 gate tests from the v2 pre-registration, including the Monday call order."""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from races.budget import Budget
from races.http import HTTPResult
from races.jupiter import Jupiter
from races.ledger import Ledger
from races.rules import r5
from races.run import load_config

ROOT = Path(__file__).resolve().parents[2]
CFG, _locked = load_config(ROOT / "races")
NOW = datetime(2026, 11, 16, 0, 12, tzinfo=timezone.utc)
WSOL = "So11111111111111111111111111111111111111112"


def _token(**over):
    base = {
        "id": "PassMint11111111111111111111111111111111",
        "symbol": "PASS",
        "isVerified": True,
        "mcap": 30_000_000,
        "fdv": 40_000_000,
        "liquidity": 2_000_000,
        "organicScore": 70,
        "tags": ["meme"],
        "launchpad": "",
        "firstPool": {"createdAt": "2026-01-01T00:00:00Z"},
        "stats24h": {"priceChange": 20, "volumeChange": 150, "buyVolume": 1000, "buyOrganicVolume": 80},
        "stats6h": {"priceChange": 2},
        "usdPrice": 50,
    }
    base.update(over)
    return base


class Gate(unittest.TestCase):
    def test_60_day_old_token_is_rejected(self):
        created = (NOW - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ok, reason = r5.gate(_token(firstPool={"createdAt": created}), CFG, NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "age")

    def test_stable_tag_and_cbbtc_denylist_are_rejected(self):
        ok, reason = r5.gate(_token(tags=["stable"]), CFG, NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "tag")
        ok, reason = r5.gate(_token(symbol="cbBTC", tags=[]), CFG, NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "denylist")

    def test_mcap_fdv_028_is_rejected(self):
        ok, reason = r5.gate(_token(mcap=28_000_000, fdv=100_000_000), CFG, NOW)
        self.assertFalse(ok)
        self.assertEqual(reason, "mcap_fdv")
        self.assertAlmostEqual(28_000_000 / 100_000_000, 0.28)

    def test_meme_tagged_passing_token_is_accepted(self):
        ok, reason = r5.gate(_token(tags=["meme"]), CFG, NOW)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "ok")

    def test_plus_65_percent_in_24h_does_not_enter(self):
        token = _token()
        token["stats24h"] = dict(token["stats24h"], priceChange=65)
        ok, reason = r5.signal(token, CFG)
        self.assertFalse(ok)
        self.assertEqual(reason, "price_24h")

    def test_choice_30_meme_launchpad_and_untagged(self):
        self.assertEqual(r5.bucket(_token(tags=["meme"], launchpad=""), CFG), "meme")
        self.assertEqual(r5.bucket(_token(tags=[], launchpad="pump.fun"), CFG), "meme")
        self.assertEqual(r5.bucket(_token(tags=[], launchpad=""), CFG), "untagged")
        self.assertEqual(r5.bucket(_token(tags=[], launchpad="orca"), CFG), "non_meme")


class MondayOrder(unittest.TestCase):
    def test_monday_0012_is_two_universe_one_search_then_at_most_two_exits(self):
        crash = "CrashMint111111111111111111111111111111"
        health = "HealthMint1111111111111111111111111111"
        entry = "EntryMint111111111111111111111111111111"
        passed = _token(id=entry, symbol="ENTRY", usdPrice=40)
        transport = _MondayTransport(passed, crash, health)
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(tmp, {WSOL, crash, health, entry})
            budget = Budget(CFG)
            logs = []
            jup = Jupiter(CFG, transport, budget, lambda *a: logs.append(a))
            state = {"wallets": {"R5": {
                "cash_sol": 5.0,
                "positions": [_pos(crash, "CRASH"), _pos(health, "HLTH")],
                "entered": [],
                "fills_by_day": {},
                "cooldowns": {},
            }}, "r5": {"rr": 0, "tokens": [], "freeze_slot": None, "below_min": True, "week": None}}
            ctx = type("Ctx", (), {})()
            ctx.cfg = CFG
            ctx.slot = NOW
            ctx.slot_iso = "2026-11-16T00:12:00Z"
            ctx.state = state
            ctx.jup = jup
            ctx.ledger = ledger
            ctx.transport = transport
            ctx.log = lambda *a: logs.append(a)
            r5.on_run(ctx)
        kinds = [row["kind"] for row in jup.calls]
        self.assertEqual(kinds[:3], ["universe", "universe", "search"])
        self.assertLessEqual(len(kinds), 5)
        self.assertTrue(all(kind == "exit_quote" for kind in kinds[3:]))
        self.assertLessEqual(len(kinds) - 3, 2)
        self.assertNotIn("entry_quote", kinds)
        self.assertIn("toptraded/24h", jup.calls[0]["url"])
        self.assertIn("toporganicscore/24h", jup.calls[1]["url"])
        self.assertIn("/tokens/v2/search", jup.calls[2]["url"])
        self.assertEqual(budget.used["R5"], len(kinds))


class FrozenLists(unittest.TestCase):
    def test_workflow_and_config_freeze_the_same_denylist_and_tags(self):
        text = (ROOT / ".github/workflows/races.yml").read_text(encoding="utf-8")
        w = CFG["wallets"]["R5"]
        self.assertIn("R5_DENYLIST: " + ",".join(w["denylist_symbols"]), text)
        self.assertIn("R5_DENY_SUBSTRINGS: " + ",".join(w["symbol_substrings"]), text)
        self.assertIn("R5_REJECT_TAGS: " + ",".join(w["reject_tags"]), text)


def _pos(mint, symbol):
    return {
        "symbol": symbol, "mint": mint, "pool_id": None,
        "opened_at": "2026-11-15T00:12:00Z", "entry_slot": "2026-11-15T00:12:00Z",
        "tokens_raw": "1000000", "decimals": 6, "size_sol": 0.5,
        "entry_price_sol": 0.5, "entry_outlay_sol": 0.53, "entry_cost_bps": 60,
        "mark_price_sol": 0.5, "mark_sol": 0.5, "failed_sell_quotes": 0,
        "mark_stale": False, "reserve_sol": 80, "liquidity_usd": 2_000_000, "meme": False,
    }


class _MondayTransport:
    def __init__(self, passed, crash, health):
        self.passed = passed
        self.crash = crash
        self.health = health

    def request(self, method, url, headers=None, body=None):
        if "toptraded/24h" in url:
            return HTTPResult(200, [self.passed], None)
        if "toporganicscore/24h" in url:
            return HTTPResult(200, [], None)
        if "/tokens/v2/search" in url:
            return HTTPResult(200, [
                {"id": WSOL, "usdPrice": 100, "symbol": "SOL"},
                {"id": self.crash, "usdPrice": 1, "symbol": "CRASH", "liquidity": 2_000_000},
                {"id": self.health, "usdPrice": 50, "symbol": "HLTH", "liquidity": 2_000_000},
                self.passed,
            ], None)
        if "/swap/v1/quote" in url:
            return HTTPResult(200, {
                "inAmount": "1000000", "outAmount": "100000000", "priceImpactPct": "0.01",
            }, None)
        return HTTPResult(404, None, None)


class Hash(unittest.TestCase):
    def test_v2_file_matches_the_pinned_sha(self):
        import hashlib
        digest = hashlib.sha256((ROOT / "races/prereg_races_v2.md").read_bytes()).hexdigest()
        self.assertEqual(digest, CFG["prereg"]["sha256_expected"])
        self.assertEqual(digest, "5f52f68845e93c7338c08b1a898e29d6a96560c5f108916000e53e708f578e66")
        v1 = hashlib.sha256((ROOT / "races/prereg_races.md").read_bytes()).hexdigest()
        self.assertEqual(v1, CFG["prereg"]["sha256_v1"])
