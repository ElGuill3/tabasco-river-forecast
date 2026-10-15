"""GloFAS operational forecasts from the Copernicus CEMS Early Warning Data Store.

Dataset ``cems-glofas-forecast``: the daily GloFAS forecasts as issued (control
plus 50 perturbed members). Requests need an account in ``~/.cdsapirc`` and
wait in a queue (about 40 minutes for one request in October 2026), so one
request covers a whole month of issue days for a small area around the cell.

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
import zipfile
from collections.abc import Iterator

import numpy as np
import pandas as pd
import xarray as xr

DATASET = "cems-glofas-forecast"
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
