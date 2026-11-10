"""Base lane. ETH stays on the v0.3 walk; Base runs after it, every run."""
import hashlib
import json
import os
import shutil
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


def _addr(n):
    return "0x%040x" % (n + 1)


def _row(chain, slug, n):
    return {"collection": slug, "contracts": [{"address": _addr(n), "chain": chain}]}


class Market:
    """Under-cap collections. `passes` slugs clear the v0.3 screen; the rest fail sales."""

    def __init__(self, eth, base, passes, base_error=None):
        self.eth = list(eth)
        self.base = list(base)
        self.passes = set(passes)
        self.base_error = base_error
        self.calls = 0
        self.max_calls = P.MAX_CALLS
        self.raw = []
        self.rate = {"limit": "120", "remaining": "100", "reset": "1"}
        self.log = []
        self.base_calls = 0

    def _tick(self, kind, chain):
        if self.calls >= self.max_calls:
            raise opensea.Budget()
        self.calls += 1
        if chain == "base":
            self.base_calls += 1
        self.log.append((kind, chain))

    def list_collections(self, chain, cursor):
        self._tick("list", chain)
        if chain == "base" and self.base_error is not None:
            raise opensea.OpenSeaError(self.base_error)
        rows = self.eth if chain == "ethereum" else self.base
        if cursor:
            return [], None
        return list(rows), None

    def listings(self, slug):
        self._tick("listings", "base" if slug.startswith("b") or slug.startswith("p") else "ethereum")
        return [nh.order(0.1 + k * 0.01, END, digest="%02x" % k) for k in range(5)]

    def collection(self, slug):
        chain = "base" if slug.startswith("b") or slug.startswith("p") else "ethereum"
        self._tick("collection", chain)
        return {"created_date": "2020-01-01", "fees": [{"fee": 1, "required": True}]}

    def offers(self, slug, cursor):
        chain = "base" if slug.startswith("b") or slug.startswith("p") else "ethereum"
        self._tick("offers", chain)
        rows = [nh.order(0.09, END, role="offer", offerer="0x%040x" % (800 + k), digest="%02x" % (40 + k))
                for k in range(3)]
        return rows, None

    def events(self, slug, after, cursor):
        chain = "base" if slug.startswith("b") or slug.startswith("p") else "ethereum"
        self._tick("events", chain)
        count = 30 if slug in self.passes else 5
        rows = [{"event_type": "sale", "event_timestamp": int(NOW - 1000 - k * 1500),
                 "buyer": "0x%040x" % (2000 + k), "seller": "0x%040x" % (6000 + (k % 7))}
                for k in range(count)]
        return rows, None

    def contract(self, chain, address):
        raise AssertionError("item type is on the listing")


def _eth_rows(n, prefix="e"):
    return [_row("ethereum", "%s%d" % (prefix, i), i) for i in range(n)]


def _base_rows(n, start=100):
    return [_row("base", "b%d" % i, start + i) for i in range(n)]


def _run(fake, data, when=None, **kw):
    snapshot.run(data, fake, NOW if when is None else when, "sha", GAS, eth_usd=2000, **kw)


def _load(data):
    root = os.path.join(data, "nft-pilot")
    with open(os.path.join(root, "status.json")) as fh:
        status = json.load(fh)
    with open(os.path.join(root, "books.json")) as fh:
        books = json.load(fh)
    return status, books


def _eth_slice(status, books):
    """Fields the ETH lane owns. Base calls, Base passes, and outages are left out."""
    return {
        "status": status["status"],
        "partial": status["partial"],
        "stop": status["stop"],
        "reason": status["reason"],
        "floor_passing_this_run": status["floor_passing_this_run"],
        "screened": status["screened"],
        "rejections": status["rejections"],
        "flags": status["flags"],
        "pending_served": status["pending_served"],
        "pending_deferred": status["pending_deferred"],
        "passers_footnoted": status["passers_footnoted"],
        "rule": status["rule"],
        "early_kill": status["early_kill"],
        "ethereum": status["by_chain"]["ethereum"],
        "pending": books["pending"],
        "passes": books["passes"],
        "first_runs": books["first_runs"],
        "pilot_start": books["pilot_start"],
        "state_version": books["state_version"],
        "v03_init_at": books["v03_init_at"],
        "early_distinct": books["early_distinct"],
        "complete_days": books["complete_days"],
        "measured_days": books["measured_days"],
        "usd_runs": books["usd_runs"],
        "seen": {k: v for k, v in books["seen"].items() if ":eip155:1:" in k},
        "offers": {k: v for k, v in books["offers"].items() if ":eip155:1:" in k},
    }


class BaseLaneTests(unittest.TestCase):
    def test_constants_leave_the_eth_lane_frozen(self):
        self.assertEqual(P.MAX_CALLS, 480)
        self.assertEqual(P.PASS_TARGET, 10)
        self.assertEqual(P.MAX_LIST_PAGES, 8)
        self.assertEqual(P.PACE_PER_MINUTE, 60)
        self.assertEqual(P.RATE_RESERVE, 24)
        self.assertEqual(P.STATE_VERSION, "v0.3")
        self.assertEqual((P.WINDOW_DAYS, P.MIN_SALES, P.MIN_BUYERS, P.MAX_BUYER_SHARE), (7, 30, 10, 0.20))
        self.assertEqual(P.BASE_PASS_TARGET, 5)
        self.assertEqual(P.BASE_MAX_CALLS, 200)
        self.assertEqual(P.BASE_STATE_VERSION, "v0.4-base")
        self.assertTrue(P.RULE.startswith("v0.3 "))
        self.assertTrue(P.BASE_RULE.startswith("v0.4-base "))
        self.assertEqual(P.BASE_RULE.split(" ", 1)[1], P.RULE.split(" ", 1)[1])

    def test_eth_output_matches_with_base_lane_off(self):
        """ETH output is identical whether the Base lane runs. The lane is what lists Base."""
        eth = _eth_rows(10)
        base = _base_rows(3)
        passes = {"e%d" % i for i in range(10)} | {"b%d" % i for i in range(3)}
        off = Market(eth, base, passes)
        on = Market(eth, base, passes)
        data_off, data_on = tempfile.mkdtemp(), tempfile.mkdtemp()
        _run(off, data_off, base_lane=False)
        _run(on, data_on, base_lane=True)
        st_off, bk_off = _load(data_off)
        st_on, bk_on = _load(data_on)
        self.assertEqual(_eth_slice(st_off, bk_off), _eth_slice(st_on, bk_on))
        self.assertEqual(st_off["floor_passing_this_run"], 10)
        self.assertEqual(st_off["by_chain"]["ethereum"]["stop"], "pass_target")
        self.assertNotIn(("list", "base"), off.log)
        self.assertEqual(st_off["by_chain"]["base"]["pages"], 0)
        self.assertGreaterEqual(st_on["by_chain"]["base"]["pages"], 1)
        self.assertIn(("list", "base"), on.log)
        self.assertEqual(st_on["by_chain"]["base"]["passing"], 3)
        self.assertTrue(st_on["by_chain"]["base"]["requested"])
        self.assertEqual(bk_on["state_version"], "v0.3")
        self.assertEqual(bk_on["base"]["version"], "v0.4-base")
        self.assertEqual(bk_on["base"]["init_at"], int(NOW))
        self.assertEqual(bk_off["first_runs"], bk_on["first_runs"])
        self.assertNotIn("b0", json.dumps(bk_on["passes"]))
        eth_log = [item for item in on.log if item[1] == "ethereum"]
        self.assertEqual([item for item in off.log if item[1] == "ethereum"], eth_log)

    def test_base_page_when_eth_pending_fills_target(self):
        """Ten ETH pending re-checks, then ten new ETH passes, and Base still gets a page."""
        eth = _eth_rows(10)
        passes = {"e%d" % i for i in range(10)}
        first = Market(eth, [], passes)
        data = tempfile.mkdtemp()
        _run(first, data)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(len(books["pending"]), 10)
        saved = tempfile.mkdtemp()
        shutil.copytree(os.path.join(data, "nft-pilot"), os.path.join(saved, "nft-pilot"))
        off = Market(eth, _base_rows(2), passes | {"b0", "b1"})
        on = Market(eth, _base_rows(2), passes | {"b0", "b1"})
        _run(off, saved, NOW + 3600, base_lane=False)
        _run(on, data, NOW + 3600, base_lane=True)
        st_off, _bk_off = _load(saved)
        st_on, bk_on = _load(data)
        self.assertEqual(st_off["pending_served"], 10)
        self.assertEqual(st_on["pending_served"], 10)
        self.assertEqual(st_off["floor_passing_this_run"], 10)
        self.assertEqual(st_on["floor_passing_this_run"], 10)
        self.assertEqual(st_off["by_chain"]["ethereum"], st_on["by_chain"]["ethereum"])
        self.assertNotIn(("list", "base"), off.log)
        self.assertIn(("list", "base"), on.log)
        self.assertGreaterEqual(st_on["by_chain"]["base"]["pages"], 1)
        self.assertEqual(st_on["stop"], "pass_target")
        self.assertEqual(bk_on["state_version"], "v0.3")
        self.assertEqual(st_on["status"], "ok")

    def test_base_subcap_stops_base_and_keeps_the_run_green(self):
        eth = _eth_rows(10)
        base = _base_rows(80, start=500)
        passes = {"e%d" % i for i in range(10)}  # Base rows fail sales, so the 5 is not reached
        fake = Market(eth, base, passes)
        data = tempfile.mkdtemp()
        _run(fake, data)
        status, _books = _load(data)
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["partial"])
        self.assertEqual(status["stop"], "pass_target")
        self.assertEqual(status["floor_passing_this_run"], 10)
        self.assertEqual(status["by_chain"]["base"]["stop"], "call_cap")
        self.assertEqual(status["by_chain"]["base"]["calls"], P.BASE_MAX_CALLS)
        self.assertLessEqual(fake.base_calls, P.BASE_MAX_CALLS)
        self.assertEqual(fake.base_calls, P.BASE_MAX_CALLS)
        self.assertEqual(status["by_chain"]["ethereum"]["stop"], "pass_target")
        self.assertLess(status["opensea_calls"], P.MAX_CALLS)
        self.assertEqual(
            status["by_chain"]["ethereum"]["calls"] + status["by_chain"]["base"]["calls"],
            status["opensea_calls"],
        )
        self.assertEqual(snapshot.fail_on_published_status(os.path.join(data, "nft-pilot", "status.json")), 0)

    def test_base_429_counts_outage_and_leaves_eth(self):
        eth = _eth_rows(4)
        passes = {"e%d" % i for i in range(4)}
        quiet = Market(eth, [], passes)
        noisy = Market(eth, _base_rows(1), passes, base_error=429)
        data_ok, data_bad = tempfile.mkdtemp(), tempfile.mkdtemp()
        _run(quiet, data_ok)
        _run(noisy, data_bad)
        ok, ok_books = _load(data_ok)
        bad, bad_books = _load(data_bad)
        self.assertEqual(_eth_slice(ok, ok_books), _eth_slice(bad, bad_books))
        self.assertEqual(bad["status"], "ok")
        self.assertFalse(bad["partial"])
        self.assertIsNone(bad["reason"])
        self.assertEqual(bad["outage_runs"], 1)
        self.assertEqual(ok["outage_runs"], 0)
        self.assertEqual(bad_books["outage_at"][0]["reason"], "http 429")
        self.assertEqual(bad_books["first_runs"], ok_books["first_runs"])
        self.assertEqual(bad_books["passes"], ok_books["passes"])
        self.assertEqual(bad_books["v03_init_at"], ok_books["v03_init_at"])
        self.assertEqual(bad["by_chain"]["base"]["requested"], True)
        self.assertIsNone(bad["by_chain"]["base"]["stop"])
        self.assertEqual(snapshot.fail_on_published_status(os.path.join(data_bad, "nft-pilot", "status.json")), 0)
        self.assertEqual(bad_books["base"]["run_passes"], [])

        class Budget429(Market):
            """The live client turns a final HTTP 429 into Budget and records the status."""

            def list_collections(self, chain, cursor):
                if chain == "base":
                    self.calls += 1
                    self.last_error_status = 429
                    self.log.append(("list", "base"))
                    raise opensea.Budget()
                return Market.list_collections(self, chain, cursor)

        data_budget = tempfile.mkdtemp()
        _run(Budget429(eth, [], passes), data_budget)
        budget, budget_books = _load(data_budget)
        self.assertEqual(_eth_slice(ok, ok_books), _eth_slice(budget, budget_books))
        self.assertEqual(budget["status"], "ok")
        self.assertEqual(budget["outage_runs"], 1)
        self.assertEqual(budget_books["outage_at"][0]["reason"], "http 429")
        self.assertEqual(budget["by_chain"]["base"]["stop"], "call_cap")
        self.assertEqual(snapshot.fail_on_published_status(os.path.join(data_budget, "nft-pilot", "status.json")), 0)

    def test_base_pending_rechecks_count_toward_five(self):
        data = tempfile.mkdtemp()
        root = os.path.join(data, "nft-pilot")
        os.makedirs(root)
        pending = []
        for i in range(6):
            pending.append({
                "asset_id": "nft_collection:eip155:8453:" + _addr(900 + i),
                "slug": "p%d" % i, "chain": "base", "snapshot_t": "2026-11-09T00:00:00Z",
                "seasoned": False, "floor_eth": 0.1, "floor_usd": 200.0, "tries": 0,
                "fees": [{"fee": 1, "required": True}],
                "book": {"digests": [], "top_bid_eth": 0.09, "bidders_near_top": 3},
                "top_buyer_share": 0.1,
                "counts": {"sales_7d": 30, "buyers_7d": 30, "listings": 5},
            })
        state = {
            "state_version": "v0.3", "pilot_start": "2026-11-01",
            "passes": {"2026-11-01": ["nft_collection:eip155:1:" + _addr(1)]},
            "first_runs": [8, 8, 8], "complete_days": ["2026-11-01"],
            "pending": [], "seen": {}, "offers": {}, "gas": [],
            "v03_init_at": 10, "first_measured_at": 10, "early_distinct": [],
            "measured_days": ["2026-11-01"], "usd_runs": {"chainlink": 3, "fallback": 0},
            "base": {
                "version": "v0.4-base", "init_at": 10, "pending": pending,
                "passes": {}, "run_passes": [],
            },
        }
        with open(os.path.join(root, "books.json"), "w") as fh:
            json.dump(state, fh)
        fake = Market(_eth_rows(0), _base_rows(4), set())
        _run(fake, data)
        status, books = _load(data)
        self.assertNotIn(("list", "base"), fake.log)
        self.assertEqual(status["by_chain"]["base"]["pages"], 0)
        self.assertEqual(status["by_chain"]["base"]["passing"], 0)
        self.assertEqual(status["by_chain"]["base"]["stop"], "pass_target")
        self.assertEqual([p["slug"] for p in books["base"]["pending"]], ["p5"])
        self.assertEqual(books["first_runs"], [8, 8, 8])
        self.assertEqual(books["passes"]["2026-11-01"], ["nft_collection:eip155:1:" + _addr(1)])
        self.assertEqual(books["v03_init_at"], 10)
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(books["pilot_start"], "2026-11-01")
        with open(os.path.join(root, "units.jsonl")) as fh:
            units = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(units), 5)
        self.assertTrue(all(u["rule"] == P.BASE_RULE for u in units))
        self.assertEqual(status["units"], 0)

    def test_base_count_starts_at_v04_without_resetting_eth(self):
        data = tempfile.mkdtemp()
        root = os.path.join(data, "nft-pilot")
        os.makedirs(root)
        state = {
            "state_version": "v0.3", "pilot_start": "2026-11-01",
            "passes": {"2026-11-01": ["nft_collection:eip155:1:" + _addr(3)]},
            "first_runs": [7, 7, 7], "complete_days": ["2026-11-01"],
            "pending": [], "seen": {}, "offers": {}, "gas": [],
            "v03_init_at": 10, "first_measured_at": 10, "early_distinct": [],
            "measured_days": ["2026-11-01"], "usd_runs": {"chainlink": 3, "fallback": 0},
            "base": {
                "version": "old", "init_at": 1,
                "pending": [], "passes": {"2020-01-01": ["old-base"]}, "run_passes": [9],
            },
        }
        with open(os.path.join(root, "books.json"), "w") as fh:
            json.dump(state, fh)
        _run(Market([], [], set()), data)
        _status, books = _load(data)
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(books["v03_init_at"], 10)
        self.assertEqual(books["pilot_start"], "2026-11-01")
        self.assertEqual(books["first_runs"], [7, 7, 7])
        self.assertEqual(books["passes"]["2026-11-01"], ["nft_collection:eip155:1:" + _addr(3)])
        self.assertEqual(books["base"]["version"], "v0.4-base")
        self.assertEqual(books["base"]["init_at"], int(NOW))
        self.assertNotIn("old-base", json.dumps(books["base"]["passes"]))
        self.assertEqual(books["base"]["run_passes"], [0])

    def test_base_lane_stops_at_eight_pages(self):
        class Pages:
            def __init__(self):
                self.calls = 0
                self.max_calls = P.MAX_CALLS
                self.rate = {"limit": None, "remaining": None, "reset": None}
                self.raw = []
                self.base_lists = 0

            def list_collections(self, chain, cursor):
                self.calls += 1
                if chain == "ethereum":
                    return _eth_rows(10), None
                self.base_lists += 1
                page = int(cursor or 0)
                return [], None if page >= 7 else str(page + 1)

            def listings(self, slug):
                self.calls += 1
                return [nh.order(0.1 + k * 0.01, END, digest="%02x" % k) for k in range(5)]

            def collection(self, slug):
                self.calls += 1
                return {"created_date": "2020-01-01", "fees": [{"fee": 1, "required": True}]}

            def offers(self, slug, cursor):
                self.calls += 1
                return [nh.order(0.09, END, role="offer", offerer="0x%040x" % (800 + k), digest="%02x" % (40 + k))
                        for k in range(3)], None

            def events(self, slug, after, cursor):
                self.calls += 1
                rows = [{"event_type": "sale", "event_timestamp": int(NOW - 1000 - k * 1500),
                         "buyer": "0x%040x" % (2000 + k), "seller": "0x%040x" % (6000 + (k % 7))}
                        for k in range(30)]
                return rows, None

            def contract(self, chain, address):
                raise AssertionError("item type is on the listing")

        data = tempfile.mkdtemp()
        fake = Pages()
        _run(fake, data)
        status, _books = _load(data)
        self.assertEqual(fake.base_lists, P.MAX_LIST_PAGES)
        self.assertEqual(status["by_chain"]["base"]["pages"], 8)
        self.assertEqual(status["by_chain"]["base"]["stop"], "depth")
        self.assertEqual(status["stop"], "pass_target")
        self.assertEqual(status["floor_passing_this_run"], 10)
        self.assertEqual(status["status"], "ok")

    def test_base_deadline_does_not_change_eth_stop(self):
        clock = {"t": 1_000_000.0}
        real = snapshot.time.time
        snapshot.time.time = lambda: clock["t"]
        try:
            class Late(Market):
                def events(self, slug, after, cursor):
                    rows, nxt = Market.events(self, slug, after, cursor)
                    if slug == "e9":
                        clock["t"] = 1_000_000.0 + snapshot.RUN_LIMIT_SECONDS + 5
                    return rows, nxt

            eth = _eth_rows(10)
            fake = Late(eth, _base_rows(2), {"e%d" % i for i in range(10)} | {"b0", "b1"})
            data = tempfile.mkdtemp()
            _run(fake, data)
        finally:
            snapshot.time.time = real
        status, _books = _load(data)
        self.assertEqual(status["stop"], "pass_target")
        self.assertIsNone(status["reason"])
        self.assertEqual(status["floor_passing_this_run"], 10)
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["by_chain"]["base"]["stop"], "deadline")
        self.assertEqual(status["by_chain"]["base"]["pages"], 0)
        self.assertEqual(status["by_chain"]["base"]["calls"], 0)
        self.assertNotIn(("list", "base"), fake.log)

    def test_privacy_guard_covers_by_chain_fields(self):
        eth = _eth_rows(2)
        data = tempfile.mkdtemp()
        _run(Market(eth, [], {"e0", "e1"}), data)
        root = tempfile.mkdtemp()
        shutil.copytree(os.path.join(data, "nft-pilot"), os.path.join(root, "nft-pilot"))
        guard = os.path.join(ROOT, "scripts", "privacy-guard.sh")
        env = os.environ.copy()
        env["PRIVACY_IDENTITY_HASHES"] = hashlib.sha256(b"not-in-the-status").hexdigest()
        env.pop("SIGHTLINE_PRIVATE_TOKEN", None)
        env.pop("PRIVACY_GUARD_PATTERNS", None)
        env.pop("PRIVACY_IDENTITY_KEY", None)
        proc = subprocess.run(
            ["bash", guard, "--root", root, "nft-pilot/status.json"],
            text=True, capture_output=True, env=env,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("privacy-guard: OK", proc.stdout)
        status_path = os.path.join(root, "nft-pilot", "status.json")
        status = json.load(open(status_path))
        self.assertIn("requested", status["by_chain"]["base"])
        self.assertIn("pages", status["by_chain"]["base"])
        self.assertIn("calls", status["by_chain"]["base"])
        self.assertIn("stop", status["by_chain"]["base"])
        status["by_chain"]["base"]["pages"] = "collection-slug"
        with open(status_path, "w") as fh:
            json.dump(status, fh)
        bad = subprocess.run(
            ["bash", guard, "--root", root, "nft-pilot/status.json"],
            text=True, capture_output=True, env=env,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("by_chain fields are not counts", bad.stderr)
        self.assertNotIn("collection-slug", bad.stderr)
        snapshot.public_by_chain(json.load(open(os.path.join(data, "nft-pilot", "status.json")))["by_chain"])
        self.assertRaises(RuntimeError, snapshot.public_by_chain, status["by_chain"])


if __name__ == "__main__":
    unittest.main()
