"""Daily basin-mean rainfall from NASA GPM IMERG Early Run (V07, 0.1°).

IMERG Early is available about four hours after real time, so a model trained
on it can run with the same source every day. Files come from NASA GES DISC and
need a NASA Earthdata login in ``~/.netrc``; only the basin's bounding box is
requested through OPeNDAP.
"""

from __future__ import annotations

import csv
import datetime as dt
import math
import re
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

PRODUCT = "GPM_3IMERGDE.07"
DATA_URL = f"https://gpm1.gesdisc.eosdis.nasa.gov/data/GPM_L3/{PRODUCT}"
OPENDAP_URL = f"https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/{PRODUCT}"
FILE_PATTERN = re.compile(r"3B-DAY-E\.MS\.MRG\.3IMERG\.(\d{8})-S000000-E235959\.V\w+\.nc4")
STEP = 0.1
FIRST_LON = -179.95
FIRST_LAT = -89.95
FIELDS = ["date", "basin_mean_mm", "file"]
RETRY_ATTEMPTS = 5


class TransientError(RuntimeError):
    """A failure worth retrying: network drop, timeout or server error."""


def retry(
    fetch: Callable[[], float],
    attempts: int = RETRY_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
) -> float:
    """Call ``fetch``, waiting 2, 4, 8... seconds between attempts on TransientError."""
    sleep = sleep or time.sleep
    for attempt in range(attempts):
        try:
            return fetch()
        except TransientError:
            if attempt == attempts - 1:
                raise
            sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable")


@dataclass(frozen=True)
class GridWindow:
    """Inclusive index ranges into the global IMERG grid."""

    lon_range: tuple[int, int]
    lat_range: tuple[int, int]

    @classmethod
    def covering(cls, west: float, south: float, east: float, north: float) -> GridWindow:
        def low(value, first):
            return math.floor((value - first) / STEP + 1e-9)

        def high(value, first):
            return math.ceil((value - first) / STEP - 1e-9)

        return cls(
            lon_range=(low(west, FIRST_LON), high(east, FIRST_LON)),
            lat_range=(low(south, FIRST_LAT), high(north, FIRST_LAT)),
        )

    @property
    def lons(self) -> np.ndarray:
        return FIRST_LON + STEP * np.arange(self.lon_range[0], self.lon_range[1] + 1)

    @property
    def lats(self) -> np.ndarray:
        return FIRST_LAT + STEP * np.arange(self.lat_range[0], self.lat_range[1] + 1)


def month_listing_url(year: int, month: int) -> str:
    return f"{DATA_URL}/{year:04d}/{month:02d}/"


def files_in_listing(html: str) -> dict[dt.date, str]:
    """Daily file names in a GES DISC month directory page, by date."""
    files = {}
    for match in FILE_PATTERN.finditer(html):
        if html[match.end() : match.end() + 4] == ".xml":
            continue
        files[dt.datetime.strptime(match.group(1), "%Y%m%d").date()] = match.group(0)
    return dict(sorted(files.items()))


def subset_url(day: dt.date, name: str, window: GridWindow) -> str:
    (lon0, lon1), (lat0, lat1) = window.lon_range, window.lat_range
    return (
        f"{OPENDAP_URL}/{day:%Y/%m}/{name}.nc4"
        f"?precipitation%5B0:0%5D%5B{lon0}:{lon1}%5D%5B{lat0}:{lat1}%5D"
    )


def basin_mask(window: GridWindow, basin_geometry) -> np.ndarray:
    """(lon, lat) mask of the window cells whose centre lies inside the basin."""
    from shapely import contains_xy

    lon, lat = np.meshgrid(window.lons, window.lats, indexing="ij")
    return contains_xy(basin_geometry, lon, lat)


def basin_mean(precipitation: np.ndarray, mask: np.ndarray) -> float:
    """Mean rainfall (mm/day) over the masked cells, ignoring missing cells."""
    values = precipitation[mask]
    values = values[~np.isnan(values)]
    return float(values.mean()) if values.size else math.nan


def read_subset(content: bytes) -> np.ndarray:
    """The (lon, lat) precipitation array of one OPeNDAP NetCDF-4 answer."""
    import io

    import h5py

    with h5py.File(io.BytesIO(content), "r") as handle:
        data = handle["precipitation"][0].astype(float)
        fill = handle["precipitation"].attrs.get("_FillValue")
    if fill is not None:
        data[data == float(np.asarray(fill).ravel()[0])] = np.nan
    data[data < 0] = np.nan
    return data


def update_store(
    store: Path,
    listing: Mapping[dt.date, str],
    fetch_day: Callable[[dt.date, str], float],
    workers: int = 1,
) -> int:
    """Append the listed days missing from ``store``; return how many were added.

    Up to ``workers`` days are fetched at once, but rows are written in date
    order as soon as every earlier day is done, so an interrupted run resumes
    where it stopped without gaps.
    """
    done = set()
    if store.exists():
        with store.open(newline="", encoding="utf-8") as handle:
            done = {row["date"] for row in csv.DictReader(handle)}
    else:
        store.parent.mkdir(parents=True, exist_ok=True)
        with store.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(FIELDS)

    missing = [
        (day, name) for day, name in sorted(listing.items()) if f"{day:%Y-%m-%d}" not in done
    ]
    added = 0
    with (
        store.open("a", newline="", encoding="utf-8") as handle,
        ThreadPoolExecutor(max_workers=workers) as pool,
    ):
        writer = csv.writer(handle)
        values = pool.map(lambda item: fetch_day(*item), missing)
        for (day, name), value in zip(missing, values, strict=True):
            writer.writerow([f"{day:%Y-%m-%d}", "" if math.isnan(value) else round(value, 4), name])
            handle.flush()
            added += 1
    return added
