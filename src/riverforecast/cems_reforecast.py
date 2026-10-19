"""Constraint-admitted GloFAS v4 reforecast requests for all Tabasco cells."""

from __future__ import annotations

import datetime as dt
import itertools
import json
from pathlib import Path

from riverforecast import cems
from riverforecast.cems import read_cell  # noqa: F401 - shared public reader

DATASET = "cems-glofas-reforecast"
CONSTRAINTS = Path(__file__).resolve().parents[2] / "data/glofas/reforecast-constraints.json"
PRODUCTS = ["control_reforecast", "ensemble_perturbed_reforecast"]


def load_constraints(path: Path = CONSTRAINTS) -> list[dict]:
    return json.loads(path.read_text())


def validate_request(request: dict, rows: list[dict]) -> None:
    """Every date/product must occur in an official v4 constraint row."""
    if (
        request.get("system_version") != ["version_4_0"]
        or request.get("hydrological_model") != ["lisflood"]
        or request.get("product_type") != PRODUCTS
        or request.get("leadtime_hour") != ["24", "72", "168"]
        or request.get("area") != cems.AREA_TABASCO
        or len(cems.request_dates(request)) * 33 > 900
    ):
        raise cems.CemsError("request violates v4 constraints or field limit")
    for year, month, day, product in itertools.product(
        request["hyear"], request["hmonth"], request["hday"], PRODUCTS
    ):
        if not any(
            year in row["hyear"]
            and month in row["hmonth"]
            and day in row["hday"]
            and product in row["product_type"]
            and all(
                set(request[key]) <= set(row[key])
                for key in ("system_version", "hydrological_model", "variable", "leadtime_hour")
            )
            for row in rows
        ):
            raise cems.CemsError(
                f"date/product outside constraints: {year}-{month}-{day} {product}"
            )


def _request(years, month, days):
    return {
        "system_version": ["version_4_0"],
        "hydrological_model": ["lisflood"],
        "product_type": list(PRODUCTS),
        "variable": ["river_discharge_in_the_last_24_hours"],
        "hyear": list(years),
        "hmonth": [month],
        "hday": list(days),
        "leadtime_hour": ["24", "72", "168"],
        "data_format": "netcdf",
        "download_format": "unarchived",
        "area": list(cems.AREA_TABASCO),
    }


def plan_requests(rows: list[dict] | None = None) -> list[tuple[str, dict]]:
    """One month/day-set, sliced years: <=27 issues = <=891 fields.

    Union the per-product admitted dates first, then group identical day sets
    across years; never manufacture a rectangular cross-product of differing calendars.
    """
    rows = load_constraints() if rows is None else rows
    calendars = {}
    for row in rows:
        if (
            row["system_version"] == ["version_4_0"]
            and row["hydrological_model"] == ["lisflood"]
            and row["product_type"] == [PRODUCTS[0]]
        ):
            for year, month in itertools.product(row["hyear"], row["hmonth"]):
                calendars.setdefault((year, month), set()).update(row["hday"])
    groups = {}
    for (year, month), days in sorted(calendars.items()):
        groups.setdefault((month, tuple(sorted(days))), []).append(year)
    jobs = []
    for (month, days), years in sorted(groups.items()):
        step = 900 // (33 * len(days))
        if step < 1:
            raise cems.CemsError("constraint day set exceeds field limit")
        for offset in range(0, len(years), step):
            selected = years[offset : offset + step]
            request = _request(selected, month, days)
            validate_request(request, rows)
            label = f"{selected[0]}-{month}-{days[0]}_to_{selected[-1]}-{month}-{days[-1]}"
            jobs.append((label, request))
    if not jobs:
        raise cems.CemsError("no v4 constraints available")
    return jobs


def single_request(issue: dt.date, rows: list[dict] | None = None) -> dict:
    rows = load_constraints() if rows is None else rows
    request = _request([f"{issue.year}"], f"{issue.month:02d}", [f"{issue.day:02d}"])
    validate_request(request, rows)
    return request


def plan_summary(jobs) -> str:
    dates = [date for _, request in jobs for date in cems.request_dates(request)]
    fields = len(dates) * 33
    # 24 x 54 cells on the 0.05 degree grid, float32; excludes NetCDF metadata/compression.
    size = fields * 24 * 54 * 4 / 2**20
    return (
        f"{len(jobs)} requests, {fields} fields, "
        f"estimated raw float32 payload {size:.1f} MiB; "
        f"{len(dates)} issues, {min(dates)}..{max(dates)}"
    )
