"""On-chain redemption rate. Public account bytes only.

SPL stake pool (borsh, packed):
  pool_mint at byte 162 (32 bytes)
  total_lamports u64 LE at byte 258
  pool_token_supply u64 LE at byte 266
  last_update_epoch u64 LE at byte 274
Withdrawal fees sit after the lockup. Option and FutureEpoch tags make
their offsets variable, so they are walked and logged raw.

mSOL: Marinade state, msol_price u64 LE at byte 512, divided by 2^32.
The state's mint is the first pubkey after the 8-byte discriminator (byte 8).
"""

from fractions import Fraction

POOL_MINT_OFFSET = 162
POOL_MINT_LEN = 32
TOTAL_LAMPORTS_OFFSET = 258
POOL_TOKEN_SUPPLY_OFFSET = 266
LAST_UPDATE_EPOCH_OFFSET = 274
LOCKUP_OFFSET = 282
LOCKUP_LEN = 48
STAKE_POOL_ACCOUNT_TYPE = 1

MSOL_MINT_OFFSET = 8
MSOL_PRICE_OFFSET = 512
MSOL_PRICE_DENOMINATOR = 2 ** 32
MINT_DECIMALS_OFFSET = 44


class RateError(Exception):
    pass


def _need(data, off, size):
    if off < 0 or size < 0 or off + size > len(data):
        raise RateError("short account")
    return data[off:off + size], off + size


def _u8(data, off):
    raw, off = _need(data, off, 1)
    return raw[0], off


def _u64(data, off):
    raw, off = _need(data, off, 8)
    return int.from_bytes(raw, "little"), off


def u64_at(data, offset):
    return int.from_bytes(data[offset:offset + 8], "little")


def _fee(data, off):
    start = off
    den, off = _u64(data, off)
    num, off = _u64(data, off)
    return {
        "numerator": num,
        "denominator": den,
        "raw_hex": data[start:start + 16].hex(),
    }, off


def _optional_pubkey(data, off):
    tag, off = _u8(data, off)
    if tag == 0:
        return None, off
    if tag != 1:
        raise RateError("option tag")
    _raw, off = _need(data, off, 32)
    return _raw, off


def _future_fee(data, off):
    tag, off = _u8(data, off)
    if tag == 0:
        return {"tag": 0, "fee": None}, off
    if tag not in (1, 2):
        raise RateError("future tag")
    fee, off = _fee(data, off)
    return {"tag": tag, "fee": fee}, off


def withdrawal_fees(data):
    """Walk the borsh tail and return the four withdrawal-fee fields.

    Current stake and SOL fees are the active ones. The next_* fields are
    logged raw and are not the fee in the display adjustment.
    """
    off = LOCKUP_OFFSET + LOCKUP_LEN
    _epoch_fee, off = _fee(data, off)
    _next_epoch, off = _future_fee(data, off)
    _deposit_vote, off = _optional_pubkey(data, off)
    _withdraw_vote, off = _optional_pubkey(data, off)
    _stake_deposit, off = _fee(data, off)
    stake, off = _fee(data, off)
    next_stake, off = _future_fee(data, off)
    _stake_referral, off = _u8(data, off)
    _sol_deposit_authority, off = _optional_pubkey(data, off)
    _sol_deposit, off = _fee(data, off)
    _sol_referral, off = _u8(data, off)
    _sol_withdraw_authority, off = _optional_pubkey(data, off)
    sol, off = _fee(data, off)
    next_sol, off = _future_fee(data, off)
    return {
        "stake_withdrawal_fee": stake,
        "next_stake_withdrawal_fee": next_stake,
        "sol_withdrawal_fee": sol,
        "next_sol_withdrawal_fee": next_sol,
    }


def read_spl(data):
    if len(data) < LAST_UPDATE_EPOCH_OFFSET + 8:
        raise RateError("short account")
    if data[0] != STAKE_POOL_ACCOUNT_TYPE:
        raise RateError("account type")
    total = u64_at(data, TOTAL_LAMPORTS_OFFSET)
    supply = u64_at(data, POOL_TOKEN_SUPPLY_OFFSET)
    if supply == 0:
        raise RateError("zero supply")
    fees = None
    try:
        fees = withdrawal_fees(data)
    except RateError:
        fees = None
    return {
        "pool_mint": data[POOL_MINT_OFFSET:POOL_MINT_OFFSET + POOL_MINT_LEN],
        "total_lamports": total,
        "pool_token_supply": supply,
        "last_update_epoch": u64_at(data, LAST_UPDATE_EPOCH_OFFSET),
        "rate": Fraction(total, supply),
        "withdrawal_fees": fees,
    }


def read_msol(data):
    if len(data) < MSOL_PRICE_OFFSET + 8:
        raise RateError("short account")
    price = u64_at(data, MSOL_PRICE_OFFSET)
    if price == 0:
        raise RateError("zero price")
    return {
        "mint": data[MSOL_MINT_OFFSET:MSOL_MINT_OFFSET + 32],
        "msol_price": price,
        "rate": Fraction(price, MSOL_PRICE_DENOMINATOR),
    }


def read_mint_decimals(data):
    if data is None or len(data) <= MINT_DECIMALS_OFFSET:
        return None
    decimals = data[MINT_DECIMALS_OFFSET]
    if not 0 <= decimals <= 18:
        return None
    return decimals
