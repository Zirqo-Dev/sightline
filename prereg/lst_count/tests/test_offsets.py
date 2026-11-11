"""Byte offsets pinned to the lock fixtures, and the spec sha256."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lst_count import SPEC_SHA256
from lst_count.b58 import b58decode
from lst_count.rate import (
    LAST_UPDATE_EPOCH_OFFSET,
    MSOL_MINT_OFFSET,
    MSOL_PRICE_DENOMINATOR,
    MSOL_PRICE_OFFSET,
    POOL_MINT_OFFSET,
    POOL_TOKEN_SUPPLY_OFFSET,
    TOTAL_LAMPORTS_OFFSET,
    read_msol,
    read_spl,
    withdrawal_fees,
)
from lst_count.speccheck import assert_spec, file_sha256, spec_path

FIX = Path(__file__).resolve().parents[1] / "fixtures"
JITOSOL_MINT = "J1toso1uCk3RLmjorhTtrVwY9HJ7X8V9yYac6Y7kGCPn"
MSOL_MINT = "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So"


def u64(data, offset):
    return int.from_bytes(data[offset:offset + 8], "little")


class SpecSha(unittest.TestCase):
    def test_locked_sha256(self):
        digest = file_sha256(spec_path())
        self.assertEqual(digest, "d6dc5538b6e6ec0ec71ae98b522c658664775381a97fbd366a2ba94e4c3df052")
        self.assertEqual(digest, SPEC_SHA256)
        self.assertEqual(assert_spec(), digest)

    def test_a_changed_byte_does_not_match(self):
        raw = spec_path().read_bytes()
        flipped = bytearray(raw)
        flipped[0] ^= 0x01
        import tempfile
        with tempfile.NamedTemporaryFile() as handle:
            handle.write(flipped)
            handle.flush()
            with self.assertRaises(SystemExit):
                assert_spec(handle.name)


class JitoSOLOffsets(unittest.TestCase):
    def setUp(self):
        self.data = (FIX / "jitosol_stake_pool.bin").read_bytes()

    def test_constants_are_the_spec_offsets(self):
        self.assertEqual(POOL_MINT_OFFSET, 162)
        self.assertEqual(TOTAL_LAMPORTS_OFFSET, 258)
        self.assertEqual(POOL_TOKEN_SUPPLY_OFFSET, 266)
        self.assertEqual(LAST_UPDATE_EPOCH_OFFSET, 274)

    def test_fixture_fields_at_those_bytes(self):
        self.assertEqual(self.data[162:194], b58decode(JITOSOL_MINT))
        self.assertEqual(u64(self.data, 258), 10235978162489280)
        self.assertEqual(u64(self.data, 266), 7808097503711767)
        self.assertEqual(u64(self.data, 274), 1083)
        self.assertEqual(self.data[0], 1)
        # None-layout withdrawal fees on this fixture: denominator then numerator.
        self.assertEqual(self.data[365:381].hex(), "e8030000000000000100000000000000")
        self.assertEqual(u64(self.data, 365), 1000)
        self.assertEqual(u64(self.data, 373), 1)
        self.assertEqual(u64(self.data, 402), 1000)
        self.assertEqual(u64(self.data, 410), 1)

    def test_decoder_matches_the_fixture(self):
        info = read_spl(self.data)
        self.assertEqual(info["pool_mint"], b58decode(JITOSOL_MINT))
        self.assertEqual(info["total_lamports"], 10235978162489280)
        self.assertEqual(info["pool_token_supply"], 7808097503711767)
        self.assertEqual(info["last_update_epoch"], 1083)
        self.assertEqual(info["rate"].numerator, 10235978162489280)
        self.assertEqual(info["rate"].denominator, 7808097503711767)
        fees = info["withdrawal_fees"]
        self.assertEqual(fees["stake_withdrawal_fee"]["numerator"], 1)
        self.assertEqual(fees["stake_withdrawal_fee"]["denominator"], 1000)
        self.assertEqual(fees["sol_withdrawal_fee"]["numerator"], 1)
        self.assertEqual(fees["sol_withdrawal_fee"]["denominator"], 1000)
        self.assertEqual(fees["stake_withdrawal_fee"]["raw_hex"], "e8030000000000000100000000000000")


class MarinadeOffsets(unittest.TestCase):
    def setUp(self):
        self.data = (FIX / "msol_state.bin").read_bytes()

    def test_constants(self):
        self.assertEqual(MSOL_PRICE_OFFSET, 512)
        self.assertEqual(MSOL_PRICE_DENOMINATOR, 2 ** 32)
        self.assertEqual(MSOL_MINT_OFFSET, 8)

    def test_fixture_price_and_mint(self):
        self.assertEqual(self.data[8:40], b58decode(MSOL_MINT))
        self.assertEqual(u64(self.data, 512), 6087609011)
        info = read_msol(self.data)
        self.assertEqual(info["msol_price"], 6087609011)
        self.assertEqual(info["rate"].numerator, 6087609011)
        self.assertEqual(info["rate"].denominator, 2 ** 32)
        self.assertAlmostEqual(float(info["rate"]), 1.41738192, places=8)


class ShiftedFee(unittest.TestCase):
    def test_a_present_option_does_not_move_the_rate_or_hide_the_fee(self):
        data = bytearray(700)
        data[0] = 1
        data[162:194] = b"\x11" * 32
        data[258:266] = (200).to_bytes(8, "little")
        data[266:274] = (100).to_bytes(8, "little")
        data[274:282] = (7).to_bytes(8, "little")
        off = 330  # after the 48-byte lockup
        off += 16  # epoch fee
        data[off] = 0  # next epoch fee is None
        off += 1
        data[off] = 1  # preferred deposit vote is Some
        off += 1 + 32
        data[off] = 0
        off += 1
        off += 16  # stake deposit fee
        stake_at = off
        data[off:off + 8] = (7).to_bytes(8, "little")
        data[off + 8:off + 16] = (1).to_bytes(8, "little")
        off += 16
        data[off] = 0  # next stake withdrawal fee
        off += 1
        off += 1  # stake referral
        data[off] = 0  # sol deposit authority
        off += 1
        off += 16  # sol deposit fee
        off += 1  # sol referral
        data[off] = 0  # sol withdraw authority
        off += 1
        data[off:off + 8] = (9).to_bytes(8, "little")
        data[off + 8:off + 16] = (1).to_bytes(8, "little")
        info = read_spl(bytes(data))
        self.assertEqual(info["total_lamports"], 200)
        self.assertEqual(info["pool_token_supply"], 100)
        self.assertEqual(info["last_update_epoch"], 7)
        fees = withdrawal_fees(bytes(data))
        self.assertEqual(fees["stake_withdrawal_fee"]["denominator"], 7)
        self.assertEqual(fees["stake_withdrawal_fee"]["numerator"], 1)
        self.assertEqual(fees["sol_withdrawal_fee"]["denominator"], 9)
        self.assertEqual(fees["sol_withdrawal_fee"]["numerator"], 1)
        self.assertNotEqual(stake_at, 365)


if __name__ == "__main__":
    unittest.main()
