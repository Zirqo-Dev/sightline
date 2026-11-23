"""Read-only replay CLI.

  python3 -m tools.replay --series PATH --rules FILE [--seed N] [--json]

PATH is a bar jsonl, a JSON bar list, or a directory of scan manifests and
pool snapshots. FILE is a rule config. Nothing is written. The process does
not use the network.
"""

from __future__ import annotations

import argparse
import json
import sys

from .engine import RuleError, format_report, replay
from .load import ReplayRefused, load_bars


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Replay fixed rules over historical scans.")
    parser.add_argument("--series", required=True, help="scan/snapshot file or directory")
    parser.add_argument("--rules", required=True, help="pluggable rule config (JSON)")
    parser.add_argument("--seed", type=int, default=None, help="random-entry baseline seed")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    try:
        bars = load_bars(args.series)
        with open(args.rules, "r", encoding="utf-8") as handle:
            rules = json.load(handle)
        result = replay(bars, rules, seed=args.seed)
    except (ReplayRefused, RuleError, OSError) as exc:
        print(f"replay: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(result, sys.stdout, indent=1)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(format_report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
