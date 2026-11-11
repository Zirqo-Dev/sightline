"""python -m races --data-dir DIR [--dry-run] [--now ISO]"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from races.dry import DryTransport
from races.run import run_once


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sightline paper wallet race")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--now", default=None, help="ISO time. Defaults to the current UTC minute.")
    args = parser.parse_args(argv)
    if args.now:
        now = datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    else:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    transport = DryTransport() if args.dry_run else None
    result = run_once(Path(args.data_dir), now, dry_run=args.dry_run, transport=transport)
    calls = result["calls"]
    order = ("R1", "R2", "R3", "R4", "SOL", "R5")
    print("slot", result["slot"])
    print("calls " + " ".join(f"{name}={calls[name]}" for name in order) + f" total={calls['total']} cap={calls['cap']}")


if __name__ == "__main__":
    main()
