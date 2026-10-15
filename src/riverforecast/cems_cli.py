"""Download GloFAS operational forecasts for a station, one request per month.

    uv run python -m riverforecast.cems_cli fetch BDCTB --start 2026-01-01

Writes ``<root>/glofas/<CODE>/operational/<YYYY-MM>.csv`` (same columns as the
reforecast table). A month is skipped when its table already reaches the last
requested day. Several months wait in the CEMS queue at once (``--parallel``).
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


def main(argv: list[str] | None = None, client=None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.cems_cli")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="download operational forecasts month by month")
    fetch.add_argument("code")
    fetch.add_argument("--start", type=dt.date.fromisoformat, required=True)
    fetch.add_argument(
        "--end", type=dt.date.fromisoformat, default=dt.date.today() - dt.timedelta(days=1)
    )
    fetch.add_argument("--parallel", type=int, default=3)
    args = parser.parse_args(argv)

    if client is None:
        import cdsapi

        client = cdsapi.Client(quiet=True, progress=False)
    folder = args.root / "glofas" / args.code / "operational"
    folder.mkdir(parents=True, exist_ok=True)

    pending, skipped = [], 0
    for year, month in cems.months(args.start, args.end):
        first = max(args.start, dt.date(year, month, 1))
        last = min(args.end, (pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)).date())
        target = folder / f"{year}-{month:02d}.csv"
        if target.exists() and pd.read_csv(target)["issue"].max() >= f"{last}":
            skipped += 1
            continue
        days = list(range(first.day, last.day + 1))
        pending.append((year, month, days, target))

    def fetch_month(job):
        year, month, days, target = job
        label = f"{year}-{month:02d}"
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
    print(f"{stored} months stored, {failed} failed{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
