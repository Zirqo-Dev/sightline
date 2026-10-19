"""End-to-end run on a scripted RPC: SOL funding, USDC funding, a busy wallet, no paging of infra."""

import json
import pathlib
import unittest

from sol_forensics.engine import analyse, labels_from_config, _touch_buyer
from sol_forensics.parse import USDC_MINT
from sol_forensics.rpc import BudgetExceeded

MINT = "Mint11111111111111111111111111111111111111"
DEV = "Dev1111111111111111111111111111111111111111"
CURVE = "Curve111111111111111111111111111111111111"
BUYER = "Buyer11111111111111111111111111111111111"
FUNDER = "Funder1111111111111111111111111111111111"
GRAND = "Grand11111111111111111111111111111111111"
USDC_W = "UsdcW111111111111111111111111111111111111"
USDC_F = "UsdcF111111111111111111111111111111111111"
BUSY = "Busy1111111111111111111111111111111111111"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def key(pubkey, signer=False):
    return {"pubkey": pubkey, "signer": signer, "writable": True, "source": "transaction"}


def tx(keys, instructions, pre, post, pre_tok=None, post_tok=None, slot=1, bt=1_000_000, fee=5000):
    return {
        "slot": slot,
        "blockTime": bt,
        "version": 0,
        "transaction": {
            "message": {
                "accountKeys": keys,
                "instructions": instructions,
                "recentBlockhash": "x",
                "addressTableLookups": [],
            }
        },
        "meta": {
            "err": None,
            "fee": fee,
            "preBalances": pre,
            "postBalances": post,
            "preTokenBalances": pre_tok or [],
            "postTokenBalances": post_tok or [],
            "innerInstructions": [],
        },
    }


def bal(index, owner, amount, mint=MINT):
    return {
        "accountIndex": index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {"amount": str(amount), "decimals": 0},
    }


def sol_transfer(src, dst, lamports, bt):
    return tx(
        [key(src, True), key(dst)],
        [
            {
                "program": "system",
                "parsed": {
                    "type": "transfer",
                    "info": {"source": src, "destination": dst, "lamports": lamports},
                },
            }
        ],
        [lamports + 5000, 0],
        [0, lamports],
        slot=2,
        bt=bt,
        fee=5000,
    )


def usdc_transfer(src, dst, amount, bt):
    return tx(
        [key(src, True), key(dst), key("SrcAta"), key("DstAta")],
        [
            {
                "program": "spl-token",
                "parsed": {
                    "type": "transferChecked",
                    "info": {
                        "source": "SrcAta",
                        "destination": "DstAta",
                        "authority": src,
                        "mint": USDC_MINT,
                        "tokenAmount": {"amount": str(amount), "decimals": 6},
                    },
                },
            }
        ],
        [5000, 0, 0, 0],
        [0, 0, 0, 0],
        pre_tok=[bal(2, src, amount, USDC_MINT)],
        post_tok=[bal(3, dst, amount, USDC_MINT)],
        slot=3,
        bt=bt,
        fee=5000,
    )


class FakeRpc:
    def __init__(self, budget=5000):
        self.calls = __import__("collections").Counter()
        self.budget = budget
        self.commitment = "confirmed"
        self.seen = []
        self.supply = {"amount": "1000000", "decimals": 6, "uiAmountString": "1"}
        self.sig_pages = {}
        self.txs = {}
        self.balances = {}

    @property
    def used(self):
        return int(sum(self.calls.values()))

    def call(self, method, params):
        if self.used >= self.budget:
            raise BudgetExceeded(self.used, self.budget)
        self.calls[method] += 1
        self.seen.append((method, params))
        if method == "getTokenSupply":
            return {"value": self.supply}
        if method == "getSignaturesForAddress":
            return self._page(params[0], (params[1] or {}).get("before"))
        if method == "getTransaction":
            self.assert_version(params[1])
            return self.txs.get(params[0])
        if method == "getTokenAccountsByOwner":
            raw = self.balances.get(params[0], 0)
            if not raw:
                return {"value": []}
            return {
                "value": [
                    {
                        "account": {
                            "data": {"parsed": {"info": {"tokenAmount": {"amount": str(raw)}}}}
                        }
                    }
                ]
            }
        if method == "getTokenLargestAccounts":
            return {"value": []}
        if method == "getAccountInfo":
            return {
                "value": {
                    "executable": False,
                    "owner": "11111111111111111111111111111111",
                    "data": {"parsed": {"info": {"owner": params[0]}}},
                }
            }
        raise AssertionError(method)

    def assert_version(self, cfg):
        if cfg.get("maxSupportedTransactionVersion") != 1:
            raise AssertionError(cfg)

    def _page(self, address, before):
        pages = self.sig_pages.get(address) or [[]]
        if before is None:
            return pages[0]
        for i, page in enumerate(pages):
            if page and page[-1]["signature"] == before:
                return pages[i + 1] if i + 1 < len(pages) else []
        return []


def sig(name, slot, bt):
    return {"signature": name, "slot": slot, "blockTime": bt, "err": None}


def config(tmp):
    return {
        "_base": str(tmp),
        "paths": {
            "reports": "reports",
            "index": "index.jsonl",
            "cache": "cache.json",
            "panel": "panel.json",
        },
        "window": {
            "buys": 10,
            "seconds": 60,
            "hops": 2,
            "sig_pages": 50,
            "wallet_pages": 5,
            "top_buyers": 100,
            "oldest_txs": 3,
            "fresh_hours": 24,
            "old_wallet_days": 30,
            "hub_k": 25,
            "fund_proximity_seconds": 600,
            "hub_tight_seconds": 600,
        },
        "flags": {
            "HIGH_SNIPE": 20,
            "CLUSTERED": 10,
            "FRESH_HEAVY": 0.5,
            "DEV_HEAVY": 5,
            "STILL_LOADED": 5,
        },
        "known_infra": [
            {"address": DEV, "name": "Example deployer", "source_url": "https://example.com/dev"}
        ],
        "known_cex": [{"address": "nolabel", "name": "dropped"}],
        "stablecoins": [
            {"mint": USDC_MINT, "symbol": "USDC", "source_url": "https://example.com/usdc"}
        ],
    }


def scripted():
    rpc = FakeRpc()
    create = tx(
        [key(DEV, True), key(CURVE), key(MINT)],
        [
            {
                "program": "system",
                "parsed": {
                    "type": "createAccount",
                    "info": {
                        "source": DEV,
                        "newAccount": CURVE,
                        "owner": PUMP,
                        "lamports": 1000,
                        "space": 1,
                    },
                },
            }
        ],
        [10, 0, 0],
        [9, 1, 0],
        post_tok=[bal(0, DEV, 100), bal(1, CURVE, 900)],
        slot=1000,
        bt=1_000_000,
    )
    buy = tx(
        [key(BUYER, True)],
        [],
        [5],
        [4],
        post_tok=[bal(0, BUYER, 50)],
        slot=1010,
        bt=1_000_010,
    )
    usdc_buy = tx(
        [key(USDC_W, True)],
        [],
        [5],
        [4],
        post_tok=[bal(0, USDC_W, 25)],
        slot=1020,
        bt=1_000_020,
    )
    busy_buy = tx(
        [key(BUSY, True)],
        [],
        [5],
        [4],
        post_tok=[bal(0, BUSY, 10)],
        slot=1030,
        bt=1_000_030,
    )
    history = [
        sig("sig-busy-buy", 1030, 1_000_030),
        sig("sig-usdc-buy", 1020, 1_000_020),
        sig("sig-buy", 1010, 1_000_010),
        sig("sig-create", 1000, 1_000_000),
    ]
    # Newest first, one short page, so creation is reached.
    rpc.sig_pages[CURVE] = [list(reversed(history))]
    rpc.sig_pages[MINT] = [list(reversed(history))]
    rpc.txs.update(
        {
            "sig-create": create,
            "sig-buy": buy,
            "sig-usdc-buy": usdc_buy,
            "sig-busy-buy": busy_buy,
            "sig-fund-buyer": sol_transfer(FUNDER, BUYER, 2_000_000_000, 999_900),
            "sig-fund-funder": sol_transfer(GRAND, FUNDER, 3_000_000_000, 999_000),
            "sig-fund-usdc": usdc_transfer(USDC_F, USDC_W, 40_000_000, 900_000),
        }
    )
    rpc.sig_pages[BUYER] = [[sig("sig-fund-buyer", 900, 999_900)]]
    rpc.sig_pages[FUNDER] = [[sig("sig-fund-funder", 800, 999_000)]]
    rpc.sig_pages[USDC_W] = [[sig("sig-fund-usdc", 850, 900_000)]]
    rpc.sig_pages[USDC_F] = [[sig("sig-nowhere", 1, 1)]]
    rpc.txs["sig-nowhere"] = sol_transfer("Nobody", USDC_F, 1_000_000, 1)
    busy_pages = []
    n = 0
    for _page in range(5):
        page = []
        for _i in range(1000):
            n += 1
            page.append(sig(f"busy-hist-{n}", 5000 - n, 100))
        busy_pages.append(page)
    rpc.sig_pages[BUSY] = busy_pages
    rpc.balances = {BUYER: 50, USDC_W: 25, BUSY: 0, DEV: 80}
    return rpc


class EngineTests(unittest.TestCase):
    def test_traces_sol_and_usdc_and_does_not_page_infra_or_invent_a_busy_funder(self):
        tmp = pathlib.Path(self.id().replace(".", "_"))
        # unittest doesn't give a tmp dir helper without pytest; use the stdlib.
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            rpc = scripted()
            report = analyse(
                MINT,
                config=config(tmp),
                rpc=rpc,
                now=1_800_000_000,
                pump_lookup=lambda _m: {"mint": MINT, "bonding_curve": CURVE, "symbol": "TEST"},
                symbol_lookup=lambda _m: "TEST",
            )
            self.assertFalse(report["data_quality"]["partial"])
            self.assertFalse(report["data_quality"]["history_too_deep"])
            self.assertEqual(report["dev_wallet"], DEV)
            self.assertEqual(report["symbol"], "TEST")
            wallets = {b["wallet"] for b in report["buyers"]}
            self.assertEqual(wallets, {DEV, BUYER, USDC_W, BUSY})
            self.assertNotIn(CURVE, wallets)
            paged = [params[0] for method, params in rpc.seen if method == "getSignaturesForAddress"]
            self.assertNotIn(DEV, paged)
            self.assertIn(CURVE, paged)
            by_wallet = {b["wallet"]: b for b in report["buyers"]}
            buyer = by_wallet[BUYER]["funding"]
            self.assertTrue(buyer["fresh"])
            self.assertEqual(buyer["hops"][0]["funder"], FUNDER)
            self.assertEqual(buyer["hops"][0]["asset"], "SOL")
            self.assertEqual(buyer["hops"][0]["kind"], "system.transfer")
            self.assertEqual(buyer["hops"][1]["funder"], GRAND)
            usdc = by_wallet[USDC_W]["funding"]
            self.assertEqual(usdc["hops"][0]["asset"], "USDC")
            self.assertEqual(usdc["hops"][0]["funder"], USDC_F)
            self.assertEqual(usdc["hops"][0]["amount_raw"], "40000000")
            busy = by_wallet[BUSY]["funding"]
            self.assertEqual(busy["status"], "unresolved_busy")
            self.assertIsNone(busy["hops"][0]["funder"])
            self.assertGreaterEqual(report["data_quality"]["wallets_unresolved_busy"], 1)
            versions = [
                params[1].get("maxSupportedTransactionVersion")
                for method, params in rpc.seen
                if method == "getTransaction"
            ]
            self.assertTrue(versions)
            self.assertTrue(all(v == 1 for v in versions))
            # Second run reuses the funding cache and does not page the buyer again.
            before = paged.count(BUYER)
            report2 = analyse(
                MINT,
                config=config(tmp),
                rpc=rpc,
                now=1_800_000_100,
                pump_lookup=lambda _m: {"mint": MINT, "bonding_curve": CURVE, "symbol": "TEST"},
                symbol_lookup=lambda _m: None,
            )
            paged2 = [params[0] for method, params in rpc.seen if method == "getSignaturesForAddress"]
            self.assertEqual(paged2.count(BUYER), before)
            self.assertGreater(report2["data_quality"]["cache_hits"], 0)
            panel = json.loads((tmp / "panel.json").read_text(encoding="utf-8"))
            self.assertIn("not trading signals", panel["disclaimer"])
            blob = json.dumps(panel)
            for code in ("HIGH_SNIPE", "CLUSTERED", "FRESH_HEAVY", "DEV_HEAVY", "STILL_LOADED", "DEV_PAID_BUY"):
                self.assertNotIn(code, blob)

    def test_history_cap_is_partial_only_as_too_deep_and_budget_does_not_crash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            tmp = pathlib.Path(raw)
            rpc = FakeRpc()
            page = [sig(f"s{i}", 10_000 - i, 50) for i in range(1000)]
            rpc.sig_pages[CURVE] = [page]
            report = analyse(
                MINT,
                config=config(tmp),
                rpc=rpc,
                now=10,
                max_pages=1,
                pump_lookup=lambda _m: {"mint": MINT, "bonding_curve": CURVE},
                symbol_lookup=lambda _m: None,
            )
            self.assertTrue(report["data_quality"]["history_too_deep"])
            self.assertEqual(report["buyers"], [])
            self.assertFalse(report["data_quality"]["partial"])

            tiny = FakeRpc(budget=1)
            tiny.sig_pages[MINT] = [[sig("only", 1, 1)]]
            stopped = analyse(
                MINT,
                config=config(tmp),
                rpc=tiny,
                now=11,
                pump_lookup=lambda _m: None,
                symbol_lookup=lambda _m: None,
            )
            self.assertTrue(stopped["data_quality"]["partial"])
            self.assertEqual(stopped["data_quality"]["partial_reason"], "call budget reached")
            self.assertTrue((tmp / "reports" / f"{MINT}.json").is_file())

    def test_later_receipt_does_not_inflate_slot0_or_rewrite_the_first_fee_payer(self):
        buyers = {}
        _touch_buyer(
            buyers,
            "Wallet",
            100,
            slot=10,
            offset=0,
            block_time=1,
            signature="first",
            fee_payer="Wallet",
            dev="Dev",
            label=None,
        )
        _touch_buyer(
            buyers,
            "Wallet",
            900,
            slot=80,
            offset=70,
            block_time=40,
            signature="later",
            fee_payer="Someone",
            dev="Dev",
            label=None,
        )
        _touch_buyer(
            buyers,
            "Other",
            50,
            slot=20,
            offset=10,
            block_time=5,
            signature="self",
            fee_payer="Other",
            dev="Dev",
            label=None,
        )
        _touch_buyer(
            buyers,
            "Other",
            25,
            slot=30,
            offset=20,
            block_time=9,
            signature="devpaid",
            fee_payer="Dev",
            dev="Dev",
            label=None,
        )
        row = buyers["Wallet"]
        self.assertEqual(row["amount_raw"], 1000)
        self.assertEqual(row["amount_slot0_raw"], 100)
        self.assertEqual(row["signature"], "first")
        self.assertEqual(row["fee_payer"], "Wallet")
        self.assertTrue(row["fee_payer_is_buyer"])
        self.assertIsNone(row["dev_paid_signature"])
        other = buyers["Other"]
        self.assertEqual(other["signature"], "self")
        self.assertTrue(other["fee_payer_is_buyer"])
        self.assertEqual(other["dev_paid_signature"], "devpaid")
        self.assertEqual(other["amount_slot0_raw"], 0)

    def test_shipped_labels_have_sources_and_include_the_solada_deployer(self):
        cfg = json.loads((ROOT / "solada" / "forensics" / "config.json").read_text(encoding="utf-8"))
        labels, dropped = labels_from_config(cfg)
        self.assertEqual(dropped, [])
        self.assertIn("A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ", labels)
        self.assertEqual(labels["A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ"]["kind"], "infra")
        self.assertEqual(cfg["known_cex"], [])
        for row in cfg["known_infra"] + cfg["stablecoins"]:
            self.assertTrue(str(row["source_url"]).startswith("http"))
            self.assertTrue(row.get("address") or row.get("mint"))


if __name__ == "__main__":
    unittest.main()
