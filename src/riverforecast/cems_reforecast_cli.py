"""Plan or download the constraint-admitted GloFAS v4 reforecast archive."""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from riverforecast import cems
from riverforecast import cems_reforecast as rf


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.cems_reforecast_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "plan", help="print request count, fields and float32 raw size; no download"
    )
    fetch = commands.add_parser(
        "fetch-raw", help="retain v4 Tabasco ZIP answers atomically; rerun to resume"
    )
    fetch.add_argument("--area", choices=["tabasco"], required=True)
    fetch.add_argument("--out", type=Path, required=True)
    fetch.add_argument(
        "--parallel",
        type=int,
        default=3,
        help="concurrent requests (default: 3); stop on cost or queue limits",
    )
    fetch.add_argument(
        "--issue",
        type=dt.date.fromisoformat,
        help="only this constraint-admitted issue (small validation request)",
    )
    args = parser.parse_args(argv)
    if args.command == "plan":
        print(rf.plan_summary(rf.plan_requests()))
        return 0
    if args.parallel < 1:
        parser.error("parallel must be positive")
    try:
        jobs = (
            [(str(args.issue), rf.single_request(args.issue))] if args.issue else rf.plan_requests()
        )
    except cems.CemsError as error:
        parser.error(str(error))
    return cems.fetch_raw(jobs, rf.DATASET, args.out, args.parallel, client)


if __name__ == "__main__":
    raise SystemExit(main())
