"""GloFAS operational forecasts from the Copernicus CEMS Early Warning Data Store.

Dataset ``cems-glofas-forecast``: the daily GloFAS forecasts as issued (control
plus 50 perturbed members). Requests need an account in ``~/.cdsapirc`` and
wait in a queue (about 40 minutes for one request in October 2026), so one
request covers at most five issue days. Raw mode retains the whole Tabasco area.

The answer is a zip with one NetCDF for the control run and one for the
perturbed members (``dis24``, m³/s, dimensions forecast_period x
forecast_reference_time x latitude x longitude, plus ``number`` for members).

Grid labels: CEMS and Open-Meteo label the same 0.05° cells half a step apart
in longitude. The BDCTB river cell is 17.425 / 268.525 (-91.475) in CEMS and
17.425 / -91.525 in Open-Meteo (checked: both carry ~2 180 m³/s on
2026-10-01-02 while neighbours carry 1-3 m³/s).
"""

from __future__ import annotations

import calendar
import datetime as dt
import io
import itertools
import sys
import tempfile
import threading
import zipfile
from collections.abc import Iterator
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

DATASET = "cems-glofas-forecast"
AREA_TABASCO = [18.3, -93.6, 17.1, -90.9]
CELL_AREA = [17.5, -91.55, 17.35, -91.4]  # north, west, south, east around BDCTB
BDCTB_CELL = (17.425, 268.525)
LEADS_HOURS = (24, 72, 168)
COLUMNS = ["issue", "valid", "lead_days", "member", "discharge_m3s"]


class CemsError(ValueError):
    """A CEMS answer cannot be read."""


def months(start: dt.date, end: dt.date) -> Iterator[tuple[int, int]]:
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def forecast_request(
    year: int, month: int, area: list[float], days: list[int] | None = None
) -> dict:
    """Request for the given issue days of one month (default: the whole month)."""
    days = days or list(range(1, calendar.monthrange(year, month)[1] + 1))
    return {
        "system_version": ["operational"],
        "hydrological_model": ["lisflood"],
        "product_type": ["control_forecast", "ensemble_perturbed_forecasts"],
        "variable": ["river_discharge_in_the_last_24_hours"],
        "year": [f"{year}"],
        "month": [f"{month:02d}"],
        "day": [f"{day:02d}" for day in days],
        "leadtime_hour": [f"{hours}" for hours in LEADS_HOURS],
        "area": list(area),
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


def read_archive(content: bytes, cell: tuple[float, float]) -> pd.DataFrame:
    """Tidy rows (issue, valid, lead_days, member, discharge) for one grid cell."""
    rows = []
    with zipfile.ZipFile(io.BytesIO(content)) as zipped:
        for name in sorted(zipped.namelist()):
            with xr.open_dataset(io.BytesIO(zipped.read(name)), engine="h5netcdf") as ds:
                rows.extend(_cell_rows(ds, cell))
    table = pd.DataFrame(rows, columns=COLUMNS)
    order = table["member"].map(lambda m: (m != "control", m))
    return (
        table.assign(_order=order)
        .sort_values(["issue", "lead_days", "_order"])
        .drop(columns="_order")
        .reset_index(drop=True)
    )


def read_cell(zip_or_nc_path: str | Path, lat: float, lon: float) -> pd.DataFrame:
    """Extract one exact CEMS-labelled cell; accept either longitude convention."""
    path = Path(zip_or_nc_path)
    if zipfile.is_zipfile(path):
        # Match longitude convention separately for each product file.
        with zipfile.ZipFile(path) as zipped:
            tables = []
            for name in sorted(zipped.namelist()):
                if name.endswith(".nc"):
                    with xr.open_dataset(io.BytesIO(zipped.read(name)), engine="h5netcdf") as ds:
                        longitude = (
                            lon % 360 if float(ds.longitude.min()) >= 0 else (lon + 180) % 360 - 180
                        )
                        tables.extend(_cell_rows(ds, (lat, longitude)))
    else:
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            longitude = lon % 360 if float(ds.longitude.min()) >= 0 else (lon + 180) % 360 - 180
            tables = _cell_rows(ds, (lat, longitude))
    table = pd.DataFrame(tables, columns=COLUMNS)
    if table.empty:
        raise CemsError("no discharge rows in answer")
    return table.sort_values(["issue", "lead_days", "member"]).reset_index(drop=True)


def request_dates(request: dict) -> list[str]:
    """Exact calendar cross-product of either CEMS request type."""
    keys = ("hyear", "hmonth", "hday") if "hyear" in request else ("year", "month", "day")
    return sorted(
        str(dt.date(int(y), int(m), int(d)))
        for y, m, d in itertools.product(*(request[k] for k in keys))
    )


def valid_raw(path: Path, request: dict, members: int) -> bool:
    """Check CRCs and exact issue/lead/member coverage without extracting a cell."""
    try:
        coverage = set()
        with zipfile.ZipFile(path) as zipped:
            if zipped.testzip() is not None:
                return False
            for name in zipped.namelist():
                if not name.endswith(".nc"):
                    continue
                with xr.open_dataset(io.BytesIO(zipped.read(name)), engine="h5netcdf") as ds:
                    values = ds["dis24"]
                    issues = {
                        str(pd.Timestamp(v).date()) for v in values.forecast_reference_time.values
                    }
                    leads = {
                        int(pd.Timedelta(v).total_seconds() / 3600)
                        for v in values.forecast_period.values
                    }
                    numbers = (
                        {int(v) for v in values.number.values} if "number" in values.dims else {0}
                    )
                    if issues != set(request_dates(request)) or leads != set(LEADS_HOURS):
                        return False
                    if coverage & numbers:
                        return False
                    coverage.update(numbers)
        return coverage == set(range(members))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return False


def fetch_raw(jobs, dataset: str, folder: Path, parallel: int, client=None) -> int:
    """Bounded queue, atomic publication, resumable failures and global cost stop.

    Already-running service requests cannot be cancelled, but no new requests are
    submitted after a cost rejection. Each worker owns its production CDS client.
    """
    folder.mkdir(parents=True, exist_ok=True)
    members = 51 if dataset == DATASET else 11
    pending, skipped = [], 0
    for label, request in jobs:
        target = folder / f"{label}.zip"
        if target.exists() and valid_raw(target, request, members):
            skipped += 1
        else:
            pending.append((label, request, target))
    stop = threading.Event()

    def download(job):
        label, request, target = job
        if stop.is_set():
            return None
        try:
            active_client = client
            if active_client is None:
                import cdsapi

                active_client = cdsapi.Client(quiet=True, progress=False, retry_max=1)
            with tempfile.TemporaryDirectory(prefix=".cems-", dir=folder) as scratch:
                raw = Path(scratch) / "answer.zip"
                active_client.retrieve(dataset, request, str(raw))
                if not valid_raw(raw, request, members):
                    raise CemsError("answer has incomplete issue/lead/member coverage")
                raw.replace(target)
            print(f"{label}: stored", flush=True)
            return True
        except Exception as error:  # noqa: BLE001 - preserve resumability on service errors
            if "cost limit" in str(error).lower():
                stop.set()
            print(f"{label}: failed ({type(error).__name__}: {error})", file=sys.stderr)
            return False

    stored = failed = 0
    iterator = iter(pending)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        active = {pool.submit(download, job) for job in itertools.islice(iterator, parallel)}
        while active:
            done, active = wait(active, return_when=FIRST_COMPLETED)
            for future in done:
                result = future.result()
                stored += result is True
                failed += result is False
            if not stop.is_set():
                active.update(
                    pool.submit(download, job) for job in itertools.islice(iterator, len(done))
                )
    note = f" ({skipped} already stored)" if skipped else ""
    print(f"{stored} requests stored, {failed} failed{note}")
    return 1 if failed else 0


def _cell_rows(ds: xr.Dataset, cell: tuple[float, float]) -> list[tuple]:
    lat, lon = cell
    lats, lons = ds["latitude"].values, ds["longitude"].values
    if not (np.isclose(lats, lat).any() and np.isclose(lons, lon).any()):
        raise CemsError(f"cell {lat},{lon} not in the archive")
    values = ds["dis24"].sel(latitude=lat, longitude=lon, method="nearest")
    members = values["number"].values if "number" in values.dims else [None]
    rows = []
    for member in members:
        series = values.sel(number=member) if member is not None else values
        label = "control" if member is None else f"perturbed_{int(member):02d}"
        for issue in series["forecast_reference_time"].values:
            issue_day = pd.Timestamp(issue)
            for period in series["forecast_period"].values:
                lead = int(pd.Timedelta(period).days)
                value = float(series.sel(forecast_reference_time=issue, forecast_period=period))
                if not np.isfinite(value):
                    raise CemsError(
                        f"missing discharge for {issue_day:%Y-%m-%d} lead {lead} {label}"
                    )
                valid = issue_day + pd.Timedelta(days=lead)
                rows.append((f"{issue_day:%Y-%m-%d}", f"{valid:%Y-%m-%d}", lead, label, value))
    return rows
