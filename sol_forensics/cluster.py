"""Union-find clustering. Merge only when the summed edge weight is at least 1.

Same-slot alone is 0.5 and never clusters two wallets by itself. Known CEX and
infrastructure addresses never become shared-funder links. A funder that touches
more than K wallets over a wide span of time is treated as probable infrastructure.
"""

from __future__ import annotations

from itertools import combinations

W_DIRECT = 1.0
W_SHARED_FUNDER = 0.8
W_SLOT0 = 0.5
W_PROXIMITY = 0.2
W_FEE_PAYER = 0.8
MERGE_AT = 1.0


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def add(self, item):
        self.parent.setdefault(item, item)

    def find(self, item):
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _pct(raw, supply):
    if not supply:
        return None
    return round(int(raw) / int(supply) * 100, 4)


def _pair(a, b):
    return (a, b) if a < b else (b, a)


def _blocked(address, labels):
    return bool(labels.get(address)) if labels else False


def build_clusters(
    nodes,
    transfers,
    *,
    labels=None,
    hub_k=25,
    proximity_seconds=600,
    hub_tight_seconds=600,
):
    """Cluster analysed wallets.

    `nodes` maps wallet -> {slot, slot_offset, amount_raw, still_raw, supply,
    fee_payer, fee_payer_signature, hops: [{funder, funded_at, signature, label}]}.
    `transfers` is a list of {src, dst, signature, asset}.
    `labels` maps address -> {kind, name} for CEX and infrastructure.
    """
    labels = labels or {}
    weights = {}
    edges = {}

    def add(a, b, weight, edge):
        if not a or not b or a == b or weight <= 0:
            return
        key = _pair(a, b)
        weights[key] = weights.get(key, 0.0) + float(weight)
        edges.setdefault(key, []).append(edge)

    for tr in transfers or []:
        add(
            tr.get("src"),
            tr.get("dst"),
            W_DIRECT,
            {
                "type": "direct_transfer",
                "weight": W_DIRECT,
                "asset": tr.get("asset"),
                "signatures": [tr.get("signature")] if tr.get("signature") else [],
            },
        )

    slot_groups = {}
    for wallet, node in nodes.items():
        if node.get("slot_offset") == 0 and node.get("slot") is not None:
            slot_groups.setdefault(node["slot"], []).append(wallet)
    for slot, wallets in slot_groups.items():
        for a, b in combinations(sorted(set(wallets)), 2):
            add(
                a,
                b,
                W_SLOT0,
                {
                    "type": "same_slot_offset_0",
                    "weight": W_SLOT0,
                    "slot": slot,
                    "signatures": _sigs_for(nodes, a, b),
                },
            )

    fee_groups = {}
    for wallet, node in nodes.items():
        payer = node.get("fee_payer")
        if payer and payer != wallet:
            fee_groups.setdefault(payer, []).append(wallet)
    for payer, wallets in fee_groups.items():
        uniq = sorted(set(wallets))
        for a, b in combinations(uniq, 2):
            add(
                a,
                b,
                W_FEE_PAYER,
                {
                    "type": "shared_fee_payer",
                    "weight": W_FEE_PAYER,
                    "fee_payer": payer,
                    "signatures": _sigs_for(nodes, a, b, "fee_payer_signature"),
                },
            )

    fund_groups = {}
    for wallet, node in nodes.items():
        for hop in node.get("hops") or []:
            funder = hop.get("funder")
            if not funder or hop.get("label") or _blocked(funder, labels):
                continue
            fund_groups.setdefault(funder, []).append(
                (wallet, hop.get("funded_at"), hop.get("signature"))
            )

    hubs = []
    suppressed = set()
    for funder, items in fund_groups.items():
        wallets = sorted({w for w, _t, _s in items})
        times = [t for _w, t, _s in items if isinstance(t, (int, float))]
        span = int(max(times) - min(times)) if len(times) >= 2 else 0
        wide = len(wallets) > hub_k and (len(times) < 2 or span > hub_tight_seconds)
        if len(wallets) > hub_k:
            hubs.append(
                {
                    "funder": funder,
                    "wallets": len(wallets),
                    "span_seconds": span,
                    "suppressed": wide,
                }
            )
        if wide:
            suppressed.add(funder)

    for funder, items in fund_groups.items():
        if funder in suppressed:
            continue
        by_wallet = {}
        for wallet, funded_at, sig in items:
            # Keep the earliest funding time from this funder.
            prev = by_wallet.get(wallet)
            if prev is None or (
                isinstance(funded_at, (int, float))
                and (prev[0] is None or funded_at < prev[0])
            ):
                by_wallet[wallet] = (funded_at, sig)
        wallets = sorted(by_wallet)
        for a, b in combinations(wallets, 2):
            ta, sa = by_wallet[a]
            tb, sb = by_wallet[b]
            close = (
                isinstance(ta, (int, float))
                and isinstance(tb, (int, float))
                and abs(ta - tb) <= proximity_seconds
            )
            weight = W_SHARED_FUNDER + (W_PROXIMITY if close else 0.0)
            add(
                a,
                b,
                weight,
                {
                    "type": "shared_funder",
                    "weight": weight,
                    "funder": funder,
                    "proximity": close,
                    "signatures": [s for s in (sa, sb) if s],
                },
            )

    uf = _UnionFind()
    for wallet in nodes:
        uf.add(wallet)
    for (a, b), weight in weights.items():
        if a in nodes and b in nodes and weight >= MERGE_AT:
            uf.union(a, b)

    groups = {}
    for wallet in nodes:
        groups.setdefault(uf.find(wallet), []).append(wallet)

    supply = None
    for node in nodes.values():
        if node.get("supply"):
            supply = int(node["supply"])
            break

    clusters = []
    for members in groups.values():
        if len(members) < 2:
            continue
        bought = sum(int(nodes[w].get("amount_raw") or 0) for w in members)
        held = sum(int(nodes[w].get("still_raw") or 0) for w in members)
        member_set = set(members)
        cluster_edges = []
        for (a, b), elist in edges.items():
            if a in member_set and b in member_set:
                for edge in elist:
                    cluster_edges.append(
                        {
                            "wallets": [a, b],
                            **edge,
                        }
                    )
        cluster_edges.sort(key=lambda e: (-e["weight"], e["type"], e["wallets"]))
        ordered = sorted(
            members,
            key=lambda w: (-int(nodes[w].get("amount_raw") or 0), w),
        )
        clusters.append(
            {
                "wallets": ordered,
                "pct_supply_bought": _pct(bought, supply),
                "pct_supply_still_held": _pct(held, supply),
                "edges": cluster_edges,
            }
        )
    clusters.sort(
        key=lambda c: (
            -(c["pct_supply_bought"] or 0),
            -(c["pct_supply_still_held"] or 0),
            c["wallets"][0],
        )
    )
    for i, cluster in enumerate(clusters, start=1):
        cluster["cluster_id"] = f"c{i}"
    return clusters, {"probable_infra_hubs": hubs}


def _sigs_for(nodes, a, b, field="signature"):
    out = []
    for wallet in (a, b):
        sig = (nodes.get(wallet) or {}).get(field) or (nodes.get(wallet) or {}).get("signature")
        if sig and sig not in out:
            out.append(sig)
    return out
