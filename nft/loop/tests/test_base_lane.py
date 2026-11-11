"""Base lane budget. ETH keeps a 280-call ceiling; Base spends what ETH leaves, up to 200."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import opensea
import pricing as P
import snapshot
import test_nh04 as nh

ROOT = nh.ROOT
NOW = nh.NOW
GAS = {"ethereum": 10 ** 9, "base": 10 ** 9}
END = NOW + 400 * 86400
# Measured on v0.3 (04a1ee7b) for n=30, pass_every=2: 1 list + 19 screened * 4 reads.
V03_PASS_TARGET_CALLS = 77


def _addr(n):
    return "0x%040x" % (n + 1)


def _row(chain, slug, n):
    return {"collection": slug, "contracts": [{"address": _addr(n), "chain": chain}]}


class Market:
    """Under-cap collections. Slugs in `passes` clear the screen; the rest fail sales."""

    def __init__(self, eth, base, passes, cap=None):
        self.eth = list(eth)
        self.base = list(base)
        self.passes = set(passes)
        self.calls = 0
        self.max_calls = P.MAX_CALLS if cap is None else cap
        self.raw = []
        self.rate = {"limit": "120", "remaining": "100", "reset": "1"}
        self.log = []

    def _tick(self, kind, chain):
        if self.calls >= self.max_calls:
            raise opensea.Budget()
        self.calls += 1
        self.log.append((kind, chain))

    def list_collections(self, chain, cursor):
        self._tick("list", chain)
        rows = self.eth if chain == "ethereum" else self.base
        if cursor:
            return [], None
        return list(rows), None

    def _chain(self, slug):
        return "base" if str(slug).startswith("b") else "ethereum"

    def listings(self, slug):
        self._tick("listings", self._chain(slug))
        return [nh.order(0.1 + k * 0.01, END, digest="%02x" % k) for k in range(5)]

    def collection(self, slug):
        self._tick("collection", self._chain(slug))
        return {"created_date": "2020-01-01", "fees": [{"fee": 1, "required": True}]}

    def offers(self, slug, cursor):
        self._tick("offers", self._chain(slug))
        rows = [nh.order(0.09, END, role="offer", offerer="0x%040x" % (800 + k), digest="%02x" % (40 + k))
                for k in range(3)]
        return rows, None

    def events(self, slug, after, cursor):
        self._tick("events", self._chain(slug))
        count = 30 if slug in self.passes else 5
        rows = [{"event_type": "sale", "event_timestamp": int(NOW - 1000 - k * 1500),
                 "buyer": "0x%040x" % (2000 + k), "seller": "0x%040x" % (6000 + (k % 7))}
                for k in range(count)]
        return rows, None

    def contract(self, chain, address):
        raise AssertionError("item type is on the listing")


def _eth_rows(n):
    return [_row("ethereum", "e%d" % i, i) for i in range(n)]


def _base_rows(n, start=1000):
    return [_row("base", "b%d" % i, start + i) for i in range(n)]


def _run(fake, data, when=None):
    snapshot.run(data, fake, NOW if when is None else when, "sha", GAS, eth_usd=2000)


def _load(data):
    root = os.path.join(data, "nft-pilot")
    with open(os.path.join(root, "status.json")) as fh:
        status = json.load(fh)
    with open(os.path.join(root, "books.json")) as fh:
        books = json.load(fh)
    return status, books


def _v03_screen_calls(fake):
    """Call count of the pre-split walk: ethereum and base share one pass target and no lane ceiling."""
    state = snapshot._fresh_state()
    caches = {"listings": {}, "offers": {}, "offer_pages": {}}
    quotes = {chain: P.gas_quote(GAS[chain], [], chain, NOW) for chain in P.CHAINS}
    details = {}

    def detail(slug):
        if slug not in details:
            details[slug] = fake.collection(slug)
        detail.usd = 2000
        return details[slug]

    detail.usd = 2000
    snapshot._screen(
        fake, quotes, detail, NOW, state, caches, snapshot._day(NOW),
        chains=P.CHAINS, pass_target=P.PASS_TARGET,
    )
    return fake.calls


class BaseLaneBudgetTests(unittest.TestCase):
    def test_split_keeps_the_global_cap(self):
        self.assertEqual(P.ETH_MAX_CALLS + P.BASE_MAX_CALLS, P.MAX_CALLS)
        self.assertEqual(P.ETH_MAX_CALLS, 280)
        self.assertEqual(P.BASE_MAX_CALLS, 200)
        self.assertEqual(P.BASE_PASS_TARGET, 5)
        self.assertEqual(P.MAX_CALLS, 480)
        self.assertEqual(P.PASS_TARGET, 10)
        self.assertTrue(P.RULE.startswith("v0.3 "))
        self.assertIn("max_calls=480", P.RULE)
        self.assertEqual(P.ETH_CAPPED_RULE.split(" ", 1)[1], P.RULE.split(" ", 1)[1])
        self.assertTrue(P.ETH_CAPPED_RULE.startswith("v0.3-eth280 "))
        self.assertTrue(P.BASE_RULE.startswith("v0.4-base "))
        self.assertEqual(P.STATE_VERSION, "v0.3")
        self.assertEqual(P.BASE_STATE_VERSION, "v0.4-base")

    def test_eth_reaches_target_under_280_and_base_still_runs(self):
        eth = _eth_rows(10)
        base = _base_rows(3)
        passes = {"e%d" % i for i in range(10)} | {"b%d" % i for i in range(3)}
        data = tempfile.mkdtemp()
        fake = Market(eth, base, passes)
        _run(fake, data)
        status, books = _load(data)
        eth_row = status["by_chain"]["ethereum"]
        base_row = status["by_chain"]["base"]
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["stop"], "pass_target")
        self.assertEqual(status["floor_passing_this_run"], 10)
        self.assertLess(eth_row["calls"], P.ETH_MAX_CALLS)
        self.assertEqual(eth_row["stop"], "pass_target")
        self.assertEqual(eth_row["passing"], 10)
        self.assertGreaterEqual(base_row["calls"], 1)
        self.assertGreaterEqual(base_row["pages"], 1)
        self.assertEqual(base_row["passing"], 3)
        self.assertIn(("list", "base"), fake.log)
        self.assertEqual(status["rule"], P.RULE)
        self.assertNotIn("v0.3-eth280", status["rule"])
        self.assertLessEqual(status["opensea_calls"], P.MAX_CALLS)
        self.assertEqual(eth_row["calls"] + base_row["calls"], status["opensea_calls"])
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(books["first_runs"], [10])
        self.assertEqual(books["base"]["version"], "v0.4-base")
        self.assertEqual(books["base"]["run_passes"], [3])
        eth_ids = json.dumps(books["passes"])
        self.assertNotIn("eip155:8453", eth_ids)
        self.assertTrue(books["base"]["passes"])
        # Next hour fills both lanes. Base units carry the v0.4-base count and stay out of the v0.3 median.
        _run(Market(eth, base, passes), data, NOW + 3600)
        status2, _books2 = _load(data)
        units = [json.loads(line) for line in open(os.path.join(data, "nft-pilot", "units.jsonl")) if line.strip()]
        base_units = [u for u in units if u["rule"] == P.BASE_RULE]
        eth_units = [u for u in units if u["rule"] == P.RULE]
        self.assertEqual(len(base_units), 3)
        self.assertEqual(len(eth_units), 10)
        self.assertEqual(status2["units"], 10)
        self.assertEqual(status2["floor_passing_this_run"], 10)
        self.assertGreaterEqual(status2["by_chain"]["base"]["calls"], 1)

    def test_eth_stops_at_280_and_base_still_gets_calls(self):
        eth = _eth_rows(90)
        base = _base_rows(80)
        fake = Market(eth, base, set())
        data = tempfile.mkdtemp()
        _run(fake, data)
        status, books = _load(data)
        eth_row = status["by_chain"]["ethereum"]
        base_row = status["by_chain"]["base"]
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["partial"])
        self.assertEqual(status["stop"], "call_cap")
        self.assertEqual(eth_row["stop"], "call_cap")
        self.assertEqual(eth_row["calls"], P.ETH_MAX_CALLS)
        self.assertLess(eth_row["passing"], P.PASS_TARGET)
        self.assertGreaterEqual(base_row["calls"], 1)
        self.assertLessEqual(base_row["calls"], P.BASE_MAX_CALLS)
        self.assertEqual(base_row["calls"], P.BASE_MAX_CALLS)
        self.assertEqual(base_row["stop"], "call_cap")
        self.assertEqual(status["opensea_calls"], P.MAX_CALLS)
        self.assertEqual(eth_row["calls"] + base_row["calls"], status["opensea_calls"])
        self.assertTrue(status["rule"].startswith("v0.3-eth280 "))
        self.assertEqual(status["rule"].split(" ", 1)[1], P.RULE.split(" ", 1)[1])
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(books["base"]["version"], "v0.4-base")
        self.assertNotIn("eip155:8453", json.dumps(books["passes"]))

    def test_base_stop_is_not_call_cap_when_base_makes_no_calls(self):
        # ETH consumes a global cap of 280, so nothing is left for Base.
        eth = _eth_rows(90)
        fake = Market(eth, _base_rows(5), set(), cap=P.ETH_MAX_CALLS)
        data = tempfile.mkdtemp()
        _run(fake, data)
        status, _books = _load(data)
        base_row = status["by_chain"]["base"]
        self.assertEqual(base_row["calls"], 0)
        self.assertEqual(base_row["pages"], 0)
        self.assertNotEqual(base_row["stop"], "call_cap")
        self.assertEqual(base_row["stop"], "not_run_budget")
        self.assertEqual(status["by_chain"]["ethereum"]["calls"], P.ETH_MAX_CALLS)
        self.assertEqual(status["by_chain"]["ethereum"]["stop"], "call_cap")
        self.assertNotIn(("list", "base"), fake.log)

    def test_eth_pass_target_stays_within_ten_percent_of_v03_calls(self):
        ref = nh.NH04Tests()._universe_fake(n=30, pass_every=2)
        ref_calls = _v03_screen_calls(ref)
        self.assertEqual(ref_calls, V03_PASS_TARGET_CALLS)
        fresh = nh.NH04Tests()._universe_fake(n=30, pass_every=2)
        data = tempfile.mkdtemp()
        _run(fresh, data)
        status, _books = _load(data)
        eth_calls = status["by_chain"]["ethereum"]["calls"]
        self.assertEqual(status["stop"], "pass_target")
        self.assertEqual(status["floor_passing_this_run"], P.PASS_TARGET)
        self.assertLess(eth_calls, P.ETH_MAX_CALLS)
        self.assertEqual(status["rule"], P.RULE)
        self.assertLessEqual(abs(eth_calls - ref_calls) / ref_calls, 0.10)
        # The shared v0.3 walk never reached Base on this fixture. The lane still does.
        self.assertGreaterEqual(status["by_chain"]["base"]["calls"], 1)
        self.assertLessEqual(status["opensea_calls"], P.MAX_CALLS)

    def test_base_stops_at_five_passes_and_eight_pages(self):
        passes = {"e%d" % i for i in range(10)} | {"b%d" % i for i in range(8)}
        data = tempfile.mkdtemp()
        _run(Market(_eth_rows(10), _base_rows(8), passes), data)
        status, books = _load(data)
        self.assertEqual(status["by_chain"]["base"]["passing"], P.BASE_PASS_TARGET)
        self.assertEqual(status["by_chain"]["base"]["stop"], "pass_target")
        self.assertEqual(books["base"]["run_passes"], [P.BASE_PASS_TARGET])
        self.assertEqual(status["floor_passing_this_run"], P.PASS_TARGET)

        class Pages(Market):
            def list_collections(self, chain, cursor):
                self._tick("list", chain)
                if chain == "ethereum":
                    return _eth_rows(10), None
                page = int(cursor or 0)
                return [], None if page >= P.MAX_LIST_PAGES - 1 else str(page + 1)

        data = tempfile.mkdtemp()
        fake = Pages(_eth_rows(10), [], passes)
        _run(fake, data)
        status, _books = _load(data)
        self.assertEqual(status["stop"], "pass_target")
        self.assertEqual(status["by_chain"]["base"]["pages"], P.MAX_LIST_PAGES)
        self.assertEqual(status["by_chain"]["base"]["stop"], "depth")
        self.assertEqual(sum(1 for kind, chain in fake.log if kind == "list" and chain == "base"), P.MAX_LIST_PAGES)

    def test_leftover_base_clock_does_not_reset_eth(self):
        data = tempfile.mkdtemp()
        root = os.path.join(data, "nft-pilot")
        os.makedirs(root)
        state = {
            "state_version": "v0.3", "pilot_start": "2026-11-01",
            "passes": {"2026-11-01": ["nft_collection:eip155:1:" + _addr(3)]},
            "first_runs": [8, 8, 8], "complete_days": ["2026-11-01"],
            "pending": [], "seen": {}, "offers": {}, "gas": [],
            "v03_init_at": 10, "first_measured_at": 10, "early_distinct": [],
            "measured_days": ["2026-11-01"], "usd_runs": {"chainlink": 3, "fallback": 0},
            "base": {
                "version": "v0.4-base", "init_at": 10, "pending": [],
                "passes": {}, "run_passes": [],
            },
        }
        with open(os.path.join(root, "books.json"), "w") as fh:
            json.dump(state, fh)
        _run(Market(_eth_rows(2), _base_rows(1), {"e0", "e1", "b0"}), data)
        _status, books = _load(data)
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(books["v03_init_at"], 10)
        self.assertEqual(books["pilot_start"], "2026-11-01")
        self.assertEqual(books["first_runs"], [8, 8, 8])
        self.assertEqual(books["base"]["version"], "v0.4-base")
        self.assertEqual(books["base"]["init_at"], 10)

    def test_privacy_guard_rejects_a_non_count_chain_field(self):
        data = tempfile.mkdtemp()
        _run(Market(_eth_rows(2), _base_rows(1), {"e0", "e1", "b0"}), data)
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "nft-pilot"))
        status_path = os.path.join(data, "nft-pilot", "status.json")
        dest = os.path.join(root, "nft-pilot", "status.json")
        with open(status_path) as fh:
            blob = fh.read()
        with open(dest, "w") as fh:
            fh.write(blob)
        guard = os.path.join(ROOT, "scripts", "privacy-guard.sh")
        env = os.environ.copy()
        env["PRIVACY_IDENTITY_HASHES"] = hashlib.sha256(b"not-in-the-status").hexdigest()
        env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        env.pop("PRIVACY_GUARD_PATTERNS", None)
        env.pop("PRIVACY_IDENTITY_KEY", None)
        good = subprocess.run(
            ["bash", guard, "--root", root, "nft-pilot/status.json"],
            text=True, capture_output=True, env=env,
        )
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("privacy-guard: OK", good.stdout)
        status = json.loads(blob)
        status["by_chain"]["base"]["stop"] = "collection-slug"
        with open(dest, "w") as fh:
            json.dump(status, fh)
        bad = subprocess.run(
            ["bash", guard, "--root", root, "nft-pilot/status.json"],
            text=True, capture_output=True, env=env,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("by_chain fields are not counts", bad.stderr)
        self.assertNotIn("collection-slug", bad.stderr)
        self.assertRaises(RuntimeError, snapshot.public_by_chain, status["by_chain"])


if __name__ == "__main__":
    unittest.main()
