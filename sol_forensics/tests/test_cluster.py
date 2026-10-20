"""Edge weights. Same-slot alone must not merge. Infrastructure is not a funder link."""

import unittest

from sol_forensics.cluster import MERGE_AT, build_clusters


def node(wallet, **kwargs):
    base = {
        "slot": 10,
        "slot_offset": 5,
        "amount_raw": 100,
        "still_raw": 40,
        "supply": 1000,
        "signature": wallet + "-buy",
        "fee_payer": None,
        "fee_payer_signature": None,
        "hops": [],
    }
    base.update(kwargs)
    return base


class ClusterTests(unittest.TestCase):
    def test_same_slot_alone_does_not_cluster(self):
        nodes = {
            "A": node("A", slot=1, slot_offset=0),
            "B": node("B", slot=1, slot_offset=0),
        }
        clusters, _extra = build_clusters(nodes, [])
        self.assertEqual(clusters, [])

    def test_direct_transfer_clusters(self):
        nodes = {"A": node("A"), "B": node("B")}
        clusters, _extra = build_clusters(
            nodes,
            [{"src": "A", "dst": "B", "signature": "sig-direct", "asset": "MINT"}],
        )
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["cluster_id"], "c1")
        self.assertEqual(set(clusters[0]["wallets"]), {"A", "B"})
        self.assertGreaterEqual(clusters[0]["edges"][0]["weight"], MERGE_AT)
        self.assertEqual(clusters[0]["edges"][0]["signatures"], ["sig-direct"])
        self.assertEqual(clusters[0]["pct_supply_bought"], 20.0)

    def test_shared_funder_needs_the_time_bonus_to_merge(self):
        close = {
            "A": node("A", hops=[{"funder": "F", "funded_at": 1000, "signature": "sa", "label": None}]),
            "B": node("B", hops=[{"funder": "F", "funded_at": 1200, "signature": "sb", "label": None}]),
        }
        clusters, _extra = build_clusters(close, [], proximity_seconds=600)
        self.assertEqual(len(clusters), 1)
        self.assertTrue(clusters[0]["edges"][0]["proximity"])

        far = {
            "A": node("A", hops=[{"funder": "F", "funded_at": 1000, "signature": "sa", "label": None}]),
            "B": node("B", hops=[{"funder": "F", "funded_at": 5000, "signature": "sb", "label": None}]),
        }
        clusters, _extra = build_clusters(far, [], proximity_seconds=600)
        self.assertEqual(clusters, [])

    def test_fee_payer_alone_does_not_merge_until_combined(self):
        nodes = {
            "A": node("A", fee_payer="Payer", fee_payer_signature="fa"),
            "B": node("B", fee_payer="Payer", fee_payer_signature="fb"),
        }
        self.assertEqual(build_clusters(nodes, [])[0], [])
        nodes["A"]["slot"] = nodes["B"]["slot"] = 3
        nodes["A"]["slot_offset"] = nodes["B"]["slot_offset"] = 0
        clusters, _extra = build_clusters(nodes, [])
        self.assertEqual(len(clusters), 1)
        kinds = {e["type"] for e in clusters[0]["edges"]}
        self.assertEqual(kinds, {"shared_fee_payer", "same_slot_offset_0"})

    def test_infra_funder_does_not_link_and_a_wide_hub_is_suppressed(self):
        labeled = {
            "A": node("A", hops=[{"funder": "INF", "funded_at": 1, "signature": "s", "label": "INFRA:x"}]),
            "B": node("B", hops=[{"funder": "INF", "funded_at": 2, "signature": "s2", "label": "INFRA:x"}]),
        }
        clusters, extra = build_clusters(labeled, [], labels={"INF": {"kind": "infra", "name": "x"}})
        self.assertEqual(clusters, [])
        self.assertEqual(extra["probable_infra_hubs"], [])

        hops = []
        nodes = {}
        for i, name in enumerate(["A", "B", "C", "D"]):
            nodes[name] = node(
                name,
                hops=[{"funder": "HUB", "funded_at": i * 10_000, "signature": name, "label": None}],
            )
            hops.append(name)
        clusters, extra = build_clusters(nodes, [], hub_k=3, hub_tight_seconds=600)
        self.assertEqual(clusters, [])
        self.assertTrue(extra["probable_infra_hubs"][0]["suppressed"])
        self.assertEqual(extra["probable_infra_hubs"][0]["wallets"], 4)


if __name__ == "__main__":
    unittest.main()
