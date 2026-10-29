"""Sync one desk's rugger cache with the private repo.

Run after the scanner (and after Solana fills). The scan does not clone or push.

    python -m rugger_blacklist.sync --desk cardano|solana --cache-dir DIR [--public-dir DIR]
"""
import argparse

import rugger_blacklist as RB


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sync the rugger cache with the private repo")
    parser.add_argument("--desk", required=True, choices=("cardano", "solana"))
    parser.add_argument("--cache-dir", required=True)
    parser.add_argument("--public-dir", default="")
    args = parser.parse_args(argv)
    RB.sync_cache(args.cache_dir, args.desk, args.public_dir or None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
