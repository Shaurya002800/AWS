"""Run the offline Resurrection Test Lab; no AWS calls."""

import argparse
import json
import os
from dataclasses import asdict
from pathlib import Path

from lab.runner import SCENARIOS, run_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description="RewindGuard X local resurrection lab")
    parser.add_argument("--dsn", default=os.environ.get("RGX_TEST_POSTGRES_DSN"))
    parser.add_argument("--output", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    run = sub.add_parser("run")
    run.add_argument("--scenario", choices=SCENARIOS, required=True)
    run.add_argument("--mode", choices=("baseline", "protected"), required=True)
    run.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.command == "list":
        print("\n".join(SCENARIOS))
        return
    if not args.dsn:
        parser.error("Set RGX_TEST_POSTGRES_DSN or pass --dsn")
    report = asdict(run_scenario(args.dsn, args.scenario, args.mode, seed=args.seed))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n")
    print(rendered)


if __name__ == "__main__":
    main()
