"""Download GloFAS operational forecasts for a station in five-day requests.

    uv run python -m riverforecast.cems_cli fetch BDCTB --start 2026-01-01

CEMS rejects requests above about 900 fields ("cost limits exceeded"); one
issue day of the full ensemble is 51 members x 3 leads = 153 fields, so each
request covers at most five issue days of one month. Writes
``<root>/glofas/<CODE>/operational/<YYYY-MM-DD>.csv`` (named by the first issue
day; same columns as the reforecast table). A request is skipped when its table
already reaches its last day. Several requests wait in the queue at once
(``--parallel``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from riverforecast import cems

DEFAULT_ROOT = Path("data/raw")
DAYS_PER_REQUEST = 5


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.cems_cli")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="download operational forecasts")
    fetch.add_argument("code")
    fetch.add_argument("--start", type=dt.date.fromisoformat, required=True)
    fetch.add_argument(
        "--end", type=dt.date.fromisoformat, default=dt.date.today() - dt.timedelta(days=1)
    )
    fetch.add_argument("--parallel", type=int, default=3)
    raw = commands.add_parser(
        "fetch-raw", help="retain five-day Tabasco ZIP answers atomically; rerun to resume"
    )
    raw.add_argument("--area", choices=["tabasco"], required=True)
    raw.add_argument("--start", type=dt.date.fromisoformat, required=True)
    raw.add_argument(
        "--end", type=dt.date.fromisoformat, default=dt.date.today() - dt.timedelta(days=1)
    )
    raw.add_argument("--out", type=Path, required=True)
    raw.add_argument(
        "--parallel",
        type=int,
        default=3,
        help="concurrent requests (default: 3); stop on cost limits",
    )
    args = parser.parse_args(argv)

    if args.command == "fetch-raw":
        if args.start > args.end or args.parallel < 1:
            parser.error("start must not exceed end and parallel must be positive")
        jobs = []
        for year, month in cems.months(args.start, args.end):
            first = max(args.start, dt.date(year, month, 1))
            last = min(args.end, (pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)).date())
            days = list(range(first.day, last.day + 1))
            for offset in range(0, len(days), DAYS_PER_REQUEST):
                chunk = days[offset : offset + DAYS_PER_REQUEST]
                jobs.append(
                    (
                        f"{year}-{month:02d}-{chunk[0]:02d}",
                        cems.forecast_request(year, month, cems.AREA_TABASCO, chunk),
                    )
                )
        return cems.fetch_raw(jobs, cems.DATASET, args.out, args.parallel, client)

    if client is None:
        import cdsapi

        client = cdsapi.Client(quiet=True, progress=False)
    folder = args.root / "glofas" / args.code / "operational"
    folder.mkdir(parents=True, exist_ok=True)

    pending, skipped = [], 0
    for year, month in cems.months(args.start, args.end):
        first = max(args.start, dt.date(year, month, 1))
        last = min(args.end, (pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)).date())
        days = list(range(first.day, last.day + 1))
        for start in range(0, len(days), DAYS_PER_REQUEST):
            chunk = days[start : start + DAYS_PER_REQUEST]
            target = folder / f"{year}-{month:02d}-{chunk[0]:02d}.csv"
            final = f"{dt.date(year, month, chunk[-1])}"
            if target.exists() and pd.read_csv(target)["issue"].max() >= final:
                skipped += 1
                continue
            pending.append((year, month, chunk, target))

    def fetch_month(job):
        year, month, days, target = job
        label = f"{year}-{month:02d}-{days[0]:02d}"
        request = cems.forecast_request(year, month, cems.CELL_AREA, days)
        try:
            with tempfile.TemporaryDirectory() as scratch:
                raw = Path(scratch) / "answer.zip"
                client.retrieve(cems.DATASET, request, str(raw))
                table = cems.read_archive(raw.read_bytes(), cems.BDCTB_CELL)
        except Exception as error:  # noqa: BLE001 - report any queue or format failure
            print(f"{label}: failed ({type(error).__name__}: {error})", file=sys.stderr)
            return False
        table.to_csv(target, index=False)
        print(f"{label}: {len(table)} records, {table.issue.nunique()} issues", flush=True)
        return True

    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as pool:
        results = list(pool.map(fetch_month, pending))
    stored, failed = sum(results), len(results) - sum(results)
    note = f" ({skipped} already stored)" if skipped else ""
    print(f"{stored} requests stored, {failed} failed{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
