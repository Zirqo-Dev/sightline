"""Solana whale-flow ledger. Not started.

TODO: a log-only Solana whale-flow ledger is not feasible from this repo yet.

The Cardano ledger follows one codenamed stake key and publishes codenames
only. This desk has no Solana codename-to-wallet map. The public RPC holder
route used on day 1 returned 429, and RugCheck top-holder rows are addresses,
which cannot be written to a public file. v6.5 skip, fill, and exit stay
frozen through the Nov 11 cutoff. This module is not imported by run.py,
fills.py, exits.py, skips.py, or observe.py. Nothing in those files may read it.

When a private cache of codenamed Solana wallets exists, a later change can
append rows of {at, codename, ticker, direction, sol} under mode log_only.
Skip, entry, exit, and kill must keep ignoring that file.
"""


def enabled():
    """False until a private codename map exists. The frozen path must not call this."""
    return False
