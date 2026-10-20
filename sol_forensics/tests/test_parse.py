"""Parsers against saved mainnet transactions, including v0 lookup tables and v1."""

import json
import pathlib
import unittest

from sol_forensics.parse import (
    SOL_OVER_STABLE_LAMPORTS,
    USDC_MINT,
    fee_payer,
    find_incoming,
    message_view,
    protocol_accounts_created,
    token_deltas,
)

FIX = pathlib.Path(__file__).parent / "fixtures"
USDC = {USDC_MINT: "USDC"}
QBITS = "Ceuu7uQHwhtHuMhPWrWL9dgri3FaGJZFkTy6FoDow7NA"
DEV = "A1qaJWQDfC9T7AcSRNwfxgiJrHUzhpJ3f8qTDUMVVcXQ"
CURVE = "DAkroVvy5Gvbost9F7uXogiGein2ikNyrJipmyxzyuj6"
HCYV = "HCYvN1ukbRjWiBi1wXaU4fSRh8ottmA9KZCifJAPFi7Z"
BUYER_V1 = "8Fux3NDfxHxiK9W9ixzvQTw4GGgMazNwRvDS5Gvwejwk"
SOL_WALLET = "9Uwz2gwYBnZLonJGLQhzFbU2XiJqBj4hcEsJU9pN39ik"
SOL_FUNDER = "is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV"
USDC_WALLET = "ExfMqYHCdyfDwgLy4xTVYdB1wG2Drs3Rf4jrYKseiUzp"
USDC_FUNDER = "DPqsobysNf5iA9w7zrQM8HLzCKZEDMkZsWbiidsAt1xo"


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))["tx"]


def key(pubkey, signer=False):
    return {"pubkey": pubkey, "signer": signer, "writable": True, "source": "transaction"}


def bare_tx(keys, instructions, pre, post, pre_tok=None, post_tok=None, fee=0, err=None):
    return {
        "slot": 1,
        "blockTime": 10,
        "version": "legacy",
        "transaction": {
            "message": {
                "accountKeys": keys,
                "instructions": instructions,
                "recentBlockhash": "x",
            }
        },
        "meta": {
            "err": err,
            "fee": fee,
            "preBalances": pre,
            "postBalances": post,
            "preTokenBalances": pre_tok or [],
            "postTokenBalances": post_tok or [],
            "innerInstructions": [],
        },
    }


class FixtureParseTests(unittest.TestCase):
    def test_v0_lookup_table_create_deltas(self):
        tx = load("v0_alt_create.json")
        view = message_view(tx)
        self.assertEqual(view["version"], 0)
        self.assertTrue(view["address_table_lookups"])
        self.assertFalse(view["transaction_config"])
        deltas, missing = token_deltas(tx, QBITS)
        self.assertEqual(missing, 0)
        self.assertGreater(deltas[DEV], 0)
        self.assertGreater(deltas[CURVE], 0)
        self.assertEqual(fee_payer(tx), DEV)
        self.assertIn(CURVE, protocol_accounts_created(tx))
        self.assertNotIn(DEV, protocol_accounts_created(tx))

    def test_v1_buy_has_no_lookup_tables(self):
        tx = load("v1_buy.json")
        view = message_view(tx)
        self.assertEqual(view["version"], 1)
        self.assertTrue(view["transaction_config"])
        self.assertFalse(view["address_table_lookups"])
        deltas, missing = token_deltas(tx, QBITS)
        self.assertEqual(missing, 0)
        self.assertGreater(deltas[BUYER_V1], 0)
        self.assertEqual(fee_payer(tx), BUYER_V1)

    def test_dev_paid_transfer_fee_payer_is_not_the_buyer(self):
        tx = load("v0_dev_paid_transfer.json")
        deltas, _missing = token_deltas(tx, QBITS)
        self.assertGreater(deltas[HCYV], 0)
        self.assertLess(deltas[DEV], 0)
        self.assertEqual(fee_payer(tx), DEV)
        self.assertNotEqual(fee_payer(tx), HCYV)

    def test_sol_system_transfer(self):
        tx = load("legacy_sol_transfer.json")
        self.assertEqual(message_view(tx)["version"], "legacy")
        found = find_incoming(tx, SOL_WALLET, USDC)
        self.assertEqual(found["funder"], SOL_FUNDER)
        self.assertEqual(found["asset"], "SOL")
        self.assertEqual(found["amount_raw"], 31000000000)
        self.assertEqual(found["kind"], "system.transfer")

    def test_usdc_transfer_checked(self):
        tx = load("legacy_usdc_transfer.json")
        found = find_incoming(tx, USDC_WALLET, USDC)
        self.assertEqual(found["funder"], USDC_FUNDER)
        self.assertEqual(found["asset"], "USDC")
        self.assertEqual(found["mint"], USDC_MINT)
        self.assertEqual(found["amount_raw"], 57126370)
        self.assertEqual(found["kind"], "spl-token.transferChecked")
        self.assertIsNone(find_incoming(tx, USDC_FUNDER, USDC))

    def test_failed_transaction_is_not_incoming(self):
        tx = bare_tx([key("A", True), key("B")], [], [1], [1], err={"InstructionError": [0, "Custom"]})
        self.assertIsNone(find_incoming(tx, "B", USDC))


class FallbackTests(unittest.TestCase):
    def test_balance_delta_when_nothing_is_parsed(self):
        wallet, funder, other = "Wallet", "Funder", "Other"
        tx = bare_tx(
            [key(wallet, True), key(funder, True), key(other)],
            [],
            [1_000_000, 50_000_000, 10],
            [2_000_000, 48_995_000, 10],
            fee=5_000,
        )
        found = find_incoming(tx, wallet, USDC)
        self.assertEqual(found["kind"], "balance-diff")
        self.assertEqual(found["funder"], funder)
        self.assertEqual(found["asset"], "SOL")
        self.assertEqual(found["amount_raw"], 1_005_000)

    def test_parsed_sol_beats_a_larger_balance_move(self):
        wallet, real, other = "Wallet", "Real", "Other"
        tx = bare_tx(
            [key(wallet, True), key(real, True), key(other)],
            [
                {
                    "program": "system",
                    "parsed": {
                        "type": "transfer",
                        "info": {"source": real, "destination": wallet, "lamports": 1000},
                    },
                }
            ],
            [0, 5_000, 200_000],
            [1000, 4_000, 100_000],
            fee=0,
        )
        found = find_incoming(tx, wallet, USDC)
        self.assertEqual(found["funder"], real)
        self.assertEqual(found["kind"], "system.transfer")
        self.assertNotEqual(found["kind"], "balance-diff")

    def test_usdc_beats_a_dust_sol_topup(self):
        wallet, sol_src, usdc_src = "Wallet", "SolSrc", "UsdcSrc"
        src_ata, dst_ata = "SrcAta", "DstAta"
        dust = SOL_OVER_STABLE_LAMPORTS - 1
        tx = bare_tx(
            [key(sol_src, True), key(wallet), key(usdc_src), key(src_ata), key(dst_ata)],
            [
                {
                    "program": "system",
                    "parsed": {
                        "type": "transfer",
                        "info": {"source": sol_src, "destination": wallet, "lamports": dust},
                    },
                },
                {
                    "program": "spl-token",
                    "parsed": {
                        "type": "transferChecked",
                        "info": {
                            "source": src_ata,
                            "destination": dst_ata,
                            "authority": usdc_src,
                            "mint": USDC_MINT,
                            "tokenAmount": {"amount": "50000000", "decimals": 6},
                        },
                    },
                },
            ],
            [dust, 0, 0, 0, 0],
            [0, dust, 0, 0, 0],
            pre_tok=[_bal(3, USDC_MINT, usdc_src, "50000000")],
            post_tok=[
                _bal(3, USDC_MINT, usdc_src, "0"),
                _bal(4, USDC_MINT, wallet, "50000000"),
            ],
        )
        found = find_incoming(tx, wallet, USDC)
        self.assertEqual(found["asset"], "USDC")
        self.assertEqual(found["funder"], usdc_src)
        self.assertEqual(found["amount_raw"], 50_000_000)

    def test_large_sol_beats_a_small_stablecoin(self):
        wallet, sol_src, usdc_src = "Wallet", "SolSrc", "UsdcSrc"
        src_ata, dst_ata = "SrcAta", "DstAta"
        tx = bare_tx(
            [key(sol_src, True), key(wallet), key(usdc_src), key(src_ata), key(dst_ata)],
            [
                {
                    "program": "system",
                    "parsed": {
                        "type": "transfer",
                        "info": {
                            "source": sol_src,
                            "destination": wallet,
                            "lamports": SOL_OVER_STABLE_LAMPORTS,
                        },
                    },
                },
                {
                    "program": "spl-token",
                    "parsed": {
                        "type": "transfer",
                        "info": {
                            "source": src_ata,
                            "destination": dst_ata,
                            "authority": usdc_src,
                            "mint": USDC_MINT,
                            "amount": "1000",
                        },
                    },
                },
            ],
            [0, 0, 0, 0, 0],
            [0, SOL_OVER_STABLE_LAMPORTS, 0, 0, 0],
            pre_tok=[_bal(3, USDC_MINT, usdc_src, "1000")],
            post_tok=[_bal(4, USDC_MINT, wallet, "1000")],
        )
        found = find_incoming(tx, wallet, USDC)
        self.assertEqual(found["asset"], "SOL")
        self.assertEqual(found["funder"], sol_src)

    def test_missing_owner_is_counted_not_crashed(self):
        tx = bare_tx(
            [key("A", True)],
            [],
            [1],
            [1],
            post_tok=[{"mint": "Mint", "uiTokenAmount": {"amount": "5"}}],
        )
        deltas, missing = token_deltas(tx, "Mint")
        self.assertEqual(missing, 1)
        self.assertEqual(deltas, {})


def _bal(index, mint, owner, amount):
    return {
        "accountIndex": index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {"amount": amount, "decimals": 6},
    }


if __name__ == "__main__":
    unittest.main()
