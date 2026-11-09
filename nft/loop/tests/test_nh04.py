"""Fixture tests for the NH-04 snapshot pilot. No live HTTP."""
import json
import os
import sys
import tempfile
import unittest
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
        gas = 0.01
        got = P.round_trip(1.0, 0.0, 0.9, 0.01, 0.05, gas)
        entry, exit_v = 1.01, 0.9 - 0.9 * 0.01 - 0.9 * 0.05 - gas
        self.assertAlmostEqual(got["entry_eth"], entry)
        self.assertAlmostEqual(got["exit_eth"], exit_v)
        self.assertAlmostEqual(got["cost"], (entry - exit_v) / entry)
        self.assertFalse(got["unsellable"])
        with_buyer = P.round_trip(1.0, 0.02, 0.9, 0.01, 0.05, gas)
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

    def test_universe_filters_and_identity(self):
        ok = {
            "n_contracts": 1, "shared": False, "standard": "erc721", "mint_ok": True, "disabled": False,
            "floor_eth": 0.1, "floor_usd": 200, "gas_eth": 0.0005, "listings": 5, "sales": 10, "buyers": 6,
            "bidders": 3, "top_share": 0.2, "sales_truncated": False,
        }
        self.assertEqual(P.rejection(ok), [])
        flips = {
            "floor": {"floor_usd": 250.01}, "gas": {"gas_eth": 0.002}, "listings": {"listings": 4},
            "sales": {"sales": 9}, "buyers": {"buyers": 5}, "bidders": {"bidders": 2},
            "concentration": {"top_share": 0.201}, "shared": {"shared": True}, "standard": {"standard": "erc1155"},
            "contracts": {"n_contracts": 2}, "mint": {"mint_ok": False}, "sales_truncated": {"sales_truncated": True},
        }
        for reason, patch in flips.items():
            self.assertIn(reason, P.rejection({**ok, **patch}))
        aid = P.asset_id("ethereum", ADDR.upper())
        self.assertEqual(aid, "nft_collection:eip155:1:" + ADDR)
        self.assertEqual(P.parse_asset_id(aid)["asset_type"], "nft_collection")
        self.assertEqual(P.parse_asset_id("nft_collection:eip155:8453:" + ADDR)["chain"], "eip155:8453")
        self.assertRaises(ValueError, P.asset_id, "solana", ADDR)
        self.assertTrue(P.mint_ok("2020-01-01", NOW))
        self.assertFalse(P.mint_ok("2026-11-01", NOW))
        self.assertFalse(P.seasoned(["2026-11-0%d" % i for i in range(1, 7)], "2026-11-06"))
        self.assertTrue(P.seasoned(["2026-11-%02d" % i for i in range(1, 8)], "2026-11-07"))

    def test_gas_uses_the_higher_of_now_and_median(self):
        sample = [{"t": NOW, "chain": "ethereum", "wei": 100}]
        self.assertEqual(P.gas_quote(200, sample, "ethereum", NOW), 200)
        self.assertEqual(P.gas_quote(10, sample, "ethereum", NOW), 55)
        self.assertIsNone(P.gas_quote(None, sample, "ethereum", NOW))
        self.assertLess(P.upper_bound_calls(), P.MAX_CALLS)
        self.assertLess(P.MAX_CALLS, P.HOURLY_LIMIT)
        self.assertEqual(P.ORDER_BY, "num_owners")
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
        buyers = [BUYER, BUYER] + ["0x" + format(i, "02x") * 20 for i in range(8)]
        events = [{"event_type": "sale", "event_timestamp": int(NOW - 100), "buyer": b, "seller": SELLER} for b in buyers]

        class Fake:
            def __init__(self):
                self.calls, self.raw = 0, []
                self.rate = {"limit": "600", "remaining": "500", "reset": "9"}
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
        snapshot.run(data, fake, NOW, "sha-1", gas, token="", push=lambda *_a: "dropped")
        self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "units.jsonl")))
        fake.listings_now, fake.offers_now = listings_t1, offers_t1
        snapshot.run(data, fake, NOW + 3600, "sha-2", gas, token="")
        fake.offers_now = offers_new
        snapshot.run(data, fake, NOW + 7200, "sha-3", gas, token="")
        units = [json.loads(line) for line in open(os.path.join(data, "nft-pilot", "units.jsonl"))]
        self.assertEqual(len(units), 2)
        priced, unsold = units
        self.assertAlmostEqual(priced["entry_eth"], 0.15 + P.wei_to_eth(10 ** 9))
        self.assertNotAlmostEqual(priced["entry_eth"], 0.10 + P.wei_to_eth(10 ** 9))
        self.assertAlmostEqual(priced["exit_eth"], 0.09 * (1 - 0.01 - 0.05) - P.wei_to_eth(10 ** 9))
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
        self.assertEqual(status["data_branch"], "nft-data")
        self.assertEqual(status["avoids_branch"], "desk-data")
        self.assertGreater(status["opensea_calls"], 0)
        self.assertEqual(status["rate_limit"]["limit"], "600")

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
        self.assertFalse(os.path.exists(os.path.join(data, "nft-pilot", "units.jsonl")))
        import jsonschema
        schema = json.load(open(os.path.join(ROOT, "schema", "nft-snapshot.schema.json")))
        jsonschema.validate(status, schema)

    def test_rate_limit_backoff_is_one_retry(self):
        seen = {"n": 0}
        def transport(_path, _params):
            seen["n"] += 1
            if seen["n"] == 1:
                return 429, {"Retry-After": "0", "X-RateLimit-Remaining": "0"}, {}
            return 200, {"X-RateLimit-Limit": "600", "X-RateLimit-Remaining": "599", "X-RateLimit-Reset": "10"}, {"collections": [], "next": None}
        client = opensea.OpenSea("test-key", transport=transport, sleep=lambda _s: None)
        self.assertEqual(client.list_collections("ethereum", None), ([], None))
        self.assertEqual(client.calls, 2)
        self.assertEqual(client.rate["remaining"], "599")
        self.assertNotIn("test-key", json.dumps(client.raw))

    def test_workflow_is_isolated(self):
        text = open(os.path.join(ROOT, ".github", "workflows", "nft-snapshot.yml")).read()
        self.assertIn("group: nft-snapshot", text)
        self.assertIn("HEAD:nft-data", text)
        self.assertNotIn("HEAD:desk-data", text)
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


if __name__ == "__main__":
    unittest.main()
