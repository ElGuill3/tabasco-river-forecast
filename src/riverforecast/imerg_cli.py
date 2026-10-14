"""Download daily IMERG Early basin-mean rainfall for a station.

    uv run python -m riverforecast.imerg_cli update BDCTB --start 1998-01-01

Needs a NASA Earthdata login in ``~/.netrc`` (``machine urs.earthdata.nasa.gov
login ... password ...``) with the "NASA GESDISC DATA ARCHIVE" application
approved. Reads ``data/basins/<CODE>.geojson`` and appends to
``data/raw/imerg/<CODE>/daily.csv``; rerunning fetches only missing days.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import geopandas as gpd
import requests

from riverforecast import imerg

DEFAULT_BASINS = Path("data/basins")
DEFAULT_ROOT = Path("data/raw/imerg")


def session() -> requests.Session:
    """A session that sends ~/.netrc credentials on the Earthdata login redirect."""
    http = requests.Session()
    http.headers["User-Agent"] = "tabasco-river-forecast"
    return http


def months(start: dt.date, end: dt.date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def main(argv: list[str] | None = None, http: requests.Session | None = None) -> int:
    parser = argparse.ArgumentParser(prog="riverforecast.imerg_cli")
    parser.add_argument("--basins", type=Path, default=DEFAULT_BASINS)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    update = commands.add_parser("update", help="fetch missing days of basin-mean rainfall")
    update.add_argument("code")
    update.add_argument("--start", type=dt.date.fromisoformat, default=dt.date(1998, 1, 1))
    update.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    args = parser.parse_args(argv)

    geometry = gpd.read_file(args.basins / f"{args.code}.geojson").geometry.iloc[0]
    window = imerg.GridWindow.covering(*geometry.bounds)
    mask = imerg.basin_mask(window, geometry)
    http = http or session()
    store = args.root / args.code / "daily.csv"

    def fetch_day(day: dt.date, name: str) -> float:
        answer = http.get(imerg.subset_url(day, name, window), timeout=120)
        answer.raise_for_status()
        return imerg.basin_mean(imerg.read_subset(answer.content), mask)

    total = 0
    for year, month in months(args.start, args.end):
        listing_page = http.get(imerg.month_listing_url(year, month), timeout=120)
        if listing_page.status_code == 404:
            continue
        listing_page.raise_for_status()
        listing = {
            day: name
            for day, name in imerg.files_in_listing(listing_page.text).items()
            if args.start <= day <= args.end
        }
        try:
            added = imerg.update_store(store, listing, fetch_day)
        except requests.HTTPError as error:
            status = error.response.status_code if error.response is not None else "?"
            hint = " (check ~/.netrc and the GES DISC app approval)" if status in (401, 403) else ""
            print(f"{args.code}: {year}-{month:02d} failed: HTTP {status}{hint}", file=sys.stderr)
            return 1
        total += added
        if added:
            print(f"{args.code}: {year}-{month:02d} +{added} days", flush=True)
    print(f"{args.code}: {total} days added to {store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
