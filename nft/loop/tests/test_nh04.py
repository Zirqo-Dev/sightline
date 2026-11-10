"""Fixture tests for the NH-04 snapshot pilot. No live HTTP."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import opensea
import pricing as P
import snapshot

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
ADDR = "0x" + "11" * 20
STORE = "0x495f947276749ce646f68ac8c248420045cb7b5e"
RECIPIENT = "0x" + "22" * 20
OFFERER = "0x" + "cd" * 20
BUYER = "0x" + "d4" * 20
SELLER = "0x" + "e5" * 20
NOW = datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp()


def wei(eth):
    return str(int(round(eth * 10 ** 18)))


def order(price, end, role="listing", offerer=OFFERER, item=2, order_type=0, flagged=False, qty=1, n_items=1, digest="aa"):
    items = [{
        "itemType": item, "token": ADDR, "identifierOrCriteria": "1", "startAmount": "1", "endAmount": "1",
    } for _ in range(n_items)]
    body = {
        "status": "ACTIVE", "type": "basic", "flagged": flagged, "remaining_quantity": qty,
        "order_hash": "0x" + digest * 32,
        "protocol_data": {"parameters": {"orderType": order_type, "endTime": str(int(end)), "offerer": offerer, "offer": items}},
    }
    price_obj = {"currency": "ETH", "decimals": 18, "value": wei(price)}
    body["price"] = {"current": price_obj} if role == "listing" else price_obj
    return body


class NH04Tests(unittest.TestCase):
    def setUp(self):
        self._urlopen = urllib.request.urlopen
        def blocked(*_a, **_k):
            raise AssertionError("live call")
        urllib.request.urlopen = blocked

    def tearDown(self):
        urllib.request.urlopen = self._urlopen

    def test_cost_formula_and_fees_from_the_row(self):
        self.assertEqual(P.LEG_GAS_UNITS, 250_000)
        self.assertEqual(P.ROUND_TRIP_GAS_UNITS, 500_000)
        leg = P.leg_eth(10 ** 9)
        self.assertAlmostEqual(leg, 250_000 / 1e9)
        self.assertAlmostEqual(P.round_trip_eth(10 ** 9), leg * 2)
        got = P.round_trip(1.0, 0.0, 0.9, 0.01, 0.05, leg)
        entry = 1.0 + leg
        exit_v = 0.9 - 0.9 * 0.01 - 0.9 * 0.05 - leg
        self.assertAlmostEqual(got["entry_eth"], entry)
        self.assertAlmostEqual(got["exit_eth"], exit_v)
        self.assertAlmostEqual(got["entry_eth"] - 1.0, leg)
        self.assertAlmostEqual(0.9 * 0.94 - got["exit_eth"], leg)
        self.assertAlmostEqual(got["cost"], (entry - exit_v) / entry)
        self.assertFalse(got["unsellable"])
        with_buyer = P.round_trip(1.0, 0.02, 0.9, 0.01, 0.05, leg)
        self.assertGreater(with_buyer["entry_eth"], got["entry_eth"])
        self.assertEqual(P.fee_rates([{"fee": 2.5, "required": True}, {"fee": 5, "required": False}]), (0.025, 0.05))
        self.assertIsNone(P.fee_rates(None))
        other = P.fee_rates([{"fee": 7, "required": True}, {"fee": 1, "required": False}])
        self.assertNotEqual(other, (0.01, 0.0))

    def test_unsellable_stays_in_the_median(self):
        missing = P.round_trip(1.0, 0.0, None, 0.01, 0.05, 0.01)
        self.assertTrue(missing["unsellable"])
        self.assertEqual(missing["cost"], 1.0)
        rows = [
            {"cost": 0.2, "unsellable": False, "seasoned": False, "snapshot_t": "2026-11-01T00:00:00Z"},
            {"cost": missing["cost"], "unsellable": True, "seasoned": True, "snapshot_t": "2026-11-01T01:00:00Z"},
        ]
        summary = P.summarize(rows, {}, None)
        self.assertEqual(summary["units"], 2)
        self.assertAlmostEqual(summary["median_cost"], 0.6)
        self.assertAlmostEqual(summary["unsellable_share"], 0.5)
        self.assertAlmostEqual(summary["median_cost_seasoned"], 1.0)

    def test_second_listing_and_held_bid(self):
        end = NOW + 3 * 3600
        soon = NOW + 60
        parsed = [P.parse_order(order(price, end if price != 0.5 else soon, digest=format(i, "02x")), NOW) for i, price in enumerate((0.5, 1.0, 1.2, 1.1))]
        self.assertAlmostEqual(P.floor_and_counts(parsed)[0], 0.5)
        self.assertAlmostEqual(P.second_cheapest(parsed), 1.1)
        self.assertIsNone(P.parse_order(order(1, end, order_type=2), NOW))
        self.assertIsNone(P.parse_order(order(1, end, flagged=True), NOW))
        self.assertIsNone(P.parse_order(order(1, end, n_items=2), NOW))
        prev = [{"hash": "0x" + "ab" * 32, "price": 0.05}, {"hash": "0x" + "cd" * 32, "price": 0.09}]
        curr = [{"hash": "0x" + "cd" * 32, "price": 0.09}, {"hash": "0x" + "ef" * 32, "price": 0.2}]
        self.assertAlmostEqual(P.held_bid(prev, curr), 0.09)
        self.assertIsNone(P.held_bid(prev, [curr[1]]))

    def test_collection_offer_price_is_per_item(self):
        end = NOW + 3 * 3600
        body = order(0.4, end, role="offer", order_type=3, qty=4, digest="b1")
        body["price_per_item"] = {"currency": "WETH", "decimals": 18, "value": wei(0.1)}
        body["criteria"] = {"traits": [], "numeric_traits": [], "encoded_token_ids": ""}
        parsed = P.parse_order(body, NOW, "offer")
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed["price"], 0.1)
        trait = order(0.1, end, role="offer", order_type=2, qty=1, digest="b2")
        trait["criteria"] = {"traits": [{"type": "fur", "value": "red"}]}
        self.assertIsNone(P.parse_order(trait, NOW, "offer"))
        token = order(0.1, end, role="offer", order_type=3, qty=1, digest="b3")
        token["criteria"] = {"encoded_token_ids": "0x01"}
        self.assertIsNone(P.parse_order(token, NOW, "offer"))
        self.assertIsNone(P.parse_order(order(1, end, order_type=2), NOW, "listing"))
        star = order(0.2, end, role="offer", order_type=2, qty=1, digest="b4")
        star["criteria"] = {"traits": None, "numeric_traits": None, "encoded_token_ids": "*"}
        self.assertAlmostEqual(P.parse_order(star, NOW, "offer")["price"], 0.2)
        named = order(0.2, end, role="offer", order_type=2, qty=1, digest="b5")
        named["criteria"] = {"trait": {"type": "hat", "value": "blue"}}
        self.assertIsNone(P.parse_order(named, NOW, "offer"))
        fixture = json.load(open(os.path.join(os.path.dirname(__file__), "fixtures", "cool_cats_offer.json")))
        live = P.parse_order(fixture, NOW, "offer")
        per = int(fixture["price_per_item"]["value"]) / 10 ** int(fixture["price_per_item"]["decimals"])
        self.assertIsNotNone(live)
        self.assertAlmostEqual(live["price"], per)

    def test_universe_filters_and_identity(self):
        ok = {
            "n_contracts": 1, "shared": False, "standard": "erc721", "mint_ok": True, "disabled": False,
            "floor_eth": 0.1, "floor_usd": 200, "gas_eth": 0.0005, "listings": 5, "sales": 30, "buyers": 10,
            "bidders": 3, "top_share": 0.2, "sales_truncated": False,
        }
        self.assertEqual(P.rejection(ok), [])
        flips = {
            "floor": {"floor_usd": 250.01}, "gas": {"gas_eth": 0.002}, "listings": {"listings": 4},
            "sales": {"sales": 29}, "buyers": {"buyers": 9}, "bidders": {"bidders": 2},
            "concentration": {"top_share": 0.201}, "shared": {"shared": True}, "standard": {"standard": "erc1155"},
            "contracts": {"n_contracts": 2}, "mint": {"mint_ok": False},
        }
        for reason, patch in flips.items():
            self.assertIn(reason, P.rejection({**ok, **patch}))
        # v0.3: truncation is a flag, never a rejection.
        self.assertEqual(P.rejection({**ok, "sales_truncated": True}), [])
        self.assertNotIn("sales_truncated", P.REJECTION_REASONS)
        aid = P.asset_id("ethereum", ADDR.upper())
        self.assertEqual(aid, "nft_collection:eip155:1:" + ADDR)
        self.assertEqual(P.parse_asset_id(aid)["asset_type"], "nft_collection")
        self.assertEqual(P.parse_asset_id("nft_collection:eip155:8453:" + ADDR)["chain"], "eip155:8453")
        self.assertRaises(ValueError, P.asset_id, "solana", ADDR)
        self.assertTrue(P.mint_ok("2020-01-01", NOW))
        self.assertFalse(P.mint_ok("2026-11-01", NOW))
        self.assertFalse(P.seasoned(["2026-11-%02d" % i for i in range(1, 8)], "2026-11-07"))
        self.assertTrue(P.seasoned(["2026-11-%02d" % i for i in range(1, 9)], "2026-11-08"))

    def test_gas_uses_the_higher_of_now_and_median(self):
        sample = [{"t": NOW, "chain": "ethereum", "wei": 100}]
        self.assertEqual(P.gas_quote(200, sample, "ethereum", NOW), 200)
        self.assertEqual(P.gas_quote(10, sample, "ethereum", NOW), 55)
        self.assertIsNone(P.gas_quote(None, sample, "ethereum", NOW))
        self.assertLessEqual(P.upper_bound_calls(), P.MAX_CALLS)
        self.assertEqual(P.READS_PER_MINUTE, 120)
        self.assertLess(P.PACE_PER_MINUTE, P.READS_PER_MINUTE)
        self.assertEqual(P.MAX_CALLS, P.PACE_PER_MINUTE * 8)
        self.assertLessEqual(P.PACE_PER_MINUTE, P.READS_PER_MINUTE // 2)
        self.assertEqual(P.PASS_TARGET, 10)
        self.assertEqual(P.NULL_COST_MAX, 0.20)
        self.assertEqual(P.ORDER_BY, "market_cap")
        self.assertNotIn("num_owners", P.RULE)
        self.assertIn("order_by=market_cap", P.RULE)
        self.assertNotIn("volume", P.ORDER_BY)
        self.assertNotIn("trending", P.PAGE_ENDPOINT)

    def test_shared_contracts_are_marked(self):
        class Client:
            def list_collections(self, chain, cursor):
                if chain != "ethereum":
                    return [], None
                return [
                    {"collection": "a", "contracts": [{"address": ADDR, "chain": "ethereum"}]},
                    {"collection": "b", "contracts": [{"address": ADDR, "chain": "ethereum"}]},
                    {"collection": "c", "contracts": [{"address": STORE, "chain": "ethereum"}]},
                    {"collection": "d", "contracts": [
                        {"address": "0x" + "12" * 20, "chain": "ethereum"},
                        {"address": "0x" + "13" * 20, "chain": "ethereum"},
                    ]},
                    {"collection": "e", "contracts": [{"address": "0x" + "14" * 20, "chain": "ethereum"}]},
                ], None
        flags = {row["slug"]: row["shared"] for row in snapshot._candidates(Client())}
        self.assertTrue(flags["a"] and flags["b"] and flags["c"] and flags["d"])
        self.assertFalse(flags["e"])

    def test_t1_fill_held_bid_and_no_wallet_leak(self):
        end = NOW + 3 * 3600
        detail = {
            "collection": "example", "created_date": "2020-01-01", "is_disabled": False,
            "owner": "0x" + "ab" * 20, "twitter_username": "not-published",
            "fees": [
                {"fee": 1, "recipient": RECIPIENT, "required": True},
                {"fee": 5, "recipient": "0x" + "33" * 20, "required": False},
            ],
            "pricing_currencies": {"listing_currency": {"symbol": "ETH", "usd_price": "2000"}},
        }
        listings_t = [order(0.10 + i * 0.01, end, digest=format(i, "02x")) for i in range(5)]
        listings_t1 = [order(price, end, digest=format(10 + i, "02x")) for i, price in enumerate((0.12, 0.15, 0.16, 0.17, 0.18))]
        offers_t = [
            order(0.09, end, role="offer", offerer=OFFERER, digest="11"),
            order(0.08, end, role="offer", offerer="0x" + "a1" * 20, digest="22"),
            order(0.085, end, role="offer", offerer="0x" + "b2" * 20, digest="33"),
        ]
        offers_t1 = offers_t + [order(0.091, end, role="offer", offerer="0x" + "c3" * 20, digest="44")]
        offers_new = [order(0.12, end, role="offer", offerer="0x" + "c3" * 20, digest="55")]
        buyers = [BUYER, BUYER] + ["0x%040x" % (i + 1) for i in range(28)]
        sellers = [SELLER] * 3 + ["0x%040x" % (i + 100) for i in range(27)]
        events = [{"event_type": "sale", "event_timestamp": int(NOW - 100 - i * 20000), "buyer": b, "seller": sellers[i]}
                  for i, b in enumerate(buyers)]

        class Fake:
            def __init__(self):
                self.calls, self.raw = 0, []
                self.rate = {"limit": "120", "remaining": "100", "reset": "9"}
                self.listings_now, self.offers_now = listings_t, offers_t
            def list_collections(self, chain, cursor):
                self.calls += 1
                if chain != "ethereum":
                    return [], None
                return [{"collection": "example", "contracts": [{"address": ADDR, "chain": "ethereum"}]}], None
            def collection(self, slug):
                self.calls += 1
                self.raw.append({"path": "collection", "body": detail})
                return detail
            def listings(self, slug):
                self.calls += 1
                return self.listings_now
            def offers(self, slug, cursor):
                self.calls += 1
                return self.offers_now, None
            def events(self, slug, after, cursor):
                self.calls += 1
                return events, None
            def contract(self, chain, address):
                raise AssertionError("item type already says erc721")

        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        fake = Fake()
        snapshot.run(data, fake, NOW, "sha-1", gas, eth_usd=2000)
        self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "units.jsonl")))
        fake.listings_now, fake.offers_now = listings_t1, offers_t1
        snapshot.run(data, fake, NOW + 3600, "sha-2", gas, eth_usd=2000)
        fake.offers_now = offers_new
        snapshot.run(data, fake, NOW + 7200, "sha-3", gas, eth_usd=2000)
        units = [json.loads(line) for line in open(os.path.join(data, "nft-pilot", "units.jsonl"))]
        self.assertEqual(len(units), 2)
        priced, unsold = units
        leg = P.leg_eth(10 ** 9)
        self.assertAlmostEqual(priced["gas_eth"], leg)
        self.assertAlmostEqual(priced["entry_eth"], 0.15 + leg)
        self.assertNotAlmostEqual(priced["entry_eth"], 0.10 + leg)
        self.assertAlmostEqual(priced["exit_eth"], 0.09 * (1 - 0.01 - 0.05) - leg)
        self.assertFalse(priced["unsellable"])
        self.assertTrue(priced["fill"])
        self.assertEqual(priced["marketplace_fee_rate"], 0.01)
        self.assertEqual(priced["creator_fee_rate"], 0.05)
        self.assertTrue(unsold["unsellable"] and unsold["cost"] == 1.0)
        blob = ""
        for name in os.listdir(os.path.join(data, "nft-pilot")):
            with open(os.path.join(data, "nft-pilot", name)) as fh:
                blob += fh.read()
        for secret in (RECIPIENT, OFFERER, BUYER, SELLER, "not-published", "0x" + "ab" * 20):
            self.assertNotIn(secret, blob.lower())
        self.assertEqual(P.stray_addresses(blob), set())
        import jsonschema
        schema = json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json")))
        status = json.loads(open(os.path.join(data, "nft-pilot", "status.json")).read())
        jsonschema.validate(status, schema)
        for unit in units:
            jsonschema.validate(unit, schema)
        self.assertEqual(status["buyer_fee_eth"], 0.0)
        self.assertEqual(status["raw"], "not_kept")
        self.assertEqual(status["null_cost_share_max"], 0.2)
        self.assertEqual(status["data_branch"], "nft-data")
        self.assertEqual(status["avoids_branch"], "desk-data")
        self.assertGreater(status["opensea_calls"], 0)
        self.assertEqual(status["rate_limit"]["limit"], "120")

    def test_no_key_publishes_nothing_measured(self):
        data = tempfile.mkdtemp()
        old = os.environ.pop("OPENSEA_API_KEY", None)
        try:
            code = snapshot.main(["--data-dir", data, "--code-sha", "abc123"])
        finally:
            if old is not None:
                os.environ["OPENSEA_API_KEY"] = old
        self.assertEqual(code, 0)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["status"], "no_key")
        self.assertEqual(status["reason"], "no key")
        self.assertFalse(status["measured"])
        self.assertIsNone(status["median_cost"])
        self.assertIsNone(status["unsellable_share"])
        self.assertEqual(status["buyer_fee_eth"], 0.0)
        self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "units.jsonl")))
        again = os.environ.pop("OPENSEA_API_KEY", None)
        try:
            first = open(os.path.join(data, "nft-pilot", "status.json")).read()
            self.assertEqual(snapshot.main(["--data-dir", data, "--code-sha", "abc123"]), 0)
            self.assertEqual(open(os.path.join(data, "nft-pilot", "status.json")).read(), first)
        finally:
            if again is not None:
                os.environ["OPENSEA_API_KEY"] = again
        import jsonschema
        schema = json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json")))
        jsonschema.validate(status, schema)

    def test_null_cost_share_blocks_the_gate(self):
        from datetime import date
        start = date(2026, 11, 1)
        row = lambda cost: {"cost": cost, "unsellable": cost == 1, "seasoned": False, "snapshot_t": "2026-11-14T00:00:00Z"}
        within = [row(0.1), row(0.1), row(0.1), row(0.1), row(None)]
        summary = P.summarize(within, {"2026-11-14": ["a"]}, start, ["2026-11-14"])
        self.assertEqual(summary["null_cost_count"], 1)
        self.assertAlmostEqual(summary["null_cost_share"], 0.2)
        self.assertEqual(summary["null_cost_share_max"], 0.20)
        self.assertAlmostEqual(summary["median_cost"], 0.1)
        self.assertEqual(summary["nh04"], "ready")
        over = within + [row(None)]
        blocked = P.summarize(over, {"2026-11-14": ["a"]}, start, ["2026-11-14"])
        self.assertGreater(blocked["null_cost_share"], 0.20)
        self.assertEqual(blocked["nh04"], "not_ready")
        self.assertFalse(blocked["nh04_ready"])

    def test_partial_day_is_unknown_not_zero(self):
        from datetime import date
        start = date(2026, 11, 1)
        days = ["2026-11-%02d" % d for d in range(8, 15)]
        passes = {day: ["a"] for day in days}
        known = P.summarize([], passes, start, days)
        self.assertTrue(known["pace_ready"])
        self.assertEqual(known["pace_median"], 1)
        missing = days[:-1]
        passes["2026-11-14"] = []
        unknown = P.summarize([], passes, start, missing)
        self.assertFalse(unknown["pace_ready"])
        self.assertIsNone(unknown["pace_median"])

    def _universe_fake(self, n=30, pass_every=1, truncate=False, cap=None, log=None):
        """n under-cap collections on ethereum; every pass_every-th one is liquid."""
        end = NOW + 400 * 86400
        log = log if log is not None else []

        class Fake:
            def __init__(self):
                self.calls, self.raw = 0, []
                self.max_calls = cap if cap is not None else P.MAX_CALLS
                self.rate = {"limit": "120", "remaining": "100", "reset": "1"}
            def _tick(self, what):
                if self.calls >= self.max_calls:
                    raise opensea.Budget()
                self.calls += 1
                log.append(what)
            def list_collections(self, chain, cursor):
                self._tick(("list", chain))
                if chain != "ethereum":
                    return [], None
                return [{"collection": "c%d" % i, "contracts": [{"address": "0x%040x" % (i + 1), "chain": "ethereum"}]}
                        for i in range(n)], None
            def listings(self, slug):
                self._tick(("listings", slug))
                return [order(0.1 + k * 0.01, end, digest="%02x" % k) for k in range(5)]
            def collection(self, slug):
                self._tick(("collection", slug))
                return {"created_date": "2020-01-01", "fees": [{"fee": 1, "required": True}]}
            def offers(self, slug, cursor):
                self._tick(("offers", slug))
                return [order(0.09, end, role="offer", offerer="0x%040x" % (500 + k), digest="%02x" % (40 + k)) for k in range(3)], None
            def events(self, slug, after, cursor):
                self._tick(("events", slug))
                idx = int(slug[1:])
                liquid = idx % pass_every == 0
                count = 100 if truncate else (30 if liquid else 5)
                page = int(cursor or 0)
                rows = [{"event_type": "sale", "event_timestamp": int(NOW - 1000 - (page * 100 + k) * 1500),
                         "buyer": "0x%040x" % (1000 + page * 100 + k), "seller": "0x%040x" % (5000 + (page * 100 + k) % 7)}
                        for k in range(count)]
                # The 7-day window: `after` is the run time minus 7 days (runs here are whole hours apart).
                assert (int(NOW) - 7 * 86400 - after) % 3600 == 0
                return rows, (str(page + 1) if truncate else None)
            def contract(self, chain, address):
                raise AssertionError("item type is on the listing")
        return Fake()

    def test_screen_until_pass_target(self):
        log = []
        fake = self._universe_fake(n=30, pass_every=2, log=log)
        data = tempfile.mkdtemp()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["floor_passing_this_run"], P.PASS_TARGET)
        # Passers are c0, c2, ... c18; screening stops after the 10th, so c19+ are never read.
        self.assertEqual(status["screened"], 19)
        self.assertNotIn(("listings", "c19"), log)
        self.assertEqual(status["rejections"]["sales"], 9)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(len(books["pending"]), 10)
        self.assertEqual(books["pending"][0]["counts"]["sales_7d"], 30)
        self.assertIn("pass_target=10", status["rule"])
        import jsonschema
        jsonschema.validate(status, json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json"))))

    def test_pending_rechecks_are_served_before_screening(self):
        log = []
        fake = self._universe_fake(n=30, pass_every=1, log=log)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        snapshot.run(data, fake, NOW, "sha", gas, eth_usd=2000)
        del log[:]
        snapshot.run(data, fake, NOW + 3600, "sha", gas, eth_usd=2000)
        first_list = log.index(("list", "ethereum"))
        pend_reads = [i for i, (kind, slug) in enumerate(log) if kind == "listings" and slug in {"c%d" % k for k in range(10)}]
        self.assertTrue(pend_reads and max(pend_reads) < first_list)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["pending_served"], 10)
        self.assertEqual(status["pending_deferred"], 0)
        # Cached listings/offers: a pending collection met again in the universe costs only its events.
        self.assertEqual(log.count(("listings", "c0")), 1)
        self.assertEqual(log.count(("offers", "c0")), 1)

    def test_call_cap_during_pending_defers_and_is_partial(self):
        log = []
        fake = self._universe_fake(n=30, pass_every=1, log=log)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        snapshot.run(data, fake, NOW, "sha", gas, eth_usd=2000)
        for tries in (1, 2, 3):
            fake.calls, fake.max_calls = 0, 7   # room for two pending re-checks (3 reads each) only
            del log[:]
            snapshot.run(data, fake, NOW + 3600 * tries, "sha", gas, eth_usd=2000)
            status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
            self.assertEqual(status["status"], "partial")
            self.assertIsNone(status["floor_passing_this_run"])
            self.assertNotIn(("list", "ethereum"), log)
        units = [json.loads(line) for line in open(os.path.join(data, "nft-pilot", "units.jsonl"))]
        self.assertEqual(sum(1 for u in units if u.get("reason") == "budget"), 4)
        self.assertEqual(sum(1 for u in units if "reason" not in u), 6)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(books["pending"], [])
        self.assertEqual(books["complete_days"], ["2026-11-09"])

    def test_truncated_sales_are_flagged_not_rejected(self):
        log = []
        fake = self._universe_fake(n=3, truncate=True, log=log)
        data = tempfile.mkdtemp()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(log.count(("events", "c0")), P.EVENT_MAX_PAGES)
        self.assertEqual(status["floor_passing_this_run"], 3)
        self.assertEqual(status["flags"], {"sales_truncated": 3, "liquid_tail": 3})
        self.assertNotIn("sales_truncated", status["rejections"])
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        counts = books["pending"][0]["counts"]
        self.assertTrue(counts["sales_truncated"] and counts["liquid_tail"])
        self.assertEqual(counts["sales_7d"], 300)
        self.assertEqual(counts["buyers_7d"], 300)

    def test_rule_string_freezes_v03_thresholds(self):
        for part in ("v0.3 ", "window=7d", "min_sales=30", "min_buyers=10", "max_buyer_share=0.20",
                     "max_list_pages=8", "pass_target=10", "max_calls=480", "pace_per_minute=60",
                     "event_pages=3", "truncated=flag", "floor_usd=250"):
            self.assertIn(part, P.RULE)
        self.assertNotIn("floor_target", P.RULE)
        self.assertEqual((P.WINDOW_DAYS, P.MIN_SALES, P.MIN_BUYERS, P.MAX_BUYER_SHARE), (7, 30, 10, 0.20))
        self.assertEqual(P.FLOOR_USD, 250)
        self.assertEqual(P.STATE_VERSION, "v0.3")

    def test_pace_fits_the_per_minute_window(self):
        # Repo key: ran_at 10:21:16Z, reset 1794306390 = 10:26:30Z, about 60 s after the
        # run's last call, so the 120 limit is a rolling minute, not an hour.
        self.assertEqual(datetime.fromtimestamp(1794306390, timezone.utc).strftime("%H:%M:%S"), "10:26:30")
        self.assertEqual(60.0 / P.PACE_PER_MINUTE, 1.0)
        self.assertLessEqual(P.PACE_PER_MINUTE, P.READS_PER_MINUTE // 2)
        self.assertLess(340 / P.PACE_PER_MINUTE * 60, snapshot.RUN_LIMIT_SECONDS)
        self.assertLess(P.MAX_CALLS / P.PACE_PER_MINUTE * 60, snapshot.RUN_LIMIT_SECONDS)
        ticks = []
        client = opensea.OpenSea("test-key", transport=lambda _p, _q: (200, {}, {"collections": []}),
                                 sleep=ticks.append, clock=lambda: 1000.0)
        client.list_collections("ethereum", None)
        client.list_collections("base", None)
        self.assertEqual(ticks, [1.0])

    def test_early_kill_two_gates(self):
        a = ["nft_collection:eip155:1:0x%040x" % i for i in range(1, 12)]
        t0 = int(NOW)
        k = P.early_kill([5, 6, 6], [], t0, t0)
        self.assertEqual((k["gate1"], k["result"]), ("fire", "STOPPED"))
        self.assertAlmostEqual(k["gate1_avg"], 17 / 3)
        self.assertEqual(P.early_kill([6, 6, 6], [], t0, t0)["gate1"], "pass")
        self.assertEqual(P.early_kill([9, 9], [], t0, t0)["gate1"], "pending")
        # Gate (ii) is a 72h window from the first measured run, not UTC days.
        self.assertEqual(P.early_kill([9, 9, 9], a[:7], t0, t0 + 72 * 3600 - 1)["gate2"], "pending")
        k = P.early_kill([9, 9, 9], a[:7], t0, t0 + 72 * 3600)
        self.assertEqual((k["gate2"], k["gate2_distinct"], k["result"], k["gate2_window_hours"]), ("fire", 7, "INCONCLUSIVE", 72))
        self.assertEqual(P.early_kill([9, 9, 9], a[:8], t0, t0 + 72 * 3600)["gate2"], "pass")

    def test_gate2_counts_only_passes_inside_72h(self):
        fake = self._universe_fake(n=3, pass_every=1)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        start = NOW + 20 * 3600     # 20:00Z: a UTC-day window would end 52h later, not 72h
        snapshot.run(data, fake, start, "sha", gas, eth_usd=2000)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(books["first_measured_at"], int(start))
        self.assertEqual(len(books["early_distinct"]), 3)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["early_kill"]["gate2"], "pending")
        snapshot.run(data, fake, start + 60 * 3600, "sha", gas, eth_usd=2000)   # day 4 UTC, inside 72h
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["early_kill"]["gate2"], "pending")
        snapshot.run(data, fake, start + 72 * 3600, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual((status["early_kill"]["gate2"], status["early_kill"]["result"]), ("fire", "STOPPED"))
        self.assertEqual(status["early_kill"]["gate2_distinct"], 3)

    def test_capped_run_with_four_passes_is_measured(self):
        log = []
        fake = self._universe_fake(n=30, pass_every=2, log=log)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        # 2 list calls, then 4 reads per screened collection (listings, collection, offers, events):
        # c0..c6 screened = 28 reads -> passes c0, c2, c4, c6.
        fake.max_calls = 1 + 28 + 1
        snapshot.run(data, fake, NOW, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["partial"])
        self.assertEqual(status["stop"], "call_cap")
        self.assertEqual(status["floor_passing_this_run"], 4)
        self.assertEqual(books["first_runs"], [4])
        self.assertEqual(len(books["passes"]["2026-11-09"]), 4)
        self.assertEqual(books["pilot_start"], "2026-11-09")
        self.assertEqual(status["by_chain"]["ethereum"], {"screened": 7, "passing": 4})
        self.assertEqual(status["by_chain"]["base"], {"screened": 0, "passing": 0})
        import jsonschema
        jsonschema.validate(status, json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json"))))

    def test_three_capped_runs_under_six_fire_gate1(self):
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        for hour in range(3):
            fake = self._universe_fake(n=30, pass_every=2)
            fake.max_calls = 30          # 4 passes before the cap, as above
            if hour:                      # room for the 4 pending re-checks first (3 reads each)
                fake.max_calls += 12
            snapshot.run(data, fake, NOW + hour * 3600, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(status["stop"], "call_cap")
        self.assertEqual(len(books["first_runs"]), 3)
        self.assertTrue(all(n < 6 for n in books["first_runs"]))
        self.assertEqual(status["early_kill"]["gate1"], "fire")
        self.assertEqual(status["early_kill"]["result"], "STOPPED")

    def test_depth_stop_is_measured(self):
        fake = self._universe_fake(n=3, pass_every=3)
        data = tempfile.mkdtemp()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual((status["status"], status["stop"], status["floor_passing_this_run"]), ("ok", "depth", 1))

    def test_calendar_backstop_fires_without_pilot_start(self):
        class Broken:
            def __init__(self):
                self.calls = 0
                self.rate = {"limit": None, "remaining": None, "reset": None}
            def list_collections(self, chain, cursor):
                self.calls += 1
                raise opensea.OpenSeaError(500)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        snapshot.run(data, Broken(), NOW, "sha", gas, eth_usd=2000)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(books["v03_init_at"], int(NOW))
        self.assertIsNone(books["pilot_start"])
        snapshot.run(data, Broken(), NOW + 7 * 86400 - 60, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["status"], "partial")
        self.assertEqual(status["early_kill"]["backstop"], "pending")
        snapshot.run(data, Broken(), NOW + 7 * 86400, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertIsNone(books["pilot_start"])
        self.assertEqual(status["early_kill"]["backstop"], "fire")
        self.assertEqual(status["early_kill"]["backstop_measured_days"], 0)
        self.assertEqual(status["early_kill"]["result"], "STOPPED")
        self.assertEqual(status["outage_runs"], 0)
        self.assertNotIn("outage runs in window", status.get("verdict_footnote") or "")
        self.assertNotIn("outage_at", books)
        days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"]
        self.assertEqual(P.early_kill([], [], None, NOW + 7 * 86400, NOW, days)["backstop"], "pass")
        self.assertEqual(P.early_kill([], [], None, NOW + 7 * 86400, NOW, days[:3])["backstop"], "fire")

    def test_min_units_counts_numeric_costs_only(self):
        from datetime import date
        start = date(2026, 11, 1)
        days = ["2026-11-%02d" % d for d in range(1, 15)]
        ids = ["nft_collection:eip155:1:0x%040x" % i for i in range(1, 11)]
        passes = {d: ids for d in days}
        def unit(i, cost):
            return {"asset_id": ids[i % 10], "cost": cost, "unsellable": False, "seasoned": False,
                    "snapshot_t": "2026-11-%02dT00:00:00Z" % (1 + i % 14), "counts": {}, "rule": P.RULE}
        # 19 numeric + 2 null: 21 units, null share 9.5% (under the 20% block), but only 19 count.
        rows = [unit(i, 0.05) for i in range(19)] + [unit(19, None), unit(20, None)]
        got = P.summarize(rows, passes, start, days)
        self.assertEqual(got["units"], 21)
        self.assertLessEqual(got["null_cost_share"], 0.2)
        self.assertEqual(got["nh04_verdict"], "INCONCLUSIVE")
        rows.append(unit(21, 0.05))
        self.assertTrue(P.summarize(rows, passes, start, days)["nh04_verdict"].startswith("cost GO, supply-limited"))

    def test_go_is_cost_go_supply_limited_and_footnoted(self):
        from datetime import date
        start = date(2026, 11, 1)
        days = ["2026-11-%02d" % d for d in range(1, 15)]
        ids = ["nft_collection:eip155:1:0x%040x" % i for i in range(1, 11)]
        passes = {d: ids[: 3 + (i % 3)] for i, d in enumerate(days)}
        def unit(i, cost, counts=None):
            return {"asset_id": ids[i % 10], "cost": cost, "unsellable": False, "seasoned": False,
                    "snapshot_t": "2026-11-%02dT00:00:00Z" % (1 + i % 14), "counts": counts or {}, "rule": P.RULE}
        units = [unit(i, 0.08) for i in range(20)]
        got = P.summarize(units, passes, start, days)
        self.assertEqual(got["nh04_verdict"], "cost GO, supply-limited (median 4 distinct/day)")
        self.assertNotEqual(got["nh04_verdict"], "GO")
        self.assertIsNone(got["verdict_footnote"])
        self.assertFalse(got["pace_ready"] and got["pace_median"] >= 20)  # F2 reported, not a gate
        self.assertEqual(P.summarize(units[:19], passes, start, days)["nh04_verdict"], "INCONCLUSIVE")
        few = [dict(u, asset_id=ids[i % 7]) for i, u in enumerate(units)]
        self.assertEqual(P.summarize(few, passes, start, days)["nh04_verdict"], "INCONCLUSIVE")
        self.assertEqual(P.summarize([unit(i, 0.3) for i in range(20)], passes, start, days)["nh04_verdict"], "NO-GO")
        old = [dict(u, rule="older rule") for u in units]
        self.assertEqual(P.summarize(old + units[:5], passes, start, days)["units"], 5)
        units[0]["counts"] = {"top_seller_share_7d": 0.6}
        units[1]["counts"] = {"max_day_share_7d": 0.51}
        self.assertIn("2 of 20 units", P.summarize(units, passes, start, days)["verdict_footnote"])

    def test_sale_profile_reports_seller_and_single_day_share(self):
        ev = [{"event_type": "sale", "event_timestamp": int(NOW - 100 - i), "buyer": "0x%040x" % i,
               "seller": SELLER if i < 6 else "0x%040x" % (100 + i)} for i in range(10)]
        ev += [{"event_type": "sale", "event_timestamp": int(NOW - 3 * 86400), "buyer": "0x%040x" % 50,
                "seller": "0x%040x" % 51}] * 2
        seller, day = P.sale_profile(ev, int(NOW) - 7 * 86400, NOW)
        self.assertAlmostEqual(seller, 6 / 12)
        self.assertAlmostEqual(day, 10 / 12)
        self.assertTrue(P.footnoted({"max_day_share_7d": day}))
        self.assertFalse(P.footnoted({"top_seller_share_7d": seller}))

    def test_rate_limit_backoff_is_one_retry(self):
        seen = {"n": 0}
        def transport(_path, _params):
            seen["n"] += 1
            if seen["n"] == 1:
                return 429, {"Retry-After": "0", "X-RateLimit-Remaining": "0"}, {}
            return 200, {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "119", "X-RateLimit-Reset": "10"}, {"collections": [], "next": None}
        client = opensea.OpenSea("test-key", transport=transport, sleep=lambda _s: None)
        self.assertEqual(client.list_collections("ethereum", None), ([], None))
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.rate["remaining"], "119")
        self.assertNotIn("test-key", json.dumps(client.raw))

    def test_gas_rpc_user_agent_and_http_error_line(self):
        seen = []

        def boom(req, timeout=None):
            seen.append((req, timeout))
            raise urllib.error.HTTPError(
                "https://user:sekret-value@ethereum-rpc.publicnode.com/eth?api_key=sekret-value",
                403,
                "Forbidden sekret-value",
                {"x-api-key": "sekret-value", "User-Agent": "sekret-value"},
                None,
            )

        urllib.request.urlopen = boom
        data = tempfile.mkdtemp()
        old = os.environ.get("OPENSEA_API_KEY")
        os.environ["OPENSEA_API_KEY"] = "sekret-value"
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                code = snapshot.main(["--data-dir", data, "--code-sha", "abc123"])
        finally:
            if old is None:
                os.environ.pop("OPENSEA_API_KEY", None)
            else:
                os.environ["OPENSEA_API_KEY"] = old
        self.assertEqual(code, 1)
        req, timeout = seen[0]
        self.assertEqual(timeout, 20)
        self.assertEqual(req.get_header("User-agent"), "sightline-nft-snapshot")
        self.assertTrue(req.has_header("User-agent"))
        self.assertNotIn("Python-urllib", req.get_header("User-agent"))
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(req.full_url, P.GAS_RPC["ethereum"])
        text = err.getvalue().strip()
        self.assertEqual(text, "snapshot error: HTTPError 403 ethereum-rpc.publicnode.com")
        self.assertNotIn("sekret-value", text)
        self.assertNotIn("api_key", text)
        self.assertNotIn("https://", text)
        self.assertEqual(snapshot._snapshot_error_line(ValueError("sekret-value")), "snapshot error: ValueError")
        self.assertEqual(snapshot._snapshot_error_line(opensea.OpenSeaError(502)), "snapshot error: OpenSeaError 502")
        with open(os.path.join(data, "nft-pilot", "status.json")) as fh:
            status = json.load(fh)
        self.assertEqual(status["status"], "error")
        self.assertEqual(status["reason"], "error")
        self.assertNotIn("sekret-value", json.dumps(status))

    def test_opensea_request_sends_user_agent(self):
        seen = []

        class Res:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _n=-1):
                return b"{}"

        def capture(req, timeout=None):
            seen.append((req, timeout))
            return Res()

        urllib.request.urlopen = capture
        client = opensea.OpenSea("sekret-value")
        status, _headers, body = client._http("/api/v2/collections", {"chain": "ethereum"})
        self.assertEqual(status, 200)
        self.assertEqual(body, {})
        req, timeout = seen[0]
        self.assertEqual(timeout, 20)
        agent = req.get_header("User-agent")
        self.assertEqual(agent, "sightline-nft-snapshot")
        self.assertTrue(req.has_header("User-agent"))
        self.assertNotIn("Python-urllib", agent)
        self.assertNotIn("sekret-value", agent)
        self.assertEqual(req.get_header("X-api-key"), "sekret-value")
        self.assertNotIn("sekret-value", req.full_url)

    @staticmethod
    def _round(answer_usd, updated, round_id=5, answered=5):
        words = [round_id, int(round(answer_usd * 10 ** 8)), updated, updated, answered]
        return "0x" + "".join("%064x" % w for w in words)

    def test_eth_usd_parser_keeps_unknown_null(self):
        self.assertIsNone(snapshot.parse_eth_usd("defillama", {
            "coins": {"coingecko:ethereum": {"price": 2111.89, "symbol": "ETH"}},
        }))
        self.assertIsNone(snapshot.parse_eth_usd("defillama", {"coins": {"coingecko:ethereum": {"price": None, "symbol": "ETH"}}}))
        stale = {"coins": {"coingecko:ethereum": {"price": 2000, "symbol": "ETH", "timestamp": NOW - 3601}}}
        fresh = {"coins": {"coingecko:ethereum": {"price": 2000, "symbol": "ETH", "timestamp": NOW - 60}}}
        self.assertIsNone(snapshot.parse_eth_usd("defillama", stale, now=NOW))
        self.assertEqual(snapshot.parse_eth_usd("defillama", fresh, now=NOW), 2000)
        self.assertIsNone(snapshot.parse_eth_usd("coinbase", {"data": {"amount": "2000", "base": "ETH", "currency": "USD"}}))

    def test_coinbase_is_gone(self):
        for name in ("snapshot.py", "pricing.py", "opensea.py", "publish_nft_data.sh"):
            text = open(os.path.join(ROOT, "nft", "loop", name)).read().lower()
            self.assertNotIn("coinbase", text, name)
        self.assertNotIn("coinbase", open(os.path.join(ROOT, ".github", "workflows", "nft-snapshot.yml")).read().lower())
        self.assertFalse(hasattr(snapshot, "ETH_USD_SOURCES"))

    def test_chainlink_primary_and_stale_rejected(self):
        self.assertEqual(snapshot.CHAINLINK_ETH_USD, "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419")
        self.assertEqual(snapshot.LATEST_ROUND_DATA, "0xfeaf968c")
        self.assertAlmostEqual(snapshot.parse_chainlink(self._round(2090.05, int(NOW) - 600), NOW), 2090.05)
        self.assertIsNone(snapshot.parse_chainlink(self._round(2090.05, int(NOW) - 3601), NOW))   # stale > 1h
        self.assertIsNotNone(snapshot.parse_chainlink(self._round(2090.05, int(NOW) - 3600), NOW))
        self.assertIsNone(snapshot.parse_chainlink(self._round(0, int(NOW)), NOW))
        self.assertIsNone(snapshot.parse_chainlink(self._round(2000, int(NOW), round_id=6, answered=5), NOW))
        self.assertIsNone(snapshot.parse_chainlink("0x", NOW))
        neg = "0x" + "%064x" % 5 + "f" * 64 + ("%064x" % int(NOW)) * 2 + "%064x" % 5
        self.assertIsNone(snapshot.parse_chainlink(neg, NOW))
        posts, gets = [], []
        def post(url, payload):
            posts.append((url, payload))
            return {"result": self._round(2090.05, int(NOW) - 60)}
        def get(url):
            gets.append(url)
            raise AssertionError("fallback read while Chainlink is fresh")
        self.assertEqual(snapshot.fetch_eth_usd(get, post, now=NOW), (2090.05, "chainlink"))
        self.assertEqual(posts[0][0], P.GAS_RPC["ethereum"])
        self.assertEqual(posts[0][1]["method"], "eth_call")
        self.assertEqual(posts[0][1]["params"][0], {"to": snapshot.CHAINLINK_ETH_USD, "data": "0xfeaf968c"})
        stale_post = lambda _u, _p: {"result": self._round(2090.05, int(NOW) - 7200)}
        llama = lambda _u: {"coins": {"coingecko:ethereum": {"price": 2001, "symbol": "ETH", "timestamp": NOW - 60}}}
        self.assertEqual(snapshot.fetch_eth_usd(llama, stale_post, now=NOW), (2001, "defillama"))
        def down(_u, _p):
            raise OSError("rpc down")
        self.assertEqual(snapshot.fetch_eth_usd(llama, down, now=NOW), (2001, "defillama"))
        self.assertEqual(snapshot.fetch_eth_usd(lambda _u: {}, down, now=NOW), (None, None))

    def test_defillama_price_is_never_published(self):
        fake = self._universe_fake(n=4, pass_every=1)
        data = tempfile.mkdtemp()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9},
                     fetch_price=lambda: (2345.67, "defillama"))
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["floor_passing_this_run"], 4)      # the cap was still screened
        self.assertIsNone(status["eth_usd"])
        self.assertEqual(status["eth_usd_source"], "fallback")
        self.assertTrue(all(p["floor_usd"] is None for p in books["pending"]))
        blob = "".join(open(os.path.join(data, "nft-pilot", n)).read() for n in os.listdir(os.path.join(data, "nft-pilot")))
        self.assertNotIn("2345.67", blob)
        floor_usd = 0.1 * 2345.67
        self.assertNotIn(repr(floor_usd), blob)
        import jsonschema
        jsonschema.validate(status, json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json"))))
        data2 = tempfile.mkdtemp()
        snapshot.run(data2, self._universe_fake(n=4, pass_every=1), NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9},
                     fetch_price=lambda: (2090.05, "chainlink"))
        st2 = json.load(open(os.path.join(data2, "nft-pilot", "status.json")))
        self.assertEqual((st2["eth_usd"], st2["eth_usd_source"]), (2090.05, "chainlink"))

    def test_q1_fallback_runs_keep_numeric_costs_and_cap(self):
        """Quiet hour: Chainlink at 3700 s (past the 3600 s limit) falls back to DefiLlama.
        Unit costs are ETH ratios and the $250 cap is screened on the internal price."""
        self.assertIsNone(snapshot.parse_chainlink(self._round(2000, int(NOW) - 3700), NOW))
        llama = lambda _u: {"coins": {"coingecko:ethereum": {"price": 2000, "symbol": "ETH", "timestamp": NOW - 60}}}
        quiet = lambda _u, _p: {"result": self._round(2000, int(NOW) - 3700)}
        self.assertEqual(snapshot.fetch_eth_usd(llama, quiet, now=NOW), (2000, "defillama"))
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        fb = lambda: (2000, "defillama")
        fake = self._universe_fake(n=4, pass_every=1)
        snapshot.run(data, fake, NOW, "sha", gas, fetch_price=fb)
        snapshot.run(data, fake, NOW + 3600, "sha", gas, fetch_price=fb)
        root = os.path.join(data, "nft-pilot")
        status = json.load(open(os.path.join(root, "status.json")))
        units = [json.loads(line) for line in open(os.path.join(root, "units.jsonl"))]
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["floor_passing_this_run"], 4)
        self.assertEqual(len(units), 4)
        self.assertTrue(all(isinstance(u["cost"], float) for u in units))   # never null on fallback
        self.assertEqual(status["null_cost_count"], 0)
        self.assertEqual(status["eth_usd_runs"], {"chainlink": 0, "fallback": 2})
        self.assertIn("0 Chainlink, 2 fallback", status["verdict_footnote"])
        import jsonschema
        jsonschema.validate(status, json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json"))))
        # Cap still bites on the internal price: at $3,000/ETH a 0.1 ETH floor is $300 > $250.
        data2 = tempfile.mkdtemp()
        snapshot.run(data2, self._universe_fake(n=4, pass_every=1), NOW, "sha", gas, fetch_price=lambda: (3000, "defillama"))
        st2 = json.load(open(os.path.join(data2, "nft-pilot", "status.json")))
        self.assertEqual(st2["floor_passing_this_run"], 0)
        self.assertGreaterEqual(st2["rejections"]["floor"], 4)
        self.assertIsNone(st2["eth_usd"])

    def test_usd_source_counts_mixed(self):
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        fake = self._universe_fake(n=4, pass_every=1)
        snapshot.run(data, fake, NOW, "sha", gas, fetch_price=lambda: (2000, "chainlink"))
        st = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(st["eth_usd_runs"], {"chainlink": 1, "fallback": 0})
        self.assertFalse((st["verdict_footnote"] or "").count("fallback"))
        snapshot.run(data, fake, NOW + 3600, "sha", gas, fetch_price=lambda: (2000, "defillama"))
        st = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(st["eth_usd_runs"], {"chainlink": 1, "fallback": 1})
        self.assertIn("1 Chainlink, 1 fallback", st["verdict_footnote"])
        # A no-price partial run is not counted.
        snapshot.run(data, fake, NOW + 7200, "sha", gas, fetch_price=lambda: (None, None))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(books["usd_runs"], {"chainlink": 1, "fallback": 1})
        self.assertEqual(P.with_usd_footnote("x", {"chainlink": 3, "fallback": 0}), "x")
        self.assertEqual(P.with_usd_footnote("x", {"chainlink": 3, "fallback": 1}).split("; ")[0], "x")

    def test_published_files_have_no_raw_offer_fields(self):
        fake = self._universe_fake(n=4, pass_every=1)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        snapshot.run(data, fake, NOW, "sha", gas, eth_usd=2000)
        snapshot.run(data, fake, NOW + 3600, "sha", gas, eth_usd=2000)
        root = os.path.join(data, "nft-pilot")
        blob = "".join(open(os.path.join(root, n)).read() for n in os.listdir(root))
        import re
        self.assertEqual(re.findall(r"0x[0-9a-fA-F]{64}", blob), [])          # no raw order hashes
        for banned in ('"hash"', "price_eth", "order_hash", "price_per_item", "protocol_data", "offerer"):
            self.assertNotIn(banned, blob)
        books = json.load(open(os.path.join(root, "books.json")))
        for summary in list(books["offers"].values()) + [p["book"] for p in books["pending"]]:
            self.assertEqual(set(summary), {"digests", "top_bid_eth", "bidders_near_top"})
            for d in summary["digests"]:
                self.assertRegex(d, r"^[0-9a-f]{64}$")
        # The held-bid check still works from digests alone: units have a held bid.
        units = [json.loads(line) for line in open(os.path.join(root, "units.jsonl"))]
        self.assertEqual(len(units), 4)
        self.assertTrue(all(u["unsellable"] is False and u["cost"] is not None for u in units))
        self.assertIn("no per-order prices", P.ATTRIBUTION)
        self.assertNotIn("Derived metrics only", P.ATTRIBUTION)

    def test_legacy_raw_offer_books_are_dropped_on_load(self):
        data = tempfile.mkdtemp()
        root = os.path.join(data, "nft-pilot")
        os.makedirs(root)
        legacy = {"state_version": "v0.2", "pilot_start": "2026-11-10", "complete_days": [], "passes": {},
                  "seen": {}, "gas": [], "offers": {"nft_collection:eip155:1:" + ADDR: [{"hash": "0x" + "ab" * 32, "price_eth": 0.1}]},
                  "pending": [{"asset_id": "nft_collection:eip155:1:" + ADDR, "slug": "x", "chain": "ethereum",
                               "snapshot_t": "2026-11-10T00:00:00Z", "book": [{"hash": "0x" + "ab" * 32, "price_eth": 0.1}]}]}
        with open(os.path.join(root, "books.json"), "w") as fh:
            json.dump(legacy, fh)
        state = snapshot.load_state(os.path.join(root, "books.json"))
        self.assertEqual(state["offers"], {})
        self.assertEqual(state["pending"], [])

    def test_missing_eth_price_does_not_spend_reads(self):
        class Fake:
            def __init__(self):
                self.calls = 0
                self.rate = {"limit": None, "remaining": None, "reset": None}
            def list_collections(self, chain, cursor):
                raise AssertionError("paged without a price")

        data = tempfile.mkdtemp()
        fake = Fake()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, fetch_price=lambda: None)
        self.assertEqual(fake.calls, 0)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["status"], "partial")
        self.assertTrue(status["partial"])
        self.assertFalse(status["measured"])
        self.assertIsNone(status["eth_usd"])
        self.assertIsNone(status["floor_passing_this_run"])
        self.assertEqual(status["reason"], "no eth price")
        self.assertEqual(status["opensea_calls"], 0)
        self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "units.jsonl")))

    def test_list_http_error_stops_partial_and_keeps_the_price(self):
        seen = []

        def transport(path, params):
            seen.append((path, params))
            return 400, {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "118", "X-RateLimit-Reset": "1000"}, {"errors": ["Owners sorting has been removed"]}
        sleeps = []
        client = opensea.OpenSea("test-key", transport=transport, sleep=sleeps.append, clock=lambda: 1000.0)
        data = tempfile.mkdtemp()
        snapshot.run(data, client, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        self.assertEqual(client.calls, 2)
        self.assertEqual(sleeps, [1.0])
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["status"], "partial")
        self.assertTrue(status["partial"])
        self.assertIsNone(status["floor_passing_this_run"])
        self.assertEqual(status["eth_usd"], 2000)
        self.assertEqual(status["opensea_calls"], 2)
        self.assertEqual(status["reason"], "opensea http 400")
        self.assertEqual(status["rate_limit"]["limit"], "120")
        self.assertEqual([params.get("order_by") for _path, params in seen], ["market_cap", "market_cap"])
        self.assertEqual([params.get("chain") for _path, params in seen], ["ethereum", "base"])
        self.assertNotIn("Owners sorting", json.dumps(status))
        self.assertNotIn("num_owners", json.dumps(status))

    def test_server_error_is_retried_and_low_bucket_waits(self):
        seen = {"n": 0}

        def transport(_path, _params):
            seen["n"] += 1
            if seen["n"] < 3:
                return 500, {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "100", "X-RateLimit-Reset": "1060"}, {}
            return 200, {"X-RateLimit-Limit": "120", "X-RateLimit-Remaining": "10", "X-RateLimit-Reset": "1030"}, {"collections": [], "next": None}

        sleeps = []
        client = opensea.OpenSea("test-key", transport=transport, sleep=sleeps.append, clock=lambda: 1000.0)
        self.assertEqual(client.list_collections("ethereum", None), ([], None))
        self.assertEqual(client.calls, 3)
        self.assertIn(2, sleeps)
        self.assertEqual(client.list_collections("base", None), ([], None))
        self.assertEqual(sleeps[-1], 30.0)

    def test_price_sets_floor_usd_without_opensea_usd(self):
        class Fake:
            def __init__(self):
                self.calls = 0
                self.rate = {"limit": "120", "remaining": "100", "reset": "1"}
                self.raw = []
            def list_collections(self, chain, cursor):
                self.calls += 1
                if chain != "ethereum":
                    return [], None
                return [{"collection": "example", "contracts": [{"address": ADDR, "chain": "ethereum"}]}], None
            def listings(self, slug):
                self.calls += 1
                return [order(0.1, NOW + 10_000, digest="%02x" % n) for n in range(5)]
            def collection(self, slug):
                self.calls += 1
                return {"created_date": "2020-01-01", "fees": [{"fee": 1, "required": True}], "pricing_currencies": {}}
            def offers(self, slug, cursor):
                self.calls += 1
                return [], None
            def events(self, slug, after, cursor):
                self.calls += 1
                return [], None

        data = tempfile.mkdtemp()
        snapshot.run(data, Fake(), NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(status["eth_usd"], 2000)
        self.assertEqual(status["status"], "ok")
        self.assertGreaterEqual(status["rejections"]["sales"], 1)
        self.assertGreaterEqual(status["rejections"]["bidders"], 1)
        self.assertEqual(set(status["rejections"]), set(P.REJECTION_REASONS))
        self.assertEqual(status["floor_passing_this_run"], 0)
        self.assertEqual(books["pending"], [])
        self.assertTrue(books["seen"])
        self.assertGreater(status["opensea_calls"], 2)

    def test_pre_fix_clock_restarts_on_the_first_ok_run(self):
        data = tempfile.mkdtemp()
        root = os.path.join(data, "nft-pilot")
        os.makedirs(root)
        old = {
            "pilot_start": "2020-01-01",
            "complete_days": ["2020-01-01", "2020-01-02"],
            "passes": {"2020-01-01": ["nft_collection:eip155:1:" + ADDR]},
            "seen": {}, "offers": {}, "pending": [], "gas": [],
        }
        with open(os.path.join(root, "books.json"), "w") as fh:
            json.dump(old, fh)

        class Quiet:
            def __init__(self):
                self.calls = 0
                self.rate = {"limit": None, "remaining": None, "reset": None}
            def list_collections(self, chain, cursor):
                self.calls += 1
                return [], None

        snapshot.run(data, Quiet(), NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        books = json.load(open(os.path.join(root, "books.json")))
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(books["first_runs"], [0])
        self.assertEqual(books["pilot_start"], "2026-11-09")
        self.assertEqual(books["complete_days"], ["2026-11-09"])
        self.assertEqual(list(books["passes"]), ["2026-11-09"])
        self.assertEqual(books["passes"]["2026-11-09"], [])

    def test_wall_clock_stop_is_measured(self):
        class Fake:
            def __init__(self):
                self.calls = 0
                self.rate = {"limit": None, "remaining": None, "reset": None}
            def list_collections(self, chain, cursor):
                self.calls += 1
                raise AssertionError("paged after the deadline")

        data = tempfile.mkdtemp()
        fake = Fake()
        snapshot.run(
            data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9},
            eth_usd=2000, deadline=snapshot.time.time() - 1,
        )
        self.assertEqual(fake.calls, 0)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        # No pending rows were deferred, so a deadline stop is a measured run (0 passes).
        self.assertEqual(status["status"], "ok")
        self.assertFalse(status["partial"])
        self.assertEqual(status["stop"], "deadline")
        self.assertEqual(status["floor_passing_this_run"], 0)
        self.assertEqual(status["reason"], "deadline")
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(books["pilot_start"], "2026-11-09")
        self.assertEqual(books["first_runs"], [0])
        self.assertEqual(books["state_version"], "v0.3")
        self.assertEqual(snapshot.RUN_LIMIT_SECONDS, 11 * 60)

    def test_dry_run_never_publishes(self):
        text = open(os.path.join(ROOT, ".github", "workflows", "nft-snapshot.yml")).read()
        dispatch = text.split("workflow_dispatch:", 1)[1].split("pull_request:", 1)[0]
        self.assertIn("dry_run:", dispatch)
        self.assertIn("type: boolean", dispatch)
        self.assertIn("default: false", dispatch)
        self.assertIn("NFT_DRY_RUN: ${{ inputs.dry_run == true }}", text)
        steps = {}
        for block in text.split("      - name: ")[1:]:
            name = block.split("\n", 1)[0].strip()
            steps[name] = block
        publish = [n for n, b in steps.items() if "publish_nft_data.sh" in b]
        self.assertEqual(publish, ["Commit to nft-data"])
        self.assertIn("if: success() && inputs.dry_run != true", steps["Commit to nft-data"])
        self.assertIn("if: success() && inputs.dry_run != true", steps["Privacy guard on nft-data"])
        self.assertIn("if: inputs.dry_run != true", steps["Snapshot"])
        dry = steps["Dry run (temp dir, step summary only)"]
        self.assertIn("if: inputs.dry_run == true", dry)
        self.assertIn("--dry-run", dry)
        self.assertIn('--data-dir "$(mktemp -d)"', dry)
        self.assertNotIn("GITHUB_WORKSPACE/data", dry)
        for name, block in steps.items():
            if "git push" in block or "HEAD:" in block:
                self.assertIn("inputs.dry_run != true", block, name)
        # Code-level guards: the publish script refuses under NFT_DRY_RUN, and --dry-run
        # refuses any data dir that is not an empty directory outside a git checkout.
        import subprocess
        script = os.path.join(ROOT, "nft", "loop", "publish_nft_data.sh")
        repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "nft-data", repo], check=True)
        proc = subprocess.run(["bash", script], cwd=repo, text=True, capture_output=True,
                              env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "NFT_DRY_RUN": "true", "TOKEN": "t"})
        self.assertEqual(proc.returncode, 1)
        self.assertIn("dry run: refusing to publish", proc.stderr)
        self.assertFalse(snapshot._dry_dir_ok(repo))
        os.makedirs(os.path.join(repo, "nft-pilot"))
        self.assertFalse(snapshot._dry_dir_ok(os.path.join(repo, "nft-pilot")))
        self.assertTrue(snapshot._dry_dir_ok(tempfile.mkdtemp()))
        old = os.environ.pop("OPENSEA_API_KEY", None)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(snapshot.main(["--dry-run", "--data-dir", repo, "--code-sha", "x"]), 2)
        finally:
            if old is not None:
                os.environ["OPENSEA_API_KEY"] = old
        self.assertFalse(os.path.exists(os.path.join(repo, "nft-pilot", "status.json")))

    def test_dry_run_summary_is_counts_only(self):
        fake = self._universe_fake(n=4, pass_every=2)
        data = tempfile.mkdtemp()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        out = os.path.join(tempfile.mkdtemp(), "summary.md")
        text = snapshot.write_summary(os.path.join(data, "nft-pilot", "status.json"), out)
        self.assertEqual(open(out).read(), text)
        for key in ("stop", "opensea_calls", "floor_passing_this_run", "by_chain", "rejections"):
            self.assertIn("- %s:" % key, text)
        self.assertIn('"depth"', text)
        self.assertNotIn("c0", text)
        self.assertNotIn("0x", text)

    def test_deadline_during_pending_serve_is_partial(self):
        fake = self._universe_fake(n=30, pass_every=1)
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        snapshot.run(data, fake, NOW, "sha", gas, eth_usd=2000)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(books["first_runs"], [10])
        self.assertEqual(len(books["pending"]), 10)
        calls = fake.calls
        snapshot.run(data, fake, NOW + 3600, "sha", gas, eth_usd=2000, deadline=snapshot.time.time() - 1)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(fake.calls, calls)
        self.assertEqual(status["status"], "partial")
        self.assertTrue(status["partial"])
        self.assertIsNone(status["stop"])
        self.assertIsNone(status["floor_passing_this_run"])
        self.assertEqual(status["pending_deferred"], 10)
        self.assertEqual(status["reason"], "pending deferred")
        self.assertEqual(books["first_runs"], [10])
        self.assertEqual(books["measured_days"], ["2026-11-09"])
        self.assertTrue(all(p["tries"] == 1 for p in books["pending"]))

    def test_early_distinct_holds_collection_ids(self):
        # Two passers share nothing but each is one contract; early_distinct stores the
        # collection id (chain + contract), never a token id.
        fake = self._universe_fake(n=4, pass_every=1)
        data = tempfile.mkdtemp()
        snapshot.run(data, fake, NOW, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        import re
        self.assertEqual(len(books["early_distinct"]), 4)
        for asset in books["early_distinct"]:
            self.assertRegex(asset, r"^nft_collection:eip155:(1|8453):0x[0-9a-f]{40}$")
        self.assertEqual(sorted(books["early_distinct"]), sorted(p["asset_id"] for p in books["pending"]))
        # Re-passing the same collections in later runs does not grow the distinct count.
        snapshot.run(data, fake, NOW + 3600, "sha", {"ethereum": 10 ** 9, "base": 10 ** 9}, eth_usd=2000)
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(len(books["early_distinct"]), 4)

    def test_workflow_is_isolated(self):
        text = open(os.path.join(ROOT, ".github", "workflows", "nft-snapshot.yml")).read()
        script = open(os.path.join(ROOT, "nft", "loop", "publish_nft_data.sh")).read()
        self.assertIn("group: nft-snapshot", text)
        self.assertIn("contents: write", text)
        self.assertIn("publish_nft_data.sh", text)
        self.assertIn("git -C data init -b nft-data", text)
        self.assertIn("steps.nft_branch.outputs.exists != 'true'", text)
        self.assertIn('dest=nft-data', script)
        self.assertIn('if [ "$dest" != "nft-data" ]', script)
        self.assertIn('HEAD:${dest}', script)
        self.assertIn("AUTHORIZATION: basic", script)
        self.assertIn("x-access-token", script)
        self.assertLess(script.index("b64=$(printf"), script.index('echo "::add-mask::${b64}"'))
        self.assertLess(script.index('echo "::add-mask::${b64}"'), script.index("git_auth()"))
        self.assertNotIn("bearer", script.lower())
        self.assertNotIn("AUTHORIZATION: bearer", text)
        self.assertNotIn("HEAD:desk-data", text)
        self.assertNotIn("HEAD:desk-data", script)
        snap = text.split("name: Snapshot", 1)[1].split("name: Privacy", 1)[0]
        self.assertNotIn("SIGHTLINE_PRIVATE_TOKEN", snap)
        src = open(os.path.join(ROOT, "nft", "loop", "snapshot.py")).read()
        self.assertNotIn("SIGHTLINE_PRIVATE_TOKEN", src)
        self.assertNotIn("sightline-private", src)
        self.assertNotIn("push_raw", src)
        self.assertNotIn("group: solana-desk-scan", text)
        self.assertNotIn("group: desk-data", text)
        self.assertNotIn("upload-artifact", text)
        self.assertIn("secrets.OPENSEA_API_KEY", text)
        secrets = set(__import__("re").findall(r"secrets\.([A-Z0-9_]+)", text))
        self.assertTrue(secrets <= {
            "OPENSEA_API_KEY", "SIGHTLINE_PRIVATE_TOKEN", "PRIVACY_GUARD_PATTERNS",
            "PRIVACY_IDENTITY_HASHES", "RUGGER_ID_PEPPER",
        })
        for banned in ("solana-desk/", "build-site.py", "desk.run"):
            self.assertNotIn(banned, text)
        # v0.3: cron-job.org is the trigger of record; no GitHub schedule (no double runs).
        self.assertNotIn("schedule:", text)
        self.assertNotIn("cron:", text)
        self.assertIn("workflow_dispatch:", text)

    def test_opensea_401_publishes_then_fails(self):
        """A 401 still writes the error status (so the commit can publish it) and the job then fails."""
        class AuthClient:
            def __init__(self, key, *args, **kwargs):
                self.calls = 0
                self.rate = {}
                self.raw = []

            def list_collections(self, *_a, **_k):
                raise opensea.AuthError()

            def listings(self, *_a, **_k):
                raise opensea.AuthError()

            def collection(self, *_a, **_k):
                raise opensea.AuthError()

            def offers(self, *_a, **_k):
                raise opensea.AuthError()

            def events(self, *_a, **_k):
                raise opensea.AuthError()

            def contract(self, *_a, **_k):
                raise opensea.AuthError()

        class ListHttp:
            def __init__(self, code):
                self.calls = 0
                self.rate = {"limit": None, "remaining": None, "reset": None}
                self.code = code

            def list_collections(self, chain, cursor):
                self.calls += 1
                raise opensea.OpenSeaError(self.code)

        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        data = tempfile.mkdtemp()
        code = self._main_with_opensea(data, AuthClient, gas)
        self.assertEqual(code, 0)
        path = os.path.join(data, "nft-pilot", "status.json")
        status = json.load(open(path))
        self.assertEqual((status["status"], status["reason"]), ("error", "auth"))
        self.assertEqual(status["outage_runs"], 0)
        self.assertFalse(status["measured"])
        self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "books.json")))
        self.assertNotIn("test-key", json.dumps(status))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(snapshot.fail_on_published_status(path), 1)
        self.assertIn("error (auth)", err.getvalue())

        data = tempfile.mkdtemp()
        snapshot.run(data, self._universe_fake(n=2, pass_every=1), NOW, "sha", gas, eth_usd=2000)
        before = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(self._main_with_opensea(data, AuthClient, gas), 0)
        path = os.path.join(data, "nft-pilot", "status.json")
        status = json.load(open(path))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual((status["status"], status["reason"], status["outage_runs"]), ("error", "auth", 1))
        self.assertEqual(snapshot.fail_on_published_status(path), 1)
        self.assertEqual(books["v03_init_at"], before["v03_init_at"])
        self.assertEqual(books["first_runs"], before["first_runs"])
        self.assertEqual(books["passes"], before["passes"])
        self.assertEqual(books["measured_days"], before["measured_days"])
        self.assertEqual(len(books["outage_at"]), 1)
        self.assertEqual(books["outage_at"][0]["early"], len(before["first_runs"]))

        fresh = tempfile.mkdtemp()
        snapshot.run(fresh, ListHttp(500), NOW, "sha", gas, eth_usd=2000)
        path = os.path.join(fresh, "nft-pilot", "status.json")
        status = json.load(open(path))
        self.assertEqual(status["reason"], "opensea http 500")
        self.assertEqual(status["outage_runs"], 0)
        self.assertEqual(snapshot.fail_on_published_status(path), 0)
        snapshot.run(fresh, ListHttp(401), NOW + 3600, "sha", gas, eth_usd=2000)
        status = json.load(open(path))
        self.assertEqual((status["status"], status["reason"], status["outage_runs"]), ("partial", "opensea http 401", 1))
        self.assertEqual(snapshot.fail_on_published_status(path), 1)
        snapshot.run(fresh, ListHttp(403), NOW + 7200, "sha", gas, eth_usd=2000)
        status = json.load(open(path))
        self.assertEqual(status["reason"], "opensea http 403")
        self.assertEqual(status["outage_runs"], 2)
        self.assertTrue(snapshot.outage_status_fails({"status": "partial", "reason": "opensea http 403"}))
        self.assertFalse(snapshot.outage_status_fails({"status": "ok", "reason": None}))
        self.assertFalse(snapshot.outage_status_fails({"status": "partial", "reason": "opensea http 500"}))
        self.assertFalse(snapshot.outage_status_fails({"status": "partial", "reason": "pending deferred"}))
        self.assertEqual(snapshot.fail_on_published_status(os.path.join(fresh, "missing.json")), 1)

    def _main_with_opensea(self, data, client_cls, gas):
        old_key = os.environ.get("OPENSEA_API_KEY")
        old_client, old_gas, old_price = opensea.OpenSea, snapshot.fetch_gas, snapshot.fetch_eth_usd
        os.environ["OPENSEA_API_KEY"] = "test-key"
        opensea.OpenSea = client_cls
        snapshot.fetch_gas = lambda: gas
        snapshot.fetch_eth_usd = lambda *_a, **_k: (2000.0, "chainlink")
        try:
            return snapshot.main(["--data-dir", data, "--code-sha", "sha-401"])
        finally:
            opensea.OpenSea = old_client
            snapshot.fetch_gas = old_gas
            snapshot.fetch_eth_usd = old_price
            if old_key is None:
                os.environ.pop("OPENSEA_API_KEY", None)
            else:
                os.environ["OPENSEA_API_KEY"] = old_key

    def test_no_key_fails_the_published_run(self):
        data = tempfile.mkdtemp()
        old = os.environ.pop("OPENSEA_API_KEY", None)
        try:
            self.assertEqual(snapshot.main(["--data-dir", data, "--code-sha", "abc123"]), 0)
            path = os.path.join(data, "nft-pilot", "status.json")
            status = json.load(open(path))
            self.assertEqual((status["status"], status["reason"], status["outage_runs"]), ("no_key", "no key", 0))
            self.assertFalse(status["measured"])
            self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "books.json")))
            self.assertEqual(snapshot.fail_on_published_status(path), 1)
            first = open(path).read()
            self.assertEqual(snapshot.main(["--data-dir", data, "--code-sha", "abc123"]), 0)
            self.assertEqual(open(path).read(), first)
            self.assertEqual(snapshot.fail_on_published_status(path), 1)
        finally:
            if old is not None:
                os.environ["OPENSEA_API_KEY"] = old
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        data = tempfile.mkdtemp()
        snapshot.run(data, self._universe_fake(n=2, pass_every=1), NOW, "sha", gas, eth_usd=2000)
        before = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        old = os.environ.pop("OPENSEA_API_KEY", None)
        try:
            path = os.path.join(data, "nft-pilot", "status.json")
            self.assertEqual(snapshot.main(["--data-dir", data, "--code-sha", "abc123"]), 0)
            status = json.load(open(path))
            self.assertEqual(status["status"], "no_key")
            self.assertEqual(status["outage_runs"], 1)
            self.assertEqual(snapshot.fail_on_published_status(path), 1)
            self.assertEqual(snapshot.main(["--data-dir", data, "--code-sha", "abc123"]), 0)
            status = json.load(open(path))
            self.assertEqual(status["outage_runs"], 2)
            self.assertEqual(status["status"], "no_key")
            books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
            self.assertEqual(len(books["outage_at"]), 2)
            self.assertEqual(books["first_runs"], before["first_runs"])
            self.assertEqual(books["passes"], before["passes"])
            self.assertEqual(books["v03_init_at"], before["v03_init_at"])
        finally:
            if old is not None:
                os.environ["OPENSEA_API_KEY"] = old

    def test_outage_footnote_only_when_the_window_has_outages(self):
        init = int(NOW)
        fired = P.early_kill([], [], None, init + 7 * 86400, init, [])
        self.assertEqual((fired["backstop"], fired["result"]), ("fire", "STOPPED"))
        self.assertIsNone(P.with_outage_footnote(None, fired, [], init, None))
        rows = [{"t": init + 3600, "early": 0}, {"t": init + 86400, "early": 0}]
        self.assertEqual(
            P.with_outage_footnote(None, fired, rows, init, None),
            "2 outage runs in window; stop may reflect data outage, not supply",
        )
        late = [{"t": init + 7 * 86400 + 1, "early": 0}]
        self.assertIsNone(P.with_outage_footnote(None, fired, late, init, None))
        gate = P.early_kill([4, 4, 4], [], init, init + 3 * 3600)
        self.assertEqual((gate["gate1"], gate["backstop"], gate["result"]), ("fire", "pending", "STOPPED"))
        self.assertIsNone(P.with_outage_footnote(None, gate, [{"t": init - 10, "early": 0}], init - 100, init))
        one = [{"t": init + 1800, "early": 1}]
        self.assertEqual(
            P.with_outage_footnote(None, gate, one, init - 100, init),
            "1 outage runs in window; stop may reflect data outage, not supply",
        )
        # A later outage, after the third measured run, does not raise that count.
        self.assertEqual(
            P.with_outage_footnote(None, gate, one + [{"t": init + 5 * 3600, "early": 3}], init - 100, init),
            "1 outage runs in window; stop may reflect data outage, not supply",
        )
        pending = P.early_kill([4], [], init, init + 3600)
        self.assertIsNone(P.with_outage_footnote(None, pending, one, init, init))
        kept = P.with_outage_footnote("2 of 20 units: top seller or one day above 50% of 7d sales", fired, rows, init, None)
        self.assertTrue(kept.startswith("2 of 20 units"))
        self.assertIn("2 outage runs in window", kept)
        self.assertEqual(fired["result"], "STOPPED")
        self.assertEqual(gate["gate1_min_avg"], 6)

        class Broken:
            def __init__(self):
                self.calls = 0
                self.rate = {"limit": None, "remaining": None, "reset": None}

            def list_collections(self, chain, cursor):
                self.calls += 1
                raise opensea.OpenSeaError(500)

        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        data = tempfile.mkdtemp()
        snapshot.run(data, Broken(), NOW, "sha", gas, eth_usd=2000)
        self.assertEqual(snapshot._persist_outage(data, NOW + 86400), 1)
        self.assertEqual(snapshot._persist_outage(data, NOW + 2 * 86400), 2)
        snapshot.run(data, Broken(), NOW + 7 * 86400 - 60, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["outage_runs"], 2)
        self.assertEqual(status["early_kill"]["backstop"], "pending")
        self.assertNotIn("outage runs in window", status.get("verdict_footnote") or "")
        snapshot.run(data, Broken(), NOW + 7 * 86400, "sha", gas, eth_usd=2000)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(status["early_kill"]["result"], "STOPPED")
        self.assertEqual(status["early_kill"]["backstop"], "fire")
        self.assertIn("2 outage runs in window; stop may reflect data outage, not supply", status["verdict_footnote"])
        self.assertEqual(status["early_kill"], P.early_kill(
            books["first_runs"], books["early_distinct"], books["first_measured_at"],
            NOW + 7 * 86400, books["v03_init_at"], books["measured_days"],
        ))

        def thin(folder, when, room):
            fake = self._universe_fake(n=30, pass_every=2)
            fake.max_calls = 30 + room
            snapshot.run(folder, fake, when, "sha", gas, eth_usd=2000)

        data = tempfile.mkdtemp()
        thin(data, NOW, 0)
        self.assertEqual(snapshot._persist_outage(data, NOW + 1800), 1)
        thin(data, NOW + 3600, 12)
        thin(data, NOW + 7200, 12)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["early_kill"]["gate1"], "fire")
        self.assertEqual(status["early_kill"]["result"], "STOPPED")
        self.assertIn("1 outage runs in window; stop may reflect data outage, not supply", status["verdict_footnote"])

        # An outage before the first measured run is outside gate (i). It does not add the note.
        data = tempfile.mkdtemp()
        snapshot.run(data, Broken(), NOW, "sha", gas, eth_usd=2000)
        self.assertEqual(snapshot._persist_outage(data, NOW + 60), 1)
        thin(data, NOW + 3600, 0)
        thin(data, NOW + 7200, 12)
        thin(data, NOW + 10800, 12)
        status = json.load(open(os.path.join(data, "nft-pilot", "status.json")))
        self.assertEqual(status["outage_runs"], 1)
        self.assertEqual(status["early_kill"]["result"], "STOPPED")
        self.assertEqual(status["early_kill"]["gate1"], "fire")
        self.assertNotIn("outage runs in window", status.get("verdict_footnote") or "")

    def test_healthy_run_stays_green(self):
        data = tempfile.mkdtemp()
        gas = {"ethereum": 10 ** 9, "base": 10 ** 9}
        snapshot.run(data, self._universe_fake(n=4, pass_every=1), NOW, "sha", gas, eth_usd=2000)
        path = os.path.join(data, "nft-pilot", "status.json")
        status = json.load(open(path))
        books = json.load(open(os.path.join(data, "nft-pilot", "books.json")))
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["outage_runs"], 0)
        self.assertIsNone(status["verdict_footnote"])
        self.assertIsNone(status["early_kill"]["result"])
        self.assertEqual(status["early_kill"]["gate1"], "pending")
        self.assertEqual(status["early_kill"]["backstop"], "pending")
        self.assertEqual(snapshot.fail_on_published_status(path), 0)
        self.assertNotIn("outage_at", books)
        self.assertEqual(set(books), {
            "complete_days", "early_distinct", "first_measured_at", "first_runs", "gas",
            "measured_days", "offers", "passes", "pending", "pilot_start", "seen",
            "state_version", "usd_runs", "v03_init_at",
        })
        self.assertEqual(set(status) - {"outage_runs"}, {
            "attribution", "avoids_branch", "buyer_fee_eth", "by_chain", "commit_sha", "data_branch",
            "distinct_collections", "distinct_per_day_median", "early_kill", "eth_usd", "eth_usd_runs",
            "eth_usd_source", "flags", "floor_passing_this_run", "kind", "measured", "median_cost",
            "median_cost_seasoned", "nh04", "nh04_ready", "nh04_verdict", "null_cost_count",
            "null_cost_share", "null_cost_share_max", "opensea_calls", "pace_median", "pace_ready",
            "partial", "passers_footnoted", "pending", "pending_deferred", "pending_served", "ran_at",
            "rate_limit", "raw", "reason", "rejections", "rule", "schema_version", "screened", "status",
            "stop", "units", "unsellable_share", "verdict_footnote",
        })
        import jsonschema
        jsonschema.validate(status, json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json"))))

    def test_published_outage_step_runs_after_the_commit(self):
        text = open(os.path.join(ROOT, ".github", "workflows", "nft-snapshot.yml")).read()
        self.assertLess(text.index("name: Commit to nft-data"), text.index("name: Fail the job on a published outage"))
        head = "\n".join(text.split("name: Fail the job on a published outage", 1)[1].splitlines()[:6])
        self.assertIn("if: always() && inputs.dry_run != true", head)
        self.assertIn("--fail-on-outage", head)
        self.assertIn("nft-pilot/status.json", head)
        self.assertIn("if: inputs.dry_run == true", text)
        self.assertNotIn("schedule:", text)


if __name__ == "__main__":
    unittest.main()
